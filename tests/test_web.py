"""Workflow checks at the HTTP boundary, including durable correction results."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import fitz
import pytest
from fastapi.testclient import TestClient

from claims import web
from claims.ai_review import _DOC_REQUIRED
from claims.ai_review import resolve_evidence as real_resolve_evidence

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


@pytest.fixture(autouse=True)
def _real_clock_by_default(monkeypatch) -> None:
    # A developer's shell or .env must not leak a demo clock into these tests.
    monkeypatch.delenv("PLUM_DEMO_CLOCK", raising=False)
    monkeypatch.delenv("PLUM_ENV", raising=False)


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PLUM_DATA_DIR", str(tmp_path))
    return TestClient(web.app)


def test_operations_worklist_page_is_available(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        response = client.get("/ops")
    assert response.status_code == 200
    assert "Review" in response.text


def test_submission_uses_real_clock_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web, "evaluate_claim", lambda *_: _mock_decision())
    _wire_inspection(monkeypatch, complete=True)
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    with _client(tmp_path, monkeypatch) as client:
        assert "demo-clock-banner" not in client.get("/").text
        saved = client.get(f"/api/claims/{_submit(client, _text_pdf('bill')).json()['id']}").json()
    assert saved["request"]["submission_date"] == today
    assert "submission_clock" not in saved["request"]
    assert not any(step.get("stage") == "clock" for step in saved["result"]["trace"])
    assert all(event["stage"] != "DEMO_CLOCK" for event in saved["events"])


@pytest.mark.parametrize("value", ["2024-11-05", "2024-11-05T10:30:00+05:30"])
def test_demo_clock_is_applied_and_traced_in_development(tmp_path, monkeypatch, value) -> None:
    monkeypatch.setenv("PLUM_ENV", "development")
    monkeypatch.setenv("PLUM_DEMO_CLOCK", value)
    monkeypatch.setattr(web, "evaluate_claim", lambda *_: _mock_decision())
    _wire_inspection(monkeypatch, complete=True)
    with _client(tmp_path, monkeypatch) as client:
        home = client.get("/").text
        saved = client.get(f"/api/claims/{_submit(client, _text_pdf('bill')).json()['id']}").json()
    assert 'id="demo-clock-banner"' in home and "PLUM_DEMO_CLOCK" in home
    assert saved["request"]["submission_date"] == "2024-11-05"
    assert saved["request"]["submission_clock"]["source"] == "PLUM_DEMO_CLOCK"
    clock_step = saved["result"]["trace"][0]
    assert clock_step["stage"] == "clock" and clock_step["rule_id"] == "demo_clock"
    assert clock_step["evidence"] == {"source": "PLUM_DEMO_CLOCK", "value": value, "submission_date": "2024-11-05", "environment": "development"}
    assert any(event["stage"] == "DEMO_CLOCK" for event in saved["events"])
    # Record-keeping timestamps stay on the real clock; only the submission date is overridden.
    assert saved["created_at"][:4] != "2024"


def test_demo_clock_is_traced_on_correction_results(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLUM_ENV", "test")
    monkeypatch.setenv("PLUM_DEMO_CLOCK", "2024-11-05")
    with _client(tmp_path, monkeypatch) as client:
        saved = client.get(f"/api/claims/{_submit(client).json()['id']}").json()
    assert saved["state"] == "DOCUMENT_CORRECTION_REQUIRED"
    assert [step for step in saved["result"]["trace"] if step["stage"] == "clock"][0]["evidence"]["source"] == "PLUM_DEMO_CLOCK"


@pytest.mark.parametrize("environment", [None, "production", "staging"])
def test_demo_clock_refuses_to_start_outside_development(tmp_path, monkeypatch, environment) -> None:
    if environment:
        monkeypatch.setenv("PLUM_ENV", environment)
    monkeypatch.setenv("PLUM_DEMO_CLOCK", "2024-11-05")
    with pytest.raises(RuntimeError, match="PLUM_DEMO_CLOCK"):
        with _client(tmp_path, monkeypatch):
            pass


def test_demo_clock_is_ignored_in_production_even_after_startup(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(web, "evaluate_claim", lambda *_: _mock_decision())
    _wire_inspection(monkeypatch, complete=True)
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    with _client(tmp_path, monkeypatch) as client:
        monkeypatch.setenv("PLUM_ENV", "production")
        monkeypatch.setenv("PLUM_DEMO_CLOCK", "2024-11-05")
        saved = client.get(f"/api/claims/{_submit(client, _text_pdf('bill')).json()['id']}").json()
    assert saved["request"]["submission_date"] == today
    assert "submission_clock" not in saved["request"]
    assert not any(step.get("stage") == "clock" for step in saved["result"]["trace"])
    assert "ignoring it" in caplog.text


def test_invalid_demo_clock_refuses_to_start(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLUM_ENV", "development")
    monkeypatch.setenv("PLUM_DEMO_CLOCK", "next tuesday")
    with pytest.raises(RuntimeError, match="ISO-8601"):
        with _client(tmp_path, monkeypatch):
            pass


def test_invalid_policy_refuses_to_start(tmp_path, monkeypatch) -> None:
    policy = json.loads(web.POLICY_PATH.read_text(encoding="utf-8"))
    policy["coverage"]["per_claim_limit"] = "5000"  # mistyped money must not default or coerce
    broken = tmp_path / "policy_terms.json"
    broken.write_text(json.dumps(policy), encoding="utf-8")
    monkeypatch.setattr(web, "POLICY_PATH", broken)
    with pytest.raises(RuntimeError, match="POLICY_CONFIGURATION_INVALID"):
        with _client(tmp_path, monkeypatch):
            pass


def test_policy_invalidated_after_startup_fails_closed_without_500(tmp_path, monkeypatch) -> None:
    broken = tmp_path / "policy_terms.json"
    broken.write_text("{not json", encoding="utf-8")
    with _client(tmp_path, monkeypatch) as client:
        monkeypatch.setattr(web, "POLICY_PATH", broken)
        home = client.get("/")
        submitted = _submit(client, _text_pdf("bill"))
    for response in (home, submitted):
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "POLICY_CONFIGURATION_INVALID"


def _submit(client: TestClient, data: bytes = PDF, content_type: str = "application/pdf", member_id: str = "EMP001"):
    fields = {
        "member_id": member_id,
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500.00",
    }
    return client.post(
        "/api/claims",
        data=fields,
        files=[("files", ("claim.pdf", data, content_type))],
    )


def _mock_decision(decision: str = "APPROVED") -> dict:
    approved_paise = 135000 if decision == "APPROVED" else 0
    return {
        "state": "DECIDED", "decision": decision,
        "approved_amount": approved_paise / 100,
        "approved_amount_paise": approved_paise,
        "reasons": [{"code": "TEST_POLICY", "message": "The documented test policy outcome."}],
        "correction_requests": [], "confidence_score": 0.95,
        "trace": [{"stage": "policy", "rule_id": "test_policy", "status": "PASS"}],
        "ledger": [{"description": "Test total", "amount_paise": approved_paise}],
    }


def _text_pdf(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    for index, line in enumerate(text.splitlines()):
        page.insert_text((40, 60 + index * 24), line, fontsize=12)
    data = document.tobytes()
    document.close()
    return data


def _gemini_document(file_id: str, kind: str = "HOSPITAL_BILL") -> dict:
    document: dict[str, Any] = {key: "" for key in _DOC_REQUIRED}
    document.update({
        "file_id": file_id,
        "document_type": kind,
        "document_type_page": 1,
        "document_type_quote": "",
        "patient_name_page": 1,
        "patient_name_quote": "",
        "date_page": 1,
        "date_quote": "",
        "diagnosis_page": 1,
        "diagnosis_quote": "",
        "hospital_name_page": 1,
        "hospital_name_quote": "",
        "test_name_page": 1,
        "test_name_quote": "",
        "total_paise": 0,
        "total_paise_page": 1,
        "total_paise_quote": "",
        "line_items": [],
    })
    return document


class FakeGeminiTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def generate(self, _model, _contents, _schema):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response), {"input_tokens": 120, "output_tokens": 40, "total_tokens": 160}


def _inspection_for_gemini(files, *, complete: bool = False, conflict: bool = False) -> dict:
    bill_content: dict[str, Any] = {"patient_name": "Rajesh Kumar", "date": "2024-11-01"}
    if complete:
        bill_content.update({"total": 1500, "line_items": [{"description": "Consultation Fee", "amount": 1500}]})
    elif conflict:
        bill_content.update({"total": 1000, "line_items": [{"description": "Consultation Fee", "amount": 1500}]})
    bill = {
        "file_id": "UPLOAD-1",
        "file_name": "claim.pdf",
        "sha256": hashlib.sha256(files[0]["data"]).hexdigest(),
        "actual_type": "HOSPITAL_BILL",
        "quality": "GOOD",
        "patient_name_on_doc": "Rajesh Kumar",
        "content": bill_content,
        "evidence": [],
        "extraction_source": "pdf_text",
        "confidence": 0.9,
        "pages": 1,
    }
    prescription = {
        "file_id": "UPLOAD-2",
        "file_name": "prescription.pdf",
        "actual_type": "PRESCRIPTION",
        "quality": "GOOD",
        "patient_name_on_doc": "Rajesh Kumar",
        "content": {"patient_name": "Rajesh Kumar", "date": "2024-11-01", "diagnosis": "Viral Fever"},
        "evidence": [],
        "extraction_source": "pdf_text",
        "confidence": 0.9,
        "pages": 1,
    }
    issues = []
    if not complete and not conflict:
        issues.extend([
            {"code": "MATERIAL_FIELD_UNVERIFIED", "field": "total", "file_name": "claim.pdf", "message": "The bill total could not be verified."},
            {"code": "MATERIAL_FIELD_UNVERIFIED", "field": "line_items", "file_name": "claim.pdf", "message": "The itemized charges could not be verified."},
        ])
    if conflict:
        issues.append({
            "code": "BILL_ARITHMETIC_CONFLICT",
            "file_name": "claim.pdf",
            "message": "The total does not match the itemized charges.",
        })
    return {
        "documents": [bill, prescription],
        "issues": issues,
        "metrics": {"pages": 1},
        "ocr_text_by_file_id": {
            "UPLOAD-1": ["HOSPITAL BILL Patient: Rajesh Kumar Date: 01-Nov-2024 Consultation Fee 1500.00 Grand Total: 1500.00"]
        },
    }


def _successful_bill_response() -> dict:
    document = _gemini_document("UPLOAD-1")
    document.update({
        "total_paise": 150000,
        "total_paise_quote": "Grand Total: 1500.00",
        "line_items": [{
            "description": "Consultation Fee",
            "amount_paise": 150000,
            "page": 1,
            "quote": "Consultation Fee 1500.00",
        }],
    })
    return {"abstain": False, "documents": [document]}


def _wire_fake_gemini(monkeypatch, transport: FakeGeminiTransport) -> None:
    monkeypatch.setenv("GEMINI_EVIDENCE_REVIEW_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-integration-secret")
    monkeypatch.setattr(
        web,
        "resolve_evidence",
        lambda *args, **kwargs: real_resolve_evidence(*args, transport=transport, **kwargs),
    )


def _wire_inspection(monkeypatch, *, complete: bool = False, conflict: bool = False) -> None:
    monkeypatch.setattr(
        web,
        "process_uploads",
        lambda files, *_args, **_kwargs: _inspection_for_gemini(
            files, complete=complete, conflict=conflict
        ),
    )


def test_clean_documents_skip_gemini(tmp_path, monkeypatch):
    transport = FakeGeminiTransport()
    _wire_fake_gemini(monkeypatch, transport)
    _wire_inspection(monkeypatch, complete=True)
    with _client(tmp_path, monkeypatch) as client:
        saved = client.get(f"/api/claims/{_submit(client, _text_pdf('bill')).json()['id']}").json()
    assert transport.calls == 0
    assert saved["state"] == "DECIDED"
    assert saved["result"]["document_metrics"]["gemini"]["status"] == "NOT_NEEDED"


def test_opt_out_skips_gemini_even_when_key_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_EVIDENCE_REVIEW_ENABLED", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-integration-secret")
    monkeypatch.setattr(web, "resolve_evidence", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resolver called")))
    _wire_inspection(monkeypatch, complete=True)
    with _client(tmp_path, monkeypatch) as client:
        saved = client.get(f"/api/claims/{_submit(client, _text_pdf('bill')).json()['id']}").json()
    assert saved["state"] == "DECIDED"
    assert saved["result"]["document_metrics"]["gemini"] == {"status": "DISABLED", "calls": 0, "pages": 0}


def test_valid_candidate_recomputes_deterministic_decision(tmp_path, monkeypatch):
    transport = FakeGeminiTransport(_successful_bill_response())
    _wire_fake_gemini(monkeypatch, transport)
    _wire_inspection(monkeypatch)
    monkeypatch.setenv("PLUM_ENV", "test")
    monkeypatch.setenv("PLUM_DEMO_CLOCK", "2024-11-02")
    source_text = "HOSPITAL BILL Patient: Rajesh Kumar Date: 01-Nov-2024 Consultation Fee 1500.00 Grand Total: 1500.00"
    with _client(tmp_path, monkeypatch) as client:
        response = _submit(client, _text_pdf(source_text))
        saved = client.get(f"/api/claims/{response.json()['id']}").json()
    assert transport.calls == 1
    assert saved["state"] == "DECIDED"
    assert saved["result"]["decision"] == "APPROVED"
    assert saved["result"]["approved_amount"] == 1350
    assert saved["request"]["claimed_amount"] == 1500.0
    assert saved["request"]["submission_date"] == "2024-11-02"
    assert saved["request"]["member_id"] == "EMP001"
    assert saved["result"]["trace"][0]["stage"] == "clock"
    assert saved["result"]["trace"][1]["stage"] == "document_evidence"
    assert saved["result"]["trace"][2]["status"] == "CANDIDATES_APPLIED"
    assert saved["result"]["trace"][2]["candidate_evidence"][0]["fields"] == ["line_items", "total_paise"]


def test_invalid_quote_keeps_arithmetic_conflict_fail_closed_and_redacts_data(tmp_path, monkeypatch):
    invalid = _successful_bill_response()
    invalid["documents"][0]["total_paise_quote"] = "SYNTHETIC_RAW_SECRET_QUOTE"
    transport = FakeGeminiTransport(invalid)
    _wire_fake_gemini(monkeypatch, transport)
    _wire_inspection(monkeypatch, conflict=True)
    raw_secret = "SYNTHETIC_RAW_DOCUMENT_BODY"
    with _client(tmp_path, monkeypatch) as client:
        response = _submit(client, _text_pdf(raw_secret + " Grand Total 1500.00"))
        saved = client.get(f"/api/claims/{response.json()['id']}").json()
    serialized = json.dumps(saved)
    assert transport.calls == 1
    assert saved["state"] == "DOCUMENT_CORRECTION_REQUIRED"
    assert saved["result"]["decision"] is None
    assert "does not match the itemized charges" in saved["result"]["reasons"][0]["message"]
    assert any(
        item.get("rule_id") == "BILL_ARITHMETIC_CONFLICT"
        for item in saved["result"]["trace"]
    )
    assert "synthetic-integration-secret" not in serialized
    assert raw_secret not in serialized
    assert "SYNTHETIC_RAW_SECRET_QUOTE" not in serialized


def test_arithmetic_conflict_provider_timeout_routes_to_manual_review(tmp_path, monkeypatch):
    transport = FakeGeminiTransport(TimeoutError("synthetic raw secret"), TimeoutError("synthetic raw secret"))
    _wire_fake_gemini(monkeypatch, transport)
    _wire_inspection(monkeypatch, conflict=True)
    with _client(tmp_path, monkeypatch) as client:
        response = _submit(client, _text_pdf("HOSPITAL BILL Grand Total: 1500.00"))
        saved = client.get(f"/api/claims/{response.json()['id']}").json()
    assert transport.calls == 2  # only the bounded transient transport retry
    assert saved["state"] == "MANUAL_REVIEW"
    assert saved["result"]["decision"] == "MANUAL_REVIEW"
    serialized = json.dumps(saved)
    assert "synthetic-integration-secret" not in serialized
    assert "synthetic raw secret" not in serialized


def test_upload_correction_is_persisted_without_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(
        web,
        "process_uploads",
        lambda *args, **kwargs: {
            "documents": [],
            "issues": [
                {
                    "code": "MISSING_DOCUMENT",
                    "file_name": "claim.pdf",
                    "required_type": "HOSPITAL_BILL",
                    "message": "Uploaded a prescription; add a hospital bill.",
                }
            ],
            "metrics": {"pages": 1},
        },
    )
    monkeypatch.setattr(web, "evaluate_claim", lambda *_: (_ for _ in ()).throw(AssertionError("adjudicated")))
    with _client(tmp_path, monkeypatch) as client:
        response = _submit(client)
        assert response.status_code == 202
        claim_id = response.json()["id"]
        saved = client.get(f"/api/claims/{claim_id}").json()
        assert saved["state"] == "DOCUMENT_CORRECTION_REQUIRED"
        assert saved["result"]["decision"] is None
        assert "hospital bill" in saved["result"]["correction_requests"][0]["message"]
        assert saved["events"][-1]["stage"] == "DOCUMENT_CORRECTION_REQUIRED"
        assert saved["documents"][0]["sha256"]


def test_upload_decision_and_trace_are_persisted(tmp_path, monkeypatch):
    captured = {}

    def inspect(files, category, member_name, policy, **_kwargs):
        captured["bytes"] = files[0]["data"]
        captured["name"] = member_name
        return {"documents": [{"actual_type": "HOSPITAL_BILL", "content": {"line_items": []}}], "issues": [], "metrics": {"pages": 1}}

    def decide(payload, policy):
        assert payload["claim_category"] == "CONSULTATION"
        assert payload["claimed_amount"] == 1500
        assert payload["documents"][0]["actual_type"] == "HOSPITAL_BILL"
        captured["ytd_claims_amount"] = payload["ytd_claims_amount"]
        captured["ytd_claims_source"] = payload["ytd_claims_source"]
        return {
            "state": "DECIDED",
            "decision": "APPROVED",
            "approved_amount": 1350,
            "approved_amount_paise": 135000,
            "reasons": [{"code": "COVERED", "message": "Covered consultation"}],
            "correction_requests": [],
            "confidence_score": 0.95,
            "trace": [{"stage": "policy", "rule_id": "CONSULTATION", "status": "PASS"}],
                "ledger": [
                    {"description": "Consultation fee", "amount_paise": 150000},
                    {"description": "Co-pay", "amount_paise": -15000},
                ],
        }

    monkeypatch.setattr(web, "process_uploads", inspect)
    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        response = _submit(client)
        assert response.status_code == 202
        claim_id = response.json()["id"]
        saved = client.get(f"/api/claims/{claim_id}").json()
        assert captured == {
            "bytes": PDF,
            "name": "Rajesh Kumar",
            "ytd_claims_amount": 0.0,
            "ytd_claims_source": "database_approved_decisions",
        }
        assert saved["state"] == "DECIDED"
        assert "ytd_claims_amount" not in saved["request"]
        assert captured["ytd_claims_amount"] == 0
        assert captured["ytd_claims_source"] == "database_approved_decisions"
        assert saved["result"]["decision"] == "APPROVED"
        assert saved["result"]["trace"][0]["rule_id"] == "extracted_facts"
        assert any(step.get("rule_id") == "CONSULTATION" for step in saved["result"]["trace"])
        assert saved["result"]["ledger"][1]["amount_paise"] == -15000
        assert client.get("/api/claims").json()["claims"][0]["id"] == claim_id


def test_identical_bill_on_another_claim_routes_to_review(tmp_path, monkeypatch):
    calls = {"adjudications": 0}

    def inspect(files, *_args, **_kwargs):
        return {
            "documents": [{
                "file_id": "UPLOAD-1",
                "sha256": hashlib.sha256(files[0]["data"]).hexdigest(),
                "actual_type": "HOSPITAL_BILL",
                "quality": "GOOD",
                "content": {"total": 1500, "line_items": [{"description": "Consultation", "amount": 1500}]},
            }],
            "issues": [],
            "metrics": {},
        }

    def decide(*_args):
        calls["adjudications"] += 1
        return _mock_decision()

    monkeypatch.setattr(web, "process_uploads", inspect)
    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        first = _submit(client).json()
        assert client.get(f"/api/claims/{first['id']}").json()["result"]["decision"] == "APPROVED"
        second = _submit(client).json()
        saved = client.get(f"/api/claims/{second['id']}").json()
        assert saved["state"] == "MANUAL_REVIEW"
        assert saved["result"]["decision"] == "MANUAL_REVIEW"
        assert saved["result"]["reasons"][0]["code"] == "DUPLICATE_BILL"
        assert saved["result"]["trace"][0]["evidence"]["matching_claim_ids"] == [first["id"]]
        assert calls["adjudications"] == 1


def test_logical_bill_duplicate_routes_to_review(tmp_path, monkeypatch):
    calls = {"count": 0}

    def inspect(files, *_args, **_kwargs):
        return {"documents": [{
            "file_id": "UPLOAD-1", "sha256": hashlib.sha256(files[0]["data"]).hexdigest(),
            "actual_type": "HOSPITAL_BILL", "quality": "GOOD",
            "content": {"bill_number": "INV-102", "hospital_name": "City Clinic", "total": 1500, "line_items": [{"description": "Consultation", "amount": 1500}]},
        }], "issues": [], "metrics": {}}

    def decide(*_args):
        calls["count"] += 1
        return _mock_decision()

    monkeypatch.setattr(web, "process_uploads", inspect)
    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        first = _submit(client, data=PDF + b" original").json()
        assert client.get(f"/api/claims/{first['id']}").json()["result"]["decision"] == "APPROVED"
        second = _submit(client, data=PDF + b" reprint").json()
        saved = client.get(f"/api/claims/{second['id']}").json()
    assert saved["result"]["decision"] == "MANUAL_REVIEW"
    assert calls["count"] == 1


def test_reviewer_can_resolve_manual_review(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "process_uploads", lambda *_args, **_kwargs: {"documents": [], "issues": [{"code": "EXTRACTION_UNAVAILABLE", "file_name": "claim.pdf", "message": "Unavailable"}], "metrics": {}})
    with _client(tmp_path, monkeypatch) as client:
        claim_id = _submit(client).json()["id"]
        response = client.post(f"/api/claims/{claim_id}/review-decision", json={"decision": "APPROVED", "approved_amount": 1000})
    assert response.status_code == 200
    assert response.json()["state"] == "DECIDED"
    assert response.json()["result"]["decision"] == "APPROVED"


def test_duplicate_hash_of_nonpayable_claim_does_not_block_later_claim(tmp_path, monkeypatch):
    calls = {"count": 0}

    def inspect(files, *_args, **_kwargs):
        return {
            "documents": [{
                "file_id": "UPLOAD-1",
                "sha256": hashlib.sha256(files[0]["data"]).hexdigest(),
                "actual_type": "HOSPITAL_BILL",
                "quality": "GOOD",
                "content": {"total": 1500, "line_items": [{"description": "Consultation", "amount": 1500}]},
            }],
            "issues": [],
            "metrics": {},
        }

    def decide(*_args):
        calls["count"] += 1
        if calls["count"] == 1:
            return _mock_decision("REJECTED")
        return _mock_decision()

    monkeypatch.setattr(web, "process_uploads", inspect)
    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        first = _submit(client, data=PDF + b" rejected").json()
        assert client.get(f"/api/claims/{first['id']}").json()["result"]["decision"] == "REJECTED"
        second = _submit(client, data=PDF + b" rejected").json()
        saved = client.get(f"/api/claims/{second['id']}").json()
        assert saved["result"]["decision"] == "APPROVED"
        assert calls["count"] == 2


def test_history_is_loaded_from_local_claims_for_annual_and_frequency_limits(tmp_path, monkeypatch):
    observed = []

    def inspect(files, *_args, **_kwargs):
        return {
            "documents": [{
                "file_id": "UPLOAD-1",
                "sha256": hashlib.sha256(files[0]["data"]).hexdigest(),
                "actual_type": "HOSPITAL_BILL",
                "quality": "GOOD",
                "content": {"total": 1500, "line_items": [{"description": "Consultation", "amount": 1500}]},
            }],
            "issues": [],
            "metrics": {},
        }

    def decide(payload, _policy):
        observed.append(payload)
        return _mock_decision()

    monkeypatch.setattr(web, "process_uploads", inspect)
    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        first = _submit(client, data=PDF + b" first").json()
        assert client.get(f"/api/claims/{first['id']}").json()["result"]["decision"] == "APPROVED"
        second = _submit(client, data=PDF + b" second").json()
        assert client.get(f"/api/claims/{second['id']}").json()["result"]["decision"] == "APPROVED"

    assert observed[1]["ytd_claims_amount"] == 1350
    assert observed[1]["ytd_claims_source"] == "database_approved_decisions"
    assert observed[1]["claims_history"] == [{"claim_id": first["id"], "date": "2024-11-01", "amount": 1500, "provider": None}]
    # Aggregate limits are supplied so the engine evaluates them instead of NOT_EVALUATED.
    assert observed[1]["sum_insured_used"] == 1350
    assert observed[1]["family_floater_used"] == 1350
    assert observed[1]["category_ytd_claims_amount"] == 1350
    assert observed[0]["sum_insured_used"] == observed[0]["category_ytd_claims_amount"] == 0


def test_dependent_history_uses_primary_member_family_pool(tmp_path, monkeypatch):
    observed = []

    def inspect(files, *_args, **_kwargs):
        return {
            "documents": [{
                "file_id": "UPLOAD-1",
                "sha256": hashlib.sha256(files[0]["data"]).hexdigest(),
                "actual_type": "HOSPITAL_BILL",
                "quality": "GOOD",
                "content": {"total": 1500, "line_items": [{"description": "Consultation", "amount": 1500}]},
            }],
            "issues": [],
            "metrics": {},
        }

    def decide(payload, _policy):
        observed.append(payload)
        return _mock_decision()

    monkeypatch.setattr(web, "process_uploads", inspect)
    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        employee = _submit(client, data=PDF + b" employee").json()
        assert client.get(f"/api/claims/{employee['id']}").json()["result"]["decision"] == "APPROVED"
        dependent = _submit(client, data=PDF + b" dependent", member_id="DEP001").json()
        assert client.get(f"/api/claims/{dependent['id']}").json()["result"]["decision"] == "APPROVED"
    assert observed[1]["member_id"] == "DEP001"
    assert observed[1]["ytd_claims_amount"] == 1350
    assert observed[1]["family_floater_used"] == 1350
    # Category usage is per member: the employee's consultation is not the dependent's.
    assert observed[1]["category_ytd_claims_amount"] == 0
    assert observed[1]["claims_history"] == [{"claim_id": employee["id"], "date": "2024-11-01", "amount": 1500, "provider": None}]
    assert observed[1]["claims_history_source"] == "local_family_submission_database"


def test_older_local_database_is_migrated_and_history_is_backfilled(tmp_path, monkeypatch):
    monkeypatch.setenv("PLUM_DATA_DIR", str(tmp_path))
    database = tmp_path / "claims.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE claims (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT, error_message TEXT)"
    )
    connection.execute(
        "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "prior",
            "2024-11-02T00:00:00+00:00",
            "2024-11-02T00:00:00+00:00",
            "DECIDED",
            json.dumps({"member_id": "EMP001", "treatment_date": "2024-11-01"}),
            json.dumps({"decision": "PARTIAL", "approved_amount_paise": 75000}),
            None,
        ),
    )
    connection.commit()
    connection.close()

    web.init_db()
    with web._connect() as migrated:
        row = migrated.execute("SELECT member_id, treatment_date, decision, approved_amount_paise FROM claims WHERE id='prior'").fetchone()
    assert tuple(row) == ("EMP001", "2024-11-01", "PARTIAL", 75000)


def test_invalid_file_is_rejected_before_storage(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        response = _submit(client, b"plain text", "text/plain")
        assert response.status_code == 422
        assert "supported PDF" in response.json()["detail"]
        assert client.get("/api/claims").json()["claims"] == []


def test_document_prefill_suggests_only_document_backed_values_without_persisting(tmp_path, monkeypatch):
    monkeypatch.setattr(
        web,
        "process_uploads",
        lambda *_args, **_kwargs: {
            "documents": [
                {
                    "actual_type": "HOSPITAL_BILL",
                    "patient_name_on_doc": "Rajesh Kumar",
                    "content": {"patient_name": "Rajesh Kumar", "date": "01-Nov-2024", "total": 1500},
                }
            ],
            "issues": [],
            "metrics": {"provider_calls": 0},
            "ocr_text_by_file_id": {"UPLOAD-1": "private source text"},
        },
    )
    with _client(tmp_path, monkeypatch) as client:
        response = client.post("/api/claims/prefill", files=[("files", ("bill.pdf", PDF, "application/pdf"))])
        assert response.status_code == 200
        assert client.get("/api/claims").json()["claims"] == []
    result = response.json()
    assert result["suggestions"] == {
        "member_id": "EMP001",
        "member_name": "Rajesh Kumar",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500.00",
    }
    assert "private source text" not in json.dumps(result)


def test_review_pages_render(tmp_path, monkeypatch):
    monkeypatch.setattr(
        web,
        "process_uploads",
        lambda *args, **kwargs: {"documents": [], "issues": [{"code": "NEEDS_BILL", "message": "Add a hospital bill."}], "metrics": {}},
    )
    with _client(tmp_path, monkeypatch) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Submit an OPD claim" in home.text
        claim_id = _submit(client).json()["id"]
        detail = client.get(f"/claims/{claim_id}")
        assert detail.status_code == 200
        assert "Decision trace" in detail.text


def test_processing_failure_can_be_retried(tmp_path, monkeypatch):
    def broken(*_args, **_kwargs):
        raise TimeoutError("provider unavailable")

    monkeypatch.setattr(web, "process_uploads", broken)
    with _client(tmp_path, monkeypatch) as client:
        claim_id = _submit(client).json()["id"]
        saved = client.get(f"/api/claims/{claim_id}").json()
        assert saved["state"] == "PROCESSING_FAILED"
        assert "TimeoutError" in saved["error_message"]

        monkeypatch.setattr(
            web,
            "process_uploads",
            lambda *args, **kwargs: {"documents": [], "issues": [{"code": "NEEDS_BILL", "message": "Add a hospital bill."}], "metrics": {}},
        )
        retried = client.post(f"/api/claims/{claim_id}/retry")
        assert retried.status_code == 202
        assert client.get(f"/api/claims/{claim_id}").json()["state"] == "DOCUMENT_CORRECTION_REQUIRED"


def test_provider_outage_routes_to_review_without_member_reupload(tmp_path, monkeypatch):
    monkeypatch.setattr(
        web,
        "process_uploads",
        lambda *args, **kwargs: {
            "documents": [],
            "issues": [
                {
                    "code": "EXTRACTION_UNAVAILABLE",
                    "file_name": "bill.png",
                    "message": "Document extraction is unavailable; ask an operator to inspect it.",
                }
            ],
            "metrics": {"provider_failures": 1},
        },
    )
    with _client(tmp_path, monkeypatch) as client:
        claim_id = _submit(client).json()["id"]
        result = client.get(f"/api/claims/{claim_id}").json()["result"]
        assert result["decision"] == "MANUAL_REVIEW"
        assert result["correction_requests"] == []
        assert result["confidence_score"] < 0.5
        assert result["trace"][0]["status"] == "DEGRADED"


# --- Audit round 2 regressions -------------------------------------------------


def _rx_pdf(name: str | None = "Rajesh Kumar", date: str | None = "2024-11-01", diagnosis: str = "Viral fever") -> bytes:
    lines = ["PRESCRIPTION", "Dr. Arun Sharma  MBBS", "Reg. No: KA/12345/2015"]
    lines += [f"Patient Name: {name}"] if name else []
    lines += [f"Date: {date}"] if date else []
    lines += [f"Diagnosis: {diagnosis}", "Medicines: Paracetamol 650mg twice daily for 3 days"]
    return _text_pdf("\n".join(lines))


def _bill_pdf(name: str | None = "Rajesh Kumar", date: str | None = "2024-11-01", bill_no: str = "B-1001", amount: int = 1500, hospital: str = "City Clinic") -> bytes:
    lines = ["HOSPITAL BILL / INVOICE", f"Hospital: {hospital}", f"Bill No: {bill_no}"]
    lines += [f"Patient Name: {name}"] if name else []
    lines += [f"Date: {date}"] if date else []
    lines += [f"1. Consultation Fee  {amount}", f"Total Amount: {amount}"]
    return _text_pdf("\n".join(lines))


def _submit_files(client: TestClient, files: list[bytes], **form: str):
    fields = {"member_id": "EMP001", "claim_category": "CONSULTATION", "treatment_date": "2024-11-01", "claimed_amount": "1500"}
    fields.update(form)
    response = client.post(
        "/api/claims", data=fields,
        files=[("files", (f"doc{index}.pdf", data, "application/pdf")) for index, data in enumerate(files)],
    )
    assert response.status_code == 202, response.text
    return client.get(f"/api/claims/{response.json()['id']}").json()


@pytest.fixture
def _demo_2024(monkeypatch) -> None:
    """Real document extraction and policy engine, replaying the supplied 2024 policy year."""
    monkeypatch.setenv("PLUM_ENV", "development")
    monkeypatch.setenv("PLUM_DEMO_CLOCK", "2024-11-05")
    monkeypatch.setenv("GEMINI_EVIDENCE_REVIEW_ENABLED", "false")
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)


@pytest.mark.usefixtures("_demo_2024")
def test_same_bill_resubmitted_with_new_form_dates_is_held_as_duplicate(tmp_path, monkeypatch):
    """F1: the member-typed treatment date is not part of the bill fingerprint."""
    with _client(tmp_path, monkeypatch) as client:
        original = _submit_files(client, [_rx_pdf(), _bill_pdf()])
        undated = _submit_files(client, [_rx_pdf(date=None), _bill_pdf(date=None)], treatment_date="2024-10-28")
        again = _submit_files(client, [_rx_pdf(date=None, diagnosis="Viral fever x"), _bill_pdf(date=None)], treatment_date="2024-10-20")
    assert original["result"]["decision"] == "APPROVED"
    for resubmission in (undated, again):
        assert resubmission["state"] == "MANUAL_REVIEW"
        assert resubmission["result"]["reasons"][0]["code"] == "DUPLICATE_BILL"
        assert original["id"] in resubmission["result"]["trace"][1]["evidence"]["matching_claim_ids"]
        assert "logical_bill_fingerprint" in resubmission["result"]["trace"][1]["evidence"]["match_types"]


@pytest.mark.usefixtures("_demo_2024")
def test_undated_bill_is_a_member_correction_not_a_payment(tmp_path, monkeypatch):
    """F8: a bill with no readable printed date is never adjudicated."""
    monkeypatch.setattr(web, "evaluate_claim", lambda *_: (_ for _ in ()).throw(AssertionError("adjudicated")))
    with _client(tmp_path, monkeypatch) as client:
        saved = _submit_files(client, [_rx_pdf(), _bill_pdf(date=None)])
    assert saved["state"] == "DOCUMENT_CORRECTION_REQUIRED"
    assert any(
        request["code"] == "MATERIAL_FIELD_UNVERIFIED" and "document date" in request["message"]
        for request in saved["result"]["correction_requests"]
    )


@pytest.mark.usefixtures("_demo_2024")
def test_same_bill_number_with_a_different_printed_date_is_a_different_bill(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        first = _submit_files(client, [_rx_pdf(), _bill_pdf()])
        second = _submit_files(client, [_rx_pdf(date="2024-11-03"), _bill_pdf(date="2024-11-03")], treatment_date="2024-11-03")
    assert first["result"]["decision"] == "APPROVED"
    # The second visit may be trimmed by the annual consultation sub-limit, but it is never a duplicate.
    assert second["result"]["decision"] in {"APPROVED", "PARTIAL"}
    assert "DUPLICATE_BILL" not in {reason["code"] for reason in second["result"]["reasons"]}


@pytest.mark.usefixtures("_demo_2024")
def test_consultation_sub_limit_usage_is_summed_from_earlier_decision_traces(tmp_path, monkeypatch):
    """The exact per-member sub-limit usage comes from each earlier decision's own trace."""
    observed = []
    real_evaluate = web.evaluate_claim

    def decide(payload, policy):
        observed.append(payload)
        return real_evaluate(payload, policy)

    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        first = _submit_files(client, [_rx_pdf(), _bill_pdf()])
        second = _submit_files(client, [_rx_pdf(date="2024-11-03"), _bill_pdf(date="2024-11-03")], treatment_date="2024-11-03")
    counted = next(step for step in first["result"]["trace"] if step["rule_id"] == "category_sub_limit")["evidence"]["counted_against_sub_limit_paise"]
    assert observed[1]["category_sub_limit_used"] == counted / 100
    assert observed[1]["category_sub_limit_used_source"] == "database_decision_traces"
    assert observed[1]["claims_history"][0]["provider"] == first["result"]["provider"]
    step = next(step for step in second["result"]["trace"] if step["rule_id"] == "category_sub_limit")
    assert step["evidence"]["usage_key"] == "category_sub_limit_used"


def test_bill_fingerprint_is_document_derived_and_matches_legacy_rows():
    bill = {
        "actual_type": "HOSPITAL_BILL",
        "content": {"bill_number": "B-1001", "hospital_name": "City Clinic", "total": "1500.005", "date": "01-Nov-2024", "patient_name": "Rajesh Kumar"},
    }
    [fingerprint] = web._bill_fingerprints([bill])
    assert fingerprint == {"kind": "bill_number", "bill_number": "b1001", "provider": "cityclinic", "total_paise": 150001, "document_date": "2024-11-01", "patient": "rajesh kumar"}
    legacy = {"bill_number": "b1001", "provider": "cityclinic", "total_paise": 150001, "treatment_date": "2024-10-01"}
    assert web._fingerprints_match(fingerprint, legacy)
    assert not web._fingerprints_match(fingerprint, {**fingerprint, "document_date": "2024-11-02"})
    assert web._fingerprints_match({**fingerprint, "document_date": None}, fingerprint)
    unnumbered = {"actual_type": "PHARMACY_BILL", "content": {"hospital_name": "Apollo", "total": 120, "date": "2024-11-01", "patient_name": "Mr. Rajesh Kumar"}}
    assert web._bill_fingerprints([unnumbered]) == [
        {"kind": "unnumbered_bill", "provider": "apollo", "total_paise": 12000, "document_date": "2024-11-01", "patient": "rajesh kumar"}
    ]


def test_provider_branch_suffix_and_bill_number_reuse_still_match():
    """Audit B-1: the provider name is compared tolerantly, and the number+total+date wins."""
    assert web._same_provider("Apollo Hospitals", "Apollo Hospitals, Indiranagar")
    assert web._same_provider("apollohospitals", "Indiranagar Apollo Hospitals")  # legacy stored token
    assert not web._same_provider("Apollo Hospitals", "Fortis Hospital")
    assert not web._same_provider("City", "City Clinic")  # too short to stand for a provider
    saved = {"kind": "bill_number", "bill_number": "inv2001", "provider": "apollohospitals", "total_paise": 80000, "document_date": "2024-11-01", "patient": "priya singh"}
    branch = {**saved, "provider": "apollohospitalsindiranagar"}
    assert web._fingerprints_match(branch, saved)
    # Same number, total, and printed date but an unrelated provider name: held, not paid.
    assert web._fingerprints_match({**saved, "provider": "fortishospital"}, saved)
    # Without a readable date the provider must still agree.
    undated = {**saved, "document_date": None}
    assert web._fingerprints_match({**undated, "provider": "apollohospitalsindiranagar"}, saved)
    assert not web._fingerprints_match({**undated, "provider": "fortishospital"}, saved)
    # A re-render with the bill number removed still matches the numbered original.
    unnumbered = {"kind": "unnumbered_bill", "provider": "apollohospitalsindiranagar", "total_paise": 80000, "document_date": "2024-11-01", "patient": "priya singh"}
    assert web._fingerprints_match(unnumbered, saved)
    assert not web._fingerprints_match({**unnumbered, "patient": "rajesh kumar"}, saved)
    # Legacy numbered rows carry no patient and no kind; they still match by number.
    legacy = {"bill_number": "inv2001", "provider": "apollohospitals", "total_paise": 80000, "treatment_date": "2024-10-01"}
    assert web._fingerprints_match(branch, legacy)


@pytest.mark.usefixtures("_demo_2024")
def test_same_bill_with_branch_suffix_on_provider_is_held_as_duplicate(tmp_path, monkeypatch):
    """Audit B-1 (critical): a branch suffix on the provider name must not pay a bill twice."""
    with _client(tmp_path, monkeypatch) as client:
        first = _submit_files(client, [_rx_pdf(), _bill_pdf(bill_no="INV-2001", hospital="Apollo Hospitals")])
        second = _submit_files(client, [_rx_pdf(), _bill_pdf(bill_no="INV-2001", hospital="Apollo Hospitals, Indiranagar")])
        spaced = _submit_files(client, [_rx_pdf(), _bill_pdf(bill_no="INV 2001", hospital="Apollo Hospitals - Indiranagar")])
    assert first["result"]["decision"] == "APPROVED"
    for resubmission in (second, spaced):
        assert resubmission["state"] == "MANUAL_REVIEW"
        assert resubmission["result"]["decision"] == "MANUAL_REVIEW"
        assert resubmission["result"]["reasons"][0]["code"] == "DUPLICATE_BILL"
        assert first["id"] in resubmission["result"]["trace"][1]["evidence"]["matching_claim_ids"]


@pytest.mark.xfail(strict=True, reason="Known gap: core accepts any family member's name on the documents; the engine fix is pending.")
@pytest.mark.usefixtures("_demo_2024")
def test_dependent_filing_with_primary_members_documents_cannot_bypass_sub_limit(tmp_path, monkeypatch):
    """Audit B-3: usage is per patient; filing Rajesh's visit under DEP001 must not be paid.

    EMP001 exhausts the ₹2,000 consultation sub-limit; a claim filed under DEP001
    whose documents name Rajesh Kumar (EMP001) must not draw on DEP001's sub-limit.
    """
    with _client(tmp_path, monkeypatch) as client:
        exhausted = _submit_files(client, [_rx_pdf(), _bill_pdf(amount=2500)], claimed_amount="2500")
        rerouted = _submit_files(
            client,
            [_rx_pdf(date="2024-11-03"), _bill_pdf(date="2024-11-03", bill_no="B-1003")],
            member_id="DEP001", treatment_date="2024-11-03",
        )
    assert exhausted["result"]["decision"] == "PARTIAL"
    assert rerouted["state"] in {"MANUAL_REVIEW", "DOCUMENT_CORRECTION_REQUIRED"}
    assert rerouted["result"]["decision"] not in {"APPROVED", "PARTIAL"}


@pytest.mark.usefixtures("_demo_2024")
def test_pre_existing_waiting_period_is_disclosed_as_not_evaluated_for_web_claims(tmp_path, monkeypatch):
    """Audit B-4: the roster carries no pre-existing conditions, so web never supplies them.

    The waiting period cannot be evaluated; the trace must say so rather than pass it.
    """
    observed = []
    real_evaluate = web.evaluate_claim

    def decide(payload, policy):
        observed.append(payload)
        return real_evaluate(payload, policy)

    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        saved = _submit_files(client, [_rx_pdf(), _bill_pdf()])
    assert "pre_existing_conditions" not in observed[0]
    step = next(step for step in saved["result"]["trace"] if step.get("rule_id") == "pre_existing_condition_wait")
    assert step["status"] == "NOT_EVALUATED"


def test_pending_claim_with_same_bill_file_blocks_a_second_payment(tmp_path, monkeypatch):
    """A bill held for review must not be paid through a second submission."""
    decisions = iter([{**_mock_decision(), "state": "MANUAL_REVIEW", "decision": "MANUAL_REVIEW", "approved_amount": 0, "approved_amount_paise": 0}, _mock_decision()])

    def inspect(files, *_args, **_kwargs):
        return {"documents": [{"file_id": "UPLOAD-1", "sha256": hashlib.sha256(files[0]["data"]).hexdigest(), "actual_type": "HOSPITAL_BILL", "quality": "GOOD", "content": {}}], "issues": [], "metrics": {}}

    monkeypatch.setattr(web, "process_uploads", inspect)
    monkeypatch.setattr(web, "evaluate_claim", lambda *_: next(decisions))
    with _client(tmp_path, monkeypatch) as client:
        first = _submit(client).json()
        second = client.get(f"/api/claims/{_submit(client).json()['id']}").json()
    assert second["result"]["reasons"][0]["code"] == "DUPLICATE_BILL"
    assert second["result"]["trace"][0]["evidence"]["matching_claim_states"] == {first["id"]: "MANUAL_REVIEW"}


@pytest.mark.usefixtures("_demo_2024")
def test_correction_required_uploads_do_not_count_toward_frequency_limits(tmp_path, monkeypatch):
    """F11: three unreadable attempts then a clean claim on the same day is paid."""
    with _client(tmp_path, monkeypatch) as client:
        attempts = [_submit_files(client, [_rx_pdf(name=None), _bill_pdf(name=None)]) for _ in range(3)]
        clean = _submit_files(client, [_rx_pdf(), _bill_pdf()])
    assert {attempt["state"] for attempt in attempts} == {"DOCUMENT_CORRECTION_REQUIRED"}
    assert clean["state"] == "DECIDED" and clean["result"]["decision"] == "APPROVED"
    same_day = next(step for step in clean["result"]["trace"] if step.get("rule_id") == "same_day_claims")
    assert same_day["evidence"]["same_day_claim_count_including_current"] == 1


def test_history_counts_adjudicated_claims_only(tmp_path, monkeypatch):
    observed = []
    outcomes: Iterator[dict[str, Any]] = iter([
        {"issues": [{"code": "UNREADABLE_DOCUMENT", "file_name": "claim.pdf", "message": "Blurry"}]},
        {"issues": [{"code": "EXTRACTION_UNAVAILABLE", "file_name": "claim.pdf", "message": "Outage"}]},
        {"issues": [], "decision": {**_mock_decision(), "state": "MANUAL_REVIEW", "decision": "MANUAL_REVIEW", "approved_amount": 0, "approved_amount_paise": 0}},
        {"issues": [], "decision": _mock_decision("REJECTED")},
        {"issues": [], "decision": _mock_decision()},
    ])
    current: dict[str, Any] = {}

    def inspect(files, *_args, **_kwargs):
        current.update(next(outcomes))
        return {"documents": [{"file_id": "UPLOAD-1", "sha256": hashlib.sha256(files[0]["data"]).hexdigest(), "actual_type": "PRESCRIPTION", "content": {}}], "issues": current["issues"], "metrics": {}}

    def decide(payload, _policy):
        observed.append(payload["claims_history"])
        return current["decision"]

    monkeypatch.setattr(web, "process_uploads", inspect)
    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        ids = [_submit(client, data=PDF + str(index).encode()).json()["id"] for index in range(5)]
        states = [client.get(f"/api/claims/{claim_id}").json()["state"] for claim_id in ids]
    assert states == ["DOCUMENT_CORRECTION_REQUIRED", "MANUAL_REVIEW", "MANUAL_REVIEW", "DECIDED", "DECIDED"]
    # Only the engine-adjudicated review and the rejected claim count; the blurry upload and outage hold do not.
    assert [item["claim_id"] for item in observed[-1]] == [ids[2], ids[3]]


def test_submission_date_uses_the_policy_timezone(tmp_path, monkeypatch):
    """01:15 IST on 5 Nov is still 4 Nov in UTC; the claim must be dated 5 Nov."""
    monkeypatch.setattr(web, "_now", lambda: "2024-11-04T19:45:00+00:00")
    monkeypatch.setattr(web, "evaluate_claim", lambda *_: _mock_decision())
    _wire_inspection(monkeypatch, complete=True)
    with _client(tmp_path, monkeypatch) as client:
        saved = client.get(f"/api/claims/{_submit(client, _text_pdf('bill')).json()['id']}").json()
    assert saved["request"]["submission_date"] == "2024-11-05"
    assert saved["request"]["submission_timezone"] == "Asia/Kolkata"
    assert saved["request"]["submitted_at"] == "2024-11-04T19:45:00+00:00"
    monkeypatch.setenv("PLUM_POLICY_TIMEZONE", "UTC")
    assert web._submission_date("2024-11-04T19:45:00+00:00", None) == "2024-11-04"


def test_demo_clock_datetime_is_dated_in_the_policy_timezone(monkeypatch):
    monkeypatch.setenv("PLUM_ENV", "development")
    for value, expected in (("2024-11-05", "2024-11-05"), ("2024-11-05T23:30:00-05:00", "2024-11-06"), ("2024-11-05T02:00:00", "2024-11-05")):
        monkeypatch.setenv("PLUM_DEMO_CLOCK", value)
        submitted_at, clock = web._submission_clock()
        assert web._submission_date(submitted_at, clock) == expected


def test_unknown_policy_timezone_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.setenv("PLUM_POLICY_TIMEZONE", "Mars/Olympus_Mons")
    with pytest.raises(RuntimeError, match="PLUM_POLICY_TIMEZONE"):
        with _client(tmp_path, monkeypatch):
            pass


def test_demo_clock_claim_recovered_in_production_is_not_adjudicated(tmp_path, monkeypatch):
    """F4: a queued demo-clock claim picked up after a restart into production goes to review."""
    monkeypatch.setenv("PLUM_ENV", "development")
    monkeypatch.setenv("PLUM_DEMO_CLOCK", "2024-11-05")
    real_process_claim = web.process_claim
    monkeypatch.setattr(web, "process_claim", lambda _claim_id: None)  # simulate a crash before processing
    with _client(tmp_path, monkeypatch) as client:
        claim_id = _submit(client).json()["id"]
    monkeypatch.setattr(web, "process_claim", real_process_claim)
    monkeypatch.setenv("PLUM_ENV", "production")
    monkeypatch.delenv("PLUM_DEMO_CLOCK")
    monkeypatch.setattr(web, "process_uploads", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("inspected")))
    monkeypatch.setattr(web, "evaluate_claim", lambda *_: (_ for _ in ()).throw(AssertionError("adjudicated")))
    with _client(tmp_path, monkeypatch) as client:
        for _ in range(200):
            saved = client.get(f"/api/claims/{claim_id}").json()
            if saved["state"] not in {"QUEUED", "PROCESSING"}:
                break
            time.sleep(0.01)
    assert saved["state"] == "MANUAL_REVIEW"
    assert saved["result"]["reasons"][0]["code"] == "DEMO_CLOCK_NOT_HONORED"
    quarantine = next(step for step in saved["result"]["trace"] if step["rule_id"] == "demo_clock_not_honored")
    assert quarantine["evidence"] == {"stamped_submission_date": "2024-11-05", "stamped_environment": "development", "processing_environment": "production"}
    assert any(event["detail"].get("reason") == "demo_clock_outside_development" for event in saved["events"])


@pytest.mark.usefixtures("_demo_2024")
def test_request_fingerprint_matches_decision_trace_and_aggregates_are_evaluated(tmp_path, monkeypatch):
    """F6/F7: one policy fingerprint end to end; sum insured and floater are evaluated on the web path."""
    with _client(tmp_path, monkeypatch) as client:
        saved = _submit_files(client, [_rx_pdf(), _bill_pdf()])
    source = next(step for step in saved["result"]["trace"] if step.get("rule_id") == "policy_source")
    assert saved["request"]["policy_canonical_sha256"] == source["evidence"]["canonical_sha256"]
    assert "policy_sha256" not in saved["request"]
    statuses = {step.get("rule_id"): step.get("status") for step in saved["result"]["trace"]}
    assert statuses["sum_insured"] == "PASS"
    assert statuses["family_floater_limit"] == "PASS"
    assert statuses["annual_opd_limit"] == "PASS"


def test_policy_fingerprint_mismatch_and_legacy_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "evaluate_claim", lambda *_: _mock_decision())
    _wire_inspection(monkeypatch, complete=True)
    real_process_claim = web.process_claim
    monkeypatch.setattr(web, "process_claim", lambda _claim_id: None)
    with _client(tmp_path, monkeypatch) as client:
        stale_id = _submit(client, _text_pdf("stale")).json()["id"]
        legacy_id = _submit(client, _text_pdf("legacy")).json()["id"]
    policy = web._read_policy()
    with web._connect() as connection:
        for claim_id, update in ((stale_id, {"policy_canonical_sha256": "0" * 64}), (legacy_id, {"policy_sha256": web._legacy_policy_sha256(policy)})):
            request = json.loads(connection.execute("SELECT request_json FROM claims WHERE id=?", (claim_id,)).fetchone()[0])
            request.pop("policy_canonical_sha256")
            request.update(update)
            connection.execute("UPDATE claims SET request_json=? WHERE id=?", (json.dumps(request), claim_id))
    real_process_claim(stale_id)
    real_process_claim(legacy_id)
    stale, legacy = web._load_claim(stale_id), web._load_claim(legacy_id)
    assert stale is not None and legacy is not None
    assert stale["result"]["reasons"][0]["code"] == "POLICY_CHANGED"
    assert stale["result"]["trace"][0]["evidence"]["current_canonical_sha256"] == policy["canonical_sha256"]
    assert legacy["state"] == "DECIDED"


def test_claimed_amount_with_subpaise_precision_is_rejected(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/claims",
            data={"member_id": "EMP001", "claim_category": "CONSULTATION", "treatment_date": "2024-11-01", "claimed_amount": "1500.005"},
            files=[("files", ("claim.pdf", PDF, "application/pdf"))],
        )
    assert response.status_code == 422
