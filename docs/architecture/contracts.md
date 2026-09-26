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
| `GET /api/claims` | Optional `limit` 1–100 | Recent claim summaries | 422 invalid limit |
| `POST /api/claims/{id}/retry` | Failed claim ID | HTTP 202 queued retry | 404 unknown ID; 409 unless state is `PROCESSING_FAILED` |
| `GET /`, `GET /claims/{id}` | Browser navigation | Submission or reviewer HTML | 404 unknown claim ID; `GET /` returns 503 `POLICY_CONFIGURATION_INVALID` if the policy file has become invalid |
| `GET /ops` | Browser navigation | Local operations worklist HTML; client reads the list and full claim API for escalation, evidence, model usage, and trace | No authentication in the local prototype; must be added before real member data |

**Startup.** The app refuses to start (`RuntimeError`) when `data/policy_terms.json` fails validation (message contains `POLICY_CONFIGURATION_INVALID`), or when `PLUM_DEMO_CLOCK` is set while `PLUM_ENV` is not `development`/`test` or holds an unparseable value. Intake stamps `submission_date` from the real clock; the demo clock replaces it only in development/test and adds a `clock/demo_clock/OVERRIDDEN` trace step plus a `DEMO_CLOCK` event. Record timestamps always use the real clock. The intake snapshot stores a hash of the canonical policy; if it differs at processing time the claim routes to `MANUAL_REVIEW` (`POLICY_CHANGED`).

`process_claim(id)` changes `QUEUED → PROCESSING → DOCUMENT_CORRECTION_REQUIRED | MANUAL_REVIEW | DECIDED | PROCESSING_FAILED`. It records immutable claim events and resumes `QUEUED`/`PROCESSING` records on app startup. FastAPI background tasks execute work in the app process; this is a durable *record* with recovery, not a separate managed queue. Provider exceptions are stored by exception type only to avoid putting document text in error messages. Before adjudication, exact bill-file hashes and complete logical fingerprints (bill number, provider, amount, treatment date) are checked against paid claims; a match routes to manual review. Same-day/month fraud signals count all local submissions for the employee's covered family; policy-year benefit totals count approved/partial decisions for that family. Alternative-medicine sessions count approved/partial decisions for the enrolled member. Since the prototype has no insurer remittance feed, approved amounts are the available annual-benefit consumption proxy, explicitly identified in the trace.

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

Each file has `file_name`, byte `data`, and optional `content_type`. The adapter checks magic bytes, size, image resolution/contrast, PDF page count, duplicate hashes, actual document type, required-document matrix, and patient names. Digital PDF text is local. For images and scans, the default provider uses `SARVAM_API_KEY` from the environment. It calls digitisation for OCR and invokes schema extraction only if accepted text is missing a material field.

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

## Fixture adapter (`claims.fixtures`)

`normalize_fixture(case: dict) -> dict` copies `case.input` and maps every supplied document's `actual_type`, `quality`, `content`, and `patient_name_on_doc` into the same normalized document shape accepted by the evaluator. Every mapped document carries `source="fixture_metadata"`; the source is recorded as provenance in the trace and never changes a policy rule. The function drops `case_id`, `expected`, and the harness-only `simulate_component_failure`, so no production rule can branch on a test ID. `load_cases(path)` reads the supplied cases unmodified; `load_policy(path)` delegates to `claims.policy.load_policy`.

## Policy loader (`claims.policy`)

`load_policy(path) -> dict` reads the raw bytes, records their sha256, validates them against the strict `RawPolicy` schema (unknown keys forbidden; money, percent and count fields required and strictly typed; cross-references checked), and normalizes them into the canonical config (`schema_version: plum.canonical_policy.v1`, money in paise) with `source`, `canonical_sha256` and an `audit` list of every derivation, interpretation, conflict resolution, reference repair and defaulted optional field. Any failure raises `PolicyConfigurationError` (`code = "POLICY_CONFIGURATION_INVALID"`); nothing defaults silently. `ensure_canonical(policy)` normalizes a raw dict passed directly to the evaluator. The rules are explained in [policy interpretation](../design/policy-interpretation.md).

## Claim evaluator (`claims.core`)

```python
evaluate_claim(payload: dict, policy: dict) -> dict
```

`policy` is the canonical config (a raw dict is normalized first; an invalid one returns `MANUAL_REVIEW` with `POLICY_CONFIGURATION_INVALID` and confidence 0.0). Every result's trace starts with a `configuration/policy_source` step carrying the policy ID, source and canonical sha256, and the conflict-resolution IDs in force. Input requires member and policy IDs, claim category, treatment date, claimed amount and normalized documents. It may include `submission_date`, year-to-date approved amount, sum-insured/family-floater utilisation, same-day/month submission history, prior approved alternative-medicine sessions, hospital name and pre-authorization evidence. Required pre-authorization: a dated approval record is checked against the validity window (invalid → reject); no record, reference or claimed approval → `PRE_AUTH_MISSING` reject; a claimed approval without a dated record, or a form contradicting a record → manual review. Production web inputs are populated from local claim history; fixture inputs carry their supplied history. The evaluator reads the policy structure dynamically. It returns:

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

Decision is `APPROVED`, `PARTIAL`, `REJECTED`, `MANUAL_REVIEW`, or `null` while documents need correction. Financial adjustments are rounded to paise using decimal arithmetic. Trace entries distinguish `PASS`, `FAIL`, `ASSUMPTION`, `NOT_EVALUATED`, `FLAG`, and degraded/skipped stages. A missing patient name on the documents is `NOT_EVALUATED` and disclosed (`PATIENT_IDENTITY_NOT_VERIFIED`, lower confidence) for every source; a name not on the member's roster, or conflicting names, stops the claim. Absent utilisation leaves aggregate limits `NOT_EVALUATED` with an advisory reason on payable outcomes. Malformed claim/policy evidence is returned as a traced review or correction result, rather than an unhandled exception. Confidence is a heuristic evidence-completeness rubric, not a calibrated probability; its factors appear in the `confidence/confidence_rubric` trace step.

The interpretations that reconcile the supplied policy with the supplied cases (per-claim ceiling, pre-auth precedence, dental report, roster repairs) are general rules recorded in the canonical audit trail and referenced from each trace. They are not legal policy advice and need insurer confirmation.
