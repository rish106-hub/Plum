from tools.live_ocr_outcome_benchmark import (
    SCENARIOS,
    _evidence_checks,
    _matches_expected,
    _summary_markdown,
)


def test_correction_path_uses_early_document_metrics_and_abstains() -> None:
    scenario = next(item for item in SCENARIOS if item.name == "doubtful")
    claim = {
        "state": "DOCUMENT_CORRECTION_REQUIRED",
        "result": {
            "decision": None,
            "metrics": {"provider_calls": 3, "provider_failures": 0},
            "trace": [],
        },
    }

    checks = _evidence_checks(scenario, claim)

    assert _matches_expected(scenario, claim)
    assert all(check["passed"] for check in checks)


def test_summary_keeps_outcome_regression_separate_from_accuracy_claims() -> None:
    summary = {
        "status": "PASS",
        "passed": 1,
        "total": 1,
        "results": [
            {
                "scenario": "approved",
                "expected": "APPROVED",
                "state": "DECIDED",
                "decision": "APPROVED",
                "reason_codes": ["COVERED"],
                "document_metrics": {"provider_calls": 4, "provider_failures": 0},
                "browser_errors": [],
                "elapsed_seconds": 1.0,
                "matched": True,
            }
        ],
    }

    report = _summary_markdown(summary)

    assert "bounded end-to-end outcome regression" in report
    assert "not a production OCR-accuracy" in report
    assert "4 calls, 0 failures" in report
    assert "0 page errors" in report
