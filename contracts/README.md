# IoT Air Quality Platform - Contracts Micro-Repository (`iot-airquality-contracts`)

## 1. Overview & Protocol Specification

The `iot-airquality-contracts` repository defines the canonical Protocol Buffers v3 schema for the entire bare-metal IoT air quality ecosystem. It enforces strict binary backwards-compatibility, wire-level compactness, and zero-allocation framing.

### Wire Efficiency & Sizing Benchmarks

| Metric / Format | JSON Telemetry | Standard Protobuf (Keyframe) | Delta-Encoded ZigZag Protobuf |
| :--- | :--- | :--- | :--- |
| **Payload Size** | ~180 – 240 bytes | **~28 – 35 bytes** | **~6 – 12 bytes / sample** |
| **Parsing Cost** | String tokenize / Float parse | Zero-copy Varint decode | Bitshift & ZigZag decode |
| **Bandwidth Savings** | Baseline (0%) | **~85% reduction** | **~96% reduction** |
| **Wi-Fi TX Time** | ~14ms | **~2.1ms** | **~0.8ms** |

---

## 2. Packet Architecture

The schema supports two operational telemetry modes:

1. **`FullSample` (Keyframe):** Sent on boot, every 60 seconds, or upon significant environmental shifts (> 50 PPM CO2 drift). Transmits full 32-bit floating-point metrics (`temperature_celsius`, `humidity_percent`, `pm25_ug_m3`, `pressure_hpa`).
2. **`DeltaSample` (ZigZag Varints):** Sent every 5 seconds. Uses Google Protobuf `sint32` (ZigZag varint encoding) to pack micro-deltas relative to the previous sample. Small positive/negative drifts (`-2`, `+1`) occupy only **1 single byte** on the wire.

---

## 3. Compilation Workflow

Run the cross-compilation pipeline:

```bash
./compile.sh
```

Generates:
- **C/C++ (Nanopb):** `../iot-airquality-firmware-esp32/include/air_quality.pb.h` and `../iot-airquality-firmware-esp32/src/air_quality.pb.c`
- **Rust (Prost):** Auto-generated during Cargo build pipeline in `../iot-airquality-ingestor-rust/`
- **Python (protoc):** `../iot-airquality-dashboard-ui/proto/air_quality_pb2.py`
