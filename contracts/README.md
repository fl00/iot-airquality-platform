# Contracts

[`proto/air_quality.proto`](proto/air_quality.proto) is the reference for the wire format between a sensor node and the ingestor. Field semantics are documented in the file itself.

- Transport: MQTT, topic `sensors/<device_id>/airquality`, one `AirQualityPacket` per message.
- One packet carries one complete measurement (`FullSample`), about 70 bytes.
- Delta-encoded batches are not part of V1; field 8 is reserved for them. See [ADR 0005](../docs/adr/0005-v1-scope.md).

## Bindings

| Language | File | How it is produced |
| :--- | :--- | :--- |
| Python (simulator, tests) | `firmware/tools/proto/air_quality_pb2.py` | `protoc` or `grpc_tools` |
| C (firmware, Nanopb) | `firmware/include/air_quality.pb.h`, `firmware/src/air_quality.pb.c` | `nanopb_generator.py` with [`proto/air_quality.options`](proto/air_quality.options) |
| Rust (ingestor, Prost) | `ingestor/src/proto/mod.rs` | Maintained by hand |

## Changing the contract

1. Edit `proto/air_quality.proto`. Never reuse or renumber a field; add new ones.
2. Run `make contracts`. A generator that is not installed is skipped and reported: update that binding by hand.
3. Update `ingestor/src/proto/mod.rs`.
4. Run `make test` with `NANOPB_DIR` set: the firmware host test encodes a packet in C and decodes it with the Python bindings, which catches a mismatch between the two.
