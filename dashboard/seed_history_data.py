#!/usr/bin/env python3
"""
Seed InfluxDB Database with 7 Days of Historical Telemetry Data.
Populates realistic environmental curves (CO2, Temperature, Humidity, PM2.5, Battery, RSSI)
for testing FastHTML UI downsampling and uPlot canvas rendering.
"""

import os
import sys
import time
import math
import random
import urllib.request

INFLUX_URL = os.getenv("INFLUXDB_URL", "http://127.0.0.1:8086")
TOKEN = os.getenv(
    "INFLUXDB_TOKEN",
    "kH9pBO5KNEvbEh620uQz_T21DvVnVxb9OsvXAC-YZJN5esopKVVKOCugDTq7aBlcWMjQl5562RyHopbSE2vlow=="
)
ORG = os.getenv("INFLUXDB_ORG", "baremetal-iot")
BUCKET = os.getenv("INFLUXDB_DATABASE", os.getenv("INFLUXDB_BUCKET", "airquality"))

SENSORS = [
    {"id": "sensor-esp32-01", "base_co2": 520, "base_temp": 22.0, "base_hum": 46.0, "base_pm25": 4.5},
    {"id": "sensor-esp32-02", "base_co2": 420, "base_temp": 20.8, "base_hum": 42.0, "base_pm25": 1.2},
    {"id": "sensor-esp32-03", "base_co2": 660, "base_temp": 23.8, "base_hum": 52.0, "base_pm25": 11.5},
]

def seed():
    write_url = f"{INFLUX_URL.rstrip('/')}/api/v2/write?org={ORG}&bucket={BUCKET}&precision=s"
    print(f"[Seed] Seeding 7 days of historical telemetry into InfluxDB: {write_url}")

    now = int(time.time())
    lines = []

    # 7 days with a 5-minute sampling resolution = 2016 points per sensor
    for s in SENSORS:
        for i in range(2016):
            ts = now - ((2016 - i) * 300)
            angle = (i / 288) * 2 * math.pi
            noise = (random.random() - 0.5)

            co2 = int(s["base_co2"] + 130.0 * math.sin(angle - 1.0) + noise * 20.0)
            temp = round(s["base_temp"] + 2.8 * math.sin(angle - 2.0) + noise * 0.3, 2)
            hum = round(s["base_hum"] + 7.0 * math.cos(angle - 2.0) + noise * 0.8, 1)
            pm25 = round(max(0.5, s["base_pm25"] + 3.5 * math.sin(angle * 2.0) + noise * 1.5), 2)
            battery = 3850 - ((i // 100) % 200)
            rssi = -62 - (i % 10)

            line = f"air_quality,device_id={s['id']} co2={co2}i,temperature={temp},humidity={hum},pm25={pm25},battery_mv={battery}i,rssi={rssi}i,status=0i {ts}"
            lines.append(line)

    # Send in chunks of 1000 lines
    chunk_size = 1000
    for i in range(0, len(lines), chunk_size):
        chunk = "\n".join(lines[i:i+chunk_size])
        req = urllib.request.Request(
            write_url,
            data=chunk.encode("utf-8"),
            headers={
                "Authorization": f"Token {TOKEN}",
                "Content-Type": "text/plain; charset=utf-8"
            }
        )
        with urllib.request.urlopen(req) as resp:
            if resp.status != 204:
                print(f"Warning: unexpected write status {resp.status}")

    print(f"✔ Successfully inserted {len(lines)} historical records in InfluxDB (Bucket: {BUCKET})!")

if __name__ == "__main__":
    seed()
