# nuh, the client. `make help` lists targets.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

VERSION := $(shell grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)"/\1/')

AGENTS        ?=
AGENT_FLAG    := $(if $(AGENTS),--agent $(AGENTS))
CLIENT_PYTHON ?= python3
CLIENT_VENV   := .venv

.PHONY: help test lint install hook-install update man docs tag release clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@if [[ -f server/Makefile ]]; then echo "  The decision server has its own targets: make -C server help"; fi

$(CLIENT_VENV)/.deps: pyproject.toml Makefile
	test -x $(CLIENT_VENV)/bin/python || $(CLIENT_PYTHON) -m venv $(CLIENT_VENV)
	$(CLIENT_VENV)/bin/pip install -q -e '.[dev]'
	touch $@

# --- test and lint ----------------------------------------------------------

test: $(CLIENT_VENV)/.deps ## Tests (pytest tests/, live-LLM tests excluded)
	$(CLIENT_VENV)/bin/pytest tests/ --ignore=tests/test_llm_live.py -q

# The gate is syntax errors and undefined names only, until the existing
# findings are cleaned up.
lint: $(CLIENT_VENV)/.deps ## Lint (ruff, error-class rules)
	$(CLIENT_VENV)/bin/ruff check --isolated --select E9,F63,F7,F82 src tests scripts

# --- install ----------------------------------------------------------------

# The man page is rendered into the wheel when pipx builds it (hatch_build.py), so after changing help
# text, run this again. The completion is written by the pipx copy, the same step `nuh install` runs.
install: ## Install nuh for your user (pipx, editable from this clone) with its man page and zsh completion
	pipx install --force --editable .
	"$$(pipx environment --value PIPX_BIN_DIR)/nuh" install --completion-only

hook-install: $(CLIENT_VENV)/.deps ## Install the nuh hook from this clone's .venv (AGENTS=all adds Codex)
	$(CLIENT_VENV)/bin/nuh install $(AGENT_FLAG)

# The hook script runs nuh with the interpreter of the nuh that wrote it, so rewrite it with the pipx copy
# `install` just refreshed: the installed hook then matches the installed package.
update: install ## Refresh the pipx install (make install), then rewrite the hook with it (AGENTS=all adds Codex)
	"$$(pipx environment --value PIPX_BIN_DIR)/nuh" update $(AGENT_FLAG)

# --- docs -------------------------------------------------------------------

man: $(CLIENT_VENV)/.deps ## Render the man page to build/man/nuh.1 for a preview (man -l build/man/nuh.1)
	PYTHONPATH=src $(CLIENT_VENV)/bin/python -m nuh.manpage build/man/nuh.1

# mkdocs and the forge read these pages straight from the repo, so their generated blocks are committed;
# tests/test_gen_cli_docs.py fails while one differs from what this renders.
docs: $(CLIENT_VENV)/.deps ## Regenerate the docs' generated blocks: site/cli.md, README's command list, action types
	PYTHONPATH=src $(CLIENT_VENV)/bin/python scripts/gen_cli_docs.py

# --- release ----------------------------------------------------------------

tag: ## Tag vX.Y.Z (version from pyproject.toml) at HEAD
	@if git rev-parse -q --verify "refs/tags/v$(VERSION)" >/dev/null; then \
		echo "Tag v$(VERSION) already exists; bump the version in pyproject.toml first."; \
		exit 1; \
	fi
	git tag -a v$(VERSION) -m "v$(VERSION)"
	@echo "Tagged v$(VERSION). Push it to release: git push origin v$(VERSION)"

release: lint test tag ## Lint, test, then tag; pushing the tag runs the release workflow

clean: ## Remove caches and build artifacts (keeps .venv)
	rm -rf dist build .pytest_cache .pytest-tmp .ruff_cache
	find . \( -path ./.venv -o -path ./server/.venv -o -path ./.git \) -prune -o \
		-name __pycache__ -type d -prune -exec rm -rf {} +
