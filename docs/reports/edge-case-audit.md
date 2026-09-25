# Edge-case audit

## Scope and method

This is a failure-mode inventory, not a remediation plan. It covers claim intake, the document gate, Sarvam and Gemini handoffs, deterministic policy interpretation, persisted traces, and the fixture evaluation.

The first audit baseline had `79` passing tests and `20` passing subtests. After the specialist-handoff changes and failure-injection hardening, the latest full recheck had `95` passing tests and `20` passing subtests. The findings below record behavior at audit time and retain fixed cases as regression history. “Verified” means reproduced at audit time or demonstrated by a direct, reachable code path. “Hypothesized” means the code makes the failure plausible, but it was not reproduced end to end in this audit.

Priority meanings:

- **P0** — can produce a wrong financial or coverage outcome.
- **P1** — can block a valid claim, hide why a result occurred, or materially misstate evaluation quality.
- **P2** — operational or member pain with a narrower trigger.

## Version-1 handoff boundary status

| Boundary | Initial injected failure | Current status | Evidence of current status |
| --- | --- | --- | --- |
| Candidate citation/value binding | ₹9,000 total cited only a patient-name quote and was applied | **Fixed** | Same payload now abstains; `test_handoff_rejects_amount_with_unrelated_source_quote` covers the amount case |
| Resolver trace/metric persistence | Unknown keys carried raw secret text through the handoff | **Fixed** | Trace is reconstructed from safe fields and metrics are allowlisted; `test_handoff_discards_untrusted_trace_and_metrics_fields` |
| Decision top-level contract | Extra fields, scalar nested items, empty explanations, and boolean confidence passed | **Fixed at this level** | Exact field set, dictionary elements, non-empty reason/trace, and finite non-boolean confidence are required; `test_approved_result_requires_explanation_and_excludes_extra_fields` |
| Correction presence | A null decision with no correction request passed | **Fixed at this level** | At least one entry is required; `test_correction_requires_actionable_request` |
| Persisted evidence field size | A 200,000-character field persisted in full | **Fixed for reproduced input** | Direct recheck truncates it to 500 characters and bounds nested depth/count |
| Decision nested schemas | Placeholder dictionaries and ledger values were accepted without required-key or arithmetic checks | **Fixed** | Required reason/trace fields and a reconciled payable ledger are enforced by `test_payable_decision_requires_reconciled_ledger` |
| Correction entry schema | A scalar list entry satisfied the non-empty correction requirement | **Fixed** | Correction requests now require nonempty `{code,message}` entries; `test_correction_rejects_scalar_request_entries` |
| Total artifact size | Per-value bounds exist, but no total serialized-byte budget is enforced | **Remaining** | V29 aggregate-size caveat |

## Verified risks

### V1 — Only the first itemized document is priced (P0; fixed after audit)

- **Area:** policy calculation
- **Exact trigger:** A claim has two bills. Both totals and both item lists reconcile to the claimed amount, but each bill contains part of the expense.
- **Observed result:** A consultation with bills for ₹1,500 and ₹500, claimed at ₹2,000, was approved for ₹1,350. Reconciliation summed both bills to ₹2,000, while `_line_items()` priced only the first bill's ₹1,500 and then applied the 10% co-pay.
- **Impact:** A valid multi-bill claim can be underpaid. Document reconciliation says the evidence is complete, which makes the wrong amount look trustworthy.
- **How to detect:** Submit two distinct, internally consistent bills and compare `reconciliation.bill_amount.line_items_total_paise` with the sum of `ledger` line items. They diverge.
- **Current handling:** Fixed: the reducer now collects item lines from every bill, with a two-bill regression test. The observed failure above describes the audit baseline.

### V2 — Configured coverage allowlists and usage constraints are not adjudicated (P0)

- **Area:** policy interpretation
- **Exact trigger:** A line item is absent from a positive coverage list but also absent from an exclusion list, or an alternative-medicine claim violates practitioner/system/session constraints.
- **Observed result:** The following direct policy evaluations all returned `APPROVED`: dental “Diamond Tooth Jewellery”; vision “Designer Sunglasses”; and alternative medicine “Acupuncture, 25 sessions.”
- **Impact:** The system can pay benefits that the policy does not affirmatively cover. It can also approve more sessions than allowed or treatment by an unverified practitioner.
- **How to detect:** For each configured allowlist or constraint, submit a claim that violates only that rule and look for a trace step referencing the corresponding policy key.
- **Current handling:** Not handled. `covered_procedures`, `covered_items`, `covered_systems`, `requires_registered_practitioner`, and `max_sessions_per_year` exist in `policy_terms.json:55-86`; the evaluator only checks category-specific exclusion lists.

### V3 — Several policy-level coverage controls are loaded but never applied (P0)

- **Area:** policy interpretation
- **Exact trigger:** A claim depends on the 365-day pre-existing-condition wait, family floater combined limit, per-employee sum insured, relationship allowlist, category `covered` flag, or policy `renewal_status`.
- **Impact:** A claim can be evaluated without rules that may change eligibility or available benefit. The trace gives no indication that these configured rules were skipped.
- **How to detect:** Change one of these values in a copied policy, rerun an otherwise identical claim, and compare both decision and trace. No trace rule references these keys.
- **Current handling:** Not handled. These fields are present in `policy_terms.json`, but there are no corresponding evaluator branches.

### V4 — Document dates are not reconciled with the submitted treatment date (P0)

- **Area:** document gate / policy handoff
- **Exact trigger:** All documents contain a readable date that differs from the submitted treatment date.
- **Observed result:** A consultation dated `2024-11-01` remained `APPROVED` when both extracted document dates were changed to `2024-05-01`.
- **Impact:** Documents from another episode can support a claim. Waiting periods, policy-period checks, history counts, and submission timing all use the form date while ignoring contradictory document dates.
- **How to detect:** Alter only extracted `date` fields and confirm that no reconciliation or identity trace step changes.
- **Current handling:** Not handled. Extracted dates are collected, but `_evaluate_claim()` never compares them with `payload.treatment_date`.

### V5 — Missing or temporally impossible dates can pass the core evaluator (P0)

- **Area:** claim intake / policy interpretation
- **Exact trigger:** Call the public deterministic evaluator without `treatment_date`, or set `submission_date` before `treatment_date`.
- **Observed result:** Both claims returned `APPROVED` for ₹1,350 with reason `COVERED`.
- **Impact:** Waiting-period, policy-period, fraud-period, and submission-deadline controls can be bypassed by malformed upstream payloads. A negative submission age is treated as within deadline.
- **How to detect:** Remove `treatment_date`, then separately set a pre-treatment `submission_date`, and inspect the decision plus `NOT_EVALUATED` trace steps.
- **Current handling:** Partially handled. The web form validates the treatment date, but the evaluator accepts missing dates; the deadline comparison only checks `age > deadline` in `claims/core.py:313-329`.

### V6 — The live intake can never evaluate the submission deadline (P1)

- **Area:** claim intake
- **Exact trigger:** Submit any claim through the UI/API.
- **Impact:** The configured 30-day submission deadline is always `NOT_EVALUATED`, even for clearly late claims.
- **How to detect:** Inspect the saved request and the `submission_deadline` trace step for any web-submitted claim.
- **Current handling:** Not handled. `/api/claims` accepts member, category, treatment date, amount, and files only (`claims/web.py:545-600`); it never records `submission_date`.

### V7 — Pre-authorization cannot be supplied through live intake (P1)

- **Area:** claim intake / policy interpretation
- **Exact trigger:** Submit a live MRI over ₹10,000, PET scan, or another pre-authorization-triggering claim.
- **Impact:** A member with valid pre-authorization cannot provide it, and a member without it cannot explicitly attest that it was not obtained. The live path can only reach `PRE_AUTH_STATUS_UNKNOWN` and manual review for this rule.
- **How to detect:** Search the HTML form and API parameters for a pre-authorization field, then submit a triggering claim and inspect the trace.
- **Current handling:** Safe but incomplete. Missing live status routes to manual review. Explicit `true`/`false` is supported by the core evaluator but is unreachable from the current UI/API.

### V8 — Policy contradictions are resolved only by fixture-only flags (P1)

- **Area:** policy interpretation / eval parity
- **Exact trigger:** Process TC006 or TC010 semantics through the live path rather than `normalize_fixture()`.
- **Impact:** The showcased expected outcomes do not describe default live behavior. A ₹12,000 dental claim is rejected by the global ₹5,000 cap and lacks a required dental report; a ₹4,500 mixed consultation bill is capped as a whole at the ₹2,000 consultation sub-limit.
- **How to detect:** Remove `fixture_compatibility_assumptions` and `fixture_sub_limit_item_phrase` from TC006/TC010 and compare the result.
- **Current handling:** Explicitly exposed as `ASSUMPTION` in the fixture trace, but no live intake field or settled policy interpretation exists. The policy also marks `DENTAL_REPORT` both required (`policy_terms.json:53`) and optional (`policy_terms.json:192`).

### V9 — A structured-extraction outage can be blamed on the member's document (P1)

- **Area:** Sarvam handoff / document gate
- **Exact trigger:** A digital PDF is locally classified, is missing a material field, and the provider's `extract_fields()` call times out or fails.
- **Observed result:** A readable hospital bill with a locally extracted total but no item lines produced `provider_failures=1` and only `MATERIAL_FIELD_UNVERIFIED`; it did not produce `EXTRACTION_UNAVAILABLE`.
- **Impact:** The web path routes the claim to `DOCUMENT_CORRECTION_REQUIRED` and asks the member for a clearer document even though the observed failure was the extraction service.
- **How to detect:** Inject a provider exception after successful local PDF parsing; compare `metrics.provider_failures` with issue codes and final state.
- **Current handling:** Not handled for partial provider failures. Provider outages route to operator review only when the document source remains entirely unavailable.

### V10 — The evidence model cannot rescue evidence that OCR did not cue or transcribe (P1)

- **Area:** Gemini handoff
- **Exact trigger:** OCR misses the relevant label/value on a visually readable page, or transcribes it differently from the image.
- **Observed result:** `build_trigger()` returned `relevant_page_unavailable` and made zero model calls when a bill was missing total/lines but OCR text had no money cues.
- **Impact:** The vision-capable fallback is blocked in many of the cases where vision would be valuable. Even after a call, every accepted quote must be an exact normalized substring of OCR text, so visual correction of an OCR error cannot be accepted.
- **How to detect:** Supply a document record with missing material fields and OCR text without the field cue; inspect Gemini status, call count, and abstain reason.
- **Current handling:** Fail-closed. `claims/ai_review.py:243-324` requires OCR cue-based page selection, and `claims/ai_review.py:398-410` validates quotes against OCR text.

### V11 — The AI correction pass is all-or-nothing across files and fields (P2)

- **Area:** Gemini handoff
- **Exact trigger:** One requested field has valid evidence while another field or another file returns an unsupported quote, schema discrepancy, or conflict.
- **Impact:** All validated candidates are discarded, including independently supported facts. The member can receive the same full correction list after a partially successful evidence pass.
- **How to detect:** Return one valid candidate and one invalid candidate in the same bounded request; status becomes `ABSTAINED` with no candidates.
- **Current handling:** Deliberate fail-closed behavior. There is no partial candidate result.

### V12 — Extraction evidence needed for reconstruction is not persisted (P1; partly fixed after audit)

- **Area:** trace / observability
- **Exact trigger:** Review any live claim after processing, especially one based on Sarvam or local PDF parsing.
- **Impact:** An operator cannot reconstruct the exact extracted diagnosis, treatment, date, doctor details, or source snippets from the saved claim. The original files remain, but reproducing the result requires rerunning mutable extraction components.
- **How to detect:** Compare the in-memory `inspection.documents[*].content/evidence` with the saved API result. The saved document rows contain filename, media type, size, and hash only; the inspection object itself is not stored.
- **Current handling:** A bounded `document_evidence` trace now persists extracted facts and source snippets for decided live claims, before Gemini and policy steps. Raw OCR remains ephemeral. Correction and provider-failure paths still need the same provenance coverage.

### V13 — Applied Gemini evidence appears after the final decision in the trace (P1; fixed after audit)

- **Area:** trace / observability
- **Exact trigger:** Gemini validates and applies candidates, after which deterministic adjudication succeeds.
- **Impact:** The persisted trace reads as though the decision happened before the evidence that caused it. This makes sequence reconstruction misleading.
- **How to detect:** Inspect `result.trace` for a claim with `CANDIDATES_APPLIED`; the Gemini step is the last element, after `decision.outcome`.
- **Current handling:** Fixed: the saved trace now places the Gemini evidence step before the policy result, and the web test checks that order. The observed failure above describes the audit baseline.

### V14 — Correction and decision results use incompatible reason shapes (P2)

- **Area:** component contract / UI integration
- **Exact trigger:** Compare a document-correction result with a decided or manual-review result.
- **Impact:** `reasons` is a list of strings for document correction and a list of `{code, message}` objects for adjudication. Consumers must branch on runtime type, and integrations assuming one schema can fail.
- **How to detect:** Fetch one `DOCUMENT_CORRECTION_REQUIRED` claim and one `DECIDED` claim from the API and inspect `result.reasons`.
- **Current handling:** The bundled UI tolerates the current responses, but the public result contract is not uniform.

### V15 — The eval pass count ignores most assignment “system_must” requirements (P1; partly fixed after audit)

- **Area:** evaluation
- **Exact trigger:** Change or remove an actionable message, trace detail, line-item explanation, graceful-degradation note, or calculation-order trace while preserving decision, expected amount, reason code, and confidence threshold.
- **Impact:** The evaluator can still report the case as matched even though the case's required behavior is absent. This is most material for TC001-TC003, TC005-TC011.
- **How to detect:** Read `_matches()` in `scripts/evaluate.py:19-38`: it does not inspect `expected.system_must` or `expected.notes`.
- **Current handling:** The generated evaluator now adds explicit structural checks for the documented correction, waiting period, line-item, pre-authorization, limit, risk, pricing-order and degradation behaviors. These checks still cannot establish OCR accuracy or cover every natural-language nuance; the case-level record lists exactly which checks were run.

### V16 — Fixture metadata bypasses parts of the live evidence standard (P1)

- **Area:** evaluation parity
- **Exact trigger:** Run a supplied structured fixture through `normalize_fixture()`.
- **Impact:** Fixture documents skip the live “no readable patient name” stop, allow fixture-only policy assumptions, and can use sparse bill evidence that the upload gate would reject. A 12/12 fixture result therefore does not predict end-to-end document behavior.
- **How to detect:** Compare the same normalized claim with document `source="fixture_metadata"` and `source="uploaded_file"`.
- **Current handling:** Disclosed in the eval report, but the headline pass count and generated decisions still combine fixture compatibility with core policy correctness.

### V17 — Policy-at-intake is hashed but not bound to adjudication (P1)

- **Area:** claim intake / trace
- **Exact trigger:** Change `policy_terms.json` after a claim is queued but before `process_claim()` reads the policy.
- **Impact:** The claim is adjudicated under a different policy from the one hashed in its stored request. Neither the decision trace nor state transition flags the mismatch.
- **How to detect:** Queue a claim, change a material policy value, process it, then compare `request.policy_sha256` with the policy actually used.
- **Current handling:** Not handled. Intake stores a hash (`claims/web.py:597`), but processing rereads the current file and never compares the hashes.

### V18 — Referenced dependents are absent from the member roster (P1)

- **Area:** policy data / intake
- **Exact trigger:** Submit for DEP003, DEP004, DEP005, or DEP006, which are referenced by employee records but have no member records.
- **Impact:** These dependents cannot be selected or validated, and their names cannot be used by identity checks or Gemini's roster allowlist.
- **How to detect:** Compare every `members[*].dependents` value with the set of `members[*].member_id`.
- **Current handling:** Not handled. Four referenced IDs are dangling in `policy_terms.json:239-302`.

### V19 — Ordinary name variation causes a hard member correction (P2)

- **Area:** document identity gate
- **Exact trigger:** OCR adds or omits a middle name/initial, title, or transliteration while all documents agree with one another.
- **Observed result:** Replacing “Rajesh Kumar” with “Rajesh K Kumar” on both clean documents stopped adjudication with `PATIENT_NOT_COVERED`.
- **Impact:** Legitimate members can be forced to re-upload documents even when the discrepancy is a common formatting variation.
- **How to detect:** Run the same claim with punctuation-only, title, middle-initial, reordered-name, and transliteration variants.
- **Current handling:** Only case-folding and non-letter removal are applied; matching is otherwise exact.

### V20 — The image gate does not detect blur (P2)

- **Area:** document gate
- **Exact trigger:** Upload a sufficiently large, high-contrast image whose text is severely blurred.
- **Observed result:** A 1000×1000 image blurred with Gaussian radius 8 returned `(True, None)` from `_image_quality()`.
- **Impact:** The “catch document problems early” gate can pass visibly unreadable photos into paid extraction. The final error may be slower and less specific than an immediate blur correction.
- **How to detect:** Use a small blur corpus at fixed resolution and compare gate result with OCR character recovery.
- **Current handling:** Resolution, total pixels, file validity, and near-blank contrast are checked; sharpness is not.

### V21 — One unreadable/unknown upload cascades into several member corrections (P2)

- **Area:** document gate / UX
- **Exact trigger:** A single uploaded image yields too little OCR text to identify its type.
- **Observed result:** One file produced five issues: unreadable document, unidentified document, missing prescription, missing bill, and patient unverified.
- **Impact:** The member receives overlapping consequences rather than one causal request, obscuring whether the next action is to retake the same photo or add other documents.
- **How to detect:** Feed a valid image container with insufficient recognized text and count issue codes by file.
- **Current handling:** Every derived rule emits independently; there is no causal suppression or prioritization.

### V22 — Provider manual-review claims cannot be retried after recovery (P2)

- **Area:** operations workflow
- **Exact trigger:** A provider outage routes the claim to `MANUAL_REVIEW`, then the provider recovers.
- **Impact:** The existing retry endpoint rejects the retry with HTTP 409, so operators cannot rerun extraction through the product workflow.
- **How to detect:** Create an `EXTRACTION_UNAVAILABLE` manual-review claim, restore the provider, and call `/api/claims/{id}/retry`.
- **Current handling:** Retry is restricted to `PROCESSING_FAILED` in `claims/web.py:648-660`.

### V23 — Duplicate detection only recognizes byte-identical bills (P2)

- **Area:** fraud / duplicate controls
- **Exact trigger:** Resave, recompress, crop, rotate, screenshot, or scan the same bill before resubmission.
- **Impact:** The content-identical expense has a different SHA-256 and bypasses the duplicate review.
- **How to detect:** Submit an approved bill, re-encode it without changing visible content, and submit again.
- **Current handling:** Exact hashes of prior payable bill files are checked. The limited scope is visible in `claims/web.py:348-375`.

### V24 — The handoff accepted quotes unrelated to candidate values (P0; fixed after dry run)

- **Area:** structured evidence handoff
- **Exact trigger:** A resolver returns a schema-valid candidate whose proof quote exists on the page but is unrelated to the candidate value.
- **Observed result:** A hospital-bill candidate proposed a ₹9,000 total and one ₹9,000 “Unrelated treatment” line. Both fields cited only `Patient: Rajesh Kumar`. `resolve_document_handoff()` returned `CANDIDATES_VALIDATED`, applied the values, and cleared the original missing-total and missing-line issues.
- **Impact:** A buggy or substituted resolver can inject a financially material fact through a formally valid handoff. The downstream deterministic evaluator then treats the injected value as extracted evidence.
- **How to detect:** Pair a valid page-local quote with a different date, amount, diagnosis, description, or document type and check whether the handoff rejects the semantic mismatch.
- **Current handling:** Fixed after the injected failure: the handoff now independently validates text inclusion, date support, labeled totals, line descriptions/amounts, and document-type markers against each field's quotes. The same payload now abstains and preserves the original issue.

### V25 — Resolver trace and metrics accepted arbitrary persistent data (P1; fixed after dry run)

- **Area:** structured evidence handoff / privacy
- **Exact trigger:** A valid `ABSTAINED` envelope includes extra keys in `trace` or `metrics`.
- **Observed result:** Injected `raw_ocr: TRACE_SECRET` and `raw_secret: METRIC_SECRET` both survived `resolve_document_handoff()`.
- **Impact:** A provider adapter can persist raw OCR, medical data, credentials, or very large payloads despite the handoff contract saying raw OCR is not returned.
- **How to detect:** Add unknown and oversized keys to otherwise valid trace/metrics objects, then serialize the returned handoff and saved claim.
- **Current handling:** Fixed after the injected failure: metrics are projected onto a numeric allowlist and trace is rebuilt from stage/status plus a closed reason vocabulary. The injected secrets no longer survive.

### V26 — Placeholder explanation objects and a missing ledger could pass (P1; fixed after dry run)

- **Area:** decision handoff / human-readable output
- **Exact trigger:** The policy evaluator returns a financially consistent `APPROVED` result with `reasons: [{}]`, `trace: [{}]`, an empty ledger, and a valid numeric confidence.
- **Observed result:** The pre-hardening `adjudicate_handoff()` returned this object unchanged. The hardened boundary routes it to manual review.
- **Impact:** A payable result can still satisfy the boundary without a reason code/message, identifiable trace step, or reproducible payable ledger. The UI falls back to generic copy and shows no calculation.
- **How to detect:** Inject non-empty placeholder dictionaries and no ledger, then render both member and operations views.
- **Current handling:** Fixed: every reason needs a nonempty code and message, every trace step needs stage/rule/status, and payable decisions require a nonempty ledger whose paise total matches the approved amount.

### V27 — Ledger arithmetic was not validated (P1; fixed after dry run)

- **Area:** decision handoff / privacy
- **Exact trigger:** A financially consistent result uses dictionaries with missing or unknown nested keys, embeds sensitive text inside them, or provides a ledger whose adjustments do not reconcile to the approved amount.
- **Observed result:** Before hardening, an approval for ₹10 with a ledger entry of `999999` paise passed unchanged. The current boundary routes it to manual review.
- **Impact:** The amount header can disagree with the displayed calculation, and arbitrary nested text can be persisted. Renderers may show blank labels or misleading arithmetic despite a valid top-level decision.
- **How to detect:** Fuzz keys and values inside each reason, trace, and ledger object; independently sum ledger money and compare it with the approved amount.
- **Current handling:** Fixed for payable ledger arithmetic and human-readable reason/trace fields. A total serialized-artifact budget remains V29's aggregate-size caveat.

### V28 — A correction handoff could be structurally non-actionable (P1; fixed after dry run)

- **Area:** decision handoff / member output
- **Exact trigger:** The evaluator returns a null decision and correction state with `correction_requests: [1]` and `trace: [{}]`.
- **Observed result:** The pre-hardening handoff accepted the payload unchanged. The current boundary routes it to manual review.
- **Impact:** The member receives a value that is present but does not identify a document, problem, or next action.
- **How to detect:** Fuzz correction entries with scalars, empty objects, missing messages, and oversized messages; render each result.
- **Current handling:** Fixed: every correction request must be an object with a nonempty code and member-facing message; trace entries must carry an identifiable stage, rule and status.

### V29 — Persisted extracted facts were individually unbounded (P2; fixed after dry run)

- **Area:** trace / human-readable output
- **Exact trigger:** An accepted document field contains a very long string or collection.
- **Observed result:** `document_evidence_trace()` preserved a 200,000-character `treatment` value in full. Source snippets are bounded, but field values are not.
- **Impact:** A single provider result can inflate the database/API response and make claim or operations pages slow or unreadable.
- **How to detect:** Pass large strings and large `medicines`, `tests_ordered`, or `line_items` collections through `document_evidence_trace()` and measure serialized result size and render time.
- **Current handling:** Fixed for the reproduced case: strings are truncated to 500 characters, nested depth is limited, and list/dictionary counts are capped. A single total serialized-byte budget is still not enforced, so aggregate-size testing remains relevant.

### V30 — The member evidence panel omits local and Sarvam evidence (P2)

- **Area:** human-readable output
- **Exact trigger:** A decided claim uses only local PDF extraction or Sarvam, with no Gemini correction.
- **Impact:** The member-facing “Evidence used” panel says no AI-sourced correction was applied and points to the trace, rather than displaying the extracted facts and citations now present in `document_evidence`. The operations page shows those facts, so the two views communicate materially different evidence detail.
- **How to detect:** Open the same non-Gemini claim in the member view and `/ops`; compare the evidence sections.
- **Current handling:** The member view reads only `candidate_evidence`; the operations view reads `document_evidence`. The raw facts are available, but not presented consistently.

## Bounded orchestration dry-run matrix

These runs used supplied fixtures, deterministic in-memory payloads, or stubbed providers. No live provider call, production service, migration, or external write was used. “Contained” means the pipeline stopped, abstained, requested a correction, or routed to manual review without approving unsupported money. “Gap” means the run exposed a remaining contract or output weakness.

| ID | Scenario / injected failure | Boundary exercised | Observed outcome | Assessment |
| --- | --- | --- | --- | --- |
| DR01 | All 12 supplied fixtures through decision handoff | fixture → policy → decision contract | Expected decision/state/amounts reproduced, including null decisions for TC001-TC003 | Contained within fixture scope |
| DR02 | Two valid bills totaling ₹2,000 | document facts → policy ledger | ₹1,800 approved; both bills contribute ₹2,000 of ledger lines | Fixed recheck |
| DR03 | Two prescriptions, missing consultation bill | document gate | Decision remains null; correction names uploaded and required types | Contained |
| DR04 | Unreadable pharmacy bill | document gate | Decision remains null; specific re-upload request | Contained |
| DR05 | Two patient names | document gate | Decision remains null; both names surfaced | Contained |
| DR06 | Clean extracted documents | router → Gemini | `NOT_NEEDED`; zero model calls | Contained / cost-bounded |
| DR07 | Gemini key present but opt-in false | configuration → router | Resolver skipped | Contained |
| DR08 | Valid cited bill candidate | Gemini → handoff → document gate | Candidate applied, document gate rerun, deterministic amount recomputed | Contained under default resolver |
| DR09 | Invalid source quote on arithmetic correction | Gemini validator | Candidate rejected; correction remains; raw quote not persisted | Contained |
| DR10 | Two transient Gemini timeouts | transport retry → web route | One retry only; claim routed to manual review; exception text redacted | Contained |
| DR11 | Resolver raises with document text in exception | resolver → handoff | Original documents/issues retained; `resolver_failure` stores type only | Contained |
| DR12 | Old-schema or foreign-file envelope | resolver → handoff | `invalid_handoff_envelope`; local evidence retained | Contained |
| DR13 | Malformed nested proof record | resolver → handoff | Abstained before candidate application | Contained |
| DR14 | Known-field echo conflicts with local evidence | Gemini validator | Entire model response abstains | Contained, with all-or-nothing cost noted in V11 |
| DR15 | File/page/call budget exceeded | Gemini router | Zero-call abstention with bounded reason | Contained |
| DR16 | Policy evaluator raises with private text | policy → decision handoff | Zero-amount manual review; exception message redacted | Contained |
| DR17 | Approved rupees and paise disagree | policy → decision handoff | Zero-amount manual review | Contained |
| DR18 | State says manual review while decision says approved | policy → decision handoff | Zero-amount manual review | Contained |
| DR19 | Approved amount contains fractional paise | policy → decision handoff | Zero-amount manual review | Contained |
| DR20 | ₹9,000 candidate cites only a patient-name quote | resolver → handoff | Initially accepted; after hardening, abstains and retains original issue | Fixed recheck: V24 |
| DR21 | Valid abstention adds secret trace/metric keys | resolver → handoff | Initially leaked; after hardening, unknown fields are removed | Fixed recheck: V25 |
| DR22 | Approved result has empty explanation and `confidence=true` | policy → decision handoff | After hardening, routes to zero-amount manual review | Fixed recheck |
| DR23 | Nested result lists contain strings and an extra secret field | policy → decision handoff | After hardening, routes to zero-amount manual review | Fixed recheck |
| DR24 | Null decision has no correction request | policy → decision handoff | After hardening, routes to zero-amount manual review | Fixed recheck |
| DR25 | Extracted treatment is 200,000 characters | document facts → persisted trace | After hardening, field is truncated to 500 characters | Fixed recheck: V29 |
| DR25A | Approval uses `[{}]` reasons/trace and no ledger | policy → decision handoff | Rejected to manual review | Fixed by required reason/trace fields and payable-ledger check |
| DR25B | ₹10 approval carries a 999999-paise ledger line | policy → decision handoff | Rejected to manual review | Fixed by ledger sum reconciliation |
| DR25C | Null decision uses scalar correction `[1]` | policy → decision handoff | Rejected to manual review | Fixed by structured correction-request validation |
| DR26 | Sarvam setup raises during provider construction | provider setup → document adapter | Intake continues with failure metric and actionable issue | Contained |
| DR27 | Sarvam structured extraction fails after local parse | provider → document gate | Member correction path used despite provider failure | **Gap: V9** |
| DR28 | Optional risk enricher raises | optional component → policy result | Supported decision remains; trace and confidence show degradation | Contained |
| DR29 | Identical payable bill submitted again | persistence → duplicate gate | Manual review with prior claim reference | Contained for exact bytes only; V23 remains |
| DR30 | Non-Gemini decided claim opened in member and ops views | trace → presentation | Ops shows extracted facts; member evidence panel does not | **Gap: V30** |

## Failure-injection details

### FI1 — Semantically unrelated citation

- **Input shape:** One `HOSPITAL_BILL` with known patient and missing total/lines; OCR page contains only `Patient: Rajesh Kumar`.
- **Injected handoff:** `total_paise=900000` and one line item for `900000`; each proof uses page 1 and the patient-name quote.
- **Expected safe behavior:** Reject because neither the amount nor description occurs in the cited evidence.
- **Observed behavior:** Initially `CANDIDATES_VALIDATED`; after handoff hardening, `ABSTAINED` and the missing-field issue remains.
- **Failure class:** fixed cross-boundary semantic validation gap.

### FI2 — Trace and metric payload injection

- **Input shape:** Otherwise valid versioned `ABSTAINED` envelope.
- **Injected fields:** `trace.raw_ocr="TRACE_SECRET"`; `metrics.raw_secret="METRIC_SECRET"`.
- **Expected safe behavior:** Unknown fields dropped or envelope rejected.
- **Observed behavior:** Initially both values survived; after handoff hardening, both are removed and the unknown reason is normalized.
- **Failure class:** fixed closed-schema and data-minimization gap.

### FI3 — Explanation-free approval

- **Input shape:** `state=DECIDED`, `decision=APPROVED`, ₹10/1000 paise, empty reasons/trace/ledger, `confidence_score=true`.
- **Expected safe behavior:** Reject an unexplained payable result and reject boolean confidence.
- **Observed behavior:** Initially passed; after handoff hardening, it routes to zero-amount manual review. A residual variant with `reasons: [{}]`, `trace: [{}]`, empty ledger, and numeric confidence still passes.
- **Failure class:** partially fixed decision-contract and human-explanation gap.

### FI4 — Malformed nested decision artifact

- **Input shape:** Financially consistent approval with string elements in reasons, trace, and ledger plus `private_blob="HANDOFF_SECRET"`.
- **Expected safe behavior:** Nested schema or additional-property rejection.
- **Observed behavior:** The original scalar/extra-field payload now fails closed. A residual variant with dictionaries missing required keys passes, and ledger arithmetic is not reconciled to approved money.
- **Failure class:** partially fixed nested validation and persistence-boundary gap.

### FI5 — Empty correction

- **Input shape:** Null decision, correction state, null amounts, empty reasons/trace/ledger, no correction request.
- **Expected safe behavior:** Require at least one specific, actionable correction.
- **Observed behavior:** The empty-list payload now fails closed. A residual `correction_requests: [1]` with `trace: [{}]` passes.
- **Failure class:** partially fixed member-actionability contract gap.

### FI6 — Oversized human-readable evidence

- **Input shape:** A permitted `treatment` field containing 200,000 characters.
- **Expected safe behavior:** A bounded field, record, or total-trace budget.
- **Observed behavior:** Initially retained the full value; after bounding was added, the field is truncated to 500 characters.
- **Failure class:** fixed per-field output-size gap; aggregate serialized size remains untested.

## Hypothesized risks

### H1 — Concurrent claims can overspend limits or evade duplicate review (P0)

- **Area:** persistence / concurrency
- **Exact trigger:** Two claims for the same family, or two byte-identical bills, process concurrently before either claim reaches a payable `DECIDED` state.
- **Likely impact:** Both workers can observe the same prior YTD amount and no prior payable duplicate, then approve independently.
- **How to detect:** Place a barrier after each worker reads history/duplicates, release both together, and inspect combined approved amount and duplicate flags.
- **Current handling:** No atomic reservation or serialized family/bill decision boundary is visible. This was not reproduced with true concurrent workers in this audit.

### H2 — Multiple application workers can process the same queued claim (P1)

- **Area:** job execution
- **Exact trigger:** Two processes run startup recovery or otherwise call `process_claim()` while the claim is still `QUEUED`/`PROCESSING`.
- **Likely impact:** Duplicate provider calls, competing result writes, duplicate events, and inconsistent cost metrics.
- **How to detect:** Start two workers against the same SQLite database with one queued claim and count extraction calls/events.
- **Current handling:** The state check and transition to `PROCESSING` are separate operations; no compare-and-set claim lease is used.

### H3 — Long sequential extraction can leave claims apparently stuck (P1)

- **Area:** provider handoff / operations
- **Exact trigger:** Several scanned files each require digitise and structured extraction near their 90-second job deadlines.
- **Likely impact:** A six-file web claim can occupy a worker thread for many minutes. A restart can repeat some external work because provider job IDs are not persisted.
- **How to detect:** Use a provider stub that completes each stage just before timeout and measure total claim latency and repeated calls after restart.
- **Current handling:** Per-job polling is bounded, but there is no claim-level deadline or durable provider checkpoint.

### H4 — Backdated claim processing can distort frequency and benefit history (P2)

- **Area:** history aggregation
- **Exact trigger:** A member submits an older treatment claim after newer claims have already been decided.
- **Likely impact:** Later treatment events count toward the backdated claim's monthly/same-day risk and available annual benefit. That may be intentional for benefit consumption but is not distinguished in trace semantics.
- **How to detect:** Decide newer claims first, then submit an older claim in the same policy year and compare history with a treatment-date-as-of view.
- **Current handling:** `_member_claim_history()` loads every other policy-year record without an as-of cutoff.

### H5 — Local file storage and unauthenticated read APIs expose medical data in non-local deployments (P1)

- **Area:** intake / privacy
- **Exact trigger:** Run the app on a shared or reachable host.
- **Likely impact:** Anyone who can reach the service can list claims and retrieve claim details; uploaded medical files remain on disk without a retention workflow.
- **How to detect:** Access `/api/claims` and `/api/claims/{id}` without credentials from another client, then inspect upload retention after claim completion.
- **Current handling:** Appropriate only for the documented local prototype boundary. No authentication, authorization, tenant isolation, encryption-at-rest layer, or deletion path is present in this repository.

## Concentrated test blind spots

The current suite is strongest on the twelve supplied fixture decisions, strict Gemini response validation, basic upload validation, persistence, and selected failure routing. The highest-value untested boundaries exposed by this audit are:

1. Adversarial quote-to-value variants beyond the new unrelated-amount regression, especially dates, diagnoses, multi-line descriptions, and type markers.
2. Required nested schemas for reasons, trace, ledger, and correction entries, plus ledger-to-approved-amount reconciliation.
3. A total serialized-byte budget across the complete persisted trace, beyond the new per-value depth/count bounds.
4. Every positive coverage allowlist and non-monetary limit in the policy file.
5. Cross-document and form-to-document date consistency.
6. Live parity for submission deadline, pre-authorization, and fixture-only assumptions.
7. Extraction provenance and chronological trace order on correction, duplicate, and provider-failure exits, not only decided claims.
8. Provider partial-failure attribution.
9. Concurrent history, duplicate, and worker execution.
10. Member/operations presentation parity for local, Sarvam, and Gemini evidence.
11. Systematic coverage of every `system_must` clause and its natural-language quality, beyond the current explicit structural checks.
