# ==============================================================================
# Bare-Metal IoT Platform - Unified Automation Makefile
# ==============================================================================
SHELL := /bin/bash
.DEFAULT_GOAL := help

# Local settings and secrets (INFLUXDB_TOKEN, ...): see .env.example
-include .env
export

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
## 🚀 STACK ORCHESTRATION & LIFECYCLE
## -----------------------------------------------------------------------------

.PHONY: start up
start: ## Start the application platform in background (Ingestor, Dashboard, Mock)
	@./scripts/stack-ctl.sh start

up: start ## Alias for 'make start'

.PHONY: stop down
stop: ## Gracefully stop all platform application processes (Ingestor, UI, Mock)
	@./scripts/stack-ctl.sh stop

down: stop ## Alias for 'make stop'

.PHONY: restart
restart: ## Restart the entire application platform stack
	@./scripts/stack-ctl.sh restart

.PHONY: status
status: ## Display live runtime status and health of all components
	@./scripts/stack-ctl.sh status

.PHONY: logs
logs: ## Stream live logs from all background services (Ctrl+C to exit)
	@mkdir -p logs
	@touch logs/ingestor.log logs/dashboard.log logs/mock.log
	@echo -e "${CYAN}==> Streaming platform logs (Press Ctrl+C to exit)...${NC}\n"
	@tail -f logs/ingestor.log logs/dashboard.log logs/mock.log

## -----------------------------------------------------------------------------
## 🗄️ INFRASTRUCTURE SERVICES (Mosquitto & InfluxDB)
## -----------------------------------------------------------------------------

.PHONY: start-infra
start-infra: ## Start both Mosquitto MQTT Broker and InfluxDB Engine
	@./scripts/stack-ctl.sh start-infra

.PHONY: stop-infra
stop-infra: ## Stop both Mosquitto MQTT Broker and InfluxDB Engine
	@./scripts/stack-ctl.sh stop-infra

.PHONY: start-mosquitto
start-mosquitto: ## Start Mosquitto MQTT Broker (:1883)
	@./scripts/stack-ctl.sh start-mosquitto

.PHONY: stop-mosquitto
stop-mosquitto: ## Stop Mosquitto MQTT Broker (:1883)
	@./scripts/stack-ctl.sh stop-mosquitto

.PHONY: start-influxdb
start-influxdb: ## Start InfluxDB Time-Series Engine (:8086)
	@./scripts/stack-ctl.sh start-influxdb

.PHONY: stop-influxdb
stop-influxdb: ## Stop InfluxDB Time-Series Engine (:8086)
	@./scripts/stack-ctl.sh stop-influxdb

.PHONY: start-all
start-all: ## Start EVERYTHING (Infra + Rust Ingestor + Dashboard + Mock)
	@./scripts/stack-ctl.sh start-all

.PHONY: stop-all
stop-all: ## Stop EVERYTHING (Mock + Dashboard + Rust Ingestor + Infra)
	@./scripts/stack-ctl.sh stop-all

## -----------------------------------------------------------------------------
## 🚀 FOREGROUND DEV (SINGLE SERVICES)
## -----------------------------------------------------------------------------

.PHONY: run-ingestor
run-ingestor: ## Run Rust Ingestor in foreground (Port 9100 metrics/stream)
	@echo -e "${CYAN}==> Starting Rust Ingestor...${NC}"
	@RUST_LOG="info,iot_airquality_ingestor=debug" ./ingestor/target/release/iot-airquality-ingestor

.PHONY: run-dashboard
run-dashboard: ## Run FastHTML Dashboard UI in foreground (Port 8000)
	@echo -e "${CYAN}==> Starting FastHTML Dashboard UI...${NC}"
	@cd dashboard && uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1 --loop uvloop

.PHONY: run-mock
run-mock: ## Run ESP32 Hardware Mock Telemetry Publisher in foreground
	@echo -e "${CYAN}==> Starting Hardware Mock Publisher...${NC}"
	@python3 ./firmware/tools/mock_publisher.py

## -----------------------------------------------------------------------------
## 🧹 CLEANUP
## -----------------------------------------------------------------------------

.PHONY: clean
clean: stop ## Clean build artifacts, temporary caches, and logs
	@echo -e "${YELLOW}==> Cleaning build artifacts and logs...${NC}"
	@cd ingestor && cargo clean 2>/dev/null || true
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@rm -rf logs
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

