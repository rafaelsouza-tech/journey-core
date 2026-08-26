# =============================================================================
# journey-core — comandos de desenvolvimento
# Uso: make <alvo>   (make help lista tudo)
# =============================================================================

.DEFAULT_GOAL := help
UV ?= uv
PORT ?= 8000

BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[1;33m
NC := \033[0m

.PHONY: help setup install dev run test test-cov test-unit test-integration test-e2e \
        lint format typecheck secrets check demo docker-build docker-up docker-down clean

help: ## Lista os comandos disponíveis
	@echo ""
	@echo "$(BLUE)journey-core$(NC)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(GREEN)%-18s$(NC) %s\n", $$1, $$2}'
	@echo ""

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------

setup: install ## Instala dependências e cria .env com PHONE_HASH_SALT gerado
	@if [ -f .env ]; then \
		echo "$(YELLOW)✓ .env já existe — mantido$(NC)"; \
	else \
		cp .env.example .env; \
		salt=$$(openssl rand -hex 32 2>/dev/null || $(UV) run python -c "import secrets; print(secrets.token_hex(32))"); \
		sed -i.bak "s/^PHONE_HASH_SALT=.*/PHONE_HASH_SALT=$$salt/" .env && rm -f .env.bak; \
		echo "$(GREEN)✓ .env criado com PHONE_HASH_SALT gerado$(NC)"; \
	fi

install: ## Instala dependências (uv sync)
	@$(UV) sync --frozen
	@echo "$(GREEN)✓ Dependências instaladas$(NC)"

# -----------------------------------------------------------------------------
# Execução
# -----------------------------------------------------------------------------

dev: ## Sobe a API com hot reload em http://localhost:$(PORT)
	$(UV) run uvicorn app.main:create_app --factory --reload --host 0.0.0.0 --port $(PORT)

run: ## Sobe a API sem reload
	$(UV) run uvicorn app.main:create_app --factory --host 0.0.0.0 --port $(PORT)

demo: ## Executa o roteiro do revisor de ponta a ponta (in-process; --base-url para API no ar)
	$(UV) run python scripts/demo.py $(ARGS)

# -----------------------------------------------------------------------------
# Testes
# -----------------------------------------------------------------------------

test: ## Roda toda a suíte
	$(UV) run pytest

test-unit: ## Só testes unitários
	$(UV) run pytest -m unit

test-integration: ## Só testes de integração (endpoints)
	$(UV) run pytest -m integration

test-e2e: ## Só o walkthrough do revisor
	$(UV) run pytest -m e2e

test-cov: ## Testes com cobertura (falha abaixo de 90%)
	$(UV) run pytest --cov=app --cov-report=term-missing --cov-fail-under=90

# -----------------------------------------------------------------------------
# Qualidade
# -----------------------------------------------------------------------------

lint: ## Ruff (lint + formatação, sem alterar)
	$(UV) run ruff check app tests scripts
	$(UV) run ruff format --check app tests scripts

format: ## Formata e corrige o que for automático
	$(UV) run ruff format app tests scripts
	$(UV) run ruff check --fix app tests scripts

typecheck: ## Mypy com type hints obrigatórios em app/
	$(UV) run mypy app

secrets: ## Varredura de segredos (gitleaks via pre-commit)
	$(UV) run pre-commit run gitleaks --all-files

check: lint typecheck test-cov ## Gate completo: lint + tipos + testes com cobertura + invariantes
	@echo "$(BLUE)Invariantes do enunciado$(NC)"
	@! grep -rnE 'template_id\s*(==|!=)\s*["'"'"']' app/ || (echo "branching por template encontrado" && exit 1)
	@! grep -rniE 'openai|anthropic|langchain|langgraph|langfuse|vertex' pyproject.toml app/ || (echo "dependência de IA encontrada" && exit 1)
	@echo "$(GREEN)✓ check completo$(NC)"

# -----------------------------------------------------------------------------
# Docker
# -----------------------------------------------------------------------------

docker-build: ## Build da imagem
	docker compose build

docker-up: ## Sobe a API em container (usa .env)
	docker compose up --build

docker-down: ## Derruba o container
	docker compose down

# -----------------------------------------------------------------------------
# Limpeza
# -----------------------------------------------------------------------------

clean: ## Remove caches e relatórios
	@find . -type d -name "__pycache__" -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	@echo "$(GREEN)✓ Limpo$(NC)"
