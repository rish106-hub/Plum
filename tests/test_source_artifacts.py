"""Guard the evaluator-supplied artifacts and score every original case against them."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from claims.agent_pipeline import adjudicate_handoff
from claims.core import evaluate_claim
from claims.fixtures import load_cases, load_policy, normalize_fixture

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "test_cases.json"
POLICY_PATH = ROOT / "data" / "policy_terms.json"

# sha256 of the files as supplied in the evaluator's initial commit. These files
# are read-only inputs; behavior must be derived from them, never fitted to edits.
SUPPLIED_ARTIFACTS = {
    "data/policy_terms.json": "1b19689948d8273c32ec2b5f35c75c25ad48a5ae46b937092c464178de4484ce",
    "tests/fixtures/test_cases.json": "4b9b9a047ec6a6479a81f6b2767f00f931920ad7bedb3f547abb54b771034e63",
    "docs/reference/assignment.md": "538eb43b6b6ecd983904cbda0c8f2efcca8ea1703e8e18e6c981f85c7db0e2bc",
    "docs/reference/sample_documents_guide.md": "b28f17041652a5d553abf1521088e282c38d25965d15567054bbcc0d556b939d",
}
CASES = load_cases(FIXTURE_PATH)


@pytest.mark.parametrize("relative_path, expected", sorted(SUPPLIED_ARTIFACTS.items()))
def test_supplied_artifact_is_unmodified(relative_path: str, expected: str) -> None:
    assert hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest() == expected


def test_all_original_cases_are_present() -> None:
    assert [case["case_id"] for case in CASES] == [f"TC{number:03d}" for number in range(1, 13)]


def _raise_optional_enrichment_failure(_: dict) -> None:
    raise RuntimeError("synthetic optional component failure")


@pytest.fixture(scope="module")
def policy() -> dict:
    return load_policy(POLICY_PATH)


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_original_case_matches_expected(case: dict, policy: dict) -> None:
    options = {}
    if case["input"].get("simulate_component_failure"):
        options["optional_risk_enricher"] = _raise_optional_enrichment_failure
    result = adjudicate_handoff(
        normalize_fixture(case), policy, lambda claim, terms: evaluate_claim(claim, terms, **options)
    )
    expected = case["expected"]
    assert result["decision"] == expected["decision"]
    if "approved_amount" in expected:
        assert result["approved_amount"] == expected["approved_amount"]
    codes = {reason["code"] for reason in result["reasons"]}
    assert set(expected.get("rejection_reasons", [])) <= codes
    confidence_rule = expected.get("confidence_score")
    if isinstance(confidence_rule, str):
        assert confidence_rule.startswith("above ")
        assert result["confidence_score"] > float(confidence_rule.removeprefix("above "))
    assert result["trace"][0]["evidence"]["source_sha256"] == SUPPLIED_ARTIFACTS["data/policy_terms.json"]
