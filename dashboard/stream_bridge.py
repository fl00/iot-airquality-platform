"""
Bridge to the ingestor event stream (http://127.0.0.1:9100/stream, Server-Sent Events).

Keeps the latest state of each sensor in memory and forwards every event to the
browsers connected to /api/v1/stream. The dashboard has no MQTT or Protobuf dependency.
"""

import asyncio
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import orjson

from aqi_engine import aqi_meta

INGESTOR_HOST = os.getenv("INGESTOR_HOST", "127.0.0.1")
INGESTOR_PORT = int(os.getenv("INGESTOR_PORT", os.getenv("METRICS_PORT", "9100")))
RECONNECT_DELAY_SECONDS = 2

# Optional display names: device_id -> (name, location). Unlisted devices show their id.
SENSOR_METADATA: Dict[str, Tuple[str, str]] = {
    # "kitchen": ("Cuisine", "Rez-de-chaussée"),
}

# A node publishes every 5 s: it is considered offline after this silence.
OFFLINE_AFTER_SECONDS = 30

# SensorStatus of the wire contract -> (label, CSS class)
DEVICE_STATUS = {
    0: ("En ligne", "online"),
    1: ("Préchauffage", "warn"),
    2: ("Capteur manquant", "warn"),
    3: ("Panne", "warn"),
}
OFFLINE_LABEL = "Hors ligne"


def status_badge(sensor: Dict[str, Any]) -> Tuple[str, str]:
    """(label, CSS class) of a sensor right now, from its last event."""
    if not sensor["last_seen"]:
        return "En attente de mesures", "warn"
    if time.time() - sensor["last_seen"] > OFFLINE_AFTER_SECONDS:
        return OFFLINE_LABEL, "warn"
    return DEVICE_STATUS.get(sensor["device_status"], ("Statut inconnu", "warn"))


def initial_sensor_state(device_id: str) -> Dict[str, Any]:
    """State of a sensor that has not sent anything yet."""
    name, location = SENSOR_METADATA.get(device_id, (device_id, ""))
    return {
        "device_id": device_id,
        "name": name,
        "location": location,
        "device_status": 0,
        "battery_mv": 0,
        "rssi_dbm": 0,
        "co2_ppm": 0,
        "temperature": 0.0,
        "humidity": 0.0,
        "pm25": 0.0,
        "aqi": aqi_meta(1),
        "last_seen": 0,
    }


class SensorStateStore:
    """Latest state of every sensor seen since start-up.

    Written by the bridge task (event loop thread) and read by page handlers
    (thread pool), hence the lock. Readers get copies.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sensors: Dict[str, Dict[str, Any]] = {}

    def get(self, device_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            sensor = self._sensors.get(device_id)
            return dict(sensor) if sensor else None

    def get_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(s) for s in self._sensors.values()]

    def update(self, device_id: str, state: Dict[str, Any]) -> None:
        with self._lock:
            self._sensors[device_id] = state


sensor_store = SensorStateStore()

# One bounded queue per browser connected to /api/v1/stream
sse_subscribers: Set[asyncio.Queue] = set()


def _push(queue: asyncio.Queue, item: str) -> None:
    """Queues an event for a browser; a slow browser loses its oldest event."""
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


def handle_event(sample: Dict[str, Any]) -> None:
    """Applies one ingestor event (format: root README, "Data Contracts")."""
    device_id = sample["device_id"]
    state = initial_sensor_state(device_id)
    state.update({
        "device_status": sample["status"],
        "battery_mv": sample["battery_millivolts"],
        "rssi_dbm": sample["rssi_dbm"],
        "co2_ppm": sample["co2_ppm"],
        "temperature": round(sample["temperature_celsius"], 2),
        "humidity": round(sample["humidity_percent"], 2),
        "pm25": round(sample["pm25_ug_m3"], 2),
        "aqi": aqi_meta(sample["aqi_level"]),
        "last_seen": int(time.time()),
    })
    sensor_store.update(device_id, state)

    # Serialized once, shared by every browser
    label, css_class = status_badge(state)
    event_json = orjson.dumps({**state, "status_label": label, "status_class": css_class}).decode()
    for queue in sse_subscribers:
        _push(queue, event_json)


async def _read_stream() -> None:
    """Reads events until the ingestor closes the connection."""
    reader, writer = await asyncio.open_connection(INGESTOR_HOST, INGESTOR_PORT)
    try:
        writer.write(b"GET /stream HTTP/1.1\r\nHost: ingestor\r\n\r\n")
        await writer.drain()
        status_line = await reader.readline()
        if b" 200 " not in status_line:
            raise ConnectionError(f"unexpected answer: {status_line!r}")
        print("[Bridge] Connected to the ingestor stream.")

        while line := await reader.readline():
            if not line.startswith(b"data: "):
                continue
            try:
                handle_event(orjson.loads(line[6:]))
            except Exception as ex:  # one bad event must not stop the stream
                print(f"[Bridge] Event ignored ({ex!r}): {line[:200]!r}")
    finally:
        writer.close()


async def run_bridge() -> None:
    """Keeps the bridge connected for the lifetime of the application."""
    reported_down = False
    while True:
        try:
            await _read_stream()
            print("[Bridge] Ingestor closed the stream; reconnecting.")
            reported_down = False
        except OSError as ex:
            if not reported_down:  # once per outage, not once per attempt
                print(f"[Bridge] Ingestor unreachable at {INGESTOR_HOST}:{INGESTOR_PORT} ({ex}); retrying.")
                reported_down = True
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)
