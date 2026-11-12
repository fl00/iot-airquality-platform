# Deployment

Single host, Debian or Ubuntu, on the same LAN as the sensor nodes.

```bash
make build                               # as a normal user: builds the ingestor
sudo ./deploy/deploy-baremetal.sh
```

The script can be re-run to update the application: existing secrets and the InfluxDB setup are kept.

## What it installs

| Service | Listens on | Configuration |
| :--- | :--- | :--- |
| Mosquitto | `0.0.0.0:1883` | [`mosquitto/mosquitto.conf`](mosquitto/mosquitto.conf): authentication required |
| InfluxDB 2 | `127.0.0.1:8086` | Package defaults; bucket `airquality`, 90-day retention |
| Ingestor | `127.0.0.1:9100` | [`systemd/iot-ingestor.service`](systemd/iot-ingestor.service) |
| Dashboard | `127.0.0.1:8000` | [`systemd/iot-dashboard.service`](systemd/iot-dashboard.service) |
| Caddy | `0.0.0.0:80` | [`Caddyfile`](Caddyfile): reverse proxy to the dashboard |

Application files go to `/opt/iot-airquality/`. Ingestor and dashboard run as the unprivileged user `iot-service` with systemd sandboxing and a 40 MB memory limit each.

## Secrets

Generated on the first run, never stored in the repository:

| File | Mode | Content |
| :--- | :--- | :--- |
| `/etc/iot-airquality/env` | `0640 root:iot-service` | `INFLUXDB_TOKEN`, MQTT account of the ingestor. Loaded by both systemd units |
| `/etc/iot-airquality/sensor-credentials` | `0600 root` | MQTT account for the sensor nodes (copy into `firmware/include/secrets.h`), InfluxDB admin password |
| `/etc/mosquitto/passwd` | `0600 mosquitto` | Hashed MQTT passwords |

To rotate everything, delete `/etc/iot-airquality/env` and re-run the script, then reflash the nodes with the new MQTT password.

## Operations

```bash
systemctl status iot-ingestor iot-dashboard
journalctl -u iot-ingestor -f
curl -s http://127.0.0.1:9100/metrics
```

Routine checks for the validation period are in [`docs/V1_VALIDATION.md`](../docs/V1_VALIDATION.md).

## Limits

- Plain HTTP and MQTT, no dashboard login: for a trusted LAN only.
- The script has been syntax-checked but not yet run on a clean host.
- [`iac/main.bicep`](iac/main.bicep) (Azure VM) predates V1 and opens ports 80 and 1883 to the internet. It is not part of V1; do not use it as is.
