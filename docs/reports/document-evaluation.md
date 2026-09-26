# Synthetic document intake evaluation

**6/6** labelled synthetic document scenarios matched their safe expected route.

This run uses actual generated PDF/image bytes through `claims.documents.process_uploads`. It demonstrates intake routing, not OCR accuracy, handwriting recognition, multilingual extraction, or calibrated blur detection on real-world data.

| Scenario | Expected safe route | Result | Match |
| --- | --- | --- | --- |
| clean_consultation | ACCEPTED | ACCEPTED | Yes |
| wrong_document_type | MISSING_DOCUMENT | MISSING_DOCUMENT | Yes |
| patient_mismatch | PATIENT_MISMATCH | PATIENT_MISMATCH | Yes |
| amount_conflict | BILL_ARITHMETIC_CONFLICT | BILL_ARITHMETIC_CONFLICT | Yes |
| multi_page_bill | ACCEPTED | ACCEPTED | Yes |
| blurred_phone_photo | EXTRACTION_UNAVAILABLE | EXTRACTION_UNAVAILABLE, MISSING_DOCUMENT, PATIENT_UNVERIFIED, UNIDENTIFIED_DOCUMENT | Yes |

## Scope and next evidence

- Clean and multi-page selectable-text PDFs prove the local parsing branch, not visual OCR.
- The blurred photo is not rejected by an uncalibrated local blur heuristic. Without a configured extraction provider it routes to operator review, avoiding a member-facing false positive.
- A provider-backed, consented labelled corpus is still required before reporting field accuracy, handwriting support, multilingual performance, or false-rejection rates.
