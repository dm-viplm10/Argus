.PHONY: up down logs test lint format setup verify evaluate graph-export

setup:              ## First-time setup: build images, init DB
	docker compose build
	docker compose up neo4j redis -d
	sleep 10
	docker compose run --rm app python -m scripts.seed_neo4j
	docker compose down

up:                 ## Start all services
	docker compose up -d

down:               ## Stop all services
	docker compose down

logs:               ## Tail all logs
	docker compose logs -f

test:               ## Run test suite
	docker compose run --rm app pytest tests/ -v

lint:               ## Run linter
	docker compose run --rm app ruff check src/ tests/

format:             ## Auto-format code
	docker compose run --rm app ruff format src/ tests/

evaluate:           ## Run evaluation framework
	docker compose run --rm app python -m scripts.run_evaluation

verify:             ## Verify all infra and API keys
	docker compose run --rm app python -m scripts.verify_setup

graph-export:       ## Export identity graph
	docker compose run --rm app python -m scripts.export_graph
