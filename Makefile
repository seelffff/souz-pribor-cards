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

EXAMPLES = manometer_dm02 mp3u_manotom pd100_feed_only injection

examples:
	for name in $(EXAMPLES); do \
		$(PYTHON) -m app generate examples/input/$$name.json --out-dir examples/output; \
	done
	-$(PYTHON) -m app generate examples/input/broken.json 2> examples/output/broken.txt

.PHONY: examples
