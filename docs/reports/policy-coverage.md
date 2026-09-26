# Policy coverage assessment

This is the current implementation map for [`data/policy_terms.json`](../../data/policy_terms.json). A rule is not labelled passed merely because the evaluator lacks its required evidence.

| Policy area | Current handling | Evidence boundary |
| --- | --- | --- |
| Policy identity, dates, renewal | Deterministic membership, policy-period, and active-renewal checks | Intake persists a policy hash; a changed policy routes to review |
| Covered categories, relationships, document matrix | Deterministic category/relationship checks and early document gate | Required document types are enforced before adjudication |
| Per-claim, category, annual OPD caps | Integer-paise calculations and itemized ledger | Annual use comes from local approved/partial claim history |
| Sum insured and family floater | Evaluated when an explicit utilisation feed is supplied; trace shows remaining balance | This OPD prototype does not fabricate hospitalisation or remittance history |
| Waiting periods, exclusions, dental/vision allowlists | Deterministic phrase/alias checks with recorded policy references | Clinical evidence must be present in normalized document facts |
| Pre-authorization | Required treatment triggers, uploaded approval type, reference/date, and configured validity window | A self-attested form value cannot satisfy the rule without an approval document |
| Network discounts and co-pay | Deterministic, itemized calculation; discount precedes co-pay | Provider identity must be extracted or supplied |
| Pharmacy branded/generic rule | Branded status requires printed evidence; mandatory-generic branded claims are manual review | No inferred brand substitutions |
| Submission deadline and minimum amount | Deterministic treatment/submission date and amount checks | Web intake persists the server-side submission date |
| Same-day/monthly/high-value/fraud-score thresholds | Deterministic review signals and trace entries | Fraud score is evaluated only when an upstream scorer supplies one |

## What this assessment does not claim

The repository does not claim provider OCR accuracy, handwriting understanding, multilingual extraction quality, fraud-model accuracy, or insurer-remittance integration. When those sources are unavailable, the trace records `NOT_EVALUATED` or routes to review according to the affected policy rule; it does not represent missing evidence as a pass.
