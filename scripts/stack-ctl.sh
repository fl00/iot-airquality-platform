#!/usr/bin/env bash
# ==============================================================================
# IoT Air Quality Platform - local stack controller (used by the Makefile)
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

# Local settings and secrets (INFLUXDB_TOKEN, ...): see .env.example
if [ -f "${BASE_DIR}/.env" ]; then
    set -a; . "${BASE_DIR}/.env"; set +a
fi

# ------------------------------------------------------------------------------
# 📦 INFRASTRUCTURE SERVICES (Mosquitto & InfluxDB)
# ------------------------------------------------------------------------------

cmd_start_mosquitto() {
    echo -e "${CYAN}==> Starting Mosquitto MQTT Broker (:1883)...${NC}"
    if nc -z 127.0.0.1 1883 2>/dev/null; then
        echo -e "${GREEN}✔ Mosquitto is already running on port 1883.${NC}"
        return 0
    fi
    if command -v systemctl >/dev/null 2>&1; then
        sudo systemctl start mosquitto 2>/dev/null || mosquitto -d 2>/dev/null || true
    else
        mosquitto -d 2>/dev/null || true
    fi
    sleep 0.5
    if nc -z 127.0.0.1 1883 2>/dev/null; then
        echo -e "${GREEN}✔ Mosquitto MQTT broker started successfully.${NC}"
    else
        echo -e "${RED}✖ Failed to start Mosquitto. Please verify service status or run: sudo systemctl start mosquitto${NC}"
    fi
}

cmd_stop_mosquitto() {
    echo -e "${YELLOW}==> Stopping Mosquitto MQTT Broker...${NC}"
    if command -v systemctl >/dev/null 2>&1; then
        sudo systemctl stop mosquitto 2>/dev/null || sudo pkill -f mosquitto 2>/dev/null || true
    else
        sudo pkill -f mosquitto 2>/dev/null || true
    fi
    sleep 0.5
    if ! nc -z 127.0.0.1 1883 2>/dev/null; then
        echo -e "${GREEN}✔ Mosquitto stopped.${NC}"
    else
        echo -e "${YELLOW}⚠ Mosquitto still responding on port 1883 (may require sudo privileges).${NC}"
    fi
}

cmd_start_influxdb() {
    echo -e "${CYAN}==> Starting InfluxDB Time-Series Engine (:8086)...${NC}"
    if nc -z 127.0.0.1 8086 2>/dev/null; then
        echo -e "${GREEN}✔ InfluxDB is already running on port 8086.${NC}"
        return 0
    fi
    if command -v systemctl >/dev/null 2>&1; then
        sudo systemctl start influxdb 2>/dev/null || sudo systemctl start influxd 2>/dev/null || influxd &
    else
        influxd &
    fi
    sleep 1.0
    if nc -z 127.0.0.1 8086 2>/dev/null; then
        echo -e "${GREEN}✔ InfluxDB engine started successfully.${NC}"
    else
        echo -e "${RED}✖ Failed to start InfluxDB. Please verify service status or run: sudo systemctl start influxdb${NC}"
    fi
}

cmd_stop_influxdb() {
    echo -e "${YELLOW}==> Stopping InfluxDB Time-Series Engine...${NC}"
    if command -v systemctl >/dev/null 2>&1; then
        sudo systemctl stop influxdb 2>/dev/null || sudo systemctl stop influxd 2>/dev/null || sudo pkill -f influxd 2>/dev/null || true
    else
        sudo pkill -f influxd 2>/dev/null || true
    fi
    sleep 0.5
    if ! nc -z 127.0.0.1 8086 2>/dev/null; then
        echo -e "${GREEN}✔ InfluxDB stopped.${NC}"
    else
        echo -e "${YELLOW}⚠ InfluxDB still responding on port 8086 (may require sudo privileges).${NC}"
    fi
}

cmd_start_infra() {
    cmd_start_mosquitto
    cmd_start_influxdb
}

cmd_stop_infra() {
    cmd_stop_influxdb
    cmd_stop_mosquitto
}

# ------------------------------------------------------------------------------
# 🚀 APPLICATION STACK (Rust Ingestor, FastHTML UI, Mock Publisher)
# ------------------------------------------------------------------------------

cmd_stop() {
    echo -e "${YELLOW}==> Stopping IoT Platform Application Stack...${NC}"
    
    # 1. Kill Mock Publisher
    if [ -f "${LOG_DIR}/mock.pid" ]; then
        PID=$(cat "${LOG_DIR}/mock.pid" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
        rm -f "${LOG_DIR}/mock.pid"
    fi
    pkill -9 -f "mock_publisher.py" 2>/dev/null || true

    # 2. Kill Dashboard UI (Port 8000)
    if [ -f "${LOG_DIR}/dashboard.pid" ]; then
        PID=$(cat "${LOG_DIR}/dashboard.pid" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
        rm -f "${LOG_DIR}/dashboard.pid"
    fi
    fuser -k 8000/tcp 2>/dev/null || true

    # 3. Kill Rust Ingestor (Port 9100)
    if [ -f "${LOG_DIR}/ingestor.pid" ]; then
        PID=$(cat "${LOG_DIR}/ingestor.pid" 2>/dev/null || true)
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null || true
        fi
        rm -f "${LOG_DIR}/ingestor.pid"
    fi
    fuser -k 9100/tcp 2>/dev/null || true
    pkill -9 -f "iot-airquality-ingestor" 2>/dev/null || true

    sleep 0.5
    echo -e "${GREEN}✔ All application processes stopped and ports released.${NC}"
}

cmd_start() {
    mkdir -p "${LOG_DIR}"
    if [ -z "${INFLUXDB_TOKEN:-}" ]; then
        echo -e "${RED}✖ INFLUXDB_TOKEN is not set. Copy .env.example to .env and fill it in.${NC}"
        exit 1
    fi
    echo -e "${CYAN}==> Checking Core Infrastructure (Mosquitto & InfluxDB)...${NC}"
    if ! nc -z 127.0.0.1 1883 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Notice: Mosquitto is stopped. Starting it now...${NC}"
        cmd_start_mosquitto
    fi
    if ! nc -z 127.0.0.1 8086 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Notice: InfluxDB is stopped. Starting it now...${NC}"
        cmd_start_influxdb
    fi

    # Build ingestor binary if not already built
    if [ ! -f "${BASE_DIR}/ingestor/target/release/iot-airquality-ingestor" ]; then
        echo -e "${CYAN}==> Building release binary for Rust Ingestor...${NC}"
        (cd "${BASE_DIR}/ingestor" && cargo build --release)
    fi

    # Clean previous instances
    fuser -k 9100/tcp 2>/dev/null || true
    fuser -k 8000/tcp 2>/dev/null || true
    pkill -9 -f "mock_publisher.py" 2>/dev/null || true

    # 1. Start Rust Ingestor
    echo -e "${CYAN}==> Starting Rust Ingestion Hub (:9100)...${NC}"
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

cmd_start_all() {
    cmd_start_infra
    cmd_start
}

cmd_stop_all() {
    cmd_stop
    cmd_stop_infra
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
        printf "  ${GREEN}%-24s${NC} ${GREEN}%-12s${NC} %-10s %s\n" "Rust Telemetry Ingestor" "RUNNING" ":9100" "PID: ${PID} "
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
    start-mosquitto)
        cmd_start_mosquitto
        ;;
    stop-mosquitto)
        cmd_stop_mosquitto
        ;;
    start-influxdb)
        cmd_start_influxdb
        ;;
    stop-influxdb)
        cmd_stop_influxdb
        ;;
    start-infra)
        cmd_start_infra
        ;;
    stop-infra)
        cmd_stop_infra
        ;;
    start-all)
        cmd_start_all
        ;;
    stop-all)
        cmd_stop_all
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|start-mosquitto|stop-mosquitto|start-influxdb|stop-influxdb|start-infra|stop-infra|start-all|stop-all}"
        exit 1
        ;;
esac
