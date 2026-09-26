# Policy interpretation

The evaluator supplied two machine-readable inputs: `data/policy_terms.json` (the policy) and `tests/fixtures/test_cases.json` (the expected behavior). Both are kept byte-for-byte as delivered; see the [artifact map](../reference/submission-artifact-map.md). **The fixture is authoritative for expected outcomes.** Where the policy text admits more than one reading, or two parts of the policy disagree, we derived one general rule that satisfies the fixture and the policy text together, wrote it in code, and recorded it in an audit trail. No rule branches on a case ID, member ID, file name, or evidence source.

This document explains each rule, the contradiction it resolves, and what we would confirm with a policy owner before it governed real payments. The live list of every interpretation the engine applies is generated from code into [`../reports/policy-audit.md`](../reports/policy-audit.md).

## Pipeline

```text
data/policy_terms.json (raw bytes, sha256 recorded)
    │
    ▼
Schema validation (claims.policy.RawPolicy, strict Pydantic)
    │   unknown keys, mistyped or missing money/percent/count fields,
    │   inconsistent categories, bad dates → PolicyConfigurationError
    │   (code POLICY_CONFIGURATION_INVALID); never a silent default
    ▼
PolicyNormalizer (interpretation tables live in claims/policy.py)
    │   derives ceilings, parses pre-auth text, merges exclusions,
    │   resolves roster references, attaches provenance to every term
    ▼
Canonical config (schema plum.canonical_policy.v1, money in paise)
    │   source sha256 + canonical sha256 + audit[] entries
    ▼
Evaluator (claims.core) reads only the canonical config
    │   first trace step: configuration/policy_source with both
    │   fingerprints and the conflict-resolution ids in force
    ▼
Decision, ledger, reasons, confidence, trace
```

- `claims.policy.load_policy(path)` reads the bytes, hashes them, validates, and normalizes. `claims.fixtures`, `claims.web`, `scripts.evaluate` and the tests all load the policy this way.
- An invalid policy never produces a payment decision. `evaluate_claim` returns `MANUAL_REVIEW` with `POLICY_CONFIGURATION_INVALID`, and the web app refuses to start (see [contracts](../architecture/contracts.md#http-intake-and-workflow-claimsweb)).
- Each audit entry has an `id`, a `kind` (`derived`, `interpretation`, `conflict_resolution`, `reference_repair`, `absent_optional_field`), the policy paths it reads, and a description.
- Each match term (exclusion, condition, pre-auth item, network name, AYUSH system) carries `provenance`. It is `policy_text` when its words appear in the supplied wording (plural and singular forms count) and `interpretation` when it comes from a table in `claims/policy.py`. Matches that rely only on interpretation terms reduce confidence (see [confidence](#confidence-rubric)).

## Rules and the contradictions they resolve

### 1. Per-claim ceiling versus category sub-limit

Audit ids: `PER_CLAIM_CEILING_RULE`, `PER_CLAIM_CEILING.<CATEGORY>`.

**Rule.** A category's per-claim ceiling is `max(coverage.per_claim_limit, category.sub_limit)`. It is tested against the eligible amount: the total after excluded and non-covered lines are removed, before network discount and co-pay. Exceeding it rejects the claim with `PER_CLAIM_EXCEEDED`; it is not a partial cap. The sub-limit is not applied as a separate payable cap. The resulting ceilings are consultation ₹5,000, vision ₹5,000, alternative medicine ₹8,000, diagnostic ₹10,000, dental ₹10,000 and pharmacy ₹15,000.

**Why this rule.** The policy has a ₹5,000 global per-claim limit and category sub-limits, some higher and one lower. The fixture pins down how they combine:

| Case | Expected | What it requires |
| --- | --- | --- |
| TC008 | Consultation ₹7,500 → `REJECTED`, `PER_CLAIM_EXCEEDED` | Consultation ceiling is ₹5,000, and exceeding it rejects rather than caps. |
| TC010 | Consultation ₹4,500 at a network hospital → `APPROVED` ₹3,240 | The consultation sub-limit (₹2,000) cannot be a per-claim or payable cap for consultation. |
| TC006 | Dental ₹12,000 with a ₹4,000 cosmetic line → `PARTIAL` ₹8,000 | Dental's ceiling is above the global ₹5,000, and the test runs on the eligible ₹8,000; on the claimed ₹12,000 it would reject. |

**Pre-authorization takes precedence over the ceiling.** TC007 (MRI ₹15,000, no pre-auth) is expected to be rejected with `PRE_AUTH_MISSING`, not `PER_CLAIM_EXCEEDED`. The policy requires pre-auth for MRI above ₹10,000, which is also the diagnostic ceiling; pre-authorization only means something if an authorized MRI above ₹10,000 can be paid. So when a matched pre-auth rule governs the treatment:

- pre-auth missing or invalid: the ceiling status is `DEFERRED_TO_PRE_AUTH` and the rejection comes from pre-auth;
- pre-auth valid with an authorized amount: `AUTHORIZED_BY_PRE_AUTH`, capped at the authorized amount;
- pre-auth valid without an authorized amount: `PRE_AUTH_AMOUNT_UNVERIFIED`, routed to review.

**Unresolved.** The consultation `sub_limit` of ₹2,000 has no effect under this rule. No reading that is consistent with TC010 can give it one. It is the only number in the policy the engine does not apply. It is recorded in `PER_CLAIM_CEILING.CONSULTATION`, and the trace shows both figures.

**Confirm with the policy owner:** whether category sub-limits are annual category caps, per-claim caps, or ceilings that override the global limit, and what the consultation ₹2,000 is meant to limit.

### 2. Pre-authorization

Audit ids: `PRE_AUTH_PARSED.*`, `PRE_AUTH_THRESHOLD_CONFLICT.pet_scan`.

**Rule.** The free-text `pre_authorization.required_for` entries are parsed into `{item, amount_greater_than}`. For example, "MRI scan (amount > ₹10,000)" becomes MRI above ₹10,000. A qualifier the parser cannot read fails validation instead of being ignored. The diagnostic `high_value_tests_requiring_pre_auth` list and `pre_auth_threshold` are merged into the same rules. Short forms (MRI, CT, PET-CT, and similar) come from an interpretation table. The amount tested is the sum of bill lines that match the item, or the claimed amount if no line matches.

When pre-authorization is required, the evidence decides the outcome:

| Evidence | Outcome |
| --- | --- |
| Dated approval record (reference and issue date) | Checked against `validity_days`: pass, or `PRE_AUTH_INVALID` (reject) |
| Form says not obtained, but a dated record exists | `PRE_AUTH_CONFLICT`, review |
| Form says obtained, or a document exists, without both date and reference | `PRE_AUTH_STATUS_UNKNOWN`, review |
| No record, no reference and no claim of approval | `PRE_AUTH_MISSING`, reject, with instructions to obtain pre-auth and resubmit the approval record |

TC007 supplies no pre-authorization information at all and expects `PRE_AUTH_MISSING`. The missing-record reading therefore applies to every source, including uploads.

**Contradiction.** The global list says PET scan always needs pre-auth. The diagnostic threshold implies it is needed only above ₹10,000. The engine applies the stricter reading (always).

**Confirm with the policy owner:** the PET threshold, and whether a member's claim of approval without the approval record should reject or go to review.

### 3. Dental report

Audit id: `DENTAL_REPORT_CONFLICT.DENTAL`.

`opd_categories.dental.requires_dental_report` is `true`, but `document_requirements.DENTAL` lists `DENTAL_REPORT` as optional. TC006 has no dental report and still expects a decision. The document matrix governs the correction gate. When the report is absent, the trace records `advisory_document` as `ADVISORY`, a payable outcome carries `ADVISORY_DOCUMENT_ABSENT`, and confidence drops by 0.03.

**Confirm:** whether a dental report is ever required, for example above an amount.

### 4. Roster references

Audit ids: `DANGLING_DEPENDENT.*` (kind `reference_repair`), `DEPENDENT_JOIN_DATE.*`, `RELATIONSHIP_VOCABULARY.*`.

- EMP003 → DEP003, EMP007 → DEP004 and DEP005, and EMP010 → DEP006 are listed as dependents but are not in `members`. The file is left as it is. The normalizer keeps them as `unresolved_dependents`, and they are not used to match covered patient names. A claim filed under one of those IDs is an unknown member and goes to `MANUAL_REVIEW` (`MEMBERSHIP_OR_POLICY_UNKNOWN`). The roster trace lists the unresolved dependents.
- DEP001 and DEP002 have no `join_date`, so they inherit the primary member's. The waiting-period trace shows `join_date_source`.
- The roster uses `CHILD`, but the family floater uses `CHILDREN`. A vocabulary table maps singular relationships to the floater's plural forms.

**Confirm:** the missing dependents' records, and dependents' join dates.

### 5. Aggregate limits when utilisation is absent

Audit id: `AGGREGATE_LIMITS_NEED_UTILISATION`.

Annual OPD limit, sum insured, family floater and annual session limits depend on other claims. When utilisation comes with the claim (`ytd_claims_amount`, `sum_insured_used`, `family_floater_used`, `prior_sessions`), the limit is applied. A capped payment becomes `PARTIAL`; a payment capped to zero is `REJECTED` with `NO_PAYABLE_AMOUNT`. When utilisation is absent:

- the trace rule is `NOT_EVALUATED`, never `PASS`;
- a payable outcome carries an advisory reason, for example `ANNUAL_LIMIT_NOT_EVALUATED` ("subject to the member's remaining annual OPD balance"), and confidence drops (−0.04 for annual, −0.03 for sessions);
- the decision is not blocked, because these limits can only reduce payment. On a rejection the unknown doesn't matter.

A single claim whose own sessions exceed the annual session cap is rejected with `SESSION_LIMIT_EXCEEDED` without history. TC011 has no year-to-date figure and is expected to be `APPROVED`, which rules out routing absent usage to review. The web intake always supplies year-to-date, session and history figures from its local claim records, so live claims are always evaluated against them. Those figures are approved amounts, which stand in for remittance data the prototype does not have.

**Confirm:** the authoritative utilisation feed, and whether payment should wait for it.

### 6. Exclusions

Audit ids: `EXCLUSION_MERGED.*`, `EXCLUSION_TERMS.*`.

Exclusions appear in three places: `exclusions.conditions` (the whole claim and every line), `exclusions.dental_exclusions` and `vision_exclusions`, and the categories' own `excluded_procedures` and `excluded_items`. They are merged into one list per scope. Two entries merge when one label, without its parenthetical, contains the other. Four entries merge (for example "LASIK" and "LASIK Surgery"), and each merge is audited.

- A claim-level hit on diagnosis, treatment or test name gives `EXCLUDED_CONDITION`, which rejects the claim.
- A line-level hit gives `EXCLUDED_PROCEDURE`: the line is removed, and the ledger records the reason and the policy reference.
- Parenthetical labels only add their interpreted terms, so an exclusion is never widened. "Implants (Cosmetic)" matches cosmetic implants, not every implant.
- "Vaccination (non-medically necessary)" has a clinical qualifier that documents cannot settle, so a hit goes to review (`EXCLUSION_QUALIFIER_REVIEW`).
- Negations such as "no history of substance abuse" do not match.

### 7. Other normalizations

- **Waiting-period synonyms** (`CONDITION_TERMS.*`): for example T2DM → diabetes and HTN → hypertension. Matching respects word boundaries, so "Herniation" does not trigger the hernia waiting period.
- **Network hospitals** (`NETWORK_NAME_VARIANTS.*`, `NETWORK_MATCH_RULE`): printed variants such as "Apollo Hospital" are recognized. A name matches exactly, or before a comma or dash branch suffix ("Apollo Hospitals, Bengaluru"). The trace shows the matched term.
- **Covered AYUSH systems** (`COVERED_SYSTEM_TERMS.*`): therapy and practitioner indicators such as panchakarma or vaidya for Ayurveda. With no indicator, the result is `COVERED_SYSTEM_UNKNOWN` and the claim goes to review.
- **Pharmacy brand status** (`BRAND_CLASSIFICATION.PHARMACY`): `branded_drug_copay_percent` needs each line to be classified BRANDED or GENERIC from printed text. An unclassified line gives `PHARMACY_BRAND_STATUS_UNKNOWN` (review). Under `generic_mandatory`, a branded line gives `GENERIC_SUBSTITUTION_REVIEW`.
- **Absent optional fields** (`NO_NETWORK_DISCOUNT.*`, `CATEGORY_PRE_AUTH_FLAG_ABSENT.*`): a category without a network discount gets 0%, and a category without a pre-auth flag gets `false`. Item-level pre-auth rules still apply. Each default is audited.

### 8. Evidence gaps that used to depend on the evidence source

Earlier versions exempted fixture evidence from several gates. Those exemptions are gone. Each gap now has one rule for every source (fixture metadata, PDF text, OCR, model candidate), and a test runs all 12 cases under four source labels and asserts identical results.

| Gap | Rule |
| --- | --- |
| No patient name on the documents | A missing name cannot contradict the member, so the claim is attributed to the member and `patient_identity` is `NOT_EVALUATED`. A payable outcome carries `PATIENT_IDENTITY_NOT_VERIFIED` and −0.04 confidence. A name that is present and not on the roster, or two different names, still stops the claim. |
| No practitioner registration where the category requires one | `PRACTITIONER_REGISTRATION_UNKNOWN`, review |
| No prior-session history | See rule 5 |

## Confidence rubric

Confidence is a heuristic evidence-completeness score. It is **not a calibrated probability** and is not an auto-pay threshold. The base is 0.96. A factor is deducted only when it affects the outcome reached (for example, an unknown annual balance cannot change a rejection). Every factor, and whether it was applied, is listed in the `confidence/confidence_rubric` trace step.

| Factor | Deduction | Applies to |
| --- | ---: | --- |
| Weak document quality | 0.10 | any outcome |
| Bill amount unavailable | 0.08 | any outcome |
| Optional component failure | 0.23 | any outcome |
| Patient name unavailable | 0.04 | payable |
| Annual OPD usage not evaluated | 0.04 | payable |
| Session history not evaluated | 0.03 | payable |
| Advisory document absent | 0.03 | payable |
| Line exclusion matched only through interpretation terms | 0.06 | payable |
| Claim exclusion matched only through interpretation terms | 0.06 | rejection |

Early returns use fixed values: document correction 0.9, amount mismatch 0.75, unknown member 0.4, malformed input or invalid policy 0.0. The fixture constrains the scores only loosely: TC004 must be above 0.85, TC012 above 0.90, and TC011 below TC004. The actual per-case scores are in the [verification summary](../reports/verification-summary.md). Calibration needs labelled outcomes that this prototype does not have.

## What is not special-cased

- `claims/` contains no case IDs. `claims.fixtures.normalize_fixture` drops `case_id`, `expected` and `simulate_component_failure` before the evaluator sees the claim.
- The only case-specific code is in the test harness. `scripts/evaluate.py` injects the optional-component failure that TC011's input requests, and it runs per-case behavior checks for the fixture's prose `system_must` requirements (for example, that TC010's co-pay basis equals the amount after discount). Those checks read expected values from the fixture and the raw policy rather than hard-coding them.
- The interpretation tables are general vocabulary (synonyms, name variants, relationship forms). They were written to cover the policy's own terms, and some entries (such as panchakarma and Apollo Hospital) are also the ones the supplied cases use. Any new vocabulary should be added to the tables with an audit entry, never as a branch in `claims/core.py`.
