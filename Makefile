PYTHON ?= python

run:
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

.PHONY: run lint fmt

eval:
	$(PYTHON) -m eval.run_eval --runs 5

.PHONY: eval
