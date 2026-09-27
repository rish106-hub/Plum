# Live Sarvam outcome benchmark

Status: **PASS** (4/4 scenarios matched).

All inputs are synthetic image-only PDFs. A returned extraction therefore exercised the live Sarvam path.

| Scenario | Expected | State | Decision | Reasons | Provider calls/failures | Seconds | Match |
| --- | --- | --- | --- | --- | --- | ---: | --- |
| approved | APPROVED | DECIDED | APPROVED | COVERED | 4/0 | 33.199 | Yes |
| doubtful | SAFE_HOLD | DOCUMENT_CORRECTION_REQUIRED | None | UNIDENTIFIED_DOCUMENT, MISSING_DOCUMENT | 3/0 | 15.08 | Yes |
| rejected | REJECTED | DECIDED | REJECTED | NO_PAYABLE_AMOUNT, EXCLUDED_PROCEDURE | 2/0 | 12.08 | Yes |
| human_review | MANUAL_REVIEW | MANUAL_REVIEW | MANUAL_REVIEW | DUPLICATE_STAMP | 4/0 | 21.092 | Yes |

Every claim was submitted through the website controls and verified through the local API.
Raw API responses are retained under `responses/`; input, form, outcome, and review-queue screenshots are retained under `screenshots/`.
