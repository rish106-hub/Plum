"""Strict policy schema and canonical normalizer behavior."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from claims.core import evaluate_claim
from claims.fixtures import load_cases, normalize_fixture
from claims.policy import (
    CANONICAL_SCHEMA,
    PolicyConfigurationError,
    canonical_fingerprint,
    ensure_canonical,
    load_policy,
    normalize_policy,
    policy_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "policy_terms.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "test_cases.json"


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def policy() -> dict:
    return load_policy(POLICY_PATH)


def _audit(policy: dict, prefix: str) -> list[dict]:
    return [entry for entry in policy["audit"] if entry["id"].startswith(prefix)]


def test_canonical_config_is_fingerprinted_and_deterministic(policy: dict) -> None:
    assert policy["schema_version"] == CANONICAL_SCHEMA
    assert policy["source"]["sha256"] == hashlib.sha256(POLICY_PATH.read_bytes()).hexdigest()
    assert load_policy(POLICY_PATH) == policy
    assert ensure_canonical(policy) is policy
    assert policy["canonical_sha256"] == canonical_fingerprint(policy) == policy_fingerprint(policy)
    assert policy_fingerprint(json.loads(POLICY_PATH.read_text(encoding="utf-8"))) == normalize_policy(json.loads(POLICY_PATH.read_text(encoding="utf-8")))["canonical_sha256"]


@pytest.mark.parametrize("path", [
    ("coverage", "per_claim_limit"),
    ("coverage", "annual_opd_limit"),
    ("opd_categories", "consultation", "sub_limit"),
    ("opd_categories", "consultation", "copay_percent"),
    ("submission_rules", "minimum_claim_amount"),
    ("fraud_thresholds", "same_day_claims_limit"),
])
def test_missing_limit_fails_fast_instead_of_defaulting_to_zero(raw: dict, path: tuple[str, ...]) -> None:
    broken = deepcopy(raw)
    parent = broken
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]
    with pytest.raises(PolicyConfigurationError) as error:
        normalize_policy(broken)
    assert ".".join(path) in str(error.value)
    assert error.value.code == "POLICY_CONFIGURATION_INVALID"


@pytest.mark.parametrize("value", ["5000", 5000.5, -1, None, True])
def test_mistyped_limit_is_rejected(raw: dict, value: object) -> None:
    broken = deepcopy(raw)
    broken["coverage"]["per_claim_limit"] = value
    with pytest.raises(PolicyConfigurationError, match="coverage.per_claim_limit"):
        normalize_policy(broken)


def test_percentages_are_bounded(raw: dict) -> None:
    broken = deepcopy(raw)
    broken["opd_categories"]["consultation"]["copay_percent"] = 110
    with pytest.raises(PolicyConfigurationError, match="copay_percent"):
        normalize_policy(broken)


@pytest.mark.parametrize("location, key, value", [
    (("waiting_periods",), "condition_aliases", {"diabetes": ["T2DM"]}),
    (("exclusions",), "condition_aliases", ["tonic"]),
    ((), "network_hospital_aliases", {"Apollo Hospitals": ["Apollo"]}),
    (("opd_categories", "pharmacy"), "brand_status_field", "brand_status"),
])
def test_edited_policy_alias_fields_are_rejected(raw: dict, location: tuple[str, ...], key: str, value: object) -> None:
    edited = deepcopy(raw)
    target = edited
    for part in location:
        target = target[part]
    target[key] = value
    with pytest.raises(PolicyConfigurationError, match="Extra inputs are not permitted"):
        normalize_policy(edited)


def test_malformed_policy_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PolicyConfigurationError, match="cannot read"):
        load_policy(path)
    with pytest.raises(PolicyConfigurationError):
        normalize_policy([])  # type: ignore[arg-type]


def test_category_and_document_matrix_must_agree(raw: dict) -> None:
    broken = deepcopy(raw)
    del broken["document_requirements"]["VISION"]
    with pytest.raises(PolicyConfigurationError, match="VISION"):
        normalize_policy(broken)


def test_unparseable_pre_auth_qualifier_fails_fast(raw: dict) -> None:
    broken = deepcopy(raw)
    broken["pre_authorization"]["required_for"].append("Endoscopy (if elective)")
    with pytest.raises(PolicyConfigurationError, match="required_for"):
        normalize_policy(broken)


def test_invalid_policy_routes_claim_to_review_with_configuration_code(raw: dict) -> None:
    broken = deepcopy(raw)
    del broken["coverage"]["per_claim_limit"]
    case = load_cases(FIXTURE_PATH)[3]
    result = evaluate_claim(normalize_fixture(case), broken)
    assert result["decision"] == "MANUAL_REVIEW"
    assert result["reasons"][0]["code"] == "POLICY_CONFIGURATION_INVALID"
    assert result["approved_amount_paise"] == 0


def test_per_claim_ceiling_per_category(policy: dict) -> None:
    ceilings = {name: item["per_claim_ceiling_paise"] // 100 for name, item in policy["categories"].items()}
    assert ceilings == {
        "CONSULTATION": 5000, "DIAGNOSTIC": 10000, "PHARMACY": 15000,
        "DENTAL": 10000, "VISION": 5000, "ALTERNATIVE_MEDICINE": 8000,
    }
    assert policy["categories"]["CONSULTATION"]["per_claim_ceiling_ref"] == "coverage.per_claim_limit"
    assert _audit(policy, "PER_CLAIM_CEILING_RULE")[0]["kind"] == "conflict_resolution"


def test_pre_auth_rules_are_parsed_from_original_strings_and_diagnostic_list(policy: dict) -> None:
    rules = {rule["id"]: rule for rule in policy["pre_authorization"]["rules"]}
    assert rules["mri_scan"]["amount_greater_than_paise"] == 1_000_000
    assert rules["ct_scan"]["amount_greater_than_paise"] == 1_000_000
    assert "opd_categories.diagnostic.pre_auth_threshold" in rules["mri_scan"]["source_paths"]
    mri = next(term for term in rules["mri_scan"]["terms"] if term["text"] == "mri")
    assert (mri["provenance"], mri["context"]) == ("policy_text", "test_or_line")
    pet = next(term for term in rules["pet_scan"]["terms"] if term["text"] == "pet")
    assert pet["context"] == "test_or_line" and "scan" in pet["requires_any"]
    assert next(term for term in rules["mri_scan"]["terms"] if term["text"] == "mri scan")["context"] == "service"
    assert _audit(policy, "PRE_AUTH_MATCH_SCOPE")
    # Global list says PET always; the diagnostic threshold says above Rs 10,000.
    assert rules["pet_scan"]["amount_greater_than_paise"] is None
    assert _audit(policy, "PRE_AUTH_THRESHOLD_CONFLICT.pet_scan")
    assert set(rules) >= {"major_surgical_procedures", "planned_hospitalization"}


def test_duplicate_exclusion_representations_are_merged(policy: dict) -> None:
    by_scope: dict[str, list[dict]] = {}
    for entry in policy["exclusions"]:
        by_scope.setdefault(entry["scope"], []).append(entry)
    whitening = next(entry for entry in by_scope["DENTAL"] if entry["label"] == "Teeth whitening")
    assert whitening["source_paths"] == ["exclusions.dental_exclusions[0]", "opd_categories.dental.excluded_procedures[0]"]
    lasik = next(entry for entry in by_scope["VISION"] if entry["label"] == "LASIK")
    assert "opd_categories.vision.excluded_items[0]" in lasik["source_paths"]
    labels = [entry["label"] for entry in policy["exclusions"]]
    assert len(labels) == len(set(labels))
    assert len(by_scope["DENTAL"]) == 6 and len(by_scope["VISION"]) == 3 and len(by_scope["ALL"]) == 10
    assert all(entry["applies_to"] == ["line"] for scope in ("DENTAL", "VISION") for entry in by_scope[scope])
    assert len(_audit(policy, "EXCLUSION_MERGED.")) == 4


def test_parenthetical_exclusions_are_interpreted_not_broadened(policy: dict) -> None:
    implants = next(entry for entry in policy["exclusions"] if entry["label"] == "Implants (Cosmetic)")
    assert "implants" not in {term["text"] for term in implants["terms"]}
    vaccination = next(entry for entry in policy["exclusions"] if entry["label"].startswith("Vaccination"))
    assert vaccination["qualifier"] == "non-medically necessary"


def test_dangling_roster_references_are_repaired_in_normalizer_with_audit(policy: dict) -> None:
    members = {member["member_id"]: member for member in policy["members"]}
    assert members["EMP007"]["dependents"] == []
    assert members["EMP007"]["unresolved_dependents"] == ["DEP004", "DEP005"]
    assert members["EMP001"]["dependents"] == ["DEP001", "DEP002"]
    repaired = {entry["id"] for entry in policy["audit"] if entry["kind"] == "reference_repair"}
    assert repaired == {
        "DANGLING_DEPENDENT.EMP003.DEP003", "DANGLING_DEPENDENT.EMP007.DEP004",
        "DANGLING_DEPENDENT.EMP007.DEP005", "DANGLING_DEPENDENT.EMP010.DEP006",
    }
    assert members["DEP001"]["join_date"] == "2024-04-01"
    assert members["DEP002"]["covered_relationship"] == "CHILDREN"


def test_dental_report_flag_conflict_is_recorded(policy: dict) -> None:
    assert policy["categories"]["DENTAL"]["advisory_documents"] == ["DENTAL_REPORT"]
    assert "DENTAL_REPORT" in policy["document_requirements"]["DENTAL"]["optional"]
    assert _audit(policy, "DENTAL_REPORT_CONFLICT.DENTAL")[0]["kind"] == "conflict_resolution"


def test_every_interpretation_term_is_labelled(policy: dict) -> None:
    groups = [
        *[entry["terms"] for entry in policy["exclusions"]],
        *[entry["terms"] for entry in policy["waiting_periods"]["specific_conditions"]],
        *[rule["terms"] for rule in policy["pre_authorization"]["rules"]],
        *[hospital["terms"] for hospital in policy["network_hospitals"]],
    ]
    provenances = {term["provenance"] for terms in groups for term in terms}
    assert provenances == {"policy_text", "interpretation"}
    for entry in policy["audit"]:
        assert entry["source_paths"] and entry["description"]


def test_obesity_exclusion_is_grounded_in_policy_text(policy: dict) -> None:
    obesity = next(entry for entry in policy["exclusions"] if entry["label"] == "Obesity and weight loss programs")
    assert {"text": "obesity", "provenance": "policy_text"} in obesity["terms"]
    assert {"text": "bariatric", "provenance": "interpretation"} in obesity["terms"]


# --------------------------------------------------------------------------
# Audit round 2
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mutation", ["content", "audit", "fingerprint", "schema"])
def test_tampered_canonical_policy_is_refused(policy: dict, mutation: str) -> None:
    tampered = deepcopy(policy)
    if mutation == "content":
        tampered["categories"]["CONSULTATION"]["per_claim_ceiling_paise"] = 10**9
    elif mutation == "audit":
        tampered["audit"] = tampered["audit"][1:]
    elif mutation == "fingerprint":
        tampered["canonical_sha256"] = "0" * 64
    else:
        tampered["schema_version"] = "plum.canonical_policy.v0"
    with pytest.raises(PolicyConfigurationError):
        ensure_canonical(tampered)


def _mutated(raw: dict, mutate: object) -> dict:
    broken = deepcopy(raw)
    mutate(broken)  # type: ignore[operator]
    return broken


@pytest.mark.parametrize("label, mutate", [
    ("per_claim_limit 0", lambda p: p["coverage"].__setitem__("per_claim_limit", 0)),
    ("per_claim_limit negative", lambda p: p["coverage"].__setitem__("per_claim_limit", -1)),
    ("sub_limit 0", lambda p: p["opd_categories"]["dental"].__setitem__("sub_limit", 0)),
    ("whitespace member name", lambda p: p["members"][0].__setitem__("name", "   ")),
    ("garbage exclusion", lambda p: p["exclusions"]["conditions"].append("(foo)")),
    ("blank exclusion", lambda p: p["exclusions"]["dental_exclusions"].append("  ")),
    ("unbalanced exclusion", lambda p: p["opd_categories"]["vision"]["excluded_items"].append("LASIK (cosmetic")),
    ("case-duplicate category", lambda p: p["opd_categories"].__setitem__("CONSULTATION", deepcopy(p["opd_categories"]["consultation"]))),
    ("renewal typo", lambda p: p["policy_holder"].__setitem__("renewal_status", "ACTVE")),
    ("renewal lowercase", lambda p: p["policy_holder"].__setitem__("renewal_status", "active")),
    ("currency", lambda p: p["submission_rules"].__setitem__("currency", "USD")),
])
def test_schema_rejects_unusable_values(raw: dict, label: str, mutate: object) -> None:
    with pytest.raises(PolicyConfigurationError):
        normalize_policy(_mutated(raw, mutate))


def test_known_non_active_renewal_status_is_accepted_and_blocks_payment(raw: dict) -> None:
    lapsed = normalize_policy(_mutated(raw, lambda p: p["policy_holder"].__setitem__("renewal_status", "LAPSED")))
    result = evaluate_claim(normalize_fixture(next(case for case in load_cases(FIXTURE_PATH) if case["case_id"] == "TC004")), lapsed)
    assert (result["decision"], result["reasons"][0]["code"]) == ("REJECTED", "POLICY_NOT_ACTIVE")


def test_requires_prescription_is_enforced_through_the_document_matrix(raw: dict, policy: dict) -> None:
    for category, item in raw["opd_categories"].items():
        entry = _audit(policy, f"PRESCRIPTION_REQUIREMENT.{category.upper()}")
        assert entry, category
        assert ("PRESCRIPTION" in policy["document_requirements"][category.upper()]["required"]) == item["requires_prescription"]
    # A policy that flags a prescription the matrix forgot still gets it required.
    edited = normalize_policy(_mutated(raw, lambda p: p["opd_categories"]["dental"].__setitem__("requires_prescription", True)))
    assert "PRESCRIPTION" in edited["document_requirements"]["DENTAL"]["required"]
    assert "PRESCRIPTION" not in edited["document_requirements"]["DENTAL"]["optional"]
    assert _audit(edited, "PRESCRIPTION_REQUIREMENT.DENTAL")[0]["kind"] == "conflict_resolution"


def test_every_supplied_field_is_used_validated_or_audited(policy: dict) -> None:
    informational = {entry["id"] for entry in policy["audit"] if entry["kind"] == "informational"}
    assert "INFORMATIONAL_FIELD.policy_holder.employee_count" in informational
    assert _audit(policy, "SUBMISSION_CURRENCY") and _audit(policy, "FRAUD_THRESHOLD_ROLES")
    assert _audit(policy, "CATEGORY_SUB_LIMIT_RULE")[0]["kind"] == "conflict_resolution"
    assert _audit(policy, "BENEFIT_ORDER")


def test_covered_item_abbreviations_are_labelled_interpretation(policy: dict) -> None:
    root_canal = policy["categories"]["DENTAL"]["covered_items"][0]
    provenance = {term["text"]: term["provenance"] for term in root_canal["terms"]}
    assert provenance["root canal"] == "policy_text"
    assert provenance["rct"] == "interpretation"
    assert _audit(policy, "COVERED_ITEM_TERMS.DENTAL.Root Canal Treatment")
    assert policy["categories"]["CONSULTATION"]["service_scope"] == "matching_lines"
    assert policy["categories"]["DENTAL"]["service_scope"] == "all_eligible_lines"
