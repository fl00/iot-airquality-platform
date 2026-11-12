#!/usr/bin/env bash
# ==============================================================================
# IoT Air Quality Platform - single-host deployment (Debian / Ubuntu)
# Installs Mosquitto, InfluxDB 2, Caddy, the Rust ingestor and the dashboard.
# Safe to re-run: existing secrets and the InfluxDB setup are kept.
#
# Usage:  make build && sudo ./deploy/deploy-baremetal.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="/opt/iot-airquality"
ENV_DIR="/etc/iot-airquality"
ENV_FILE="${ENV_DIR}/env"
SENSOR_CREDENTIALS="${ENV_DIR}/sensor-credentials"
INGESTOR_BIN="${BASE_DIR}/ingestor/target/release/iot-airquality-ingestor"

if [ "$EUID" -ne 0 ]; then
    echo "ERROR: run as root (sudo ./deploy/deploy-baremetal.sh)" >&2
    exit 1
fi
if [ ! -x "${INGESTOR_BIN}" ]; then
    echo "ERROR: ingestor binary not found. Run 'make build' first (as a normal user)." >&2
    exit 1
fi

echo "=== [1/6] Packages ==="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl wget gpg openssl mosquitto mosquitto-clients caddy python3 python3-venv >/dev/null
if ! command -v influxd >/dev/null 2>&1; then
    wget -q https://repos.influxdata.com/influxdata-archive_compat.key
    gpg --dearmor -o /etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg influxdata-archive_compat.key
    rm -f influxdata-archive_compat.key
    echo 'deb [signed-by=/etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg] https://repos.influxdata.com/debian stable main' \
        > /etc/apt/sources.list.d/influxdata.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq influxdb2 influxdb2-cli >/dev/null
fi

# InfluxDB is only used by local services: do not listen on the network.
mkdir -p /etc/influxdb
grep -qs '^http-bind-address' /etc/influxdb/config.toml \
    || echo 'http-bind-address = "127.0.0.1:8086"' >> /etc/influxdb/config.toml

echo "=== [2/6] Service user and secrets ==="
id iot-service >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin iot-service
mkdir -p "${ENV_DIR}"
if [ ! -f "${ENV_FILE}" ]; then
    # Generated once, never stored in the repository.
    INFLUXDB_TOKEN="$(openssl rand -hex 32)"
    INFLUX_ADMIN_PASSWORD="$(openssl rand -hex 16)"
    MQTT_INGESTOR_PASSWORD="$(openssl rand -hex 16)"
    MQTT_SENSOR_PASSWORD="$(openssl rand -hex 16)"

    cat > "${ENV_FILE}" <<ENV
INFLUXDB_TOKEN=${INFLUXDB_TOKEN}
MQTT_USER=ingestor
MQTT_PASSWORD=${MQTT_INGESTOR_PASSWORD}
# Optional: where the daily health report sends its alerts (ntfy topic URL or webhook)
# ALERT_URL=
ENV
    cat > "${SENSOR_CREDENTIALS}" <<CRED
# Copy these two values into firmware/include/secrets.h
MQTT_USER=sensor
MQTT_PASSWORD=${MQTT_SENSOR_PASSWORD}
# InfluxDB web UI (http://127.0.0.1:8086)
INFLUX_ADMIN_USER=iotadmin
INFLUX_ADMIN_PASSWORD=${INFLUX_ADMIN_PASSWORD}
CRED
    chown root:iot-service "${ENV_FILE}"
    chmod 0640 "${ENV_FILE}"
    chmod 0600 "${SENSOR_CREDENTIALS}"

    echo "=== [3/6] Mosquitto accounts and InfluxDB setup ==="
    : > /etc/mosquitto/passwd
    mosquitto_passwd -b /etc/mosquitto/passwd ingestor "${MQTT_INGESTOR_PASSWORD}"
    mosquitto_passwd -b /etc/mosquitto/passwd sensor "${MQTT_SENSOR_PASSWORD}"
    chown mosquitto:mosquitto /etc/mosquitto/passwd
    chmod 0600 /etc/mosquitto/passwd

    systemctl enable influxdb
    systemctl restart influxdb
    sleep 3
    influx setup --force \
        --username iotadmin --password "${INFLUX_ADMIN_PASSWORD}" \
        --org baremetal-iot --bucket airquality --retention 2160h \
        --token "${INFLUXDB_TOKEN}" >/dev/null
else
    echo "=== [3/6] Secrets already present in ${ENV_FILE}: accounts left untouched ==="
fi

echo "=== [4/6] Mosquitto configuration ==="
cp "${SCRIPT_DIR}/mosquitto/mosquitto.conf" /etc/mosquitto/conf.d/99-iot.conf

echo "=== [5/6] Application files ==="
install -D -m 0755 "${INGESTOR_BIN}" "${APP_ROOT}/ingestor/target/release/iot-airquality-ingestor"
mkdir -p "${APP_ROOT}/dashboard"
cp -r "${BASE_DIR}/dashboard/." "${APP_ROOT}/dashboard/"
[ -d "${APP_ROOT}/dashboard/.venv" ] || python3 -m venv "${APP_ROOT}/dashboard/.venv"
"${APP_ROOT}/dashboard/.venv/bin/pip" install -q --upgrade pip
"${APP_ROOT}/dashboard/.venv/bin/pip" install -q -r "${APP_ROOT}/dashboard/requirements.txt"
install -m 0755 "${SCRIPT_DIR}/health-report.py" "${APP_ROOT}/health-report.py"
chown -R iot-service:iot-service "${APP_ROOT}"

echo "=== [6/6] Services ==="
cp "${SCRIPT_DIR}"/systemd/iot-*.service "${SCRIPT_DIR}"/systemd/iot-*.timer /etc/systemd/system/
cp "${SCRIPT_DIR}/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable mosquitto influxdb caddy iot-ingestor iot-dashboard >/dev/null
systemctl restart mosquitto influxdb caddy iot-ingestor iot-dashboard
systemctl enable --now iot-health-report.timer >/dev/null

sleep 2
for svc in mosquitto influxdb iot-ingestor iot-dashboard caddy; do
    printf '  %-14s %s\n' "${svc}" "$(systemctl is-active "${svc}")"
done
echo "Dashboard: http://<host>/     Sensor MQTT credentials: ${SENSOR_CREDENTIALS}"
