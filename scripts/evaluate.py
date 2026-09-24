"""Run every supplied fixture through the normalised policy pipeline."""

from __future__ import annotations

import json
from pathlib import Path

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


def main() -> int:
    policy = load_policy(ROOT / "policy_terms.json")
    cases = load_cases(ROOT / "test_cases.json")
    records = []
    for case in cases:
        options = {}
        if case.get("input", {}).get("simulate_component_failure"):
            options["optional_risk_enricher"] = _raise_optional_enrichment_failure
        result = evaluate_claim(normalize_fixture(case), policy, **options)
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

    output_path = ROOT / "docs" / "eval_outputs.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    passed = sum(record["matched"] for record in records)
    lines = [
        "# Evaluation report",
        "",
        f"Policy: `{policy['policy_id']}`. Cases: {len(records)}. Expected decision/amount/reason/confidence checks matched: **{passed}/{len(records)}**.",
        "",
        "These are structured fixtures with no actual image or PDF bytes. A pass establishes policy-pipeline behavior, not OCR accuracy. The complete machine-readable outputs are also in [eval_outputs.json](docs/eval_outputs.json).",
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
            "",
            "```json",
            json.dumps(record["output"], indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    (ROOT / "EVAL_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{passed}/{len(records)} fixture expectations matched")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
