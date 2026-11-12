#!/usr/bin/env bash
# Local development stack: ingestor + dashboard + simulated sensors, in the background.
# Used by `make start|stop|restart|status`. Mosquitto and InfluxDB are system services
# and must already be running (sudo systemctl start mosquitto influxdb).
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${BASE_DIR}/logs"
INGESTOR_BIN="${BASE_DIR}/ingestor/target/release/iot-airquality-ingestor"
mkdir -p "${LOG_DIR}"

# Local settings and secrets (INFLUXDB_TOKEN, ...): see .env.example
if [ -f "${BASE_DIR}/.env" ]; then
    set -a; . "${BASE_DIR}/.env"; set +a
fi

port_open() { (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# start_service <name> <command...>: runs in the background, logs to logs/<name>.log
start_service() {
    local name="$1"; shift
    setsid "$@" </dev/null > "${LOG_DIR}/${name}.log" 2>&1 &
    echo $! > "${LOG_DIR}/${name}.pid"
}

# SIGTERM first (the ingestor then writes its pending batch), SIGKILL after 5 s.
stop_service() {
    local pid_file="${LOG_DIR}/$1.pid" pid
    [ -f "${pid_file}" ] || return 0
    pid=$(cat "${pid_file}")
    rm -f "${pid_file}"
    kill "${pid}" 2>/dev/null || return 0
    for _ in $(seq 50); do
        kill -0 "${pid}" 2>/dev/null || return 0
        sleep 0.1
    done
    kill -9 "${pid}" 2>/dev/null || true
}

cmd_stop() {
    stop_service mock
    stop_service dashboard
    stop_service ingestor
    echo "Stack stopped."
}

cmd_start() {
    [ -n "${INFLUXDB_TOKEN:-}" ] || { echo "INFLUXDB_TOKEN is not set: copy .env.example to .env and fill it in." >&2; exit 1; }
    [ -x "${INGESTOR_BIN}" ]     || { echo "Ingestor not built: run 'make build'." >&2; exit 1; }
    port_open 1883 || { echo "Mosquitto is not reachable on port 1883 (sudo systemctl start mosquitto)." >&2; exit 1; }
    port_open 8086 || { echo "InfluxDB is not reachable on port 8086 (sudo systemctl start influxdb)." >&2; exit 1; }

    cmd_stop >/dev/null
    start_service ingestor "${INGESTOR_BIN}"
    start_service dashboard python3 -m uvicorn --app-dir "${BASE_DIR}/dashboard" main:app --host 127.0.0.1 --port 8000
    start_service mock python3 "${BASE_DIR}/firmware/tools/mock_publisher.py"

    echo "Stack started."
    echo "  Dashboard        http://localhost:8000/"
    echo "  Ingestor metrics http://localhost:9100/metrics"
    echo "  Logs             make logs"
}

cmd_status() {
    local name port
    for entry in "Mosquitto:1883" "InfluxDB:8086" "Ingestor:9100" "Dashboard:8000"; do
        name="${entry%%:*}"; port="${entry##*:}"
        if port_open "${port}"; then echo "  ${name} (port ${port}): running"; else echo "  ${name} (port ${port}): stopped"; fi
    done
    if [ -f "${LOG_DIR}/mock.pid" ] && kill -0 "$(cat "${LOG_DIR}/mock.pid")" 2>/dev/null; then
        echo "  Simulated sensors: running"
    else
        echo "  Simulated sensors: stopped"
    fi
}

case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_start ;;
    status)  cmd_status ;;
    *)       echo "Usage: $0 {start|stop|restart|status}" >&2; exit 1 ;;
esac
