# Deployment

Single host, Debian or Ubuntu, on the same LAN as the sensor nodes. To use a Mac that keeps macOS, run the same deployment in an Ubuntu virtual machine: see [`MACOS_VM.md`](MACOS_VM.md).

```bash
make build                               # as a normal user: builds the ingestor
sudo ./deploy/deploy-baremetal.sh
```

The script can be re-run to update the application: existing secrets and the InfluxDB setup are kept.

## What it installs

| Service | Listens on | Configuration |
| :--- | :--- | :--- |
| Mosquitto | `0.0.0.0:1883` | [`mosquitto/mosquitto.conf`](mosquitto/mosquitto.conf): authentication required |
| InfluxDB 2 | `127.0.0.1:8086` | Package defaults, bound to localhost; bucket `airquality`, 90-day retention |
| Ingestor | `127.0.0.1:9100` | [`systemd/iot-ingestor.service`](systemd/iot-ingestor.service) |
| Dashboard | `127.0.0.1:8000` | [`systemd/iot-dashboard.service`](systemd/iot-dashboard.service) |
| Caddy | `0.0.0.0:80` | [`Caddyfile`](Caddyfile): reverse proxy to the dashboard |
| Health report | — | [`health-report.py`](health-report.py), run daily at 07:00 by `iot-health-report.timer` |

Application files go to `/opt/iot-airquality/`. Ingestor and dashboard run as the unprivileged user `iot-service` with systemd sandboxing and a memory limit (ingestor 40 MB, dashboard 128 MB).

## Secrets

Generated on the first run, never stored in the repository:

| File | Mode | Content |
| :--- | :--- | :--- |
| `/etc/iot-airquality/env` | `0640 root:iot-service` | `INFLUXDB_TOKEN`, MQTT account of the ingestor. Loaded by both systemd units |
| `/etc/iot-airquality/sensor-credentials` | `0600 root` | MQTT account for the sensor nodes (copy into `firmware/include/secrets.h`), InfluxDB admin password |
| `/etc/mosquitto/passwd` | `0600 mosquitto` | Hashed MQTT passwords |

To rotate everything, delete `/etc/iot-airquality/env` and re-run the script, then reflash the nodes with the new MQTT password.

## Alerts

The daily health report writes `/var/lib/iot-airquality/health.csv` and raises an alert when a node is silent for more than 10 minutes, completeness over 24 h is below 99 %, InfluxDB writes are failing, or the disk is more than 90 % full.

To receive alerts on a phone or by another channel, add one line to `/etc/iot-airquality/env`:

```
ALERT_URL=https://ntfy.sh/<a-topic-name-only-you-know>
```

Any URL accepting a plain-text POST works (ntfy, self-hosted or not, or a webhook). The alert text contains device ids and figures, nothing else. Without `ALERT_URL`, alerts are visible with `systemctl --failed` and `journalctl -u iot-health-report`.

## Operations

```bash
systemctl status iot-ingestor iot-dashboard
journalctl -u iot-ingestor -f
curl -s http://127.0.0.1:9100/metrics
sudo systemctl start iot-health-report && column -s, -t /var/lib/iot-airquality/health.csv | tail   # report now
```

Routine checks for the validation period are in [`docs/V1_VALIDATION.md`](../docs/V1_VALIDATION.md).

## Limits

- Plain HTTP and MQTT, no dashboard login: for a trusted LAN only.
- The script has been syntax-checked but not yet run on a clean host.
- No remote access and no cloud deployment in V1. Do not open ports 80 or 1883 to the internet; remote access through a private overlay network is planned for V2 ([ADR 0005](../docs/adr/0005-v1-scope.md)).
