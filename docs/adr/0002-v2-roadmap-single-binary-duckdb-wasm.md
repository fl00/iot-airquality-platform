# ADR 0002: V2 Evolution Roadmap (Single-Binary Rust Engine, DuckDB Parquet, WASM Canvas)

## Status
**Proposed / Roadmap for V2**

## Context & Future Ambition
While V1 achieves an extraordinary **<45MB RAM total footprint** by optimizing each decoupled tier (Mosquitto, Rust Ingestor, InfluxDB v2, FastHTML, Caddy), the platform still operates 5 independent daemon processes.

For ultra-constrained edge gateways (e.g. 256MB RAM industrial routers, edge micro-appliances, battery-powered solar gateways), we seek to collapse the entire server topology into a **Single Statically-Linked Binary** and drive total idle memory consumption down to **sub-10MB RAM**.

---

## Architectural Vision for V2

```
                       ┌──────────────────────────────────────────────────────────┐
                       │           Single Statically-Linked Binary (Rust)         │
                       │                        (< 10MB RAM)                      │
                       │                                                          │
                       │   ┌──────────────────┐      ┌─────────────────────────┐  │
ESP32 Telemetry ──────>│   │  Embedded MQTT   │ ───> │ In-Memory Arrow Ring    │  │
 (Protobuf Wire)       │   │  Broker (Tokio)  │      │ Buffer Micro-Batches    │  │
                       │   └──────────────────┘      └────────────┬────────────┘  │
                       │                                          │               │
                       │   ┌──────────────────┐                   ▼               │
Browser Client <──────>│   │   Axum HTTP/SSE  │      ┌─────────────────────────┐  │
 (WASM Decoder)        │   │  Embedded Web UI │ <─── │ DuckDB Columnar Engine  │  │
                       │   └──────────────────┘      │  & Parquet Persistence  │  │
                       │                             └─────────────────────────┘  │
                       └──────────────────────────────────────────────────────────┘
```

---

## Core Pillars of the V2 Architecture

### 1. Unified Single-Binary Host Process
- **Technology:** Rust with Tokio async runtime.
- **Components:**
  - **Embedded MQTT Broker:** Integrated lightweight MQTT server running on the same event loop (e.g. `rumqttd` embedded library), eliminating the standalone Mosquitto process.
  - **Embedded Web Server:** High-performance `Axum` HTTP/SSE server serving compiled-in static assets (`rust-embed`) and SSE streams.
- **Benefit:** Zero inter-process IPC overhead, single systemd unit, trivial distribution as a standalone 15MB binary with zero external dependencies.

### 2. Embedded Columnar Storage via DuckDB & Direct Parquet
- **Technology:** `duckdb-rs` embedded engine with automated Parquet partition compaction.
- **Design:**
  - Ingested metrics are written directly to in-memory Arrow tables.
  - Periodic background micro-compaction flushes closed chunks into Snappy-compressed columnar Parquet files partitioned by date (`/data/airquality/year=2026/month=08/day=18.parquet`).
  - Historical analytical queries (aggregations, percentiles, downsampling) execute via DuckDB SQL directly over Parquet files with zero external database server overhead.
- **Benefit:** Eliminates InfluxDB daemon entirely. Storage engine consumes < 5MB RAM and provides ACID analytical capabilities with standard SQL.

### 3. Client-Side WebAssembly (WASM) Binary Canvas Decoder
- **Technology:** Rust compiled to `wasm32-unknown-unknown` running in the browser.
- **Design:**
  - SSE streams push raw, unparsed binary Protobuf / Arrow micro-buffers directly over the wire to the browser.
  - The WASM module decodes binary varints directly in linear browser memory and directly manipulates the Canvas pixel buffer via WebGL / 2D Canvas context.
  - Zero JSON serialization/deserialization on the wire or in JavaScript.
- **Benefit:** Reduces network payload by another 60%, eliminates browser garbage collection pauses, and delivers consistent 120 FPS rendering on mobile devices.

---

## Target V2 Resource Footprint Scorecard

| Component / Subsystem | V1 Architecture (Measured) | V2 Architecture (Target) |
| :--- | :--- | :--- |
| **MQTT Broker** | Mosquitto (~3.5 MB) | Embedded in Rust (< 1.0 MB) |
| **Ingestion Pipeline** | Rust Binary (~5.8 MB) | Embedded (< 2.0 MB) |
| **Time-Series Storage** | InfluxDB v2 (~22.0 MB) | DuckDB Embedded (< 4.5 MB) |
| **Web Server & UI** | FastHTML / Uvicorn (~14.5 MB) | Axum Embedded (< 2.0 MB) |
| **Reverse Proxy** | Caddy (~8.0 MB) | Direct TLS / Axum (< 0.5 MB) |
| **TOTAL IDLE RAM** | **~43.8 MB** | **< 9.5 MB** |

---

## Migration Path & Compatibility
The V1 Protobuf schema (`iot-airquality-contracts/proto/air_quality.proto`) remains 100% compatible with V2. Firmware deployed in V1 requires zero modifications to operate with the V2 single-binary backend engine.
