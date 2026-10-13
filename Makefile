.DEFAULT_GOAL := help
SHELL := /bin/bash

BLUEPRINT ?= spec/examples/firecraft.blueprint.yaml
INPUT     ?=
Q         ?= how do I build a dakota fire hole?
TOPK      ?= 5

set_env = set -a; [ -f .env ] && source .env; set +a;

.PHONY: help setup up down shell reset test validate forge-install serve-install agent-install

help:
	@echo "RAGmen"
	@echo ""
	@echo "  make test                          offline test suite (no LLM, no DB)"
	@echo "  make validate BLUEPRINT=<path>     validate a blueprint YAML"
	@echo ""
	@echo "  forge add INPUT=<path> BLUEPRINT=<path>   register + chunk a source"
	@echo "  forge cook <source_id>                    resolve → embed → write"
	@echo "  forge retract <source_id>                 remove source with refcounting"
	@echo "  forge conflicts                           list multi-source disagreements"
	@echo ""
	@echo "  make serve                         start read-only MCP server (memory)"
	@echo "  make up / down / shell             Neo4j container"
	@echo ""
	@echo "current: BLUEPRINT=$(BLUEPRINT)"

test:
	bash tests/test_blueprint.sh
	cd forge && python -m pytest -q --tb=short -W ignore::DeprecationWarning
	cd serve && python -m pytest -q --tb=short -W ignore::DeprecationWarning
	cd agent && python -m pytest -q --tb=short -W ignore::DeprecationWarning

validate:
	python blueprint/scripts/validate_blueprint.py $(BLUEPRINT)

forge-install:
	cd forge && pip install -e ".[dev]"

serve-install:
	cd serve && pip install -e ".[dev]"

agent-install:
	cd agent && pip install -e ".[dev]"

setup: forge-install serve-install agent-install

serve:
	@$(set_env) lattice-serve --store memory --blueprint $(BLUEPRINT)

up:
	docker compose up -d

down:
	docker compose down

shell:
	@$(set_env) docker compose exec neo4j cypher-shell -u $$NEO4J_USER -p $$NEO4J_PASSWORD

reset:
	docker compose down -v && docker compose up -d
