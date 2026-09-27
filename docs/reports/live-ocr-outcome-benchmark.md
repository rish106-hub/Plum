# Live Sarvam outcome benchmark

Status: **PASS** (4/4 scenarios matched).

All inputs are synthetic image-only PDFs. Each claim was submitted through the website controls, polled through the local API, and visually retained as screenshots.

**Scope boundary:** this is a bounded end-to-end outcome regression. It verifies these four synthetic workflows and selected critical fields; it is not a production OCR-accuracy, handwriting, multilingual, calibration, or population-level benchmark.

| Scenario | Expected | State | Decision | Reasons | Provider calls/failures | Browser errors | Seconds | Match |
| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |
| approved | APPROVED | DECIDED | APPROVED | COVERED | 4/0 | 0 | 33.199 | Yes |
| doubtful | SAFE_HOLD | DOCUMENT_CORRECTION_REQUIRED | None | UNIDENTIFIED_DOCUMENT, MISSING_DOCUMENT | 3/0 | 0 | 15.08 | Yes |
| rejected | REJECTED | DECIDED | REJECTED | NO_PAYABLE_AMOUNT, EXCLUDED_PROCEDURE | 2/0 | 0 | 12.08 | Yes |
| human_review | MANUAL_REVIEW | MANUAL_REVIEW | MANUAL_REVIEW | DUPLICATE_STAMP | 4/0 | 0 | 21.092 | Yes |

Provider verification: **13 calls, 0 failures**. Browser verification: **0 page errors**.

Raw API responses are under `output/live-ocr-benchmark/responses/`; input, filled-form, outcome, and authenticated review-queue screenshots are under `output/live-ocr-benchmark/screenshots/`.

The broader labelled dirty-document accuracy benchmark remains `scripts.evaluate_documents --providers live`; its latest offline verification status is reported separately and must not be inferred from this outcome run.
