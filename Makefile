.PHONY: help install db dev engine build lint typecheck test test-core test-engine up down logs smoke clean

help:
	@echo "RecoverGraph console — targets:"
	@echo "  make install      install web deps + prisma client"
	@echo "  make db           create/push the SQLite schema (data/dashboard.db)"
	@echo "  make dev          dashboard dev server on :3000 (auto-spawns engine sidecar)"
	@echo "  make engine       run the engine service standalone on :3031"
	@echo "  make build        production build (standalone output)"
	@echo "  make lint         eslint"
	@echo "  make typecheck    tsc --noEmit"
	@echo "  make test         python suites: engine + recovergraph core"
	@echo "  make up / down    docker compose production stack"
	@echo "  make smoke        end-to-end engine smoke test (submit + poll a real job)"

install:
	bun install
	bunx prisma generate

db:
	bunx prisma db push

dev:
	bun run dev

engine:
	cd engine && python3 index.py

build:
	bun run build

lint:
	bun run lint

typecheck:
	bun run typecheck

test: test-engine test-core

test-engine:
	python3 -m pytest engine/tests -q

test-core:
	python3 -m pytest recovergraph/tests -q

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

smoke:
	bash scripts/smoke.sh

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .next
