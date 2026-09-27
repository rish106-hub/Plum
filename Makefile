# Convenience targets. PYTHON defaults to the project venv; override with `make verify PYTHON=python3`.
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
export PYTHONPATH := $(CURDIR)

.PHONY: verify verify-live test lint eval eval-documents eval-live live-outcome-generate live-outcome-run live-outcome-report samples browser-check demo

## Full offline verification: artifact hashes, ruff, mypy, pytest, evaluations, reports, summary.
verify:
	$(PYTHON) -m scripts.verify_submission

## Same, plus dirty-corpus OCR accuracy (needs SARVAM_API_KEY; makes paid provider calls).
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

## Generate the seven synthetic image-only PDFs used by the website-driven outcome regression.
live-outcome-generate:
	$(PYTHON) -m tools.live_ocr_outcome_benchmark generate

## Submit all four outcomes through a running website and verify them through its API.
## Requires SARVAM_API_KEY in the server and PLUM_REVIEW_TOKEN in this process.
live-outcome-run:
	$(PYTHON) -m tools.live_ocr_outcome_benchmark run --base-url $${BASE_URL:-http://127.0.0.1:8000}

## Rebuild the Markdown report from the retained machine-readable result without provider calls.
live-outcome-report:
	$(PYTHON) -m tools.live_ocr_outcome_benchmark render

samples:
	$(PYTHON) -m tools.generate_samples

## Run the real browser flow against a separately started development server.
browser-check: samples
	$(PYTHON) -m scripts.browser_check

## Local demo with the 2024 policy period replayed (development only; see docs/guides/local-runbook.md).
demo:
	PLUM_ENV=development PLUM_DEMO_CLOCK=2024-11-05 $(PYTHON) -m uvicorn claims.web:app
