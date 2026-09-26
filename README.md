# Plum OPD claims processor

An evidence-first local prototype for processing outpatient health-insurance claims. It accepts claim metadata and PDF/image documents, catches document problems before adjudication, extracts only the evidence required to continue, applies the supplied policy deterministically, and exposes a reviewer trace for every outcome.

This repository is a demonstrable MVP, not a production payment authority. The fixture suite proves policy-pipeline behavior. It does not prove representative OCR accuracy, medical interpretation, fraud detection, calibrated confidence, insurer payment reconciliation, or reduced human-review time.

## What is delivered

The implementation covers the assignment’s required workflow:

| Requirement | Implementation | Evidence |
| --- | --- | --- |
| Accept a claim | FastAPI form/API accepts member, category, treatment date, amount, dated pre-authorization evidence, and one or more PDFs/images. | `claims/web.py`, `tests/test_web.py` |
| Catch document problems early | A document gate checks type, readability, required documents, patient identity, dates, and bill consistency. It returns specific correction requests with `decision: null`. | `claims/documents.py`, `tests/test_documents.py` |
| Extract structured information | Selectable PDF text is parsed locally; images/scanned PDFs can use Sarvam Document AI. The bounded schema covers clinical, bill, lab, and pharmacy fields needed for review, while optional Gemini evidence review is opt-in. | `claims/documents.py`, `claims/ai_review.py` |
| Make a claim decision | Deterministic policy code produces `APPROVED`, `PARTIAL`, `REJECTED`, or `MANUAL_REVIEW`, with amount, reasons, confidence, ledger, and trace. | `claims/core.py`, `tests/test_core.py` |
| Explain every outcome | Persisted trace events identify stages, rule IDs, policy references, evidence, pass/fail status, degradation, and pricing arithmetic. | `claims/agent_pipeline.py`, reviewer UI |
| Handle failure gracefully | Invalid handoffs, provider failures, missing material evidence, duplicates, and ambiguity fail closed into correction or review rather than guessed payment. | `tests/test_agent_pipeline.py`, `tests/test_ai_review.py` |

The assignment brief is reproduced in [`docs/reference/assignment.md`](docs/reference/assignment.md). The supplied-artifact paths are mapped in [`docs/reference/submission-artifact-map.md`](docs/reference/submission-artifact-map.md). Supporting architecture, contracts, reports, runbook, and screenshots are indexed in [`docs/README.md`](docs/README.md).

## Quick start

Requirements: Python 3.12 or newer and `pip`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m tools.generate_samples
.venv/bin/python -m uvicorn claims.web:app --reload
```

Open <http://127.0.0.1:8000> for submission and <http://127.0.0.1:8000/ops> for the local operations worklist.

The app stores private local uploads and SQLite state below ignored `.data/`. The operations page is intentionally unauthenticated for this prototype; add authentication, authorization, retention, encryption, and audit controls before using real member documents.

### Provider configuration

- Selectable-text PDFs use local parsing and need no provider call.
- Images and scanned PDFs use Sarvam Document AI when `SARVAM_API_KEY` is configured.
- Gemini evidence review is off by default. It requires both `GEMINI_API_KEY` and `GEMINI_EVIDENCE_REVIEW_ENABLED=true`.
- Never place provider keys in commands, logs, screenshots, commits, or this README.

For an end-to-end synthetic upload, see [`docs/guides/local-runbook.md`](docs/guides/local-runbook.md). Use the generated synthetic files, not real health documents.

## Verification: expected results and final results

All checks below were run against the current checkout on 26 September 2026. The browser run used a fresh temporary `PLUM_DATA_DIR`, so previous local claim history could not influence the result.

### Automated quality gates

| Check | Expected result | Final result |
| --- | --- | --- |
| Full test suite | Exit 0; all tests pass | **109 passed**, 7 dependency deprecation warnings |
| Ruff | Exit 0; no lint findings | **All checks passed** |
| mypy | Exit 0; no type errors | **Success: no issues found in 10 source files** |
| compileall | Exit 0 | **Passed** |
| Fixture evaluation | All 12 supplied cases match expected behavior | **12/12 matched** |
| Real browser flow | Approval, correction, duplicate review, and no JS errors | **Passed** |

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy claims scripts tools
.venv/bin/python -m compileall -q claims scripts tools
PYTHONPATH=. .venv/bin/python -m scripts.evaluate
PYTHONPATH=. .venv/bin/python -m scripts.evaluate_documents
```

The fixture evaluation command writes [`docs/reports/evaluation.md`](docs/reports/evaluation.md) and [`docs/reports/evaluation-data.json`](docs/reports/evaluation-data.json). The real-byte intake command writes [`docs/reports/document-evaluation.md`](docs/reports/document-evaluation.md) and [`docs/reports/document-evaluation.json`](docs/reports/document-evaluation.json). The first report contains the full trace for each supplied structured case; the second is explicitly a safe-routing benchmark, not an OCR accuracy report.

### Fixture results

The expected and produced outcomes matched for all 12 supplied structured cases:

| Cases | Result |
| --- | --- |
| TC001–TC003 | `NEEDS_CORRECTION`, with `decision: null` |
| TC004 | `APPROVED`, ₹1,350 |
| TC005 | `REJECTED`, ₹0 |
| TC006 | `REJECTED`, ₹0 (line-level exclusion remains visible in the trace) |
| TC007–TC008 | `REJECTED`, ₹0 |
| TC009 | `MANUAL_REVIEW`, ₹0 |
| TC010–TC011 | `APPROVED`, ₹1,440 and ₹4,000 respectively |
| TC012 | `REJECTED`, ₹0 |

These fixtures contain structured metadata rather than actual PDF/image bytes. Therefore, 12/12 is a regression result for normalization, reconciliation, policy rules, money arithmetic, traces, and failure branches—not an OCR benchmark.

### Document-intake evaluation

The labelled synthetic document set uses actual generated PDF and image bytes, and covers a clean consultation, wrong document type, patient mismatch, amount conflict, multi-page bill, and blurred phone photo. It proves that the intake path accepts or safely routes those files; it does **not** claim handwriting, multilingual, stamp-overlap, or production OCR accuracy. The current generated report is [`docs/reports/document-evaluation.md`](docs/reports/document-evaluation.md).

### Browser results

The fresh-data browser check exercised the actual submission and reviewer screens with a controlled in-policy test timestamp; it does not backdate a live claim:

| Flow | Expected | Final result |
| --- | --- | --- |
| Clean consultation | `APPROVED`, payable ₹1,350, visible trace and ledger | **Passed** |
| Wrong documents repeated | `DOCUMENT_CORRECTION_REQUIRED`, `decision: null`, actionable hospital-bill request | **Passed** |
| Exact repeat of an already approved bill | `MANUAL_REVIEW`, reason `DUPLICATE_BILL`, amount pending review | **Passed** |
| Frontend runtime | No page errors | **Passed; zero browser errors** |

The clean browser scenario used selectable-text synthetic PDFs, so it exercised the real upload and review path without requiring a paid OCR call. A separate synthetic image path is documented in the runbook and may incur Sarvam usage.

## Architecture

### Design objective

The core design decision is to separate evidence extraction from policy authority. Documents are messy and benefit from specialized parsing or bounded model assistance. Eligibility, precedence, money arithmetic, state transitions, and final outcomes must be deterministic and reproducible. A model can propose a fact with provenance; it cannot approve a claim or calculate the payable amount.

```text
Claim + files
    │
    ▼
Intake + private local storage + SHA-256 hashes
    │
    ▼
Document inspection and required-document gate
    │                    └── correction request; decision = null
    ▼
Typed evidence extraction with source fields
    │
    ▼
Cross-document reconciliation: identity, dates, totals, line items
    │                    └── ambiguity or duplicate → MANUAL_REVIEW
    ▼
Deterministic policy evaluator reading data/policy_terms.json
    │
    ▼
Integer-paise ledger + precedence + confidence/degradation
    │
    ▼
Persisted decision, trace, reviewer UI, and operations worklist
```

### Component responsibilities

| Component | Owns | Does not own |
| --- | --- | --- |
| `claims.web` | API/UI, uploads, SQLite workflow, history lookup, persistence | Coverage decisions or model interpretation |
| `claims.documents` | File validation, local PDF parsing, Sarvam adapter, normalized document evidence | Policy eligibility or payable amount |
| `claims.fixtures` | Adapter from the supplied JSON cases into the same evidence shape | Claiming that OCR occurred |
| `claims.ai_review` | Opt-in, targeted evidence candidates, source quotes, page/field validation | Policy, member amount, payment, or unsupported facts |
| `claims.agent_pipeline` | Handoff schemas, provenance checks, fail-closed finalization, bounded trace artifacts | Acting as a general message bus or distributed agent runtime |
| `claims.core` | Policy rules, precedence, reconciliation, integer-paise pricing, reasons, ledger | OCR, external model calls, source-document mutation |
| Templates/static UI | Submission, evidence disclosure, ledger, correction, escalation, operations views | Replacing persisted trace or reviewer judgment |

### Why this architecture is suitable

1. **Correctness is placed at the payment boundary.** The most harmful errors are wrong eligibility and wrong money. Keeping those in deterministic code makes an outcome replayable from policy, normalized facts, and ledger inputs.
2. **AI is used where it has leverage.** OCR and semantic evidence recovery are the uncertain parts. The fast path for clear digital PDFs avoids unnecessary model calls; ambiguous material fields can be escalated with bounded context.
3. **Evidence is first-class.** Every accepted candidate must be tied to a file, page/quote where available, identity allowlist, date, amount, and arithmetic. This gives a reviewer something inspectable instead of a persuasive paragraph.
4. **Fail-closed behavior protects members and the insurer.** Missing documents become a specific correction request; conflicting or unsupported evidence becomes review. Abstention is not converted into approval.
5. **The workflow is small enough to operate locally.** FastAPI, SQLite, and an in-process sequence reduce deployment and coordination failure surfaces for a take-home MVP while preserving explicit seams for a queue, object storage, and Postgres later.
6. **History is auditable but honestly scoped.** Claim events and document hashes are persisted. Local approved/partial decisions provide a disclosed benefit-usage proxy; they are not remittance or payment records.

The “multi-agent” boundary is deliberately bounded: Sarvam and optional Gemini can help produce evidence candidates, while ordinary code owns routing and the deterministic evaluator owns the result. The repository does not claim six independent agents, a distributed queue, or autonomous policy negotiation. See [`docs/architecture/agent-pipeline.md`](docs/architecture/agent-pipeline.md) for implemented-vs-proposed boundaries.

### Money and decision precedence

Amounts are represented in integer paise at the policy boundary. The evaluator reconciles bill totals and line items, filters eligible lines, applies configured caps and sub-limits, applies network discount before co-pay, and emits each adjustment in the ledger. Policy failures and review signals have explicit precedence so a reviewer can see both the primary reason and the checks that were not evaluated.

`NOT_EVALUATED` is distinct from `PASS`. For example, a fixture with no submission timestamp cannot establish the 30-day deadline. Confidence is an evidence-quality score, not a calibrated probability and not an auto-pay threshold.

### Policy and evidence safety rules

- A required pre-authorization must contain an approval reference and issue date. The evaluator checks the configured validity window against treatment date; missing evidence routes to review and invalid evidence rejects the claim.
- High-value manual-review thresholds and generic-medicine requirements are read from `data/policy_terms.json`. A branded pharmacy item under a mandatory-generic policy is review-only until medical necessity is verified.
- If structured extraction fails after local parsing identified missing material fields, the workflow routes to `MANUAL_REVIEW` as system degradation. It does not tell the member that their file was unclear.
- Document-correction responses use the same `{code, message, file_name, required_type}` object shape as other result reasons, so UI and API consumers do not need state-specific string parsing.

## Cost model and cost arbitrage

### Observed and published inputs

The repository records provider call/page/token metrics in the result. The figures below are either published list prices or measured synthetic requests; they are not a production invoice.

| Path | Cost basis | Example |
| --- | --- | --- |
| Selectable PDF, local parsing | No OCR API call | ₹0 provider cost for extraction; local compute/storage still exist |
| Sarvam digitisation | Published ₹0.50/page | 3-page scan = **₹1.50** before retries, storage, compute, taxes, and review |
| Sarvam digitisation + schema extraction | Published ₹0.50 + ₹1/page | 3 pages through both = **₹4.50** before the same overheads |
| Gemini evidence review | One synthetic request measured 1,429 input + 333 output tokens | At documented introductory paid-tier rates of $0.75/M input and $3.75/M output, approximately **$0.0023**; pricing and document size can change |
| Human review | Not measured in this prototype | Measure reviewer minutes × loaded hourly cost |

Sources: [Sarvam API pricing](https://www.sarvam.ai/api-pricing) and [Google Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing). The exact Gemini run and limitations are recorded in [`docs/guides/local-runbook.md`](docs/guides/local-runbook.md).

### Where the arbitrage comes from

The economic advantage is conditional, not a claimed measured savings percentage:

```text
Always-OCR baseline: every page → paid OCR → full extraction → human review

Evidence-first path:
  clear PDF → local parse → deterministic decision
  scan → paid digitisation only when needed
  unresolved material fact → bounded evidence pass
  ambiguity/duplicate/conflict → targeted human review
```

This creates three sources of leverage:

- **Avoided provider calls:** local parsing handles selectable PDFs.
- **Avoided unnecessary extraction:** schema extraction and Gemini are conditional on missing or conflicting material evidence, not default steps.
- **Avoided unsafe automation:** high-risk uncertainty is routed to review, reducing the cost of silently wrong decisions. Reviewer-minute reduction is not measured yet.

The correct production metric is not API price per page:

```text
cost per correctly resolved claim
= provider cost + local compute/storage + retry/failure cost
  + reviewer minutes + correction/re-upload cost
  + expected cost of incorrect approval or rejection
```

For a fair comparison, benchmark this architecture against an always-OCR baseline on the same labelled document set and report field accuracy, wrong-member matches, arithmetic accuracy, abstention, reviewer minutes, latency, and total cost per correctly resolved claim. No such comparative production benchmark is present here, so a savings percentage would be manufactured and is intentionally omitted.

## Scale and operational path

The assignment states 75,000+ claims/year, or about 205/day on average; 10× is about 2,055/day. That average does not justify microservices in this prototype. At higher load or higher latency variance, evolve the seams in this order:

1. Move uploads to encrypted object storage and keep only content-addressed metadata in Postgres.
2. Put document inspection and provider calls behind a durable queue with idempotency keys, stage leases, retries, timeouts, and provider rate limits.
3. Parallelize independent file inspection while enforcing claim-wide page/token/currency budgets.
4. Preserve deterministic fan-in for reconciliation and policy evaluation; never let parallel model outputs vote on a payment result.
5. Add observability for p95 time to feedback, correction cycles, provider failure rate, cost per correctly resolved claim, reviewer minutes, and false approval/rejection rates.
6. Add retention, access control, encryption, vendor data-processing review, and immutable audit storage before real member data.

The current architecture is suitable for a narrow, explainable MVP with explicit migration points for production scale. It is not presented as production-ready infrastructure.

## Known limitations and open risks

- The 12 fixtures contain no actual images/PDFs; handwriting, stamps, multilingual extraction, and image-quality accuracy remain unmeasured.
- Policy data and expected fixture behavior conflict in places, including TC006 and TC010. The compatibility interpretation is recorded in the trace and reports; an insurer policy owner must resolve it before automatic payment.
- The prototype has no insurer remittance feed. Approved/partial history is only a local consumption proxy and does not model reversals or actual payment.
- Exact SHA-256 duplicate detection does not catch cropped, recompressed, or re-scanned copies. Similarity matching needs a review threshold and false-positive measurement.
- Confidence is heuristic and not calibrated.
- The intake UI does not yet collect every field supported by the evaluator, including independent patient selection, provider, pre-authorization evidence/status, and submission date.
- The local operations page has no authentication.
- Gemini and Sarvam live accuracy, cost distribution, and human-review reduction require a labelled held-out evaluation. A synthetic accepted Gemini candidate proves validation works, not general accuracy.
- No deployment URL or demo video is included in this checkout; the local runbook and browser evidence are the available delivery artifacts.

The detailed risk register is [`docs/reports/edge-case-audit.md`](docs/reports/edge-case-audit.md), and the implementation feedback audit is [`docs/reports/feedback-audit.md`](docs/reports/feedback-audit.md).

## Repository map

```text
claims/
  web.py              FastAPI app, SQLite workflow, reviewer and operations routes
  core.py             deterministic policy evaluator and money ledger
  documents.py        upload validation, PDF parsing, Sarvam adapter, evidence normalization
  ai_review.py        optional Gemini evidence resolver and validators
  agent_pipeline.py   evidence/decision handoff validation and fail-closed boundaries
  fixtures.py         structured fixture adapter
  templates/          submission, claim review, and operations pages
  static/             browser behavior and styles
data/policy_terms.json        versioned policy configuration
tests/                        unit, integration, handoff, and HTTP tests
scripts/evaluate.py           reproducible 12-case evaluation
scripts/browser_check.py      real browser approval/correction/duplicate flow
docs/                         architecture, contracts, reports, guides, research, screenshots
```

## Further reading

- [Architecture overview](docs/architecture/overview.md)
- [Bounded evidence-agent design](docs/architecture/agent-pipeline.md)
- [AI escalation logic](docs/architecture/ai-escalation.md)
- [Component contracts](docs/architecture/contracts.md)
- [Evaluation report with full traces](docs/reports/evaluation.md)
- [Local terminal/browser runbook](docs/guides/local-runbook.md)
- [Demo outline](docs/guides/demo.md)
