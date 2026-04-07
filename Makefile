# Makefile

.PHONY: test-agent test-agent-dani test-agent-query logs-agent build up down

## Start all services
up:
	docker compose up -d

## Stop all services
down:
	docker compose down

## Build all images
build:
	docker compose build

## Run Phase 2 agent test as researcher-jane (default)
test-agent:
	docker compose exec api python -m api.agent.test_agent

test_mcp:
	docker compose exec mcp-server python -m test_tools
## Run Phase 2 agent test as researcher-dani (mixed access)
test-agent-dani:
	docker compose exec -e TEST_RESEARCHER=researcher-dani api python -m api.agent.test_agent

## Run a single custom query: make test-agent-query Q="your question here"
test-agent-query:
	docker compose exec api python -m api.agent.test_agent "$(Q)"

## Tail agent-related logs from the API container
logs-agent:
	docker compose logs -f api | grep -E "(agent|tool|query|mcp|ERROR|WARNING)"

## Tail MCP server logs
logs-mcp:
	docker compose logs -f mcp-server

## Check that all required services are healthy
health-check:
	@echo "Checking service health..."
	@docker compose ps
	@echo "\nPostgreSQL:"
	@docker compose exec postgres pg_isready -U ctuser -d clinical_trials
	@echo "\nMCP Server:"
	@curl -sf http://localhost:8001/health && echo "OK" || echo "UNREACHABLE"
	@echo "\nAPI:"
	@curl -sf http://localhost:8000/health && echo "OK" || echo "UNREACHABLE"