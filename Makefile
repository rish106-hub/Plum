# Convenience targets. PYTHON defaults to the project venv; override with `make verify PYTHON=python3`.
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
export PYTHONPATH := $(CURDIR)

.PHONY: verify verify-live test lint eval eval-documents eval-live samples demo

## Full offline verification: artifact hashes, ruff, mypy, pytest, evaluations, reports, summary.
verify:
	$(PYTHON) -m scripts.verify_submission

## Same, plus the live OCR benchmark (needs SARVAM_API_KEY; makes paid provider calls).
verify-live:
	$(PYTHON) -m scripts.verify_submission --live

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy claims scripts tools

eval:
	$(PYTHON) -m scripts.evaluate

eval-documents:
	$(PYTHON) -m scripts.evaluate_documents

eval-live:
	$(PYTHON) -m scripts.evaluate_documents --providers live

samples:
	$(PYTHON) -m tools.generate_samples

## Local demo with the 2024 policy period replayed (development only; see docs/guides/local-runbook.md).
demo:
	PLUM_ENV=development PLUM_DEMO_CLOCK=2024-11-05 $(PYTHON) -m uvicorn claims.web:app
