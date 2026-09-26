"""Behavioral tests for the deterministic policy reducer and fixture contract."""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from claims.core import evaluate_claim
from claims.fixtures import load_cases, load_policy, normalize_fixture
from claims.policy import normalize_policy

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "policy_terms.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "test_cases.json"


def _raise_optional_enrichment_failure(_: dict) -> None:
    raise RuntimeError("synthetic optional component failure")


class ClaimCoreTests(unittest.TestCase):
    policy: dict
    cases: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_policy(POLICY_PATH)
        cls.cases = {case["case_id"]: case for case in load_cases(FIXTURE_PATH)}

    def evaluate(self, case_id: str) -> dict:
        case = self.cases[case_id]
        options = {}
        if case.get("input", {}).get("simulate_component_failure"):
            options["optional_risk_enricher"] = _raise_optional_enrichment_failure
        return evaluate_claim(normalize_fixture(case), self.policy, **options)

    def test_all_fixture_decisions_and_amounts(self) -> None:
        for case_id, case in self.cases.items():
            with self.subTest(case_id=case_id):
                result = self.evaluate(case_id)
                self.assertEqual(result["decision"], case["expected"]["decision"])
                if "approved_amount" in case["expected"]:
                    self.assertEqual(result["approved_amount"], case["expected"]["approved_amount"])
                if "rejection_reasons" in case["expected"]:
                    self.assertTrue(set(case["expected"]["rejection_reasons"]) <= {reason["code"] for reason in result["reasons"]})
                confidence_rule = case["expected"].get("confidence_score")
                if isinstance(confidence_rule, str) and confidence_rule.startswith("above "):
                    self.assertGreater(result["confidence_score"], float(confidence_rule.removeprefix("above ")))
                self.assertTrue(result["trace"])

    def test_wrong_document_names_uploaded_and_missing_types(self) -> None:
        result = self.evaluate("TC001")
        self.assertEqual(result["state"], "NEEDS_CORRECTION")
        self.assertIsNone(result["approved_amount"])
        message = result["correction_requests"][0]["message"]
        self.assertIn("PRESCRIPTION", message)
        self.assertIn("HOSPITAL_BILL", message)

    def test_unreadable_bill_and_patient_mismatch_stop_before_policy(self) -> None:
        for case_id, code in (("TC002", "DOCUMENT_UNREADABLE"), ("TC003", "PATIENT_MISMATCH")):
            with self.subTest(case_id=case_id):
                result = self.evaluate(case_id)
                self.assertIn(code, {entry["code"] for entry in result["correction_requests"]})
                self.assertEqual([step["stage"] for step in result["trace"]], ["configuration", "document_gate"])
        self.assertIn("Rajesh Kumar", self.evaluate("TC003")["correction_requests"][0]["message"])
        self.assertIn("Arjun Mehta", self.evaluate("TC003")["correction_requests"][0]["message"])

    def test_waiting_period_uses_specific_condition_and_real_calendar(self) -> None:
        result = self.evaluate("TC005")
        waiting = next(step for step in result["trace"] if step["rule_id"] == "waiting_period")
        self.assertEqual(waiting["evidence"]["eligible_from"], "2024-11-30")
        self.assertEqual(waiting["policy_ref"], "waiting_periods.specific_conditions.diabetes")
        mri = self.evaluate("TC007")
        self.assertEqual(next(step for step in mri["trace"] if step["rule_id"] == "waiting_period")["status"], "PASS")
        self.assertEqual(mri["trace"][-1]["evidence"]["primary_reason"], "PRE_AUTH_MISSING")

    def test_dental_item_exclusion_and_category_ceiling_are_visible(self) -> None:
        result = self.evaluate("TC006")
        self.assertEqual(result["approved_amount_paise"], 800000)
        lines = [entry for entry in result["ledger"] if entry["kind"] == "line_item"]
        self.assertEqual([entry["status"] for entry in lines], ["ELIGIBLE", "EXCLUDED"])
        self.assertEqual(lines[1]["policy_ref"], "exclusions.dental_exclusions[0]")
        self.assertTrue(lines[1]["reason"])
        ceiling = next(step for step in result["trace"] if step["rule_id"] == "per_claim_limit")
        self.assertEqual(ceiling["status"], "PASS")
        self.assertEqual(ceiling["policy_ref"], "opd_categories.dental.sub_limit")
        self.assertEqual((ceiling["evidence"]["eligible_amount"], ceiling["evidence"]["limit"]), (8000, 10000))

    def test_discount_precedes_copay_and_uses_integer_paise(self) -> None:
        result = self.evaluate("TC010")
        pricing = next(step for step in result["trace"] if step["rule_id"] == "payable_amount")["evidence"]
        self.assertEqual(pricing["eligible_paise"], 450000)
        self.assertEqual(pricing["network_discount_paise"], 90000)
        self.assertEqual(pricing["copay_paise"], 36000)
        self.assertEqual(pricing["payable_paise"], 324000)

    def test_all_matching_bill_lines_are_used_for_pricing(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        first_bill = claim["documents"][1]
        first_bill["fields"]["total"] = 1000
        first_bill["fields"]["line_items"] = [{"description": "Consultation Fee", "amount": 1000}]
        second_bill = deepcopy(first_bill)
        second_bill["file_id"] = "SECOND-BILL"
        second_bill["fields"]["total"] = 500
        second_bill["fields"]["line_items"] = [{"description": "CBC Test", "amount": 500}]
        claim["documents"].append(second_bill)

        result = evaluate_claim(claim, self.policy)

        self.assertEqual(result["decision"], "APPROVED")
        self.assertEqual(result["approved_amount_paise"], 135000)
        bill_lines = [entry for entry in result["ledger"] if entry["kind"] == "line_item"]
        self.assertEqual([entry["source_document"] for entry in bill_lines], [first_bill["file_id"], "SECOND-BILL"])

    def test_optional_failure_is_visible_without_changing_supported_decision(self) -> None:
        result = self.evaluate("TC011")
        self.assertEqual(result["decision"], "APPROVED")
        self.assertLess(result["confidence_score"], self.evaluate("TC004")["confidence_score"])
        failure_step = next(step for step in result["trace"] if step["rule_id"] == "risk_enrichment")
        self.assertEqual(failure_step["status"], "SKIPPED_COMPONENT_FAILURE")
        self.assertEqual(failure_step["error_type"], "RuntimeError")
        self.assertIn("manual review", " ".join(reason["message"] for reason in result["reasons"]).lower())

    def test_public_claim_flag_cannot_request_fault_injection(self) -> None:
        claim = normalize_fixture(self.cases["TC011"])
        claim["simulate_component_failure"] = True
        result = evaluate_claim(claim, self.policy)
        enrichment = next(step for step in result["trace"] if step["rule_id"] == "risk_enrichment")
        self.assertEqual(enrichment["status"], "PASS")
        self.assertNotIn("COMPONENT_DEGRADED", {reason["code"] for reason in result["reasons"]})

    def test_same_day_and_monthly_history_route_to_review(self) -> None:
        base = normalize_fixture(self.cases["TC004"])
        base["claims_history"] = [{"date": "2024-11-01"}, {"date": "2024-11-01"}]
        same_day = evaluate_claim(base, self.policy)
        self.assertEqual(same_day["decision"], "MANUAL_REVIEW")
        self.assertIn("SAME_DAY_CLAIMS", {reason["code"] for reason in same_day["reasons"]})

        base["claims_history"] = [{"date": f"2024-11-{day:02d}"} for day in range(2, 8)]
        monthly = evaluate_claim(base, self.policy)
        self.assertEqual(monthly["decision"], "MANUAL_REVIEW")
        self.assertIn("MONTHLY_CLAIMS", {reason["code"] for reason in monthly["reasons"]})

    def test_missing_patient_name_is_disclosed_and_lowers_payable_confidence(self) -> None:
        # Formerly: an upload with no readable patient name went to review while
        # a fixture did not. The rule is now provenance-independent: the claim is
        # attributed to the submitting member, the identity step is NOT_EVALUATED,
        # and a payable outcome carries an advisory and a confidence deduction.
        for source in ("fixture_metadata", "uploaded_file"):
            with self.subTest(source=source):
                claim = normalize_fixture(self.cases["TC004"])
                for document in claim["documents"]:
                    document["source"] = source
                    document["patient_name"] = None
                    document["fields"].pop("patient_name", None)
                result = evaluate_claim(claim, self.policy)
                self.assertEqual(result["decision"], "APPROVED")
                self.assertIn("PATIENT_IDENTITY_NOT_VERIFIED", {reason["code"] for reason in result["reasons"]})
                identity = next(step for step in result["trace"] if step["rule_id"] == "patient_identity")
                self.assertEqual(identity["status"], "NOT_EVALUATED")
                self.assertLess(result["confidence_score"], self.evaluate("TC004")["confidence_score"])

    def test_policy_values_are_read_from_input(self) -> None:
        raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        raw["opd_categories"]["consultation"]["copay_percent"] = 0
        result = evaluate_claim(normalize_fixture(self.cases["TC004"]), normalize_policy(raw))
        self.assertEqual(result["approved_amount"], 1500)
        self.assertEqual(evaluate_claim(normalize_fixture(self.cases["TC004"]), raw)["approved_amount"], 1500)

    def test_patient_name_is_checked_against_member_roster(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        for document in claim["documents"]:
            document["patient_name"] = "Someone Else"
            document["fields"]["patient_name"] = "Someone Else"
        result = evaluate_claim(claim, self.policy)
        self.assertIsNone(result["decision"])
        self.assertEqual(result["correction_requests"][0]["code"], "PATIENT_MEMBER_MISMATCH")

    def test_document_patient_must_be_the_benefit_account_member(self) -> None:
        claim = self._consultation_claim()
        for document in claim["documents"]:
            document["patient_name"] = "Sunita Kumar"
            document["fields"]["patient_name"] = "Sunita Kumar"

        result = evaluate_claim(claim, self.policy)

        self.assertIsNone(result["decision"])
        self.assertEqual(result["correction_requests"][0]["code"], "PATIENT_MEMBER_MISMATCH")
        identity = next(step for step in result["trace"] if step["rule_id"] == "roster_patient_match")
        self.assertEqual(identity["evidence"]["benefit_member_id"], "EMP001")

    def test_clause_aware_negation_does_not_cross_punctuation_or_negate_modifiers(self) -> None:
        claim = self._consultation_claim()
        self._set_claim_text(claim, "No complications; multivitamin tonic")
        result = evaluate_claim(claim, self.policy)
        self.assertIn("EXCLUDED_CONDITION", {reason["code"] for reason in result["reasons"]})

        claim = self._consultation_claim()
        self._set_claim_text(claim, "Not well controlled diabetes")
        result = evaluate_claim(claim, self.policy)
        waiting = next(step for step in result["trace"] if step["rule_id"] == "waiting_period")
        self.assertEqual(waiting["policy_ref"], "waiting_periods.specific_conditions.diabetes")

    def test_ambiguous_clinical_alias_routes_to_manual_review(self) -> None:
        claim = self._consultation_claim()
        self._set_claim_text(claim, "ECG shows ST depression")
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("AMBIGUOUS_CLINICAL_TERM", {reason["code"] for reason in result["reasons"]})

    def test_honorific_is_ignored_for_covered_patient_matching(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        for document in claim["documents"]:
            document["patient_name"] = "Mr. Rajesh Kumar"
            document["fields"]["patient_name"] = "Mr. Rajesh Kumar"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "APPROVED")

    def test_negated_condition_and_excluded_line_produce_a_partial_result(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        prescription = claim["documents"][0]["fields"]
        prescription["diagnosis"] = "Viral fever with no history of substance abuse"
        bill = claim["documents"][1]["fields"]
        bill["line_items"] = [
            {"description": "Consultation fee", "amount": 1300},
            {"description": "Multivitamin supplement", "amount": 200},
        ]
        bill["total"] = 1500
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "PARTIAL")
        self.assertNotIn("EXCLUDED_CONDITION", {reason["code"] for reason in result["reasons"]})
        self.assertIn("EXCLUDED_PROCEDURE", {reason["code"] for reason in result["reasons"]})

    def test_claim_bill_and_line_items_must_reconcile(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        claim["claimed_amount"] = 1600
        result = evaluate_claim(claim, self.policy)
        self.assertIsNone(result["decision"])
        self.assertEqual(result["correction_requests"][0]["code"], "AMOUNT_MISMATCH")
        self.assertEqual(next(step for step in result["trace"] if step["rule_id"] == "bill_amount")["status"], "FAIL")

    def test_unknown_annual_usage_is_not_evaluated_disclosed_and_provenance_independent(self) -> None:
        # Formerly: uploads without YTD usage went to review. Aggregate limits now
        # apply only when utilisation is supplied (the web intake always supplies
        # it); otherwise the rule is NOT_EVALUATED, disclosed, and lowers confidence.
        outcomes = []
        for source in ("fixture_metadata", "uploaded_file"):
            claim = normalize_fixture(self.cases["TC004"])
            claim.pop("ytd_claims_amount")
            claim["documents"] = [{**document, "source": source} for document in claim["documents"]]
            result = evaluate_claim(claim, self.policy)
            annual = next(step for step in result["trace"] if step["rule_id"] == "annual_opd_limit")
            self.assertEqual(annual["status"], "NOT_EVALUATED")
            self.assertIn("ANNUAL_LIMIT_NOT_EVALUATED", {reason["code"] for reason in result["reasons"]})
            outcomes.append((result["decision"], result["approved_amount"], result["confidence_score"]))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(outcomes[0][:2], ("APPROVED", 1350))
        self.assertLess(outcomes[0][2], self.evaluate("TC004")["confidence_score"])

    def test_supplied_annual_usage_limits_payment(self) -> None:
        # Formerly the remaining ₹1,000 capped the gross amount before co-pay
        # (paying 900). Benefit limits now cap the net payable after discount
        # and co-pay: min(1350, 1000) = 1000.
        claim = normalize_fixture(self.cases["TC004"])
        claim["ytd_claims_amount"] = 49000
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "PARTIAL")
        self.assertEqual(result["approved_amount"], 1000)
        self.assertIn("ANNUAL_LIMIT_LIMITED", {reason["code"] for reason in result["reasons"]})

    def test_malformed_line_amount_produces_review_trace(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        claim["documents"][1]["fields"]["line_items"][0]["amount"] = "not a number"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertEqual(result["reasons"][0]["code"], "MALFORMED_EVIDENCE")
        self.assertEqual(result["trace"][0]["status"], "FAIL")

    def _consultation_claim(self) -> dict:
        return normalize_fixture(self.cases["TC004"])

    def _set_claim_text(self, claim: dict, text: str) -> None:
        prescription = next(doc for doc in claim["documents"] if doc["doc_type"] == "PRESCRIPTION")
        prescription["fields"].update(diagnosis=text, treatment=text)

    def test_exclusions_use_phrase_boundaries_and_do_not_reject_covered_phrases(self) -> None:
        for phrase in ("Cataract Surgery", "Health checkup", "Mental health counselling"):
            with self.subTest(phrase=phrase):
                claim = self._consultation_claim()
                self._set_claim_text(claim, phrase)
                result = evaluate_claim(claim, self.policy)
                self.assertNotIn("EXCLUDED_CONDITION", {reason["code"] for reason in result["reasons"]})
                exclusion = next(step for step in result["trace"] if step["rule_id"] == "excluded_condition")
                self.assertEqual(exclusion["evidence"], [])
        claim = self._consultation_claim()
        self._set_claim_text(claim, "Multivitamin tonic")
        result = evaluate_claim(claim, self.policy)
        self.assertIn("EXCLUDED_CONDITION", {reason["code"] for reason in result["reasons"]})

    def test_condition_aliases_apply_waiting_period(self) -> None:
        # The aliases now live in the normalizer's interpretation table, not the policy file.
        for alias in ("T2DM", "HTN", "Hypothyroidism"):
            with self.subTest(alias=alias):
                claim = normalize_fixture(self.cases["TC005"])
                self._set_claim_text(claim, alias)
                result = evaluate_claim(claim, self.policy)
                self.assertEqual(result["decision"], "REJECTED")
                self.assertIn("WAITING_PERIOD", {reason["code"] for reason in result["reasons"]})
                waiting = next(step for step in result["trace"] if step["rule_id"] == "waiting_period")
                self.assertEqual([term["provenance"] for term in waiting["evidence"]["matched_terms"]], ["interpretation"])

    def test_dependent_inherits_primary_member_waiting_period_start(self) -> None:
        claim = self._consultation_claim()
        claim["member_id"] = "DEP001"
        claim["treatment_date"] = "2024-04-15"
        prescription = next(doc for doc in claim["documents"] if doc["doc_type"] == "PRESCRIPTION")
        prescription["patient_name"] = "Sunita Kumar"
        prescription["fields"]["patient_name"] = "Sunita Kumar"
        bill = next(doc for doc in claim["documents"] if doc["doc_type"] == "HOSPITAL_BILL")
        bill["patient_name"] = "Sunita Kumar"
        bill["fields"]["patient_name"] = "Sunita Kumar"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("WAITING_PERIOD", {reason["code"] for reason in result["reasons"]})

    def test_child_relationship_matches_plural_policy_term(self) -> None:
        claim = self._consultation_claim()
        claim["member_id"] = "DEP002"
        for document in claim["documents"]:
            document["patient_name"] = "Arjun Kumar"
            document["fields"]["patient_name"] = "Arjun Kumar"

        result = evaluate_claim(claim, self.policy)

        self.assertEqual(result["decision"], "APPROVED")
        self.assertNotIn("RELATIONSHIP_NOT_COVERED", {reason["code"] for reason in result["reasons"]})
        relationship = next(step for step in result["trace"] if step["rule_id"] == "covered_relationship")
        self.assertEqual(relationship["status"], "PASS")
        self.assertEqual(relationship["evidence"]["covered_relationship"], "CHILDREN")

    def test_policy_period_and_minimum_claim_amount_are_enforced(self) -> None:
        claim = self._consultation_claim()
        claim["treatment_date"] = "2025-04-01"
        result = evaluate_claim(claim, self.policy)
        self.assertIn("OUTSIDE_POLICY_PERIOD", {reason["code"] for reason in result["reasons"]})
        claim["treatment_date"] = "2024-03-31"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["trace"][-1]["evidence"]["primary_reason"], "OUTSIDE_POLICY_PERIOD")

        claim = self._consultation_claim()
        claim["claimed_amount"] = 500
        bill = next(doc for doc in claim["documents"] if doc["doc_type"] == "HOSPITAL_BILL")
        bill["fields"]["total"] = 500
        bill["fields"]["line_items"] = [{"description": "Consultation fee", "amount": 500}]
        result = evaluate_claim(claim, self.policy)
        self.assertNotIn("MINIMUM_CLAIM_AMOUNT", {reason["code"] for reason in result["reasons"]})

        claim["claimed_amount"] = 100
        bill["fields"]["total"] = 100
        bill["fields"]["line_items"] = [{"description": "Consultation fee", "amount": 100}]
        result = evaluate_claim(claim, self.policy)
        self.assertIn("MINIMUM_CLAIM_AMOUNT", {reason["code"] for reason in result["reasons"]})

    def test_pet_scan_always_requires_pre_authorization(self) -> None:
        claim = normalize_fixture(self.cases["TC007"])
        claim["claimed_amount"] = 9000
        for document in claim["documents"]:
            fields = document["fields"]
            if "tests_ordered" in fields:
                fields["tests_ordered"] = ["PET Scan"]
            if "test_name" in fields:
                fields["test_name"] = "PET Scan"
            if fields.get("line_items"):
                fields["line_items"] = [{"description": "PET Scan", "amount": 9000}]
                fields["total"] = 9000
        result = evaluate_claim(claim, self.policy)
        self.assertIn("PRE_AUTH_MISSING", {reason["code"] for reason in result["reasons"]})

    def test_required_pre_auth_without_any_approval_record_is_missing_for_every_source(self) -> None:
        # Formerly: an upload with no pre-auth status went to review while the
        # edited fixture carried an explicit false. With no approval record,
        # reference, or claimed approval, pre-authorization was not obtained.
        for source in ("fixture_metadata", "uploaded_file", "sarvam_extract", "gemini_candidate"):
            with self.subTest(source=source):
                claim = normalize_fixture(self.cases["TC007"])
                claim["documents"] = [
                    {**document, "source": source, "patient_name": "Suresh Patil"}
                    for document in claim["documents"]
                ]
                claim.pop("pre_authorization", None)
                result = evaluate_claim(claim, self.policy)
                self.assertEqual(result["decision"], "REJECTED")
                self.assertIn("PRE_AUTH_MISSING", {reason["code"] for reason in result["reasons"]})
                self.assertIn("approval record", " ".join(reason["message"] for reason in result["reasons"]).lower())
                step = next(item for item in result["trace"] if item["rule_id"] == "pre_authorization")
                self.assertEqual(step["status"], "FAIL")
                self.assertEqual(step["evidence"]["status_source"], "no_approval_record_supplied")
                self.assertEqual(step["evidence"]["matched_rules"][0]["rule_id"], "mri_scan")
                ceiling = next(item for item in result["trace"] if item["rule_id"] == "per_claim_limit")
                self.assertEqual(ceiling["status"], "DEFERRED_TO_PRE_AUTH")

    def test_claimed_pre_auth_without_dated_record_routes_to_review(self) -> None:
        claim = normalize_fixture(self.cases["TC007"])
        claim["pre_authorization"] = {"obtained": True}
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("PRE_AUTH_STATUS_UNKNOWN", {reason["code"] for reason in result["reasons"]})
        step = next(item for item in result["trace"] if item["rule_id"] == "pre_authorization")
        self.assertEqual(step["status"], "NOT_EVALUATED")

    def test_mri_at_or_below_threshold_needs_no_pre_auth(self) -> None:
        claim = normalize_fixture(self.cases["TC007"])
        claim["claimed_amount"] = 9000
        bill = next(doc for doc in claim["documents"] if doc["doc_type"] == "HOSPITAL_BILL")
        bill["fields"].update(total=9000, line_items=[{"description": "MRI Lumbar Spine", "amount": 9000}])
        result = evaluate_claim(claim, self.policy)
        step = next(item for item in result["trace"] if item["rule_id"] == "pre_authorization")
        self.assertEqual(step["status"], "PASS")
        self.assertNotIn("PRE_AUTH_MISSING", {reason["code"] for reason in result["reasons"]})

    def test_live_required_pre_auth_explicit_false_is_policy_denial(self) -> None:
        claim = normalize_fixture(self.cases["TC007"])
        claim["documents"] = [
            {**document, "source": "uploaded_file", "patient_name": "Suresh Patil"}
            for document in claim["documents"]
        ]
        claim["pre_authorization"] = False
        result = evaluate_claim(claim, self.policy)

        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("PRE_AUTH_MISSING", {reason["code"] for reason in result["reasons"]})
        step = next(item for item in result["trace"] if item["rule_id"] == "pre_authorization")
        self.assertEqual(step["status"], "FAIL")

    def test_pharmacy_branded_medicine_routes_to_generic_substitution_review(self) -> None:
        claim = self._consultation_claim()
        claim["claim_category"] = "PHARMACY"
        bill = next(doc for doc in claim["documents"] if doc["doc_type"] == "HOSPITAL_BILL")
        bill["doc_type"] = "PHARMACY_BILL"
        bill["fields"]["total"] = 1500
        bill["fields"]["line_items"] = [{"description": "Brand Medicine", "amount": 1500, "brand_status": "BRANDED", "brand_evidence": "Brand"}]
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("GENERIC_SUBSTITUTION_REVIEW", {reason["code"] for reason in result["reasons"]})
        self.assertTrue(any(item["description"] == "Branded medicine co-pay" for item in result["ledger"]))

        bill["fields"]["line_items"][0].pop("brand_status")
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("PHARMACY_BRAND_STATUS_UNKNOWN", {reason["code"] for reason in result["reasons"]})

    def test_required_pre_auth_must_be_dated_and_within_validity_window(self) -> None:
        claim = normalize_fixture(self.cases["TC007"])
        claim["documents"].append({"file_id": "PREAUTH-1", "actual_type": "PRE_AUTHORIZATION", "quality": "GOOD", "fields": {"date": "2024-09-01", "approval_reference": "AUTH-123"}, "source": "fixture_metadata"})
        claim["pre_authorization"] = {
            "obtained": True,
            "issued_date": "2024-09-01",
            "approval_reference": "AUTH-123",
        }
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("PRE_AUTH_INVALID", {reason["code"] for reason in result["reasons"]})

    def test_required_pre_auth_accepts_dated_approval_evidence(self) -> None:
        claim = normalize_fixture(self.cases["TC007"])
        claim["documents"].append({"file_id": "PREAUTH-2", "actual_type": "PRE_AUTHORIZATION", "quality": "GOOD", "fields": {"date": "2024-10-20", "approval_reference": "AUTH-456"}, "source": "fixture_metadata"})
        claim["pre_authorization"] = {
            "obtained": True,
            "issued_date": "2024-10-20",
            "approval_reference": "AUTH-456",
        }
        result = evaluate_claim(claim, self.policy)
        self.assertNotIn("PRE_AUTH_STATUS_UNKNOWN", {reason["code"] for reason in result["reasons"]})
        step = next(item for item in result["trace"] if item["rule_id"] == "pre_authorization")
        self.assertEqual(step["status"], "PASS")

    def test_conflicting_pre_auth_form_and_document_routes_to_review(self) -> None:
        claim = normalize_fixture(self.cases["TC007"])
        claim["documents"] = [
            {**document, "source": "uploaded_file", "patient_name": "Suresh Patil"}
            for document in claim["documents"]
        ]
        claim["documents"].append({
            "file_id": "PREAUTH-CONFLICT", "actual_type": "PRE_AUTHORIZATION", "quality": "GOOD",
            "fields": {"date": "2024-10-20", "approval_reference": "AUTH-789"}, "source": "uploaded_file",
        })
        claim["pre_authorization"] = {"obtained": False}

        result = evaluate_claim(claim, self.policy)

        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("PRE_AUTH_CONFLICT", {reason["code"] for reason in result["reasons"]})
        step = next(item for item in result["trace"] if item["rule_id"] == "pre_authorization")
        self.assertEqual(step["evidence"]["status_source"], "form_document_conflict")

    def test_sum_insured_and_family_floater_remaining_limits_reduce_payment(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        claim["sum_insured_used"] = 499000
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "PARTIAL")
        self.assertEqual(result["approved_amount"], 1000)
        self.assertIn("SUM_INSURED_LIMITED", {reason["code"] for reason in result["reasons"]})

        claim = normalize_fixture(self.cases["TC004"])
        claim["sum_insured_used"] = 0
        claim["family_floater_used"] = 149000
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "PARTIAL")
        self.assertEqual(result["approved_amount"], 1000)
        self.assertIn("FAMILY_FLOATER_LIMITED", {reason["code"] for reason in result["reasons"]})

    def test_alternative_medicine_requires_covered_system_and_annual_session_history(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        claim["claim_category"] = "ALTERNATIVE_MEDICINE"
        claim["prior_sessions"] = 16
        for document in claim["documents"]:
            document["fields"]["diagnosis"] = "Ayurveda therapy (5 sessions)"
            document["fields"]["doctor_registration"] = "KA/12345/2020"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("SESSION_LIMIT_EXCEEDED", {reason["code"] for reason in result["reasons"]})

        claim["prior_sessions"] = 0
        for document in claim["documents"]:
            document["fields"]["diagnosis"] = "Therapy (5 sessions)"
            document["source"] = "uploaded_file"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("COVERED_SYSTEM_UNKNOWN", {reason["code"] for reason in result["reasons"]})

    def test_blank_line_description_is_unknown_not_excluded(self) -> None:
        claim = self._consultation_claim()
        bill = next(doc for doc in claim["documents"] if doc["doc_type"] == "HOSPITAL_BILL")
        bill["fields"]["line_items"][0]["description"] = ""
        result = evaluate_claim(claim, self.policy)
        line_item = next(item for item in result["ledger"] if item["kind"] == "line_item")
        self.assertEqual(line_item["status"], "UNKNOWN")
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertNotIn("EXCLUDED_PROCEDURE", {reason["code"] for reason in result["reasons"]})

    def test_configured_network_provider_branch_alias_matches(self) -> None:
        # Name variants and the branch-suffix rule now live in the normalizer.
        for name, provenance in (("Apollo Hospitals, Bengaluru", "policy_text"), ("Apollo Hospital - Jayanagar", "policy_text"), ("Narayana Hrudayalaya, Bengaluru", "interpretation")):
            with self.subTest(name=name):
                claim = normalize_fixture(self.cases["TC010"])
                claim["hospital_name"] = name
                result = evaluate_claim(claim, self.policy)
                pricing = next(step for step in result["trace"] if step["rule_id"] == "payable_amount")["evidence"]
                self.assertTrue(pricing["network_hospital"])
                self.assertEqual(result["approved_amount"], 3240)
                network = next(step for step in result["trace"] if step["rule_id"] == "network_hospital")
                self.assertEqual(network["evidence"]["match"]["provenance"], provenance)
        claim = normalize_fixture(self.cases["TC010"])
        claim["hospital_name"] = "Apollo Clinic Annexe"
        for document in claim["documents"]:
            document["fields"].pop("hospital_name", None)
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["approved_amount"], 4050)

    def test_document_date_conflict_routes_to_review(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        for document in claim["documents"]:
            fields = document.get("fields") or {}
            if "date" in fields:
                fields["date"] = "2024-05-01"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("DOCUMENT_DATE_CONFLICT", {reason["code"] for reason in result["reasons"]})

    def test_common_extracted_document_date_format_is_compared(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        for document in claim["documents"]:
            document["fields"]["date"] = "01-Nov-2024"

        result = evaluate_claim(claim, self.policy)

        self.assertEqual(result["decision"], "APPROVED")
        date_check = next(step for step in result["trace"] if step["rule_id"] == "document_treatment_date")
        self.assertEqual(date_check["status"], "PASS")

    def _dental_claim(self, *lines: tuple[str, int]) -> dict:
        claim = normalize_fixture(self.cases["TC006"])
        bill = claim["documents"][0]["fields"]
        bill["line_items"] = [{"description": description, "amount": amount} for description, amount in lines]
        bill["total"] = sum(amount for _, amount in lines)
        claim["claimed_amount"] = bill["total"]
        return claim

    def test_dental_item_neither_covered_nor_excluded_routes_to_review(self) -> None:
        # Formerly an unrecognised line was NOT_ON_ALLOWLIST and silently removed,
        # so a string miss could reject a real procedure. It is now UNRESOLVED.
        result = evaluate_claim(self._dental_claim(("Root Canal Treatment", 3000), ("Diamond Tooth Jewellery", 2000)), self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertEqual(result["reasons"][0]["code"], "LINE_ITEM_UNRESOLVED")
        lines = [entry for entry in result["ledger"] if entry["kind"] == "line_item"]
        self.assertEqual([entry.get("line_check", entry["status"]) for entry in lines], ["ELIGIBLE", "UNRESOLVED"])

    def test_item_from_another_category_list_is_not_covered_and_partial(self) -> None:
        result = evaluate_claim(self._dental_claim(("Root Canal Treatment", 3000), ("Eye Examination", 1000)), self.policy)
        self.assertEqual((result["decision"], result["approved_amount"]), ("PARTIAL", 3000))
        line = [entry for entry in result["ledger"] if entry["kind"] == "line_item"][1]
        self.assertEqual((line["status"], line["reason_code"]), ("NOT_COVERED", "NOT_ON_ALLOWLIST"))

    def test_covered_dental_procedures_match_billing_names_and_abbreviations(self) -> None:
        for description in ("Root canal", "RCT", "Root canal treatment - molar", "Scaling", "IOPA X-ray", "Crown"):
            with self.subTest(description=description):
                result = evaluate_claim(self._dental_claim((description, 8000)), self.policy)
                self.assertEqual((result["decision"], result["approved_amount"]), ("APPROVED", 8000))
        result = evaluate_claim(self._dental_claim(("RCT", 8000), ("Whitening", 4000)), self.policy)
        self.assertEqual((result["decision"], result["approved_amount"]), ("PARTIAL", 8000))

    def test_unlisted_dental_line_is_never_a_confident_rejection(self) -> None:
        result = evaluate_claim(self._dental_claim(("Root canal", 8000), ("Consultation", 500)), self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertNotIn("NO_PAYABLE_AMOUNT", {reason["code"] for reason in result["reasons"]})
        self.assertIn("LINE_ITEM_UNRESOLVED", {reason["code"] for reason in result["reasons"]})

    def test_unitemized_bill_cannot_pay_an_allowlisted_category(self) -> None:
        claim = self._dental_claim(("Root Canal Treatment", 8000))
        claim["documents"][0]["fields"].pop("line_items")
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        line = next(entry for entry in result["ledger"] if entry["kind"] == "line_item")
        self.assertEqual((line["description"], line["itemized"], line["status"]), ("Bill total (not itemized)", False, "UNRESOLVED"))

    def test_supplied_submission_date_enforces_deadline(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        claim["submission_date"] = "2024-12-15"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("SUBMISSION_LATE", {reason["code"] for reason in result["reasons"]})

    def test_malformed_submission_and_pre_existing_inputs_fail_closed(self) -> None:
        claim = self._consultation_claim()
        claim["submission_date"] = "yesterday"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("MALFORMED_SUBMISSION_DATE", {reason["code"] for reason in result["reasons"]})

        claim = self._consultation_claim()
        claim["pre_existing_conditions"] = "diabetes"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("MALFORMED_PRE_EXISTING_CONDITIONS", {reason["code"] for reason in result["reasons"]})

    def test_session_variants_and_ambiguous_session_language_are_safe(self) -> None:
        for text in ("Ayurveda therapy: sessions: 5", "Ayurveda 5-session course"):
            with self.subTest(text=text):
                claim = self._consultation_claim()
                claim["claim_category"] = "ALTERNATIVE_MEDICINE"
                claim["prior_sessions"] = 16
                for document in claim["documents"]:
                    document["fields"]["diagnosis"] = text
                    document["fields"]["doctor_registration"] = "KA/12345/2020"
                result = evaluate_claim(claim, self.policy)
                self.assertIn("SESSION_LIMIT_EXCEEDED", {reason["code"] for reason in result["reasons"]})

        claim = self._consultation_claim()
        claim["claim_category"] = "ALTERNATIVE_MEDICINE"
        for document in claim["documents"]:
            document["fields"]["diagnosis"] = "Ayurveda sessions recommended"
            document["fields"]["doctor_registration"] = "KA/12345/2020"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("SESSION_COUNT_MALFORMED", {reason["code"] for reason in result["reasons"]})

    def test_mixed_consultation_line_requires_allocation_review(self) -> None:
        claim = self._consultation_claim()
        bill = next(doc for doc in claim["documents"] if doc["doc_type"] == "HOSPITAL_BILL")
        bill["fields"]["line_items"] = [{"description": "Physician charges for blood report review", "amount": 1500}]
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("MIXED_SERVICE_LINE", {reason["code"] for reason in result["reasons"]})

    def test_pre_authorization_does_not_bypass_benefit_limits(self) -> None:
        claim = normalize_fixture(self.cases["TC007"])
        claim["ytd_claims_amount"] = 49900
        claim["documents"].append({"file_id": "PREAUTH-LIMIT", "actual_type": "PRE_AUTHORIZATION", "quality": "GOOD", "fields": {"date": "2024-10-20", "approval_reference": "AUTH-LIMIT"}, "source": "fixture_metadata"})
        claim["pre_authorization"] = {"obtained": True, "issued_date": "2024-10-20", "approval_reference": "AUTH-LIMIT"}

        result = evaluate_claim(claim, self.policy)

        self.assertTrue(any(item["description"] == "Annual OPD remaining limit" for item in result["ledger"]))

    def test_missing_treatment_date_cannot_be_adjudicated(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        claim.pop("treatment_date")
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("TREATMENT_DATE_REQUIRED", {reason["code"] for reason in result["reasons"]})

    def test_explicit_pre_existing_condition_history_applies_generic_wait(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        claim["treatment_date"] = "2024-10-01"
        for document in claim["documents"]:
            if "date" in document["fields"]:
                document["fields"]["date"] = "2024-10-01"
        claim["pre_existing_conditions"] = [{"condition": "Viral Fever"}]
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("PRE_EXISTING_WAITING_PERIOD", {reason["code"] for reason in result["reasons"]})
        waiting = next(step for step in result["trace"] if step["rule_id"] == "pre_existing_condition_wait")
        self.assertEqual(waiting["status"], "FAIL")

    def test_per_claim_ceiling_is_the_larger_of_global_and_category_limit(self) -> None:
        # Formerly the global limit rejected TC006 and the consultation sub-limit
        # capped TC010, contradicting the supplied expectations.
        consultation = self.evaluate("TC008")
        ceiling = next(step for step in consultation["trace"] if step["rule_id"] == "per_claim_limit")
        self.assertEqual((ceiling["status"], ceiling["policy_ref"], ceiling["evidence"]["limit"]), ("FAIL", "coverage.per_claim_limit", 5000))
        network = self.evaluate("TC010")
        self.assertEqual(next(step for step in network["trace"] if step["rule_id"] == "per_claim_limit")["status"], "PASS")
        sub_limit = next(step for step in network["trace"] if step["rule_id"] == "category_sub_limit")
        self.assertEqual((sub_limit["status"], sub_limit["evidence"]["service_net_payable"]), ("PASS", 1080))

        claim = normalize_fixture(self.cases["TC006"])
        bill = claim["documents"][0]["fields"]
        bill["line_items"] = [{"description": "Root Canal Treatment", "amount": 10500}]
        bill["total"] = 10500
        claim["claimed_amount"] = 10500
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("PER_CLAIM_EXCEEDED", {reason["code"] for reason in result["reasons"]})
        self.assertIn("10000", " ".join(reason["message"] for reason in result["reasons"]))

    def test_decisions_do_not_depend_on_evidence_provenance(self) -> None:
        for case_id, case in self.cases.items():
            baseline = self.evaluate(case_id)
            for source in ("uploaded_file", "pdf_text", "sarvam_extract", "gemini_candidate"):
                with self.subTest(case_id=case_id, source=source):
                    claim = normalize_fixture(case)
                    for document in claim["documents"]:
                        document["source"] = source
                    options = {"optional_risk_enricher": _raise_optional_enrichment_failure} if case["input"].get("simulate_component_failure") else {}
                    result = evaluate_claim(claim, self.policy, **options)
                    for field in ("decision", "approved_amount", "confidence_score", "correction_requests", "ledger"):
                        self.assertEqual(result[field], baseline[field])
                    self.assertEqual(result["reasons"], baseline["reasons"])

    def test_trace_carries_policy_fingerprint(self) -> None:
        result = self.evaluate("TC004")
        source = result["trace"][0]
        self.assertEqual(source["rule_id"], "policy_source")
        self.assertEqual(source["evidence"]["source_sha256"], self.policy["source"]["sha256"])
        self.assertIn("PER_CLAIM_CEILING_RULE", source["evidence"]["conflict_resolutions"])


class AuditRoundTwoTests(unittest.TestCase):
    """Regression tests for the second audit of the policy engine."""

    policy: dict
    cases: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_policy(POLICY_PATH)
        cls.cases = {case["case_id"]: case for case in load_cases(FIXTURE_PATH)}

    def claim(self, case_id: str) -> dict:
        return normalize_fixture(self.cases[case_id])

    @staticmethod
    def codes(result: dict) -> set[str]:
        return {reason["code"] for reason in result["reasons"]}

    @staticmethod
    def step(result: dict, rule_id: str) -> dict:
        return next(step for step in result["trace"] if step.get("rule_id") == rule_id)

    @staticmethod
    def set_bill(claim: dict, *lines: tuple[str, int]) -> dict:
        bill = next(doc for doc in claim["documents"] if doc["doc_type"].endswith("BILL"))
        bill["fields"]["line_items"] = [{"description": description, "amount": amount} for description, amount in lines]
        bill["fields"]["total"] = sum(amount for _, amount in lines)
        claim["claimed_amount"] = bill["fields"]["total"]
        return claim

    # Finding 2: negation-aware waiting-period and pre-auth matching.
    def test_negated_condition_does_not_trigger_waiting_period(self) -> None:
        claim = self.claim("TC005")
        prescription = claim["documents"][0]["fields"]
        prescription["diagnosis"] = "No history of diabetes; viral fever"
        prescription["medicines"] = []
        result = evaluate_claim(claim, self.policy)
        self.assertNotIn("WAITING_PERIOD", self.codes(result))
        self.assertEqual(self.step(result, "waiting_period")["evidence"]["condition"], "initial")

    def test_negated_imaging_does_not_require_pre_auth(self) -> None:
        claim = self.claim("TC004")
        claim["documents"][0]["fields"]["treatment"] = "Rest and fluids; no MRI scan needed"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(self.step(result, "pre_authorization")["status"], "PASS")

    # Finding 3: short imaging forms only in test/line context.
    def test_pet_in_diagnosis_prose_is_not_a_pet_scan(self) -> None:
        for text in ("Dog bite from pet", "Fever; CT not required", "Pet dander allergy"):
            with self.subTest(text=text):
                claim = self.claim("TC004")
                claim["documents"][0]["fields"]["diagnosis"] = text
                result = evaluate_claim(claim, self.policy)
                self.assertEqual(result["decision"], "APPROVED")
                self.assertNotIn("PRE_AUTH_MISSING", self.codes(result))

    def test_short_forms_still_match_ordered_tests_and_bill_lines(self) -> None:
        self.assertEqual(evaluate_claim(self.claim("TC007"), self.policy)["reasons"][0]["code"], "PRE_AUTH_MISSING")
        claim = self.set_bill(self.claim("TC007"), ("PET-CT whole body", 3000))
        claim["documents"][0]["fields"]["tests_ordered"] = ["PET-CT whole body"]
        claim["documents"][1]["fields"]["test_name"] = "PET-CT whole body"
        self.assertIn("PRE_AUTH_MISSING", self.codes(evaluate_claim(claim, self.policy)))
        claim = self.set_bill(self.claim("TC007"), ("PET bottle allergy panel", 3000))
        claim["documents"][0]["fields"]["tests_ordered"] = ["PET bottle allergy panel"]
        claim["documents"][1]["fields"]["test_name"] = "PET bottle allergy panel"
        self.assertNotIn("PRE_AUTH_MISSING", self.codes(evaluate_claim(claim, self.policy)))

    # Finding 4: the consultation sub_limit binds.
    def test_consultation_sub_limit_caps_the_consultation_fee_benefit(self) -> None:
        result = evaluate_claim(self.set_bill(self.claim("TC004"), ("Consultation Fee", 4900)), self.policy)
        self.assertEqual((result["decision"], result["approved_amount"]), ("PARTIAL", 2000))
        self.assertEqual(result["reasons"][0]["code"], "CATEGORY_SUB_LIMIT_LIMITED")
        adjustment = next(item for item in result["ledger"] if item.get("rule_id") == "category_sub_limit")
        self.assertEqual(adjustment["amount_paise"], -241000)

    def test_an_unfamiliar_name_for_a_consultation_fee_does_not_escape_the_sub_limit(self) -> None:
        result = evaluate_claim(self.set_bill(self.claim("TC004"), ("Doctor visit charges", 4000)), self.policy)
        self.assertEqual((result["decision"], result["approved_amount"]), ("PARTIAL", 2000))
        self.assertEqual(result["reasons"][0]["code"], "CATEGORY_SUB_LIMIT_LIMITED")

    def test_category_history_bounds_repeated_consultations(self) -> None:
        paid, used = [], 0
        for _ in range(4):
            claim = self.set_bill(self.claim("TC004"), ("Consultation Fee", 4900))
            claim["category_ytd_claims_amount"] = used / 100
            result = evaluate_claim(claim, self.policy)
            paid.append(result["approved_amount_paise"])
            used += result["approved_amount_paise"]
        self.assertEqual(paid, [200000, 0, 0, 0])
        self.assertLessEqual(sum(paid), self.policy["categories"]["CONSULTATION"]["sub_limit_paise"])

    def test_tests_and_medicines_on_a_consultation_bill_are_not_capped_by_the_fee_sub_limit(self) -> None:
        result = evaluate_claim(self.claim("TC010"), self.policy)
        self.assertEqual(result["approved_amount"], 3240)
        claim = self.claim("TC010")
        claim["category_ytd_claims_amount"] = 1500
        result = evaluate_claim(claim, self.policy)
        # Fee net 1080 exceeds the ₹500 left; medicines (net 2160) are unaffected.
        self.assertEqual((result["decision"], result["approved_amount"]), ("PARTIAL", 2660))

    def test_unitemized_consultation_above_the_sub_limit_routes_to_review(self) -> None:
        claim = self.claim("TC004")
        claim["documents"][1]["fields"].pop("line_items")
        claim["documents"][1]["fields"]["total"] = claim["claimed_amount"] = 4000
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("CATEGORY_SUB_LIMIT_UNVERIFIED", self.codes(result))

    def test_absent_category_history_is_disclosed_on_payable_outcomes(self) -> None:
        result = evaluate_claim(self.claim("TC004"), self.policy)
        self.assertIn("CATEGORY_SUB_LIMIT_HISTORY_NOT_EVALUATED", self.codes(result))
        claim = self.claim("TC004")
        claim["category_ytd_claims_amount"] = 0
        supplied = evaluate_claim(claim, self.policy)
        self.assertNotIn("CATEGORY_SUB_LIMIT_HISTORY_NOT_EVALUATED", self.codes(supplied))
        self.assertGreater(supplied["confidence_score"], result["confidence_score"])

    # Finding 5: aggregate caps apply to the net payable.
    def test_annual_limit_caps_the_net_payable_after_discount_and_copay(self) -> None:
        claim = self.claim("TC010")
        claim["ytd_claims_amount"] = 48000
        result = evaluate_claim(claim, self.policy)
        self.assertEqual((result["decision"], result["approved_amount"]), ("PARTIAL", 2000))
        order = [item["description"] for item in result["ledger"] if item["kind"] == "adjustment"]
        self.assertLess(order.index("Member co-pay"), order.index("Annual OPD remaining limit"))

    # Finding 6: the risk-signal enrichment is a real default component.
    def test_risk_enrichment_runs_by_default_and_flags_accumulated_value(self) -> None:
        clean = evaluate_claim(self.claim("TC004"), self.policy)
        self.assertEqual(self.step(clean, "risk_enrichment")["status"], "PASS")
        claim = self.claim("TC004")
        claim["claims_history"] = [{"claim_id": "C1", "date": "2024-10-20", "amount": 24000, "provider": "Other Clinic"}]
        flagged = evaluate_claim(claim, self.policy)
        self.assertEqual(flagged["decision"], "MANUAL_REVIEW")
        self.assertIn("RISK_SIGNAL_REVIEW", self.codes(flagged))
        prior = {"claim_id": "C2", "amount": 1500, "provider": "City Clinic, Bengaluru"}
        claim["claims_history"] = [{**prior, "date": "2024-10-25"}]
        follow_up = evaluate_claim(claim, self.policy)
        signals = {signal["signal"]: signal["status"] for signal in self.step(follow_up, "risk_enrichment")["evidence"]}
        self.assertEqual(signals["repeat_billing"], "PASS")
        claim["claims_history"] = [{**prior, "date": claim["treatment_date"]}]
        repeat = evaluate_claim(claim, self.policy)
        signals = {signal["signal"]: signal["status"] for signal in self.step(repeat, "risk_enrichment")["evidence"]}
        self.assertEqual(signals["repeat_billing"], "FLAG")

    def test_unverified_sub_limit_share_is_not_a_reason_on_a_rejection(self) -> None:
        rejected = evaluate_claim(self.claim("TC005"), self.policy)
        self.assertEqual(rejected["decision"], "REJECTED")
        self.assertNotIn("CATEGORY_SUB_LIMIT_UNVERIFIED", self.codes(rejected))
        self.assertEqual(self.step(rejected, "category_sub_limit")["status"], "NOT_EVALUATED")

    def test_component_failure_is_consistent_with_an_approval(self) -> None:
        def broken(_: dict) -> dict:
            raise RuntimeError("down")

        result = evaluate_claim(self.claim("TC011"), self.policy, optional_risk_enricher=broken)
        baseline = evaluate_claim(self.claim("TC011"), self.policy)
        self.assertEqual((result["decision"], baseline["decision"]), ("APPROVED", "APPROVED"))
        self.assertLess(result["confidence_score"], baseline["confidence_score"])
        message = next(reason["message"] for reason in result["reasons"] if reason["code"] == "COMPONENT_DEGRADED")
        self.assertIn("decision rests on them", message)
        self.assertIn("manual review of this decision is recommended", message)
        self.assertTrue(result["trace"][-1]["evidence"]["post_decision_review_recommended"])
        self.assertFalse(baseline["trace"][-1]["evidence"]["post_decision_review_recommended"])

    def test_malformed_enrichment_output_counts_as_a_component_failure(self) -> None:
        result = evaluate_claim(self.claim("TC004"), self.policy, optional_risk_enricher=lambda _: None)
        self.assertEqual(self.step(result, "risk_enrichment")["status"], "SKIPPED_COMPONENT_FAILURE")

    # Finding 7: ledger lines on a claim-level exclusion.
    def test_claim_level_exclusion_marks_every_line_excluded(self) -> None:
        result = evaluate_claim(self.claim("TC012"), self.policy)
        lines = [item for item in result["ledger"] if item["kind"] == "line_item"]
        self.assertEqual({line["status"] for line in lines}, {"EXCLUDED"})
        diet = next(line for line in lines if line["description"].startswith("Personalised"))
        self.assertEqual((diet["reason_code"], diet["line_check"]), ("EXCLUDED_CONDITION", "ELIGIBLE"))
        self.assertNotIn("Covered.", {line["reason"] for line in lines})

    def test_rejected_claim_lines_are_not_adjudicated(self) -> None:
        result = evaluate_claim(self.claim("TC007"), self.policy)
        line = next(item for item in result["ledger"] if item["kind"] == "line_item")
        self.assertEqual((line["status"], line["line_check"]), ("NOT_ADJUDICATED", "ELIGIBLE"))
        self.assertIn("PRE_AUTH_MISSING", line["reason"])

    # Finding 8: materiality-scoped confidence.
    def test_missing_identity_lowers_member_dependent_rejections_and_reviews_only(self) -> None:
        exclusion = evaluate_claim(self.claim("TC012"), self.policy)
        self.assertEqual(exclusion["confidence_score"], 0.96)
        waiting = self.claim("TC005")
        for document in waiting["documents"]:
            document["patient_name"] = None
            document["fields"].pop("patient_name", None)
        waiting_result = evaluate_claim(waiting, self.policy)
        self.assertEqual(waiting_result["decision"], "REJECTED")
        self.assertLess(waiting_result["confidence_score"], evaluate_claim(self.claim("TC005"), self.policy)["confidence_score"])
        review = evaluate_claim(self.claim("TC009"), self.policy)
        factors = {item["reason"]: item["applied"] for item in self.step(review, "confidence_rubric")["evidence"]["factors"]}
        self.assertTrue(factors["patient_name_unavailable"])

    # Finding 9: core fails closed for payment.
    def test_core_fails_closed_without_bill_amount_or_good_quality(self) -> None:
        claim = self.claim("TC004")
        claim["documents"][1]["fields"] = {"patient_name": "Rajesh Kumar"}
        result = evaluate_claim(claim, self.policy)
        self.assertEqual((result["decision"], result["reasons"][0]["code"]), ("MANUAL_REVIEW", "BILL_AMOUNT_UNVERIFIED"))
        self.assertFalse([item for item in result["ledger"] if item["kind"] == "line_item"])
        claim = self.claim("TC004")
        claim["documents"][1]["quality"] = "PARTIAL"
        result = evaluate_claim(claim, self.policy)
        self.assertEqual((result["decision"], result["reasons"][0]["code"]), ("MANUAL_REVIEW", "DOCUMENT_QUALITY_INSUFFICIENT"))

    def test_explicit_identity_failure_from_document_layer_blocks_payment(self) -> None:
        for status, decision in (("FAILED", "MANUAL_REVIEW"), ("UNVERIFIED", "MANUAL_REVIEW"), ("VERIFIED", "APPROVED"), ("NOT_AVAILABLE", "APPROVED")):
            with self.subTest(status=status):
                claim = self.claim("TC004")
                claim["identity_verification"] = status
                self.assertEqual(evaluate_claim(claim, self.policy)["decision"], decision)

    # Finding 10: member-supplied pre-auth records are disclosed as unverified.
    def test_member_supplied_pre_auth_record_is_unverified_with_insurer(self) -> None:
        claim = self.claim("TC007")
        claim["documents"].append({"file_id": "PA", "doc_type": "PRE_AUTHORIZATION", "quality": "GOOD", "fields": {"approval_reference": "PA-1", "date": "2024-10-25", "approved_amount": 15000}})
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "PARTIAL")
        step = self.step(result, "pre_authorization")
        self.assertEqual(step["evidence"]["status_source"], "member_supplied_record_unverified_with_insurer")
        self.assertIn("PRE_AUTH_NOT_VERIFIED_WITH_INSURER", self.codes(result))
        factors = {item["reason"]: item["applied"] for item in self.step(result, "confidence_rubric")["evidence"]["factors"]}
        self.assertTrue(factors["pre_auth_record_member_supplied"])

    # Finding 13: tampered canonical config.
    def test_tampered_canonical_policy_is_refused(self) -> None:
        tampered = deepcopy(self.policy)
        tampered["categories"]["CONSULTATION"]["copay_percent"] = 0
        result = evaluate_claim(self.claim("TC004"), tampered)
        self.assertEqual((result["decision"], result["reasons"][0]["code"]), ("MANUAL_REVIEW", "POLICY_CONFIGURATION_INVALID"))
        self.assertNotIn("evidence", result["trace"][0])

    def test_trace_persists_the_normalizer_audit_trail(self) -> None:
        source = self.step(evaluate_claim(self.claim("TC004"), self.policy), "policy_source")["evidence"]
        self.assertEqual(source["audit"], self.policy["audit"])
        self.assertTrue(source["canonical_sha256_verified"])
        self.assertEqual(len(source["audit_sha256"]), 64)

    # Finding 14: invalid risk inputs.
    def test_invalid_risk_inputs_are_rejected_not_ignored(self) -> None:
        for key, value in (("fraud_score", "NaN"), ("fraud_score", "sNaN"), ("fraud_score", True), ("fraud_score", 1.5), ("prior_sessions", -50), ("prior_sessions", True), ("claims_history", "x"), ("currency", "USD"), ("ytd_claims_amount", -1)):
            with self.subTest(key=key, value=value):
                claim = self.claim("TC004")
                claim[key] = value
                result = evaluate_claim(claim, self.policy)
                self.assertEqual((result["decision"], result["reasons"][0]["code"]), ("MANUAL_REVIEW", "MALFORMED_EVIDENCE"))
        claim = self.claim("TC004")
        claim["fraud_score"] = "0.85"
        self.assertIn("FRAUD_SCORE_REVIEW", self.codes(evaluate_claim(claim, self.policy)))

    # Finding 16: the primary reason leads the reasons list.
    def test_primary_reason_is_listed_first(self) -> None:
        for case_id, case in self.cases.items():
            wanted = case["expected"].get("rejection_reasons")
            if wanted:
                with self.subTest(case_id=case_id):
                    result = evaluate_claim(self.claim(case_id), self.policy)
                    self.assertEqual(result["reasons"][0]["code"], wanted[0])
                    self.assertEqual(result["trace"][-1]["evidence"]["primary_reason"], wanted[0])


if __name__ == "__main__":
    unittest.main()
