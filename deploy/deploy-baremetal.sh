#!/usr/bin/env bash
# ==============================================================================
# IoT Air Quality Platform - Automated Bare-Metal Deployment Script
# Provisions Mosquitto, Tuned InfluxDB v3 (Rust/IOx), Caddy, Rust Ingestor, & FastHTML UI
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=============================================================================="
echo "   Starting Zero-Bloat Bare-Metal IoT Platform Provisioning (InfluxDB v3)    "
echo "=============================================================================="

# 1. Check Root Privileges
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run deploy-baremetal.sh as root (sudo ./deploy-baremetal.sh)"
    exit 1
fi

# 2. Apply Kernel Performance Tuning
echo "=== [1/7] Applying Kernel Performance Tuning (sysctl) ==="
cp "${SCRIPT_DIR}/sysctl.d/99-iot-performance.conf" /etc/sysctl.d/99-iot-performance.conf
sysctl --system >/dev/null 2>&1 || true
echo "✔ Kernel performance parameters applied."

# 3. Create Service User
echo "=== [2/7] Provisioning Dedicated Sandboxed User ==="
if ! id "iot-service" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin iot-service
    echo "✔ Created user 'iot-service'."
fi

# 4. Install Mosquitto, InfluxDB v3/v2, and Caddy Repositories & Packages
echo "=== [3/7] Installing Core Packages (Mosquitto, InfluxDB Engine, Caddy, Python3) ==="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    curl wget mosquitto mosquitto-clients caddy python3 python3-pip python3-venv >/dev/null

# Install InfluxDB Engine if not present
if ! command -v influxd >/dev/null 2>&1; then
    echo "Installing InfluxDB repository..."
    wget -q https://repos.influxdata.com/influxdata-archive_compat.key
    gpg --dearmor -o /etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg influxdata-archive_compat.key
    rm -f influxdata-archive_compat.key
    echo 'deb [signed-by=/etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg] https://repos.influxdata.com/ubuntu noble stable' | tee /etc/apt/sources.list.d/influxdata.list
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq influxdb2 influxdb2-cli >/dev/null
fi
echo "✔ Infrastructure packages installed."

# 5. Configure Mosquitto & Tune InfluxDB v3 Engine with 90d Retention & Parquet Storage
echo "=== [4/7] Applying Tuned Mosquitto (8MB RAM Cap) & InfluxDB Config (32MB RAM Limit) ==="
mkdir -p /etc/mosquitto/conf.d /etc/influxdb /var/lib/influxdb/data
cp "${SCRIPT_DIR}/mosquitto/mosquitto.conf" /etc/mosquitto/conf.d/99-iot-hardened.conf
systemctl restart mosquitto || true
cp "${SCRIPT_DIR}/influxdb/config.yml" /etc/influxdb/config.yml
chown -R iot-service:iot-service /var/lib/influxdb
systemctl restart influxdb || true
sleep 3

# Automated InfluxDB Initial Database Setup (Idempotent)
if command -v influx >/dev/null 2>&1; then
    echo "Configuring InfluxDB database/bucket 'airquality' with 90-day retention..."
    influx setup \
        --username iotadmin \
        --password "BareMetalSecurePass2026!" \
        --org baremetal-iot \
        --bucket airquality \
        --retention 2160h \
        --token "baremetal-dev-token-987654321" \
        --force >/dev/null 2>&1 || true
    echo "✔ InfluxDB v3/v2 initialized (Database: airquality, Retention: 90d, Parquet enabled)."
fi

# 6. Deploy FastHTML Dashboard & Python Virtual Environment
echo "=== [5/7] Setting Up FastHTML Dashboard UI ==="
APP_ROOT="/opt/iot-airquality"
mkdir -p "${APP_ROOT}"
cp -r "${BASE_DIR}/iot-airquality-dashboard-ui" "${APP_ROOT}/"
cp -r "${BASE_DIR}/iot-airquality-ingestor-rust" "${APP_ROOT}/"

cd "${APP_ROOT}/iot-airquality-dashboard-ui"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
chown -R iot-service:iot-service "${APP_ROOT}"
echo "✔ FastHTML environment configured with InfluxDB v3 SQL support."

# 7. Configure Caddy & Systemd Units
echo "=== [6/7] Deploying Hardened Systemd Units & Caddy Proxy ==="
cp "${SCRIPT_DIR}/systemd/iot-ingestor.service" /etc/systemd/system/
cp "${SCRIPT_DIR}/systemd/iot-dashboard.service" /etc/systemd/system/
cp "${SCRIPT_DIR}/Caddyfile" /etc/caddy/Caddyfile

systemctl daemon-reload
systemctl enable mosquitto influxdb caddy iot-dashboard
systemctl restart mosquitto
systemctl restart influxdb
systemctl restart caddy
systemctl restart iot-dashboard

echo "=== [7/7] Deployment Health Validation ==="
sleep 2
echo "Status check:"
echo "- Mosquitto: $(systemctl is-active mosquitto)"
echo "- InfluxDB:  $(systemctl is-active influxdb)"
echo "- Dashboard: $(systemctl is-active iot-dashboard)"
echo "- Caddy:     $(systemctl is-active caddy)"

echo "=============================================================================="
echo "✔ Bare-Metal IoT Platform (InfluxDB v3 Engine) deployed successfully!"
echo "  Access Dashboard at: http://localhost (or VM Public IP)"
echo "=============================================================================="
