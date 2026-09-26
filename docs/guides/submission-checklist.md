# MVP submission checklist

Use this checklist immediately before sharing the repository.

## Repository hygiene

- [ ] `git status --short` contains only intentional submission changes.
- [ ] No `.env`, `.data/`, virtual environment, API key, or real health document is tracked.
- [ ] The README and local runbook contain no personal absolute filesystem path.
- [ ] `docs/reports/feedback-audit.md` and `docs/reports/edge-case-audit.md` describe the current submission, not historical PR state.

## Reproducible evidence

- [ ] Follow [local setup and reproducible demo](local-runbook.md) from a fresh clone or fresh virtual environment.
- [ ] Confirm the digital-PDF path returns `APPROVED` for ₹1,350 with the fixed demo clock and temporary data directory.
- [ ] Confirm the repeated-prescription path returns `DOCUMENT_CORRECTION_REQUIRED`.
- [ ] Confirm resubmitting the same approved bill returns `MANUAL_REVIEW` with `DUPLICATE_BILL`.

## Verification record

- [ ] Run the pytest, Ruff, mypy, compileall, fixture-evaluation, and document-evaluation commands in the local runbook.
- [ ] Run `scripts.browser_check` separately against the fixed-clock local server.
- [ ] State the distinction accurately: GitHub Actions runs automated checks; the browser flow is local verification and is not part of CI.

## Claims to keep precise

- [ ] The fixture report proves deterministic policy behavior, not OCR quality.
- [ ] The document benchmark proves safe routing for its six synthetic cases, not handwriting, multilingual, or production OCR accuracy.
- [ ] Local claim/benefit history is not insurer remittance history.
- [ ] Gemini may recover cited evidence when explicitly enabled; it never decides coverage or payment.
