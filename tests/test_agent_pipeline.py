"""Contract checks at specialist handoffs."""

from __future__ import annotations

from claims.agent_pipeline import (
    adjudicate_handoff,
    document_evidence_trace,
    resolve_document_handoff,
)


def test_document_trace_records_grounded_facts_without_full_ocr() -> None:
    trace = document_evidence_trace([{
        "file_id": "UPLOAD-1",
        "actual_type": "HOSPITAL_BILL",
        "content": {"patient_name": "Rajesh Kumar", "total": 1500, "raw_text": "private full OCR"},
        "evidence": [{"field": "total", "source": "pdf_text", "snippet": "Total: 1500"}],
    }])

    assert trace["evidence"][0]["fields"] == {"patient_name": "Rajesh Kumar", "total": 1500}
    assert trace["evidence"][0]["sources"][0]["snippet"] == "Total: 1500"
    assert "private full OCR" not in str(trace)


def test_invalid_evidence_envelope_cannot_replace_local_documents() -> None:
    inspection = {
        "documents": [{"file_id": "UPLOAD-1", "actual_type": "HOSPITAL_BILL", "content": {"total": 1500}}],
        "issues": [{"code": "MISSING_DOCUMENT", "message": "Upload a prescription."}],
        "metrics": {"pages": 1},
    }

    def resolver(*_args: object, **_kwargs: object) -> dict:
        return {"status": "CANDIDATES_VALIDATED", "candidates": [{"file_id": "UNRELATED", "fields": {"total_paise": 1}, "evidence": []}], "metrics": {}, "trace": {}}

    handoff = resolve_document_handoff(inspection, {}, {}, "CONSULTATION", "Rajesh Kumar", ["Rajesh Kumar"], {}, resolver)

    assert handoff["documents"] == inspection["documents"]
    assert handoff["issues"] == inspection["issues"]
    assert handoff["status"] == "ABSTAINED"
    assert handoff["trace"][0]["reason"] == "invalid_handoff_envelope"


def test_resolver_exception_keeps_original_document_gate() -> None:
    inspection = {"documents": [], "issues": [{"code": "MISSING_DOCUMENT"}], "metrics": {}}

    def resolver(*_args: object, **_kwargs: object) -> dict:
        raise RuntimeError("private source document text")

    handoff = resolve_document_handoff(inspection, {}, {}, "CONSULTATION", "Rajesh Kumar", [], {}, resolver)

    assert handoff["issues"] == inspection["issues"]
    assert handoff["trace"][0]["reason"] == "resolver_failure"
    assert "private source document text" not in str(handoff)


def test_handoff_rejects_old_schema_and_foreign_source_ids() -> None:
    inspection = {"documents": [{"file_id": "UPLOAD-1"}], "issues": [], "metrics": {}}

    def resolver(*_args: object, **_kwargs: object) -> dict:
        return {
            "schema_version": 0,
            "producer": "gemini_evidence",
            "task": "resolve_document_facts",
            "source_file_ids": ["OTHER-CLAIM"],
            "status": "NOT_NEEDED",
            "candidates": [],
            "metrics": {"calls": 0},
            "trace": {"stage": "gemini_evidence", "status": "NOT_NEEDED"},
        }

    handoff = resolve_document_handoff(inspection, {}, {}, "CONSULTATION", "Rajesh Kumar", [], {}, resolver)

    assert handoff["status"] == "ABSTAINED"
    assert handoff["trace"][0]["reason"] == "invalid_handoff_envelope"


def test_handoff_accepts_versioned_no_call_result() -> None:
    inspection = {"documents": [{"file_id": "UPLOAD-1"}], "issues": [], "metrics": {}}

    def resolver(*_args: object, **_kwargs: object) -> dict:
        return {
            "schema_version": 1,
            "producer": "gemini_evidence",
            "task": "resolve_document_facts",
            "source_file_ids": ["UPLOAD-1"],
            "status": "NOT_NEEDED",
            "candidates": [],
            "metrics": {"calls": 0},
            "trace": {"stage": "gemini_evidence", "status": "NOT_NEEDED"},
        }

    handoff = resolve_document_handoff(inspection, {}, {}, "CONSULTATION", "Rajesh Kumar", [], {}, resolver)

    assert handoff["status"] == "NOT_NEEDED"
    assert handoff["documents"] == inspection["documents"]


def test_handoff_rejects_amount_with_unrelated_source_quote() -> None:
    inspection = {"documents": [{"file_id": "UPLOAD-1", "actual_type": "HOSPITAL_BILL", "pages": 1, "content": {}}], "issues": [{"code": "AMOUNT_UNVERIFIED"}], "metrics": {}}

    def resolver(*_args: object, **_kwargs: object) -> dict:
        return {
            "schema_version": 1, "producer": "gemini_evidence", "task": "resolve_document_facts",
            "source_file_ids": ["UPLOAD-1"], "status": "CANDIDATES_VALIDATED",
            "candidates": [{"file_id": "UPLOAD-1", "fields": {"total_paise": 900000}, "evidence": [{"field": "total_paise", "page": 1, "quote": "Patient: Rajesh Kumar"}]}],
            "metrics": {"calls": 1}, "trace": {"stage": "gemini_evidence", "status": "CANDIDATES_VALIDATED"},
        }

    handoff = resolve_document_handoff(inspection, {}, {"UPLOAD-1": ["Patient: Rajesh Kumar\nTotal: ₹9000"]}, "CONSULTATION", "Rajesh Kumar", ["Rajesh Kumar"], {}, resolver)

    assert handoff["status"] == "ABSTAINED"
    assert handoff["issues"] == inspection["issues"]


def test_handoff_discards_untrusted_trace_and_metrics_fields() -> None:
    inspection = {"documents": [{"file_id": "UPLOAD-1"}], "issues": [], "metrics": {}}

    def resolver(*_args: object, **_kwargs: object) -> dict:
        return {
            "schema_version": 1, "producer": "gemini_evidence", "task": "resolve_document_facts",
            "source_file_ids": ["UPLOAD-1"], "status": "ABSTAINED", "candidates": [],
            "metrics": {"calls": 0, "raw_ocr": "private patient text"},
            "trace": {"stage": "gemini_evidence", "status": "ABSTAINED", "reason": "unknown-secret", "raw_ocr": "private patient text"},
        }

    handoff = resolve_document_handoff(inspection, {}, {}, "CONSULTATION", "Rajesh Kumar", [], {}, resolver)

    assert handoff["status"] == "ABSTAINED"
    assert "private patient text" not in str(handoff)
    assert handoff["trace"][0]["reason"] == "provider_response_unavailable"


def test_malformed_nested_evidence_abstains_before_application() -> None:
    inspection = {"documents": [{"file_id": "UPLOAD-1", "actual_type": "HOSPITAL_BILL", "pages": 1, "content": {}}], "issues": [{"code": "MATERIAL_FIELD_UNVERIFIED"}], "metrics": {}}

    def resolver(*_args: object, **_kwargs: object) -> dict:
        return {"status": "CANDIDATES_VALIDATED", "candidates": [{"file_id": "UPLOAD-1", "fields": {"total_paise": 150000}, "evidence": ["invalid proof"]}], "metrics": {}, "trace": {}}

    handoff = resolve_document_handoff(inspection, {}, {"UPLOAD-1": ["Total 1500"]}, "CONSULTATION", "Rajesh Kumar", ["Rajesh Kumar"], {}, resolver)

    assert handoff["status"] == "ABSTAINED"
    assert handoff["issues"] == inspection["issues"]


def test_inconsistent_amount_fails_closed_without_payment() -> None:
    def evaluator(_payload: dict, _policy: dict) -> dict:
        return {"decision": "APPROVED", "approved_amount": 1300, "approved_amount_paise": 135000, "reasons": [], "trace": [], "ledger": [], "confidence_score": 0.99}

    result = adjudicate_handoff({"claimed_amount": 1500}, {}, evaluator)

    assert result["decision"] == "MANUAL_REVIEW"
    assert result["approved_amount_paise"] == 0
    assert result["reasons"][0]["code"] == "INVALID_DECISION_HANDOFF"


def test_decision_state_disagreement_fails_closed() -> None:
    def evaluator(_payload: dict, _policy: dict) -> dict:
        return {"state": "MANUAL_REVIEW", "decision": "APPROVED", "approved_amount": 1350, "approved_amount_paise": 135000, "reasons": [], "trace": [], "ledger": [], "confidence_score": 0.96}

    result = adjudicate_handoff({"claimed_amount": 1500}, {}, evaluator)

    assert result["decision"] == "MANUAL_REVIEW"
    assert result["trace"][0]["reason"] == "invalid_decision_handoff"


def test_extra_fractional_rupee_cannot_pass_amount_contract() -> None:
    def evaluator(_payload: dict, _policy: dict) -> dict:
        return {"state": "DECIDED", "decision": "APPROVED", "approved_amount": "10.009", "approved_amount_paise": 1000, "reasons": [], "trace": [], "ledger": [], "confidence_score": 0.96}

    result = adjudicate_handoff({"claimed_amount": 11}, {}, evaluator)

    assert result["decision"] == "MANUAL_REVIEW"


def test_policy_agent_exception_is_redacted_and_routes_to_review() -> None:
    def evaluator(_payload: dict, _policy: dict) -> dict:
        raise RuntimeError("private source document text")

    result = adjudicate_handoff({"claimed_amount": 1500}, {}, evaluator)

    assert result["decision"] == "MANUAL_REVIEW"
    assert result["trace"][0]["error_type"] == "RuntimeError"
    assert "private source document text" not in str(result)


def test_approved_result_requires_explanation_and_excludes_extra_fields() -> None:
    def evaluator(_payload: dict, _policy: dict) -> dict:
        return {
            "state": "DECIDED", "decision": "APPROVED", "approved_amount": 10,
            "approved_amount_paise": 1000, "reasons": [], "correction_requests": [],
            "confidence_score": True, "trace": [], "ledger": [],
            "raw_ocr": "private patient text",
        }

    result = adjudicate_handoff({"claimed_amount": 10}, {}, evaluator)

    assert result["decision"] == "MANUAL_REVIEW"
    assert "private patient text" not in str(result)


def test_correction_requires_actionable_request() -> None:
    def evaluator(_payload: dict, _policy: dict) -> dict:
        return {
            "state": "NEEDS_CORRECTION", "decision": None, "approved_amount": None,
            "approved_amount_paise": None, "reasons": [], "correction_requests": [],
            "confidence_score": None, "trace": [{"stage": "document_gate", "status": "BLOCKED"}], "ledger": [],
        }

    result = adjudicate_handoff({"claimed_amount": 10}, {}, evaluator)

    assert result["decision"] == "MANUAL_REVIEW"


def test_correction_rejects_scalar_request_entries() -> None:
    def evaluator(_payload: dict, _policy: dict) -> dict:
        return {
            "state": "NEEDS_CORRECTION", "decision": None, "approved_amount": None,
            "approved_amount_paise": None, "reasons": [], "correction_requests": [1],
            "confidence_score": None, "trace": [{"stage": "document_gate", "rule_id": "required_document", "status": "BLOCKED"}], "ledger": [],
        }

    result = adjudicate_handoff({"claimed_amount": 10}, {}, evaluator)

    assert result["decision"] == "MANUAL_REVIEW"


def test_payable_decision_requires_reconciled_ledger() -> None:
    def evaluator(_payload: dict, _policy: dict) -> dict:
        return {
            "state": "DECIDED", "decision": "APPROVED", "approved_amount": 10,
            "approved_amount_paise": 1000,
            "reasons": [{"code": "COVERED", "message": "Covered."}], "correction_requests": [],
            "confidence_score": 0.9,
            "trace": [{"stage": "decision", "rule_id": "outcome", "status": "APPROVED"}],
            "ledger": [{"description": "Unsupported amount", "amount_paise": 999999}],
        }

    result = adjudicate_handoff({"claimed_amount": 10}, {}, evaluator)

    assert result["decision"] == "MANUAL_REVIEW"
