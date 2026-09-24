.PHONY: lint test source-check integrity check

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
RUFF ?= $(if $(wildcard .venv/bin/ruff),.venv/bin/ruff,ruff)
PYTHON_EXECUTABLE := $(shell command -v $(PYTHON))

lint:
	$(RUFF) check runtime tests
	$(RUFF) format --check runtime tests
	$(PYTHON) -B runtime/crm/workspace_lint.py --source-only

test:
	cd runtime && $(abspath $(PYTHON_EXECUTABLE)) -B -m unittest discover -s tests -v
	cd runtime/linkedin && $(abspath $(PYTHON_EXECUTABLE)) -B -m unittest discover -s tests -v
	$(PYTHON) -B -m unittest discover -s tests -v
	node --test agents/agent-dm-agent/protocol-test/webmcp-contract.test.cjs

source-check: lint test

integrity:
	cd runtime && $(abspath $(PYTHON_EXECUTABLE)) -B -m crm.cli integrity
	cd runtime && $(abspath $(PYTHON_EXECUTABLE)) -B -m crm.coverage --output reports/coverage.json
	cd runtime/linkedin && $(abspath $(PYTHON_EXECUTABLE)) -B store.py verify

check: source-check integrity
