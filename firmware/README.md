# Firmware

ESP32 firmware of the sensor node (PlatformIO, Arduino framework). Hardware, wiring and commissioning are described in [`hardware/README.md`](../hardware/README.md) (French).

Every 5 s the node publishes one `AirQualityPacket` on `sensors/<DEVICE_ID>/airquality` ([contract](../contracts/proto/air_quality.proto)).

| File | Role |
| :--- | :--- |
| `src/main.cpp` | Sensors, Wi-Fi, MQTT, offline queue, watchdog |
| `include/config.h` | Pins, intervals, limits |
| `include/secrets.h` | Wi-Fi and MQTT credentials, `DEVICE_ID`. Not committed: copy `secrets.example.h` |
| `include/telemetry.h` | Sensor snapshot to packet, `status` computation |
| `include/pms7003.h`, `include/scd4x.h` | Sensor frame parsers |
| `include/air_quality.pb.h`, `src/air_quality.pb.c` | Nanopb bindings of the contract |
| `tools/mock_publisher.py` | Simulates three nodes from a PC |
| `test_host/` | Host test of the parsers and of the packet encoding |

## Behaviour

- **Offline queue.** While Wi-Fi or the broker is unavailable, packets are appended to `/offline_queue.bin` on flash (512 KB, about 11 h) and replayed on reconnection. When full, the queue is cleared and restarts. Packets produced before the clock is synchronised are not queued.
- **Clock.** NTP, UTC. Until synchronised, packets carry timestamp 0 and the ingestor uses the reception time.
- **Status.** `WARMING_UP` during the first 30 s (PMS7003) and until BSEC has calibrated (about 5 min); `DEGRADED` when a sensor is silent (its fields are 0); `FAULT` when all are.
- **Watchdog.** The node resets if the main loop stalls for 15 s.
- **Memory.** Buffers are statically allocated; nothing is allocated on the heap in the main loop.

## Build

```bash
cp include/secrets.example.h include/secrets.h
pio run -e esp32dev -t upload          # real sensors
pio run -e esp32dev-mock -t upload     # no sensor wired: synthetic values, same wire format
pio device monitor -b 115200
NANOPB_DIR=/path/to/nanopb test_host/run.sh
```

`DEVICE_ID` must be unique per node and match `[A-Za-z0-9_-]{1,31}`.
