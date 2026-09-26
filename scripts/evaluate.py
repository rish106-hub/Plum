"""Run every supplied fixture through the normalised policy pipeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from claims.agent_pipeline import adjudicate_handoff
from claims.core import evaluate_claim
from claims.fixtures import load_cases, load_policy, normalize_fixture

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data" / "policy_terms.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "test_cases.json"
REPORT_DIR = ROOT / "docs" / "reports"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _raise_optional_enrichment_failure(_: dict) -> None:
    raise RuntimeError("synthetic optional component failure")


def _matches(expected: dict, result: dict) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if result["decision"] != expected.get("decision"):
        issues.append(f"decision {result['decision']!r} != {expected.get('decision')!r}")
    if "approved_amount" in expected and result["approved_amount"] != expected["approved_amount"]:
        issues.append(
            f"approved amount {result['approved_amount']!r} != {expected['approved_amount']!r}"
        )
    wanted_codes = set(expected.get("rejection_reasons", []))
    actual_codes = {reason["code"] for reason in result["reasons"]}
    if not wanted_codes.issubset(actual_codes):
        issues.append(f"missing reason code(s): {sorted(wanted_codes - actual_codes)}")
    confidence_rule = expected.get("confidence_score")
    if isinstance(confidence_rule, str) and confidence_rule.startswith("above "):
        threshold = float(confidence_rule.removeprefix("above "))
        if result["confidence_score"] <= threshold:
            issues.append(f"confidence {result['confidence_score']} is not above {threshold}")
    return not issues, issues


def _behavior_checks(case: dict, raw_policy: dict, result: dict, normal_confidence: float) -> list[str]:
    """Check the concrete behaviors behind each case's prose requirements.

    ``raw_policy`` is the supplied policy JSON as written, so stated limits are
    checked against the source document rather than the engine's own config.
    """
    case_id = case["case_id"]
    failures: list[str] = []
    corrections = result.get("correction_requests") or []
    correction_text = " ".join(str(item.get("message", "")) for item in corrections if isinstance(item, dict)).casefold()
    reason_text = " ".join(str(item.get("message", "")) for item in result.get("reasons", []) if isinstance(item, dict)).casefold()
    reason_codes = {item.get("code") for item in result.get("reasons", []) if isinstance(item, dict)}
    trace = result.get("trace") or []
    ledger = result.get("ledger") or []

    def require(condition: bool, description: str) -> None:
        if not condition:
            failures.append(description)

    if case_id in {"TC001", "TC002", "TC003"}:
        require(result.get("decision") is None and not any(step.get("stage") == "policy" for step in trace), "policy evaluation was not stopped")
    if case_id == "TC001":
        require("prescription" in correction_text and "hospital_bill" in correction_text, "correction does not name uploaded and required document types")
    elif case_id == "TC002":
        require("pharmacy_bill" in correction_text and "re-upload" in correction_text, "unreadable pharmacy bill correction is not actionable")
    elif case_id == "TC003":
        require("rajesh kumar" in correction_text and "arjun mehta" in correction_text, "patient mismatch does not name both patients")
    elif case_id == "TC005":
        waiting: dict = next((step for step in trace if step.get("rule_id") == "waiting_period"), {})
        require(waiting.get("status") == "FAIL" and str(waiting.get("evidence", {}).get("eligible_from", "")) in reason_text, "eligibility date missing from waiting-period reason")
    elif case_id == "TC006":
        lines = [item for item in ledger if item.get("kind") == "line_item"]
        statuses = {item.get("status") for item in lines}
        require({"ELIGIBLE", "EXCLUDED"} <= statuses and "EXCLUDED_PROCEDURE" in reason_codes, "line-level dental inclusion or exclusion missing")
        require(all(item.get("reason") for item in lines if item.get("status") != "ELIGIBLE"), "a rejected line item has no line-level reason")
    elif case_id == "TC007":
        require("PRE_AUTH_MISSING" in reason_codes and "approval record" in reason_text, "pre-authorization reason or resubmission action missing")
        require("resubmit" in reason_text, "resubmission instruction missing")
    elif case_id == "TC008":
        claimed = str(case["input"]["claimed_amount"])
        limit = str(raw_policy["coverage"]["per_claim_limit"])
        require(claimed in reason_text and limit in reason_text, "claimed amount and per-claim limit are not both stated")
    elif case_id == "TC009":
        signal: dict = next((step for step in trace if step.get("rule_id") == "same_day_claims"), {})
        require(result.get("decision") == "MANUAL_REVIEW" and signal.get("status") == "FLAG" and signal.get("evidence", {}).get("same_day_claim_count_including_current", 0) > signal.get("evidence", {}).get("limit", 999999), "same-day review signal missing")
    elif case_id == "TC010":
        pricing: dict = next((step for step in trace if step.get("rule_id") == "payable_amount"), {})
        adjustments = {item.get("description") for item in ledger if item.get("kind") == "adjustment"}
        require(pricing.get("evidence", {}).get("network_discount_paise", 0) > 0 and {"Network discount", "Member co-pay"} <= adjustments, "network discount and co-pay breakdown missing")
        order = [item.get("description") for item in ledger if item.get("kind") == "adjustment"]
        discount: dict = next((item for item in ledger if item.get("description") == "Network discount"), {})
        copay: dict = next((item for item in ledger if item.get("description") == "Member co-pay"), {})
        require(
            "Network discount" in order and "Member co-pay" in order
            and order.index("Network discount") < order.index("Member co-pay")
            and copay.get("basis_paise") == discount.get("basis_paise", 0) + discount.get("amount_paise", 0),
            "co-pay was not computed on the post-discount amount",
        )
    elif case_id == "TC011":
        degraded = any(step.get("status") == "SKIPPED_COMPONENT_FAILURE" for step in trace)
        require(degraded and result.get("confidence_score", 1) < normal_confidence and "manual review" in reason_text, "graceful degradation is not fully visible")
    return failures


def main() -> int:
    policy = load_policy(POLICY_PATH)
    raw_policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    cases = load_cases(FIXTURE_PATH)
    fingerprints = {
        "policy_sha256": _sha256(POLICY_PATH),
        "policy_canonical_sha256": policy["canonical_sha256"],
        "fixture_sha256": _sha256(FIXTURE_PATH),
    }
    if fingerprints["policy_sha256"] != policy["source"]["sha256"]:
        raise RuntimeError("policy file changed while it was being evaluated")
    records = []
    for case in cases:
        options = {}
        if case.get("input", {}).get("simulate_component_failure"):
            options["optional_risk_enricher"] = _raise_optional_enrichment_failure
        payload = normalize_fixture(case)
        result = adjudicate_handoff(
            payload, policy, lambda claim, terms: evaluate_claim(claim, terms, **options)
        )
        matched, issues = _matches(case["expected"], result)
        records.append(
            {
                "case_id": case["case_id"],
                "case_name": case["case_name"],
                "expected": case["expected"],
                "matched": matched,
                "mismatches": issues,
                "output": result,
            }
        )

    normal_confidence = next(
        record["output"]["confidence_score"] for record in records if record["case_id"] == "TC004"
    )
    cases_by_id = {case["case_id"]: case for case in cases}
    for record in records:
        behavior_failures = _behavior_checks(cases_by_id[record["case_id"]], raw_policy, record["output"], normal_confidence)
        record["behavior_checks"] = {"matched": not behavior_failures, "mismatches": behavior_failures}
        record["mismatches"].extend(behavior_failures)
        record["matched"] = not record["mismatches"]

    output_path = REPORT_DIR / "evaluation-data.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"fingerprints": fingerprints, "records": records}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    passed = sum(record["matched"] for record in records)
    lines = [
        "# Evaluation report",
        "",
        f"Policy: `{policy['policy_id']}`. Cases: {len(records)}. Expected decision, amount, reason, confidence, and explicitly checked behavior matched: **{passed}/{len(records)}**.",
        "",
        f"- Policy file sha256: `{fingerprints['policy_sha256']}`",
        f"- Canonical policy sha256: `{fingerprints['policy_canonical_sha256']}`",
        f"- Fixture file sha256: `{fingerprints['fixture_sha256']}`",
        "",
        "These are structured fixtures with no actual image or PDF bytes. A pass establishes policy-pipeline behavior, not OCR accuracy. The complete machine-readable outputs are also in [evaluation-data.json](evaluation-data.json).",
        "",
        "| Case | Expected | Produced | Amount | Match |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for record in records:
        result = record["output"]
        lines.append(
            f"| {record['case_id']} | {record['expected'].get('decision')} | "
            f"{result['decision']} | {result['approved_amount']} | "
            f"{'Yes' if record['matched'] else 'No'} |"
        )
    lines += [
        "",
        "## Interpretation and limits",
        "",
        "- The engine reads only the canonical policy produced by `claims.policy` from the unmodified policy file; every interpretation, merge, and conflict resolution is listed in the canonical config's `audit` array and referenced from the trace.",
        "- Per-claim ceiling: max(global `per_claim_limit`, category `sub_limit`), tested on the eligible amount after excluded lines are removed. A matched pre-authorization rule governs amounts above the ceiling instead.",
        "- Aggregate limits (annual OPD, sum insured, family floater, annual sessions) apply when utilisation accompanies the claim; otherwise they are `NOT_EVALUATED`, disclosed as advisory reasons on payable outcomes, and lower confidence.",
        "- Fixture payloads without a submission timestamp retain `NOT_EVALUATED`; uploaded claims use their persisted creation date for the 30-day deadline check.",
        "- The confidence values are a heuristic evidence-completeness rubric, not calibrated probabilities. Deductions apply only for unknowns material to the outcome reached. TC011's simulated optional failure lowers confidence and is recorded in the trace.",
        "- Provider accuracy, handwriting, multilingual extraction, and image quality require a separately labelled image/PDF set. The fixture results make no claim about those capabilities.",
        "",
        "## Complete outputs and traces",
        "",
    ]
    for record in records:
        lines += [
            f"### {record['case_id']}: {record['case_name']}",
            "",
            f"Match: **{'Yes' if record['matched'] else 'No'}**. "
            + ("No discrepancies." if record["matched"] else "; ".join(record["mismatches"])),
            f"Explicit behavior checks: **{'Passed' if record['behavior_checks']['matched'] else 'Failed'}**."
            + ("" if record["behavior_checks"]["matched"] else " " + "; ".join(record["behavior_checks"]["mismatches"])),
            "",
            "```json",
            json.dumps(record["output"], indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    (REPORT_DIR / "evaluation.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"policy sha256 {fingerprints['policy_sha256']}")
    print(f"canonical policy sha256 {fingerprints['policy_canonical_sha256']}")
    print(f"fixture sha256 {fingerprints['fixture_sha256']}")
    print(f"{passed}/{len(records)} fixture expectations matched")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
