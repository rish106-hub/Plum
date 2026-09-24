"""Behavioral tests for the deterministic policy reducer and fixture contract."""

from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

from claims.core import evaluate_claim
from claims.fixtures import load_cases, load_policy, normalize_fixture

ROOT = Path(__file__).resolve().parents[1]


class ClaimCoreTests(unittest.TestCase):
    policy: dict
    cases: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_policy(ROOT / "policy_terms.json")
        cls.cases = {case["case_id"]: case for case in load_cases(ROOT / "test_cases.json")}

    def evaluate(self, case_id: str) -> dict:
        return evaluate_claim(normalize_fixture(self.cases[case_id]), self.policy)

    def test_all_fixture_decisions_and_amounts(self) -> None:
        for case_id, case in self.cases.items():
            with self.subTest(case_id=case_id):
                result = self.evaluate(case_id)
                self.assertEqual(result["decision"], case["expected"]["decision"])
                if "approved_amount" in case["expected"]:
                    self.assertEqual(result["approved_amount"], case["expected"]["approved_amount"])
                if "rejection_reasons" in case["expected"]:
                    self.assertTrue(set(case["expected"]["rejection_reasons"]) <= {reason["code"] for reason in result["reasons"]})
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
                self.assertEqual([step["stage"] for step in result["trace"]], ["document_gate"])
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

    def test_dental_item_exclusion_and_assumptions_are_visible(self) -> None:
        result = self.evaluate("TC006")
        self.assertEqual(result["approved_amount_paise"], 800000)
        self.assertEqual([entry["status"] for entry in result["ledger"] if entry["kind"] == "line_item"], ["ELIGIBLE", "EXCLUDED"])
        self.assertTrue(any(step["status"] == "ASSUMPTION" and step["rule_id"] == "additional_document_requirement" for step in result["trace"]))
        self.assertTrue(any(step["status"] == "ASSUMPTION" and step["rule_id"] == "per_claim_limit" for step in result["trace"]))

    def test_discount_precedes_copay_and_uses_integer_paise(self) -> None:
        result = self.evaluate("TC010")
        pricing = next(step for step in result["trace"] if step["rule_id"] == "payable_amount")["evidence"]
        self.assertEqual(pricing["eligible_paise"], 450000)
        self.assertEqual(pricing["network_discount_paise"], 90000)
        self.assertEqual(pricing["copay_paise"], 36000)
        self.assertEqual(pricing["payable_paise"], 324000)

    def test_optional_failure_is_visible_without_changing_supported_decision(self) -> None:
        result = self.evaluate("TC011")
        self.assertEqual(result["decision"], "APPROVED")
        self.assertLess(result["confidence_score"], self.evaluate("TC004")["confidence_score"])
        self.assertTrue(any(step["status"] == "SKIPPED_COMPONENT_FAILURE" for step in result["trace"]))
        self.assertIn("manual review", " ".join(reason["message"] for reason in result["reasons"]).lower())

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

    def test_real_upload_with_no_identity_requires_review(self) -> None:
        claim = normalize_fixture(self.cases["TC009"])
        claim["documents"] = [{**document, "source": "uploaded_file"} for document in claim["documents"]]
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("PATIENT_IDENTITY_UNKNOWN", {reason["code"] for reason in result["reasons"]})

    def test_policy_values_are_read_from_input(self) -> None:
        policy = deepcopy(self.policy)
        policy["opd_categories"]["consultation"]["copay_percent"] = 0
        result = evaluate_claim(normalize_fixture(self.cases["TC004"]), policy)
        self.assertEqual(result["approved_amount"], 1500)

    def test_patient_name_is_checked_against_member_roster(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        for document in claim["documents"]:
            document["patient_name"] = "Someone Else"
            document["fields"]["patient_name"] = "Someone Else"
        result = evaluate_claim(claim, self.policy)
        self.assertIsNone(result["decision"])
        self.assertEqual(result["correction_requests"][0]["code"], "PATIENT_NOT_COVERED")

    def test_claim_bill_and_line_items_must_reconcile(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        claim["claimed_amount"] = 1600
        result = evaluate_claim(claim, self.policy)
        self.assertIsNone(result["decision"])
        self.assertEqual(result["correction_requests"][0]["code"], "AMOUNT_MISMATCH")
        self.assertEqual(next(step for step in result["trace"] if step["rule_id"] == "bill_amount")["status"], "FAIL")

    def test_real_upload_unknown_annual_usage_needs_review(self) -> None:
        claim = normalize_fixture(self.cases["TC004"])
        claim.pop("ytd_claims_amount")
        claim["documents"] = [{**document, "source": "uploaded_file"} for document in claim["documents"]]
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("ANNUAL_USAGE_UNKNOWN", {reason["code"] for reason in result["reasons"]})

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
        for alias in ("T2DM", "HTN", "Hypothyroidism"):
            with self.subTest(alias=alias):
                claim = normalize_fixture(self.cases["TC005"])
                self._set_claim_text(claim, alias)
                result = evaluate_claim(claim, self.policy)
                self.assertEqual(result["decision"], "REJECTED")
                self.assertIn("WAITING_PERIOD", {reason["code"] for reason in result["reasons"]})

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

    def test_pharmacy_branded_copay_requires_explicit_status(self) -> None:
        claim = self._consultation_claim()
        claim["claim_category"] = "PHARMACY"
        bill = next(doc for doc in claim["documents"] if doc["doc_type"] == "HOSPITAL_BILL")
        bill["doc_type"] = "PHARMACY_BILL"
        bill["fields"]["total"] = 1500
        bill["fields"]["line_items"] = [{"description": "Brand Medicine", "amount": 1500, "brand_status": "BRANDED", "brand_evidence": "Brand"}]
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["approved_amount"], 1050)
        self.assertTrue(any(item["description"] == "Branded medicine co-pay" for item in result["ledger"]))

        bill["fields"]["line_items"][0].pop("brand_status")
        result = evaluate_claim(claim, self.policy)
        self.assertEqual(result["decision"], "MANUAL_REVIEW")
        self.assertIn("PHARMACY_BRAND_STATUS_UNKNOWN", {reason["code"] for reason in result["reasons"]})

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
        claim = normalize_fixture(self.cases["TC010"])
        claim["hospital_name"] = "Apollo Hospitals, Bengaluru"
        result = evaluate_claim(claim, self.policy)
        pricing = next(step for step in result["trace"] if step["rule_id"] == "payable_amount")["evidence"]
        self.assertTrue(pricing["network_hospital"])
        self.assertEqual(result["approved_amount"], 3240)

    def test_fixture_limit_interpretations_are_explicit_and_not_general_rules(self) -> None:
        dental = normalize_fixture(self.cases["TC006"])
        dental.pop("fixture_compatibility_assumptions")
        result = evaluate_claim(dental, self.policy)
        self.assertEqual(result["decision"], "REJECTED")
        self.assertIn("PER_CLAIM_EXCEEDED", {reason["code"] for reason in result["reasons"]})

        consultation = normalize_fixture(self.cases["TC010"])
        consultation.pop("fixture_compatibility_assumptions")
        consultation.pop("fixture_sub_limit_item_phrase")
        result = evaluate_claim(consultation, self.policy)
        cap = next(step for step in result["trace"] if step["rule_id"] == "category_sub_limit")
        self.assertEqual(cap["status"], "LIMITED")


if __name__ == "__main__":
    unittest.main()
