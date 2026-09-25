"""Workflow checks at the HTTP boundary, including durable correction results."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from claims import web
from claims.ai_review import _DOC_REQUIRED
from claims.ai_review import resolve_evidence as real_resolve_evidence

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PLUM_DATA_DIR", str(tmp_path))
    return TestClient(web.app)


def test_operations_worklist_page_is_available(tmp_path, monkeypatch) -> None:
    with _client(tmp_path, monkeypatch) as client:
        response = client.get("/ops")
    assert response.status_code == 200
    assert "Review" in response.text


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
    document = {key: "" for key in _DOC_REQUIRED}
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
    bill_content = {"patient_name": "Rajesh Kumar", "date": "2024-11-01"}
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
    source_text = "HOSPITAL BILL Patient: Rajesh Kumar Date: 01-Nov-2024 Consultation Fee 1500.00 Grand Total: 1500.00"
    with _client(tmp_path, monkeypatch) as client:
        response = _submit(client, _text_pdf(source_text))
        saved = client.get(f"/api/claims/{response.json()['id']}").json()
    assert transport.calls == 1
    assert saved["state"] == "DECIDED"
    assert saved["result"]["decision"] == "APPROVED"
    assert saved["result"]["approved_amount"] == 1350
    assert saved["request"]["claimed_amount"] == 1500.0
    assert saved["request"]["member_id"] == "EMP001"
    assert saved["result"]["trace"][0]["stage"] == "document_evidence"
    assert saved["result"]["trace"][1]["status"] == "CANDIDATES_APPLIED"
    assert saved["result"]["trace"][1]["candidate_evidence"][0]["fields"] == ["line_items", "total_paise"]


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
    assert "does not match the itemized charges" in saved["result"]["reasons"][0]
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
        assert "hospital bill" in saved["result"]["correction_requests"][0]
        assert saved["events"][-1]["stage"] == "DOCUMENT_CORRECTION_REQUIRED"
        assert saved["documents"][0]["sha256"]


def test_upload_decision_and_trace_are_persisted(tmp_path, monkeypatch):
    captured = {}

    def inspect(files, category, member_name, policy):
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
    assert observed[1]["claims_history"] == [{"date": "2024-11-01"}]


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
    assert observed[1]["claims_history"] == [{"date": "2024-11-01"}]


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
