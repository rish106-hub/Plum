# Plum OPD claims processor

This local-first app checks outpatient claim documents, extracts evidence, applies the supplied policy, and shows an operations review trace. The 12 supplied cases are structured fixtures. Actual PDF/image uploads use a separate adapter and then the same claims evaluator.

## Quick start

Requires Python 3.12 or newer. Use `pip` as the package manager.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# Set SARVAM_API_KEY in .env for image and scanned-PDF extraction.
.venv/bin/python -m tools.generate_samples sample_documents
.venv/bin/python -m uvicorn claims.web:app --reload
```

Open <http://127.0.0.1:8000>. Local SQLite data and private uploads are stored under ignored `.data/`. Digital PDFs with selectable text can be processed locally; images and scanned PDFs require the Sarvam key. Use synthetic documents for this demonstration.

## Verification

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy claims scripts tools
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q claims scripts tools
.venv/bin/python -m scripts.evaluate
```

The eval command writes [EVAL_REPORT.md](EVAL_REPORT.md) and [complete JSON outputs](docs/eval_outputs.json). Its pass count measures fixture behavior, not OCR accuracy.

For a terminal-based synthetic PDF/image upload, OCR expectations, and decision-trace guide, see the [local runbook](docs/LOCAL_RUNBOOK.md).

## Module map

```text
claims/
  core.py        policy rules, reconciliation, money ledger and trace
  fixtures.py    structured test-case adapter
  documents.py   actual image/PDF gate and extraction adapter
  web.py         upload API, SQLite workflow and reviewer pages
  templates/     submission and review pages
  static/        UI styling and behavior
scripts/evaluate.py   reproducible 12-case report
tests/                component and HTTP behavior checks
docs/                 architecture, contracts and eval outputs
```

Document problems produce an actionable correction request with `decision: null`. Accepted evidence passes to deterministic code reading `policy_terms.json`; the output contains a decision, payable amount, reason codes, confidence, line-item ledger and trace. The supplied policy conflicts with TC006 and TC010 expected outcomes. Those fixture-compatible interpretations are explicitly recorded in the [architecture](docs/ARCHITECTURE.md) and [evaluation](EVAL_REPORT.md); they are not insurer approval authority.

Selectable PDF text is extracted locally. Sarvam digitisation is used for images/scans; schema extraction is reserved for accepted documents missing material fields. Its [published list prices](https://www.sarvam.ai/api-pricing) are ₹0.50/page for digitisation and ₹1/page for extraction. A three-page scan is therefore ₹1.50 for OCR alone or ₹4.50 if every page also needs schema extraction, before retries, storage, compute or review. These are scenarios, not measured spend.

## Submission artifacts

- [Architecture and 10× load path](docs/ARCHITECTURE.md)
- [Component contracts](docs/CONTRACTS.md)
- [Eval report with all 12 traces](EVAL_REPORT.md)
- [Demo outline](docs/DEMO.md)

The app has clear local setup instructions; no deployment URL is supplied here. All implementation commits use Rishav Dewan's Git identity.
