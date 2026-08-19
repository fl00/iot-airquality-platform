#!/usr/bin/env python3
"""
IoT Air Quality Mock Telemetry Publisher.
Generates realistic binary Protobuf telemetry packets and publishes them to Mosquitto MQTT.
"""

import os
import sys
import time
import math
import random
import paho.mqtt.client as mqtt

# Ensure local proto import resolves
sys.path.insert(0, os.path.dirname(__file__))
from proto.air_quality_pb2 import AirQualityPacket, FullSample, SensorStatus

MQTT_HOST = os.getenv("MQTT_BROKER_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", "1883"))

SENSORS = [
    {"id": "sensor-esp32-01", "base_co2": 450, "base_temp": 22.0, "base_hum": 45.0, "base_pm25": 4.0},
    {"id": "sensor-esp32-02", "base_co2": 410, "base_temp": 20.5, "base_hum": 40.0, "base_pm25": 1.2},
    {"id": "sensor-esp32-03", "base_co2": 620, "base_temp": 23.5, "base_hum": 52.0, "base_pm25": 11.0},
]

def main():
    print("================================================================")
    print("   IoT Air Quality Protobuf Mock Telemetry Generator           ")
    print(f"   Target: {MQTT_HOST}:{MQTT_PORT} (Every 5s per sensor)       ")
    print("================================================================")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mock-sensor-publisher")
    
    try:
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        client.loop_start()
        print(f"[Publisher] Connected to Mosquitto at {MQTT_HOST}:{MQTT_PORT}")
    except Exception as ex:
        print(f"[Publisher] Warning: Could not connect to Mosquitto ({ex}).")
        print("[Publisher] Emitting packets to stdout dry-run simulation mode.")

    sequence = 0
    try:
        while True:
            sequence += 1
            now_sec = int(time.time())
            
            for s in SENSORS:
                angle = (sequence * 0.1) + (hash(s["id"]) % 10)
                noise = (random.random() - 0.5)

                co2 = int(s["base_co2"] + 110.0 * math.sin(angle * 0.4) + noise * 20.0)
                temp = round(s["base_temp"] + 2.5 * math.sin(angle * 0.2) + noise * 0.3, 2)
                hum = round(s["base_hum"] + 6.0 * math.cos(angle * 0.3) + noise * 0.8, 1)
                pm25 = round(max(0.5, s["base_pm25"] + 3.0 * math.sin(angle * 0.8) + noise * 1.2), 2)
                battery = 3850 - ((sequence // 10) % 200)
                rssi = -60 - (sequence % 15)

                # Construct Protobuf Message
                packet = AirQualityPacket(
                    device_id=s["id"],
                    sequence_number=sequence,
                    base_timestamp_sec=now_sec,
                    battery_millivolts=battery,
                    rssi_dbm=rssi,
                    status=SensorStatus.STATUS_OK,
                    full_sample=FullSample(
                        temperature_celsius=temp,
                        humidity_percent=hum,
                        co2_ppm=co2,
                        pm25_ug_m3=pm25,
                        pm10_ug_m3=round(pm25 * 1.5, 2),
                        tvoc_ppb=85.0 + 20.0 * math.sin(angle),
                        pressure_hpa=1013.25 + 2.0 * math.sin(angle * 0.1)
                    )
                )

                payload = packet.SerializeToString()
                topic = f"sensors/{s['id']}/airquality"

                try:
                    client.publish(topic, payload, qos=0)
                except Exception:
                    pass

                print(f"[TX] #{sequence:04d} -> {topic} ({len(payload)} bytes) | CO2: {co2}ppm, Temp: {temp}°C, Hum: {hum}%, PM2.5: {pm25}µg")

            time.sleep(5)
    except KeyboardInterrupt:
        print("\n[Publisher] Terminating mock publisher.")
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
