#!/usr/bin/env bash
# ==============================================================================
# Bare-Metal IoT Platform - Stack Lifecycle & Process Controller
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${BASE_DIR}/logs"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

mkdir -p "${LOG_DIR}"

cmd_stop() {
    echo -e "${YELLOW}==> Stopping IoT Platform Stack...${NC}"
    
    # 1. Kill Mock Publisher
    if [ -f "${LOG_DIR}/mock.pid" ]; then
        PID=$(cat "${LOG_DIR}/mock.pid" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
        fi
        rm -f "${LOG_DIR}/mock.pid"
    fi
    pkill -f "mock_publisher.py" 2>/dev/null || true

    # 2. Kill Dashboard UI (Port 8000)
    if [ -f "${LOG_DIR}/dashboard.pid" ]; then
        PID=$(cat "${LOG_DIR}/dashboard.pid" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
        fi
        rm -f "${LOG_DIR}/dashboard.pid"
    fi
    fuser -k 8000/tcp 2>/dev/null || true

    # 3. Kill Rust Ingestor (Port 9100)
    if [ -f "${LOG_DIR}/ingestor.pid" ]; then
        PID=$(cat "${LOG_DIR}/ingestor.pid" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
        fi
        rm -f "${LOG_DIR}/ingestor.pid"
    fi
    fuser -k 9100/tcp 2>/dev/null || true
    pkill -f "iot-airquality-ingestor" 2>/dev/null || true

    sleep 0.5
    echo -e "${GREEN}✔ All IoT platform processes stopped and ports released.${NC}"
}

cmd_status() {
    echo -e "\n${CYAN}==============================================================================${NC}"
    echo -e "${CYAN}   IoT Air Quality Platform - Runtime Health & Service Status                 ${NC}"
    echo -e "${CYAN}==============================================================================${NC}\n"
    printf "  %-24s %-12s %-10s %s\n" "SERVICE" "STATUS" "PORT/PID" "DETAILS"
    echo "  ----------------------------------------------------------------------------"

    # Mosquitto
    if nc -z 127.0.0.1 1883 2>/dev/null; then
        printf "  ${GREEN}%-24s${NC} ${GREEN}%-12s${NC} %-10s %s\n" "Mosquitto MQTT Broker" "RUNNING" ":1883" "MQTT Wire Broker"
    else
        printf "  ${RED}%-24s${NC} ${RED}%-12s${NC} %-10s %s\n" "Mosquitto MQTT Broker" "STOPPED" ":1883" "TCP port unreachable"
    fi

    # InfluxDB
    if nc -z 127.0.0.1 8086 2>/dev/null; then
        printf "  ${GREEN}%-24s${NC} ${GREEN}%-12s${NC} %-10s %s\n" "InfluxDB Time-Series" "RUNNING" ":8086" "Time-Series Engine"
    else
        printf "  ${RED}%-24s${NC} ${RED}%-12s${NC} %-10s %s\n" "InfluxDB Time-Series" "STOPPED" ":8086" "HTTP port unreachable"
    fi

    # Rust Ingestor
    if nc -z 127.0.0.1 9100 2>/dev/null; then
        PID=$(fuser 9100/tcp 2>/dev/null | tr -d ' ' || echo "OK")
        printf "  ${GREEN}%-24s${NC} ${GREEN}%-12s${NC} %-10s %s\n" "Rust Telemetry Ingestor" "RUNNING" ":9100" "PID: ${PID} (Tokio SIMD)"
    else
        printf "  ${RED}%-24s${NC} ${RED}%-12s${NC} %-10s %s\n" "Rust Telemetry Ingestor" "STOPPED" ":9100" "Inactive"
    fi

    # FastHTML Dashboard UI
    if nc -z 127.0.0.1 8000 2>/dev/null; then
        PID=$(fuser 8000/tcp 2>/dev/null | tr -d ' ' || echo "OK")
        printf "  ${GREEN}%-24s${NC} ${GREEN}%-12s${NC} %-10s %s\n" "FastHTML Dashboard UI" "RUNNING" ":8000" "PID: ${PID} (Uvicorn)"
    else
        printf "  ${RED}%-24s${NC} ${RED}%-12s${NC} %-10s %s\n" "FastHTML Dashboard UI" "STOPPED" ":8000" "Inactive"
    fi

    # Mock Publisher
    if [ -f "${LOG_DIR}/mock.pid" ] && kill -0 "$(cat "${LOG_DIR}/mock.pid" 2>/dev/null)" 2>/dev/null; then
        PID=$(cat "${LOG_DIR}/mock.pid")
        printf "  ${GREEN}%-24s${NC} ${GREEN}%-12s${NC} %-10s %s\n" "Mock Telemetry Publisher" "RUNNING" "PID: ${PID}" "Emitting 5s Protobuf"
    else
        printf "  ${YELLOW}%-24s${NC} ${YELLOW}%-12s${NC} %-10s %s\n" "Mock Telemetry Publisher" "IDLE" "-" "No active simulation"
    fi
    echo ""
}

cmd_start() {
    mkdir -p "${LOG_DIR}"
    echo -e "${CYAN}==> Checking Core Infrastructure (Mosquitto & InfluxDB)...${NC}"
    if ! nc -z 127.0.0.1 1883 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Warning: Mosquitto MQTT is not responding on 127.0.0.1:1883${NC}"
        echo -e "    Start it with: sudo systemctl start mosquitto (or mosquitto -d)"
    fi
    if ! nc -z 127.0.0.1 8086 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Warning: InfluxDB is not responding on 127.0.0.1:8086${NC}"
        echo -e "    Start it with: sudo systemctl start influxdb (or influxd &)"
    fi

    # Build ingestor binary if not already built
    if [ ! -f "${BASE_DIR}/ingestor/target/release/iot-airquality-ingestor" ]; then
        echo -e "${CYAN}==> Building release binary for Rust Ingestor...${NC}"
        (cd "${BASE_DIR}/ingestor" && cargo build --release)
    fi

    # Stop any running instances first
    fuser -k 9100/tcp 2>/dev/null || true
    fuser -k 8000/tcp 2>/dev/null || true
    pkill -f "mock_publisher.py" 2>/dev/null || true

    # 1. Start Rust Ingestor
    echo -e "${CYAN}==> Starting Rust Ingestion Hub (:9100)...${NC}"
    MQTT_BROKER_HOST="127.0.0.1" \
    MQTT_BROKER_PORT=1883 \
    MQTT_TOPIC="sensors/+/airquality" \
    INFLUXDB_URL="http://127.0.0.1:8086" \
    INFLUXDB_BUCKET="airquality" \
    INFLUXDB_ORG="baremetal-iot" \
    INFLUXDB_TOKEN="kH9pBO5KNEvbEh620uQz_T21DvVnVxb9OsvXAC-YZJN5esopKVVKOCugDTq7aBlcWMjQl5562RyHopbSE2vlow==" \
    METRICS_PORT=9100 \
    METRICS_ENABLED=true \
    RUST_LOG="info,iot_airquality_ingestor=debug" \
    setsid "${BASE_DIR}/ingestor/target/release/iot-airquality-ingestor" </dev/null > "${LOG_DIR}/ingestor.log" 2>&1 &
    PID_INGEST=$!
    echo $PID_INGEST > "${LOG_DIR}/ingestor.pid"

    # 2. Start FastHTML Dashboard UI
    echo -e "${CYAN}==> Starting FastHTML Dashboard UI (:8000)...${NC}"
    PYTHONPATH="${BASE_DIR}/dashboard" \
    setsid python3 -m uvicorn --app-dir "${BASE_DIR}/dashboard" main:app --host 127.0.0.1 --port 8000 --workers 1 --loop uvloop </dev/null > "${LOG_DIR}/dashboard.log" 2>&1 &
    PID_DASH=$!
    echo $PID_DASH > "${LOG_DIR}/dashboard.pid"

    # 3. Start Mock Telemetry Publisher
    echo -e "${CYAN}==> Starting Hardware Mock Publisher...${NC}"
    setsid python3 "${BASE_DIR}/firmware/tools/mock_publisher.py" </dev/null > "${LOG_DIR}/mock.log" 2>&1 &
    PID_MOCK=$!
    echo $PID_MOCK > "${LOG_DIR}/mock.pid"

    # Wait for ready state
    sleep 1.2

    echo -e "\n${GREEN}==============================================================================${NC}"
    echo -e "${GREEN}✔ Platform stack successfully started in background!                        ${NC}"
    echo -e "${GREEN}==============================================================================${NC}"
    echo -e "  🌐 Dashboard UI:       ${CYAN}http://localhost:8000/${NC}"
    echo -e "  📊 Prometheus Metrics: ${CYAN}http://localhost:9100/metrics${NC}"
    echo -e "  ⚡ Live SSE Stream:    ${CYAN}http://localhost:9100/stream${NC}"
    echo -e "  📜 Logs Streaming:     Run ${YELLOW}make logs${NC} or tail logs/*.log"
    echo -e "  🛑 Stop Stack:         Run ${YELLOW}make stop${NC}\n"
}

case "${1:-status}" in
    start|up)
        cmd_start
        ;;
    stop|down)
        cmd_stop
        ;;
    restart)
        cmd_stop
        cmd_start
        ;;
    status)
        cmd_status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
