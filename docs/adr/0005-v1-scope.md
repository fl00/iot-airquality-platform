# ADR 0005: V1 Scope — One Simple Path End to End

## Status

**Accepted.** Supersedes parts of ADR 0001, 0002 and 0003 (listed below).

## Context

V1 has one goal: a chain from sensor to chart that runs unattended for several weeks and whose data can be trusted, so that the next evolutions are chosen from field feedback rather than from assumptions.

Operating conditions of V1: 1 to 3 sensor nodes, mains powered, on Wi-Fi, one packet every 5 s each, one host on the same LAN, one or two browsers.

A review of the codebase against that goal found that several optimisations added complexity, and bugs, without a measurable benefit at this load. The figures quoted for them in earlier ADRs and READMEs (memory, latency, throughput) had not been measured.

## Decisions

### 1. Wire contract: one complete sample per packet; no delta batches

The original contract had two payloads: `FullSample` (a complete measurement) and `DeltaBatch` (up to 12 samples encoded as differences from the previous one). V1 keeps `FullSample` only. Field 8 is reserved in the schema so it can return later without breaking compatibility.

| | Complete sample (V1) | Delta batches |
| :--- | :--- | :--- |
| Payload | about 64 bytes per measurement | about 8 bytes per measurement, plus periodic full samples |
| Volume, one node at 5 s | about 1.1 MB per day | about 0.2 MB per day |
| Decoding | Stateless: every packet stands alone | Stateful: needs the last full sample of that node |
| Lost or reordered packet | That measurement only | Every following delta is wrong until the next full sample, silently |
| Ingestor restart | No effect | Deltas are undecodable until the next full sample |
| Offline replay from flash | Any order, idempotent | Order and completeness required |

Delta encoding saves bytes and radio time. Neither is a constraint for a mains-powered node on Wi-Fi: 1 MB per day is negligible, and the radio stays on for MQTT keep-alive regardless. The cost, on the other hand, is real: state on both sides, and a failure mode (drift after a lost packet) that corrupts data without any error. It was implemented in the ingestor but never emitted by the firmware or the simulator, and had no test.

**Decision:** not relevant for V1. Recorded as a V2 optimisation, with its trigger and prerequisites, in the table below.

### 2. Live stream: JSON events; no 16-byte binary frame

Supersedes ADR 0002 (binary SSE frame) and the frame description in ADR 0003.

The binary frame replaced the device id by a 4-bit index derived from the id suffix, through four hand-maintained lookup tables. Devices not named `sensor-esp32-NN` collided or were shown as another sensor. It also dropped the VOC value and the device status. At one event every 5 s per node, JSON parsing cost is not observable. The ingestor now emits one JSON object per sample, carrying the real id, every field and the AQI level.

### 3. InfluxDB failures: retry buffer; no circuit breaker

The ingestor keeps up to 5,000 samples in memory when a write fails and retries every flush interval. This replaces the circuit breaker, which protected a single local HTTP call that already has a 5 s timeout, and which discarded the batch.

### 4. AQI level computed once, by the ingestor

Thresholds existed in Rust, Python and JavaScript, and the Python copy ignored VOC. The ingestor is now the only place; the dashboard maps the level to a label.

### 5. Stock infrastructure configuration

InfluxDB 2 OSS and the kernel run with their defaults. The previous InfluxDB configuration file mixed InfluxDB 3 options with a v2 installation, and the kernel tuning targeted connection volumes V1 does not have. Supersedes the "InfluxDB v3" choice of ADR 0001: V1 uses InfluxDB 2 and InfluxQL.

### 6. Secrets outside the repository, authenticated broker

Tokens and passwords are generated at deployment. Services refuse to start without a token.

## Deferred to V2

To be decided from the V1 feedback log ([V1_VALIDATION.md](../V1_VALIDATION.md)). Each item states what would justify it.

| Optimisation | Revisit when | Prerequisites |
| :--- | :--- | :--- |
| Delta batches (decision 1) | A node runs on battery, or on a metered or low-rate link (LoRa, cellular) | Sequence-gap detection in the ingestor; forced full sample after reconnect and at a fixed period; tests for loss and reordering |
| Binary live stream (decision 2) | Event rate above roughly 100 per second, or a constrained browser link | A frame that carries the device id, or a negotiated id table |
| Embedded storage engine (ADR 0004) | InfluxDB memory or operations prove to be a burden during V1 | Measured resource usage from V1 |
| Buffer persisted on disk in the ingestor | InfluxDB outages longer than the memory buffer are observed | — |
| Persistent broker session, QoS 1 from the node | Packets are lost during ingestor restarts in practice | — |
| TLS for MQTT and HTTP, dashboard login | Any exposure beyond the LAN | Certificates on the node |
| Alerting (offline node, threshold exceeded) | Requested from V1 use | — |
| PMS7003 duty cycling | Sensor lifetime becomes a concern | Warm-up handling in the firmware |
| Generated Nanopb and Prost bindings in CI | The contract changes more than occasionally | Generators installed in CI |

## Consequences

- About 500 lines fewer in the ingestor and dashboard, unit tests included, and four dependencies removed (`async-trait`, `thiserror`, `prost-types`, `prost-build`).
- Every interface has one definition; see "Data Contracts" in the root README.
- Bandwidth and browser CPU are higher than the theoretical optimum, by amounts that do not matter at V1 load.
- Resource budgets quoted in earlier ADRs are targets, not measurements. Real figures are collected during the V1 validation.
