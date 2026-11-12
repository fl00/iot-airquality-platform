#!/usr/bin/env bash
# Runs every automated check of the platform. Used by `make test` and by CI.
#
#   1. Ingestor unit tests            (cargo)
#   2. End-to-end test                (private mosquitto, influxd, ingestor, dashboard: see e2e_test.py)
#   3. Firmware encoding, host build  (only when NANOPB_DIR points to a nanopb 0.4.x checkout)
#   4. JavaScript syntax              (only when node is installed)
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${BASE_DIR}"

echo "== [1/4] Ingestor unit tests =="
(cd ingestor && cargo test --quiet)

echo "== [2/4] End-to-end test =="
(cd ingestor && cargo build --release --quiet)
python3 scripts/e2e_test.py

echo "== [3/4] Firmware encoding (host build) =="
if [ -n "${NANOPB_DIR:-}" ]; then
    firmware/test_host/run.sh
else
    echo "skipped: NANOPB_DIR is not set"
fi

echo "== [4/4] JavaScript syntax =="
if command -v node >/dev/null 2>&1; then
    node -c dashboard/static/js/stream.js
    node -c dashboard/static/js/chart.js
    echo "ok"
else
    echo "skipped: node is not installed"
fi

echo "ALL CHECKS PASSED"
