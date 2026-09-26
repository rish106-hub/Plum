from __future__ import annotations

from scripts.evaluate_documents import evaluate


def test_synthetic_document_intake_benchmark_exercises_real_files(tmp_path) -> None:
    result = evaluate(tmp_path / "documents", tmp_path / "reports")
    assert result["passed"] == result["total"] == 6
    assert (tmp_path / "reports" / "document-evaluation.md").exists()
    multi_page = next(record for record in result["records"] if record["scenario"] == "multi_page_bill")
    assert multi_page["documents"][0]["pages"] == 2
