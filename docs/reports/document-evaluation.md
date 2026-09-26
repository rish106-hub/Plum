# Document intake, routing and fail-closed evaluation (offline)

**28/28** offline scenarios matched their safe expected route.

**This is not an OCR-accuracy report.** No provider was called (`provider_calls = 0` throughout). It shows that selectable-text PDFs parse and route correctly, that image-only documents are never turned into a confident decision without a reader, and that provider failures fail closed. OCR accuracy on the dirty corpus is measured only by `--providers live`; see `document-ocr-evaluation.md`.

## Intake and routing (selectable-text PDFs): 6/6

Intake and routing on generated selectable-text PDFs (local text layer, no OCR) plus one image without a reader.

| Scenario | Expected | Route | Issue codes | Match |
| --- | --- | --- | --- | --- |
| clean_consultation | ACCEPTED | ADJUDICATION_ELIGIBLE | none | Yes |
| wrong_document_type | MISSING_DOCUMENT | DOCUMENT_CORRECTION_REQUIRED | MISSING_DOCUMENT | Yes |
| patient_mismatch | PATIENT_MISMATCH | DOCUMENT_CORRECTION_REQUIRED | PATIENT_MISMATCH | Yes |
| amount_conflict | BILL_ARITHMETIC_CONFLICT | DOCUMENT_CORRECTION_REQUIRED | BILL_ARITHMETIC_CONFLICT | Yes |
| multi_page_bill | ACCEPTED | ADJUDICATION_ELIGIBLE | none | Yes |
| blurred_phone_photo | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |

## Image-only dirty corpus with OCR unavailable: 14/14

Every image-only corpus document with OCR unavailable: must be held (manual review or re-upload) with no extracted content.

| Scenario | Expected | Route | Issue codes | Match |
| --- | --- | --- | --- | --- |
| rx_clean_scan | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| rx_handwritten | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| rx_stamp_over_registration | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| rx_hindi_english | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| rx_scanned_pdf | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| bill_phone_photo_skewed | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| bill_multipage_scan | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| bill_cropped_partial | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| bill_struck_correction | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| bill_low_contrast | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| blank_page | UNREADABLE_IMAGE | DOCUMENT_CORRECTION_REQUIRED | MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNREADABLE_IMAGE | Yes |
| lab_report_scan | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| pharmacy_bill_scan | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| non_medical_receipt | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |

## Mandatory vs optional provider failure: 8/8

Scripted provider failures. Mandatory (Sarvam OCR) failures must block adjudication; optional (Gemini evidence) failures must neither clear an issue nor block a complete claim.

| Scenario | Expected | Route | Issue codes | Match |
| --- | --- | --- | --- | --- |
| mandatory_digitise_exception | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| mandatory_digitise_timeout | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| mandatory_digitise_empty_text | UNREADABLE_DOCUMENT | DOCUMENT_CORRECTION_REQUIRED | MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT, UNREADABLE_DOCUMENT | Yes |
| mandatory_extract_exception | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MATERIAL_FIELD_UNVERIFIED | Yes |
| mandatory_non_medical_text | UNIDENTIFIED_DOCUMENT | DOCUMENT_CORRECTION_REQUIRED | MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| optional_gemini_exception_after_mandatory_failure | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| optional_gemini_fabricated_candidate_after_mandatory_failure | EXTRACTION_UNAVAILABLE | MANUAL_REVIEW | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |
| optional_gemini_exception_on_complete_claim | ACCEPTED | ADJUDICATION_ELIGIBLE | none | Yes |
