# MVP submission checklist

Use this checklist immediately before sharing the repository.

## Repository hygiene

- [ ] `git status --short` contains only intentional submission changes.
- [ ] No `.env`, `.data/`, virtual environment, API key, or real health document is tracked.
- [ ] The README and local runbook contain no personal absolute filesystem path.
- [ ] Generated verification reports and `docs/reports/live-ocr-outcome-benchmark.md` describe the current code and do not claim historical PR state as current evidence.

## Reproducible evidence

- [ ] Follow [local setup and reproducible demo](local-runbook.md) from a fresh clone or fresh virtual environment.
- [ ] Confirm the digital-PDF path returns `APPROVED` for ₹1,350 with the fixed demo clock and temporary data directory.
- [ ] Confirm the repeated-prescription path returns `DOCUMENT_CORRECTION_REQUIRED`.
- [ ] Confirm resubmitting the same approved bill returns `MANUAL_REVIEW` with `DUPLICATE_BILL`.

## Verification record

- [ ] Run the pytest, Ruff, mypy, compileall, fixture-evaluation, and document-evaluation commands in the local runbook.
- [ ] Run `scripts.browser_check` separately against the fixed-clock local server.
- [ ] If provider-backed evidence is being delivered, run `make live-outcome-generate` and `make live-outcome-run` against a fresh configured server and retain its report/screenshots.
- [ ] State the distinction accurately: GitHub Actions runs offline and provider-free checks; browser/provider-backed evidence is local verification and paid live OCR is not part of CI.

## Claims to keep precise

- [ ] The fixture report proves deterministic policy behavior, not OCR quality.
- [ ] The offline document suites prove routing and fail-closed behavior, not OCR accuracy.
- [ ] The four-scenario website/API live regression proves only its clean synthetic outcomes; the labelled dirty-corpus run is the separate accuracy benchmark, and neither is a production sample.
- [ ] Local claim/benefit history is not insurer remittance history.
- [ ] Gemini may recover cited evidence when explicitly enabled; it never decides coverage or payment.
