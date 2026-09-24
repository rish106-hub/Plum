# Component contracts

All amounts in external claim inputs and `approved_amount` are INR rupees. Internal ledger and `approved_amount_paise` are integer paise. Dates supplied by clients use `YYYY-MM-DD`. Missing values remain missing; components must not substitute a plausible patient, date, total or authorization.

## HTTP intake and workflow (`claims.web`)

| Operation | Input | Output | Errors |
| --- | --- | --- | --- |
| `POST /api/claims` | Multipart `member_id`, `claim_category`, `treatment_date`, `claimed_amount`, and 1–6 PDF/JPEG/PNG/WebP `files` | HTTP 202 `{id,state:"QUEUED",url}`; files saved under private local storage; claim and event rows committed | HTTP 422 for invalid category/member/date/money/media, empty file, per-file 10 MB or total 30 MB excess; storage/database failures surface as server errors |
| `GET /api/claims/{id}` | Claim ID | Stored claim request, state, result, file metadata and ordered events | 404 unknown ID |
| `GET /api/claims` | Optional `limit` 1–100 | Recent claim summaries | 422 invalid limit |
| `POST /api/claims/{id}/retry` | Failed claim ID | HTTP 202 queued retry | 404 unknown ID; 409 unless state is `PROCESSING_FAILED` |
| `GET /`, `GET /claims/{id}` | Browser navigation | Submission or reviewer HTML | 404 unknown claim ID |

`process_claim(id)` changes `QUEUED → PROCESSING → DOCUMENT_CORRECTION_REQUIRED | MANUAL_REVIEW | DECIDED | PROCESSING_FAILED`. It records immutable claim events and resumes `QUEUED`/`PROCESSING` records on app startup. FastAPI background tasks execute work in the app process; this is a durable *record* with recovery, not a separate managed queue. Provider exceptions are stored by exception type only to avoid putting document text in error messages. Before adjudication, exact bill-file hashes are checked against other claims; a match routes to manual review. Same-day/month counts and policy-year approved totals are derived from local history for the employee and dependents. Since the prototype has no insurer remittance feed, approved amounts are the available annual-benefit consumption proxy, explicitly identified in the trace.

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

`normalize_fixture(case: dict) -> dict` copies `case.input` and maps every supplied document's `actual_type`, `quality`, `content`, and `patient_name_on_doc` into the same normalized document shape accepted by the evaluator. Every mapped document carries `source="fixture_metadata"`. The function ignores `case_id` and `expected`, so no production rule can branch on a test ID. `load_cases(path)` and `load_policy(path)` read the supplied JSON files and raise parse/file errors to the caller.

## Claim evaluator (`claims.core`)

```python
evaluate_claim(payload: dict, policy: dict) -> dict
```

Input requires member and policy IDs, claim category, treatment date, claimed amount and normalized documents. It may include year-to-date approved amount, same-day/month claim history, hospital name and pre-authorization evidence. Production web inputs are populated from local claim history; fixture inputs carry their supplied history. The evaluator reads the policy structure dynamically. It returns:

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

Decision is `APPROVED`, `PARTIAL`, `REJECTED`, `MANUAL_REVIEW`, or `null` while documents need correction. Financial adjustments are rounded to paise using decimal arithmetic. Trace entries distinguish `PASS`, `FAIL`, `ASSUMPTION`, `NOT_EVALUATED`, `FLAG`, and degraded/skipped stages. Missing material identity on a real upload leads to review. Malformed claim/policy evidence is returned as a traced review or correction result, rather than an unhandled exception. Confidence is an evidence-quality rubric, not a calibrated probability.

The fixture-specific dental and consultation interpretations are recorded in `PLAN.md` and in the output trace. They are not general legal policy advice.
