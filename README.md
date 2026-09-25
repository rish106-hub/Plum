# Plum OPD claims processor

This local-first app checks outpatient claim documents, extracts evidence, applies the supplied policy, and shows an operations review trace. The 12 supplied cases are structured fixtures. Actual PDF/image uploads use a separate adapter and then the same claims evaluator.

## Quick start

Requires Python 3.12 or newer. Use `pip` as the package manager.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# Set SARVAM_API_KEY for image/scanned-PDF extraction. Set GEMINI_API_KEY and
# GEMINI_EVIDENCE_REVIEW_ENABLED=true only after approving external transmission.
# Clear files need no Gemini call even while it is enabled.
.venv/bin/python -m tools.generate_samples
.venv/bin/python -m uvicorn claims.web:app --reload
```

Open <http://127.0.0.1:8000> for claim submission and <http://127.0.0.1:8000/ops> for the local operations worklist. Local SQLite data and private uploads are stored under ignored `.data/`. Digital PDFs with selectable text can be processed locally; images and scanned PDFs require the Sarvam key. Exact repeats of an uploaded bill route to manual review, and the app derives claim frequency and prior approved benefit usage from its local history. Use synthetic documents for this demonstration. The operations page is a local prototype without authentication; add access controls before using real member data.

## Verification

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy claims scripts tools
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q claims scripts tools
.venv/bin/python -m scripts.evaluate
```

The eval command writes an [evaluation report](docs/reports/evaluation.md) and [machine-readable outputs](docs/reports/evaluation-data.json). Its pass count measures fixture behavior, not OCR accuracy.

For a terminal-based synthetic PDF/image upload, OCR expectations, and decision-trace guide, see the [local runbook](docs/guides/local-runbook.md).

## Module map

```text
claims/
  agent_pipeline.py  checked handoffs and fail-closed decision finalization
  core.py        policy rules, reconciliation, money ledger and trace
  fixtures.py    structured test-case adapter
  documents.py   actual image/PDF gate and extraction adapter
  web.py         upload API, SQLite workflow and reviewer pages
  templates/     submission and review pages
  static/        UI styling and behavior
scripts/evaluate.py   reproducible 12-case report
data/                 versioned policy configuration
tests/                component and HTTP checks; fixtures live in tests/fixtures/
docs/                 architecture, guides, reports, design, research, and reference material
```

Document problems produce an actionable correction request with `decision: null`. Accepted evidence passes to deterministic code reading `data/policy_terms.json`; the output contains a decision, payable amount, reason codes, confidence, line-item ledger and trace. Prior approved amounts are the local prototype's annual benefit consumption proxy; there is no insurer remittance feed to distinguish approval from actual payment. The supplied policy conflicts with TC006 and TC010 expected outcomes. Those fixture-compatible interpretations are explicitly recorded in the [architecture](docs/architecture/overview.md) and [evaluation](docs/reports/evaluation.md); they are not insurer approval authority.

Selectable PDF text is extracted locally. Sarvam digitisation is used for images/scans; schema extraction is reserved for accepted documents missing material fields. Its [published list prices](https://www.sarvam.ai/api-pricing) are ₹0.50/page for digitisation and ₹1/page for extraction. A three-page scan is therefore ₹1.50 for OCR alone or ₹4.50 if every page also needs schema extraction, before retries, storage, compute or review. These are scenarios, not measured spend.

## Submission artifacts

- [Architecture and 10× load path](docs/architecture/overview.md)
- [Specialist architecture and prompts](docs/architecture/agent-pipeline.md)
- [Component contracts](docs/architecture/contracts.md)
- [Evaluation report with all 12 traces](docs/reports/evaluation.md)
- [Edge-case audit and open risks](docs/reports/edge-case-audit.md)
- [Local demo outline](docs/guides/demo.md)

The app has clear local setup instructions; no deployment URL is supplied here. All implementation commits use Rishav Dewan's Git identity.
