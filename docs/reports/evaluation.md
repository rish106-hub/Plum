# Evaluation report

Policy: `PLUM_GHI_2024`. Cases: 12. Expected decision, amount, reason, confidence, and explicitly checked behavior matched: **12/12**.

- Policy file sha256: `1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce`
- Canonical policy sha256: `36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160`
- Fixture file sha256: `4b9b9a047ec6a6479a81f6b2767f00f931920ad7bedb3f547abb54b771034e63`

These are structured fixtures with no actual image or PDF bytes. A pass establishes policy-pipeline behavior, not OCR accuracy. The complete machine-readable outputs are also in [evaluation-data.json](evaluation-data.json).

| Case | Expected | Produced | Amount | Match |
| --- | --- | --- | ---: | --- |
| TC001 | None | None | None | Yes |
| TC002 | None | None | None | Yes |
| TC003 | None | None | None | Yes |
| TC004 | APPROVED | APPROVED | 1350 | Yes |
| TC005 | REJECTED | REJECTED | 0 | Yes |
| TC006 | PARTIAL | PARTIAL | 8000 | Yes |
| TC007 | REJECTED | REJECTED | 0 | Yes |
| TC008 | REJECTED | REJECTED | 0 | Yes |
| TC009 | MANUAL_REVIEW | MANUAL_REVIEW | 0 | Yes |
| TC010 | APPROVED | APPROVED | 3240 | Yes |
| TC011 | APPROVED | APPROVED | 4000 | Yes |
| TC012 | REJECTED | REJECTED | 0 | Yes |

## Interpretation and limits

- The engine reads only the canonical policy produced by `claims.policy` from the unmodified policy file; every interpretation, merge, and conflict resolution is listed in the canonical config's `audit` array and referenced from the trace.
- Per-claim ceiling: max(global `per_claim_limit`, category `sub_limit`), tested on the eligible amount after excluded lines are removed. A matched pre-authorization rule governs amounts above the ceiling instead.
- Category `sub_limit`: an annual per-member cap on the net benefit (after discount and co-pay) for the category's own service lines; for consultation that is the consultation-fee lines, so tests and medicines billed with a consultation are not capped by it. The claim's own share is always checked; prior category usage (`category_ytd_claims_amount`) is applied when supplied.
- Aggregate limits (category sub-limit history, annual OPD, sum insured, family floater, annual sessions) are applied to the net payable after network discount and co-pay when utilisation accompanies the claim; otherwise they are `NOT_EVALUATED`, disclosed as advisory reasons on payable outcomes, and lower confidence.
- The supplied cases carry no submission date, so the 30-day submission deadline is `NOT_EVALUATED` for them. Web intake stamps a server-side `submission_date` at intake (the real clock; `PLUM_DEMO_CLOCK` only when `PLUM_ENV` is development or test, traced as `clock/demo_clock`) and the deadline is checked against it.
- The confidence values are a heuristic evidence-completeness rubric, not calibrated probabilities. Deductions apply only for unknowns material to the outcome reached. TC011's simulated failure of the risk-signal enrichment component (which runs by default) lowers confidence below the same claim's no-failure confidence, is recorded in the trace, and marks the decision `post_decision_review_recommended`.
- Provider accuracy, handwriting, multilingual extraction, and image quality require a separately labelled image/PDF set. The fixture results make no claim about those capabilities.

## Complete outputs and traces

### Normalizer audit trail

Every decision trace carries the same 100-entry audit trail in its `policy_source` step (audit sha256 `18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8`). It is listed once here and elided from the case outputs below; `evaluation-data.json` keeps it in full.

| Id | Kind | Description |
| --- | --- | --- |
| `AGGREGATE_LIMITS_NEED_UTILISATION` | interpretation | Annual OPD limit, sum insured and family floater are cross-claim aggregates of benefit paid. They are applied to the net payable (after network discount and co-pay) when a utilisation figure accompanies the claim; otherwise the rule is NOT_EVALUATED, disclosed as an advisory reason, and lowers confidence on payable outcomes. They never block an otherwise decidable claim. |
| `BENEFIT_ORDER` | interpretation | Order: (1) line eligibility; (2) per-claim ceiling on the eligible amount (admissibility, rejects); (3) pre-authorized amount cap; (4) network discount; (5) co-pay on the discounted amount; (6) benefit caps on the resulting net payable, in order: category sub_limit on the category's own service lines, remaining annual OPD limit, remaining sum insured, remaining family floater. |
| `PER_CLAIM_CEILING.CONSULTATION` | derived | CONSULTATION per-claim ceiling is Rs 5000: the global per-claim limit is the claim ceiling; the lower category sub_limit caps the net benefit on the category's own service lines (see CATEGORY_SUB_LIMIT_RULE). |
| `CATEGORY_SERVICE_TERMS.CONSULTATION` | interpretation | CONSULTATION sub_limit applies to bill lines that are the category's own service, recognised by: consultation, consultation fee, consultation charges, consulting fee, doctor fee, doctors fee, physician fee, opd fee, opd charges, visit fee, teleconsultation. Lines recognised as tests or medicines (test, lab, laboratory, cbc, blood, urine, profile, panel, culture, x-ray, xray, scan, ultrasound, ecg, medicine, medicines, drug, tablet, capsule, syrup, injection, pharmacy) fall under the global per-claim limit only; any other eligible line counts against the sub_limit. |
| `PER_CLAIM_CEILING.DIAGNOSTIC` | derived | DIAGNOSTIC per-claim ceiling is Rs 10000: category sub_limit exceeds the global per-claim limit and supersedes it for this category. |
| `PER_CLAIM_CEILING.PHARMACY` | derived | PHARMACY per-claim ceiling is Rs 15000: category sub_limit exceeds the global per-claim limit and supersedes it for this category. |
| `NO_NETWORK_DISCOUNT.PHARMACY` | absent_optional_field | PHARMACY declares no network discount; 0% is applied. |
| `CATEGORY_PRE_AUTH_FLAG_ABSENT.PHARMACY` | absent_optional_field | PHARMACY has no category-wide pre-authorization flag; item rules in pre_authorization.required_for still apply. |
| `BRAND_CLASSIFICATION.PHARMACY` | interpretation | Branded co-pay requires each medicine line to be classified BRANDED/GENERIC with supporting text; unclassified lines route to review. |
| `PER_CLAIM_CEILING.DENTAL` | derived | DENTAL per-claim ceiling is Rs 10000: category sub_limit exceeds the global per-claim limit and supersedes it for this category. |
| `NO_NETWORK_DISCOUNT.DENTAL` | absent_optional_field | DENTAL declares no network discount; 0% is applied. |
| `CATEGORY_PRE_AUTH_FLAG_ABSENT.DENTAL` | absent_optional_field | DENTAL has no category-wide pre-authorization flag; item rules in pre_authorization.required_for still apply. |
| `DENTAL_REPORT_CONFLICT.DENTAL` | conflict_resolution | requires_dental_report=true conflicts with document_requirements listing DENTAL_REPORT as optional (listed). The document matrix governs the correction gate; an absent report is traced as ADVISORY and lowers confidence on payable outcomes. |
| `COVERED_ITEM_TERMS.DENTAL.Root Canal Treatment` | interpretation | 'Root Canal Treatment' is also recognised on a bill line as: root canal, RCT, endodontic treatment. |
| `COVERED_ITEM_TERMS.DENTAL.Tooth Extraction` | interpretation | 'Tooth Extraction' is also recognised on a bill line as: extraction, tooth removal. |
| `COVERED_ITEM_TERMS.DENTAL.Dental Filling` | interpretation | 'Dental Filling' is also recognised on a bill line as: filling, composite restoration, amalgam restoration. |
| `COVERED_ITEM_TERMS.DENTAL.Scaling and Polishing` | interpretation | 'Scaling and Polishing' is also recognised on a bill line as: scaling, polishing, oral prophylaxis, teeth cleaning. |
| `COVERED_ITEM_TERMS.DENTAL.Dental X-Ray` | interpretation | 'Dental X-Ray' is also recognised on a bill line as: x-ray, xray, IOPA, OPG, dental radiograph. |
| `COVERED_ITEM_TERMS.DENTAL.Crown Placement` | interpretation | 'Crown Placement' is also recognised on a bill line as: crown, dental cap. |
| `COVERED_ITEM_TERMS.DENTAL.Gum Treatment` | interpretation | 'Gum Treatment' is also recognised on a bill line as: periodontal treatment, periodontal therapy, gum surgery, gingival treatment. |
| `PER_CLAIM_CEILING.VISION` | derived | VISION per-claim ceiling is Rs 5000: sub_limit equals the global limit. |
| `NO_NETWORK_DISCOUNT.VISION` | absent_optional_field | VISION declares no network discount; 0% is applied. |
| `CATEGORY_PRE_AUTH_FLAG_ABSENT.VISION` | absent_optional_field | VISION has no category-wide pre-authorization flag; item rules in pre_authorization.required_for still apply. |
| `COVERED_ITEM_TERMS.VISION.Glasses` | interpretation | 'Glasses' is also recognised on a bill line as: spectacles, eyeglasses, spectacle lenses. |
| `COVERED_ITEM_TERMS.VISION.Contact Lenses` | interpretation | 'Contact Lenses' is also recognised on a bill line as: contact lens. |
| `COVERED_ITEM_TERMS.VISION.Eye Examination` | interpretation | 'Eye Examination' is also recognised on a bill line as: eye exam, eye test, eye checkup, refraction test. |
| `COVERED_ITEM_TERMS.VISION.Cataract Surgery` | interpretation | 'Cataract Surgery' is also recognised on a bill line as: cataract, phacoemulsification. |
| `PER_CLAIM_CEILING.ALTERNATIVE_MEDICINE` | derived | ALTERNATIVE_MEDICINE per-claim ceiling is Rs 8000: category sub_limit exceeds the global per-claim limit and supersedes it for this category. |
| `NO_NETWORK_DISCOUNT.ALTERNATIVE_MEDICINE` | absent_optional_field | ALTERNATIVE_MEDICINE declares no network discount; 0% is applied. |
| `CATEGORY_PRE_AUTH_FLAG_ABSENT.ALTERNATIVE_MEDICINE` | absent_optional_field | ALTERNATIVE_MEDICINE has no category-wide pre-authorization flag; item rules in pre_authorization.required_for still apply. |
| `COVERED_SYSTEM_TERMS.Ayurveda` | interpretation | Ayurveda is also recognised from: ayurvedic, panchakarma, vaidya. |
| `COVERED_SYSTEM_TERMS.Homeopathy` | interpretation | Homeopathy is also recognised from: homoeopathy, homeopathic, homoeopathic. |
| `COVERED_SYSTEM_TERMS.Unani` | interpretation | Unani is also recognised from: hakim. |
| `COVERED_SYSTEM_TERMS.Naturopathy` | interpretation | Naturopathy is also recognised from: naturopathic, nature cure. |
| `PER_CLAIM_CEILING_RULE` | conflict_resolution | Each category's per-claim ceiling is max(coverage.per_claim_limit, category sub_limit), tested against the eligible amount after excluded/non-covered lines are removed; exceeding it rejects the claim (PER_CLAIM_EXCEEDED). Where a matched pre-authorization rule governs the treatment, the pre-authorization decides instead. The ceiling cannot be the lower consultation sub_limit: the supplied network consultation case pays Rs 3,240 on a Rs 4,500 bill. |
| `CATEGORY_SUB_LIMIT_RULE` | conflict_resolution | A category sub_limit is an annual, per-member cap on the net benefit (after network discount and co-pay) paid for the category's own service lines. This claim's own service benefit is always capped; earlier usage this policy year (category_sub_limit_used, else category_ytd_claims_amount as an upper bound) reduces what remains, and when neither is supplied the history is NOT_EVALUATED and disclosed. Any excess is removed and the claim is PARTIAL. For consultation the service lines are consultation-fee lines (CATEGORY_SERVICE_TERMS.CONSULTATION); tests and medicines billed with a consultation are not consultation services. For every other category all eligible lines are the category's service. If the service share of an unitemized bill cannot be established and the net payable exceeds what remains, a claim that could otherwise pay routes to review (CATEGORY_SUB_LIMIT_UNVERIFIED). A governing pre-authorization supersedes the cap. Rejected alternative: an annual aggregate over the whole claim. The supplied network consultation case pays Rs 3,240 in one consultation claim, above an annual Rs 2,000 consultation cap, so that reading would either break the fixture or pay more when category history is absent than when it is zero. |
| `PRESCRIPTION_REQUIREMENT.CONSULTATION` | derived | requires_prescription=true agrees with the document matrix (PRESCRIPTION required); the document gate enforces it. |
| `PRESCRIPTION_REQUIREMENT.DIAGNOSTIC` | derived | requires_prescription=true agrees with the document matrix (PRESCRIPTION required); the document gate enforces it. |
| `PRESCRIPTION_REQUIREMENT.PHARMACY` | derived | requires_prescription=true agrees with the document matrix (PRESCRIPTION required); the document gate enforces it. |
| `PRESCRIPTION_REQUIREMENT.DENTAL` | derived | requires_prescription=false agrees with the document matrix (PRESCRIPTION not required); the document gate enforces it. |
| `PRESCRIPTION_REQUIREMENT.VISION` | derived | requires_prescription=true agrees with the document matrix (PRESCRIPTION required); the document gate enforces it. |
| `PRESCRIPTION_REQUIREMENT.ALTERNATIVE_MEDICINE` | derived | requires_prescription=true agrees with the document matrix (PRESCRIPTION required); the document gate enforces it. |
| `CONDITION_TERMS.diabetes` | interpretation | diabetes waiting period is also triggered by: diabetic, diabetes mellitus, T2DM, T1DM. |
| `CONDITION_TERMS.hypertension` | interpretation | hypertension waiting period is also triggered by: HTN, high blood pressure. |
| `CONDITION_TERMS.thyroid_disorders` | interpretation | thyroid disorders waiting period is also triggered by: thyroid, hypothyroidism, hyperthyroidism, thyroiditis. |
| `CONDITION_TERMS.joint_replacement` | interpretation | joint replacement waiting period is also triggered by: knee replacement, hip replacement, arthroplasty. |
| `CONDITION_TERMS.maternity` | interpretation | maternity waiting period is also triggered by: pregnancy, antenatal, prenatal, obstetric. |
| `CONDITION_TERMS.mental_health` | interpretation | mental health waiting period is also triggered by: depression, anxiety disorder, psychiatric. |
| `CONDITION_TERMS.obesity_treatment` | interpretation | obesity treatment waiting period is also triggered by: obesity, bariatric, weight loss. |
| `CONDITION_TERMS.hernia` | interpretation | hernia waiting period is also triggered by: inguinal hernia, umbilical hernia. |
| `CONDITION_TERMS.cataract` | interpretation | cataract waiting period is also triggered by: cataract surgery. |
| `EXCLUSION_MERGED.DENTAL.teeth_whitening` | conflict_resolution | 'Teeth Whitening' duplicates 'Teeth whitening' for DENTAL; merged into one canonical exclusion. |
| `EXCLUSION_MERGED.DENTAL.orthodontic_treatment` | conflict_resolution | 'Orthodontic Treatment (Braces)' duplicates 'Orthodontic treatment' for DENTAL; merged into one canonical exclusion. |
| `EXCLUSION_MERGED.VISION.lasik_surgery` | conflict_resolution | 'LASIK Surgery' duplicates 'LASIK' for VISION; merged into one canonical exclusion. |
| `EXCLUSION_MERGED.VISION.refractive_surgery` | conflict_resolution | 'Refractive Surgery' duplicates 'Refractive surgery' for VISION; merged into one canonical exclusion. |
| `EXCLUSION_TERMS.Self-inflicted injuries` | interpretation | 'Self-inflicted injuries' is matched through: self-harm, deliberate self harm. |
| `EXCLUSION_TERMS.War or nuclear hazard` | interpretation | 'War or nuclear hazard' is matched through: war, nuclear hazard, war injury. |
| `EXCLUSION_TERMS.Substance abuse treatment` | interpretation | 'Substance abuse treatment' is matched through: substance abuse, de-addiction, drug rehabilitation, alcohol dependence. |
| `EXCLUSION_TERMS.Experimental treatments` | interpretation | 'Experimental treatments' is matched through: experimental treatment, experimental therapy, investigational treatment. |
| `EXCLUSION_TERMS.Infertility and assisted reproduction` | interpretation | 'Infertility and assisted reproduction' is matched through: infertility, assisted reproduction, IVF, in vitro fertilisation, in vitro fertilization. |
| `EXCLUSION_TERMS.Obesity and weight loss programs` | interpretation | 'Obesity and weight loss programs' is matched through: obesity, weight loss program, weight loss, weight management, bariatric. |
| `EXCLUSION_TERMS.Bariatric surgery` | interpretation | 'Bariatric surgery' is matched through: gastric bypass, sleeve gastrectomy. |
| `EXCLUSION_TERMS.Cosmetic or aesthetic procedures` | interpretation | 'Cosmetic or aesthetic procedures' is matched through: aesthetic procedure, cosmetic procedure, cosmetic surgery, cosmetic treatment. |
| `EXCLUSION_TERMS.Vaccination (non-medically necessary)` | interpretation | 'Vaccination (non-medically necessary)' is matched through: vaccination, vaccine. |
| `EXCLUSION_TERMS.Health supplements and tonics` | interpretation | 'Health supplements and tonics' is matched through: health supplement, supplement, tonic, multivitamin. |
| `EXCLUSION_TERMS.Teeth whitening` | interpretation | 'Teeth whitening' is matched through: tooth whitening, whitening. |
| `EXCLUSION_TERMS.Orthodontic Treatment (Braces)` | interpretation | 'Orthodontic Treatment (Braces)' is matched through: orthodontic treatment, braces. |
| `EXCLUSION_LINE_TERMS.Cosmetic dental procedures` | interpretation | 'Cosmetic dental procedures' also matches bill lines containing: cosmetic, aesthetic, upgrade. |
| `EXCLUSION_TERMS.Implants (Cosmetic)` | interpretation | 'Implants (Cosmetic)' is matched through: cosmetic implant. |
| `PRE_AUTH_PARSED.mri_scan` | derived | 'MRI scan (amount > ₹10,000)' parsed as item 'MRI scan' requiring pre-authorization above Rs 10000. Short forms: MRI, magnetic resonance imaging. |
| `PRE_AUTH_PARSED.ct_scan` | derived | 'CT scan (amount > ₹10,000)' parsed as item 'CT scan' requiring pre-authorization above Rs 10000. Short forms: CT, computed tomography. |
| `PRE_AUTH_PARSED.pet_scan` | derived | 'PET scan' parsed as item 'PET scan' always requiring pre-authorization. Short forms: PET, PET-CT, positron emission tomography. |
| `PRE_AUTH_PARSED.major_surgical_procedures` | derived | 'Major surgical procedures' parsed as item 'Major surgical procedures' always requiring pre-authorization. Short forms: major surgery. |
| `PRE_AUTH_PARSED.planned_hospitalization` | derived | 'Planned hospitalization' parsed as item 'Planned hospitalization' always requiring pre-authorization. Short forms: planned hospitalisation, planned admission. |
| `PRE_AUTH_THRESHOLD_CONFLICT.pet_scan` | conflict_resolution | 'PET scan' threshold differs between pre_authorization.required_for (always) and opd_categories.diagnostic.pre_auth_threshold (above Rs 10000); the stricter reading (always) is used. |
| `PRE_AUTH_MATCH_SCOPE` | interpretation | Pre-authorization rules match the services in the claim (treatment, ordered tests, test names, bill lines), never the diagnosis, and ignore negated mentions. Single-word short forms of 4 letters or fewer (MRI, CT, PET) match only as whole words inside an ordered test, a test name or a bill line; 'PET' additionally needs an imaging word in the same entry (scan, ct, imaging, tomography). |
| `NETWORK_NAME_VARIANTS.Apollo Hospitals` | interpretation | Apollo Hospitals is also recognised as: Apollo Hospital. |
| `NETWORK_NAME_VARIANTS.Fortis Healthcare` | interpretation | Fortis Healthcare is also recognised as: Fortis Hospital, Fortis Hospitals. |
| `NETWORK_NAME_VARIANTS.Max Healthcare` | interpretation | Max Healthcare is also recognised as: Max Hospital, Max Super Speciality Hospital. |
| `NETWORK_NAME_VARIANTS.Manipal Hospitals` | interpretation | Manipal Hospitals is also recognised as: Manipal Hospital. |
| `NETWORK_NAME_VARIANTS.Narayana Health` | interpretation | Narayana Health is also recognised as: Narayana Hrudayalaya. |
| `NETWORK_NAME_VARIANTS.Medanta` | interpretation | Medanta is also recognised as: Medanta The Medicity. |
| `NETWORK_NAME_VARIANTS.Kokilaben Dhirubhai Ambani Hospital` | interpretation | Kokilaben Dhirubhai Ambani Hospital is also recognised as: Kokilaben Hospital. |
| `NETWORK_NAME_VARIANTS.Aster CMI Hospital` | interpretation | Aster CMI Hospital is also recognised as: Aster CMI. |
| `NETWORK_NAME_VARIANTS.Columbia Asia` | interpretation | Columbia Asia is also recognised as: Columbia Asia Hospital. |
| `NETWORK_NAME_VARIANTS.Sakra World Hospital` | interpretation | Sakra World Hospital is also recognised as: Sakra Hospital. |
| `DANGLING_DEPENDENT.EMP003.DEP003` | reference_repair | EMP003 lists dependent DEP003, which is not in the roster (or does not point back). It is excluded from covered-patient matching; a claim for it cannot be verified and routes to review as an unknown member. |
| `DANGLING_DEPENDENT.EMP007.DEP004` | reference_repair | EMP007 lists dependent DEP004, which is not in the roster (or does not point back). It is excluded from covered-patient matching; a claim for it cannot be verified and routes to review as an unknown member. |
| `DANGLING_DEPENDENT.EMP007.DEP005` | reference_repair | EMP007 lists dependent DEP005, which is not in the roster (or does not point back). It is excluded from covered-patient matching; a claim for it cannot be verified and routes to review as an unknown member. |
| `DANGLING_DEPENDENT.EMP010.DEP006` | reference_repair | EMP010 lists dependent DEP006, which is not in the roster (or does not point back). It is excluded from covered-patient matching; a claim for it cannot be verified and routes to review as an unknown member. |
| `DEPENDENT_JOIN_DATE.DEP001` | derived | DEP001 has no join_date and inherits EMP001's (2024-04-01). |
| `RELATIONSHIP_VOCABULARY.DEP002` | interpretation | Roster relationship CHILD is read as family-floater relationship CHILDREN. |
| `DEPENDENT_JOIN_DATE.DEP002` | derived | DEP002 has no join_date and inherits EMP001's (2024-04-01). |
| `NETWORK_MATCH_RULE` | interpretation | Provider names match a network hospital exactly, or before a comma/dash branch or city suffix. |
| `SUBMISSION_CURRENCY` | derived | The schema accepts only INR. All money is converted to integer paise; a claim that states a different currency is rejected as malformed input. |
| `FRAUD_THRESHOLD_ROLES` | interpretation | auto_manual_review_above routes a single claim above it to manual review. high_value_claim_threshold (the same amount in the supplied policy) marks a single claim as high value in the risk trace and is the limit on the member family's trailing 30-day claimed value used by the risk-signal enrichment component; exceeding it routes to review (RISK_SIGNAL_REVIEW). |
| `INFORMATIONAL_FIELD.policy_name` | informational | Descriptive; shown in outputs only. |
| `INFORMATIONAL_FIELD.insurer` | informational | Descriptive; shown in outputs only. |
| `INFORMATIONAL_FIELD.policy_holder.company_name` | informational | Descriptive; shown in outputs only. |
| `INFORMATIONAL_FIELD.policy_holder.employee_count` | informational | Group size is an underwriting fact; no claim rule in the policy depends on it. |

### TC001: Wrong Document Uploaded

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "NEEDS_CORRECTION",
  "decision": null,
  "approved_amount": null,
  "approved_amount_paise": null,
  "reasons": [],
  "correction_requests": [
    {
      "code": "DOCUMENT_MISSING",
      "required_type": "HOSPITAL_BILL",
      "uploaded_types": [
        "PRESCRIPTION"
      ],
      "message": "Uploaded PRESCRIPTION; please upload a readable HOSPITAL_BILL for this consultation claim."
    }
  ],
  "confidence_score": 0.9,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "FAIL",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F001",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F002",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        }
      ],
      "details": [
        {
          "code": "DOCUMENT_MISSING",
          "required_type": "HOSPITAL_BILL",
          "uploaded_types": [
            "PRESCRIPTION"
          ],
          "message": "Uploaded PRESCRIPTION; please upload a readable HOSPITAL_BILL for this consultation claim."
        }
      ]
    }
  ],
  "ledger": []
}
```

### TC002: Unreadable Document

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "NEEDS_CORRECTION",
  "decision": null,
  "approved_amount": null,
  "approved_amount_paise": null,
  "reasons": [],
  "correction_requests": [
    {
      "code": "DOCUMENT_UNREADABLE",
      "file_id": "F004",
      "required_type": "PHARMACY_BILL",
      "message": "The PHARMACY_BILL (blurry_bill.jpg) cannot be read. Please re-upload a clear PHARMACY_BILL."
    }
  ],
  "confidence_score": 0.9,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "FAIL",
      "policy_ref": "document_requirements.PHARMACY",
      "evidence": [
        {
          "file_id": "F003",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F004",
          "type": "PHARMACY_BILL",
          "quality": "UNREADABLE",
          "patient_name": null,
          "provenance": "fixture_metadata"
        }
      ],
      "details": [
        {
          "code": "DOCUMENT_UNREADABLE",
          "file_id": "F004",
          "required_type": "PHARMACY_BILL",
          "message": "The PHARMACY_BILL (blurry_bill.jpg) cannot be read. Please re-upload a clear PHARMACY_BILL."
        }
      ]
    }
  ],
  "ledger": []
}
```

### TC003: Documents Belong to Different Patients

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "NEEDS_CORRECTION",
  "decision": null,
  "approved_amount": null,
  "approved_amount_paise": null,
  "reasons": [],
  "correction_requests": [
    {
      "code": "PATIENT_MISMATCH",
      "patients": [
        {
          "file_id": "F005",
          "name": "Rajesh Kumar"
        },
        {
          "file_id": "F006",
          "name": "Arjun Mehta"
        }
      ],
      "message": "Documents show different patients (F005: Rajesh Kumar, F006: Arjun Mehta). Please upload documents for the same patient."
    }
  ],
  "confidence_score": 0.9,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "FAIL",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F005",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": "Rajesh Kumar",
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F006",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": "Arjun Mehta",
          "provenance": "fixture_metadata"
        }
      ],
      "details": [
        {
          "code": "PATIENT_MISMATCH",
          "patients": [
            {
              "file_id": "F005",
              "name": "Rajesh Kumar"
            },
            {
              "file_id": "F006",
              "name": "Arjun Mehta"
            }
          ],
          "message": "Documents show different patients (F005: Rajesh Kumar, F006: Arjun Mehta). Please upload documents for the same patient."
        }
      ]
    }
  ],
  "ledger": []
}
```

### TC004: Clean Consultation — Full Approval

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "DECIDED",
  "decision": "APPROVED",
  "approved_amount": 1350,
  "approved_amount_paise": 135000,
  "reasons": [
    {
      "code": "CATEGORY_SUB_LIMIT_HISTORY_NOT_EVALUATED",
      "message": "Earlier consultation benefit this policy year was not supplied; this claim was checked against the full ₹2000 consultation sub-limit on its own."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.93,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "PASS",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F007",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": "Rajesh Kumar",
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F008",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": "Rajesh Kumar",
          "provenance": "fixture_metadata"
        }
      ],
      "details": []
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members[0] / policy_id / opd_categories.consultation",
      "evidence": {
        "member_id": "EMP001",
        "category": "CONSULTATION"
      }
    },
    {
      "stage": "identity",
      "rule_id": "roster_patient_match",
      "status": "PASS",
      "policy_ref": "members",
      "evidence": {
        "document_names": [
          "Rajesh Kumar",
          "Rajesh Kumar"
        ],
        "allowed_names": [
          "arjun kumar",
          "rajesh kumar",
          "sunita kumar"
        ],
        "unresolved_roster_dependents": []
      }
    },
    {
      "stage": "identity",
      "rule_id": "identity_verification",
      "status": "NOT_EVALUATED",
      "evidence": {
        "document_layer_status": null
      },
      "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."
    },
    {
      "stage": "reconciliation",
      "rule_id": "bill_amount",
      "status": "PASS",
      "evidence": {
        "claimed_paise": 150000,
        "bill_total_paise": 150000,
        "line_items_total_paise": 150000,
        "bill_files": [
          "F008"
        ]
      }
    },
    {
      "stage": "reconciliation",
      "rule_id": "document_quality",
      "status": "PASS",
      "evidence": {
        "weak_documents": [],
        "required_types": [
          "HOSPITAL_BILL",
          "PRESCRIPTION"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "document_treatment_date",
      "status": "PASS",
      "policy_ref": "claim.treatment_date",
      "evidence": {
        "treatment_date": "2024-11-01",
        "document_dates": [
          {
            "file_id": "F007",
            "date": "2024-11-01"
          },
          {
            "file_id": "F008",
            "date": "2024-11-01"
          }
        ],
        "unreadable_date_files": []
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_renewal_status",
      "status": "PASS",
      "policy_ref": "policy_holder.renewal_status",
      "evidence": {
        "renewal_status": "ACTIVE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_relationship",
      "status": "PASS",
      "policy_ref": "coverage.family_floater.covered_relationships",
      "evidence": {
        "relationship": "SELF",
        "covered_relationship": "SELF"
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_covered",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.covered",
      "evidence": {
        "covered": true
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_coverage_period",
      "status": "PASS",
      "policy_ref": "policy_holder.policy_start_date / policy_holder.policy_end_date",
      "evidence": {
        "treatment_date": "2024-11-01",
        "policy_start_date": "2024-04-01",
        "policy_end_date": "2025-03-31"
      }
    },
    {
      "stage": "policy",
      "rule_id": "minimum_claim_amount",
      "status": "PASS",
      "policy_ref": "submission_rules.minimum_claim_amount",
      "evidence": {
        "claimed_amount_paise": 150000,
        "minimum_claim_amount_paise": 50000
      }
    },
    {
      "stage": "policy",
      "rule_id": "submission_deadline",
      "status": "NOT_EVALUATED",
      "policy_ref": "submission_rules.deadline_days_from_treatment",
      "details": "Submission date absent."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365,
        "conditions": []
      },
      "details": "No explicit pre-existing-condition evidence was supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "matched_terms": [],
        "join_date": "2024-04-01",
        "join_date_source": "members[0].join_date",
        "treatment_date": "2024-11-01",
        "eligible_from": "2024-05-01"
      }
    },
    {
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
    },
    {
      "stage": "policy",
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth / pre_authorization.required_for",
      "details": "Not required for the services and amount in this claim."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "prior_claims": [],
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "monthly_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.monthly_claims_limit",
      "evidence": {
        "monthly_claim_count_including_current": 1,
        "limit": 6,
        "month": "2024-11",
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "high_value_claim",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.high_value_claim_threshold",
      "evidence": {
        "claimed_amount_paise": 150000,
        "threshold_paise": 2500000
      },
      "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."
    },
    {
      "stage": "risk",
      "rule_id": "auto_manual_review_amount",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.auto_manual_review_above",
      "evidence": {
        "claimed_amount_paise": 150000,
        "threshold_paise": 2500000
      }
    },
    {
      "stage": "risk",
      "rule_id": "fraud_score",
      "status": "NOT_EVALUATED",
      "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold",
      "evidence": {
        "score": null,
        "threshold": 0.8
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false,
      "evidence": [
        {
          "signal": "trailing_30_day_value",
          "status": "PASS",
          "evidence": {
            "claims_in_window": 1,
            "value_paise": 150000,
            "threshold_paise": 2500000,
            "window": [
              "2024-10-02",
              "2024-11-01"
            ]
          },
          "policy_ref": "fraud_thresholds.high_value_claim_threshold"
        },
        {
          "signal": "repeat_billing",
          "status": "PASS",
          "evidence": {
            "matching_claims": [],
            "provider": "city clinic bengaluru",
            "amount_paise": 150000
          }
        }
      ]
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 1500,
        "eligible_amount": 1500,
        "limit": 5000,
        "global_per_claim_limit": 5000,
        "category_sub_limit": 2000,
        "limit_source": "coverage.per_claim_limit",
        "interpretation": "PER_CLAIM_CEILING_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "network_hospital",
      "status": "NOT_APPLICABLE",
      "policy_ref": "network_hospitals",
      "evidence": {
        "hospital_name": "City Clinic, Bengaluru",
        "match": null,
        "match_rule": "exact_or_branch_suffix",
        "discount_percent": 0
      }
    },
    {
      "stage": "policy",
      "rule_id": "generic_medicine_requirement",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.generic_mandatory",
      "evidence": {
        "generic_mandatory": false,
        "branded_lines": []
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 150000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 15000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 135000
      },
      "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "sub_limit": 2000,
        "period": "policy_year_per_member",
        "service_scope": "matching_lines",
        "usage_key": null,
        "usage_basis": null,
        "used": null,
        "remaining_before_claim": 2000,
        "service_net_payable": 900,
        "counted_against_sub_limit_paise": 90000,
        "net_payable_after": 1350,
        "interpretation": "CATEGORY_SUB_LIMIT_RULE"
      },
      "details": "Prior category usage not supplied; this claim was checked against the full sub_limit on its own."
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "PASS",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": 5000,
        "ytd_source": "claim_payload",
        "remaining": 45000,
        "net_payable_before_limit": 1350
      },
      "details": "Applied to the net payable after discount and co-pay."
    },
    {
      "stage": "policy",
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured_paise": 50000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 135000
      },
      "details": "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "enabled": true,
        "combined_limit_paise": 15000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 135000
      },
      "details": "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."
    },
    {
      "stage": "confidence",
      "rule_id": "confidence_rubric",
      "status": "DEGRADED",
      "evidence": {
        "base": 0.96,
        "outcome_classes": [
          "payable"
        ],
        "factors": [
          {
            "reason": "category_usage_not_evaluated",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "applied": true
          }
        ],
        "score": 0.93
      },
      "details": "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it could change that outcome or its amount."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "APPROVED",
      "evidence": {
        "primary_reason": null,
        "review_reasons": [],
        "approved_amount_paise": 135000,
        "post_decision_review_recommended": false
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Consultation Fee",
      "source_document": "F008",
      "amount_paise": 100000,
      "amount": 1000,
      "status": "ELIGIBLE",
      "reason_code": null,
      "reason": "Covered.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "line_item",
      "description": "CBC Test",
      "source_document": "F008",
      "amount_paise": 30000,
      "amount": 300,
      "status": "ELIGIBLE",
      "reason_code": null,
      "reason": "Covered.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "line_item",
      "description": "Dengue NS1 Test",
      "source_document": "F008",
      "amount_paise": 20000,
      "amount": 200,
      "status": "ELIGIBLE",
      "reason_code": null,
      "reason": "Covered.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 150000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -15000,
      "amount": -150,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 150000,
      "percent": 10
    }
  ]
}
```

### TC005: Waiting Period — Diabetes

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "DECIDED",
  "decision": "REJECTED",
  "approved_amount": 0,
  "approved_amount_paise": 0,
  "reasons": [
    {
      "code": "WAITING_PERIOD",
      "message": "The diabetes waiting period ends on 2024-11-30; treatment was on 2024-10-15. Claims for this condition are eligible from 2024-11-30."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.96,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "PASS",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F009",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": "Vikram Joshi",
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F010",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": "Vikram Joshi",
          "provenance": "fixture_metadata"
        }
      ],
      "details": []
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members[4] / policy_id / opd_categories.consultation",
      "evidence": {
        "member_id": "EMP005",
        "category": "CONSULTATION"
      }
    },
    {
      "stage": "identity",
      "rule_id": "roster_patient_match",
      "status": "PASS",
      "policy_ref": "members",
      "evidence": {
        "document_names": [
          "Vikram Joshi",
          "Vikram Joshi"
        ],
        "allowed_names": [
          "vikram joshi"
        ],
        "unresolved_roster_dependents": []
      }
    },
    {
      "stage": "identity",
      "rule_id": "identity_verification",
      "status": "NOT_EVALUATED",
      "evidence": {
        "document_layer_status": null
      },
      "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."
    },
    {
      "stage": "reconciliation",
      "rule_id": "bill_amount",
      "status": "PASS",
      "evidence": {
        "claimed_paise": 300000,
        "bill_total_paise": 300000,
        "line_items_total_paise": null,
        "bill_files": [
          "F010"
        ]
      }
    },
    {
      "stage": "reconciliation",
      "rule_id": "document_quality",
      "status": "PASS",
      "evidence": {
        "weak_documents": [],
        "required_types": [
          "HOSPITAL_BILL",
          "PRESCRIPTION"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "document_treatment_date",
      "status": "PASS",
      "policy_ref": "claim.treatment_date",
      "evidence": {
        "treatment_date": "2024-10-15",
        "document_dates": [
          {
            "file_id": "F010",
            "date": "2024-10-15"
          }
        ],
        "unreadable_date_files": []
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_renewal_status",
      "status": "PASS",
      "policy_ref": "policy_holder.renewal_status",
      "evidence": {
        "renewal_status": "ACTIVE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_relationship",
      "status": "PASS",
      "policy_ref": "coverage.family_floater.covered_relationships",
      "evidence": {
        "relationship": "SELF",
        "covered_relationship": "SELF"
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_covered",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.covered",
      "evidence": {
        "covered": true
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_coverage_period",
      "status": "PASS",
      "policy_ref": "policy_holder.policy_start_date / policy_holder.policy_end_date",
      "evidence": {
        "treatment_date": "2024-10-15",
        "policy_start_date": "2024-04-01",
        "policy_end_date": "2025-03-31"
      }
    },
    {
      "stage": "policy",
      "rule_id": "minimum_claim_amount",
      "status": "PASS",
      "policy_ref": "submission_rules.minimum_claim_amount",
      "evidence": {
        "claimed_amount_paise": 300000,
        "minimum_claim_amount_paise": 50000
      }
    },
    {
      "stage": "policy",
      "rule_id": "submission_deadline",
      "status": "NOT_EVALUATED",
      "policy_ref": "submission_rules.deadline_days_from_treatment",
      "details": "Submission date absent."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365,
        "conditions": []
      },
      "details": "No explicit pre-existing-condition evidence was supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "waiting_period",
      "status": "FAIL",
      "policy_ref": "waiting_periods.specific_conditions.diabetes",
      "evidence": {
        "condition": "diabetes",
        "matched_terms": [
          {
            "text": "diabetes",
            "provenance": "policy_text"
          },
          {
            "text": "diabetes mellitus",
            "provenance": "interpretation"
          }
        ],
        "join_date": "2024-09-01",
        "join_date_source": "members[4].join_date",
        "treatment_date": "2024-10-15",
        "eligible_from": "2024-11-30"
      }
    },
    {
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
    },
    {
      "stage": "policy",
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth / pre_authorization.required_for",
      "details": "Not required for the services and amount in this claim."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "prior_claims": [],
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "monthly_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.monthly_claims_limit",
      "evidence": {
        "monthly_claim_count_including_current": 1,
        "limit": 6,
        "month": "2024-10",
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "high_value_claim",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.high_value_claim_threshold",
      "evidence": {
        "claimed_amount_paise": 300000,
        "threshold_paise": 2500000
      },
      "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."
    },
    {
      "stage": "risk",
      "rule_id": "auto_manual_review_amount",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.auto_manual_review_above",
      "evidence": {
        "claimed_amount_paise": 300000,
        "threshold_paise": 2500000
      }
    },
    {
      "stage": "risk",
      "rule_id": "fraud_score",
      "status": "NOT_EVALUATED",
      "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold",
      "evidence": {
        "score": null,
        "threshold": 0.8
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false,
      "evidence": [
        {
          "signal": "trailing_30_day_value",
          "status": "PASS",
          "evidence": {
            "claims_in_window": 1,
            "value_paise": 300000,
            "threshold_paise": 2500000,
            "window": [
              "2024-09-15",
              "2024-10-15"
            ]
          },
          "policy_ref": "fraud_thresholds.high_value_claim_threshold"
        },
        {
          "signal": "repeat_billing",
          "status": "NOT_EVALUATED",
          "details": "Provider or amount unavailable for this claim or its history."
        }
      ]
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 3000,
        "eligible_amount": 3000,
        "limit": 5000,
        "global_per_claim_limit": 5000,
        "category_sub_limit": 2000,
        "limit_source": "coverage.per_claim_limit",
        "interpretation": "PER_CLAIM_CEILING_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "network_hospital",
      "status": "NOT_EVALUATED",
      "policy_ref": "network_hospitals",
      "evidence": {
        "hospital_name": null,
        "match": null,
        "match_rule": "exact_or_branch_suffix",
        "discount_percent": 0
      }
    },
    {
      "stage": "policy",
      "rule_id": "generic_medicine_requirement",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.generic_mandatory",
      "evidence": {
        "generic_mandatory": false,
        "branded_lines": []
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 300000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 30000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 270000
      },
      "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "sub_limit": 2000,
        "period": "policy_year_per_member",
        "service_scope": "matching_lines",
        "usage_key": null,
        "usage_basis": null,
        "used": null,
        "remaining_before_claim": 2000,
        "service_net_payable": null,
        "counted_against_sub_limit_paise": null,
        "net_payable_after": 2700,
        "interpretation": "CATEGORY_SUB_LIMIT_RULE"
      },
      "details": "The bill is not itemized, so the category's own service share cannot be established."
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": null,
        "ytd_source": null,
        "remaining": null,
        "net_payable_before_limit": 2700
      },
      "details": "Year-to-date OPD usage was not supplied; the annual limit is applied at settlement against the utilisation ledger."
    },
    {
      "stage": "policy",
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured_paise": 50000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 270000
      },
      "details": "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "enabled": true,
        "combined_limit_paise": 15000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 270000
      },
      "details": "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."
    },
    {
      "stage": "confidence",
      "rule_id": "confidence_rubric",
      "status": "PASS",
      "evidence": {
        "base": 0.96,
        "outcome_classes": [
          "date_dependent_rejection",
          "identity_dependent_rejection",
          "rejection"
        ],
        "factors": [
          {
            "reason": "category_usage_not_evaluated",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "applied": false
          },
          {
            "reason": "annual_opd_usage_not_evaluated",
            "points": 0.04,
            "applies_to": [
              "payable"
            ],
            "applied": false
          }
        ],
        "score": 0.96
      },
      "details": "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it could change that outcome or its amount."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "REJECTED",
      "evidence": {
        "primary_reason": "WAITING_PERIOD",
        "review_reasons": [],
        "approved_amount_paise": 0,
        "post_decision_review_recommended": false
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Bill total (not itemized)",
      "source_document": "F010",
      "amount_paise": 300000,
      "amount": 3000,
      "status": "NOT_ADJUDICATED",
      "reason_code": null,
      "reason": "Passed the line-level checks, but the claim was rejected (WAITING_PERIOD); no amount is payable for this line.",
      "itemized": false,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered",
      "line_check": "ELIGIBLE"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 300000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -30000,
      "amount": -300,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 300000,
      "percent": 10
    }
  ]
}
```

### TC006: Dental Partial Approval — Cosmetic Exclusion

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "DECIDED",
  "decision": "PARTIAL",
  "approved_amount": 8000,
  "approved_amount_paise": 800000,
  "reasons": [
    {
      "code": "EXCLUDED_PROCEDURE",
      "message": "Teeth Whitening is excluded (Teeth whitening); ₹4000 removed."
    },
    {
      "code": "TREATMENT_DATE_NOT_CORROBORATED",
      "message": "No document carries a readable date, so the timing rules used the submitted treatment date without corroboration."
    },
    {
      "code": "ADVISORY_DOCUMENT_ABSENT",
      "message": "No DENTAL_REPORT was uploaded. It is optional in the document requirements, so the claim was decided without it."
    },
    {
      "code": "CATEGORY_SUB_LIMIT_HISTORY_NOT_EVALUATED",
      "message": "Earlier dental benefit this policy year was not supplied; this claim was checked against the full ₹10000 dental sub-limit on its own."
    },
    {
      "code": "ANNUAL_LIMIT_NOT_EVALUATED",
      "message": "Year-to-date OPD usage was not supplied, so the ₹50000 annual OPD limit was not evaluated. Payment is subject to the member's remaining annual OPD balance."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.83,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "PASS",
      "policy_ref": "document_requirements.DENTAL",
      "evidence": [
        {
          "file_id": "F011",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": "Priya Singh",
          "provenance": "fixture_metadata"
        }
      ],
      "details": []
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members[1] / policy_id / opd_categories.dental",
      "evidence": {
        "member_id": "EMP002",
        "category": "DENTAL"
      }
    },
    {
      "stage": "identity",
      "rule_id": "roster_patient_match",
      "status": "PASS",
      "policy_ref": "members",
      "evidence": {
        "document_names": [
          "Priya Singh"
        ],
        "allowed_names": [
          "priya singh"
        ],
        "unresolved_roster_dependents": []
      }
    },
    {
      "stage": "identity",
      "rule_id": "identity_verification",
      "status": "NOT_EVALUATED",
      "evidence": {
        "document_layer_status": null
      },
      "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."
    },
    {
      "stage": "reconciliation",
      "rule_id": "bill_amount",
      "status": "PASS",
      "evidence": {
        "claimed_paise": 1200000,
        "bill_total_paise": 1200000,
        "line_items_total_paise": 1200000,
        "bill_files": [
          "F011"
        ]
      }
    },
    {
      "stage": "reconciliation",
      "rule_id": "document_quality",
      "status": "PASS",
      "evidence": {
        "weak_documents": [],
        "required_types": [
          "HOSPITAL_BILL"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "document_treatment_date",
      "status": "NOT_EVALUATED",
      "policy_ref": "claim.treatment_date",
      "evidence": {},
      "details": "No comparable document date was extracted."
    },
    {
      "stage": "policy",
      "rule_id": "policy_renewal_status",
      "status": "PASS",
      "policy_ref": "policy_holder.renewal_status",
      "evidence": {
        "renewal_status": "ACTIVE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_relationship",
      "status": "PASS",
      "policy_ref": "coverage.family_floater.covered_relationships",
      "evidence": {
        "relationship": "SELF",
        "covered_relationship": "SELF"
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_covered",
      "status": "PASS",
      "policy_ref": "opd_categories.dental.covered",
      "evidence": {
        "covered": true
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_coverage_period",
      "status": "PASS",
      "policy_ref": "policy_holder.policy_start_date / policy_holder.policy_end_date",
      "evidence": {
        "treatment_date": "2024-10-15",
        "policy_start_date": "2024-04-01",
        "policy_end_date": "2025-03-31"
      }
    },
    {
      "stage": "policy",
      "rule_id": "minimum_claim_amount",
      "status": "PASS",
      "policy_ref": "submission_rules.minimum_claim_amount",
      "evidence": {
        "claimed_amount_paise": 1200000,
        "minimum_claim_amount_paise": 50000
      }
    },
    {
      "stage": "policy",
      "rule_id": "submission_deadline",
      "status": "NOT_EVALUATED",
      "policy_ref": "submission_rules.deadline_days_from_treatment",
      "details": "Submission date absent."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365,
        "conditions": []
      },
      "details": "No explicit pre-existing-condition evidence was supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "matched_terms": [],
        "join_date": "2024-04-01",
        "join_date_source": "members[1].join_date",
        "treatment_date": "2024-10-15",
        "eligible_from": "2024-05-01"
      }
    },
    {
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
    },
    {
      "stage": "policy",
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.dental.requires_pre_auth / pre_authorization.required_for",
      "details": "Not required for the services and amount in this claim."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "prior_claims": [],
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "monthly_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.monthly_claims_limit",
      "evidence": {
        "monthly_claim_count_including_current": 1,
        "limit": 6,
        "month": "2024-10",
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "high_value_claim",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.high_value_claim_threshold",
      "evidence": {
        "claimed_amount_paise": 1200000,
        "threshold_paise": 2500000
      },
      "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."
    },
    {
      "stage": "risk",
      "rule_id": "auto_manual_review_amount",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.auto_manual_review_above",
      "evidence": {
        "claimed_amount_paise": 1200000,
        "threshold_paise": 2500000
      }
    },
    {
      "stage": "risk",
      "rule_id": "fraud_score",
      "status": "NOT_EVALUATED",
      "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold",
      "evidence": {
        "score": null,
        "threshold": 0.8
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false,
      "evidence": [
        {
          "signal": "trailing_30_day_value",
          "status": "PASS",
          "evidence": {
            "claims_in_window": 1,
            "value_paise": 1200000,
            "threshold_paise": 2500000,
            "window": [
              "2024-09-15",
              "2024-10-15"
            ]
          },
          "policy_ref": "fraud_thresholds.high_value_claim_threshold"
        },
        {
          "signal": "repeat_billing",
          "status": "PASS",
          "evidence": {
            "matching_claims": [],
            "provider": "smile dental clinic",
            "amount_paise": 1200000
          }
        }
      ]
    },
    {
      "stage": "policy",
      "rule_id": "advisory_document",
      "status": "ADVISORY",
      "policy_ref": "opd_categories.dental.requires_dental_report",
      "evidence": {
        "document": "DENTAL_REPORT",
        "present": false,
        "interpretation": "DENTAL_REPORT_CONFLICT.DENTAL"
      },
      "details": "The category flag and the document matrix disagree; the matrix (optional) governs, so absence does not block."
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.dental.sub_limit",
      "evidence": {
        "claimed_amount": 12000,
        "eligible_amount": 8000,
        "limit": 10000,
        "global_per_claim_limit": 5000,
        "category_sub_limit": 10000,
        "limit_source": "opd_categories.dental.sub_limit",
        "interpretation": "PER_CLAIM_CEILING_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "network_hospital",
      "status": "NOT_APPLICABLE",
      "policy_ref": "network_hospitals",
      "evidence": {
        "hospital_name": "Smile Dental Clinic",
        "match": null,
        "match_rule": "exact_or_branch_suffix",
        "discount_percent": 0
      }
    },
    {
      "stage": "policy",
      "rule_id": "generic_medicine_requirement",
      "status": "PASS",
      "policy_ref": "opd_categories.dental.generic_mandatory",
      "evidence": {
        "generic_mandatory": false,
        "branded_lines": []
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 800000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 0,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 800000
      },
      "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.dental.sub_limit",
      "evidence": {
        "sub_limit": 10000,
        "period": "policy_year_per_member",
        "service_scope": "all_eligible_lines",
        "usage_key": null,
        "usage_basis": null,
        "used": null,
        "remaining_before_claim": 10000,
        "service_net_payable": 8000,
        "counted_against_sub_limit_paise": 800000,
        "net_payable_after": 8000,
        "interpretation": "CATEGORY_SUB_LIMIT_RULE"
      },
      "details": "Prior category usage not supplied; this claim was checked against the full sub_limit on its own."
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": null,
        "ytd_source": null,
        "remaining": null,
        "net_payable_before_limit": 8000
      },
      "details": "Year-to-date OPD usage was not supplied; the annual limit is applied at settlement against the utilisation ledger."
    },
    {
      "stage": "policy",
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured_paise": 50000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 800000
      },
      "details": "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "enabled": true,
        "combined_limit_paise": 15000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 800000
      },
      "details": "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."
    },
    {
      "stage": "confidence",
      "rule_id": "confidence_rubric",
      "status": "DEGRADED",
      "evidence": {
        "base": 0.96,
        "outcome_classes": [
          "payable"
        ],
        "factors": [
          {
            "reason": "treatment_date_not_corroborated",
            "points": 0.03,
            "applies_to": [
              "payable",
              "date_dependent_rejection"
            ],
            "applied": true
          },
          {
            "reason": "advisory_document_absent",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "document": "DENTAL_REPORT",
            "applied": true
          },
          {
            "reason": "category_usage_not_evaluated",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "applied": true
          },
          {
            "reason": "annual_opd_usage_not_evaluated",
            "points": 0.04,
            "applies_to": [
              "payable"
            ],
            "applied": true
          }
        ],
        "score": 0.83
      },
      "details": "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it could change that outcome or its amount."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "PARTIAL",
      "evidence": {
        "primary_reason": null,
        "review_reasons": [],
        "approved_amount_paise": 800000,
        "post_decision_review_recommended": false
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Root Canal Treatment",
      "source_document": "F011",
      "amount_paise": 800000,
      "amount": 8000,
      "status": "ELIGIBLE",
      "reason_code": null,
      "reason": "Covered.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": "Root Canal Treatment",
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.dental.covered_procedures"
    },
    {
      "kind": "line_item",
      "description": "Teeth Whitening",
      "source_document": "F011",
      "amount_paise": 400000,
      "amount": 4000,
      "status": "EXCLUDED",
      "reason_code": "EXCLUDED_PROCEDURE",
      "reason": "Excluded under the policy: Teeth whitening.",
      "itemized": true,
      "exclusion_matches": [
        {
          "exclusion_id": "DENTAL:teeth_whitening",
          "label": "Teeth whitening",
          "matched_terms": [
            {
              "text": "teeth whitening",
              "provenance": "policy_text"
            },
            {
              "text": "whitening",
              "provenance": "policy_text"
            }
          ],
          "qualifier": null,
          "policy_text_match": true,
          "source_paths": [
            "exclusions.dental_exclusions[0]",
            "opd_categories.dental.excluded_procedures[0]"
          ]
        }
      ],
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "exclusions.dental_exclusions[0]"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.dental.network_discount_percent",
      "basis_paise": 800000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.dental.copay_percent",
      "basis_paise": 800000,
      "percent": 0
    }
  ]
}
```

### TC007: MRI Without Pre-Authorization

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "DECIDED",
  "decision": "REJECTED",
  "approved_amount": 0,
  "approved_amount_paise": 0,
  "reasons": [
    {
      "code": "PRE_AUTH_MISSING",
      "message": "Pre-authorization is required for MRI scan and no approval record was supplied. Obtain pre-authorization from the insurer, then resubmit the claim with the approval record (reference number and issue date)."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.96,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "PASS",
      "policy_ref": "document_requirements.DIAGNOSTIC",
      "evidence": [
        {
          "file_id": "F012",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F013",
          "type": "LAB_REPORT",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F014",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        }
      ],
      "details": []
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members[6] / policy_id / opd_categories.diagnostic",
      "evidence": {
        "member_id": "EMP007",
        "category": "DIAGNOSTIC"
      }
    },
    {
      "stage": "identity",
      "rule_id": "patient_identity",
      "status": "NOT_EVALUATED",
      "details": "No patient name was extracted from the documents; the claim is attributed to the submitting member."
    },
    {
      "stage": "identity",
      "rule_id": "roster_patient_match",
      "status": "NOT_EVALUATED",
      "policy_ref": "members",
      "evidence": {
        "document_names": [],
        "allowed_names": [
          "suresh patil"
        ],
        "unresolved_roster_dependents": [
          "DEP004",
          "DEP005"
        ]
      }
    },
    {
      "stage": "identity",
      "rule_id": "identity_verification",
      "status": "NOT_EVALUATED",
      "evidence": {
        "document_layer_status": null
      },
      "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."
    },
    {
      "stage": "reconciliation",
      "rule_id": "bill_amount",
      "status": "PASS",
      "evidence": {
        "claimed_paise": 1500000,
        "bill_total_paise": 1500000,
        "line_items_total_paise": 1500000,
        "bill_files": [
          "F014"
        ]
      }
    },
    {
      "stage": "reconciliation",
      "rule_id": "document_quality",
      "status": "PASS",
      "evidence": {
        "weak_documents": [],
        "required_types": [
          "HOSPITAL_BILL",
          "LAB_REPORT",
          "PRESCRIPTION"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "document_treatment_date",
      "status": "NOT_EVALUATED",
      "policy_ref": "claim.treatment_date",
      "evidence": {},
      "details": "No comparable document date was extracted."
    },
    {
      "stage": "policy",
      "rule_id": "policy_renewal_status",
      "status": "PASS",
      "policy_ref": "policy_holder.renewal_status",
      "evidence": {
        "renewal_status": "ACTIVE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_relationship",
      "status": "PASS",
      "policy_ref": "coverage.family_floater.covered_relationships",
      "evidence": {
        "relationship": "SELF",
        "covered_relationship": "SELF"
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_covered",
      "status": "PASS",
      "policy_ref": "opd_categories.diagnostic.covered",
      "evidence": {
        "covered": true
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_coverage_period",
      "status": "PASS",
      "policy_ref": "policy_holder.policy_start_date / policy_holder.policy_end_date",
      "evidence": {
        "treatment_date": "2024-11-02",
        "policy_start_date": "2024-04-01",
        "policy_end_date": "2025-03-31"
      }
    },
    {
      "stage": "policy",
      "rule_id": "minimum_claim_amount",
      "status": "PASS",
      "policy_ref": "submission_rules.minimum_claim_amount",
      "evidence": {
        "claimed_amount_paise": 1500000,
        "minimum_claim_amount_paise": 50000
      }
    },
    {
      "stage": "policy",
      "rule_id": "submission_deadline",
      "status": "NOT_EVALUATED",
      "policy_ref": "submission_rules.deadline_days_from_treatment",
      "details": "Submission date absent."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365,
        "conditions": []
      },
      "details": "No explicit pre-existing-condition evidence was supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "matched_terms": [],
        "join_date": "2024-04-01",
        "join_date_source": "members[6].join_date",
        "treatment_date": "2024-11-02",
        "eligible_from": "2024-05-01"
      }
    },
    {
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
    },
    {
      "stage": "policy",
      "rule_id": "pre_authorization",
      "status": "FAIL",
      "policy_ref": "pre_authorization.required_for / opd_categories.*.high_value_tests_requiring_pre_auth / pre_authorization.validity_days",
      "evidence": {
        "matched_rules": [
          {
            "rule_id": "mri_scan",
            "label": "MRI scan",
            "matched_terms": [
              {
                "text": "mri",
                "provenance": "policy_text",
                "context": "test_or_line",
                "requires_any": []
              }
            ],
            "amount_basis": 15000,
            "amount_greater_than": 10000,
            "source_paths": [
              "pre_authorization.required_for[0]",
              "opd_categories.diagnostic.high_value_tests_requiring_pre_auth[0]",
              "opd_categories.diagnostic.pre_auth_threshold"
            ]
          }
        ],
        "category_requires_pre_auth": false,
        "claimed_amount": 15000,
        "form_status": null,
        "approval_document": null,
        "issued_date": null,
        "approval_reference": null,
        "authorized_amount": null,
        "validity_days": 30,
        "status_source": "no_approval_record_supplied",
        "insurer_verified": false
      }
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "prior_claims": [],
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "monthly_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.monthly_claims_limit",
      "evidence": {
        "monthly_claim_count_including_current": 1,
        "limit": 6,
        "month": "2024-11",
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "high_value_claim",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.high_value_claim_threshold",
      "evidence": {
        "claimed_amount_paise": 1500000,
        "threshold_paise": 2500000
      },
      "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."
    },
    {
      "stage": "risk",
      "rule_id": "auto_manual_review_amount",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.auto_manual_review_above",
      "evidence": {
        "claimed_amount_paise": 1500000,
        "threshold_paise": 2500000
      }
    },
    {
      "stage": "risk",
      "rule_id": "fraud_score",
      "status": "NOT_EVALUATED",
      "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold",
      "evidence": {
        "score": null,
        "threshold": 0.8
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false,
      "evidence": [
        {
          "signal": "trailing_30_day_value",
          "status": "PASS",
          "evidence": {
            "claims_in_window": 1,
            "value_paise": 1500000,
            "threshold_paise": 2500000,
            "window": [
              "2024-10-03",
              "2024-11-02"
            ]
          },
          "policy_ref": "fraud_thresholds.high_value_claim_threshold"
        },
        {
          "signal": "repeat_billing",
          "status": "NOT_EVALUATED",
          "details": "Provider or amount unavailable for this claim or its history."
        }
      ]
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "DEFERRED_TO_PRE_AUTH",
      "policy_ref": "opd_categories.diagnostic.sub_limit",
      "evidence": {
        "claimed_amount": 15000,
        "eligible_amount": 15000,
        "limit": 10000,
        "global_per_claim_limit": 5000,
        "category_sub_limit": 10000,
        "limit_source": "opd_categories.diagnostic.sub_limit",
        "interpretation": "PER_CLAIM_CEILING_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "network_hospital",
      "status": "NOT_EVALUATED",
      "policy_ref": "network_hospitals",
      "evidence": {
        "hospital_name": null,
        "match": null,
        "match_rule": "exact_or_branch_suffix",
        "discount_percent": 0
      }
    },
    {
      "stage": "policy",
      "rule_id": "generic_medicine_requirement",
      "status": "PASS",
      "policy_ref": "opd_categories.diagnostic.generic_mandatory",
      "evidence": {
        "generic_mandatory": false,
        "branded_lines": []
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 1500000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 0,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 1500000
      },
      "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "DEFERRED_TO_PRE_AUTH",
      "policy_ref": "opd_categories.diagnostic.sub_limit",
      "evidence": {
        "sub_limit": 10000,
        "period": "policy_year_per_member",
        "service_scope": "all_eligible_lines",
        "usage_key": null,
        "usage_basis": null,
        "used": null,
        "remaining_before_claim": 10000,
        "service_net_payable": null,
        "counted_against_sub_limit_paise": null,
        "net_payable_after": 15000,
        "interpretation": "CATEGORY_SUB_LIMIT_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": null,
        "ytd_source": null,
        "remaining": null,
        "net_payable_before_limit": 15000
      },
      "details": "Year-to-date OPD usage was not supplied; the annual limit is applied at settlement against the utilisation ledger."
    },
    {
      "stage": "policy",
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured_paise": 50000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 1500000
      },
      "details": "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "enabled": true,
        "combined_limit_paise": 15000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 1500000
      },
      "details": "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."
    },
    {
      "stage": "confidence",
      "rule_id": "confidence_rubric",
      "status": "PASS",
      "evidence": {
        "base": 0.96,
        "outcome_classes": [
          "amount_dependent_rejection",
          "rejection"
        ],
        "factors": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04,
            "applies_to": [
              "payable",
              "review",
              "identity_dependent_rejection"
            ],
            "applied": false
          },
          {
            "reason": "treatment_date_not_corroborated",
            "points": 0.03,
            "applies_to": [
              "payable",
              "date_dependent_rejection"
            ],
            "applied": false
          },
          {
            "reason": "annual_opd_usage_not_evaluated",
            "points": 0.04,
            "applies_to": [
              "payable"
            ],
            "applied": false
          }
        ],
        "score": 0.96
      },
      "details": "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it could change that outcome or its amount."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "REJECTED",
      "evidence": {
        "primary_reason": "PRE_AUTH_MISSING",
        "review_reasons": [],
        "approved_amount_paise": 0,
        "post_decision_review_recommended": false
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "MRI Lumbar Spine",
      "source_document": "F014",
      "amount_paise": 1500000,
      "amount": 15000,
      "status": "NOT_ADJUDICATED",
      "reason_code": null,
      "reason": "Passed the line-level checks, but the claim was rejected (PRE_AUTH_MISSING); no amount is payable for this line.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.diagnostic.covered",
      "line_check": "ELIGIBLE"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.diagnostic.network_discount_percent",
      "basis_paise": 1500000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.diagnostic.copay_percent",
      "basis_paise": 1500000,
      "percent": 0
    }
  ]
}
```

### TC008: Per-Claim Limit Exceeded

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "DECIDED",
  "decision": "REJECTED",
  "approved_amount": 0,
  "approved_amount_paise": 0,
  "reasons": [
    {
      "code": "PER_CLAIM_EXCEEDED",
      "message": "Claimed amount ₹7500 (eligible ₹7500) exceeds the per-claim limit of ₹5000 for consultation claims."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.96,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "PASS",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F015",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F016",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        }
      ],
      "details": []
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members[2] / policy_id / opd_categories.consultation",
      "evidence": {
        "member_id": "EMP003",
        "category": "CONSULTATION"
      }
    },
    {
      "stage": "identity",
      "rule_id": "patient_identity",
      "status": "NOT_EVALUATED",
      "details": "No patient name was extracted from the documents; the claim is attributed to the submitting member."
    },
    {
      "stage": "identity",
      "rule_id": "roster_patient_match",
      "status": "NOT_EVALUATED",
      "policy_ref": "members",
      "evidence": {
        "document_names": [],
        "allowed_names": [
          "amit verma"
        ],
        "unresolved_roster_dependents": [
          "DEP003"
        ]
      }
    },
    {
      "stage": "identity",
      "rule_id": "identity_verification",
      "status": "NOT_EVALUATED",
      "evidence": {
        "document_layer_status": null
      },
      "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."
    },
    {
      "stage": "reconciliation",
      "rule_id": "bill_amount",
      "status": "PASS",
      "evidence": {
        "claimed_paise": 750000,
        "bill_total_paise": 750000,
        "line_items_total_paise": 750000,
        "bill_files": [
          "F016"
        ]
      }
    },
    {
      "stage": "reconciliation",
      "rule_id": "document_quality",
      "status": "PASS",
      "evidence": {
        "weak_documents": [],
        "required_types": [
          "HOSPITAL_BILL",
          "PRESCRIPTION"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "document_treatment_date",
      "status": "NOT_EVALUATED",
      "policy_ref": "claim.treatment_date",
      "evidence": {},
      "details": "No comparable document date was extracted."
    },
    {
      "stage": "policy",
      "rule_id": "policy_renewal_status",
      "status": "PASS",
      "policy_ref": "policy_holder.renewal_status",
      "evidence": {
        "renewal_status": "ACTIVE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_relationship",
      "status": "PASS",
      "policy_ref": "coverage.family_floater.covered_relationships",
      "evidence": {
        "relationship": "SELF",
        "covered_relationship": "SELF"
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_covered",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.covered",
      "evidence": {
        "covered": true
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_coverage_period",
      "status": "PASS",
      "policy_ref": "policy_holder.policy_start_date / policy_holder.policy_end_date",
      "evidence": {
        "treatment_date": "2024-10-20",
        "policy_start_date": "2024-04-01",
        "policy_end_date": "2025-03-31"
      }
    },
    {
      "stage": "policy",
      "rule_id": "minimum_claim_amount",
      "status": "PASS",
      "policy_ref": "submission_rules.minimum_claim_amount",
      "evidence": {
        "claimed_amount_paise": 750000,
        "minimum_claim_amount_paise": 50000
      }
    },
    {
      "stage": "policy",
      "rule_id": "submission_deadline",
      "status": "NOT_EVALUATED",
      "policy_ref": "submission_rules.deadline_days_from_treatment",
      "details": "Submission date absent."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365,
        "conditions": []
      },
      "details": "No explicit pre-existing-condition evidence was supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "matched_terms": [],
        "join_date": "2024-04-01",
        "join_date_source": "members[2].join_date",
        "treatment_date": "2024-10-20",
        "eligible_from": "2024-05-01"
      }
    },
    {
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
    },
    {
      "stage": "policy",
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth / pre_authorization.required_for",
      "details": "Not required for the services and amount in this claim."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "prior_claims": [],
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "monthly_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.monthly_claims_limit",
      "evidence": {
        "monthly_claim_count_including_current": 1,
        "limit": 6,
        "month": "2024-10",
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "high_value_claim",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.high_value_claim_threshold",
      "evidence": {
        "claimed_amount_paise": 750000,
        "threshold_paise": 2500000
      },
      "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."
    },
    {
      "stage": "risk",
      "rule_id": "auto_manual_review_amount",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.auto_manual_review_above",
      "evidence": {
        "claimed_amount_paise": 750000,
        "threshold_paise": 2500000
      }
    },
    {
      "stage": "risk",
      "rule_id": "fraud_score",
      "status": "NOT_EVALUATED",
      "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold",
      "evidence": {
        "score": null,
        "threshold": 0.8
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false,
      "evidence": [
        {
          "signal": "trailing_30_day_value",
          "status": "PASS",
          "evidence": {
            "claims_in_window": 1,
            "value_paise": 750000,
            "threshold_paise": 2500000,
            "window": [
              "2024-09-20",
              "2024-10-20"
            ]
          },
          "policy_ref": "fraud_thresholds.high_value_claim_threshold"
        },
        {
          "signal": "repeat_billing",
          "status": "NOT_EVALUATED",
          "details": "Provider or amount unavailable for this claim or its history."
        }
      ]
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "FAIL",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 7500,
        "eligible_amount": 7500,
        "limit": 5000,
        "global_per_claim_limit": 5000,
        "category_sub_limit": 2000,
        "limit_source": "coverage.per_claim_limit",
        "interpretation": "PER_CLAIM_CEILING_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "network_hospital",
      "status": "NOT_EVALUATED",
      "policy_ref": "network_hospitals",
      "evidence": {
        "hospital_name": null,
        "match": null,
        "match_rule": "exact_or_branch_suffix",
        "discount_percent": 0
      }
    },
    {
      "stage": "policy",
      "rule_id": "generic_medicine_requirement",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.generic_mandatory",
      "evidence": {
        "generic_mandatory": false,
        "branded_lines": []
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 750000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 75000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 675000
      },
      "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "sub_limit": 2000,
        "period": "policy_year_per_member",
        "service_scope": "matching_lines",
        "usage_key": null,
        "usage_basis": null,
        "used": null,
        "remaining_before_claim": 2000,
        "service_net_payable": 1800,
        "counted_against_sub_limit_paise": 180000,
        "net_payable_after": 6750,
        "interpretation": "CATEGORY_SUB_LIMIT_RULE"
      },
      "details": "Prior category usage not supplied; this claim was checked against the full sub_limit on its own."
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "PASS",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": 10000,
        "ytd_source": "claim_payload",
        "remaining": 40000,
        "net_payable_before_limit": 6750
      },
      "details": "Applied to the net payable after discount and co-pay."
    },
    {
      "stage": "policy",
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured_paise": 50000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 675000
      },
      "details": "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "enabled": true,
        "combined_limit_paise": 15000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 675000
      },
      "details": "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."
    },
    {
      "stage": "confidence",
      "rule_id": "confidence_rubric",
      "status": "PASS",
      "evidence": {
        "base": 0.96,
        "outcome_classes": [
          "amount_dependent_rejection",
          "rejection"
        ],
        "factors": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04,
            "applies_to": [
              "payable",
              "review",
              "identity_dependent_rejection"
            ],
            "applied": false
          },
          {
            "reason": "treatment_date_not_corroborated",
            "points": 0.03,
            "applies_to": [
              "payable",
              "date_dependent_rejection"
            ],
            "applied": false
          },
          {
            "reason": "category_usage_not_evaluated",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "applied": false
          }
        ],
        "score": 0.96
      },
      "details": "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it could change that outcome or its amount."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "REJECTED",
      "evidence": {
        "primary_reason": "PER_CLAIM_EXCEEDED",
        "review_reasons": [],
        "approved_amount_paise": 0,
        "post_decision_review_recommended": false
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Consultation Fee",
      "source_document": "F016",
      "amount_paise": 200000,
      "amount": 2000,
      "status": "NOT_ADJUDICATED",
      "reason_code": null,
      "reason": "Passed the line-level checks, but the claim was rejected (PER_CLAIM_EXCEEDED); no amount is payable for this line.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered",
      "line_check": "ELIGIBLE"
    },
    {
      "kind": "line_item",
      "description": "Medicines",
      "source_document": "F016",
      "amount_paise": 550000,
      "amount": 5500,
      "status": "NOT_ADJUDICATED",
      "reason_code": null,
      "reason": "Passed the line-level checks, but the claim was rejected (PER_CLAIM_EXCEEDED); no amount is payable for this line.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered",
      "line_check": "ELIGIBLE"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 750000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -75000,
      "amount": -750,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 750000,
      "percent": 10
    }
  ]
}
```

### TC009: Fraud Signal — Multiple Same-Day Claims

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "MANUAL_REVIEW",
  "decision": "MANUAL_REVIEW",
  "approved_amount": 0,
  "approved_amount_paise": 0,
  "reasons": [
    {
      "code": "SAME_DAY_CLAIMS",
      "message": "This is claim 4 on the same treatment date; policy review threshold is 2. Manual review is required."
    },
    {
      "code": "CATEGORY_SUB_LIMIT_UNVERIFIED",
      "message": "The consultation sub-limit (₹2000 a year, ₹2000 remaining) applies to the consultation service itself, and the bill is not itemized enough to establish that share. An operator must verify it before payment."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.92,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "PASS",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F017",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F018",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        }
      ],
      "details": []
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members[7] / policy_id / opd_categories.consultation",
      "evidence": {
        "member_id": "EMP008",
        "category": "CONSULTATION"
      }
    },
    {
      "stage": "identity",
      "rule_id": "patient_identity",
      "status": "NOT_EVALUATED",
      "details": "No patient name was extracted from the documents; the claim is attributed to the submitting member."
    },
    {
      "stage": "identity",
      "rule_id": "roster_patient_match",
      "status": "NOT_EVALUATED",
      "policy_ref": "members",
      "evidence": {
        "document_names": [],
        "allowed_names": [
          "ravi menon"
        ],
        "unresolved_roster_dependents": []
      }
    },
    {
      "stage": "identity",
      "rule_id": "identity_verification",
      "status": "NOT_EVALUATED",
      "evidence": {
        "document_layer_status": null
      },
      "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."
    },
    {
      "stage": "reconciliation",
      "rule_id": "bill_amount",
      "status": "PASS",
      "evidence": {
        "claimed_paise": 480000,
        "bill_total_paise": 480000,
        "line_items_total_paise": null,
        "bill_files": [
          "F018"
        ]
      }
    },
    {
      "stage": "reconciliation",
      "rule_id": "document_quality",
      "status": "PASS",
      "evidence": {
        "weak_documents": [],
        "required_types": [
          "HOSPITAL_BILL",
          "PRESCRIPTION"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "document_treatment_date",
      "status": "NOT_EVALUATED",
      "policy_ref": "claim.treatment_date",
      "evidence": {},
      "details": "No comparable document date was extracted."
    },
    {
      "stage": "policy",
      "rule_id": "policy_renewal_status",
      "status": "PASS",
      "policy_ref": "policy_holder.renewal_status",
      "evidence": {
        "renewal_status": "ACTIVE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_relationship",
      "status": "PASS",
      "policy_ref": "coverage.family_floater.covered_relationships",
      "evidence": {
        "relationship": "SELF",
        "covered_relationship": "SELF"
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_covered",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.covered",
      "evidence": {
        "covered": true
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_coverage_period",
      "status": "PASS",
      "policy_ref": "policy_holder.policy_start_date / policy_holder.policy_end_date",
      "evidence": {
        "treatment_date": "2024-10-30",
        "policy_start_date": "2024-04-01",
        "policy_end_date": "2025-03-31"
      }
    },
    {
      "stage": "policy",
      "rule_id": "minimum_claim_amount",
      "status": "PASS",
      "policy_ref": "submission_rules.minimum_claim_amount",
      "evidence": {
        "claimed_amount_paise": 480000,
        "minimum_claim_amount_paise": 50000
      }
    },
    {
      "stage": "policy",
      "rule_id": "submission_deadline",
      "status": "NOT_EVALUATED",
      "policy_ref": "submission_rules.deadline_days_from_treatment",
      "details": "Submission date absent."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365,
        "conditions": []
      },
      "details": "No explicit pre-existing-condition evidence was supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "matched_terms": [],
        "join_date": "2024-04-01",
        "join_date_source": "members[7].join_date",
        "treatment_date": "2024-10-30",
        "eligible_from": "2024-05-01"
      }
    },
    {
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
    },
    {
      "stage": "policy",
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth / pre_authorization.required_for",
      "details": "Not required for the services and amount in this claim."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "FLAG",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 4,
        "limit": 2,
        "prior_claims": [
          {
            "claim_id": "CLM_0081",
            "date": "2024-10-30",
            "amount": 1200,
            "provider": "City Clinic A"
          },
          {
            "claim_id": "CLM_0082",
            "date": "2024-10-30",
            "amount": 1800,
            "provider": "City Clinic B"
          },
          {
            "claim_id": "CLM_0083",
            "date": "2024-10-30",
            "amount": 2100,
            "provider": "Wellness Center"
          }
        ],
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "monthly_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.monthly_claims_limit",
      "evidence": {
        "monthly_claim_count_including_current": 4,
        "limit": 6,
        "month": "2024-10",
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "high_value_claim",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.high_value_claim_threshold",
      "evidence": {
        "claimed_amount_paise": 480000,
        "threshold_paise": 2500000
      },
      "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."
    },
    {
      "stage": "risk",
      "rule_id": "auto_manual_review_amount",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.auto_manual_review_above",
      "evidence": {
        "claimed_amount_paise": 480000,
        "threshold_paise": 2500000
      }
    },
    {
      "stage": "risk",
      "rule_id": "fraud_score",
      "status": "NOT_EVALUATED",
      "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold",
      "evidence": {
        "score": null,
        "threshold": 0.8
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false,
      "evidence": [
        {
          "signal": "trailing_30_day_value",
          "status": "PASS",
          "evidence": {
            "claims_in_window": 4,
            "value_paise": 990000,
            "threshold_paise": 2500000,
            "window": [
              "2024-09-30",
              "2024-10-30"
            ]
          },
          "policy_ref": "fraud_thresholds.high_value_claim_threshold"
        },
        {
          "signal": "repeat_billing",
          "status": "NOT_EVALUATED",
          "details": "Provider or amount unavailable for this claim or its history."
        }
      ]
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 4800,
        "eligible_amount": 4800,
        "limit": 5000,
        "global_per_claim_limit": 5000,
        "category_sub_limit": 2000,
        "limit_source": "coverage.per_claim_limit",
        "interpretation": "PER_CLAIM_CEILING_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "network_hospital",
      "status": "NOT_EVALUATED",
      "policy_ref": "network_hospitals",
      "evidence": {
        "hospital_name": null,
        "match": null,
        "match_rule": "exact_or_branch_suffix",
        "discount_percent": 0
      }
    },
    {
      "stage": "policy",
      "rule_id": "generic_medicine_requirement",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.generic_mandatory",
      "evidence": {
        "generic_mandatory": false,
        "branded_lines": []
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 480000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 48000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 432000
      },
      "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "sub_limit": 2000,
        "period": "policy_year_per_member",
        "service_scope": "matching_lines",
        "usage_key": null,
        "usage_basis": null,
        "used": null,
        "remaining_before_claim": 2000,
        "service_net_payable": null,
        "counted_against_sub_limit_paise": null,
        "net_payable_after": 4320,
        "interpretation": "CATEGORY_SUB_LIMIT_RULE"
      },
      "details": "The bill is not itemized, so the category's own service share cannot be established."
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": null,
        "ytd_source": null,
        "remaining": null,
        "net_payable_before_limit": 4320
      },
      "details": "Year-to-date OPD usage was not supplied; the annual limit is applied at settlement against the utilisation ledger."
    },
    {
      "stage": "policy",
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured_paise": 50000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 432000
      },
      "details": "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "enabled": true,
        "combined_limit_paise": 15000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 432000
      },
      "details": "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."
    },
    {
      "stage": "confidence",
      "rule_id": "confidence_rubric",
      "status": "DEGRADED",
      "evidence": {
        "base": 0.96,
        "outcome_classes": [
          "review"
        ],
        "factors": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04,
            "applies_to": [
              "payable",
              "review",
              "identity_dependent_rejection"
            ],
            "applied": true
          },
          {
            "reason": "treatment_date_not_corroborated",
            "points": 0.03,
            "applies_to": [
              "payable",
              "date_dependent_rejection"
            ],
            "applied": false
          },
          {
            "reason": "category_usage_not_evaluated",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "applied": false
          },
          {
            "reason": "annual_opd_usage_not_evaluated",
            "points": 0.04,
            "applies_to": [
              "payable"
            ],
            "applied": false
          }
        ],
        "score": 0.92
      },
      "details": "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it could change that outcome or its amount."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "MANUAL_REVIEW",
      "evidence": {
        "primary_reason": "SAME_DAY_CLAIMS",
        "review_reasons": [
          "CATEGORY_SUB_LIMIT_UNVERIFIED",
          "SAME_DAY_CLAIMS"
        ],
        "approved_amount_paise": 0,
        "post_decision_review_recommended": false
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Bill total (not itemized)",
      "source_document": "F018",
      "amount_paise": 480000,
      "amount": 4800,
      "status": "NOT_ADJUDICATED",
      "reason_code": null,
      "reason": "Passed the line-level checks, but the claim was routed to manual review; no amount is payable for this line.",
      "itemized": false,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered",
      "line_check": "ELIGIBLE"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 480000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -48000,
      "amount": -480,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 480000,
      "percent": 10
    }
  ]
}
```

### TC010: Network Hospital — Discount Applied

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "DECIDED",
  "decision": "APPROVED",
  "approved_amount": 3240,
  "approved_amount_paise": 324000,
  "reasons": [
    {
      "code": "TREATMENT_DATE_NOT_CORROBORATED",
      "message": "No document carries a readable date, so the timing rules used the submitted treatment date without corroboration."
    },
    {
      "code": "CATEGORY_SUB_LIMIT_HISTORY_NOT_EVALUATED",
      "message": "Earlier consultation benefit this policy year was not supplied; this claim was checked against the full ₹2000 consultation sub-limit on its own."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.9,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "PASS",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F019",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": "Deepak Shah",
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F020",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": "Deepak Shah",
          "provenance": "fixture_metadata"
        }
      ],
      "details": []
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members[9] / policy_id / opd_categories.consultation",
      "evidence": {
        "member_id": "EMP010",
        "category": "CONSULTATION"
      }
    },
    {
      "stage": "identity",
      "rule_id": "roster_patient_match",
      "status": "PASS",
      "policy_ref": "members",
      "evidence": {
        "document_names": [
          "Deepak Shah",
          "Deepak Shah"
        ],
        "allowed_names": [
          "deepak shah"
        ],
        "unresolved_roster_dependents": [
          "DEP006"
        ]
      }
    },
    {
      "stage": "identity",
      "rule_id": "identity_verification",
      "status": "NOT_EVALUATED",
      "evidence": {
        "document_layer_status": null
      },
      "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."
    },
    {
      "stage": "reconciliation",
      "rule_id": "bill_amount",
      "status": "PASS",
      "evidence": {
        "claimed_paise": 450000,
        "bill_total_paise": 450000,
        "line_items_total_paise": 450000,
        "bill_files": [
          "F020"
        ]
      }
    },
    {
      "stage": "reconciliation",
      "rule_id": "document_quality",
      "status": "PASS",
      "evidence": {
        "weak_documents": [],
        "required_types": [
          "HOSPITAL_BILL",
          "PRESCRIPTION"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "document_treatment_date",
      "status": "NOT_EVALUATED",
      "policy_ref": "claim.treatment_date",
      "evidence": {},
      "details": "No comparable document date was extracted."
    },
    {
      "stage": "policy",
      "rule_id": "policy_renewal_status",
      "status": "PASS",
      "policy_ref": "policy_holder.renewal_status",
      "evidence": {
        "renewal_status": "ACTIVE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_relationship",
      "status": "PASS",
      "policy_ref": "coverage.family_floater.covered_relationships",
      "evidence": {
        "relationship": "SELF",
        "covered_relationship": "SELF"
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_covered",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.covered",
      "evidence": {
        "covered": true
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_coverage_period",
      "status": "PASS",
      "policy_ref": "policy_holder.policy_start_date / policy_holder.policy_end_date",
      "evidence": {
        "treatment_date": "2024-11-03",
        "policy_start_date": "2024-04-01",
        "policy_end_date": "2025-03-31"
      }
    },
    {
      "stage": "policy",
      "rule_id": "minimum_claim_amount",
      "status": "PASS",
      "policy_ref": "submission_rules.minimum_claim_amount",
      "evidence": {
        "claimed_amount_paise": 450000,
        "minimum_claim_amount_paise": 50000
      }
    },
    {
      "stage": "policy",
      "rule_id": "submission_deadline",
      "status": "NOT_EVALUATED",
      "policy_ref": "submission_rules.deadline_days_from_treatment",
      "details": "Submission date absent."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365,
        "conditions": []
      },
      "details": "No explicit pre-existing-condition evidence was supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "matched_terms": [],
        "join_date": "2024-04-01",
        "join_date_source": "members[9].join_date",
        "treatment_date": "2024-11-03",
        "eligible_from": "2024-05-01"
      }
    },
    {
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
    },
    {
      "stage": "policy",
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth / pre_authorization.required_for",
      "details": "Not required for the services and amount in this claim."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "prior_claims": [],
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "monthly_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.monthly_claims_limit",
      "evidence": {
        "monthly_claim_count_including_current": 1,
        "limit": 6,
        "month": "2024-11",
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "high_value_claim",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.high_value_claim_threshold",
      "evidence": {
        "claimed_amount_paise": 450000,
        "threshold_paise": 2500000
      },
      "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."
    },
    {
      "stage": "risk",
      "rule_id": "auto_manual_review_amount",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.auto_manual_review_above",
      "evidence": {
        "claimed_amount_paise": 450000,
        "threshold_paise": 2500000
      }
    },
    {
      "stage": "risk",
      "rule_id": "fraud_score",
      "status": "NOT_EVALUATED",
      "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold",
      "evidence": {
        "score": null,
        "threshold": 0.8
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false,
      "evidence": [
        {
          "signal": "trailing_30_day_value",
          "status": "PASS",
          "evidence": {
            "claims_in_window": 1,
            "value_paise": 450000,
            "threshold_paise": 2500000,
            "window": [
              "2024-10-04",
              "2024-11-03"
            ]
          },
          "policy_ref": "fraud_thresholds.high_value_claim_threshold"
        },
        {
          "signal": "repeat_billing",
          "status": "PASS",
          "evidence": {
            "matching_claims": [],
            "provider": "apollo hospitals",
            "amount_paise": 450000
          }
        }
      ]
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 4500,
        "eligible_amount": 4500,
        "limit": 5000,
        "global_per_claim_limit": 5000,
        "category_sub_limit": 2000,
        "limit_source": "coverage.per_claim_limit",
        "interpretation": "PER_CLAIM_CEILING_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "network_hospital",
      "status": "PASS",
      "policy_ref": "network_hospitals",
      "evidence": {
        "hospital_name": "Apollo Hospitals",
        "match": {
          "network_hospital": "Apollo Hospitals",
          "matched_term": "apollo hospitals",
          "provenance": "policy_text",
          "policy_ref": "network_hospitals[0]"
        },
        "match_rule": "exact_or_branch_suffix",
        "discount_percent": 20
      }
    },
    {
      "stage": "policy",
      "rule_id": "generic_medicine_requirement",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.generic_mandatory",
      "evidence": {
        "generic_mandatory": false,
        "branded_lines": []
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 450000,
        "network_hospital": true,
        "network_discount_paise": 90000,
        "copay_paise": 36000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 324000
      },
      "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "sub_limit": 2000,
        "period": "policy_year_per_member",
        "service_scope": "matching_lines",
        "usage_key": null,
        "usage_basis": null,
        "used": null,
        "remaining_before_claim": 2000,
        "service_net_payable": 1080,
        "counted_against_sub_limit_paise": 108000,
        "net_payable_after": 3240,
        "interpretation": "CATEGORY_SUB_LIMIT_RULE"
      },
      "details": "Prior category usage not supplied; this claim was checked against the full sub_limit on its own."
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "PASS",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": 8000,
        "ytd_source": "claim_payload",
        "remaining": 42000,
        "net_payable_before_limit": 3240
      },
      "details": "Applied to the net payable after discount and co-pay."
    },
    {
      "stage": "policy",
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured_paise": 50000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 324000
      },
      "details": "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "enabled": true,
        "combined_limit_paise": 15000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 324000
      },
      "details": "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."
    },
    {
      "stage": "confidence",
      "rule_id": "confidence_rubric",
      "status": "DEGRADED",
      "evidence": {
        "base": 0.96,
        "outcome_classes": [
          "payable"
        ],
        "factors": [
          {
            "reason": "treatment_date_not_corroborated",
            "points": 0.03,
            "applies_to": [
              "payable",
              "date_dependent_rejection"
            ],
            "applied": true
          },
          {
            "reason": "category_usage_not_evaluated",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "applied": true
          }
        ],
        "score": 0.9
      },
      "details": "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it could change that outcome or its amount."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "APPROVED",
      "evidence": {
        "primary_reason": null,
        "review_reasons": [],
        "approved_amount_paise": 324000,
        "post_decision_review_recommended": false
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Consultation Fee",
      "source_document": "F020",
      "amount_paise": 150000,
      "amount": 1500,
      "status": "ELIGIBLE",
      "reason_code": null,
      "reason": "Covered.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "line_item",
      "description": "Medicines",
      "source_document": "F020",
      "amount_paise": 300000,
      "amount": 3000,
      "status": "ELIGIBLE",
      "reason_code": null,
      "reason": "Covered.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": -90000,
      "amount": -900,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 450000,
      "percent": 20
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -36000,
      "amount": -360,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 360000,
      "percent": 10
    }
  ]
}
```

### TC011: Component Failure — Graceful Degradation

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

TC011 confidence with the component working: 0.79.
```json
{
  "state": "DECIDED",
  "decision": "APPROVED",
  "approved_amount": 4000,
  "approved_amount_paise": 400000,
  "reasons": [
    {
      "code": "COMPONENT_DEGRADED",
      "message": "The risk-signal enrichment component (30-day claim value and repeat-billing signals) failed and was skipped. All mandatory document, policy and fraud-threshold checks completed and the decision rests on them, but processing was incomplete, so a manual review of this decision is recommended."
    },
    {
      "code": "PATIENT_IDENTITY_NOT_VERIFIED",
      "message": "No patient name was readable on the documents; payment is attributed to the submitting member and should be verified at settlement."
    },
    {
      "code": "TREATMENT_DATE_NOT_CORROBORATED",
      "message": "No document carries a readable date, so the timing rules used the submitted treatment date without corroboration."
    },
    {
      "code": "SESSION_HISTORY_NOT_EVALUATED",
      "message": "Prior sessions this year were not supplied; this claim's 5 sessions are within the 20-session annual cap on their own."
    },
    {
      "code": "CATEGORY_SUB_LIMIT_HISTORY_NOT_EVALUATED",
      "message": "Earlier alternative_medicine benefit this policy year was not supplied; this claim was checked against the full ₹8000 alternative_medicine sub-limit on its own."
    },
    {
      "code": "ANNUAL_LIMIT_NOT_EVALUATED",
      "message": "Year-to-date OPD usage was not supplied, so the ₹50000 annual OPD limit was not evaluated. Payment is subject to the member's remaining annual OPD balance."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.56,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "PASS",
      "policy_ref": "document_requirements.ALTERNATIVE_MEDICINE",
      "evidence": [
        {
          "file_id": "F021",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F022",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        }
      ],
      "details": []
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members[5] / policy_id / opd_categories.alternative_medicine",
      "evidence": {
        "member_id": "EMP006",
        "category": "ALTERNATIVE_MEDICINE"
      }
    },
    {
      "stage": "identity",
      "rule_id": "patient_identity",
      "status": "NOT_EVALUATED",
      "details": "No patient name was extracted from the documents; the claim is attributed to the submitting member."
    },
    {
      "stage": "identity",
      "rule_id": "roster_patient_match",
      "status": "NOT_EVALUATED",
      "policy_ref": "members",
      "evidence": {
        "document_names": [],
        "allowed_names": [
          "kavita nair"
        ],
        "unresolved_roster_dependents": []
      }
    },
    {
      "stage": "identity",
      "rule_id": "identity_verification",
      "status": "NOT_EVALUATED",
      "evidence": {
        "document_layer_status": null
      },
      "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."
    },
    {
      "stage": "reconciliation",
      "rule_id": "bill_amount",
      "status": "PASS",
      "evidence": {
        "claimed_paise": 400000,
        "bill_total_paise": 400000,
        "line_items_total_paise": 400000,
        "bill_files": [
          "F022"
        ]
      }
    },
    {
      "stage": "reconciliation",
      "rule_id": "document_quality",
      "status": "PASS",
      "evidence": {
        "weak_documents": [],
        "required_types": [
          "HOSPITAL_BILL",
          "PRESCRIPTION"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "document_treatment_date",
      "status": "NOT_EVALUATED",
      "policy_ref": "claim.treatment_date",
      "evidence": {},
      "details": "No comparable document date was extracted."
    },
    {
      "stage": "policy",
      "rule_id": "policy_renewal_status",
      "status": "PASS",
      "policy_ref": "policy_holder.renewal_status",
      "evidence": {
        "renewal_status": "ACTIVE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_relationship",
      "status": "PASS",
      "policy_ref": "coverage.family_floater.covered_relationships",
      "evidence": {
        "relationship": "SELF",
        "covered_relationship": "SELF"
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_covered",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.covered",
      "evidence": {
        "covered": true
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_coverage_period",
      "status": "PASS",
      "policy_ref": "policy_holder.policy_start_date / policy_holder.policy_end_date",
      "evidence": {
        "treatment_date": "2024-10-28",
        "policy_start_date": "2024-04-01",
        "policy_end_date": "2025-03-31"
      }
    },
    {
      "stage": "policy",
      "rule_id": "minimum_claim_amount",
      "status": "PASS",
      "policy_ref": "submission_rules.minimum_claim_amount",
      "evidence": {
        "claimed_amount_paise": 400000,
        "minimum_claim_amount_paise": 50000
      }
    },
    {
      "stage": "policy",
      "rule_id": "submission_deadline",
      "status": "NOT_EVALUATED",
      "policy_ref": "submission_rules.deadline_days_from_treatment",
      "details": "Submission date absent."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365,
        "conditions": []
      },
      "details": "No explicit pre-existing-condition evidence was supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "matched_terms": [],
        "join_date": "2024-04-01",
        "join_date_source": "members[5].join_date",
        "treatment_date": "2024-10-28",
        "eligible_from": "2024-05-01"
      }
    },
    {
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
    },
    {
      "stage": "policy",
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.requires_pre_auth / pre_authorization.required_for",
      "details": "Not required for the services and amount in this claim."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "prior_claims": [],
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "monthly_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.monthly_claims_limit",
      "evidence": {
        "monthly_claim_count_including_current": 1,
        "limit": 6,
        "month": "2024-10",
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "high_value_claim",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.high_value_claim_threshold",
      "evidence": {
        "claimed_amount_paise": 400000,
        "threshold_paise": 2500000
      },
      "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."
    },
    {
      "stage": "risk",
      "rule_id": "auto_manual_review_amount",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.auto_manual_review_above",
      "evidence": {
        "claimed_amount_paise": 400000,
        "threshold_paise": 2500000
      }
    },
    {
      "stage": "risk",
      "rule_id": "fraud_score",
      "status": "NOT_EVALUATED",
      "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold",
      "evidence": {
        "score": null,
        "threshold": 0.8
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "SKIPPED_COMPONENT_FAILURE",
      "degraded": true,
      "error_type": "RuntimeError",
      "details": "The risk-signal enrichment component failed and was skipped; mandatory document, policy and fraud-threshold checks completed."
    },
    {
      "stage": "policy",
      "rule_id": "covered_system",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.covered_systems",
      "evidence": {
        "matched": [
          {
            "system": "Ayurveda",
            "matched_terms": [
              {
                "text": "panchakarma",
                "provenance": "interpretation"
              },
              {
                "text": "vaidya",
                "provenance": "interpretation"
              }
            ]
          }
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "max_sessions",
      "status": "NOT_EVALUATED",
      "policy_ref": "opd_categories.alternative_medicine.max_sessions_per_year",
      "evidence": {
        "current_sessions": 5,
        "prior_sessions": null,
        "total_sessions": null,
        "max_sessions_per_year": 20,
        "history_source": null
      },
      "details": "Prior session history was not supplied; only this claim's sessions were checked."
    },
    {
      "stage": "policy",
      "rule_id": "registered_practitioner",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.requires_registered_practitioner",
      "evidence": {
        "registrations": [
          "AYUR/KL/2345/2019"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.sub_limit",
      "evidence": {
        "claimed_amount": 4000,
        "eligible_amount": 4000,
        "limit": 8000,
        "global_per_claim_limit": 5000,
        "category_sub_limit": 8000,
        "limit_source": "opd_categories.alternative_medicine.sub_limit",
        "interpretation": "PER_CLAIM_CEILING_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "network_hospital",
      "status": "NOT_APPLICABLE",
      "policy_ref": "network_hospitals",
      "evidence": {
        "hospital_name": "Ayur Wellness Centre",
        "match": null,
        "match_rule": "exact_or_branch_suffix",
        "discount_percent": 0
      }
    },
    {
      "stage": "policy",
      "rule_id": "generic_medicine_requirement",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.generic_mandatory",
      "evidence": {
        "generic_mandatory": false,
        "branded_lines": []
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 400000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 0,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 400000
      },
      "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.sub_limit",
      "evidence": {
        "sub_limit": 8000,
        "period": "policy_year_per_member",
        "service_scope": "all_eligible_lines",
        "usage_key": null,
        "usage_basis": null,
        "used": null,
        "remaining_before_claim": 8000,
        "service_net_payable": 4000,
        "counted_against_sub_limit_paise": 400000,
        "net_payable_after": 4000,
        "interpretation": "CATEGORY_SUB_LIMIT_RULE"
      },
      "details": "Prior category usage not supplied; this claim was checked against the full sub_limit on its own."
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": null,
        "ytd_source": null,
        "remaining": null,
        "net_payable_before_limit": 4000
      },
      "details": "Year-to-date OPD usage was not supplied; the annual limit is applied at settlement against the utilisation ledger."
    },
    {
      "stage": "policy",
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured_paise": 50000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 400000
      },
      "details": "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "enabled": true,
        "combined_limit_paise": 15000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 400000
      },
      "details": "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."
    },
    {
      "stage": "confidence",
      "rule_id": "confidence_rubric",
      "status": "DEGRADED",
      "evidence": {
        "base": 0.96,
        "outcome_classes": [
          "payable"
        ],
        "factors": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04,
            "applies_to": [
              "payable",
              "review",
              "identity_dependent_rejection"
            ],
            "applied": true
          },
          {
            "reason": "treatment_date_not_corroborated",
            "points": 0.03,
            "applies_to": [
              "payable",
              "date_dependent_rejection"
            ],
            "applied": true
          },
          {
            "reason": "component_failure",
            "points": 0.23,
            "applies_to": [
              "payable",
              "review",
              "rejection"
            ],
            "component": "risk_enrichment",
            "applied": true
          },
          {
            "reason": "session_history_not_evaluated",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "applied": true
          },
          {
            "reason": "category_usage_not_evaluated",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "applied": true
          },
          {
            "reason": "annual_opd_usage_not_evaluated",
            "points": 0.04,
            "applies_to": [
              "payable"
            ],
            "applied": true
          }
        ],
        "score": 0.56
      },
      "details": "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it could change that outcome or its amount."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "APPROVED",
      "evidence": {
        "primary_reason": null,
        "review_reasons": [],
        "approved_amount_paise": 400000,
        "post_decision_review_recommended": true
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Panchakarma Therapy (5 sessions)",
      "source_document": "F022",
      "amount_paise": 300000,
      "amount": 3000,
      "status": "ELIGIBLE",
      "reason_code": null,
      "reason": "Covered.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.alternative_medicine.covered"
    },
    {
      "kind": "line_item",
      "description": "Consultation",
      "source_document": "F022",
      "amount_paise": 100000,
      "amount": 1000,
      "status": "ELIGIBLE",
      "reason_code": null,
      "reason": "Covered.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.alternative_medicine.covered"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.alternative_medicine.network_discount_percent",
      "basis_paise": 400000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.alternative_medicine.copay_percent",
      "basis_paise": 400000,
      "percent": 0
    }
  ]
}
```

### TC012: Excluded Treatment

Match: **Yes**. No discrepancies.
Explicit behavior checks: **Passed**.

```json
{
  "state": "DECIDED",
  "decision": "REJECTED",
  "approved_amount": 0,
  "approved_amount_paise": 0,
  "reasons": [
    {
      "code": "EXCLUDED_CONDITION",
      "message": "Treatment is excluded under the policy: Obesity and weight loss programs."
    },
    {
      "code": "WAITING_PERIOD",
      "message": "The obesity treatment waiting period ends on 2025-04-01; treatment was on 2024-10-18. Claims for this condition are eligible from 2025-04-01."
    },
    {
      "code": "EXCLUDED_PROCEDURE",
      "message": "Bariatric Consultation is excluded (Obesity and weight loss programs); ₹3000 removed."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.96,
  "trace": [
    {
      "stage": "configuration",
      "rule_id": "policy_source",
      "status": "LOADED",
      "policy_ref": "policy",
      "evidence": {
        "policy_id": "PLUM_GHI_2024",
        "schema_version": "plum.canonical_policy.v1",
        "source_sha256": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
        "canonical_sha256": "36e830200717ec720d7ce6a12a716cc2269d3d1d6e06c93b077312346d0fa160",
        "canonical_sha256_verified": true,
        "audit_sha256": "18a7e050eef431aacf4be838582123aa30c1a29e0498c5cc973a1c2cc8ba3fa8",
        "audit_entries": 100,
        "interpretation_entries": 56,
        "conflict_resolutions": [
          "DENTAL_REPORT_CONFLICT.DENTAL",
          "PER_CLAIM_CEILING_RULE",
          "CATEGORY_SUB_LIMIT_RULE",
          "EXCLUSION_MERGED.DENTAL.teeth_whitening",
          "EXCLUSION_MERGED.DENTAL.orthodontic_treatment",
          "EXCLUSION_MERGED.VISION.lasik_surgery",
          "EXCLUSION_MERGED.VISION.refractive_surgery",
          "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan"
        ],
        "audit": "<100 entries; see 'Normalizer audit trail' above>"
      },
      "details": "Canonical policy configuration, verified against its fingerprint. The complete normalizer audit trail that produced it is recorded here so it is persisted with the decision."
    },
    {
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "PASS",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F023",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        },
        {
          "file_id": "F024",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null,
          "provenance": "fixture_metadata"
        }
      ],
      "details": []
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members[8] / policy_id / opd_categories.consultation",
      "evidence": {
        "member_id": "EMP009",
        "category": "CONSULTATION"
      }
    },
    {
      "stage": "identity",
      "rule_id": "patient_identity",
      "status": "NOT_EVALUATED",
      "details": "No patient name was extracted from the documents; the claim is attributed to the submitting member."
    },
    {
      "stage": "identity",
      "rule_id": "roster_patient_match",
      "status": "NOT_EVALUATED",
      "policy_ref": "members",
      "evidence": {
        "document_names": [],
        "allowed_names": [
          "anita desai"
        ],
        "unresolved_roster_dependents": []
      }
    },
    {
      "stage": "identity",
      "rule_id": "identity_verification",
      "status": "NOT_EVALUATED",
      "evidence": {
        "document_layer_status": null
      },
      "details": "Identity verdict supplied by the document layer; an explicit failed or unverified verdict blocks payment."
    },
    {
      "stage": "reconciliation",
      "rule_id": "bill_amount",
      "status": "PASS",
      "evidence": {
        "claimed_paise": 800000,
        "bill_total_paise": 800000,
        "line_items_total_paise": 800000,
        "bill_files": [
          "F024"
        ]
      }
    },
    {
      "stage": "reconciliation",
      "rule_id": "document_quality",
      "status": "PASS",
      "evidence": {
        "weak_documents": [],
        "required_types": [
          "HOSPITAL_BILL",
          "PRESCRIPTION"
        ]
      }
    },
    {
      "stage": "policy",
      "rule_id": "document_treatment_date",
      "status": "NOT_EVALUATED",
      "policy_ref": "claim.treatment_date",
      "evidence": {},
      "details": "No comparable document date was extracted."
    },
    {
      "stage": "policy",
      "rule_id": "policy_renewal_status",
      "status": "PASS",
      "policy_ref": "policy_holder.renewal_status",
      "evidence": {
        "renewal_status": "ACTIVE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_relationship",
      "status": "PASS",
      "policy_ref": "coverage.family_floater.covered_relationships",
      "evidence": {
        "relationship": "SELF",
        "covered_relationship": "SELF"
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_covered",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.covered",
      "evidence": {
        "covered": true
      }
    },
    {
      "stage": "policy",
      "rule_id": "policy_coverage_period",
      "status": "PASS",
      "policy_ref": "policy_holder.policy_start_date / policy_holder.policy_end_date",
      "evidence": {
        "treatment_date": "2024-10-18",
        "policy_start_date": "2024-04-01",
        "policy_end_date": "2025-03-31"
      }
    },
    {
      "stage": "policy",
      "rule_id": "minimum_claim_amount",
      "status": "PASS",
      "policy_ref": "submission_rules.minimum_claim_amount",
      "evidence": {
        "claimed_amount_paise": 800000,
        "minimum_claim_amount_paise": 50000
      }
    },
    {
      "stage": "policy",
      "rule_id": "submission_deadline",
      "status": "NOT_EVALUATED",
      "policy_ref": "submission_rules.deadline_days_from_treatment",
      "details": "Submission date absent."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365,
        "conditions": []
      },
      "details": "No explicit pre-existing-condition evidence was supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "waiting_period",
      "status": "FAIL",
      "policy_ref": "waiting_periods.specific_conditions.obesity_treatment",
      "evidence": {
        "condition": "obesity_treatment",
        "matched_terms": [
          {
            "text": "obesity",
            "provenance": "policy_text"
          },
          {
            "text": "bariatric",
            "provenance": "interpretation"
          }
        ],
        "join_date": "2024-04-01",
        "join_date_source": "members[8].join_date",
        "treatment_date": "2024-10-18",
        "eligible_from": "2025-04-01"
      }
    },
    {
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "FAIL",
      "policy_ref": "exclusions.conditions",
      "evidence": [
        {
          "exclusion_id": "ALL:obesity_and_weight_loss_programs",
          "label": "Obesity and weight loss programs",
          "matched_terms": [
            {
              "text": "obesity",
              "provenance": "policy_text"
            },
            {
              "text": "bariatric",
              "provenance": "interpretation"
            }
          ],
          "qualifier": null,
          "policy_text_match": true,
          "source_paths": [
            "exclusions.conditions[5]"
          ]
        }
      ]
    },
    {
      "stage": "policy",
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth / pre_authorization.required_for",
      "details": "Not required for the services and amount in this claim."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "prior_claims": [],
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "monthly_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.monthly_claims_limit",
      "evidence": {
        "monthly_claim_count_including_current": 1,
        "limit": 6,
        "month": "2024-10",
        "history_source": "claim_payload"
      }
    },
    {
      "stage": "risk",
      "rule_id": "high_value_claim",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.high_value_claim_threshold",
      "evidence": {
        "claimed_amount_paise": 800000,
        "threshold_paise": 2500000
      },
      "details": "Single-claim marker; routing is by auto_manual_review_above (FRAUD_THRESHOLD_ROLES)."
    },
    {
      "stage": "risk",
      "rule_id": "auto_manual_review_amount",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.auto_manual_review_above",
      "evidence": {
        "claimed_amount_paise": 800000,
        "threshold_paise": 2500000
      }
    },
    {
      "stage": "risk",
      "rule_id": "fraud_score",
      "status": "NOT_EVALUATED",
      "policy_ref": "fraud_thresholds.fraud_score_manual_review_threshold",
      "evidence": {
        "score": null,
        "threshold": 0.8
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false,
      "evidence": [
        {
          "signal": "trailing_30_day_value",
          "status": "PASS",
          "evidence": {
            "claims_in_window": 1,
            "value_paise": 800000,
            "threshold_paise": 2500000,
            "window": [
              "2024-09-18",
              "2024-10-18"
            ]
          },
          "policy_ref": "fraud_thresholds.high_value_claim_threshold"
        },
        {
          "signal": "repeat_billing",
          "status": "NOT_EVALUATED",
          "details": "Provider or amount unavailable for this claim or its history."
        }
      ]
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 8000,
        "eligible_amount": 5000,
        "limit": 5000,
        "global_per_claim_limit": 5000,
        "category_sub_limit": 2000,
        "limit_source": "coverage.per_claim_limit",
        "interpretation": "PER_CLAIM_CEILING_RULE"
      }
    },
    {
      "stage": "policy",
      "rule_id": "network_hospital",
      "status": "NOT_EVALUATED",
      "policy_ref": "network_hospitals",
      "evidence": {
        "hospital_name": null,
        "match": null,
        "match_rule": "exact_or_branch_suffix",
        "discount_percent": 0
      }
    },
    {
      "stage": "policy",
      "rule_id": "generic_medicine_requirement",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.generic_mandatory",
      "evidence": {
        "generic_mandatory": false,
        "branded_lines": []
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 500000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 50000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 450000
      },
      "details": "Network discount applied before co-pay; benefit limits are applied to this net amount next."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "LIMITED",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "sub_limit": 2000,
        "period": "policy_year_per_member",
        "service_scope": "matching_lines",
        "usage_key": null,
        "usage_basis": null,
        "used": null,
        "remaining_before_claim": 2000,
        "service_net_payable": 4500,
        "counted_against_sub_limit_paise": 200000,
        "net_payable_after": 2000,
        "interpretation": "CATEGORY_SUB_LIMIT_RULE"
      },
      "details": "Prior category usage not supplied; this claim was checked against the full sub_limit on its own."
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": null,
        "ytd_source": null,
        "remaining": null,
        "net_payable_before_limit": 2000
      },
      "details": "Year-to-date OPD usage was not supplied; the annual limit is applied at settlement against the utilisation ledger."
    },
    {
      "stage": "policy",
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured_paise": 50000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 200000
      },
      "details": "Aggregate limit on the net payable; applied only when utilisation is supplied with the claim."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "enabled": true,
        "combined_limit_paise": 15000000,
        "used_paise": null,
        "remaining_paise": null,
        "net_payable_before_limit_paise": 200000
      },
      "details": "Aggregate limit on the net payable; applied only when family utilisation is supplied with the claim."
    },
    {
      "stage": "confidence",
      "rule_id": "confidence_rubric",
      "status": "PASS",
      "evidence": {
        "base": 0.96,
        "outcome_classes": [
          "rejection"
        ],
        "factors": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04,
            "applies_to": [
              "payable",
              "review",
              "identity_dependent_rejection"
            ],
            "applied": false
          },
          {
            "reason": "treatment_date_not_corroborated",
            "points": 0.03,
            "applies_to": [
              "payable",
              "date_dependent_rejection"
            ],
            "applied": false
          },
          {
            "reason": "line_exclusion_matched_by_interpretation_only",
            "points": 0.06,
            "applies_to": [
              "payable"
            ],
            "lines": [
              "Bariatric Consultation"
            ],
            "applied": false
          },
          {
            "reason": "category_usage_not_evaluated",
            "points": 0.03,
            "applies_to": [
              "payable"
            ],
            "applied": false
          },
          {
            "reason": "annual_opd_usage_not_evaluated",
            "points": 0.04,
            "applies_to": [
              "payable"
            ],
            "applied": false
          }
        ],
        "score": 0.96
      },
      "details": "Heuristic evidence-completeness rubric, not a calibrated probability. The base score is reduced only by unknowns and degradations that are material to the outcome reached: an unknown is material when resolving it could change that outcome or its amount."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "REJECTED",
      "evidence": {
        "primary_reason": "EXCLUDED_CONDITION",
        "review_reasons": [],
        "approved_amount_paise": 0,
        "post_decision_review_recommended": false
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Bariatric Consultation",
      "source_document": "F024",
      "amount_paise": 300000,
      "amount": 3000,
      "status": "EXCLUDED",
      "reason_code": "EXCLUDED_PROCEDURE",
      "reason": "Excluded under the policy: Obesity and weight loss programs.",
      "itemized": true,
      "exclusion_matches": [
        {
          "exclusion_id": "ALL:obesity_and_weight_loss_programs",
          "label": "Obesity and weight loss programs",
          "matched_terms": [
            {
              "text": "bariatric",
              "provenance": "interpretation"
            }
          ],
          "qualifier": null,
          "policy_text_match": false,
          "source_paths": [
            "exclusions.conditions[5]"
          ]
        }
      ],
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "exclusions.conditions[5]"
    },
    {
      "kind": "line_item",
      "description": "Personalised Diet and Nutrition Program",
      "source_document": "F024",
      "amount_paise": 500000,
      "amount": 5000,
      "status": "EXCLUDED",
      "reason_code": "EXCLUDED_CONDITION",
      "reason": "Excluded with the whole claim under the claim-level exclusion: Obesity and weight loss programs.",
      "itemized": true,
      "exclusion_matches": null,
      "covered_item": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "exclusions.conditions[5]",
      "line_check": "ELIGIBLE"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 500000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -50000,
      "amount": -500,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 500000,
      "percent": 10
    },
    {
      "kind": "adjustment",
      "description": "Consultation sub-limit",
      "amount_paise": -250000,
      "amount": -2500,
      "policy_ref": "opd_categories.consultation.sub_limit",
      "rule_id": "category_sub_limit"
    }
  ]
}
```
