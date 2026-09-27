from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from claims.agent_pipeline import resolve_document_handoff
from claims.documents import process_uploads
from scripts import evaluate_documents
from scripts.evaluate_documents import (
    ADJUDICATION_ELIGIBLE,
    CORPUS_DIR,
    evaluate,
    evaluate_live,
    load_corpus,
    route,
)
from tools.generate_dirty_documents import LABELS, build_corpus

POLICY = json.loads((Path(__file__).resolve().parents[1] / "data" / "policy_terms.json").read_text(encoding="utf-8"))
REQUIRED_CONDITIONS = {
    "clean_scan", "handwriting", "phone_photo", "skew", "rubber_stamp", "multi_page", "scanned_pdf",
    "blank", "very_low_contrast", "devanagari", "partial_page", "struck_through_amount", "non_medical",
}


@pytest.fixture(autouse=True)
def no_live_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SARVAM_API_KEY", "GEMINI_API_KEY", "GEMINI_EVIDENCE_REVIEW_ENABLED"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def corpus() -> list[dict]:
    return load_corpus()


def test_corpus_labels_load_and_match_committed_files(corpus: list[dict]) -> None:
    assert 10 <= len(corpus) <= 20
    labels = json.loads((CORPUS_DIR / "labels.json").read_text(encoding="utf-8"))
    assert sum(doc["bytes"] for doc in labels["documents"]) < 5 * 1024 * 1024
    committed = {path.name for path in CORPUS_DIR.iterdir()} - {"labels.json"}
    assert committed == {doc["file"] for doc in corpus}
    # Committed labels are the generator's labels plus byte-derived fields only.
    derived = {"pages", "mime_type", "bytes", "sha256"}
    for spec, doc in zip(LABELS, labels["documents"], strict=True):
        assert {k: v for k, v in doc.items() if k not in derived} == {k: v for k, v in spec.items() if k != "pages"}
    conditions = {condition for doc in corpus for condition in doc["conditions"]}
    assert REQUIRED_CONDITIONS <= conditions
    members = {member["name"] for member in POLICY["members"]}
    for doc in corpus:
        assert doc["member_name"] in members
        assert doc["expected_behavior"] in doc["acceptable_behaviors"]
        assert doc["claim_category"] in POLICY["document_requirements"]
        assert set(doc.get("critical_fields", [])) <= set(doc["fields"]) | set(doc.get("absent_fields", []))


def test_corpus_files_are_image_only_with_real_formats(corpus: list[dict]) -> None:
    for doc in corpus:
        if doc["mime_type"] == "application/pdf":
            with pymupdf.open(stream=doc["data"], filetype="pdf") as pdf:
                assert len(pdf) == doc["pages"]
                assert all(not page.get_text().strip() for page in pdf), doc["file"]
        else:
            with Image.open(io.BytesIO(doc["data"])) as image:
                assert Image.MIME.get(image.format or "") == doc["mime_type"]


def test_load_corpus_rejects_a_file_that_drifted_from_its_label(tmp_path: Path) -> None:
    for path in CORPUS_DIR.iterdir():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / "blank_page.jpg").write_bytes((tmp_path / "blank_page.jpg").read_bytes() + b"\0")
    with pytest.raises(ValueError, match="blank_page.jpg"):
        load_corpus(tmp_path)


def test_generator_is_deterministic() -> None:
    first, labels_first = build_corpus()
    second, labels_second = build_corpus()
    assert first == second
    assert labels_first == labels_second


def test_offline_benchmark_is_labelled_routing_and_never_calls_a_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Even with a key configured, the offline suite must not build a live provider.
    monkeypatch.setenv("SARVAM_API_KEY", "not-a-real-key")
    result = evaluate(tmp_path / "documents", tmp_path / "reports")
    assert result["passed"] == result["total"]
    assert result["ocr_evaluated"] is False
    assert result["suites"]["intake_routing"]["total"] == 6
    report = (tmp_path / "reports" / "document-evaluation.md").read_text(encoding="utf-8")
    assert "not an OCR-accuracy report" in report
    multi_page = next(r for r in result["suites"]["intake_routing"]["records"] if r["scenario"] == "multi_page_bill")
    assert multi_page["documents"][0]["pages"] == 2
    for suite in ("intake_routing", "image_fail_closed"):
        assert all(r["metrics"]["provider_calls"] == 0 for r in result["suites"][suite]["records"])
    assert os.environ["SARVAM_API_KEY"] == "not-a-real-key"  # restored after the run


def test_offline_image_inputs_fail_closed(corpus: list[dict]) -> None:
    for doc in corpus:
        result = process_uploads([{"file_name": doc["file"], "data": doc["data"]}], doc["claim_category"], doc["member_name"], POLICY, provider=None)
        codes = {issue["code"] for issue in result["issues"]}
        assert doc["offline_expected_code"] in codes, doc["file"]
        assert route(result["issues"]) != ADJUDICATION_ELIGIBLE
        assert all(d["quality"] != "GOOD" and not d["content"] for d in result["documents"])
        for issue in result["issues"]:
            assert issue["message"].strip(), "every hold must carry an actionable message"


class _FailingOCR:
    def digitise(self, data: bytes, mime_type: str) -> str:
        raise TimeoutError("Sarvam document job exceeded 90 seconds")

    def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict:
        raise AssertionError("extract must not run after a failed digitise")


class _ExtractFails:
    def digitise(self, data: bytes, mime_type: str) -> str:
        return "HOSPITAL BILL / RECEIPT\nPatient Name: Rajesh Kumar\nDate: 01-Nov-2024\nTotal Amount: 1500.00\n"

    def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict:
        raise RuntimeError("Sarvam job ended with failed")


@pytest.mark.parametrize("provider", [_FailingOCR(), _ExtractFails()], ids=["digitise", "extract"])
def test_mandatory_ocr_failure_never_reaches_a_financial_decision(corpus: list[dict], provider: object) -> None:
    for doc in corpus:
        if doc["offline_expected_code"] != "EXTRACTION_UNAVAILABLE":
            continue
        # The member matches the stubbed OCR name so that only the provider failure can hold the claim.
        result = process_uploads([{"file_name": doc["file"], "data": doc["data"]}], "DENTAL", "Rajesh Kumar", POLICY, provider=provider)  # type: ignore[arg-type]
        assert "EXTRACTION_UNAVAILABLE" in {issue["code"] for issue in result["issues"]}, doc["file"]
        assert route(result["issues"]) == "MANUAL_REVIEW"
        assert result["metrics"]["provider_failures"] >= 1


def test_optional_gemini_cannot_clear_a_mandatory_ocr_failure(corpus: list[dict]) -> None:
    photo = next(doc for doc in corpus if doc["id"] == "bill_phone_photo_skewed")
    failed = process_uploads([{"file_name": photo["file"], "data": photo["data"]}], "DENTAL", "Rajesh Kumar", POLICY, provider=_FailingOCR())
    handoff = resolve_document_handoff(
        failed, {"UPLOAD-1": {"data": photo["data"], "mime_type": photo["mime_type"]}}, failed["ocr_text_by_file_id"],
        "DENTAL", "Rajesh Kumar", ["Rajesh Kumar"], POLICY, evaluate_documents._fabricated_gemini,
    )
    assert handoff["status"] == "ABSTAINED"
    assert route(handoff["issues"]) == "MANUAL_REVIEW"
    assert not any(doc["content"] for doc in handoff["documents"])


def test_failure_injection_distinguishes_mandatory_from_optional(tmp_path: Path) -> None:
    suite = evaluate(tmp_path / "documents", tmp_path / "reports")["suites"]["provider_failure_injection"]
    assert suite["passed"] == suite["total"]
    records = {record["scenario"]: record for record in suite["records"]}
    assert all(r["route"] != ADJUDICATION_ELIGIBLE for r in records.values() if r["failure_class"] == "mandatory")
    # An optional provider failure leaves a complete, locally verified claim eligible.
    assert records["optional_gemini_exception_on_complete_claim"]["route"] == ADJUDICATION_ELIGIBLE
    assert records["optional_gemini_fabricated_candidate_after_mandatory_failure"]["gemini_status"] == "ABSTAINED"


def test_live_mode_without_keys_reports_not_run_and_no_metrics(tmp_path: Path) -> None:
    result = evaluate_live(tmp_path, gemini=True)
    assert result["status"] == "NOT_RUN"
    assert result["reason"] == "NOT_RUN (missing SARVAM_API_KEY)"
    assert result["metrics"] is None
    assert len(result["records"]) == len(LABELS)
    assert all(record["status"] == "NOT_RUN (missing SARVAM_API_KEY)" for record in result["records"])
    assert all(set(record) == {"scenario", "file", "conditions", "status"} for record in result["records"])
    written = json.loads((tmp_path / "document-ocr-evaluation.json").read_text(encoding="utf-8"))
    assert written == result
    markdown = (tmp_path / "document-ocr-evaluation.md").read_text(encoding="utf-8")
    assert "no accuracy" in markdown and "%" not in markdown


def test_live_cli_without_keys_exits_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["evaluate_documents", "--providers", "live", "--report-dir", str(tmp_path)])
    assert evaluate_documents.main() == 2
    assert "NOT_RUN (missing SARVAM_API_KEY)" in capsys.readouterr().out


class _MisreadingOCR:
    """A reader that confidently returns the same wrong bill for every page."""

    def digitise(self, data: bytes, mime_type: str) -> str:
        return (
            "CITY MEDICAL CENTRE\nHOSPITAL BILL / RECEIPT\nPatient Name: Rajesh Kumar\nDate: 01-Nov-2024\n"
            "Consultation Fee 1200.00\nTotal Amount: 1200.00\n"
        )

    def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict:
        return {"document_type": document_type, "quality": "GOOD", "fields": {}}


def test_live_scoring_flags_unsafe_confident_errors_with_an_injected_provider(tmp_path: Path) -> None:
    result = evaluate_live(
        tmp_path,
        provider_factory=_MisreadingOCR,
        gemini=True,
        digitise_cost_inr_per_page=0.5,
        extract_cost_inr_per_page=1.0,
    )
    assert result["status"] == "RUN"
    assert result["provider"] == "injected_test_provider"
    assert result["gemini"] == "NOT_RUN (missing GEMINI_API_KEY)"
    records = {record["scenario"]: record for record in result["records"]}
    photo = records["bill_phone_photo_skewed"]
    assert photo["observed_behavior"] == "extract"
    assert photo["fields"]["total"]["outcome"] == "wrong"
    assert photo["unsafe_confident_error"] is True
    cropped = records["bill_cropped_partial"]
    assert cropped["fields"]["total"]["outcome"] == "hallucinated"
    assert records["blank_page"]["observed_behavior"] == "request_reupload"
    assert records["blank_page"]["observed_type"] is None
    assert records["rx_clean_scan"]["classification_correct"] is False
    metrics = result["metrics"]
    assert metrics["unsafe_confident_errors"] >= 2
    assert metrics["sarvam_digitise_calls"] == len(LABELS) - 1  # the blank page is rejected locally
    assert metrics["latency_seconds_p50"] is not None
    assert metrics["wrong_total_rate"] is not None
    assert metrics["line_item_reconciliation_rate"] is not None
    assert records["bill_struck_correction"]["alteration_detected"] is True
    assert metrics["missed_alteration_count"] == 0
    assert metrics["configured_cost_inr"] is not None
    assert metrics["cost_rate_source"] == "CLI supplied per-page rates"
    assert all(record["provider_call_log"] or record["scenario"] == "blank_page" for record in result["records"])
