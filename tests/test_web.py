"""Workflow checks at the HTTP boundary, including durable correction results."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from claims import web

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("PLUM_DATA_DIR", str(tmp_path))
    return TestClient(web.app)


def _submit(client: TestClient, data: bytes = PDF, content_type: str = "application/pdf", ytd: str | None = None):
    fields = {
        "member_id": "EMP001",
        "claim_category": "CONSULTATION",
        "treatment_date": "2024-11-01",
        "claimed_amount": "1500.00",
    }
    if ytd is not None:
        fields["ytd_claims_amount"] = ytd
    return client.post(
        "/api/claims",
        data=fields,
        files=[("files", ("claim.pdf", data, content_type))],
    )


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
        return {
            "state": "DECIDED",
            "decision": "APPROVED",
            "approved_amount": 1350,
            "approved_amount_paise": 135000,
            "reasons": ["Covered consultation"],
            "correction_requests": [],
            "confidence_score": 0.95,
            "trace": [{"stage": "policy", "rule_id": "CONSULTATION", "status": "PASS"}],
            "ledger": [{"description": "Co-pay", "amount_paise": -15000}],
        }

    monkeypatch.setattr(web, "process_uploads", inspect)
    monkeypatch.setattr(web, "evaluate_claim", decide)
    with _client(tmp_path, monkeypatch) as client:
        response = _submit(client, ytd="0")
        assert response.status_code == 202
        claim_id = response.json()["id"]
        saved = client.get(f"/api/claims/{claim_id}").json()
        assert captured == {"bytes": PDF, "name": "Rajesh Kumar"}
        assert saved["state"] == "DECIDED"
        assert saved["request"]["ytd_claims_amount"] == 0
        assert saved["result"]["decision"] == "APPROVED"
        assert saved["result"]["trace"][0]["rule_id"] == "CONSULTATION"
        assert saved["result"]["ledger"][0]["amount_paise"] == -15000
        assert client.get("/api/claims").json()["claims"][0]["id"] == claim_id


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
