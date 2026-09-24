# Local claim runbook

Use this when you want to upload one synthetic claim from Terminal and read the decision and its trace. The local app runs OCR when needed, then the deterministic policy evaluator returns the outcome.

## 1. Start the app

From the repository folder:

```bash
cd /Users/somilthakur/Desktop/Project
.venv/bin/python -m uvicorn claims.web:app --reload
```

Leave this terminal open. If `.venv` has not been created yet, run this once first:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Open a second terminal for the claim submission. The app uses local SQLite and private upload files under `.data/`.

## 2. Submit a synthetic scan from Terminal

The repository sample is a synthetic scanned bill image and a selectable-text prescription. This request sends the image through Sarvam OCR and parses the prescription locally. It can incur a small Sarvam charge. Use synthetic/sample files here, not real health documents.

```bash
cd /Users/somilthakur/Desktop/Project
CLAIM_ID="$(curl -sS -X POST http://127.0.0.1:8000/api/claims \
  -F 'member_id=EMP001' \
  -F 'claim_category=CONSULTATION' \
  -F 'treatment_date=2024-11-01' \
  -F 'claimed_amount=1500.00' \
  -F 'files=@sample_documents/synthetic_hospital_bill.png;type=image/png' \
  -F 'files=@sample_documents/synthetic_prescription.pdf;type=application/pdf' \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
printf 'Claim ID: %s\n' "$CLAIM_ID"
```

The image/scanned-PDF path needs `SARVAM_API_KEY` set in the ignored `.env` file. Restart the app after changing `.env`. The key is read by the app at startup; never paste it into a command, commit, screenshot, or log.

## 3. Wait for the outcome and inspect the evidence

```bash
for attempt in $(seq 1 45); do
  CLAIM_JSON="$(curl -sS "http://127.0.0.1:8000/api/claims/$CLAIM_ID")"
  CLAIM_STATE="$(printf '%s' "$CLAIM_JSON" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["state"])')"
  case "$CLAIM_STATE" in
    DECIDED|APPROVED|PARTIAL|REJECTED|MANUAL_REVIEW|DOCUMENT_CORRECTION_REQUIRED|PROCESSING_FAILED) break ;;
  esac
  sleep 1
done
printf '%s' "$CLAIM_JSON" | .venv/bin/python -c 'import json,sys; c=json.load(sys.stdin); print(json.dumps({"state":c["state"],"decision":(c.get("result") or {}).get("decision"),"approved_amount":(c.get("result") or {}).get("approved_amount"),"reasons":(c.get("result") or {}).get("reasons"),"correction_requests":(c.get("result") or {}).get("correction_requests"),"trace":(c.get("result") or {}).get("trace"),"document_metrics":(c.get("result") or {}).get("document_metrics")}, indent=2))'
```

You can also open the reviewer page at `http://127.0.0.1:8000/claims/$CLAIM_ID`.

## Read the result

- `APPROVED` or `PARTIAL`: supported eligible amount after policy limits and adjustments. Check the `trace` and `reasons` before interpreting it.
- `REJECTED`: a deterministic policy rule rejected the claim. The trace identifies the rule and evidence.
- `DOCUMENT_CORRECTION_REQUIRED`: OCR/readability/completeness did not pass the intake gate. Follow `correction_requests`; there is no adjudication decision yet.
- `MANUAL_REVIEW`: evidence is ambiguous, a duplicate signal needs verification, or a required extraction provider failed. This is an intentional abstention, not an approval.
- `PROCESSING_FAILED`: inspect the local server terminal for the failure category, then retry from the reviewer page if appropriate.

Do not treat confidence as a calibrated probability. This is a prototype against supplied policy data, and its local annual-benefit history is based on approved decisions rather than insurer remittance records.

## No-OCR-cost digital PDF path

Selectable-text PDFs are parsed locally. To avoid an OCR provider call, use the digital bill PDF rather than the `.png` above; keep the other form fields and prescription PDF. Images and scanned PDFs use Sarvam and may incur a charge.

## Stop the app

In the first terminal, press `Ctrl-C`. Local claim history and uploads stay in `.data/` for your next run.
