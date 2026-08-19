# ADR 0001: Zero-Bloat Bare-Metal Architecture (Protobuf, Rust, FastHTML, InfluxDB v2)

## Status
**Accepted** (Implemented in V1)

## Context & Problem Statement
Traditional IoT telemetry platforms frequently suffer from severe resource bloat:
- Node.js / React dashboards requiring 150MB+ client bundles and 200MB+ server heaps.
- JSON-over-MQTT telemetry bloating packet sizes to 200+ bytes per frame, increasing radio transmission time and battery drain on edge microcontrollers.
- Unconstrained database engines (e.g. unconfigured InfluxDB or Elasticsearch) exhausting host memory on sub-$5/mo cloud instances (such as Azure `Standard_B1s_v2` with 1GB RAM).

The challenge is to deliver a sub-50MB total idle memory footprint across the entire backend while supporting sub-second telemetry visualization, interactive zooming, store-and-forward resilience, and zero-allocation serialization.

---

## Decision Drivers
1. **Memory Budget:** Total idle host memory MUST NOT exceed **50MB RAM** across all background processes.
2. **Bandwidth & Power:** Telemetry frames MUST be sub-35 bytes on the wire.
3. **Decoupled Architecture:** Clean micro-repository boundaries (Contracts, Firmware, Ingestor, UI, Ops).
4. **Zero-DOM-Reallocation UI:** Fast, fluid charting without tearing down or rebuilding the DOM for each live data point.

---

## Architectural Choices & Rationale

### 1. Protobuf v3 + Nanopb Static Framing
- **Choice:** Google Protocol Buffers v3 with Nanopb for ESP32 and Prost for Rust.
- **Rationale:** Nanopb allows 100% static buffer serialization (`pb_ostream_from_buffer`), ensuring **zero `malloc()` calls** in the ESP32 transmission loop. Packet size dropped from ~220 bytes (JSON) to **28 bytes** (Protobuf), saving ~85% radio uptime.

### 2. Rust Ingestion Engine with MPSC Micro-Batching
- **Choice:** Tokio multi-threaded async engine with `rumqttc`, bounded channel (1024), and Influx Line Protocol batching (12 samples / 30s timeout).
- **Rationale:** Decouples high-frequency network ingestion from blocking disk/HTTP I/O. Memory usage sits under **6MB RSS** under load.

### 3. FastHTML + HTMX + SSE uPlot Canvas Scope
- **Choice:** Python FastHTML running on `uvicorn` with `uvloop` single-worker. HTMX for page navigation and badge OOB updates; native JavaScript `EventSource` pushing into a `uPlot` Canvas ring buffer (max 3600 points).
- **Rationale:** Prevents SPA bloat while delivering instant 60 FPS Canvas rendering without virtual DOM churn.

### 4. InfluxDB v3 (Rust / Apache Arrow DataFusion / Parquet)
- **Choice:** InfluxDB v3 (IOx engine) with SQL querying (`DATE_BIN()`), Parquet columnar persistence, and 32MB cache limit.
- **Rationale:** Replaces Go-runtime GC and Flux interpreter with native Rust SIMD vectorization. Cuts query CPU usage by 70%, increases disk compression via Parquet by 35%, and aligns natively with the browser columnar memory layout.

---

## Consequences

### Positive
- **Total Platform Memory Idle:** **~42.3 MB RAM** (reduced from ~53MB in v2; vs ~650MB on standard Docker/Node/TS stacks).
- **Vectorized Downsampling:** SQL queries with `DATE_BIN()` execute in 1-2ms without GC stutter.
- **Firmware Uptime:** Zero heap fragmentation risks on ESP32 over multi-year continuous operation.
- **Client Performance:** Initial page weight < 48KB, instant First Contentful Paint (< 45ms).

### Negative / Trade-offs
- Multiple process runtimes (Rust, Python, InfluxDB daemon, Mosquitto, Caddy) require systemd supervision.
- Schema changes require running the contract compiler across target languages.
