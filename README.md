# IoT Air Quality Platform

[![CI](https://github.com/fl00/iot-airquality-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/fl00/iot-airquality-platform/actions/workflows/ci.yml)

Indoor air quality monitoring on a single small host (Raspberry Pi, home server): ESP32 sensor nodes measure CO2, particulate matter, VOC, temperature, humidity and pressure; the platform stores the measurements and shows them live and over time.

**Status: V1 prototype.** The goal of V1 is an end-to-end chain that can run unattended for several weeks and produce trustworthy data. Scope decisions and deferred optimisations are recorded in [ADR 0005](docs/adr/0005-v1-scope.md); the validation procedure is in [docs/V1_VALIDATION.md](docs/V1_VALIDATION.md).

## Contents

- [Architecture](#architecture)
- [Data Contracts](#data-contracts)
- [Behaviour on Failure](#behaviour-on-failure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Tests](#tests)
- [Security](#security)
- [Known Limits of V1](#known-limits-of-v1)
- [Repository Layout](#repository-layout)
- [Documentation](#documentation)
- [License](#license)

## Architecture

```
 ESP32 node ──MQTT/Protobuf──► Mosquitto ──► Ingestor (Rust) ──line protocol──► InfluxDB 2
 (firmware/)                    :1883         127.0.0.1:9100                      127.0.0.1:8086
                                                   │                                   ▲
                                                   │ JSON events (SSE)                 │ InfluxQL
                                                   ▼                                   │
                                              Dashboard (Python) ──────────────────────┘
                                              127.0.0.1:8000
                                                   ▲
                                                   │ HTTP + SSE
                                              Caddy :80 ◄── browser
```

| Component | Technology | Responsibility |
| :--- | :--- | :--- |
| Sensor node | ESP32, PlatformIO, Nanopb | Reads BME680, SCD41 and PMS7003; publishes one packet every 5 s; buffers on flash while offline |
| Broker | Mosquitto | MQTT transport, authenticated |
| Ingestor | Rust, Tokio, Prost | Only MQTT subscriber. Validates packets, writes to InfluxDB in batches, computes the AQI level, publishes the live event stream and Prometheus metrics |
| Storage | InfluxDB 2 | Time series, 90-day retention |
| Dashboard | Python, FastHTML, HTMX, uPlot | Live cards and history charts. Reads the ingestor stream and InfluxDB; has no MQTT or Protobuf dependency |
| Reverse proxy | Caddy | Single HTTP entry point |

Only two ports are reachable from the network: 1883 (sensors) and 80 (browsers). Everything else listens on `127.0.0.1`.

## Data Contracts

The platform has three interfaces. Each has exactly one definition.

### 1. Sensor node to ingestor (MQTT, Protobuf)

- Topic: `sensors/<device_id>/airquality`, one `AirQualityPacket` per message, about 64 bytes.
- Schema: [`contracts/proto/air_quality.proto`](contracts/proto/air_quality.proto). It is the reference; field semantics are documented there.
- `device_id` matches `[A-Za-z0-9_-]{1,31}` and must equal the id in the topic.
- A field whose sensor is missing is `0`, and `status` is `STATUS_DEGRADED`.
- `base_timestamp_sec` is `0` until the node clock is synchronised; the ingestor then uses the reception time.

### 2. Ingestor to InfluxDB (line protocol)

Measurement `air_quality`, tag `device_id`, timestamp in nanoseconds.

| Field | Type | Unit | Note |
| :--- | :--- | :--- | :--- |
| `co2` | integer | ppm | Omitted when the node reports 0 (sensor missing) |
| `pressure` | float | hPa | Omitted when the node reports 0 (sensor missing) |
| `temperature` | float | °C | |
| `humidity` | float | % | |
| `pm25`, `pm10` | float | µg/m³ | |
| `tvoc` | float | ppb | BSEC breath-VOC equivalent |
| `battery_mv` | integer | mV | 0 = mains powered |
| `rssi` | integer | dBm | |
| `status` | integer | | `SensorStatus` value: 0 OK, 1 warming up, 2 degraded, 3 fault |

### 3. Ingestor live stream (HTTP, Server-Sent Events)

`GET http://127.0.0.1:9100/stream` sends one JSON object per accepted packet:

```json
{"device_id":"kitchen","timestamp_ns":1794000000000000000,"co2_ppm":650,
 "temperature_celsius":21.5,"humidity_percent":45.0,"pm25_ug_m3":3.0,"pm10_ug_m3":5.0,
 "tvoc_ppb":500.0,"pressure_hpa":1013.2,"battery_millivolts":0,"rssi_dbm":-60,
 "status":0,"aqi_level":1}
```

`aqi_level` (1 excellent to 5 bad) is the worst of the CO2, PM2.5 and VOC sub-levels. It is computed in one place, `compute_aqi_level` in [`ingestor/src/metrics.rs`](ingestor/src/metrics.rs); thresholds and their rationale are in the [air quality guide](docs/AIR_QUALITY_GUIDE_EN.md).

The same port serves `GET /metrics` (Prometheus text format) and `GET /health`.

## Behaviour on Failure

| Situation | Behaviour | Data lost |
| :--- | :--- | :--- |
| Node loses Wi-Fi or the broker | Packets are appended to a 512 KB flash queue (about 11 h) and replayed on reconnection | None up to 11 h; beyond, the queue is cleared and restarts |
| Node has no synchronised clock and is offline | Packets are not queued (they could not be dated on replay) | Yes, until NTP has synchronised once |
| A sensor is disconnected | Its fields are 0, `status` = degraded; CO2 and pressure are not stored; the dashboard shows "Degraded" | That sensor's values only |
| Node stops publishing | Dashboard shows "Offline" after 30 s | — |
| Invalid packet (bad id, topic mismatch, non-finite value, undecodable) | Dropped and counted in `iot_packets_dropped_total` | That packet |
| InfluxDB unreachable or failing | Up to 5,000 samples are kept in memory (about 7 h for one node) and retried every 30 s | None up to the buffer size; then the oldest samples |
| Ingestor stopped (`systemctl stop`, Ctrl-C) | Pending batch is written before exit | None for accepted packets |
| Ingestor or broker down | The node cannot tell that nobody listens | Yes, for the duration |
| InfluxDB has no data for a chart | Empty chart; values are never synthesised | — |

## Getting Started

### Prerequisites

- Rust toolchain, Python 3.10+, Mosquitto, InfluxDB 2.x
- For the firmware: PlatformIO (`pipx install platformio`)

### Local run with simulated sensors

```bash
cp .env.example .env                               # then set INFLUXDB_TOKEN
pip install -r dashboard/requirements.txt paho-mqtt protobuf
make build                                         # release build of the ingestor
make start                                         # ingestor + dashboard + 3 simulated sensors
```

Open `http://localhost:8000/`. `make status`, `make logs` and `make stop` manage the stack; `make help` lists every target.

### Real sensor node

Wiring, firmware build and commissioning are described in [`hardware/README.md`](hardware/README.md) (French). In short:

```bash
cd firmware
cp include/secrets.example.h include/secrets.h     # Wi-Fi, broker address, DEVICE_ID
pio run -e esp32dev -t upload && pio device monitor
```

### Deployment on a dedicated host

```bash
make build && sudo ./deploy/deploy-baremetal.sh
```

See [`deploy/README.md`](deploy/README.md).

## Configuration

All settings are environment variables. Locally they are read from `.env`; in production from `/etc/iot-airquality/env`.

| Variable | Default | Used by | Purpose |
| :--- | :--- | :--- | :--- |
| `INFLUXDB_TOKEN` | none, required | ingestor, dashboard | InfluxDB API token. Services refuse to start without it |
| `INFLUXDB_URL` | `http://127.0.0.1:8086` | ingestor, dashboard | |
| `INFLUXDB_ORG` | `baremetal-iot` | ingestor | |
| `INFLUXDB_BUCKET` | `airquality` | ingestor, dashboard | |
| `MQTT_BROKER_HOST` / `MQTT_BROKER_PORT` | `127.0.0.1` / `1883` | ingestor, simulator | |
| `MQTT_USER` / `MQTT_PASSWORD` | unset | ingestor, simulator | Required when the broker enforces authentication (always in production) |
| `MQTT_TOPIC` | `sensors/+/airquality` | ingestor | |
| `INGESTOR_BATCH_SIZE` | `12` | ingestor | Samples per InfluxDB write |
| `INGESTOR_FLUSH_INTERVAL_SECS` | `30` | ingestor | Maximum delay before a write, and retry period |
| `METRICS_PORT` | `9100` | ingestor, dashboard | Ingestor HTTP port on `127.0.0.1` |

Sensor node settings (pins, intervals, credentials) are compile-time constants in `firmware/include/config.h` and `secrets.h`.

## Tests

```bash
make test
```

| Check | Covers |
| :--- | :--- |
| Ingestor unit tests | Packet validation, line protocol, AQI thresholds, stream event |
| End-to-end test (`scripts/e2e_test.py`) | A packet published over MQTT reaches the InfluxDB write and the dashboard; rejected packets; retry after a failed write; flush on SIGTERM. InfluxDB is replaced by a local recorder, so no database is needed |
| Firmware host test (`firmware/test_host/run.sh`) | Sensor parsers and Protobuf encoding, decoded by the Python bindings. Runs when `NANOPB_DIR` is set |

Not covered by automation: the firmware on real hardware, and a real InfluxDB instance. Both are part of the [V1 validation](docs/V1_VALIDATION.md).

## Security

| Threat | Measure |
| :--- | :--- |
| Secrets in the repository | None. Tokens and passwords are generated at deployment and stored in `/etc/iot-airquality/` (root-owned) |
| Rogue MQTT client | Broker requires a user and password; the ingestor rejects packets whose topic id differs from the payload id |
| Injection through the device id | Ids are restricted to `[A-Za-z0-9_-]` by the ingestor before storage, and again by the dashboard before any query |
| Exposure of internal services | Ingestor, InfluxDB and dashboard listen on `127.0.0.1` only |
| Resource exhaustion | Bounded queues everywhere; at most 64 concurrent live-stream browsers |
| Host compromise through a service | systemd units run as an unprivileged user with `ProtectSystem=strict`, `NoNewPrivileges`, `MemoryMax=40M` |

V1 assumes a trusted LAN: MQTT and the dashboard are not encrypted, and the dashboard has no login. See the limits below before exposing anything to the internet.

## Known Limits of V1

- No TLS on MQTT or HTTP, no dashboard authentication.
- Samples buffered by the ingestor are held in memory: a crash or power cut while InfluxDB is down loses them.
- The ingestor uses a clean MQTT session: packets sent while it is down are lost.
- The dashboard forgets its sensor list on restart; cards reappear with the next packet (within 5 s).
- VOC thresholds are derived from two published BSEC readings and must be confirmed on real sensors.
- Firmware has been compiled but not yet run on hardware.
- `deploy/iac/main.bicep` (Azure VM) predates V1 and opens MQTT and HTTP to the internet without encryption. Do not use it as is.

## Repository Layout

```
contracts/    Protobuf schema (reference for the wire format) and binding generator
firmware/     ESP32 firmware, simulator (tools/mock_publisher.py), host test
hardware/     Carrier PCB and enclosure generators, bill of materials, commissioning (French)
ingestor/     Rust service: MQTT -> InfluxDB + live stream
dashboard/    FastHTML web application
deploy/       Deployment script, systemd units, Mosquitto and Caddy configuration
scripts/      Local stack controller, test runner, end-to-end test
docs/         Architecture decision records, air quality guide, V1 validation
```

## Documentation

| Document | Content |
| :--- | :--- |
| [docs/V1_VALIDATION.md](docs/V1_VALIDATION.md) | Commissioning, weekly checks, exit criteria, feedback log |
| [docs/adr/](docs/adr/) | Architecture decision records. [ADR 0005](docs/adr/0005-v1-scope.md) defines the V1 scope and lists the deferred optimisations |
| [docs/AIR_QUALITY_GUIDE_EN.md](docs/AIR_QUALITY_GUIDE_EN.md) ([FR](docs/AIR_QUALITY_GUIDE.md)) | Indicators, health thresholds, AQI levels |
| [hardware/README.md](hardware/README.md) | Sensor node: parts, wiring, PCB, enclosure (French) |
| Component READMEs | [contracts](contracts/README.md), [firmware](firmware/README.md), [ingestor](ingestor/README.md), [dashboard](dashboard/README.md), [deploy](deploy/README.md) |

## License

Released under the [MIT License](LICENSE). The firmware links the Bosch BSEC library, which is proprietary and has its own licence terms.
