# V1 Validation

How to commission the V1 prototype, what to check while it runs, and when to call it validated. The outcome is a feedback log that drives the choice of V2 evolutions ([ADR 0005](adr/0005-v1-scope.md)).

## 1. Scope

| In scope | Out of scope |
| :--- | :--- |
| 1 to 3 sensor nodes, mains powered, Wi-Fi | Battery operation, other radio links |
| One host on the same LAN | Internet exposure, TLS, user accounts |
| Live view and history up to 90 days | Alerting, exports |

## 2. Commissioning

Each step must pass before the next one starts.

| Step | Action | Pass criterion |
| :--- | :--- | :--- |
| 1. Software chain | `make test` | `ALL CHECKS PASSED` |
| 2. Local stack, simulated sensors | Mosquitto and InfluxDB running; `cp .env.example .env`, set the token, `make build && make start` | Three cards on `http://localhost:8000/`, values change every 5 s, history chart fills in |
| 3. Host deployment | `make build && sudo ./deploy/deploy-baremetal.sh` | The five services are `active`; dashboard reachable on `http://<host>/` |
| 4. Node without sensors | `pio run -e esp32dev-mock -t upload` with the host address and MQTT credentials in `secrets.h` | A card with the node's `DEVICE_ID` appears, status "En ligne" |
| 5. Wired prototype | Dupont wiring as in [`hardware/README.md`](../hardware/README.md), `pio run -e esp32dev -t upload` | Serial `[TX]` lines show plausible values for every sensor; status goes "Préchauffage" then "En ligne" |
| 6. Fault checks | See section 3 | Each behaves as described |

The carrier PCB is ordered, and the enclosure printed, only after the wired prototype has passed the validation period.

## 3. Fault Checks

Run once at commissioning. Expected behaviour is specified in "Behaviour on Failure" in the root README.

| Fault to provoke | Expected observation |
| :--- | :--- |
| Unplug one sensor from the node | Status "Capteur manquant" within 20 s; CO2 shown as "—" if the SCD41 is the one unplugged |
| Power off the node | Status "Hors ligne" after 30 s |
| `sudo systemctl stop influxdb` for 5 minutes, then start | `iot_influx_buffered_samples` rises then returns to 0; no gap in the chart |
| Switch off the Wi-Fi access point for 5 minutes | Serial log shows `flash` then a replay; no gap in the chart afterwards |
| `sudo systemctl restart iot-ingestor` | At most a few seconds missing in the chart |
| Keep the node powered off for 15 minutes, then `sudo systemctl start iot-health-report` | An alert is raised and reaches `ALERT_URL` |

## 4. Monitoring During the Period

**Daily, automatic.** The health report runs at 07:00 ([`deploy/README.md`](../deploy/README.md#alerts)). Set `ALERT_URL` before the period starts, and provoke one alert (power off a node for 15 minutes, then run `sudo systemctl start iot-health-report`) to check that it reaches you.

**Weekly, about ten minutes.** Read the last seven days of the report and copy anything notable into the feedback log (section 6):

```bash
column -s, -t /var/lib/iot-airquality/health.csv | tail -n 25
systemctl show iot-ingestor iot-dashboard -p MemoryCurrent
```

| Indicator | Column or source | Expected |
| :--- | :--- | :--- |
| Completeness | `completeness_pct` | At least 99 % (17,280 points per node and per day) |
| Packets lost between node and storage | `missing_by_seq` | A few per day at most; a pattern is a finding |
| Node reboots | `node_reboots` | 0, or explained (power cut, Wi-Fi recovery) |
| Dropped packets | `packets_dropped_total` | 0, or explained |
| InfluxDB errors | `influx_errors_total` | Stable outside known outages |
| Buffered samples | `influx_buffered_samples` | 0 |
| Service restarts | `ingestor_restarts`, `dashboard_restarts` | 0, or explained |
| Memory | `MemoryCurrent` | Stable week over week, well below the unit limit (ingestor 40 MB, dashboard 128 MB) |
| Sensor plausibility | Dashboard, 7-day view | Outdoor-air CO2 near 420 ppm after airing; temperature within 1 °C of a reference thermometer; no flat lines |

On the node, also note from the serial log if accessible: `sIAQ` against `bVOC` (to confirm the VOC thresholds).

## 5. Exit Criteria

V1 is validated when all of the following hold over **21 consecutive days**:

1. Completeness of at least 99 % for every node.
2. No unexplained service restart, and no manual intervention needed to keep data flowing.
3. Every fault check of section 3 behaved as specified.
4. Readings judged plausible against at least one reference (thermometer, outdoor CO2 baseline).
5. The feedback log has an entry for every week, and `health.csv` a line for every day.

These thresholds are a starting proposal; adjust them before the period starts, not during it.

## 6. Feedback Log

One line per observation, as it happens. Keep it factual: what was seen, not the fix.

| Date | Area (node / ingestor / storage / dashboard / hardware) | Observation | Impact | Idea for V2 |
| :--- | :--- | :--- | :--- | :--- |
| | | | | |

At the end of the period, group the entries by area and compare them with the "Deferred to V2" table of ADR 0005: an optimisation enters V2 only if an entry justifies it.
