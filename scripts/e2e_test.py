#!/usr/bin/env python3
"""
End-to-end test: MQTT packet -> Rust ingestor -> InfluxDB -> dashboard and health report.

Starts private instances of Mosquitto, InfluxDB, the ingestor and the dashboard on test
ports, with their data in a temporary directory: an InfluxDB already running on the
machine is never touched. Needs the `mosquitto` and `influxd` binaries, a built
ingestor, and the Python packages of the dashboard and of firmware/tools
(paho-mqtt, protobuf).

Usage: python3 scripts/e2e_test.py
"""

import csv
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "firmware", "tools"))

import paho.mqtt.client as mqtt  # noqa: E402
from proto.air_quality_pb2 import AirQualityPacket, FullSample, SensorStatus  # noqa: E402

MQTT_PORT, INFLUX_PORT, INGESTOR_PORT, DASHBOARD_PORT, ALERT_PORT = 18883, 18086, 19100, 18000, 18099
INFLUX_URL = f"http://127.0.0.1:{INFLUX_PORT}"
TOKEN = "e2e-" + os.urandom(16).hex()  # for the throwaway InfluxDB instance only
DEVICE = "e2e-kitchen"  # deliberately not of the form sensor-esp32-NN
QUIET = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class AlertReceiver(http.server.BaseHTTPRequestHandler):
    """Stands for the ALERT_URL of the health report."""

    received = []

    def do_POST(self):
        AlertReceiver.received.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def wait_port(port, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} did not open")


def wait_for(predicate, what, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for: {what}")


def http_get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.read().decode()


def metric(name):
    for line in http_get(INGESTOR_PORT, "/metrics").splitlines():
        if line.startswith(name + " "):
            return float(line.split()[1])
    raise KeyError(name)


def influx_setup():
    """First-run setup of the private InfluxDB instance, with the token the ingestor already uses."""
    body = json.dumps({"username": "e2e", "password": "e2e-password", "org": "baremetal-iot",
                       "bucket": "airquality", "token": TOKEN}).encode()
    request = urllib.request.Request(f"{INFLUX_URL}/api/v2/setup", data=body,
                                     headers={"Content-Type": "application/json"})
    urllib.request.urlopen(request, timeout=10).read()


def influx_rows(fields="*"):
    """Stored points of the test device, oldest first, as dicts."""
    query = f"SELECT {fields} FROM air_quality WHERE device_id = '{DEVICE}'"
    url = f"{INFLUX_URL}/query?" + urllib.parse.urlencode({"db": "airquality", "epoch": "s", "q": query})
    request = urllib.request.Request(url, headers={"Authorization": f"Token {TOKEN}"})
    series = json.load(urllib.request.urlopen(request, timeout=10))["results"][0].get("series")
    if not series:
        return []
    return [dict(zip(series[0]["columns"], row)) for row in series[0]["values"]]


def packet(device_id, seq, ts, co2, status=SensorStatus.STATUS_OK, pressure=1013.2):
    return AirQualityPacket(
        device_id=device_id, sequence_number=seq, base_timestamp_sec=ts, rssi_dbm=-60, status=status,
        full_sample=FullSample(temperature_celsius=21.5, humidity_percent=45.0, co2_ppm=co2,
                               pm25_ug_m3=3.0, pm10_ug_m3=5.0, tvoc_ppb=5000.0, pressure_hpa=pressure,
                               static_iaq=152.5, iaq_accuracy=3),
    ).SerializeToString()


def main():
    ingestor_bin = os.path.join(BASE, "ingestor", "target", "release", "iot-airquality-ingestor")
    assert os.path.exists(ingestor_bin), "ingestor binary not found: run `make build` first"
    for binary in ("mosquitto", "influxd"):
        assert shutil.which(binary), f"`{binary}` is required for the end-to-end test"

    workdir = tempfile.mkdtemp(prefix="iot-e2e-")
    procs = []
    stream = None
    try:
        # --- Broker, ingestor, dashboard. InfluxDB is started later, on purpose. --------
        with open(os.path.join(workdir, "mosquitto.conf"), "w") as f:
            f.write(f"listener {MQTT_PORT} 127.0.0.1\nallow_anonymous true\n")
        procs.append(subprocess.Popen(["mosquitto", "-c", f.name], **QUIET))
        wait_port(MQTT_PORT)

        env = dict(os.environ, MQTT_BROKER_PORT=str(MQTT_PORT), INFLUXDB_URL=INFLUX_URL, INFLUXDB_TOKEN=TOKEN,
                   METRICS_PORT=str(INGESTOR_PORT), INGESTOR_PORT=str(INGESTOR_PORT),
                   INGESTOR_BATCH_SIZE="100", INGESTOR_FLUSH_INTERVAL_SECS="1", MQTT_USER="", MQTT_PASSWORD="")
        ingestor = subprocess.Popen([ingestor_bin], env=env, **QUIET)
        procs.append(ingestor)
        wait_port(INGESTOR_PORT)
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app", "--port", str(DASHBOARD_PORT), "--log-level", "warning"],
            cwd=os.path.join(BASE, "dashboard"), env=env, **QUIET))
        wait_port(DASHBOARD_PORT)

        stream = socket.create_connection(("127.0.0.1", INGESTOR_PORT), timeout=10)
        stream.sendall(b"GET /stream HTTP/1.1\r\nHost: localhost\r\n\r\n")
        time.sleep(1.0)  # ingestor subscribed, dashboard bridge connected

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="e2e-publisher")
        client.connect("127.0.0.1", MQTT_PORT)
        client.loop_start()

        def publish(topic_id, payload):
            client.publish(f"sensors/{topic_id}/airquality", payload, qos=1).wait_for_publish(5)

        now = int(time.time())

        # --- 1. A valid packet reaches the live stream with its real id -----------------
        publish(DEVICE, packet(DEVICE, seq=1, ts=now - 120, co2=650))
        buf = b""
        while b"\ndata: {" not in buf or not buf.endswith(b"\n\n"):
            buf += stream.recv(4096)
        event = json.loads(buf.split(b"data: ", 1)[1].split(b"\n", 1)[0])
        assert event["device_id"] == DEVICE and event["co2_ppm"] == 650 and event["sequence_number"] == 1, event
        assert event["aqi_level"] == 4, f"tvoc 5000 ppb must give AQI level 4: {event}"
        print("  ok  live stream carries the real device id and the AQI level")

        # --- 2. Rejected packets -------------------------------------------------------
        publish("someone-else", packet(DEVICE, 9, now, co2=9999))              # topic != payload id
        publish("bad id,x=1", packet("bad id,x=1", 9, now, co2=9999))          # line-protocol injection
        publish(DEVICE, packet(DEVICE, 9, now, co2=9999, pressure=float("nan")))
        wait_for(lambda: metric("iot_packets_dropped_total") == 3, "3 dropped packets in /metrics")
        print("  ok  spoofed, malformed-id and non-finite packets are dropped")

        # --- 3. InfluxDB is down: the sample is kept, then written once it is up --------
        wait_for(lambda: metric("iot_influx_errors_total") >= 1 and metric("iot_influx_buffered_samples") == 1,
                 "failed write with the sample kept in memory")
        procs.append(subprocess.Popen(
            ["influxd", "--bolt-path", os.path.join(workdir, "influxd.bolt"),
             "--engine-path", os.path.join(workdir, "engine"), "--sqlite-path", os.path.join(workdir, "influxd.sqlite"),
             "--http-bind-address", f"127.0.0.1:{INFLUX_PORT}", "--reporting-disabled", "--log-level", "error"],
            **QUIET))
        wait_port(INFLUX_PORT)
        influx_setup()
        wait_for(lambda: metric("iot_influx_buffered_samples") == 0, "buffered sample written", timeout=30)
        rows = influx_rows()
        assert len(rows) == 1 and rows[0]["co2"] == 650 and rows[0]["seq"] == 1 and rows[0]["time"] == now - 120, rows
        assert rows[0]["iaq"] == 152.5 and rows[0]["iaq_accuracy"] == 3, rows
        print("  ok  InfluxDB outage: sample kept and written afterwards; nothing invalid stored")

        # --- 4. Missing sensors: impossible zeros are not stored; status is ------------
        publish(DEVICE, packet(DEVICE, seq=2, ts=now - 115, co2=0, pressure=0.0, status=SensorStatus.STATUS_DEGRADED))
        wait_for(lambda: len(influx_rows()) == 2, "second point")
        degraded = influx_rows()[1]
        assert degraded["co2"] is None and degraded["pressure"] is None, degraded
        assert degraded["status"] == 2 and degraded["temperature"] == 21.5, degraded
        print("  ok  zero CO2 / pressure from a missing sensor are not stored")

        # --- 5. Dashboard ---------------------------------------------------------------
        wait_for(lambda: json.loads(http_get(DASHBOARD_PORT, "/health"))["sensors_active"] == 1, "dashboard state")
        home = http_get(DASHBOARD_PORT, "/")
        assert DEVICE in home and "Capteur manquant" in home, "sensor card with firmware status expected"
        assert "showChart" in http_get(DASHBOARD_PORT, f"/sensor/{DEVICE}"), "detail page must draw the chart"
        # Both points fall in the same bucket of the one-year range: the mean CO2 must ignore the missing value.
        history = json.loads(http_get(DASHBOARD_PORT, f"/api/v1/sensor/{DEVICE}/history?range=1an"))
        assert [len(column) for column in history] == [1] * 5 and history[1] == [650] and history[2] == [21.5], history
        assert http_get(DASHBOARD_PORT, "/api/v1/sensor/nobody/history") == "[[],[],[],[],[]]"
        try:
            http_get(DASHBOARD_PORT, f"/api/v1/sensor/{DEVICE}/history?range=bogus")
            raise AssertionError("unknown range must be rejected")
        except urllib.error.HTTPError as e:
            assert e.code == 400
        print("  ok  dashboard shows the sensor, its status and its real history")

        # --- 6. Daily health report -----------------------------------------------------
        # seq 2 -> 5 is a gap of two packets; 5 -> 1 is a node reboot.
        publish(DEVICE, packet(DEVICE, seq=5, ts=now - 110, co2=660))
        publish(DEVICE, packet(DEVICE, seq=1, ts=now - 20, co2=670))
        wait_for(lambda: len(influx_rows()) == 4, "four points")
        alerts = http.server.HTTPServer(("127.0.0.1", ALERT_PORT), AlertReceiver)
        threading.Thread(target=alerts.serve_forever, daemon=True).start()
        health_csv = os.path.join(workdir, "health.csv")
        report = subprocess.run(
            [sys.executable, os.path.join(BASE, "deploy", "health-report.py")], capture_output=True, text=True,
            env=dict(env, HEALTH_CSV=health_csv, ALERT_URL=f"http://127.0.0.1:{ALERT_PORT}/"))
        assert report.returncode == 1, f"4 points in a day must raise an alert: {report.stderr}"
        with open(health_csv) as f:
            line = next(csv.DictReader(f))
        assert (line["device_id"], line["points_24h"], line["missing_by_seq"], line["node_reboots"]) == \
            (DEVICE, "4", "2", "1"), line
        assert line["packets_dropped_total"] == "3" and "completeness" in line["alerts"], line
        assert len(AlertReceiver.received) == 1 and DEVICE in AlertReceiver.received[0], AlertReceiver.received
        print("  ok  health report logs points, gaps and reboots, and sends the alert")

        # --- 7. SIGTERM flushes the pending batch --------------------------------------
        publish(DEVICE, packet(DEVICE, seq=2, ts=now - 15, co2=700))
        time.sleep(0.3)
        ingestor.send_signal(signal.SIGTERM)
        assert ingestor.wait(timeout=10) == 0, "ingestor must exit cleanly on SIGTERM"
        assert any(row["co2"] == 700 for row in influx_rows()), "pending sample lost on shutdown"
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
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
