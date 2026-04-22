.PHONY: venv install run help

PY := .venv/bin/python
PIP := .venv/bin/pip

help:
	@echo "Targets:"
	@echo "  make venv    — create .venv"
	@echo "  make install — pip install -r requirements.txt"
	@echo "  make run     — start server + bot (single process)"

venv:
	python3 -m venv .venv
	@echo "Done. Now run: make install"

install:
	$(PIP) install -r requirements.txt

run:
	$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000
