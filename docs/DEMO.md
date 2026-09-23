# Demo recording guide (target: 9–10 minutes)

The assignment asks for an 8–12 minute recording. Use the synthetic files created with `.venv/bin/python -m tools.generate_samples sample_documents`. Keep the reviewer screen at normal browser zoom so the trace and ledger are readable. The [approval screenshot](screenshots/approval.png) and [correction screenshot](screenshots/correction.png) show the expected states.

## 0:00–1:00 — What the system does

Show the submission page and the two routes into one claim model: real PDF/image uploads for the app and the supplied structured fixtures for reproducible evaluation. State clearly that the fixture pass rate does not measure OCR accuracy. Point to the six required behaviors in `assignment.md`.

## 1:00–3:00 — Document problem stops early

Select EMP001, Consultation, 2024-11-01, ₹1,500, and known year-to-date reimbursed amount ₹0. Upload `synthetic_prescription.pdf` twice and submit. Show the correction screen: `decision` remains null, and the message names the uploaded prescription and required hospital/clinic bill. Expand the document-gate trace. Explain why no policy calculation ran.

## 3:00–5:30 — Successful claim and full trace

Submit a new claim with the same details, now uploading `synthetic_prescription.pdf` and `synthetic_hospital_bill.pdf`. Show APPROVED ₹1,350. Scroll through document requirements, identity and bill-total checks, waiting period, exclusions, pre-authorization, annual/per-claim limits, and the line-item ledger. Explain that ₹1,500 less the 10% consultation co-pay is ₹1,350. Show `NOT_EVALUATED` on submission deadline because the fixture has no submission timestamp, and `ASSUMPTION` on the consultation fee interpretation.

## 5:30–7:00 — Design decision worth keeping

Open `claims/core.py`, `claims/documents.py`, and `docs/CONTRACTS.md`. The document adapter can be replaced, but the policy evaluator remains deterministic and records each rule with evidence and an integer-paise amount ledger. Explain how this makes a rejection or partial approval reproducible and keeps OCR/model errors from directly moving money. Show the cost counters and the Sarvam route: local digital PDF text first, ₹0.50/page digitisation for scans, ₹1/page schema extraction only when material fields are missing.

## 7:00–8:30 — Failure and honest limitation

Show TC011 in `EVAL_REPORT.md`: optional component failure is visible, the claim continues, and confidence falls. Then show the TC006/TC010 interpretation notes. Say what would change with more time: obtain authoritative insurer clarification, build a labelled Indian document benchmark, calibrate confidence and thresholds, and move background jobs to a separate worker/queue at higher load. If the Sarvam key is configured, submit the synthetic bill PNG once and show its extraction metrics. If it is not, demonstrate the manual-review path for unavailable image extraction and state that live OCR has not been verified.

## 8:30–9:30 — Evaluation and close

Run `.venv/bin/python -m scripts.evaluate` and show 12/12 fixture expectations, then open one complete trace in `EVAL_REPORT.md`. Point to the tested browser flow and local setup commands in `README.md`. Leave the reviewer with the specific tradeoff: low cost by avoiding unnecessary provider calls, with human review for missing material evidence.
