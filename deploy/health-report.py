#!/usr/bin/env python3
"""
Daily health report of the platform (standard library only).

Appends one line per sensor node to a CSV file (the raw material of the V1 feedback
log) and raises an alert when something needs attention. Run once a day by
iot-health-report.timer; can also be run by hand.

Environment:
  INFLUXDB_TOKEN   required
  INFLUXDB_URL     default http://127.0.0.1:8086
  INFLUXDB_BUCKET  default airquality
  METRICS_PORT     ingestor HTTP port, default 9100
  HEALTH_CSV       default /var/lib/iot-airquality/health.csv
  ALERT_URL        optional. Alerts are POSTed there as plain text (an ntfy topic URL
                   or any webhook). Without it, alerts only appear in the journal.

Exit status: 0 when healthy, 1 when at least one alert was raised.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

INFLUX_URL = os.getenv("INFLUXDB_URL", "http://127.0.0.1:8086")
INFLUX_DB = os.getenv("INFLUXDB_BUCKET", "airquality")
INFLUX_TOKEN = os.environ["INFLUXDB_TOKEN"]
METRICS_URL = f"http://127.0.0.1:{os.getenv('METRICS_PORT', '9100')}/metrics"
HEALTH_CSV = os.getenv("HEALTH_CSV", "/var/lib/iot-airquality/health.csv")
ALERT_URL = os.getenv("ALERT_URL")

EXPECTED_POINTS_PER_DAY = 86400 // 5  # one packet every 5 s
MIN_COMPLETENESS_PCT = 99.0           # exit criterion of docs/V1_VALIDATION.md
MAX_SILENCE_MINUTES = 10
MAX_DISK_USED_PCT = 90

COLUMNS = [
    "date", "device_id", "points_24h", "completeness_pct", "missing_by_seq", "node_reboots",
    "minutes_since_last", "packets_dropped_total", "influx_errors_total", "influx_buffered_samples",
    "disk_used_pct", "ingestor_restarts", "dashboard_restarts", "alerts",
]


def influx(query: str) -> list:
    """Runs an InfluxQL query; returns its series (possibly empty)."""
    url = f"{INFLUX_URL}/query?" + urllib.parse.urlencode({"db": INFLUX_DB, "epoch": "s", "q": query})
    request = urllib.request.Request(url, headers={"Authorization": f"Token {INFLUX_TOKEN}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["results"][0].get("series", [])


def per_device(query: str) -> dict:
    """{device_id: first row} for a query grouped by device_id."""
    return {s["tags"]["device_id"]: s["values"][0] for s in influx(query)}


def ingestor_metrics() -> dict:
    with urllib.request.urlopen(METRICS_URL, timeout=10) as response:
        lines = response.read().decode().splitlines()
    return {name: float(value) for name, value in (l.split() for l in lines if l.startswith("iot_") and "{" not in l)}


def service_restarts(unit: str) -> str:
    """NRestarts of a systemd unit, or "" where systemd is not available."""
    try:
        out = subprocess.run(["systemctl", "show", unit, "-p", "NRestarts", "--value"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except OSError:
        return ""


def main() -> int:
    alerts = []  # (device_id or "", message)
    now = time.time()

    try:
        metrics = ingestor_metrics()
    except OSError as ex:
        metrics = {}
        alerts.append(("", f"ingestor unreachable ({ex})"))
    if metrics.get("iot_influx_buffered_samples", 0) > 0:
        alerts.append(("", "ingestor is buffering: InfluxDB writes are failing"))

    disk = shutil.disk_usage("/")
    disk_used_pct = round(100 * disk.used / disk.total)
    if disk_used_pct > MAX_DISK_USED_PCT:
        alerts.append(("", f"disk {disk_used_pct} % full"))

    # Nodes seen during the last 7 days: a node that just went silent is still reported,
    # a node retired for more than a week is not.
    last_seen = per_device("SELECT last(temperature) FROM air_quality WHERE time > now() - 7d GROUP BY device_id")
    devices = sorted(last_seen)
    if not devices:
        alerts.append(("", "no sensor node has reported for 7 days"))

    last_24h = "WHERE time > now() - 24h GROUP BY device_id"
    points = per_device(f"SELECT count(temperature) FROM air_quality {last_24h}")
    # seq grows by 1 per packet and restarts at 1 after a node reboot:
    # a negative step is a reboot, a step above 1 is a gap of (step - 1) lost packets.
    steps = f"(SELECT difference(seq) AS step FROM air_quality {last_24h})"
    reboots = per_device(f"SELECT count(step) FROM {steps} WHERE step < 0 GROUP BY device_id")
    gaps = per_device(f"SELECT sum(step), count(step) FROM {steps} WHERE step > 1 GROUP BY device_id")

    rows = []
    for device in devices:
        count = points.get(device, [0, 0])[1]
        completeness = round(100 * count / EXPECTED_POINTS_PER_DAY, 1)
        silence_min = round((now - last_seen[device][0]) / 60)
        gap = gaps.get(device)
        missing = int(gap[1] - gap[2]) if gap else 0
        if silence_min > MAX_SILENCE_MINUTES:
            alerts.append((device, f"silent for {silence_min} min"))
        if completeness < MIN_COMPLETENESS_PCT:
            alerts.append((device, f"completeness {completeness} % over 24 h"))
        rows.append({
            "device_id": device, "points_24h": count, "completeness_pct": completeness,
            "missing_by_seq": missing, "node_reboots": reboots.get(device, [0, 0])[1],
            "minutes_since_last": silence_min,
        })
    if not rows:
        rows.append({"device_id": ""})

    shared = {
        "date": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        "packets_dropped_total": int(metrics.get("iot_packets_dropped_total", -1)),
        "influx_errors_total": int(metrics.get("iot_influx_errors_total", -1)),
        "influx_buffered_samples": int(metrics.get("iot_influx_buffered_samples", -1)),
        "disk_used_pct": disk_used_pct,
        "ingestor_restarts": service_restarts("iot-ingestor"),
        "dashboard_restarts": service_restarts("iot-dashboard"),
    }
    new_file = not os.path.exists(HEALTH_CSV)
    with open(HEALTH_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            writer.writeheader()
        for row in rows:
            mine = [msg for device, msg in alerts if device in ("", row["device_id"])]
            writer.writerow({**shared, **row, "alerts": "; ".join(mine)})

    if not alerts:
        print("Healthy.")
        return 0

    text = "IoT Air Quality: " + "; ".join(f"{device}: {msg}" if device else msg for device, msg in alerts)
    print(f"ALERT {text}", file=sys.stderr)
    if ALERT_URL:
        try:
            urllib.request.urlopen(urllib.request.Request(ALERT_URL, data=text.encode()), timeout=15)
        except OSError as ex:
            print(f"Alert could not be sent to ALERT_URL: {ex}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
