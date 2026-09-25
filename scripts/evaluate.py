"""Run every supplied fixture through the normalised policy pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from claims.agent_pipeline import adjudicate_handoff
from claims.core import evaluate_claim
from claims.fixtures import load_cases, load_policy, normalize_fixture

ROOT = Path(__file__).resolve().parents[1]


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


def _behavior_checks(case: dict, policy: dict, result: dict, normal_confidence: float) -> list[str]:
    """Check the concrete behaviors behind each case's prose requirements."""
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
        statuses = {item.get("status") for item in ledger if item.get("kind") == "line_item"}
        require({"ELIGIBLE", "EXCLUDED"} <= statuses and "EXCLUDED_PROCEDURE" in reason_codes, "line-level dental inclusion or exclusion missing")
    elif case_id == "TC007":
        require("PRE_AUTH_MISSING" in reason_codes and "approval record" in reason_text, "pre-authorization reason or resubmission action missing")
    elif case_id == "TC008":
        claimed = str(case["input"]["claimed_amount"])
        limit = str(policy["coverage"]["per_claim_limit"])
        require(claimed in reason_text and limit in reason_text, "claimed amount and per-claim limit are not both stated")
    elif case_id == "TC009":
        signal: dict = next((step for step in trace if step.get("rule_id") == "same_day_claims"), {})
        require(result.get("decision") == "MANUAL_REVIEW" and signal.get("status") == "FLAG" and signal.get("evidence", {}).get("same_day_claim_count_including_current", 0) > signal.get("evidence", {}).get("limit", 999999), "same-day review signal missing")
    elif case_id == "TC010":
        pricing: dict = next((step for step in trace if step.get("rule_id") == "payable_amount"), {})
        adjustments = {item.get("description") for item in ledger if item.get("kind") == "adjustment"}
        require(pricing.get("evidence", {}).get("network_discount_paise", 0) > 0 and {"Network discount", "Member co-pay"} <= adjustments, "network discount and co-pay breakdown missing")
    elif case_id == "TC011":
        degraded = any(step.get("status") == "SKIPPED_COMPONENT_FAILURE" for step in trace)
        require(degraded and result.get("confidence_score", 1) < normal_confidence and "manual review" in reason_text, "graceful degradation is not fully visible")
    return failures


def main() -> int:
    policy = load_policy(ROOT / "data" / "policy_terms.json")
    cases = load_cases(ROOT / "tests" / "fixtures" / "test_cases.json")
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
        behavior_failures = _behavior_checks(cases_by_id[record["case_id"]], policy, record["output"], normal_confidence)
        record["behavior_checks"] = {"matched": not behavior_failures, "mismatches": behavior_failures}
        record["mismatches"].extend(behavior_failures)
        record["matched"] = not record["mismatches"]

    output_path = ROOT / "docs" / "reports" / "evaluation-data.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    passed = sum(record["matched"] for record in records)
    lines = [
        "# Evaluation report",
        "",
        f"Policy: `{policy['policy_id']}`. Cases: {len(records)}. Expected decision, amount, reason, confidence, and explicitly checked behavior matched: **{passed}/{len(records)}**.",
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
        "- TC006 uses the dental sub-limit over the general claim cap and accepts a bill without a dental report, solely as a documented fixture compatibility interpretation. A real case with this policy conflict routes to review.",
        "- TC010 treats the consultation sub-limit as applying to the consultation-fee line and applies network discount before co-pay.",
        "- The fixtures contain no submission timestamp. The 30-day deadline is `NOT_EVALUATED`, rather than compared with today's date.",
        "- The confidence values are evidence-quality scores, not calibrated probabilities. TC011's simulated optional failure lowers confidence and is recorded in the trace.",
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
    (ROOT / "docs" / "reports" / "evaluation.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{passed}/{len(records)} fixture expectations matched")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
