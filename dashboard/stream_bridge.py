"""
Internal Ingestor Stream Bridge and Live UI Broadcaster.
Connects via local asynchronous HTTP/SSE to the Rust Ingestor (:9100/stream).
Eliminates MQTT & Protobuf dependencies in Dashboard UI (Option 3 Clean Architecture).
"""

import os
import time
import asyncio
import threading
from typing import Dict, Any, List, Optional, Tuple, Set
import orjson

from aqi_engine import compute_aqi

INGESTOR_HOST = os.getenv("INGESTOR_HOST", "127.0.0.1")
INGESTOR_PORT = int(os.getenv("INGESTOR_PORT", os.getenv("METRICS_PORT", "9100")))

# ==============================================================================
# Declarative Metadata Directory for Registered Hardware Sensors
# ==============================================================================
SENSOR_METADATA: Dict[str, Tuple[str, str]] = {
    "sensor-esp32-01": ("ESP32 Lab Alpha (NDIR + SPS30)", "Hardware Lab - Bay 4"),
    "sensor-esp32-02": ("ESP32 Cleanroom Beta", "Cleanroom ISO Class 6"),
    "sensor-esp32-03": ("ESP32 Workshop Gamma", "Rapid Prototyping Workshop"),
}

# ==============================================================================
# Initial Sensor State Factory Helper (Zero Fake Telemetry)
# ==============================================================================
def create_initial_sensor_state(
    device_id: str,
    name: str = None,
    location: str = "Facility"
) -> Dict[str, Any]:
    meta_name, meta_loc = SENSOR_METADATA.get(device_id, (name or f"ESP32 Node {device_id}", location))
    return {
        "device_id": device_id,
        "name": meta_name,
        "location": meta_loc,
        "status": "Waiting for Telemetry",
        "battery_mv": 0,
        "rssi_dbm": 0,
        "co2_ppm": 0,
        "temperature": 0.0,
        "humidity": 0.0,
        "pm25": 0.0,
        "aqi": compute_aqi(0, 0.0),
        "aqi_level": 1,
        "timestamp": 0,
        "last_seen": 0,
    }

# ==============================================================================
# Thread-Safe In-Memory Sensor State Registry (Concurrency Hardened)
# ==============================================================================
class SensorStateStore:
    """
    Thread-safe, high-concurrency in-memory registry for active sensor telemetry.
    Protects state mutations from background stream reader and provides
    consistent, atomic snapshots to FastHTML / Starlette async request handlers.
    """
    def __init__(self, initial_state: Optional[Dict[str, Dict[str, Any]]] = None):
        self._lock = threading.RLock()
        self._sensors: Dict[str, Dict[str, Any]] = initial_state or {}

    def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Returns an atomic copy of a sensor state."""
        with self._lock:
            sensor = self._sensors.get(device_id)
            return dict(sensor) if sensor else None

    def get_all(self) -> List[Dict[str, Any]]:
        """Returns a thread-safe snapshot list of all active sensor states."""
        with self._lock:
            return [dict(s) for s in self._sensors.values()]

    def count(self) -> int:
        """Returns the number of active sensors."""
        with self._lock:
            return len(self._sensors)

    def get_metadata(self, device_id: str) -> Tuple[str, str]:
        """Returns (name, location) with safe default fallbacks."""
        if device_id in SENSOR_METADATA:
            return SENSOR_METADATA[device_id]
        with self._lock:
            s = self._sensors.get(device_id)
            if s:
                return s.get("name", f"ESP32 Node {device_id}"), s.get("location", "Facility")
            return f"ESP32 Node {device_id}", "Facility"

    def update(self, device_id: str, telemetry: Dict[str, Any]) -> None:
        """Atomically updates sensor telemetry in the registry."""
        with self._lock:
            self._sensors[device_id] = telemetry

    # Dict-like compatibility interface
    def __getitem__(self, device_id: str) -> Dict[str, Any]:
        val = self.get(device_id)
        if val is None:
            raise KeyError(device_id)
        return val

    def __setitem__(self, device_id: str, telemetry: Dict[str, Any]) -> None:
        self.update(device_id, telemetry)

    def __len__(self) -> int:
        return self.count()

    def values(self) -> List[Dict[str, Any]]:
        return self.get_all()

# Thread-Safe In-Memory Sensor State Registry (Pure Dynamic Auto-Discovery)
sensor_store = SensorStateStore({})

# Active SSE subscriber queues
sse_subscribers: Set[asyncio.Queue] = set()

def _safe_push_queue(q: asyncio.Queue, item: Any):
    """Pushes item to queue; drops oldest element if bounded queue is full."""
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            q.put_nowait(item)
        except Exception:
            pass

class StreamBridge:
    """
    Asynchronous Local IPC Reader.
    Reads the JSON event stream of the Rust Ingestor (:9100/stream), keeps the latest
    state of each sensor in memory and forwards every event to the browser SSE clients.
    """
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._read_loop())
            print(f"[Stream Bridge] Started async listener to Rust Ingestor at {INGESTOR_HOST}:{INGESTOR_PORT}/stream")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
            print("[Stream Bridge] Stopped listener cleanly.")

    async def _read_loop(self):
        """Persistent SSE reader loop with exponential backoff and zero-allocation socket streaming."""
        backoff = 1.0
        while self._running:
            try:
                reader, writer = await asyncio.open_connection(INGESTOR_HOST, INGESTOR_PORT)
                backoff = 1.0
                print(f"[Stream Bridge] Connected to Rust Ingestor IPC stream ({INGESTOR_HOST}:{INGESTOR_PORT}).")

                req = (
                    f"GET /stream HTTP/1.1\r\n"
                    f"Host: {INGESTOR_HOST}:{INGESTOR_PORT}\r\n"
                    f"Accept: text/event-stream\r\n"
                    f"Connection: keep-alive\r\n\r\n"
                )
                writer.write(req.encode("ascii"))
                await writer.drain()

                while self._running:
                    line = await reader.readline()
                    if not line:
                        break # Server disconnected
                    
                    line_str = line.decode("utf-8", errors="replace").strip()
                    if line_str.startswith("data: "):
                        raw_data = line_str[6:].strip()
                        
                        if raw_data.startswith("{"):
                            try:
                                sample = orjson.loads(raw_data)
                                device_id = sample["device_id"]

                                co2 = int(sample.get("co2_ppm", 0))
                                pm25 = round(float(sample.get("pm25_ug_m3", 0.0)), 2)
                                tvoc = float(sample.get("tvoc_ppb", 0.0))
                                aqi = compute_aqi(co2, pm25, tvoc)
                                name, location = sensor_store.get_metadata(device_id)

                                telemetry = {
                                    "device_id": device_id,
                                    "name": name,
                                    "location": location,
                                    "status": "Online",
                                    "battery_mv": int(sample.get("battery_millivolts", 0)),
                                    "rssi_dbm": int(sample.get("rssi_dbm", 0)),
                                    "co2_ppm": co2,
                                    "temperature": round(float(sample.get("temperature_celsius", 0.0)), 2),
                                    "humidity": round(float(sample.get("humidity_percent", 0.0)), 2),
                                    "pm25": pm25,
                                    "aqi": aqi,
                                    "aqi_level": int(sample.get("aqi_level", aqi["level"])),
                                    "timestamp": int(sample.get("timestamp_ns", 0)) // 1_000_000_000,
                                    "last_seen": int(time.time()),
                                }
                                sensor_store.update(device_id, telemetry)

                                # Serialized once, shared by every browser subscriber
                                event_json = orjson.dumps(telemetry).decode("utf-8")
                                for q in list(sse_subscribers):
                                    _safe_push_queue(q, event_json)

                            except Exception as parse_err:
                                print(f"[Stream Bridge] Error parsing incoming sample: {parse_err}")

                writer.close()
                await writer.wait_closed()
            except asyncio.CancelledError:
                break
            except Exception as ex:
                # Ingestor may be starting or temporarily offline; retry cleanly
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 1.5)

stream_bridge = StreamBridge()
