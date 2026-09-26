# Plum OPD claims processor

An evidence-first local prototype for processing outpatient (OPD) health-insurance claims. It accepts claim details and PDF or image documents, and it catches document problems before adjudication. It extracts only the evidence needed to continue, applies the supplied policy deterministically, and shows a reviewer trace for every outcome.

This is a working MVP, not a production payment authority. The supplied-case suite shows how the policy pipeline behaves. It does not measure OCR accuracy, medical interpretation, fraud detection, confidence calibration, insurer payment reconciliation, or any reduction in human-review time.

## What is delivered

| Requirement | Implementation | Evidence |
| --- | --- | --- |
| Accept a claim | A FastAPI form and API accept member, category, treatment date, amount, dated pre-authorization evidence, and one to six PDFs or images. | `claims/web.py`, `tests/test_web.py` |
| Catch document problems early | A document gate checks type, readability, required documents, patient identity, dates and bill arithmetic. It returns specific correction requests with `decision: null`. | `claims/documents.py`, `tests/test_documents.py` |
| Extract structured information | Selectable PDF text is parsed locally. Images and scanned PDFs use Sarvam Document AI. Gemini evidence review is optional and opt-in. | `claims/documents.py`, `claims/ai_review.py` |
| Make a claim decision | Deterministic policy code produces `APPROVED`, `PARTIAL`, `REJECTED` or `MANUAL_REVIEW`, with amount, reasons, confidence, ledger and trace. | `claims/core.py`, `claims/policy.py`, `tests/test_core.py`, `tests/test_policy.py` |
| Explain every outcome | Persisted trace events record the policy fingerprint, stages, rule IDs, policy references, evidence, pass/fail status, degradation, and pricing arithmetic. | `claims/agent_pipeline.py`, reviewer UI |
| Handle failure gracefully | Invalid handoffs, provider failures, missing material evidence, duplicates, ambiguity and an invalid policy fail closed into correction or review. None of them produces a guessed payment. | `tests/test_agent_pipeline.py`, `tests/test_ai_review.py`, `tests/test_document_evaluation.py` |

The assignment brief is in [`docs/reference/assignment.md`](docs/reference/assignment.md). The supplied artifacts, and how they are kept unmodified, are described in [`docs/reference/submission-artifact-map.md`](docs/reference/submission-artifact-map.md). Everything else is indexed in [`docs/README.md`](docs/README.md).

## Quick start

Requirements: Python 3.12 or newer, and `pip`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env                      # optional: provider keys only
.venv/bin/python -m tools.generate_samples   # synthetic PDFs/PNG into .data/samples
PLUM_ENV=development PLUM_DEMO_CLOCK=2024-11-05 .venv/bin/python -m uvicorn claims.web:app --reload
```

Open <http://127.0.0.1:8000> to submit a claim and <http://127.0.0.1:8000/ops> for the local operations worklist. Submit member `EMP001`, category `CONSULTATION`, treatment date `2024-11-01`, amount `1500`, with `synthetic_prescription.pdf` and `synthetic_hospital_bill.pdf`. The result is `APPROVED` for ₹1,350: ₹1,500 less the 10% consultation co-pay. The full walkthrough, including a terminal-only version, is in [`docs/guides/local-runbook.md`](docs/guides/local-runbook.md).

**Why the two environment variables.** The supplied policy runs from 2024-04-01 to 2025-03-31. By default the app records each claim's submission date with the real clock, so a November 2024 treatment submitted today is outside the 30-day submission window and is rejected. That is correct behavior. `PLUM_DEMO_CLOCK` replays the 2024 policy period, and it is honored only when `PLUM_ENV` is `development` or `test`. The app refuses to start if a demo clock is set in any other environment. When the demo clock is active, it is shown in a UI banner and appears as the first trace entry (`clock/demo_clock/OVERRIDDEN`) of every affected decision. For anything other than replaying the 2024 sample policy, start the app without the two variables.

The app also refuses to start if `data/policy_terms.json` fails schema validation (`POLICY_CONFIGURATION_INVALID`). If the policy file becomes invalid while the app is running, the intake page and submit endpoint return HTTP 503 with that code, and a claim being processed moves to the retryable `PROCESSING_FAILED` state; no decision is made against an invalid policy.

The app stores private uploads and SQLite state under the git-ignored `.data/` folder (override it with `PLUM_DATA_DIR`). The operations page has no authentication in this prototype. Add authentication, authorization, retention, encryption and audit controls before using real member documents.

### Providers

- Selectable-text PDFs are parsed locally, with no provider call.
- Images and scanned PDFs need Sarvam Document AI (`SARVAM_API_KEY`). Without it they fail closed to correction or operator review; they are never guessed.
- Gemini evidence review is off by default. It needs both `GEMINI_API_KEY` and `GEMINI_EVIDENCE_REVIEW_ENABLED=true`.
- Never put provider keys in commands, logs, screenshots, commits or this README.

## Verification

One command runs every check and regenerates every report:

```bash
make verify        # or: PYTHONPATH=. .venv/bin/python -m scripts.verify_submission
```

It runs these steps:

1. Checks the sha256 of the four supplied artifacts, and compares them with the evaluator's initial commit.
2. Runs ruff, mypy, compileall and pytest.
3. Scores the 12 supplied cases (`scripts.evaluate`) and the offline document suites (`scripts.evaluate_documents`).
4. Regenerates `docs/reports/*`.
5. Writes [`docs/reports/verification-summary.md`](docs/reports/verification-summary.md) and `.json`, with the git HEAD, timestamps and every count.
6. Rewrites the table below, and fails if a count or score written by hand anywhere else in the docs disagrees with the summary.

CI runs the same command without provider keys.

<!-- verification:start -->
_Generated by `make verify` at 2026-09-26T17:15:16+00:00 on `db99625`. Source: [`docs/reports/verification-summary.md`](docs/reports/verification-summary.md)._

| Check | Result |
| --- | --- |
| Supplied artifacts (sha256) | 4/4 unmodified |
| pytest | 260 passed, 0 failed, 103 subtests passed |
| ruff / mypy / compileall | pass / pass / pass |
| Supplied cases (`scripts.evaluate`, unmodified `test_cases.json`) | **12/12** |
| Offline document suites (routing / fail-closed, not OCR) | **28/28**: `intake_routing` 6/6, `image_fail_closed` 14/14, `provider_failure_injection` 8/8 |
| Live OCR accuracy | **NOT_RUN**: verification is offline by design (provider keys are stripped); SARVAM_API_KEY is not configured |
<!-- verification:end -->

### Supplied cases

`scripts.evaluate` runs the evaluator's `tests/fixtures/test_cases.json` **unmodified**. That file is the sole source of truth for expected outcomes, and the policy is also the untouched supplied `data/policy_terms.json`. Each case is checked on decision, approved amount, rejection codes and confidence. It is also checked on the concrete behavior behind the case's `system_must` prose: line-level reasons, the resubmission instruction, discount applied before co-pay, degradation visible in the trace, and so on. The per-case results and complete traces are in [`docs/reports/evaluation.md`](docs/reports/evaluation.md), and the per-case table is in the verification summary.

The supplied policy and the expected outcomes disagree in places. For example, the ₹5,000 global per-claim limit conflicts with the expected ₹8,000 dental payment, and a ₹2,000 consultation sub-limit read as a cap on the whole claim conflicts with the expected ₹3,240 consultation payment. Neither file was edited to make the cases pass. Instead, one general rule per conflict is written in `claims/policy.py`, every application of it is recorded in the canonical policy's audit trail and in each decision's trace, and nothing branches on a case ID. The rules, the contradictions each resolves, and what a policy owner should confirm are in [`docs/design/policy-interpretation.md`](docs/design/policy-interpretation.md). The generated list of interpretations is [`docs/reports/policy-audit.md`](docs/reports/policy-audit.md).

The supplied cases contain structured metadata, not document bytes. Passing them tests normalization, reconciliation, policy rules, money arithmetic, traces and failure branches. It is not an OCR benchmark.

### Documents

- **Offline suites** (`scripts.evaluate_documents`, part of `make verify`). They use real generated PDF and image bytes and a labelled 14-document dirty corpus (`tests/fixtures/documents/`: handwriting-style fill-ins, stamps, Hindi and English, skewed phone photos, cropped and struck-through bills). They test **intake routing and fail-closed behavior only**: the routing suite checks safe routing of selectable-text files; with OCR unavailable, every image is held for correction or review; and a mandatory provider failure always blocks adjudication, while an optional Gemini failure never clears an issue. They make no provider calls and measure no OCR accuracy.
- **Live OCR accuracy is unmeasured.** No provider keys were available when this was verified, so the live benchmark reports `NOT_RUN`. To measure classification and field accuracy, abstention, unsafe confident errors, calls and latency on the corpus, provide `SARVAM_API_KEY` and run:

  ```bash
  PYTHONPATH=. .venv/bin/python -m scripts.evaluate_documents --providers live [--gemini]
  ```

  This writes `docs/reports/document-ocr-evaluation.*`. The design is in [`docs/design/extraction-scope.md`](docs/design/extraction-scope.md).

### Browser flow

`scripts/browser_check.py` drives the real submission and reviewer screens with Playwright (approval, correction, duplicate review, and no page errors). It needs `playwright install chromium` and a server started with the demo clock. It is not part of `make verify` and was not re-run for the current verification. The ₹1,350 approval path it covers was reproduced over HTTP against the demo-clock server. The screenshots in `docs/screenshots/` and the walkthrough video `VIdeo Plum.mp4` predate the source-of-truth remediation. Screens that show TC010-style consultation amounts reflect the earlier interpretation.

## Architecture

### Design objective

The central design decision is to keep evidence extraction separate from policy authority. Documents are messy, and parsing them benefits from specialized readers and bounded model help. Eligibility, precedence, money arithmetic, state transitions and final outcomes must be deterministic and reproducible. A model can propose a fact with provenance. It cannot approve a claim or calculate the payable amount.

The system is **agentic where ambiguity benefits from specialized reasoning, and deterministic where money and policy require reproducibility.** Sarvam (OCR) and the optional Gemini evidence resolver are bounded specialists. Each has a single job, a typed handoff, and validators that reject ungrounded output. Ordinary code owns routing, and `claims.core` owns every decision. This is a bounded specialist pipeline, not a set of autonomous agents: there is no agent-to-agent negotiation, no distributed queue, and no model vote on payment. [`docs/architecture/agent-pipeline.md`](docs/architecture/agent-pipeline.md) separates what is implemented from what is proposed.

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
Typed evidence extraction with source fields (local PDF text → Sarvam → optional Gemini)
    │
    ▼
Cross-document reconciliation: identity, dates, totals, line items
    │                    └── ambiguity or duplicate → MANUAL_REVIEW
    ▼
Policy: raw JSON → strict schema → normalizer → canonical config (+ audit, sha256)
    │
    ▼
Deterministic evaluator: precedence, integer-paise ledger, confidence rubric
    │
    ▼
Persisted decision, trace, reviewer UI, and operations worklist
```

### Component responsibilities

| Component | Owns | Does not own |
| --- | --- | --- |
| `claims.web` | API/UI, uploads, SQLite workflow, history lookup, persistence, startup checks (clock, policy) | Coverage decisions or model interpretation |
| `claims.documents` | File validation, local PDF parsing, Sarvam adapter, normalized document evidence | Policy eligibility or payable amount |
| `claims.fixtures` | Adapter from the supplied JSON cases into the same evidence shape | Claiming that OCR occurred |
| `claims.ai_review` | Opt-in, targeted evidence candidates, source quotes, page/field validation | Policy, member amount, payment, or unsupported facts |
| `claims.agent_pipeline` | Handoff schemas, provenance checks, fail-closed finalization, bounded trace artifacts | Acting as a general message bus or distributed agent runtime |
| `claims.policy` | Strict policy schema, normalizer, interpretation tables, audit trail, fingerprints | Claim decisions |
| `claims.core` | Policy rules on the canonical config, precedence, reconciliation, integer-paise pricing, reasons, ledger, confidence | OCR, external model calls, source-document mutation |
| Templates/static UI | Submission, evidence disclosure, ledger, correction, escalation, operations views | Replacing persisted trace or reviewer judgment |

### Why this architecture fits

1. **Correctness sits at the payment boundary.** The most harmful errors are wrong eligibility and wrong money. Keeping both in deterministic code means an outcome can be replayed from the policy fingerprint, the normalized facts and the ledger.
2. **AI is used where it adds most.** OCR and semantic evidence recovery are the uncertain parts. Clear digital PDFs skip model calls entirely, and only ambiguous material fields are escalated, with bounded context.
3. **Evidence is first-class.** Every accepted candidate must be tied to a file, a page or quote where available, the identity allowlist, a date, an amount and consistent arithmetic.
4. **Failures close safely.** Missing documents become a specific correction request, and conflicting or unsupported evidence goes to review. An abstention is never turned into an approval.
5. **The system is small enough to run locally.** FastAPI, SQLite and an in-process sequence keep the failure surface small, and there are clear seams for a queue, object storage and Postgres later.
6. **Policy interpretation is auditable.** The engine never reads the raw policy. It reads a canonical config whose every derivation and conflict resolution is listed, with the source and canonical sha256 in every trace.

### Money and decision precedence

Amounts are integer paise at the policy boundary. The evaluator reconciles bill totals and line items, removes excluded and non-covered lines, and sends unrecognised lines to review. It then tests the per-claim ceiling on the eligible amount; a pre-authorization rule takes precedence over the ceiling when one governs the treatment. It applies network discount before co-pay, and then caps the net payable by the category sub-limit, the remaining annual OPD limit, the remaining sum insured and the remaining family floater, in that order. Each adjustment is recorded in the ledger. Reason precedence is explicit: the primary reason is chosen deterministically, and the trace still shows every check that ran or was `NOT_EVALUATED`.

`NOT_EVALUATED` is distinct from `PASS`. The supplied cases carry no submission date, so the 30-day deadline is `NOT_EVALUATED` for them. Web intake records a server-side submission date and checks the deadline against it. Aggregate limits (annual OPD, sum insured, family floater, sessions, earlier use of a category sub-limit) are applied when utilisation is supplied; when it is not, they are `NOT_EVALUATED`, disclosed as an advisory reason, and deducted from confidence. The category sub-limit is always applied to the claim's own benefit, with or without history.

### Confidence

The confidence score is a **heuristic evidence-completeness rubric, not a calibrated probability**, and it is not an auto-pay threshold. It starts at 0.96 and deducts fixed amounts for unknowns that matter to the outcome reached, such as an unverified patient name, annual usage not evaluated, a member-supplied pre-authorization record, or a failure of the risk-signal component. Every factor is listed in the trace. Calibrating it needs labelled outcomes that this prototype does not have. See [policy interpretation](docs/design/policy-interpretation.md#confidence-rubric).

### Policy and evidence safety rules

- Pre-authorization. A dated approval record (reference and issue date) is checked against the policy's validity window. No record and no claim of approval rejects the claim with `PRE_AUTH_MISSING` and resubmission instructions. A claimed approval without a dated record, or a form that contradicts a record, goes to review. An invalid record rejects the claim. A valid record is still member-supplied, so a payable outcome carries `PRE_AUTH_NOT_VERIFIED_WITH_INSURER`. Pre-auth rules match the claimed services (tests, bill lines, treatment), never the diagnosis or a negated mention.
- No payment on unestablished evidence. A bill with no total and no lines (`BILL_AMOUNT_UNVERIFIED`), a partly readable required document (`DOCUMENT_QUALITY_INSUFFICIENT`), a bill line that matches neither the covered list nor an exclusion (`LINE_ITEM_UNRESOLVED`), an explicit identity failure from the document layer (`PATIENT_IDENTITY_UNVERIFIED`) and invalid input values (`MALFORMED_EVIDENCE`) all go to review.
- High-value review thresholds, same-day and monthly frequency signals, and generic-medicine requirements come from the policy and are always checked. A default risk-signal component adds trailing 30-day value and repeat-billing signals; if it fails, the decision still rests on the mandatory checks, and it is marked for post-decision review. A branded pharmacy line under a mandatory-generic policy goes to review.
- If extraction fails after local parsing found material fields missing, the claim goes to `MANUAL_REVIEW` as a system degradation. The member is not told their file was unclear.
- Document-correction responses use the same `{code, message, file_name, required_type}` shape as other reasons.

## Cost model and cost arbitrage

### Observed and published inputs

The repository records provider calls, pages and tokens in each result. The figures below are published list prices or one measured synthetic request, not a production invoice.

| Path | Cost basis | Example |
| --- | --- | --- |
| Selectable PDF, local parsing | No OCR API call | ₹0 provider cost for extraction; local compute and storage still apply |
| Sarvam digitisation | Published ₹0.50/page | 3-page scan = **₹1.50** before retries, storage, compute, taxes and review |
| Sarvam digitisation + schema extraction | Published ₹0.50 + ₹1/page | 3 pages through both = **₹4.50** before the same overheads |
| Gemini evidence review | One synthetic request measured 1,429 input + 333 output tokens | At the documented introductory paid-tier rates of $0.75/M input and $3.75/M output, about **$0.0023**. Pricing and document size can change this. |
| Human review | Not measured in this prototype | Measure reviewer minutes × loaded hourly cost |

Sources: [Sarvam API pricing](https://www.sarvam.ai/api-pricing) and [Google Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing). The Gemini measurement is described in [`docs/guides/local-runbook.md`](docs/guides/local-runbook.md).

### Where the savings come from

The savings are conditional; no measured savings percentage is claimed:

```text
Always-OCR baseline: every page → paid OCR → full extraction → human review

Evidence-first path:
  clear PDF → local parse → deterministic decision
  scan → paid digitisation only when needed
  unresolved material fact → bounded evidence pass
  ambiguity/duplicate/conflict → targeted human review
```

The right production metric is not API price per page:

```text
cost per correctly resolved claim
= provider cost + local compute/storage + retry/failure cost
  + reviewer minutes + correction/re-upload cost
  + expected cost of incorrect approval or rejection
```

A fair comparison would run this architecture and an always-OCR baseline on the same labelled document set. It would report field accuracy, wrong-member matches, arithmetic accuracy, abstention, reviewer minutes, latency, and total cost per correctly resolved claim. No such benchmark exists here, so no savings percentage is given.

## Scale and operational path

The assignment states 75,000+ claims a year, about 205 a day on average; 10× is about 2,055 a day. That volume does not justify microservices. Under higher load or more variable latency, extend the system in this order:

1. Move uploads to encrypted object storage and keep only content-addressed metadata in Postgres.
2. Put document inspection and provider calls behind a durable queue with idempotency keys, stage leases, retries, timeouts, and provider rate limits.
3. Inspect independent files in parallel while enforcing claim-wide page, token and cost budgets.
4. Keep reconciliation and policy evaluation as a deterministic fan-in. Never let parallel model outputs vote on a payment result.
5. Monitor p95 time to feedback, correction cycles, provider failure rate, cost per correctly resolved claim, reviewer minutes, and false approval and rejection rates.
6. Before handling real member data, add retention, access control, encryption, a vendor data-processing review, and immutable audit storage.

## Known limitations and open risks

- **Live OCR accuracy is unmeasured.** No provider keys were available, so handwriting, stamps, multilingual text and phone-photo quality have been tested only for safe routing, not for extraction accuracy.
- **No altered-document or tamper detection.** A bill with a struck-through amount and a handwritten correction may pass if OCR reads it cleanly and the arithmetic agrees. The corpus includes such a bill, labelled "should abstain", so the live benchmark will show whether this gap matters.
- **The category sub-limit is an interpretation.** The engine reads a `sub_limit` as an annual, per-member cap on the net benefit for the category's own services; for consultation that means consultation-fee lines only, because the expected TC010 payment rules out a cap on the whole claim. Earlier usage comes from local decisions, and without it only this claim is capped. It needs a policy owner's decision.
- **Other interpretations need confirmation** before real payments: the per-claim ceiling rule, pre-authorization taking precedence over the ceiling and the sub-limit, the benefit order, the PET pre-auth threshold, the covered-item vocabulary, the dental report, and dependents missing from the roster. See [policy interpretation](docs/design/policy-interpretation.md).
- **Confidence is a heuristic rubric, not a calibrated probability.**
- **Multi-agent is a bounded specialist pipeline.** Two model-backed specialists sit behind validators, and a deterministic evaluator makes every decision. The multi-agent runtime described in `agent-pipeline.md` is a proposal and is not implemented.
- **No insurer remittance feed.** Annual usage, category sub-limit usage, sessions and duplicate history come from local approved or partial decisions. That stands in for payment data but does not model reversals or actual payment.
- **Pre-authorization and identity are not verified with the insurer.** Approval records are member-uploaded, and the web intake does not yet pass a document-layer identity verdict (`identity_verification`) to the engine.
- **Duplicate detection is limited.** Logical bill fingerprints catch transformed copies only when bill number, provider, amount and treatment date are all extracted. Cropped or re-scanned copies can still get through.
- **The local operations page has no authentication.** SQLite and in-process jobs are suitable for a local prototype, not for a concurrent production claims ledger.

## Repository map

```text
claims/
  web.py              FastAPI app, SQLite workflow, reviewer and operations routes
  policy.py           strict policy schema, normalizer, interpretation tables, audit trail
  core.py             deterministic policy evaluator and money ledger
  documents.py        upload validation, PDF parsing, Sarvam adapter, evidence normalization
  ai_review.py        optional Gemini evidence resolver and validators
  agent_pipeline.py   evidence/decision handoff validation and fail-closed boundaries
  fixtures.py         structured-case adapter
  templates/, static/ submission, claim review, and operations pages
data/policy_terms.json          supplied policy (unmodified, read-only)
tests/fixtures/test_cases.json  supplied cases (unmodified, read-only; source of truth)
tests/fixtures/documents/       labelled synthetic dirty-document corpus
tests/                          unit, integration, handoff, HTTP and artifact-integrity tests
scripts/verify_submission.py    one-command verification and report generation (make verify)
scripts/evaluate.py             supplied-case evaluation
scripts/evaluate_documents.py   offline document suites and live OCR benchmark
scripts/browser_check.py        real browser approval/correction/duplicate flow
tools/                          synthetic sample and corpus generators
docs/                           architecture, design, contracts, reports, guides, research
```

## Further reading

- [Policy interpretation and contradictions](docs/design/policy-interpretation.md)
- [Verification summary](docs/reports/verification-summary.md)
- [Supplied-case report with full traces](docs/reports/evaluation.md)
- [Architecture overview](docs/architecture/overview.md)
- [Bounded evidence-agent design](docs/architecture/agent-pipeline.md)
- [AI escalation logic](docs/architecture/ai-escalation.md)
- [Component contracts](docs/architecture/contracts.md)
- [Extraction scope and document benchmarks](docs/design/extraction-scope.md)
- [Local runbook](docs/guides/local-runbook.md) and [demo outline](docs/guides/demo.md)
