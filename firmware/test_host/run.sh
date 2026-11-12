#!/usr/bin/env bash
# Usage: NANOPB_DIR=/path/to/nanopb ./run.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FW="${HERE}/.."
: "${NANOPB_DIR:?set NANOPB_DIR to a nanopb 0.4.x checkout}"
OUT="$(mktemp -d)"
for f in pb_encode pb_common; do gcc -c -I"${NANOPB_DIR}" "${NANOPB_DIR}/${f}.c" -o "${OUT}/${f}.o"; done
gcc -c -I"${NANOPB_DIR}" -I"${FW}/include" "${FW}/src/air_quality.pb.c" -o "${OUT}/aq.o"
g++ -std=c++17 -Wall -Wextra -I"${NANOPB_DIR}" -I"${FW}/include" "${HERE}/test_telemetry.cpp" "${OUT}"/*.o -o "${OUT}/t"
HEX="$("${OUT}/t")"
python3 - "$HEX" "${FW}/tools" <<'PY'
import sys
sys.path.insert(0, sys.argv[2])
from proto.air_quality_pb2 import AirQualityPacket, SensorStatus
p = AirQualityPacket(); p.ParseFromString(bytes.fromhex(sys.argv[1]))
fs = p.full_sample
assert p.device_id == "sensor-esp32-01" and p.sequence_number == 42
assert p.base_timestamp_sec == 1794000000 and p.rssi_dbm == -61
assert p.status == SensorStatus.STATUS_WARMING_UP and p.HasField("full_sample")
# co2_ppm must be the SCD41 value (612), not the 999 ppm BSEC estimate
assert fs.co2_ppm == 612 and abs(fs.tvoc_ppb - 731) < 0.01 and abs(fs.temperature_celsius - 21.37) < 1e-4
assert fs.static_iaq == 62.5 and fs.iaq_accuracy == 3
assert fs.pm25_ug_m3 == 156 and fs.pm10_ug_m3 == 510 and abs(fs.pressure_hpa - 1008.2) < 1e-3
print(f"OK - {len(sys.argv[1])//2}-byte packet decodes with the Python bindings")
PY
