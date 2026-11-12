#!/usr/bin/env python3
"""
End-to-end test: MQTT packet -> Rust ingestor -> InfluxDB write + live stream -> dashboard.

Starts a private Mosquitto, the ingestor and the dashboard on test ports, and replaces
InfluxDB with a local HTTP listener that records what the ingestor writes. Needs the
`mosquitto` binary, a built ingestor, and the Python packages of the dashboard and of
firmware/tools (paho-mqtt, protobuf).

Usage: python3 scripts/e2e_test.py
"""

import http.server
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "firmware", "tools"))

import paho.mqtt.client as mqtt  # noqa: E402
from proto.air_quality_pb2 import AirQualityPacket, FullSample, SensorStatus  # noqa: E402

MQTT_PORT, INFLUX_PORT, INGESTOR_PORT, DASHBOARD_PORT = 18883, 18086, 19100, 18000
DEVICE = "e2e-kitchen"  # deliberately not of the form sensor-esp32-NN


class FakeInflux(http.server.BaseHTTPRequestHandler):
    """Records line-protocol writes. The first write fails to exercise the retry path."""

    writes = []
    failed_once = False

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if not FakeInflux.failed_once:
            FakeInflux.failed_once = True
            self.send_response(503)
        else:
            FakeInflux.writes.append(body)
            self.send_response(204)
        self.end_headers()

    def do_GET(self):  # history query from the dashboard: no data
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"results":[{}]}')

    def log_message(self, *args):
        pass


def wait_port(port, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} did not open")


def wait_for(predicate, what, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for: {what}")


def http_get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.read().decode()


def packet(device_id, co2, status=SensorStatus.STATUS_OK, pressure=1013.2, ts=None):
    return AirQualityPacket(
        device_id=device_id, sequence_number=1, base_timestamp_sec=ts or int(time.time()),
        rssi_dbm=-60, status=status,
        full_sample=FullSample(temperature_celsius=21.5, humidity_percent=45.0, co2_ppm=co2,
                               pm25_ug_m3=3.0, pm10_ug_m3=5.0, tvoc_ppb=5000.0, pressure_hpa=pressure),
    ).SerializeToString()


def all_lines():
    return [line for body in FakeInflux.writes for line in body.splitlines()]


def main():
    ingestor_bin = next((p for p in (
        os.path.join(BASE, "ingestor", "target", kind, "iot-airquality-ingestor") for kind in ("release", "debug")
    ) if os.path.exists(p)), None)
    assert ingestor_bin, "ingestor binary not found: run `make build` first"

    procs = []
    stream = None
    try:
        # --- Infrastructure -----------------------------------------------------------
        conf = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        conf.write(f"listener {MQTT_PORT} 127.0.0.1\nallow_anonymous true\n")
        conf.close()
        procs.append(subprocess.Popen(["mosquitto", "-c", conf.name],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        influx = http.server.ThreadingHTTPServer(("127.0.0.1", INFLUX_PORT), FakeInflux)
        threading.Thread(target=influx.serve_forever, daemon=True).start()
        wait_port(MQTT_PORT)

        env = dict(os.environ, MQTT_BROKER_PORT=str(MQTT_PORT), INFLUXDB_URL=f"http://127.0.0.1:{INFLUX_PORT}",
                   INFLUXDB_TOKEN="e2e-token", METRICS_PORT=str(INGESTOR_PORT), INGESTOR_PORT=str(INGESTOR_PORT),
                   INGESTOR_BATCH_SIZE="100", INGESTOR_FLUSH_INTERVAL_SECS="1", MQTT_USER="", MQTT_PASSWORD="")
        ingestor = subprocess.Popen([ingestor_bin], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(ingestor)
        wait_port(INGESTOR_PORT)
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--port", str(DASHBOARD_PORT), "--log-level", "warning"],
            cwd=os.path.join(BASE, "dashboard"), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        wait_port(DASHBOARD_PORT)

        stream = socket.create_connection(("127.0.0.1", INGESTOR_PORT), timeout=10)
        stream.sendall(b"GET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n")
        time.sleep(1.0)  # ingestor subscribed, dashboard bridge connected

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="e2e-publisher")
        client.connect("127.0.0.1", MQTT_PORT)
        client.loop_start()

        def publish(topic_id, payload):
            client.publish(f"sensors/{topic_id}/airquality", payload, qos=1).wait_for_publish(5)

        # --- 1. A valid packet reaches the live stream with its real id -----------------
        publish(DEVICE, packet(DEVICE, co2=650))
        buf = b""
        while b"\ndata: {" not in buf or not buf.endswith(b"\n\n"):
            buf += stream.recv(4096)
        event = json.loads(buf.split(b"data: ", 1)[1].split(b"\n", 1)[0])
        assert event["device_id"] == DEVICE and event["co2_ppm"] == 650, event
        assert event["aqi_level"] == 4, f"tvoc 5000 ppb must give AQI level 4: {event}"
        print("  ok  live stream carries the real device id and the AQI level")

        # --- 2. Rejected packets -------------------------------------------------------
        publish("someone-else", packet(DEVICE, co2=9999))             # topic != payload id
        publish("bad id,x=1", packet("bad id,x=1", co2=9999))         # line-protocol injection
        publish(DEVICE, packet(DEVICE, co2=9999, pressure=float("nan")))
        wait_for(lambda: "iot_packets_dropped_total 3" in http_get(INGESTOR_PORT, "/metrics"),
                 "3 dropped packets in /metrics")
        print("  ok  spoofed, malformed-id and non-finite packets are dropped")

        # --- 3. The first InfluxDB write fails: samples are kept and retried -----------
        wait_for(lambda: any(f"device_id={DEVICE} " in l for l in all_lines()), "retried write")
        lines = all_lines()
        assert len(lines) == 1 and "co2=650i" in lines[0], lines
        assert "9999" not in lines[0]
        print("  ok  failed InfluxDB write is retried, nothing lost, nothing invalid written")

        # --- 4. Missing sensors: impossible zeros are not stored; status is ------------
        publish(DEVICE, packet(DEVICE, co2=0, pressure=0.0, status=SensorStatus.STATUS_DEGRADED, ts=1794000000))
        wait_for(lambda: len(all_lines()) == 2, "second point")
        degraded = all_lines()[1]
        assert "co2=" not in degraded and "pressure=" not in degraded and "status=2i" in degraded, degraded
        assert degraded.endswith(" 1794000000000000000"), degraded
        print("  ok  zero CO2 / pressure from a missing sensor are omitted, device timestamp kept")

        # --- 5. Dashboard ---------------------------------------------------------------
        wait_for(lambda: json.loads(http_get(DASHBOARD_PORT, "/health"))["sensors_active"] == 1, "dashboard state")
        home = http_get(DASHBOARD_PORT, "/")
        assert DEVICE in home and "Degraded" in home, "sensor card with firmware status expected"
        assert http_get(DASHBOARD_PORT, f"/api/v1/sensor/{DEVICE}/history") == "[[],[],[],[],[]]"
        print("  ok  dashboard shows the sensor and its status; empty history stays empty")

        # --- 6. SIGTERM flushes the pending batch --------------------------------------
        publish(DEVICE, packet(DEVICE, co2=700))
        time.sleep(0.3)
        ingestor.send_signal(signal.SIGTERM)
        assert ingestor.wait(timeout=10) == 0, "ingestor must exit cleanly on SIGTERM"
        assert any("co2=700i" in l for l in all_lines()), "pending sample lost on shutdown"
        print("  ok  SIGTERM flushes the pending batch")

        client.loop_stop()
        print("END-TO-END TEST PASSED")
    finally:
        if stream:
            stream.close()
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    main()
