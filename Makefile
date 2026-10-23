# Uso: make help
PY       ?= .venv/bin/python
PIP      ?= .venv/bin/pip
DATAFORM ?= npx --yes @dataform/cli@3.0.0
TF       ?= terraform

.PHONY: help venv install lint format test compile tf-fmt tf-validate docker-build run-local zip clean

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

venv: ## Crea el entorno virtual .venv
	python3 -m venv .venv

install: venv ## Instala el paquete con extras de desarrollo y GCP
	$(PIP) install -e ".[dev,gcp]" duckdb sqlglot

lint: ## ruff check + format --check
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

format: ## Formatea con ruff
	$(PY) -m ruff format src tests
	$(PY) -m ruff check --fix src tests

test: ## pytest (la prueba SQL/DuckDB requiere DATAFORM_CLI o dataform en el PATH)
	$(PY) -m pytest

compile: ## Compila el proyecto Dataform
	cd dataform && $(DATAFORM) compile

tf-fmt: ## terraform fmt -check
	cd infra/terraform && $(TF) fmt -check -recursive

tf-validate: ## terraform init -backend=false && validate
	cd infra/terraform && $(TF) init -backend=false -input=false && $(TF) validate

docker-build: ## Construye la imagen del job
	docker build -t conciliacion:local .

run-local: ## Parsea un archivo local: make run-local ARCHIVO="ruta/28-09 al 04-10.xls"
	$(PY) -m conciliacion.cli parse "$(ARCHIVO)" --out salida_local

zip: ## Empaqueta el repo sin .git/.venv/caches
	cd .. && rm -f conciliacion-victoriafoods.zip && zip -qr conciliacion-victoriafoods.zip conciliacion-victoriafoods \
	  -x 'conciliacion-victoriafoods/.git/*' 'conciliacion-victoriafoods/.venv/*' '*/__pycache__/*' \
	     '*/.pytest_cache/*' '*/.ruff_cache/*' '*.egg-info/*' '*/node_modules/*' '*/.terraform/*'

clean: ## Borra caches
	rm -rf .pytest_cache .ruff_cache salida_local src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
