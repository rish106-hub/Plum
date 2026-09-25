# Demo recording guide (target: 9–10 minutes)

Record the local app after generating the synthetic PDFs with `.venv/bin/python -m tools.generate_samples`. The demo uses two connected views: claim submission at `/` and the local operations worklist at `/ops`. The worklist has no production authentication, so use only synthetic files. The latest [approval](../screenshots/approval.png), [correction](../screenshots/correction.png), and [duplicate review](../screenshots/duplicate-review.png) screenshots show the expected states.

## 0:00–1:00 — The two views

Show the claim upload form and the operations worklist. Explain that applicants submit evidence; reviewers inspect escalations, model usage, extracted facts, rule trace, and amount ledger. The 12 JSON fixtures separately test deterministic behavior and do not measure OCR accuracy.

## 1:00–2:30 — Stop early for a document problem

Select EMP001, Consultation, 2024-11-01, and ₹1,500. Upload `synthetic_prescription.pdf` twice. Show that the result has no decision or payable amount and asks specifically for the missing hospital or clinic bill. Expand the document-gate trace. Explain why policy adjudication did not run.

## 2:30–4:30 — Complete approval and trace

Submit the same details with `synthetic_prescription.pdf` and `synthetic_hospital_bill.pdf`. Show APPROVED ₹1,350. Walk through extracted facts, patient and amount reconciliation, waiting periods, exclusions, limits, and the ledger. ₹1,500 less the 10% consultation co-pay is ₹1,350. Point out `NOT_EVALUATED` on submission deadline: the current live intake does not pass a submission timestamp.

## 4:30–6:30 — Internal review portal

Submit the same bill again to trigger duplicate-bill manual review. Open `/ops`, filter or select the escalated claim, and show its reason, events, document evidence, AI usage, and full decision trace. Contrast the zero-call Gemini path for clear digital PDFs with the guarded ambiguous-evidence path. A model can supply cited facts but cannot approve coverage or calculate payment. Show the direct link from worklist to the claim decision record.

## 6:30–7:30 — Technical choice worth keeping

Open `claims/agent_pipeline.py`, `claims/core.py`, and [component contracts](../architecture/contracts.md). Explain the typed, bounded evidence handoff; the deterministic money and policy reducer; and the final decision/amount validation that routes inconsistencies to manual review with zero payable amount. Provider calls are conditional to contain cost.

## 7:30–8:30 — Technical choice to change

The local prototype uses in-process jobs and SQLite. At larger load, use a durable queue, leased workers, Postgres, object storage, and a single finalizer per claim revision. Also add production access control to `/ops`, collect submission dates and pre-authorization evidence, resolve insurer policy conflicts, and benchmark labelled Indian document images before any accuracy claim.

## 8:30–9:30 — Evaluation and close

Show [the full 12-case report](../reports/evaluation.md), including TC011's degraded optional component and the TC006/TC010 fixture interpretation notes. The target above 99% is not established by 12 structured fixtures. The real browser QA covers approval, correction, duplicate escalation, and reviewer screens; live Gemini transmission was blocked by automatic approval review for a synthetic patient-linked PDF, so provider accuracy remains unverified.
