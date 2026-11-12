#!/usr/bin/env bash
# Regenerates the language bindings of contracts/proto/air_quality.proto.
#
#   Python (mock publisher, tests)  firmware/tools/proto/air_quality_pb2.py   protoc or grpc_tools
#   C (firmware, Nanopb)            firmware/include|src/air_quality.pb.{h,c}  nanopb_generator.py
#   Rust (ingestor, Prost)          ingestor/src/proto/mod.rs                  maintained by hand
#
# A generator that is not installed is skipped: the committed bindings are then
# kept, and must be updated by hand if the contract changed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROTO_DIR="${SCRIPT_DIR}/proto"
PROTO_SRC="${PROTO_DIR}/air_quality.proto"
PY_OUT="${ROOT_DIR}/firmware/tools/proto"

if command -v protoc >/dev/null 2>&1; then
    protoc -I"${PROTO_DIR}" --python_out="${PY_OUT}" "${PROTO_SRC}"
    echo "Python bindings regenerated (protoc)."
elif python3 -c "import grpc_tools" >/dev/null 2>&1; then
    python3 -m grpc_tools.protoc -I"${PROTO_DIR}" --python_out="${PY_OUT}" "${PROTO_SRC}"
    echo "Python bindings regenerated (grpc_tools)."
else
    echo "Python bindings NOT regenerated: install protoc or 'pip install grpcio-tools'."
fi

if command -v nanopb_generator.py >/dev/null 2>&1; then
    nanopb_generator.py -I "${PROTO_DIR}" -D "${ROOT_DIR}/firmware/include" "${PROTO_SRC}"
    mv "${ROOT_DIR}/firmware/include/air_quality.pb.c" "${ROOT_DIR}/firmware/src/"
    echo "Nanopb bindings regenerated."
else
    echo "Nanopb bindings NOT regenerated: nanopb_generator.py not found."
fi

echo "Rust bindings are maintained by hand: ingestor/src/proto/mod.rs."
