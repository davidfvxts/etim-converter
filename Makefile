.PHONY: setup test lint

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -U pip && pip install -r requirements.txt

test:
	ETIM_DRY_RUN=1 python -m pytest -q

lint:
	python -m pyflakes etim tests || true
