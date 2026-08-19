# ==============================================================================
# Bare-Metal IoT Platform - Unified Automation Makefile
# ==============================================================================
SHELL := /bin/bash
.DEFAULT_GOAL := help

# Colors for terminal output
CYAN    := \033[0;36m
GREEN   := \033[0;32m
YELLOW  := \033[1;33m
RED     := \033[0;31m
NC      := \033[0m

## -----------------------------------------------------------------------------
## 📦 BUILD & COMPILATION
## -----------------------------------------------------------------------------

.PHONY: contracts
contracts: ## Compile Protobuf contracts (Nanopb C++, Rust Prost, Python mock)
	@echo -e "${CYAN}==> Compiling Protobuf Contracts...${NC}"
	@./contracts/compile.sh

.PHONY: build-ingestor
build-ingestor: ## Build optimized release binary of Rust Ingestion Engine (<3.5MB)
	@echo -e "${CYAN}==> Compiling Rust Telemetry Ingestor (Release LTO)...${NC}"
	@cd ingestor && cargo build --release

.PHONY: build
build: contracts build-ingestor ## Build all platform artifacts (Contracts + Rust Ingestor)
	@echo -e "${GREEN}✔ All platform components built successfully.${NC}"

## -----------------------------------------------------------------------------
## 🧪 TESTING & VERIFICATION
## -----------------------------------------------------------------------------

.PHONY: test
test: ## Run the complete end-to-end platform validation suite
	@./scripts/test-stack.sh

## -----------------------------------------------------------------------------
## 🚀 LOCAL EXECUTION & DEVELOPMENT
## -----------------------------------------------------------------------------

.PHONY: run-ingestor
run-ingestor: ## Run Rust Ingestor in foreground (Port 9100 metrics/stream)
	@echo -e "${CYAN}==> Starting Rust Ingestor...${NC}"
	@MQTT_BROKER_HOST=127.0.0.1 \
	 MQTT_BROKER_PORT=1883 \
	 MQTT_TOPIC="sensors/+/airquality" \
	 INFLUXDB_URL="http://127.0.0.1:8086" \
	 INFLUXDB_BUCKET="airquality" \
	 INFLUXDB_ORG="baremetal-iot" \
	 INFLUXDB_TOKEN="kH9pBO5KNEvbEh620uQz_T21DvVnVxb9OsvXAC-YZJN5esopKVVKOCugDTq7aBlcWMjQl5562RyHopbSE2vlow==" \
	 METRICS_PORT=9100 \
	 METRICS_ENABLED=true \
	 RUST_LOG="info,iot_airquality_ingestor=debug" \
	 ./ingestor/target/release/iot-airquality-ingestor

.PHONY: run-dashboard
run-dashboard: ## Run FastHTML Dashboard UI in foreground (Port 8000)
	@echo -e "${CYAN}==> Starting FastHTML Dashboard UI...${NC}"
	@cd dashboard && uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --loop uvloop

.PHONY: run-mock
run-mock: ## Run ESP32 Hardware Mock Telemetry Publisher
	@echo -e "${CYAN}==> Starting Hardware Mock Publisher...${NC}"
	@python3 ./firmware/tools/mock_publisher.py

## -----------------------------------------------------------------------------
## 🧹 CLEANUP
## -----------------------------------------------------------------------------

.PHONY: clean
clean: ## Clean build artifacts, temporary caches, and logs
	@echo -e "${YELLOW}==> Cleaning build artifacts...${NC}"
	@cd ingestor && cargo clean 2>/dev/null || true
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@rm -f *.log
	@echo -e "${GREEN}✔ Clean completed.${NC}"

## -----------------------------------------------------------------------------
## 📖 HELP
## -----------------------------------------------------------------------------

.PHONY: help
help: ## Display this interactive help menu
	@echo -e "\n${CYAN}==============================================================================${NC}"
	@echo -e "${CYAN}   Bare-Metal IoT Air Quality Platform - Developer Toolkit                     ${NC}"
	@echo -e "${CYAN}==============================================================================${NC}\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
