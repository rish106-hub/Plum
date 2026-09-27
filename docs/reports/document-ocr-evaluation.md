# Provider-backed OCR evaluation (dirty synthetic corpus)

Provider: `sarvam_document_ai`; Gemini evidence review: disabled.

| Metric | Value |
| --- | --- |
| documents | 14 |
| unsafe_confident_errors | 0 |
| classification_accuracy | 0.9231 |
| field_accuracy | 0.9091 |
| abstention_accuracy | 0.9286 |
| behavior_acceptable_rate | 0.9286 |
| false_hold_rate | 0.1111 |
| missed_hold_count | 0 |
| wrong_total_rate | 0.0714 |
| line_item_reconciliation_rate | 0.8333 |
| line_item_reconciliation_unavailable_count | 8 |
| missed_alteration_count | 0 |
| alteration_false_hold_count | 0 |
| provider_calls | 22 |
| provider_failures | 0 |
| sarvam_digitise_calls | 13 |
| sarvam_digitise_pages | 14 |
| sarvam_extract_calls | 9 |
| sarvam_extract_pages | 9 |
| gemini_calls | 0 |
| latency_seconds_p50 | 10.513 |
| latency_seconds_max | 19.685 |
| configured_cost_inr | None |
| cost_rate_source | NOT_CONFIGURED |

| Scenario | Type (exp / got) | Behaviour (exp / got) | Fields correct | Unsafe | Latency s |
| --- | --- | --- | --- | --- | --- |
| rx_clean_scan | PRESCRIPTION / PRESCRIPTION | extract / extract | 4/5 | no | 4.253 |
| rx_handwritten | PRESCRIPTION / PRESCRIPTION | extract / extract | 5/5 | no | 10.438 |
| rx_stamp_over_registration | PRESCRIPTION / PRESCRIPTION | extract / extract | 5/5 | no | 10.588 |
| rx_hindi_english | PRESCRIPTION / PRESCRIPTION | extract / extract | 4/5 | no | 10.609 |
| rx_scanned_pdf | PRESCRIPTION / PRESCRIPTION | extract / extract | 4/5 | no | 3.964 |
| bill_phone_photo_skewed | HOSPITAL_BILL / HOSPITAL_BILL | extract / extract | 5/5 | no | 3.96 |
| bill_multipage_scan | HOSPITAL_BILL / HOSPITAL_BILL | extract / extract | 5/5 | no | 3.929 |
| bill_cropped_partial | HOSPITAL_BILL / HOSPITAL_BILL | request_reupload / request_reupload | 3/3 | no | 10.437 |
| bill_struck_correction | HOSPITAL_BILL / HOSPITAL_BILL | abstain / request_reupload | 2/3 | no | 13.703 |
| bill_low_contrast | HOSPITAL_BILL / HOSPITAL_BILL | request_reupload / request_reupload | 3/3 | no | 16.593 |
| blank_page | None / None | request_reupload / request_reupload | 2/2 | no | 0.002 |
| lab_report_scan | LAB_REPORT / LAB_REPORT | extract / extract | 3/3 | no | 19.685 |
| pharmacy_bill_scan | PHARMACY_BILL / PHARMACY_BILL | extract / request_reupload | 4/5 | no | 10.715 |
| non_medical_receipt | UNKNOWN / HOSPITAL_BILL | request_reupload / request_reupload | 1/1 | no | 13.611 |

## Observed discrepancies

- **rx_clean_scan**: `doctor_name` missing (expected 'Dr. Arun Sharma'; observed None). Safety result: proceeded, but the missed field is non-critical under this benchmark; not an unsafe confident error.
- **rx_hindi_english**: `doctor_name` wrong (expected 'Dr. R. Gupta'; observed 'Dr. R. Gupta, MBBS, MD (Medicine)'). Safety result: proceeded, but the missed field is non-critical under this benchmark; not an unsafe confident error.
- **rx_scanned_pdf**: `doctor_name` missing (expected 'Dr. Arun Sharma'; observed None). Safety result: proceeded, but the missed field is non-critical under this benchmark; not an unsafe confident error.
- **bill_struck_correction**: `total` wrong (expected 1300.0; observed 1500.0). Safety result: held/routed as `DOCUMENT_CORRECTION_REQUIRED`; not an unsafe confident error.
- **pharmacy_bill_scan**: behaviour expected `extract`, observed `request_reupload`; `line_item_amounts` wrong (expected [37.5, 40.0]; observed [77.5]). Safety result: held/routed as `DOCUMENT_CORRECTION_REQUIRED`; not an unsafe confident error.
- **non_medical_receipt**: type expected `UNKNOWN`, observed `HOSPITAL_BILL`. Safety result: held/routed as `DOCUMENT_CORRECTION_REQUIRED`; not an unsafe confident error.

These results describe this fixed, generated 14-document corpus only. They do not estimate performance on real claims, demographic groups, unseen providers, or adversarial documents.

An unsafe confident error is a document that would proceed to adjudication with a wrong or fabricated critical field. The target is zero.
Configured cost is an estimate only, using the per-page rates passed to this run; it is omitted until rates are supplied.
