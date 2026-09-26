from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import types
from pypdf import PdfWriter

from claims.ai_review import (
    _DOC_REQUIRED,
    GoogleGenAITransport,
    build_trigger,
    resolve_evidence,
)


@pytest.fixture(autouse=True)
def _explicit_synthetic_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_EVIDENCE_REVIEW_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")


class FakeTransport:
    def __init__(self, responses: list[Any]):
        self.responses = list(responses)
        self.calls: list[tuple[str, list[Any], dict[str, Any]]] = []

    def generate(self, model: str, contents: list[Any], schema: dict[str, Any]) -> tuple[str, dict[str, int]]:
        self.calls.append((model, contents, schema))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response), {"input_tokens": 321, "output_tokens": 88, "total_tokens": 409}


def _model_doc(file_id: str, *, kind: str = "UNKNOWN") -> dict[str, Any]:
    value: dict[str, Any] = {key: "" for key in _DOC_REQUIRED}
    value.update({
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
    return value


def _claim_doc(*, file_id: str = "UPLOAD-1", kind: str = "UNKNOWN", content: dict[str, Any] | None = None, pages: int = 1) -> dict[str, Any]:
    return {"file_id": file_id, "actual_type": kind, "content": content or {}, "pages": pages}


def _file(pages: int = 1) -> dict[str, Any]:
    output = BytesIO()
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    writer.write(output)
    return {"data": output.getvalue(), "mime_type": "application/pdf"}


def test_google_transport_explicitly_uses_low_thinking() -> None:
    captured: dict[str, Any] = {}

    class FakeModels:
        def generate_content(self, **kwargs: Any) -> Any:
            captured.update(kwargs)
            return SimpleNamespace(
                text="{}",
                usage_metadata=SimpleNamespace(prompt_token_count=2, candidates_token_count=3),
            )

    transport = GoogleGenAITransport.__new__(GoogleGenAITransport)
    transport._types = types
    transport._client = SimpleNamespace(models=FakeModels())  # type: ignore[assignment]

    text, usage = transport.generate("gemini-test", ["field extraction"], {"type": "OBJECT"})

    assert text == "{}"
    assert usage == {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    assert captured["config"].thinking_config.thinking_level == types.ThinkingLevel.LOW


def test_prescription_without_printed_date_does_not_trigger_gemini() -> None:
    doc = _claim_doc(kind="PRESCRIPTION", content={"patient_name": "Rajesh Kumar", "diagnosis": "Viral Fever"})
    fake = FakeTransport([])

    result = resolve_evidence([doc], {"UPLOAD-1": _file()}, {"UPLOAD-1": ["Patient: Rajesh Kumar Diagnosis: Viral Fever"]}, transport=fake)

    assert result["status"] == "NOT_NEEDED"
    assert result["metrics"]["calls"] == 0
    assert fake.calls == []


def test_env_opt_in_is_required_even_when_key_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_EVIDENCE_REVIEW_ENABLED", "false")
    fake = FakeTransport([])
    result = resolve_evidence(
        [_claim_doc()],
        {"UPLOAD-1": _file()},
        {"UPLOAD-1": ["Medical Prescription"]},
        transport=fake,
    )
    assert result["status"] == "ABSTAINED"
    assert result["trace"]["reason"] == "provider_disabled"
    assert fake.calls == []


def test_unknown_type_is_grouped_and_quote_validated() -> None:
    doc = _claim_doc()
    raw = _model_doc("UPLOAD-1", kind="PRESCRIPTION")
    raw["document_type_quote"] = "Medical Prescription"
    response = {"abstain": False, "documents": [raw]}
    fake = FakeTransport([response])

    result = resolve_evidence(
        [doc], {"UPLOAD-1": _file()}, {"UPLOAD-1": ["Medical Prescription Patient: Rajesh Kumar"]}, transport=fake
    )

    assert result["status"] == "CANDIDATES_VALIDATED"
    assert result["candidates"][0]["fields"] == {"document_type": "PRESCRIPTION"}
    assert result["candidates"][0]["evidence"][0]["quote"] == "Medical Prescription"
    assert len(fake.calls) == 1
    assert fake.calls[0][2]["properties"].keys() == {"abstain", "documents"}
    assert "decision" not in str(fake.calls[0][2]).lower()
    assert "payable" not in str(fake.calls[0][2]).lower()


def test_known_type_quote_is_validated_but_not_returned_as_candidate() -> None:
    doc = _claim_doc(
        kind="HOSPITAL_BILL",
        content={"patient_name": "Rajesh Kumar", "date": "2024-11-01"},
    )
    raw = _model_doc("UPLOAD-1", kind="HOSPITAL_BILL")
    raw.update({
        "document_type_page": 1,
        "document_type_quote": "HOSPITAL BILL",
        "total_paise": 10000,
        "total_paise_quote": "Grand Total: Rs 100.00",
        "line_items": [{"description": "Consultation Fee", "amount_paise": 10000, "page": 1, "quote": "Consultation Fee 100.00"}],
    })
    fake = FakeTransport([{"abstain": False, "documents": [raw]}])

    result = resolve_evidence(
        [doc],
        {"UPLOAD-1": _file()},
        {"UPLOAD-1": ["HOSPITAL BILL Patient: Rajesh Kumar Consultation Fee 100.00 Grand Total: Rs 100.00"]},
        transport=fake,
    )

    assert result["status"] == "CANDIDATES_VALIDATED"
    assert result["candidates"][0]["fields"] == {
        "total_paise": 10000,
        "line_items": [{"description": "Consultation Fee", "amount_paise": 10000}],
    }
    assert all(proof["field"] != "document_type" for proof in result["candidates"][0]["evidence"])


def test_provider_echoes_of_known_fields_are_checked_then_discarded() -> None:
    doc = _claim_doc(
        kind="PRESCRIPTION",
        content={"patient_name": "Rajesh Kumar"},
    )
    raw = _model_doc("UPLOAD-1", kind="PRESCRIPTION")
    raw.update({
        "document_type_quote": "PRESCRIPTION",
        "patient_name": "Rajesh Kumar",
        "patient_name_quote": "Patient: Rajesh Kumar",
        "date": "2024-11-01",
        "date_quote": "Date: 01-Nov-2024",
        "diagnosis": "Viral Fever",
        "diagnosis_quote": "Diagnosis: Viral Fever",
    })
    fake = FakeTransport([{"abstain": False, "documents": [raw]}])

    result = resolve_evidence(
        [doc],
        {"UPLOAD-1": _file()},
        {"UPLOAD-1": ["PRESCRIPTION Patient: Rajesh Kumar Date: 01-Nov-2024 Diagnosis: Viral Fever"]},
        allowed_patient_names=["Rajesh Kumar"],
        transport=fake,
    )

    assert result["status"] == "CANDIDATES_VALIDATED"
    assert result["candidates"][0]["fields"] == {"diagnosis": "Viral Fever"}
    assert [proof["field"] for proof in result["candidates"][0]["evidence"]] == ["diagnosis"]
    assert "For every schema field not listed in fields_to_resolve" in fake.calls[0][1][0]


@pytest.mark.parametrize(
    ("extra_values", "reason"),
    [
        ({"date": "2024-11-02"}, "unsolicited_field_conflict"),
        ({"date": "2024-11-02", "date_quote": "Date: 01-Nov-2024"}, "unsolicited_field_conflict"),
    ],
)
def test_conflicting_unsolicited_fields_abstain(extra_values: dict[str, str], reason: str) -> None:
    doc = _claim_doc(
        kind="PRESCRIPTION",
        content={"patient_name": "Rajesh Kumar", "date": "2024-11-01"},
    )
    raw = _model_doc("UPLOAD-1", kind="PRESCRIPTION")
    raw.update({
        "document_type_quote": "PRESCRIPTION",
        "diagnosis": "Viral Fever",
        "diagnosis_quote": "Diagnosis: Viral Fever",
        **extra_values,
    })
    result = resolve_evidence(
        [doc],
        {"UPLOAD-1": _file()},
        {"UPLOAD-1": ["PRESCRIPTION Patient: Rajesh Kumar Date: 01-Nov-2024 Diagnosis: Viral Fever"]},
        allowed_patient_names=["Rajesh Kumar"],
        transport=FakeTransport([{"abstain": False, "documents": [raw]}]),
    )

    assert result["status"] == "ABSTAINED"
    assert result["trace"]["reason"] == reason


def test_unsupported_extra_field_abstains_even_when_it_is_not_locally_known() -> None:
    doc = _claim_doc(kind="PRESCRIPTION", content={"patient_name": "Rajesh Kumar"})
    raw = _model_doc("UPLOAD-1", kind="PRESCRIPTION")
    raw.update({
        "document_type_quote": "PRESCRIPTION",
        "diagnosis": "Viral Fever",
        "diagnosis_quote": "Diagnosis: Viral Fever",
        "date": "2024-11-02",
        "date_quote": "Date: 01-Nov-2024",
    })
    result = resolve_evidence(
        [doc],
        {"UPLOAD-1": _file()},
        {"UPLOAD-1": ["PRESCRIPTION Patient: Rajesh Kumar Date: 01-Nov-2024 Diagnosis: Viral Fever"]},
        allowed_patient_names=["Rajesh Kumar"],
        transport=FakeTransport([{"abstain": False, "documents": [raw]}]),
    )

    assert result["status"] == "ABSTAINED"
    assert result["trace"]["reason"] == "date_not_supported_by_quote"


def test_missing_bill_fields_require_quotes_and_exact_arithmetic() -> None:
    doc = _claim_doc(kind="HOSPITAL_BILL", content={"patient_name": "Rajesh Kumar", "date": "2024-11-01"})
    raw = _model_doc("UPLOAD-1", kind="HOSPITAL_BILL")
    raw.update({
        "total_paise": 10000,
        "total_paise_quote": "Grand Total: Rs 100.00",
        "line_items": [{"description": "Consultation Fee", "amount_paise": 10000, "page": 1, "quote": "Consultation Fee 100.00"}],
    })
    fake = FakeTransport([{"abstain": False, "documents": [raw]}])

    result = resolve_evidence(
        [doc],
        {"UPLOAD-1": _file()},
        {"UPLOAD-1": ["Patient Rajesh Kumar, bill date 01-Nov-2024. Consultation Fee 100.00. Grand Total: Rs 100.00"]},
        transport=fake,
    )

    assert result["status"] == "CANDIDATES_VALIDATED"
    assert result["candidates"][0]["fields"]["total_paise"] == 10000
    assert result["candidates"][0]["fields"]["line_items"][0]["amount_paise"] == 10000


def test_only_selected_page_and_short_snippet_are_sent_and_counted() -> None:
    doc = _claim_doc(
        kind="PRESCRIPTION",
        content={"patient_name": "Rajesh Kumar", "date": "2024-11-01"},
        pages=2,
    )
    first_page = "Patient: Rajesh Kumar Date: 01-Nov-2024 PRIVATE_PAGE_ONE unrelated clinical details"
    second_page = "Diagnosis: Viral Fever"
    raw = _model_doc("UPLOAD-1", kind="PRESCRIPTION")
    raw.update({"diagnosis": "Viral Fever", "diagnosis_page": 2, "diagnosis_quote": "Diagnosis: Viral Fever"})
    fake = FakeTransport([{"abstain": False, "documents": [raw]}])

    result = resolve_evidence(
        [doc],
        {"UPLOAD-1": _file(pages=2)},
        {"UPLOAD-1": [first_page, second_page]},
        transport=fake,
    )

    assert result["status"] == "CANDIDATES_VALIDATED"
    assert result["metrics"]["pages"] == 1
    assert "PRIVATE_PAGE_ONE" not in fake.calls[0][1][0]
    assert "Diagnosis: Viral Fever" in fake.calls[0][1][0]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda d: d.update({"document_type_page": 2}), "page_out_of_bounds"),
        (lambda d: d.update({"document_type_quote": "fake text"}), "source_quote_not_found"),
        (lambda d: d.update({"document_type": "HOSPITAL_BILL"}), "document_type_not_supported_by_quote"),
    ],
)
def test_unsupported_or_weak_evidence_abstains(mutation: Any, reason: str) -> None:
    doc = _claim_doc()
    raw = _model_doc("UPLOAD-1", kind="PRESCRIPTION")
    raw["document_type_quote"] = "Medical Prescription"
    mutation(raw)
    fake = FakeTransport([{"abstain": False, "documents": [raw]}])
    result = resolve_evidence([doc], {"UPLOAD-1": _file()}, {"UPLOAD-1": ["Medical Prescription"]}, transport=fake)
    assert result["status"] == "ABSTAINED"
    assert result["candidates"] == []
    assert result["trace"]["reason"] == reason


def test_candidate_patient_name_requires_roster_allowlist() -> None:
    doc = _claim_doc(kind="PRESCRIPTION", content={"date": "2024-11-01", "diagnosis": "Viral Fever"})
    raw = _model_doc("UPLOAD-1", kind="PRESCRIPTION")
    raw.update({"patient_name": "Rajesh Kumar", "patient_name_quote": "Patient: Rajesh Kumar"})
    fake = FakeTransport([{"abstain": False, "documents": [raw]}])
    result = resolve_evidence(
        [doc],
        {"UPLOAD-1": _file()},
        {"UPLOAD-1": ["Patient: Rajesh Kumar Medical Prescription"]},
        transport=fake,
    )
    assert result["status"] == "ABSTAINED"
    assert result["trace"]["reason"] == "identity_roster_unavailable"


def test_bill_arithmetic_conflict_abstains() -> None:
    doc = _claim_doc(kind="HOSPITAL_BILL", content={"patient_name": "Rajesh Kumar", "date": "2024-11-01", "total": 100, "line_items": [{"description": "Visit", "amount": 50}]})
    trigger, reason = build_trigger([doc], {"UPLOAD-1": ["Patient: Rajesh Kumar Date: 01-Nov-2024 Total 100 Visit 50"]})
    assert trigger is not None
    assert set(trigger.fields_by_file["UPLOAD-1"]) == {"total_paise", "line_items"}
    assert "bill_arithmetic_conflict" in trigger.reasons
    assert reason is None


def test_member_identity_conflict_skips_gemini() -> None:
    docs = [
        _claim_doc(file_id="UPLOAD-1", kind="HOSPITAL_BILL", content={"patient_name": "Rajesh Kumar"}),
        _claim_doc(file_id="UPLOAD-2", kind="PRESCRIPTION", content={"patient_name": "Other Person"}),
    ]
    fake = FakeTransport([])
    result = resolve_evidence(
        docs,
        {"UPLOAD-1": _file(), "UPLOAD-2": _file()},
        {"UPLOAD-1": ["bill"], "UPLOAD-2": ["prescription"]},
        allowed_patient_names=["Rajesh Kumar"],
        transport=fake,
    )
    assert result["status"] == "ABSTAINED"
    assert result["trace"]["reason"] == "identity_conflict"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("docs", "ocr", "reason"),
    [
        ([_claim_doc(pages=11)], {"UPLOAD-1": ["x"] * 11}, "document_page_cap_exceeded"),
        ([_claim_doc(), _claim_doc(file_id="UPLOAD-2"), _claim_doc(file_id="UPLOAD-3"), _claim_doc(file_id="UPLOAD-4")], {f"UPLOAD-{i}": ["x"] for i in range(1, 5)}, "file_cap_exceeded"),
        ([_claim_doc()], {}, "source_text_unavailable"),
    ],
)
def test_router_caps_call_or_abstains(docs: list[dict[str, Any]], ocr: dict[str, Any], reason: str) -> None:
    trigger, actual = build_trigger(docs, ocr)
    assert trigger is None
    assert actual == reason


def test_only_one_retry_for_transient_provider_failure() -> None:
    doc = _claim_doc()
    raw = _model_doc("UPLOAD-1", kind="PRESCRIPTION")
    raw["document_type_quote"] = "Medical Prescription"
    fake = FakeTransport([TimeoutError("contains secret document text"), {"abstain": False, "documents": [raw]}])
    result = resolve_evidence(
        [doc], {"UPLOAD-1": _file()}, {"UPLOAD-1": ["Medical Prescription"]}, transport=fake, sleep=lambda _: None
    )
    assert result["status"] == "CANDIDATES_VALIDATED"
    assert result["metrics"]["retries"] == 1
    assert result["metrics"]["calls"] == 2
    assert "secret" not in json.dumps(result["trace"])


def test_second_provider_failure_is_redacted_and_has_no_candidate() -> None:
    fake = FakeTransport([TimeoutError("sensitive quote"), TimeoutError("private patient data")])
    result = resolve_evidence(
        [_claim_doc()], {"UPLOAD-1": _file()}, {"UPLOAD-1": ["Medical Prescription"]}, transport=fake, sleep=lambda _: None
    )
    assert result["status"] == "ABSTAINED"
    assert result["trace"]["reason"] == "timeout"
    assert result["candidates"] == []
    assert "private" not in json.dumps(result)
