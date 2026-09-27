# Policy interpretation

The evaluator supplied two machine-readable inputs: `data/policy_terms.json` (the policy) and `tests/fixtures/test_cases.json` (the expected behavior). Both are kept byte-for-byte as delivered; see the [artifact map](../reference/submission-artifact-map.md). **The fixture is the sole source of truth for expected outcomes.** Where the policy text admits more than one reading, or two parts of the policy disagree, we derived one general rule that satisfies the fixture and the policy text together, wrote it in code, and recorded it in an audit trail. No rule branches on a case ID, member ID, file name, or evidence source.

This document explains each rule, the contradiction it resolves, and what we would confirm with a policy owner before it governed real payments. The live list of every interpretation the engine applies is generated from code into [`../reports/policy-audit.md`](../reports/policy-audit.md).

## Pipeline

```text
data/policy_terms.json (raw bytes, sha256 recorded)
    │
    ▼
Schema validation (claims.policy.RawPolicy, strict Pydantic)
    │   unknown keys, mistyped or missing fields, zero limits, blank text,
    │   bad exclusion labels, unknown renewal status, bad dates
    │   → PolicyConfigurationError (POLICY_CONFIGURATION_INVALID); never a silent default
    ▼
PolicyNormalizer (interpretation tables live in claims/policy.py)
    │   derives ceilings, parses pre-auth text, merges exclusions,
    │   resolves roster references, attaches provenance to every term
    ▼
Canonical config (schema plum.canonical_policy.v1, money in paise)
    │   source sha256 + audit[] + canonical sha256 (covers the audit too)
    ▼
Evaluator (claims.core) re-verifies the fingerprint, then reads only the canonical config
    │   first trace step: configuration/policy_source with both fingerprints,
    │   the audit sha256, the conflict-resolution ids and the full audit list
    ▼
Decision, ledger, reasons, confidence, trace
```

- `claims.policy.load_policy(path)` reads the bytes, hashes them, validates, and normalizes. `claims.fixtures`, `claims.web`, `scripts.evaluate`, `scripts.evaluate_documents` and the tests all load the policy this way.
- **Schema strictness.** Besides types and unknown keys, validation rejects: a per-claim limit, annual limit, sum insured, floater limit or category `sub_limit` of zero or less; whitespace-only names and labels; an exclusion label with no text outside its parentheses or with unbalanced parentheses; two `opd_categories` keys that differ only by case; `opd_categories` and `document_requirements` that name different categories; a `renewal_status` outside `ACTIVE`, `LAPSED`, `EXPIRED`, `CANCELLED`, `SUSPENDED`, `GRACE_PERIOD`, `PENDING_RENEWAL` (exact case); and any currency other than INR.
- **Fingerprints.** `canonical_sha256` is the sha256 of the stable JSON of every canonical key except itself, so it covers the audit trail as well as the rules. `claims.policy.policy_fingerprint(policy)` returns the verified canonical sha256; it is the one fingerprint the engine trace and the web intake snapshot use. `ensure_canonical` recomputes the fingerprint of any mapping that declares a `schema_version`, and raises `POLICY_CONFIGURATION_INVALID` on a mismatch or an unknown schema.
- An invalid policy never produces a payment decision. `evaluate_claim` returns `MANUAL_REVIEW` with `POLICY_CONFIGURATION_INVALID` and reports no fingerprint, and the web app refuses to start (see [contracts](../architecture/contracts.md#http-intake-and-workflow-claimsweb)).
- The `policy_source` trace step carries the full audit list, so the interpretations in force are persisted with every decision.
- Each audit entry has an `id`, a `kind` (`derived`, `interpretation`, `conflict_resolution`, `reference_repair`, `absent_optional_field`, `informational`), the policy paths it reads, and a description.
- Each match term (exclusion, condition, pre-auth item, covered item, network name, AYUSH system) carries `provenance`. It is `policy_text` when its words appear in the supplied wording (plural and singular forms count) and `interpretation` when it comes from a table in `claims/policy.py`. Exclusions that match only through interpretation terms reduce confidence (see [confidence](#confidence-rubric)).

## Rules and the contradictions they resolve

### 1. Per-claim ceiling

Audit ids: `PER_CLAIM_CEILING_RULE`, `PER_CLAIM_CEILING.<CATEGORY>`.

**Rule.** A category's per-claim ceiling is `max(coverage.per_claim_limit, category.sub_limit)`. It is tested against the eligible amount: the total after excluded and non-covered lines are removed, before network discount and co-pay. Exceeding it rejects the claim with `PER_CLAIM_EXCEEDED`; it is not a partial cap. The resulting ceilings are consultation ₹5,000, vision ₹5,000, alternative medicine ₹8,000, diagnostic ₹10,000, dental ₹10,000 and pharmacy ₹15,000.

**Why this rule.** This rule is forced by the fixture, not read from the policy. The policy has a ₹5,000 global per-claim limit and category sub-limits, some higher and one lower:

| Case | Expected | What it requires |
| --- | --- | --- |
| TC008 | Consultation ₹7,500 → `REJECTED`, `PER_CLAIM_EXCEEDED` | Consultation ceiling is ₹5,000, and exceeding it rejects rather than caps. |
| TC006 | Dental ₹12,000 with a ₹4,000 cosmetic line → `PARTIAL` ₹8,000 | Dental's ceiling is above the global ₹5,000, and the test runs on the eligible ₹8,000; on the claimed ₹12,000 it would reject. |
| TC010 | Consultation ₹4,500 at a network hospital → `APPROVED` ₹3,240 | The ₹2,000 consultation sub-limit cannot be the per-claim ceiling for consultation. It applies as a narrower cap instead (rule 2). |

**Pre-authorization takes precedence over the ceiling.** TC007 (MRI ₹15,000, no pre-auth) is expected to be rejected with `PRE_AUTH_MISSING`, not `PER_CLAIM_EXCEEDED`. The policy requires pre-auth for MRI above ₹10,000, which is also the diagnostic ceiling; pre-authorization only means something if an authorized MRI above ₹10,000 can be paid. So when a matched pre-auth rule governs the treatment:

- pre-auth missing or invalid: the ceiling status is `DEFERRED_TO_PRE_AUTH` and the rejection comes from pre-auth;
- pre-auth valid with an authorized amount: `AUTHORIZED_BY_PRE_AUTH`, and the eligible amount is capped at the authorized amount;
- pre-auth valid without an authorized amount: `PRE_AUTH_AMOUNT_UNVERIFIED`, routed to review.

**Confirm with the policy owner:** whether the global limit really yields to a higher category sub-limit, and whether exceeding the ceiling should reject or cap.

### 2. Category sub-limit

Audit ids: `CATEGORY_SUB_LIMIT_RULE`, `CATEGORY_SERVICE_TERMS.CONSULTATION`.

**Rule.** A category `sub_limit` is an annual, per-member cap on the net benefit (after network discount and co-pay) for the category's own service lines.

- For consultation, the service lines are consultation-fee lines, recognised by an interpretation table (consultation, consultation fee, doctor fee, OPD fee, visit fee and similar). Tests and medicines billed with a consultation are not consultation services, so they fall under the per-claim ceiling only. They are recognised by a second table (test, lab, CBC, scan, medicine, tablet and similar); the rule fails safe, so an eligible line matching neither table (for example "Doctor visit charges") counts against the sub-limit. Every other category treats all its eligible lines as its service (`service_scope: all_eligible_lines`).
- This claim's own service benefit is a certain lower bound on the year's usage, so the cap is **always** applied to it. Prior usage, when supplied, reduces what remains. The engine reads prior usage from the most precise input available:
  1. `category_sub_limit_used`: exact. The sum of earlier decisions' `counted_against_sub_limit_paise` (from their `category_sub_limit` trace step).
  2. `category_ytd_claims_amount`: an upper bound. All earlier net benefit paid to this member in this category. For consultation it is conservative, because it also counts the test and medicine share.
  3. Neither: the history part is `NOT_EVALUATED`. A payable outcome carries the advisory `CATEGORY_SUB_LIMIT_HISTORY_NOT_EVALUATED`, and confidence drops by 0.03. The claim is still capped at the full sub-limit on its own.
- Excess over the remaining sub-limit is removed as a ledger adjustment ("Consultation sub-limit", rule id `category_sub_limit`) with reason `CATEGORY_SUB_LIMIT_LIMITED`, and the outcome is `PARTIAL`.
- If the service share cannot be established because a bill is not itemized, and the net payable exceeds the remaining sub-limit, a claim that could otherwise pay goes to review with `CATEGORY_SUB_LIMIT_UNVERIFIED`. A claim already rejected on other grounds does not carry this reason, since nothing is paid.
- A matched pre-authorization rule can satisfy the authorization requirement and govern an explicitly authorized amount, but it never removes the category cap. Category, annual, sum-insured, and family limits are applied after pricing whether or not pre-authorization exists.
- The trace step records the sub-limit, the usage key and its basis, the amount remaining before this claim, the service net payable, and `counted_against_sub_limit_paise`.

**Web intake** supplies both usage figures from the atomic benefit reservation ledger, for the same member, the same category and the policy year, counting `RESERVED` and `PAID` benefit but excluding `RELEASED` benefit. `category_sub_limit_used` is the sum reserved against the category. Legacy payable rows without a reservation retain a compatibility fallback to their decision trace.

**Why not a plain annual aggregate on the whole claim.** TC010 pays ₹3,240 in a single consultation claim, which is above an annual ₹2,000 consultation cap on everything billed. That reading would break the fixture, or it would pay more when history is absent than when history is zero. Capping only the consultation-fee lines is consistent with every supplied case: TC004's fee nets ₹900 and TC010's nets ₹1,080, both within ₹2,000, so neither amount changes.

**Confirm with the policy owner:** whether sub-limits are annual or per claim, per member or per family, and which services the consultation sub-limit covers.

### 3. Benefit order

Audit id: `BENEFIT_ORDER`.

1. Line eligibility (exclusions, covered lists; rule 5).
2. Per-claim ceiling on the eligible gross amount. This rejects; it does not cap (rule 1).
3. Pre-authorized amount cap, when an approval record states one.
4. Network discount.
5. Co-pay on the discounted amount (and branded-medicine co-pay where the category has one).
6. Caps on the resulting **net payable**, in order: category sub-limit (rule 2), remaining annual OPD limit, remaining sum insured, remaining family floater.

Each step writes a ledger adjustment with its policy reference. Limit reasons (`*_LIMITED`) explain an amount, so they are listed as reasons only on payable outcomes, and on a `REJECTED` / `NO_PAYABLE_AMOUNT` outcome where the caps reduced payment to zero. On other rejections and reviews they stay in the ledger and trace.

**Confirm:** whether annual caps apply before or after co-pay.

### 4. Pre-authorization

Audit ids: `PRE_AUTH_PARSED.*`, `PRE_AUTH_THRESHOLD_CONFLICT.pet_scan`, `PRE_AUTH_MATCH_SCOPE`.

**Rule.** The free-text `pre_authorization.required_for` entries are parsed into `{item, amount_greater_than}`. For example, "MRI scan (amount > ₹10,000)" becomes MRI above ₹10,000. A qualifier the parser cannot read fails validation instead of being ignored. The diagnostic `high_value_tests_requiring_pre_auth` list and `pre_auth_threshold` are merged into the same rules. The amount tested is the sum of itemized bill lines that match the item, or the claimed amount if no line matches.

**Matching scope.** Pre-auth rules match only the services in the claim: treatment, ordered tests, test names and bill lines. They never match the diagnosis, and they ignore negated mentions (rule 9). Single-word short forms of four letters or fewer (MRI, CT, PET) match only inside an ordered test, a test name or a bill line, and "PET" also needs an imaging word (scan, CT, imaging, tomography) in the same entry. So "Dog bite from pet" and "Fever; CT not required" do not require pre-authorization.

When pre-authorization is required, the evidence decides the outcome:

| Evidence | Outcome |
| --- | --- |
| Dated approval record (uploaded document with reference and issue date) | Checked against `validity_days`: pass, or `PRE_AUTH_INVALID` (reject) |
| Form says not obtained, but a dated record exists | `PRE_AUTH_CONFLICT`, review |
| Form says obtained, or a document exists, without both date and reference | `PRE_AUTH_STATUS_UNKNOWN`, review |
| No record, no reference and no claim of approval | `PRE_AUTH_MISSING`, reject, with instructions to obtain pre-auth and resubmit the approval record |

TC007 supplies no pre-authorization information at all and expects `PRE_AUTH_MISSING`. The missing-record reading therefore applies to every source, including uploads.

**Provenance of the record.** Evidence carries `verification_status: DOCUMENT_PRESENT | INSURER_VERIFIED | NOT_AVAILABLE`. The web upload path produces `DOCUMENT_PRESENT`; its trace records `status_source: member_supplied_record_unverified_with_insurer` and `insurer_verified: false`. A pass adds the advisory `PRE_AUTH_NOT_VERIFIED_WITH_INSURER` to a payable outcome and lowers confidence by 0.05. Only an authoritative insurer/TPA integration may supply `INSURER_VERIFIED`, which removes that advisory.

**Contradiction.** The global list says PET scan always needs pre-auth. The diagnostic threshold implies it is needed only above ₹10,000. The engine applies the stricter reading (always).

**Confirm with the policy owner:** the PET threshold, whether a member's claim of approval without the approval record should reject or go to review, and how approvals are verified with the insurer.

### 5. Line items

Audit ids: `COVERED_ITEM_TERMS.<CATEGORY>.<item>`, `EXCLUSION_TERMS.*`.

Each bill line gets one status in the ledger:

| Line | Status and code | Effect |
| --- | --- | --- |
| Matches a definite exclusion | `EXCLUDED`, `EXCLUDED_PROCEDURE` | Removed; claim can be `PARTIAL` |
| Matches a qualified exclusion (vaccination) | `UNRESOLVED`, `EXCLUSION_QUALIFIER_REVIEW` | Review |
| On an allow-listed category (dental, vision), matches another category's covered list | `NOT_COVERED`, `NOT_ON_ALLOWLIST` | Removed; claim can be `PARTIAL` |
| On an allow-listed category, matches neither the covered list nor an exclusion | `UNRESOLVED`, `LINE_ITEM_UNRESOLVED` | Review |
| No readable description | `UNKNOWN`, `LINE_ITEM_DESCRIPTION_UNKNOWN` | Review |
| Otherwise | `ELIGIBLE` | Counted in the eligible amount |

- Covered-item terms add common billing names and abbreviations to each listed item, matched on word boundaries: for example root canal, RCT and endodontic treatment for Root Canal Treatment; IOPA, OPG and x-ray for Dental X-Ray; spectacles for Glasses; eye test for Eye Examination.
- A string miss is never a confident rejection. An unrecognised line goes to a reviewer.
- **Unitemized bills.** A bill with a total but no lines contributes one line, "Bill total (not itemized)", whose amount is the bill's own printed total, never the claimed amount. On an allow-listed category it is `UNRESOLVED`. A bill with neither a total nor lines contributes nothing (see rule 11, `BILL_AMOUNT_UNVERIFIED`).
- **Non-payable outcomes.** Lines must not read as covered when nothing is paid. With a claim-level exclusion, every line becomes `EXCLUDED` (`EXCLUDED_CONDITION`, "Excluded with the whole claim …"), and `line_check` keeps the line-level result. On any other rejection or review, `ELIGIBLE` lines become `NOT_ADJUDICATED` with `line_check: ELIGIBLE`.

**Confirm:** the covered-item vocabulary, and whether items outside the list should be rejected rather than reviewed.

### 6. Dental report

Audit id: `DENTAL_REPORT_CONFLICT.DENTAL`.

`opd_categories.dental.requires_dental_report` is `true`, but `document_requirements.DENTAL` lists `DENTAL_REPORT` as optional. TC006 has no dental report and still expects a decision. The document matrix governs the correction gate. When the report is absent, the trace records `advisory_document` as `ADVISORY`, a payable outcome carries `ADVISORY_DOCUMENT_ABSENT`, and confidence drops by 0.03.

**Confirm:** whether a dental report is ever required, for example above an amount.

### 7. Roster references

Audit ids: `DANGLING_DEPENDENT.*` (kind `reference_repair`), `DEPENDENT_JOIN_DATE.*`, `RELATIONSHIP_VOCABULARY.*`.

- EMP003 → DEP003, EMP007 → DEP004 and DEP005, and EMP010 → DEP006 are listed as dependents but are not in `members`. The file is left as it is. The normalizer keeps them as `unresolved_dependents`, and they are not used to match covered patient names. A claim filed under one of those IDs is an unknown member and goes to `MANUAL_REVIEW` (`MEMBERSHIP_OR_POLICY_UNKNOWN`). The roster trace lists the unresolved dependents.
- DEP001 and DEP002 have no `join_date`, so they inherit the primary member's. The waiting-period trace shows `join_date_source`.
- The roster uses `CHILD`, but the family floater uses `CHILDREN`. A vocabulary table maps singular relationships to the floater's plural forms.

**Confirm:** the missing dependents' records, and dependents' join dates.

### 8. Aggregate limits when utilisation is absent

Audit id: `AGGREGATE_LIMITS_NEED_UTILISATION`.

Annual OPD limit, sum insured, family floater, category sub-limit history and annual session limits depend on other claims. When utilisation comes with the claim (`ytd_claims_amount`, `sum_insured_used`, `family_floater_used`, `category_sub_limit_used` or `category_ytd_claims_amount`, `prior_sessions`), the limit is applied to the net payable. A capped payment becomes `PARTIAL`; a payment capped to zero is `REJECTED` with `NO_PAYABLE_AMOUNT`. When utilisation is absent:

- the trace rule is `NOT_EVALUATED`, never `PASS`;
- a payable outcome carries an advisory reason, for example `ANNUAL_LIMIT_NOT_EVALUATED` ("subject to the member's remaining annual OPD balance"), and confidence drops (−0.04 for annual OPD, −0.03 for category history, −0.03 for sessions);
- the decision is not blocked, because these limits can only reduce payment. On a rejection the unknown doesn't matter.

A single claim whose own sessions exceed the annual session cap is rejected with `SESSION_LIMIT_EXCEEDED` without history. TC011 has no year-to-date figure and is expected to be `APPROVED`, which rules out routing absent usage to review. The web intake always supplies year-to-date, category, session and history figures from its local claim records, so live claims are always evaluated against them. Those figures are approved amounts, which stand in for remittance data the prototype does not have.

**Confirm:** the authoritative utilisation feed, and whether payment should wait for it.

### 9. Exclusions and negation

Audit ids: `EXCLUSION_MERGED.*`, `EXCLUSION_TERMS.*`.

Exclusions appear in three places: `exclusions.conditions` (the whole claim and every line), `exclusions.dental_exclusions` and `vision_exclusions`, and the categories' own `excluded_procedures` and `excluded_items`. They are merged into one list per scope. Two entries merge when one label, without its parenthetical, contains the other. Four entries merge (for example "LASIK" and "LASIK Surgery"), and each merge is audited.

- A claim-level hit on diagnosis, treatment or test name gives `EXCLUDED_CONDITION`, which rejects the claim.
- A line-level hit gives `EXCLUDED_PROCEDURE`: the line is removed, and the ledger records the reason and the policy reference.
- Parenthetical labels only add their interpreted terms, so an exclusion is never widened. "Implants (Cosmetic)" matches cosmetic implants, not every implant.
- On dental lines, "Cosmetic dental procedures" also matches cosmetic, aesthetic and upgrade (for example "Gold crown upgrade"), and "Teeth whitening" also matches "whitening".
- "Vaccination (non-medically necessary)" has a clinical qualifier that documents cannot settle, so a hit goes to review (`EXCLUSION_QUALIFIER_REVIEW`).
- **Negation.** Exclusions, waiting-period conditions, pre-existing conditions and pre-auth items use a negation-aware matcher: a mention preceded closely by "no", "not", "without", "denies" or "negative for" does not match. "No history of substance abuse" is not an exclusion hit, and "No history of diabetes; viral fever" gets only the initial waiting period.

### 10. Other normalizations

- **Waiting-period synonyms** (`CONDITION_TERMS.*`): for example T2DM → diabetes and HTN → hypertension. Matching respects word boundaries, so "Herniation" does not trigger the hernia waiting period.
- **Network hospitals** (`NETWORK_NAME_VARIANTS.*`, `NETWORK_MATCH_RULE`): printed variants such as "Apollo Hospital" are recognized. A name matches exactly, or before a comma or dash branch suffix ("Apollo Hospitals, Bengaluru"). The trace shows the matched term.
- **Covered AYUSH systems** (`COVERED_SYSTEM_TERMS.*`): therapy and practitioner indicators such as panchakarma or vaidya for Ayurveda. With no indicator, the result is `COVERED_SYSTEM_UNKNOWN` and the claim goes to review.
- **Pharmacy brand status** (`BRAND_CLASSIFICATION.PHARMACY`): `branded_drug_copay_percent` needs each line to be classified BRANDED or GENERIC from printed text. An unclassified line gives `PHARMACY_BRAND_STATUS_UNKNOWN` (review). Under `generic_mandatory`, a branded line gives `GENERIC_SUBSTITUTION_REVIEW`.
- **Prescription requirement** (`PRESCRIPTION_REQUIREMENT.<CATEGORY>`): each category's `requires_prescription` flag is enforced through the document matrix; if the two disagree, the stricter reading (required) wins. In the supplied file all six categories agree.
- **Fraud thresholds** (`FRAUD_THRESHOLD_ROLES`): `auto_manual_review_above` routes a single claim to review. `high_value_claim_threshold` marks a single claim as high value in the trace and bounds the family's trailing 30-day claimed value (rule 12).
- **Currency and informational fields** (`SUBMISSION_CURRENCY`, `INFORMATIONAL_FIELD.*`): only INR is accepted. `policy_name`, `insurer`, `company_name` and `employee_count` carry no claim rule and are recorded as informational, so no supplied field is silently ignored.
- **Absent optional fields** (`NO_NETWORK_DISCOUNT.*`, `CATEGORY_PRE_AUTH_FLAG_ABSENT.*`): a category without a network discount gets 0%, and a category without a pre-auth flag gets `false`. Item-level pre-auth rules still apply. Each default is audited.

### 11. Evidence gaps (one rule for every source)

Earlier versions exempted fixture evidence from several gates. Those exemptions are gone. Each gap has one rule for every source (fixture metadata, PDF text, OCR, model candidate), and a test runs all 12 cases under four source labels and asserts identical results. The engine does not pay on evidence it has not established.

| Gap | Rule |
| --- | --- |
| No bill total and no line items | `BILL_AMOUNT_UNVERIFIED`, review; no lines enter the ledger |
| A required document whose quality is not good, clear or readable | `DOCUMENT_QUALITY_INSUFFICIENT`, review (a weak optional document only lowers confidence) |
| No patient name on the documents | A missing name cannot contradict the member, so the claim is attributed to the member and `patient_identity` is `NOT_EVALUATED`. A payable outcome carries `PATIENT_IDENTITY_NOT_VERIFIED`. This stays advisory because TC007, TC008, TC009, TC011 and TC012 carry no patient names and TC011 must be approved. A name that is present and not on the roster, or two different names, still stops the claim. |
| Document layer's identity verdict | Optional input `identity_verification` (`VERIFIED`, `NOT_AVAILABLE`, `UNVERIFIED`, `FAILED`). `UNVERIFIED` or `FAILED` gives `PATIENT_IDENTITY_UNVERIFIED`, review. Absent means not supplied and is traced `NOT_EVALUATED`. The web intake does not supply it yet. |
| No readable document date | Advisory `TREATMENT_DATE_NOT_CORROBORATED`; the timing rules use the submitted treatment date. A document date that disagrees with it, or cannot be parsed, gives `DOCUMENT_DATE_CONFLICT`, review. |
| No practitioner registration where the category requires one | `PRACTITIONER_REGISTRATION_UNKNOWN`, review |
| Invalid input (`fraud_score` not a finite number in [0, 1], `prior_sessions` not a whole number, `claims_history` not a list of objects, a non-INR `currency`, a negative or non-numeric usage amount, an unknown `identity_verification`) | `MALFORMED_EVIDENCE`, review, with the offending `field` in the trace |
| No prior-session or usage history | See rule 8 |

At web intake, a bill must also carry a readable printed date: a missing or unparseable bill date is a member correction (`MATERIAL_FIELD_UNVERIFIED`, field `date`) before adjudication. The supplied cases carry structured metadata, so for them an absent date is only the advisory above.

### 12. Risk-signal enrichment

`claims.core.risk_signal_enrichment` is a real component that runs by default on every claim. It computes two supplementary signals from the family's claim history:

- `trailing_30_day_value`: this claim plus the family's claims in the 30 days up to the treatment date, against `high_value_claim_threshold`. It flags only when earlier claims contribute; a single large claim is already handled by `auto_manual_review_above`.
- `repeat_billing`: an earlier claim from the same provider for the same amount on the same treatment date (a second bill for one visit). Follow-up visits on other days are ordinary, and a re-submitted bill is caught earlier by the duplicate-bill check.

A flag routes the claim to review (`RISK_SIGNAL_REVIEW`). A signal is `NOT_EVALUATED` when history items lack `amount` or `provider`; the web intake supplies both.

If the component raises or returns malformed output, adjudication continues without it. The trace records `SKIPPED_COMPONENT_FAILURE`, the claim carries `COMPONENT_DEGRADED` ("… processing was incomplete, so a manual review of this decision is recommended"), the outcome step records `post_decision_review_recommended: true`, and confidence drops by 0.23. The policy's own fraud thresholds (same-day count, monthly count, auto-review amount, fraud score) are separate, mandatory checks in `claims.core` and never depend on this component.

## Confidence rubric

Confidence is a heuristic evidence-completeness score. It is **not a calibrated probability** and is not an auto-pay threshold. The base is 0.96. A factor is deducted only when resolving it could change the outcome reached. Outcomes fall into classes: payable, review, rejection, and three kinds of rejection that depend on a specific fact:

- identity-dependent: `WAITING_PERIOD`, `PRE_EXISTING_WAITING_PERIOD`, `RELATIONSHIP_NOT_COVERED`, `SESSION_LIMIT_EXCEEDED`;
- date-dependent: `WAITING_PERIOD`, `PRE_EXISTING_WAITING_PERIOD`, `OUTSIDE_POLICY_PERIOD`, `SUBMISSION_LATE`, `SUBMISSION_BEFORE_TREATMENT`;
- amount-dependent: `PER_CLAIM_EXCEEDED`, `MINIMUM_CLAIM_AMOUNT`, `PRE_AUTH_MISSING`, `NO_PAYABLE_AMOUNT`.

The class is taken from the primary rejection reason.

| Factor | Deduction | Applies to |
| --- | ---: | --- |
| Weak document quality | 0.10 | payable, review, rejection |
| Bill amount unavailable | 0.08 | payable, review, amount-dependent rejection |
| Risk-signal component failure | 0.23 | payable, review, rejection |
| Patient name unavailable | 0.04 | payable, review, identity-dependent rejection |
| Treatment date not corroborated by a document | 0.03 | payable, date-dependent rejection |
| Annual OPD usage not evaluated | 0.04 | payable |
| Category sub-limit usage not evaluated | 0.03 | payable |
| Session history not evaluated | 0.03 | payable |
| Advisory document absent | 0.03 | payable |
| Pre-authorization record member-supplied | 0.05 | payable |
| Line exclusion matched only through interpretation terms | 0.06 | payable |
| Claim exclusion matched only through interpretation terms | 0.06 | rejection |

For example, TC012 is rejected on a policy-text exclusion ("obesity") in the treatment itself. The same rejection would hold for any covered family member, so its missing patient name is immaterial and nothing is deducted. A waiting-period rejection without a patient name is lowered, because the patient's enrolment decides it.

Early returns use fixed values: document correction 0.9, amount mismatch 0.75, unknown member 0.4, invalid amount, malformed input or invalid policy 0.0. The `confidence/confidence_rubric` trace step lists the outcome classes, every factor and whether it was applied. The fixture constrains the scores only loosely: TC004 must be above 0.85, TC012 above 0.90, and TC011 lower than the same claim with every component working. The actual per-case scores are in the [verification summary](../reports/verification-summary.md). Calibration needs labelled outcomes that this prototype does not have.

## How the supplied cases are checked

`scripts/evaluate.py` runs the unmodified fixture and compares each case on decision, approved amount, rejection codes and confidence. Where a case expects rejection reasons, the primary reason in the outcome trace **and** the first listed reason must both equal the first expected reason; containing the code somewhere is not enough. It also checks the concrete behavior behind each case's `system_must` prose. For example, TC008's `PER_CLAIM_EXCEEDED` message must state both the claimed amount and the policy's per-claim limit as rupee amounts. TC011 is compared with its own run with the component working, not with another case, and it must carry `post_decision_review_recommended`.

## What is not special-cased

- `claims/` contains no case IDs. `claims.fixtures.normalize_fixture` drops `case_id`, `expected` and `simulate_component_failure` before the evaluator sees the claim.
- The only case-specific code is in the test harness. For TC011, whose input requests a component failure, `scripts/evaluate.py` replaces the default risk-signal enrichment with one that raises. It does not skip or stub anything else. The harness also runs the per-case behavior checks described above. They read expected values from the fixture and the raw policy rather than hard-coding them.
- The interpretation tables are general vocabulary (synonyms, billing names, name variants, relationship forms, service terms). They were written to cover the policy's own terms, and some entries (such as panchakarma and Apollo Hospital) are also the ones the supplied cases use. Any new vocabulary should be added to the tables with an audit entry, never as a branch in `claims/core.py`.

## Production interpretation register

Before automatic insurer payment, move every business interpretation from this repository's audit trail into an insurer-owned, versioned interpretation register. Each entry should identify the policy/version and effective dates, source clause, proposed deterministic rule or vocabulary, owner approval, test cases, and supersession history. A claim must persist the register version used for its decision; an unapproved or ambiguous entry routes to review. This prevents an engineering convenience or OCR vocabulary update from silently becoming an insurer coverage decision.
