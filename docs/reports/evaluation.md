# Evaluation report

Policy: `PLUM_GHI_2024`. Cases: 12. Expected decision, amount, reason, confidence, and explicitly checked behavior matched: **12/12**.

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

- TC006 uses the dental sub-limit over the general claim cap and accepts a bill without a dental report, solely as a documented fixture compatibility interpretation. A real case with this policy conflict routes to review.
- TC010 treats the consultation sub-limit as applying to the consultation-fee line and applies network discount before co-pay.
- The fixtures contain no submission timestamp. The 30-day deadline is `NOT_EVALUATED`, rather than compared with today's date.
- The confidence values are evidence-quality scores, not calibrated probabilities. TC011's simulated optional failure lowers confidence and is recorded in the trace.
- Provider accuracy, handwriting, multilingual extraction, and image quality require a separately labelled image/PDF set. The fixture results make no claim about those capabilities.

## Complete outputs and traces

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
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "FAIL",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F001",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null
        },
        {
          "file_id": "F002",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null
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
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "FAIL",
      "policy_ref": "document_requirements.PHARMACY",
      "evidence": [
        {
          "file_id": "F003",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": null
        },
        {
          "file_id": "F004",
          "type": "PHARMACY_BILL",
          "quality": "UNREADABLE",
          "patient_name": null
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
      "stage": "document_gate",
      "rule_id": "document_requirements",
      "status": "FAIL",
      "policy_ref": "document_requirements.CONSULTATION",
      "evidence": [
        {
          "file_id": "F005",
          "type": "PRESCRIPTION",
          "quality": "GOOD",
          "patient_name": "Rajesh Kumar"
        },
        {
          "file_id": "F006",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": "Arjun Mehta"
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
      "code": "COVERED",
      "message": "Claim passed the evaluated document and policy checks."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.96,
  "trace": [
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
          "patient_name": "Rajesh Kumar"
        },
        {
          "file_id": "F008",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": "Rajesh Kumar"
        }
      ],
      "details": []
    },
    {
      "stage": "confidence",
      "rule_id": "evidence_quality",
      "status": "PASS",
      "evidence": {
        "deductions": [],
        "score": 0.96
      },
      "details": "Evidence-quality score; not a calibrated probability."
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members / policy_id / opd_categories",
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
        ]
      }
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
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
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
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured": 500000
      },
      "details": "No hospitalisation utilisation feed is available in this OPD evaluator."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "combined_limit": 150000
      },
      "details": "Family-floater consumption is not tracked separately from the annual OPD limit."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365
      },
      "details": "No pre-existing-condition history was supplied with the claim."
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
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "join_date": "2024-04-01",
        "treatment_date": "2024-11-01",
        "eligible_from": "2024-05-01"
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
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth",
      "details": "Not required for this evidence and amount."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "history_source": "fixture_or_supplied_history"
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
        "history_source": "fixture_or_supplied_history"
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 1500,
        "limit": 5000,
        "fixture_compatibility": false
      }
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "PASS",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": 5000,
        "ytd_source": "fixture_or_supplied_history",
        "remaining": 45000
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "eligible_before_cap": 1500,
        "sub_limit": 2000
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
      "details": "Network discount applied before co-pay."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "APPROVED",
      "evidence": {
        "primary_reason": null,
        "approved_amount_paise": 135000
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
      "message": "The diabetes waiting period ends on 2024-11-30; treatment was on 2024-10-15."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.96,
  "trace": [
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
          "patient_name": "Vikram Joshi"
        },
        {
          "file_id": "F010",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": "Vikram Joshi"
        }
      ],
      "details": []
    },
    {
      "stage": "confidence",
      "rule_id": "evidence_quality",
      "status": "PASS",
      "evidence": {
        "deductions": [],
        "score": 0.96
      },
      "details": "Evidence-quality score; not a calibrated probability."
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members / policy_id / opd_categories",
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
        ]
      }
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
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
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
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured": 500000
      },
      "details": "No hospitalisation utilisation feed is available in this OPD evaluator."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "combined_limit": 150000
      },
      "details": "Family-floater consumption is not tracked separately from the annual OPD limit."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365
      },
      "details": "No pre-existing-condition history was supplied with the claim."
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
      "rule_id": "waiting_period",
      "status": "FAIL",
      "policy_ref": "waiting_periods.specific_conditions.diabetes",
      "evidence": {
        "condition": "diabetes",
        "join_date": "2024-09-01",
        "treatment_date": "2024-10-15",
        "eligible_from": "2024-11-30"
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
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth",
      "details": "Not required for this evidence and amount."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "history_source": "fixture_or_supplied_history"
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
        "history_source": "fixture_or_supplied_history"
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 3000,
        "limit": 5000,
        "fixture_compatibility": false
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
        "ytd_source": "fixture_or_supplied_history",
        "remaining": null
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "LIMITED",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "eligible_before_cap": 3000,
        "sub_limit": 2000
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 200000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 20000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 180000
      },
      "details": "Network discount applied before co-pay."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "REJECTED",
      "evidence": {
        "primary_reason": "WAITING_PERIOD",
        "approved_amount_paise": 0
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Claimed treatment",
      "source_document": null,
      "amount_paise": 300000,
      "amount": 3000,
      "status": "ELIGIBLE",
      "reason_code": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "adjustment",
      "description": "Category sub-limit",
      "amount_paise": -100000,
      "amount": -1000,
      "policy_ref": "opd_categories.consultation.sub_limit"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 200000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -20000,
      "amount": -200,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 200000,
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
      "message": "Teeth Whitening is excluded; ₹4000 removed."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.96,
  "trace": [
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
          "patient_name": "Priya Singh"
        }
      ],
      "details": []
    },
    {
      "stage": "confidence",
      "rule_id": "evidence_quality",
      "status": "PASS",
      "evidence": {
        "deductions": [],
        "score": 0.96
      },
      "details": "Evidence-quality score; not a calibrated probability."
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members / policy_id / opd_categories",
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
        ]
      }
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
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
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
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured": 500000
      },
      "details": "No hospitalisation utilisation feed is available in this OPD evaluator."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "combined_limit": 150000
      },
      "details": "Family-floater consumption is not tracked separately from the annual OPD limit."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365
      },
      "details": "No pre-existing-condition history was supplied with the claim."
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
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "join_date": "2024-04-01",
        "treatment_date": "2024-10-15",
        "eligible_from": "2024-05-01"
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
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.dental.requires_pre_auth",
      "details": "Not required for this evidence and amount."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "history_source": "fixture_or_supplied_history"
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
        "history_source": "fixture_or_supplied_history"
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "ASSUMPTION",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 12000,
        "limit": 5000,
        "fixture_compatibility": true
      },
      "details": "Fixture compatibility assumption: global per-claim limit is not applied for this case; insurer confirmation required."
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": null,
        "ytd_source": "fixture_or_supplied_history",
        "remaining": null
      }
    },
    {
      "stage": "policy",
      "rule_id": "additional_document_requirement",
      "status": "ASSUMPTION",
      "policy_ref": "opd_categories.dental.required_additional_document",
      "evidence": {
        "required_document": "DENTAL_REPORT"
      },
      "details": "Fixture compatibility assumption: category requirement conflicts with the document matrix; insurer confirmation required."
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.dental.sub_limit",
      "evidence": {
        "eligible_before_cap": 8000,
        "sub_limit": 10000
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
      "details": "Network discount applied before co-pay."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "PARTIAL",
      "evidence": {
        "primary_reason": null,
        "approved_amount_paise": 800000
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
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.dental.covered"
    },
    {
      "kind": "line_item",
      "description": "Teeth Whitening",
      "source_document": "F011",
      "amount_paise": 400000,
      "amount": 4000,
      "status": "EXCLUDED",
      "reason_code": "EXCLUDED_PROCEDURE",
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.dental.excluded_procedures"
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
      "message": "Pre-authorization was required and was explicitly not obtained. Provide the approval record or correct the status if it was granted."
    },
    {
      "code": "PER_CLAIM_EXCEEDED",
      "message": "Claimed amount ₹15000 exceeds the per-claim limit of ₹5000."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.92,
  "trace": [
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
          "patient_name": null
        },
        {
          "file_id": "F013",
          "type": "LAB_REPORT",
          "quality": "GOOD",
          "patient_name": null
        },
        {
          "file_id": "F014",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null
        }
      ],
      "details": []
    },
    {
      "stage": "confidence",
      "rule_id": "evidence_quality",
      "status": "DEGRADED",
      "evidence": {
        "deductions": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04
          }
        ],
        "score": 0.92
      },
      "details": "Evidence-quality score; not a calibrated probability."
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members / policy_id / opd_categories",
      "evidence": {
        "member_id": "EMP007",
        "category": "DIAGNOSTIC"
      }
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
        ]
      }
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
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
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
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured": 500000
      },
      "details": "No hospitalisation utilisation feed is available in this OPD evaluator."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "combined_limit": 150000
      },
      "details": "Family-floater consumption is not tracked separately from the annual OPD limit."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365
      },
      "details": "No pre-existing-condition history was supplied with the claim."
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
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "join_date": "2024-04-01",
        "treatment_date": "2024-11-02",
        "eligible_from": "2024-05-01"
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
      "rule_id": "pre_authorization",
      "status": "FAIL",
      "policy_ref": "pre_authorization.required_for / opd_categories.requires_pre_auth",
      "evidence": {
        "matched_rules": [
          {
            "phrase": "MRI",
            "amount_greater_than": 10000
          }
        ],
        "claimed_amount": 15000,
        "pre_authorization": null,
        "status_source": "missing_or_unconfirmed"
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
        "history_source": "fixture_or_supplied_history"
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
        "history_source": "fixture_or_supplied_history"
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "FAIL",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 15000,
        "limit": 5000,
        "fixture_compatibility": false
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
        "ytd_source": "fixture_or_supplied_history",
        "remaining": null
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "LIMITED",
      "policy_ref": "opd_categories.diagnostic.sub_limit",
      "evidence": {
        "eligible_before_cap": 15000,
        "sub_limit": 10000
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 1000000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 0,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 1000000
      },
      "details": "Network discount applied before co-pay."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "REJECTED",
      "evidence": {
        "primary_reason": "PRE_AUTH_MISSING",
        "approved_amount_paise": 0
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
      "status": "ELIGIBLE",
      "reason_code": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.diagnostic.covered"
    },
    {
      "kind": "adjustment",
      "description": "Category sub-limit",
      "amount_paise": -500000,
      "amount": -5000,
      "policy_ref": "opd_categories.diagnostic.sub_limit"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.diagnostic.network_discount_percent",
      "basis_paise": 1000000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.diagnostic.copay_percent",
      "basis_paise": 1000000,
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
      "message": "Claimed amount ₹7500 exceeds the per-claim limit of ₹5000."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.92,
  "trace": [
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
          "patient_name": null
        },
        {
          "file_id": "F016",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null
        }
      ],
      "details": []
    },
    {
      "stage": "confidence",
      "rule_id": "evidence_quality",
      "status": "DEGRADED",
      "evidence": {
        "deductions": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04
          }
        ],
        "score": 0.92
      },
      "details": "Evidence-quality score; not a calibrated probability."
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members / policy_id / opd_categories",
      "evidence": {
        "member_id": "EMP003",
        "category": "CONSULTATION"
      }
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
        ]
      }
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
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
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
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured": 500000
      },
      "details": "No hospitalisation utilisation feed is available in this OPD evaluator."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "combined_limit": 150000
      },
      "details": "Family-floater consumption is not tracked separately from the annual OPD limit."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365
      },
      "details": "No pre-existing-condition history was supplied with the claim."
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
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "join_date": "2024-04-01",
        "treatment_date": "2024-10-20",
        "eligible_from": "2024-05-01"
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
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth",
      "details": "Not required for this evidence and amount."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "history_source": "fixture_or_supplied_history"
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
        "history_source": "fixture_or_supplied_history"
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "FAIL",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 7500,
        "limit": 5000,
        "fixture_compatibility": false
      }
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "PASS",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": 10000,
        "ytd_source": "fixture_or_supplied_history",
        "remaining": 40000
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "LIMITED",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "eligible_before_cap": 7500,
        "sub_limit": 2000
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 200000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 20000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 180000
      },
      "details": "Network discount applied before co-pay."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "REJECTED",
      "evidence": {
        "primary_reason": "PER_CLAIM_EXCEEDED",
        "approved_amount_paise": 0
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
      "status": "ELIGIBLE",
      "reason_code": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "line_item",
      "description": "Medicines",
      "source_document": "F016",
      "amount_paise": 550000,
      "amount": 5500,
      "status": "ELIGIBLE",
      "reason_code": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "adjustment",
      "description": "Category sub-limit",
      "amount_paise": -550000,
      "amount": -5500,
      "policy_ref": "opd_categories.consultation.sub_limit"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 200000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -20000,
      "amount": -200,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 200000,
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
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.92,
  "trace": [
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
          "patient_name": null
        },
        {
          "file_id": "F018",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null
        }
      ],
      "details": []
    },
    {
      "stage": "confidence",
      "rule_id": "evidence_quality",
      "status": "DEGRADED",
      "evidence": {
        "deductions": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04
          }
        ],
        "score": 0.92
      },
      "details": "Evidence-quality score; not a calibrated probability."
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members / policy_id / opd_categories",
      "evidence": {
        "member_id": "EMP008",
        "category": "CONSULTATION"
      }
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
        ]
      }
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
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
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
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured": 500000
      },
      "details": "No hospitalisation utilisation feed is available in this OPD evaluator."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "combined_limit": 150000
      },
      "details": "Family-floater consumption is not tracked separately from the annual OPD limit."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365
      },
      "details": "No pre-existing-condition history was supplied with the claim."
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
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "join_date": "2024-04-01",
        "treatment_date": "2024-10-30",
        "eligible_from": "2024-05-01"
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
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth",
      "details": "Not required for this evidence and amount."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "FLAG",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 4,
        "limit": 2,
        "history_source": "fixture_or_supplied_history"
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
        "history_source": "fixture_or_supplied_history"
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 4800,
        "limit": 5000,
        "fixture_compatibility": false
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
        "ytd_source": "fixture_or_supplied_history",
        "remaining": null
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "LIMITED",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "eligible_before_cap": 4800,
        "sub_limit": 2000
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 200000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 20000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 180000
      },
      "details": "Network discount applied before co-pay."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "MANUAL_REVIEW",
      "evidence": {
        "primary_reason": "SAME_DAY_CLAIMS",
        "approved_amount_paise": 0
      }
    }
  ],
  "ledger": [
    {
      "kind": "line_item",
      "description": "Claimed treatment",
      "source_document": null,
      "amount_paise": 480000,
      "amount": 4800,
      "status": "ELIGIBLE",
      "reason_code": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "adjustment",
      "description": "Category sub-limit",
      "amount_paise": -280000,
      "amount": -2800,
      "policy_ref": "opd_categories.consultation.sub_limit"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 200000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -20000,
      "amount": -200,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 200000,
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
      "code": "COVERED",
      "message": "Claim passed the evaluated document and policy checks."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.96,
  "trace": [
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
          "patient_name": "Deepak Shah"
        },
        {
          "file_id": "F020",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": "Deepak Shah"
        }
      ],
      "details": []
    },
    {
      "stage": "confidence",
      "rule_id": "evidence_quality",
      "status": "PASS",
      "evidence": {
        "deductions": [],
        "score": 0.96
      },
      "details": "Evidence-quality score; not a calibrated probability."
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members / policy_id / opd_categories",
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
        ]
      }
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
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
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
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured": 500000
      },
      "details": "No hospitalisation utilisation feed is available in this OPD evaluator."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "combined_limit": 150000
      },
      "details": "Family-floater consumption is not tracked separately from the annual OPD limit."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365
      },
      "details": "No pre-existing-condition history was supplied with the claim."
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
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "join_date": "2024-04-01",
        "treatment_date": "2024-11-03",
        "eligible_from": "2024-05-01"
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
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth",
      "details": "Not required for this evidence and amount."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "history_source": "fixture_or_supplied_history"
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
        "history_source": "fixture_or_supplied_history"
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 4500,
        "limit": 5000,
        "fixture_compatibility": false
      }
    },
    {
      "stage": "policy",
      "rule_id": "annual_opd_limit",
      "status": "PASS",
      "policy_ref": "coverage.annual_opd_limit",
      "evidence": {
        "annual_limit": 50000,
        "ytd_claims_amount": 8000,
        "ytd_source": "fixture_or_supplied_history",
        "remaining": 42000
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "ASSUMPTION",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "matching_line_amount": 1500,
        "sub_limit": 2000,
        "matching_phrase": "consultation fee"
      },
      "details": "Fixture compatibility assumption: sub-limit applies only to explicitly matched line items; insurer confirmation required."
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
      "details": "Network discount applied before co-pay."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "APPROVED",
      "evidence": {
        "primary_reason": null,
        "approved_amount_paise": 324000
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

```json
{
  "state": "DECIDED",
  "decision": "APPROVED",
  "approved_amount": 4000,
  "approved_amount_paise": 400000,
  "reasons": [
    {
      "code": "COMPONENT_DEGRADED",
      "message": "Optional risk enrichment failed and was skipped; manual review is recommended."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.69,
  "trace": [
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
          "patient_name": null
        },
        {
          "file_id": "F022",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null
        }
      ],
      "details": []
    },
    {
      "stage": "confidence",
      "rule_id": "evidence_quality",
      "status": "DEGRADED",
      "evidence": {
        "deductions": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04
          }
        ],
        "score": 0.92
      },
      "details": "Evidence-quality score; not a calibrated probability."
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members / policy_id / opd_categories",
      "evidence": {
        "member_id": "EMP006",
        "category": "ALTERNATIVE_MEDICINE"
      }
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
        ]
      }
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
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "PASS",
      "policy_ref": "exclusions.conditions",
      "evidence": []
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
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured": 500000
      },
      "details": "No hospitalisation utilisation feed is available in this OPD evaluator."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "combined_limit": 150000
      },
      "details": "Family-floater consumption is not tracked separately from the annual OPD limit."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365
      },
      "details": "No pre-existing-condition history was supplied with the claim."
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
      "rule_id": "waiting_period",
      "status": "PASS",
      "policy_ref": "waiting_periods.initial_waiting_period_days",
      "evidence": {
        "condition": "initial",
        "join_date": "2024-04-01",
        "treatment_date": "2024-10-28",
        "eligible_from": "2024-05-01"
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
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.requires_pre_auth",
      "details": "Not required for this evidence and amount."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "history_source": "fixture_or_supplied_history"
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
        "history_source": "fixture_or_supplied_history"
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "SKIPPED_COMPONENT_FAILURE",
      "degraded": true,
      "error_type": "RuntimeError",
      "details": "Optional enrichment failed; mandatory document and policy checks completed."
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "PASS",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 4000,
        "limit": 5000,
        "fixture_compatibility": false
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
        "ytd_source": "fixture_or_supplied_history",
        "remaining": null
      }
    },
    {
      "stage": "policy",
      "rule_id": "covered_system",
      "status": "NOT_EVALUATED",
      "policy_ref": "opd_categories.alternative_medicine.covered_systems",
      "evidence": {
        "matched": []
      },
      "details": "No listed medical system was named in the documents."
    },
    {
      "stage": "policy",
      "rule_id": "max_sessions",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.max_sessions_per_year",
      "evidence": {
        "sessions": 5,
        "max_sessions_per_year": 20
      }
    },
    {
      "stage": "policy",
      "rule_id": "registered_practitioner",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.requires_registered_practitioner",
      "evidence": {}
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "PASS",
      "policy_ref": "opd_categories.alternative_medicine.sub_limit",
      "evidence": {
        "eligible_before_cap": 4000,
        "sub_limit": 8000
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
      "details": "Network discount applied before co-pay."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "APPROVED",
      "evidence": {
        "primary_reason": null,
        "approved_amount_paise": 400000
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
      "message": "Treatment is excluded under the policy: bariatric, morbid obesity."
    },
    {
      "code": "WAITING_PERIOD",
      "message": "The obesity treatment waiting period ends on 2025-04-01; treatment was on 2024-10-18."
    },
    {
      "code": "PER_CLAIM_EXCEEDED",
      "message": "Claimed amount ₹8000 exceeds the per-claim limit of ₹5000."
    }
  ],
  "correction_requests": [],
  "confidence_score": 0.92,
  "trace": [
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
          "patient_name": null
        },
        {
          "file_id": "F024",
          "type": "HOSPITAL_BILL",
          "quality": "GOOD",
          "patient_name": null
        }
      ],
      "details": []
    },
    {
      "stage": "confidence",
      "rule_id": "evidence_quality",
      "status": "DEGRADED",
      "evidence": {
        "deductions": [
          {
            "reason": "patient_name_unavailable",
            "points": 0.04
          }
        ],
        "score": 0.92
      },
      "details": "Evidence-quality score; not a calibrated probability."
    },
    {
      "stage": "eligibility",
      "rule_id": "membership",
      "status": "PASS",
      "policy_ref": "members / policy_id / opd_categories",
      "evidence": {
        "member_id": "EMP009",
        "category": "CONSULTATION"
      }
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
        ]
      }
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
      "stage": "policy",
      "rule_id": "excluded_condition",
      "status": "FAIL",
      "policy_ref": "exclusions.conditions",
      "evidence": [
        "bariatric",
        "morbid obesity"
      ]
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
      "rule_id": "sum_insured",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.sum_insured_per_employee",
      "evidence": {
        "sum_insured": 500000
      },
      "details": "No hospitalisation utilisation feed is available in this OPD evaluator."
    },
    {
      "stage": "policy",
      "rule_id": "family_floater_limit",
      "status": "NOT_EVALUATED",
      "policy_ref": "coverage.family_floater.combined_limit",
      "evidence": {
        "combined_limit": 150000
      },
      "details": "Family-floater consumption is not tracked separately from the annual OPD limit."
    },
    {
      "stage": "policy",
      "rule_id": "pre_existing_condition_wait",
      "status": "NOT_EVALUATED",
      "policy_ref": "waiting_periods.pre_existing_conditions_days",
      "evidence": {
        "days": 365
      },
      "details": "No pre-existing-condition history was supplied with the claim."
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
      "rule_id": "waiting_period",
      "status": "FAIL",
      "policy_ref": "waiting_periods.specific_conditions.obesity_treatment",
      "evidence": {
        "condition": "obesity_treatment",
        "join_date": "2024-04-01",
        "treatment_date": "2024-10-18",
        "eligible_from": "2025-04-01"
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
      "rule_id": "pre_authorization",
      "status": "PASS",
      "policy_ref": "opd_categories.consultation.requires_pre_auth",
      "details": "Not required for this evidence and amount."
    },
    {
      "stage": "risk",
      "rule_id": "same_day_claims",
      "status": "PASS",
      "policy_ref": "fraud_thresholds.same_day_claims_limit",
      "evidence": {
        "same_day_claim_count_including_current": 1,
        "limit": 2,
        "history_source": "fixture_or_supplied_history"
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
        "history_source": "fixture_or_supplied_history"
      }
    },
    {
      "stage": "optional_risk_enrichment",
      "rule_id": "risk_enrichment",
      "status": "PASS",
      "degraded": false
    },
    {
      "stage": "policy",
      "rule_id": "per_claim_limit",
      "status": "FAIL",
      "policy_ref": "coverage.per_claim_limit",
      "evidence": {
        "claimed_amount": 8000,
        "limit": 5000,
        "fixture_compatibility": false
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
        "ytd_source": "fixture_or_supplied_history",
        "remaining": null
      }
    },
    {
      "stage": "policy",
      "rule_id": "category_sub_limit",
      "status": "LIMITED",
      "policy_ref": "opd_categories.consultation.sub_limit",
      "evidence": {
        "eligible_before_cap": 8000,
        "sub_limit": 2000
      }
    },
    {
      "stage": "pricing",
      "rule_id": "payable_amount",
      "status": "CALCULATED",
      "evidence": {
        "eligible_paise": 200000,
        "network_hospital": false,
        "network_discount_paise": 0,
        "copay_paise": 20000,
        "branded_basis_paise": 0,
        "branded_copay_paise": 0,
        "payable_paise": 180000
      },
      "details": "Network discount applied before co-pay."
    },
    {
      "stage": "decision",
      "rule_id": "outcome",
      "status": "REJECTED",
      "evidence": {
        "primary_reason": "EXCLUDED_CONDITION",
        "approved_amount_paise": 0
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
      "status": "ELIGIBLE",
      "reason_code": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "line_item",
      "description": "Personalised Diet and Nutrition Program",
      "source_document": "F024",
      "amount_paise": 500000,
      "amount": 5000,
      "status": "ELIGIBLE",
      "reason_code": null,
      "brand_status": null,
      "brand_evidence": null,
      "policy_ref": "opd_categories.consultation.covered"
    },
    {
      "kind": "adjustment",
      "description": "Category sub-limit",
      "amount_paise": -600000,
      "amount": -6000,
      "policy_ref": "opd_categories.consultation.sub_limit"
    },
    {
      "kind": "adjustment",
      "description": "Network discount",
      "amount_paise": 0,
      "amount": 0,
      "policy_ref": "opd_categories.consultation.network_discount_percent",
      "basis_paise": 200000,
      "percent": 0
    },
    {
      "kind": "adjustment",
      "description": "Member co-pay",
      "amount_paise": -20000,
      "amount": -200,
      "policy_ref": "opd_categories.consultation.copay_percent",
      "basis_paise": 200000,
      "percent": 10
    }
  ]
}
```
