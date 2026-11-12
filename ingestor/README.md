# Ingestor

Rust service, the only MQTT subscriber of the platform.

```
MQTT packet ─► decode + validate ─┬─► batch ─► InfluxDB (line protocol)
                                  └─► JSON event ─► /stream (dashboard)
```

| File | Role |
| :--- | :--- |
| `src/main.rs` | Packet validation, batch writer with retry, MQTT loop, shutdown |
| `src/sink.rs` | InfluxDB write and line-protocol formatting |
| `src/metrics.rs` | Counters, AQI level, HTTP endpoint |
| `src/config.rs` | Environment variables |
| `src/proto/mod.rs` | Prost bindings of the wire contract (maintained by hand) |

Interfaces (wire contract, InfluxDB schema, stream event) and failure behaviour are specified in the [root README](../README.md#data-contracts). Environment variables are listed [there](../README.md#configuration) as well.

## Validation rules

A packet is dropped, and counted, when any of these holds:

- it cannot be decoded, or carries no sample;
- `device_id` does not match `[A-Za-z0-9_-]{1,31}`;
- the id in the topic differs from the id in the payload;
- a float value is NaN or infinite.

## HTTP endpoint (`127.0.0.1:9100`)

| Path | Content |
| :--- | :--- |
| `/stream` | Server-Sent Events, one JSON object per accepted packet |
| `/metrics` | Prometheus text format |
| `/health` | Liveness |

| Metric | Type | Meaning |
| :--- | :--- | :--- |
| `iot_packets_received_total` | counter | MQTT messages received |
| `iot_packets_valid_total` | counter | Packets accepted |
| `iot_packets_dropped_total` | counter | Packets rejected, all causes |
| `iot_packets_spoofed_total` | counter | Subset of dropped: topic id differs from payload id |
| `iot_influx_batches_total` | counter | Write attempts |
| `iot_influx_points_written_total` | counter | Samples written |
| `iot_influx_errors_total` | counter | Failed write attempts |
| `iot_influx_last_batch_latency_ms` | gauge | Duration of the last write attempt |
| `iot_influx_buffered_samples` | gauge | Samples waiting in memory; above 0 for long means InfluxDB is failing |
| `iot_sensor_last_seen_seconds{device_id}` | gauge | Unix time of the last accepted packet per node |

## Build, run, test

```bash
cargo build --release
INFLUXDB_TOKEN=... ./target/release/iot-airquality-ingestor   # or `make run-ingestor` with a .env file
cargo test
```
