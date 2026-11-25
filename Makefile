# Thin wrappers around uv. Every target has a plain-command equivalent in README.md.
# On machines where uv isn't on PATH:  make UV="python -m uv" test
UV ?= uv
T ?=

.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test test-one smoke check probes hooks hooks-install clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install the exact locked environment
	$(UV) sync --locked

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-format and apply safe lint fixes
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

typecheck: ## Strict mypy over src/ and tests/
	$(UV) run mypy

test: ## All tests
	$(UV) run pytest

test-one: ## One test: make test-one T=tests/unit/test_cli.py::test_info_json
	@test -n "$(T)" || (echo "usage: make test-one T=<pytest node id>" && exit 2)
	$(UV) run pytest "$(T)"

smoke: ## Smoke tests only
	$(UV) run pytest -m smoke

check: lint typecheck test ## Everything CI's checks job runs

probes: ## Re-run the RAG-M1 probes on this OS and compare with the expected run
	UV="$(UV)" bash scripts/probes/run_probes.sh

hooks-install: ## Install the git pre-commit hook
	$(UV) run pre-commit install

hooks: ## Run every pre-commit hook on all files
	$(UV) run pre-commit run --all-files

clean: ## Remove caches and probe environments (keeps .venv)
	rm -rf .pytest_cache .mypy_cache .ruff_cache .probe-envs results/probes
	find . -name __pycache__ -type d -prune -not -path './.venv/*' -exec rm -rf {} +
