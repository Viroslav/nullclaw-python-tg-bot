.PHONY: up down logs build pull status reset-nullwatch reset-nullclaw clean

## First-time setup (copies nullwatch-py, checks .env)
setup:
	bash setup.sh

## Start the full stack
up:
	@[ -f .env ] || (echo "❌ Run 'make setup' first" && exit 1)
	docker compose up -d --build
	@echo ""
	@echo "✅ Stack starting — ports:"
	@echo "   nullwatch → http://127.0.0.1:7710"
	@echo "   nullclaw  → http://127.0.0.1:3000"
	@echo "   ollama    → http://127.0.0.1:11434"
	@echo ""
	@echo "Run 'make logs' or 'make status' to check."

## Stop all services (volumes preserved)
down:
	docker compose down

## Follow logs for all services
logs:
	docker compose logs -f --tail=100

## Follow logs for one service: make logs-bot | make logs-nullclaw | make logs-nullwatch
logs-%:
	docker compose logs -f --tail=100 $*

## Show container health
status:
	docker compose ps

## Rebuild bot + nullwatch images (after code changes)
build:
	bash setup.sh
	docker compose build --no-cache bot nullwatch

## Pull latest nullclaw and ollama images
pull:
	docker compose pull nullclaw ollama

## Wipe nullwatch trace data only
reset-nullwatch:
	docker compose stop nullwatch
	docker volume rm nullclaw-python-tg-bot_nullwatch-data 2>/dev/null || true
	docker compose up -d nullwatch

## Wipe nullclaw agent state only (memory, identity, etc.)
reset-nullclaw:
	docker compose stop nullclaw bot
	docker volume rm nullclaw-python-tg-bot_nullclaw-data nullclaw-python-tg-bot_nullclaw-workspace 2>/dev/null || true
	docker compose up -d nullclaw bot

## Full reset — removes ALL data and images (cannot be undone)
clean:
	docker compose down -v
	docker rmi nullwatch-local:latest 2>/dev/null || true
