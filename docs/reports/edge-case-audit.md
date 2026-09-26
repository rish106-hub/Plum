# Edge-case audit

## Current status

This document is the current-state audit for the 26 September 2026 checkout. An older version recorded findings against an earlier implementation (79–95 tests) and is superseded; its historical observations are not descriptions of the current code.

The current verification is:

- 112 tests pass, including 20 subtests.
- `scripts.evaluate` matches all 12 supplied fixtures.
- Ruff, mypy, compileall, and the isolated real-browser flow pass.
- Live intake persists the submission date and dated pre-authorization evidence, compares extracted document dates, records evidence before policy, and exposes reviewer disposition.

## Findings closed in the current implementation

The following earlier findings are now regression-tested or covered by a reachable code path:

- All bill line items are priced, not only the first bill.
- Category allowlists, exclusions, covered systems, annual session limits, registered-practitioner evidence, category coverage, policy renewal, relationship coverage, policy period, and document/treatment-date consistency are traced. Live uploads without covered-system evidence route to review.
- Missing treatment dates cannot be adjudicated; submission dates before treatment or beyond the deadline fail closed.
- Policy limits apply uniformly to fixture and upload payloads. TC006 and TC010 are documented using the current configured interpretation, not request-controlled fixture switches.
- Live intake persists a policy snapshot hash, submission date, and pre-authorization status. A policy change between intake and processing routes the claim to manual review.
- Duplicate checks include exact file hashes and complete logical bill fingerprints; correction/provider-failure attempts are excluded from paid-claim history.
- Covered dependents and ordinary honorific variation are matched against the family roster; dangling dependent IDs were removed from the policy data.
- Provider outages, malformed handoffs, unsupported citations, and unsafe traces fail closed into correction or manual review.
- Required pre-authorizations now require a dated approval reference and are checked against the configured 30-day validity period.
- High-value review thresholds and mandatory-generic pharmacy policy are traced. Branded medicine under the mandatory-generic rule routes to manual review rather than a silent payment decision.
- A 6-scenario real-byte synthetic PDF/image intake evaluation is generated separately from the 12 structured policy fixtures.

## Deliberate limits and review debt

These remain explicit prototype boundaries rather than silently claimed capabilities:

- `sum_insured_per_employee` and the family-floater combined limit constrain payment when an explicit utilisation feed exists; without one they remain `NOT_EVALUATED`. The annual OPD ledger is not a substitute for those balances.
- The generic 365-day pre-existing-condition rule is evaluated when explicit `pre_existing_conditions` evidence is supplied; absent that evidence, the trace is `NOT_EVALUATED` and the claim is not treated as proof of no prior diagnosis.
- OCR, handwriting, multilingual extraction, stamp overlap, and confidence calibration still require a consented, provider-backed labelled corpus. The synthetic real-byte benchmark demonstrates safe routing, not OCR accuracy.
- Similarity-based duplicate detection for recompressed or cropped bills needs a measured false-positive threshold before introduction.
- The local operations page has no production authentication, authorization, retention, or encryption controls.
- SQLite is appropriate for this local prototype, not a concurrent production claims ledger.

## Audit method

External audit comments are hypotheses until reproduced against source, tests, and a real upload. Generated evaluation output is the source for fixture results; this report records implementation status and remaining proof gaps without presenting historical baselines as current behavior.
