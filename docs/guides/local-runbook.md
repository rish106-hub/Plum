# Local claim runbook

Use this when you want to upload one synthetic claim from Terminal and read the decision and its trace. The local app runs OCR when needed, then the deterministic policy evaluator returns the outcome.

Every command below runs from the repository root (the folder that contains `pyproject.toml`). No machine-specific paths are needed.

## 0. One-time setup

```bash
git clone <repo-url> Plum
cd Plum
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env   # optional; only needed for Sarvam/Gemini keys
```

## Why the demo uses a demo clock

The supplied policy period is 2024-04-01 to 2025-03-31 and the sample claims are treated in November 2024. The app stamps each claim's submission date with the real clock, so a claim for `treatment_date=2024-11-01` submitted today is far outside the policy's submission window and the documented ₹1,350 approval cannot be reproduced.

For local demos only, set an explicit development clock:

- `PLUM_ENV=development` (or `test`) marks the process as non-production. Unset means production.
- `PLUM_DEMO_CLOCK=2024-11-05` (an ISO date or datetime) becomes the claim's submission date.

The demo clock is never silent. The server logs `DEMO CLOCK ACTIVE` at startup, the intake page shows a "Demo clock active" banner, each affected claim page shows a "Demo clock submission" banner, and the decision trace starts with a `clock` / `demo_clock` entry whose evidence is `{"source": "PLUM_DEMO_CLOCK", "value": "2024-11-05", "submission_date": "2024-11-05", "environment": "development"}`. Record timestamps (`created_at`, processing history) always use the real clock. If `PLUM_DEMO_CLOCK` is set without `PLUM_ENV=development` or `test`, the app refuses to start.

## 1. Start the app

```bash
PLUM_DATA_DIR="$(mktemp -d)" PLUM_ENV=development PLUM_DEMO_CLOCK=2024-11-05 \
  PLUM_REVIEW_TOKEN=local-review-token .venv/bin/python -m uvicorn claims.web:app --reload
```

Leave this terminal open. The temporary data directory keeps the approval, duplicate, and review examples reproducible; the browser check deliberately creates repeat claims. For anything other than a replay of the 2024 sample policy, start it without the demo variables so the real clock is used: `.venv/bin/python -m uvicorn claims.web:app --reload`.

Open a second terminal in the repository root for the claim submission. The app uses local SQLite and private upload files under `.data/` (override with `PLUM_DATA_DIR`). Opening `/ops` triggers the browser's Basic-auth prompt; use a reviewer identifier as the username and `local-review-token` as the password. API clients can instead send `X-Reviewer-ID` and `X-Reviewer-Token` headers.

Generate the synthetic documents once in that second terminal. They are local demo files and are intentionally not stored in Git:

```bash
.venv/bin/python -m tools.generate_samples
```

## 2. Submit a synthetic claim from Terminal

This request uses the selectable-text bill and prescription PDFs, which are parsed locally with no OCR provider call or charge. Use synthetic/sample files here, not real health documents.

```bash
CLAIM_ID="$(curl -sS -X POST http://127.0.0.1:8000/api/claims \
  -F 'member_id=EMP001' \
  -F 'claim_category=CONSULTATION' \
  -F 'treatment_date=2024-11-01' \
  -F 'claimed_amount=1500.00' \
  -F 'files=@.data/samples/synthetic_hospital_bill.pdf;type=application/pdf' \
  -F 'files=@.data/samples/synthetic_prescription.pdf;type=application/pdf' \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
printf 'Claim ID: %s\n' "$CLAIM_ID"
```

Expected result with the demo clock: `APPROVED`, `approved_amount` 1350 (₹1,500 less the 10% consultation co-pay). Submitting the same bill a second time is intentionally flagged as a duplicate (`MANUAL_REVIEW`, `DUPLICATE_BILL`); to repeat the clean approval, stop the app and start it with a fresh data folder, for example `PLUM_DATA_DIR=.data/run2`.

### Scanned-image variant (Sarvam OCR)

To exercise OCR, replace the bill line with `-F 'files=@.data/samples/synthetic_hospital_bill.png;type=image/png'`. This sends the image through Sarvam OCR and can incur a small charge. It needs `SARVAM_API_KEY` set in the ignored `.env` file. Restart the app after changing `.env`. The key is read by the app at startup; never paste it into a command, commit, screenshot, or log.

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
printf '%s' "$CLAIM_JSON" | .venv/bin/python -c 'import json,sys; c=json.load(sys.stdin); r=c.get("result") or {}; print(json.dumps({"state":c["state"],"submission_date":c["request"].get("submission_date"),"submission_clock":c["request"].get("submission_clock"),"decision":r.get("decision"),"approved_amount":r.get("approved_amount"),"reasons":r.get("reasons"),"correction_requests":r.get("correction_requests"),"trace":r.get("trace"),"document_metrics":r.get("document_metrics")}, indent=2))'
```

You can also open the reviewer page at `http://127.0.0.1:8000/claims/$CLAIM_ID`; it shows the demo-clock banner above the decision.

## Read the result

- `APPROVED` or `PARTIAL`: supported eligible amount after policy limits and adjustments. Check the `trace` and `reasons` before interpreting it.
- `REJECTED`: a deterministic policy rule rejected the claim. The trace identifies the rule and evidence.
- `DOCUMENT_CORRECTION_REQUIRED`: OCR/readability/completeness did not pass the intake gate. Follow `correction_requests`; there is no adjudication decision yet.
- `MANUAL_REVIEW`: evidence is ambiguous, a duplicate signal needs verification, or a required extraction provider failed. This is an intentional abstention, not an approval.
- `PROCESSING_FAILED`: inspect the local server terminal for the failure category, then retry from the reviewer page if appropriate.

Do not treat confidence as a calibrated probability. This is a prototype against supplied policy data. A payable decision atomically creates a `RESERVED` benefit entry; authenticated settlement updates move it to `PAID` or `RELEASED`. Those updates are local operator inputs, not an automatic insurer remittance feed.

## Optional Gemini evidence extraction

Gemini is not an always-on second OCR pass. A configured key alone does not enable it. The default setting is off; when explicitly enabled, the app calls Gemini only if local/Sarvam parsing leaves a required document fact missing, the document type is unknown, or bill totals conflict with line items. Clear claims should show `gemini.calls: 0`. The full gate sequence and fail-closed behavior are in [AI escalation logic](../architecture/ai-escalation.md).

For a synthetic-only run, first add `GEMINI_API_KEY` to the ignored `.env` file. Keep the existing `SARVAM_API_KEY` line unchanged. Only after the Gemini key is set, enable the opt-in flag:

```dotenv
GEMINI_API_KEY=replace-with-your-key
GEMINI_EVIDENCE_REVIEW_ENABLED=true
```

Do not paste either key into a terminal command or commit `.env`. Restart the app after changing `.env`, then submit synthetic documents as above. Gemini receives only the selected relevant page(s) and bounded OCR snippets for the unresolved fields. Its response must cite exact source text and page; the app validates and re-runs the document gate and deterministic policy evaluator. It never chooses eligibility or payable amounts. Provider failures, unsupported evidence, and unresolved conflicts remain manual review.

The sample claim may be clear enough not to call Gemini; that is expected and costs nothing. For a test that reaches Gemini, use a deliberately incomplete synthetic document and confirm `document_metrics.gemini` shows the call count and token usage. Do not use a real person's health documents for this demo. After the check, set the toggle back to `false` (or remove the line) and restart the app.

One synthetic one-page bill check against the current implementation used 1,429 input tokens and 333 output tokens, with one call and no retry. At Google's paid-tier introductory rates through December 31, 2026 ($0.75/1M input and $3.75/1M output), that request is about $0.0023; free-tier eligibility and actual document size can change the bill. See [current Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing).

## OCR cost

Selectable-text PDFs (the default request above) are parsed locally. Images and scanned PDFs use Sarvam and may incur a charge.

## Stop the app

In the first terminal, press `Ctrl-C`. Local claim history and uploads stay in `.data/` for your next run.
