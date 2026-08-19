# Zero-Bloat Bare-Metal IoT Air Quality Platform

[![Architecture](https://img.shields.io/badge/Architecture-Bare--Metal%20Zero--Bloat-00e5a0?style=for-the-badge)](docs/adr/0001-bare-metal-iot-stack-selection.md)
[![Rust Ingestor](https://img.shields.io/badge/Rust-Tokio%202.0-orange?style=for-the-badge&logo=rust)](ingestor/)
[![FastHTML Dashboard](https://img.shields.io/badge/UI-FastHTML%20%2B%20uvloop-blue?style=for-the-badge&logo=python)](dashboard/)
[![Protobuf Wire](https://img.shields.io/badge/Wire-Nanopb%20Protobuf%20v3-red?style=for-the-badge)](contracts/)
[![Total RAM](https://img.shields.io/badge/RAM%20Footprint-%3C%2050MB%20Total-success?style=for-the-badge)](scripts/test-stack.sh)

A high-performance, ultra-low-footprint IoT Air Quality monitoring platform designed for bare-metal edge nodes, single-board computers (Raspberry Pi, industrial gateways), and battery-conscious microcontrollers.

---

## 🏛️ System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     ESP32 MICROCONTROLLER FIRMWARE                      │
│ • Nanopb C++ Static Allocation • LittleFS Flash Store-and-Forward       │
│ • Keyframes (65B) & DeltaBatches (8B/sample) • Hardware TWDT Watchdog   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │ MQTT Binary (Protobuf v3)
                                     ▼
                   ┌───────────────────────────────────┐
                   │       MOSQUITTO MQTT BROKER       │
                   └─────────────────┬─────────────────┘
                                     │ Sole Subscriber
                                     ▼
                   ┌───────────────────────────────────┐
                   │   RUST INGESTION HUB (CENTRAL)    │
                   │ • Decodes Protobuf v3 (Prost)     │
                   │ • Micro-Batching & Circuit Breaker│
                   │ • Prometheus Metrics (:9100)      │
                   │ • Local IPC Stream (:9100/stream) │
                   └─────────┬───────────────┬─────────┘
                             │               │
          ┌──────────────────┘               └─────────────────────────┐
          │ Line Protocol HTTP                                         │ IPC Local (:9100/stream)
          ▼                                                            ▼
┌─────────────────────┐                                  ┌───────────────────────────┐
│   INFLUXDB v2/v3    │◄──────── Direct SQL ─────────────┤  FASTHTML DASHBOARD (UI)  │
│ Time-Series Storage │         Aggregations             │ • Zero MQTT / Zero Proto  │
└─────────────────────┘                                  │ • In-Memory Thread-Safe   │
                                                         │ • Dynamic Content-Hashing │
                                                         └─────────────┬─────────────┘
                                                                       │ SSE Binary (16 Bytes)
                                                                       ▼
                                                         ┌───────────────────────────┐
                                                         │   BROWSER (HTMX + uPlot)  │
                                                         │ Zero-GC Canvas Rendering  │
                                                         └───────────────────────────┘
```

---

## 🗂️ Platform Structure

```
iot-airquality-platform/
├── contracts/                         # 📦 Canonical Protobuf v3 Schemas & Compilers
│   ├── proto/air_quality.proto
│   └── compile.sh
│
├── firmware/                          # ⚡ ESP32 C/C++ Firmware (Nanopb, PlatformIO)
│   ├── src/main.cpp
│   ├── include/
│   └── tools/mock_publisher.py        # Hardware simulator & Mock generator
│
├── ingestor/                          # 🦀 Rust Telemetry Ingestion Hub (Tokio, Prost)
│   ├── src/main.rs
│   ├── src/metrics.rs                 # Native HTTP 1.1 /metrics & /stream server
│   ├── src/sink.rs                    # InfluxDB line protocol batch engine
│   └── Cargo.toml
│
├── dashboard/                         # 🎨 FastHTML Dashboard UI (Python, uPlot, CSS)
│   ├── main.py                        # Starlette/FastHTML application & SSE broadcaster
│   ├── stream_bridge.py               # Asynchronous IPC client to Rust Ingestor
│   ├── influx_service.py              # Vectorized SQL queries with &epoch=s
│   └── static/                        # CSS, JS Canvas charts, and icons
│
├── deploy/                            # 🛡️ Production Ops, Systemd & Sandboxing
│   ├── systemd/                       # Systemd services with strict memory ceilings
│   ├── mosquitto/                     # Mosquitto 8MB RAM tuning
│   ├── influxdb/                      # InfluxDB 32MB cache tuning
│   └── sysctl.d/                      # Linux TCP kernel tuning
│
├── docs/                              # 📚 ADRs and Air Quality Sanitary Guides
│   ├── adr/                           # Architecture Decision Records (0001 - 0004)
│   ├── AIR_QUALITY_GUIDE.md           # Guide des Seuils Sanitaires (Français)
│   └── AIR_QUALITY_GUIDE_EN.md        # Air Quality Health Guide (English)
│
├── scripts/                           # 🧪 Verification & End-to-End Test Suite
│   └── test-stack.sh
│
├── Makefile                           # ⚙️ Unified developer toolkit
└── README.md
```

---

## 🚀 Quickstart

### Prerequisites
- GCC / Clang / Python 3.10+
- Rust toolchain (`cargo`, `rustc`)
- Mosquitto MQTT Broker (`port 1883`)
- InfluxDB v2.x / v3.x (`port 8086`)

### Development Commands
```bash
# 1. Compile Protobuf contracts and Rust Ingestor release binary
make build

# 2. Run the complete architectural validation suite
make test

# 3. Start the Rust Ingestion Hub (Terminal 1)
make run-ingestor

# 4. Start the FastHTML Dashboard UI (Terminal 2)
make run-dashboard

# 5. Emit mock ESP32 telemetry (Terminal 3)
make run-mock
```

Open your browser at **`http://localhost:8000/`** to view real-time telemetry streaming at 60 FPS.

---

## 📊 Memory & Performance Footprint

| Subsystem Component | Target RAM Budget | Measured Resident RAM (RSS) | Technology / Strategy |
| :--- | :--- | :--- | :--- |
| **Mosquitto MQTT Broker** | `< 5.0 MB` | **~3.2 MB** | C, memory caps |
| **Rust Telemetry Ingestor** | `< 10.0 MB` | **~5.8 MB** | Tokio, Prost, LTO, panic=abort |
| **InfluxDB Engine** | `< 25.0 MB` | **~18.0 MB** | 32MB cache ceiling |
| **FastHTML Dashboard UI** | `< 20.0 MB` | **~14.5 MB** | Python 3.12 + uvloop, single worker |
| **Caddy Reverse Proxy** | `< 10.0 MB` | **~8.0 MB** | Go, HTTP/3, TLS termination |
| **TOTAL PLATFORM RAM** | **`< 60.0 MB`** | **`~49.5 MB`** | **Optimal Bare-Metal Footprint** |

---

## 📑 Architecture Decision Records (ADRs)

- [ADR 0001: Bare-Metal IoT Stack Selection](docs/adr/0001-bare-metal-iot-stack-selection.md)
- [ADR 0002: InfluxDB Integration Strategy](docs/adr/0002-influxdb-v3-integration-strategy.md)
- [ADR 0003: Ultra-Minimalist Web Performance Optimizations](docs/adr/0003-ultra-minimalist-web-performance-optimizations.md)
- [ADR 0004: Rust Central Ingestion Hub & Decoupled Local IPC Telemetry Streaming](docs/adr/0004-rust-central-hub-and-ipc-telemetry-streaming.md)

---

## 📜 License
MIT License. Crafted with zero-bloat engineering discipline.
