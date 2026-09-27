# Component contracts

All amounts in external claim inputs and `approved_amount` are INR rupees. Internal ledger and `approved_amount_paise` are integer paise. Dates supplied by clients use `YYYY-MM-DD`. Missing values remain missing; components must not substitute a plausible patient, date, total or authorization.

## Specialist handoffs (`claims.agent_pipeline`)

`resolve_document_handoff(inspection, files_by_id, ocr_text_by_file_id, claim_category, member_name, allowed_patient_names, policy, resolver) -> dict` accepts the local document inspection plus ephemeral source bytes/text and a Gemini-compatible resolver. The resolver's version 1 message must contain `schema_version: 1`, `producer: "gemini_evidence"`, `task: "resolve_document_facts"`, unique `source_file_ids` from this claim, `status`, `candidates`, `trace`, and `metrics`. The trace stage and status must match the message; a validated candidate must refer to exactly one listed source file. The handoff returns `{documents, issues, metrics, trace, status}`; status is `NOT_NEEDED`, `ABSTAINED`, or `CANDIDATES_VALIDATED`. Accepted candidates are applied only when candidate fields are allowlisted, nested evidence has valid pages and source quotes, patient names belong to the roster, and bill line items/total remain consistent. The upstream resolver also validates each source quote, amount, and date before this handoff. The document gate is run again after candidate application. Invalid envelopes or resolver exceptions become `ABSTAINED`, preserve the original documents/issues, and record a redacted reason. OCR text and file bytes are absent from the return value.

`adjudicate_handoff(payload, policy, evaluator) -> dict` invokes the deterministic evaluator and accepts only decisions in `APPROVED | PARTIAL | REJECTED | MANUAL_REVIEW | null`, matching states (`DECIDED` for payable/rejected outcomes, `MANUAL_REVIEW` for review), exact integer-paise and rupee amounts, amounts within the submitted claim, a 0–1 evidence-quality score, and complete structured reasons, trace entries, correction requests and ledger rows. Payable outcomes require a nonempty ledger whose eligible line items and adjustments sum exactly to the approved amount; excluded line items are shown for explanation and contribute zero. A `null` decision requires a correction state, null amounts and actionable `{code,message}` correction requests. Invalid output or an evaluator exception returns `MANUAL_REVIEW`, zero approved amount and a `decision_validation` trace with no private error message. This is a final sanity check, not a substitute for insurer clarification of conflicting policy terms.

`document_evidence_trace(documents) -> dict` returns one `document_evidence` step with each document's file ID, type, quality, extraction source, confidence, selected adjudication fields and up to 30 bounded source snippets. It excludes full OCR text and all fields outside its explicit allowlist. Live decided claims persist this step before Gemini and policy events.

## HTTP intake and workflow (`claims.web`)

| Operation | Input | Output | Errors |
| --- | --- | --- | --- |
| `POST /api/claims` | Multipart `member_id`, `claim_category`, `treatment_date`, `claimed_amount`, optional `pre_authorization_obtained`, `pre_authorization_issued_date`, `pre_authorization_reference`, and 1–6 PDF/JPEG/PNG/WebP `files` | HTTP 202 `{id,state:"QUEUED",url}`; files saved under private local storage; claim and event rows committed | HTTP 422 for invalid category/member/date/money/media, empty file, per-file 10 MB or total 30 MB excess; HTTP 503 `{detail:{code:"POLICY_CONFIGURATION_INVALID"}}` if the policy file has become invalid; storage/database failures surface as server errors |
| `GET /api/claims/{id}` | Claim ID | Stored claim request, state, result, file metadata and ordered events | 404 unknown ID |
| `GET /api/claims` | Optional `limit` 1–100 plus reviewer authentication | Recent claim summaries | 401 invalid/missing reviewer credentials; 422 invalid limit |
| `POST /api/claims/{id}/retry` | Failed claim ID | HTTP 202 queued retry | 404 unknown ID; 409 unless state is `PROCESSING_FAILED` |
| `GET /`, `GET /claims/{id}` | Browser navigation | Submission or reviewer HTML | 404 unknown claim ID; `GET /` returns 503 `POLICY_CONFIGURATION_INVALID` if the policy file has become invalid |
| `GET /ops` | Browser navigation plus HTTP Basic or reviewer headers | Operations worklist HTML; client reads the list and full claim API for escalation, evidence, model usage, and trace | 401 invalid/missing reviewer credentials |
| `POST /api/claims/{id}/review-decision` | Authenticated reviewer identity, decision, amount, `reason_code`, `reason_text`, `evidence_summary` | Final decision, atomic benefit reservation for payable outcomes, immutable before/after reviewer action | 401 invalid credentials; 409 wrong state or insufficient balance; 422 malformed disposition |
| `POST /api/claims/{id}/settlement` | Authenticated reviewer identity, `PAID` or `RELEASED`, reason code/text | Updated reservation plus append-only settlement event | 401 invalid credentials; 409 absent/invalid transition; 422 malformed update |

**Startup.** The app refuses to start (`RuntimeError`) when `data/policy_terms.json` fails validation (message contains `POLICY_CONFIGURATION_INVALID`), when `PLUM_DEMO_CLOCK` is set while `PLUM_ENV` is not `development`/`test` or holds an unparseable value, or when a non-development environment lacks a `PLUM_REVIEW_TOKEN` of at least 16 characters. Intake stamps `submission_date` from the real clock; the demo clock replaces it only in development/test and adds a `clock/demo_clock/OVERRIDDEN` trace step plus a `DEMO_CLOCK` event. Record timestamps always use the real clock. The intake snapshot stores `policy_canonical_sha256`, the verified canonical fingerprint from `claims.policy.policy_fingerprint`; if it differs at processing time the claim routes to `MANUAL_REVIEW` (`POLICY_CHANGED`).

`process_claim(id)` changes `QUEUED → PROCESSING → DOCUMENT_CORRECTION_REQUIRED | MANUAL_REVIEW | DECIDED | PROCESSING_FAILED`. It records immutable claim events and resumes `QUEUED`/`PROCESSING` records on app startup. FastAPI background tasks execute work in the app process; this is a durable *record* with recovery, not a separate managed queue. Provider exceptions are stored by exception type only to avoid putting document text in error messages. Before adjudication, exact bill-file hashes and complete logical fingerprints are checked against paid, reserved, in-flight, and under-review claims; a match routes to manual review. For a payable result, a SQLite `BEGIN IMMEDIATE` transaction encloses history/availability reads, deterministic evaluation, a claim-unique benefit reservation, and decision persistence. `RESERVED` and `PAID` rows consume benefit; `RELEASED` rows do not. This prevents concurrent local workers from spending one balance twice. History items still carry `claim_id`, `date`, `amount` and `provider` for frequency and risk signals. Production multi-host deployment should use Postgres row locks or compare-and-set plus an outbox.

## Document adapter (`claims.documents`)

```python
process_uploads(
    files: list[dict],
    claim_category: str,
    member_name: str,
    policy: dict,
    provider: DocumentProvider | None = None,
) -> dict
```

Each file has `file_name`, byte `data`, and optional `content_type`. The adapter checks magic bytes, size, image resolution/contrast, PDF page count, duplicate hashes, actual document type, required-document matrix, and patient names. Patient identity is classified as selected member, another covered member, unknown patient, or unavailable; only the selected member satisfies the filing contract. Digital PDF text is local. For images and scans, the default provider uses `SARVAM_API_KEY` from the environment. It calls digitisation for OCR and invokes schema extraction only if accepted text is missing a material field. Structured extraction also carries alteration, crossed-out/handwritten correction, and ORIGINAL/DUPLICATE stamp signals; positive material signals route to review rather than automatic fraud rejection.

Return shape:

```json
{
  "documents": [
    {
      "file_id": "UPLOAD-1",
      "file_name": "bill.pdf",
      "sha256": "...",
      "actual_type": "HOSPITAL_BILL",
      "quality": "GOOD",
      "patient_name_on_doc": "Rajesh Kumar",
      "content": {"patient_name": "Rajesh Kumar", "total": 1500, "line_items": []},
      "evidence": [{"field": "total", "source": "pdf_text", "snippet": "Total Amount: 1500.00", "confidence": 0.9}],
      "extraction_source": "pdf_text",
      "confidence": 0.9,
      "warnings": [],
      "pages": 1
    }
  ],
  "issues": [],
  "metrics": {
    "files": 1,
    "pages": 1,
    "provider_calls": 0,
    "provider_failures": 0,
    "sarvam_digitise_calls": 0,
    "sarvam_digitise_pages": 0,
    "sarvam_extract_calls": 0,
    "sarvam_extract_pages": 0
  }
}
```

The `issues` list contains `{code,file_name,message,required_type?}`. Nonempty issues stop policy evaluation. Examples: `MISSING_DOCUMENT`, `UNREADABLE_DOCUMENT`, `PATIENT_MISMATCH`, `AMOUNT_UNVERIFIED`, `EXTRACTION_UNAVAILABLE`. The message names the file and corrective action where the member can act. Provider outages name operator review/retry. Normal malformed documents and provider failures return issues and metrics rather than raise; caller misuse (wrong argument types) may raise `TypeError`.

### Provider boundary

```python
class DocumentProvider(Protocol):
    def digitise(self, data: bytes, mime_type: str) -> str: ...
    def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict: ...
```

`SarvamDocumentProvider` starts an asynchronous job, polls until completion with a 90-second deadline, and downloads a bounded result. Both PDF and ZIP inputs are capped at ten pages by the provider contract. A timeout, invalid result or failed job is caught by the adapter, counted in `provider_failures`, and surfaced as an issue. The adapter does not return a made-up field to keep the pipeline moving.

## Live outcome benchmark (`tools.live_ocr_outcome_benchmark`)

`generate` writes seven synthetic, image-only PDFs plus source-page PNGs and a manifest. `run` requires a configured running application, `PLUM_REVIEW_TOKEN`, and the server-side Sarvam key. It fills the real form, clicks submit, polls `GET /api/claims/{id}`, fails on unexpected state/decision, failed critical-field checks, provider failures, or browser page errors, and captures each filled form and outcome plus the authenticated review queue. `render` rebuilds the Markdown summary from the retained machine-readable result without making provider calls.

The benchmark expects four routes: approved, safe document correction with no invented total, deterministic rejection for an excluded cosmetic service, and manual review for a duplicate stamp. Its report is a bounded synthetic end-to-end regression. It is not the labelled dirty-corpus accuracy report and cannot be used as a production precision, recall, handwriting, multilingual, or calibration claim.

## Fixture adapter (`claims.fixtures`)

`normalize_fixture(case: dict) -> dict` copies `case.input` and maps every supplied document's `actual_type`, `quality`, `content`, and `patient_name_on_doc` into the same normalized document shape accepted by the evaluator. Every mapped document carries `source="fixture_metadata"`; the source is recorded as provenance in the trace and never changes a policy rule. The function drops `case_id`, `expected`, and the harness-only `simulate_component_failure`, so no production rule can branch on a test ID. `load_cases(path)` reads the supplied cases unmodified; `load_policy(path)` delegates to `claims.policy.load_policy`.

## Policy loader (`claims.policy`)

`load_policy(path) -> dict` reads the raw bytes, records their sha256, validates them against the strict `RawPolicy` schema (unknown keys forbidden; money, percent and count fields required and strictly typed; cross-references checked), and normalizes them into the canonical config (`schema_version: plum.canonical_policy.v1`, money in paise) with `source`, an `audit` list of every derivation, interpretation, conflict resolution, reference repair, informational field and defaulted optional field, and `canonical_sha256` (sha256 of every canonical key except itself, so it covers the audit). Any failure raises `PolicyConfigurationError` (`code = "POLICY_CONFIGURATION_INVALID"`); nothing defaults silently. `ensure_canonical(policy)` normalizes a raw dict passed directly to the evaluator, and for a dict that declares a `schema_version` it recomputes the fingerprint and raises on a mismatch or unknown schema. `policy_fingerprint(policy)` returns the verified canonical sha256; `canonical_fingerprint` and `audit_fingerprint` compute the two hashes. The rules are explained in [policy interpretation](../design/policy-interpretation.md).

## Claim evaluator (`claims.core`)

```python
evaluate_claim(payload: dict, policy: dict, *, optional_risk_enricher=risk_signal_enrichment) -> dict
```

`policy` is the canonical config (a raw dict is normalized first; an invalid one returns `MANUAL_REVIEW` with `POLICY_CONFIGURATION_INVALID` and confidence 0.0). Every result's trace starts with a `configuration/policy_source` step carrying the policy ID, source and canonical sha256, the audit sha256, the conflict-resolution IDs in force and the full audit list. Input requires member and policy IDs, claim category, treatment date, claimed amount and normalized documents. It may include `submission_date`, `currency` (INR only), `fraud_score` (0–1), year-to-date reserved/paid amount, sum-insured/family-floater utilisation, category sub-limit usage (`category_sub_limit_used`, else `category_ytd_claims_amount`), `claims_history` (a list of `{claim_id, date, amount, provider}`), `prior_sessions`, `identity_verification` (`VERIFIED`, `NOT_AVAILABLE`, `UNVERIFIED`, `FAILED`), hospital name and pre-authorization evidence. An invalid value returns `MANUAL_REVIEW` with `MALFORMED_EVIDENCE` and the field name in the trace. `optional_risk_enricher` defaults to the production risk-signal component; tests and the evaluator harness replace it only to inject a failure (TC011). Required pre-authorization: a dated approval record is checked against the validity window (invalid → reject); no record, reference or claimed approval → `PRE_AUTH_MISSING` reject; a claimed approval without a dated record, or a form contradicting a record → manual review. Evidence distinguishes `DOCUMENT_PRESENT` from `INSURER_VERIFIED`; member uploads remain unverified and add `PRE_AUTH_NOT_VERIFIED_WITH_INSURER`. Production web inputs are populated from the atomic reservation ledger; fixture inputs carry their supplied history. The evaluator reads the policy structure dynamically. It returns:

```json
{
  "state": "DECIDED",
  "decision": "APPROVED",
  "approved_amount": 1350,
  "approved_amount_paise": 135000,
  "reasons": [{"code": "COVERED", "message": "..."}],
  "correction_requests": [],
  "confidence_score": 0.96,
  "trace": [{"stage": "policy", "rule_id": "waiting_period", "status": "PASS", "policy_ref": "waiting_periods.initial_waiting_period_days", "evidence": {}}],
  "ledger": [{"kind": "adjustment", "description": "Member co-pay", "amount_paise": -15000}]
}
```

Decision is `APPROVED`, `PARTIAL`, `REJECTED`, `MANUAL_REVIEW`, or `null` while documents need correction. Financial adjustments are rounded to paise using decimal arithmetic. Trace entries distinguish `PASS`, `FAIL`, `ASSUMPTION`, `NOT_EVALUATED`, `FLAG`, and degraded/skipped stages. A missing patient name on the documents is `NOT_EVALUATED` and disclosed (`PATIENT_IDENTITY_NOT_VERIFIED`, lower confidence) for every source; a name not on the member's roster, or conflicting names, stops the claim; an `UNVERIFIED` or `FAILED` identity verdict routes to review (`PATIENT_IDENTITY_UNVERIFIED`). A bill with no total and no lines, a partly readable required document, and an unrecognised line on an allow-listed category route to review (`BILL_AMOUNT_UNVERIFIED`, `DOCUMENT_QUALITY_INSUFFICIENT`, `LINE_ITEM_UNRESOLVED`). Absent utilisation leaves aggregate limits `NOT_EVALUATED` with an advisory reason on payable outcomes. On a non-payable outcome, line items that passed their checks are relabelled `NOT_ADJUDICATED` (or `EXCLUDED` with the whole claim), keeping the line result in `line_check`. A risk-signal component failure is `SKIPPED_COMPONENT_FAILURE` with `COMPONENT_DEGRADED`, and the outcome step records `post_decision_review_recommended`. Malformed claim/policy evidence is returned as a traced review or correction result, rather than an unhandled exception. Confidence is a heuristic evidence-completeness rubric, not a calibrated probability; its factors appear in the `confidence/confidence_rubric` trace step.

The interpretations that reconcile the supplied policy with the supplied cases (per-claim ceiling, category sub-limit, benefit order, pre-auth precedence and match scope, line-item vocabulary, dental report, roster repairs) are general rules recorded in the canonical audit trail and referenced from each trace. They are not legal policy advice and need insurer confirmation.
