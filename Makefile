# IoT Air Quality Platform - developer commands. Run `make help`.
SHELL := /bin/bash
.DEFAULT_GOAL := help

# Local settings and secrets (INFLUXDB_TOKEN, ...): see .env.example
-include .env
export

.PHONY: help build contracts test start stop restart status logs run-ingestor run-dashboard run-mock clean

help: ## List the available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-14s %s\n", $$1, $$2}'

build: ## Build the ingestor (release)
	cd ingestor && cargo build --release

contracts: ## Regenerate Protobuf bindings after a contract change (see contracts/README.md)
	./contracts/compile.sh

test: ## Run all automated checks (unit tests, end-to-end test, firmware host test)
	./scripts/test-stack.sh

start: ## Start ingestor, dashboard and simulated sensors in the background
	@./scripts/stack-ctl.sh start

stop: ## Stop the background stack
	@./scripts/stack-ctl.sh stop

restart: ## Restart the background stack
	@./scripts/stack-ctl.sh restart

status: ## Show which services are running
	@./scripts/stack-ctl.sh status

logs: ## Follow the logs of the background stack
	@tail -F logs/ingestor.log logs/dashboard.log logs/mock.log

run-ingestor: ## Run the ingestor in the foreground
	./ingestor/target/release/iot-airquality-ingestor

run-dashboard: ## Run the dashboard in the foreground
	cd dashboard && python3 -m uvicorn main:app --host 127.0.0.1 --port 8000

run-mock: ## Publish simulated sensor packets in the foreground
	python3 ./firmware/tools/mock_publisher.py

clean: stop ## Remove build artifacts and logs
	cd ingestor && cargo clean
	rm -rf logs
