# Extraction scope and document benchmark design

This is our interpretation of the supplied [sample documents guide](../reference/sample_documents_guide.md). The guide itself is kept unchanged; everything below is a project decision.

## Adjudication material vs reviewer context

The Sarvam Extract schema asks for most fields the guide lists and retains source evidence when available. Only a subset may change a financial outcome:

| Document | Adjudication material (a missing value holds the claim) | Extracted with evidence when present | Shown to reviewers only |
| --- | --- | --- | --- |
| Prescription | patient name, diagnosis | doctor name and registration, date, treatment | specialization, clinic address, medicine dosage text |
| Hospital / clinic bill | patient name, total, itemized line items (sum must equal total) | bill number, date, GST | GSTIN, address, payment mode |
| Pharmacy bill | patient name, total, line items; brand status only with an exact printed phrase | drug licence, bill number | batch, expiry, MRP, pharmacist |
| Lab / diagnostic report | patient name, date, test name | NABL status, pathologist registration | reference ranges, remarks |
| Dental report | patient name, date, diagnosis | — | everything else |

These are the `material_fields` in `claims/documents.py::revalidate_documents`. Any value the reader cannot establish is left empty and raises `MATERIAL_FIELD_UNVERIFIED`; the pipeline never fills a gap from context or from the claim form.

## How the guide's variations are handled

| Guide variation | Our behaviour |
| --- | --- |
| Handwritten prescription | OCR via Sarvam. If patient name or diagnosis cannot be read, the member gets a specific re-upload request. A wrong confident read is the failure we measure. |
| Phone photo (skew, shadow, blur) | Local gate rejects only images that are tiny, unopenable or almost uniform (contrast stddev below 2). Everything else goes to OCR; we don't use an uncalibrated blur heuristic to reject photos. |
| Rubber stamp over registration | Registration is not adjudication material for a consultation, so an obscured number is left empty and the document still counts. |
| Multilingual (Hindi + English) | English fields are extracted; Devanagari-only lines are not adjudication material. |
| Partial / cropped page | Missing material fields hold the claim (`MATERIAL_FIELD_UNVERIFIED`, `AMOUNT_UNVERIFIED`); nothing is inferred. |
| Amounts crossed out and rewritten | No dedicated alteration detector yet. Protection comes from arithmetic reconciliation (`BILL_ARITHMETIC_CONFLICT`) and the duplicate-bill check. The corpus includes a struck-through bill labelled `abstain` so the live benchmark shows whether this gap matters. |
| Scanned / multi-page PDF | A PDF with no text layer is sent to OCR as a whole; up to 10 pages. Line items are parsed from every page's text. |

## Mandatory vs optional providers

- **Mandatory: Sarvam Document AI** (Digitise, then Extract only when material bill or prescription fields are missing). An image or scanned PDF cannot be read without it. If it is missing, throws, times out, or returns nothing, the document gets `EXTRACTION_UNAVAILABLE` (operator review) or `UNREADABLE_DOCUMENT` (re-upload). Any document issue stops adjudication, so a mandatory failure can never produce an approve, partial or reject decision.
- **Optional: Gemini evidence review** (opt-in with `GEMINI_EVIDENCE_REVIEW_ENABLED`). It may only propose facts backed by quotes found in the OCR text, and the deterministic gate re-runs afterwards. `EXTRACTION_UNAVAILABLE` is not a derived issue, so Gemini cannot clear it. When Gemini fails or abstains, existing issues stay as they are, and a claim that was already complete is not blocked.

The offline `provider_failure_injection` suite and `tests/test_document_evaluation.py` check both properties with scripted providers.

## Benchmark design

There are two separate reports, and neither one stands in for the other.

1. **Offline intake/routing and fail-closed** (`docs/reports/document-evaluation.*`, the default command). Makes no provider calls. Contents:
   - six selectable-text PDF/image routing scenarios;
   - every image in the dirty corpus run with OCR unavailable, which must be held with no extracted content;
   - scripted mandatory and optional provider failures.

   This report proves routing and safety only. It does not measure OCR accuracy.
2. **Live OCR** (`document-ocr-evaluation.*`, `--providers live`). Each corpus document goes through the real `process_uploads` Sarvam path, and optionally through the Gemini handoff (`--gemini`). Scores:
   - classification accuracy (the blank page is excluded because it has no type);
   - per-field accuracy. Values are normalised: names ignore honorifics, dates are parsed, amounts are compared to the paisa, and line items are compared as the sorted list of amounts;
   - abstention accuracy (held vs proceeded, against the label), plus false-hold rate and missed holds;
   - **unsafe confident errors**: a document that would proceed with a wrong or hallucinated critical field, or with a struck-through value. The target is 0, and the CLI exits 1 if it is not;
   - Sarvam digitise and extract call and page counts, provider failures, Gemini calls, per-call and per-document latency.

   Without `SARVAM_API_KEY`, every scenario is `NOT_RUN (missing SARVAM_API_KEY)`, `metrics` is `null`, and the exit code is 2. Nothing is estimated or carried over from earlier runs.

### Corpus

`tools/generate_dirty_documents.py` lays pages out as HTML in PyMuPDF (whose bundled Noto fonts shape Devanagari correctly), rasterises them, and applies seeded Pillow degradations. No file has a text layer. `labels.json` stores the ground truth and a SHA-256 for every file, and the benchmark refuses to run if a file has drifted. `--check` compares a fresh build with the committed bytes; font or codec differences on another machine show up as drift rather than silently changed labels.

| id | conditions | expected |
| --- | --- | --- |
| rx_clean_scan | clean greyscale scan | extract |
| rx_handwritten | handwriting-style fill-ins on a printed template | extract (hold acceptable) |
| rx_stamp_over_registration | violet stamp over registration | extract; registration may be empty |
| rx_hindi_english | Devanagari + English | extract English fields |
| rx_scanned_pdf | image-only PDF | extract |
| bill_phone_photo_skewed | 6° skew, shadow, blur, noise | extract (re-upload acceptable) |
| bill_multipage_scan | 2-page scanned PDF, total on page 2 | extract, aggregate items |
| bill_cropped_partial | torn below the second item | re-upload; any total is hallucinated |
| bill_struck_correction | struck amounts with handwritten corrections | abstain; 1200/1500 forbidden |
| bill_low_contrast | faded photocopy that passes the local gate | re-upload preferred |
| blank_page | near-blank photo | re-upload (rejected locally) |
| lab_report_scan | NABL lab report table | extract |
| pharmacy_bill_scan | pharmacy bill with Generic/Branded markers | extract |
| non_medical_receipt | restaurant receipt | classify UNKNOWN, re-upload |

### Limits

The corpus is synthetic and small (14 documents). It tests specific failure modes; it is not a sample of real claims. Handwriting is simulated with a script font plus jitter, which is easier to read than real handwriting. Results from the live run show whether the pipeline behaves safely on each condition. Production accuracy would still need a consented set of real documents.
