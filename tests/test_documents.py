from __future__ import annotations

import io
import json
import ssl
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image, ImageDraw

from claims.documents import (
    SarvamDocumentProvider,
    _text_content,
    normal_name,
    parse_document_date,
    process_uploads,
    revalidate_documents,
    sniff_media_type,
)
from claims.money import has_subpaise_precision, to_paise, to_rupees
from tools.generate_samples import create_samples

POLICY = {
    "document_requirements": {
        "CONSULTATION": {"required": ["PRESCRIPTION", "HOSPITAL_BILL"], "optional": []},
        "PHARMACY": {"required": ["PRESCRIPTION", "PHARMACY_BILL"], "optional": []},
        "DENTAL": {"required": ["HOSPITAL_BILL"], "optional": ["DENTAL_REPORT"]},
    }
}
RECEIPT_OCR_REGRESSION = json.loads(
    (Path(__file__).parent / "fixtures" / "receipt_ocr_regression.json").read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def no_live_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SARVAM_API_KEY", raising=False)


def pdf_bytes(lines: list[str]) -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    for index, line in enumerate(lines):
        page.insert_text((40, 70 + index * 25), line, fontsize=12)
    data = pdf.tobytes()
    pdf.close()
    return data


def image_bytes(width: int = 1000, height: int = 1000) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(image).text((80, 80), "Hospital Bill Patient: Rajesh Kumar", fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def annotated_image_bytes() -> bytes:
    image = Image.open(io.BytesIO(image_bytes())).convert("RGB")
    ImageDraw.Draw(image).line((650, 600, 850, 610), fill=(40, 60, 180), width=8)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class StubProvider:
    def __init__(self, ocr: str, fields: dict | None = None):
        self.ocr = ocr
        self.fields = fields
        self.digitise_calls = 0
        self.extract_calls = 0

    def digitise(self, data: bytes, mime_type: str) -> str:
        self.digitise_calls += 1
        return self.ocr

    def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict:
        self.extract_calls += 1
        assert self.fields is not None
        return {"document_type": document_type, "quality": "GOOD", "fields": self.fields}


def test_html_bill_table_beats_address_like_loose_line_and_ignores_embedded_image() -> None:
    fixture = RECEIPT_OCR_REGRESSION
    rows = "\n".join(
        f"<tr><td>{index}</td><td>{item['description']}</td><td>{item['amount']:,.2f}</td></tr>"
        for index, item in enumerate(fixture["line_items"], 1)
    )
    text = (
        "HOSPITAL BILL\n"
        f"Patient: {fixture['patient_name']}\nDate: {fixture['date']}\n"
        f"{fixture['address_like_line']}\n"
        "![Image](data:image/jpeg;base64,MS OewXtTik)\n"
        "<table><tr><td>S.No.</td><td>Description</td><td>Amount (₹)</td></tr>\n"
        f"{rows}</table>\nFinal Total Amount (₹): {fixture['total']:,.2f}"
    )

    content, _ = _text_content(text, "HOSPITAL_BILL")

    assert content["line_items"] == fixture["line_items"]
    assert "doctor_specialization" not in content


def test_loose_bill_parser_rejects_address_like_bare_integer() -> None:
    fixture = RECEIPT_OCR_REGRESSION
    content, _ = _text_content(
        f"HOSPITAL BILL\nPatient: {fixture['patient_name']}\nDate: {fixture['date']}\n"
        f"{fixture['address_like_line']}\nFinal Total Amount: {fixture['total']:.2f}",
        "HOSPITAL_BILL",
    )

    assert "line_items" not in content


def test_reconciled_sarvam_hospital_items_replace_bad_local_candidates() -> None:
    fixture = RECEIPT_OCR_REGRESSION
    provider = StubProvider(
        f"HOSPITAL BILL\nPatient: {fixture['patient_name']}\nDate: {fixture['date']}\n"
        f"{fixture['address_like_line']}\nTotal Amount: {fixture['total']:.2f}",
        {
            "total": fixture["total"],
            "line_items": fixture["line_items"],
        },
    )

    result = process_uploads(
        [{"file_name": "bill.png", "data": image_bytes()}], "DENTAL", fixture["patient_name"], POLICY, provider
    )

    assert provider.extract_calls == 1
    assert result["issues"] == []
    assert result["documents"][0]["content"]["line_items"] == [
        {**item, "brand_status": "UNKNOWN", "brand_evidence": ""}
        for item in fixture["line_items"]
    ]


def test_wrong_document_names_uploaded_and_required_type() -> None:
    first = pdf_bytes(["PRESCRIPTION", "Dr. Arun Sharma", "Patient: Rajesh Kumar", "Diagnosis: Viral Fever", "Medicines: Paracetamol 650mg"])
    second = pdf_bytes(["PRESCRIPTION", "Dr. Meena Rao", "Patient: Rajesh Kumar", "Diagnosis: Fever", "Medicines: Vitamin C 500mg"])
    result = process_uploads(
        [{"file_name": "first.pdf", "data": first}, {"file_name": "second.pdf", "data": second}],
        "CONSULTATION", "Rajesh Kumar", POLICY,
    )
    assert any(issue["code"] == "MISSING_DOCUMENT" and issue["required_type"] == "HOSPITAL_BILL" and "prescription" in issue["message"] and "hospital or clinic bill" in issue["message"] for issue in result["issues"])
    assert [doc["actual_type"] for doc in result["documents"]] == ["PRESCRIPTION", "PRESCRIPTION"]


def test_digital_pdf_local_extraction_and_dental_matrix() -> None:
    bill = pdf_bytes([
        "HOSPITAL BILL / RECEIPT",
        "Patient: Priya Singh",
        "Date: 15-Oct-2024",
        "Bill No: SMILE-12345",
        "Root Canal Treatment 8000.00",
        "Teeth Whitening 4000.00",
        "Total Amount: 12000.00",
    ])
    result = process_uploads([{"file_name": "dental.pdf", "data": bill}], "DENTAL", "Priya Singh", POLICY)
    assert result["issues"] == []
    document = result["documents"][0]
    assert document["actual_type"] == "HOSPITAL_BILL"
    assert document["patient_name_on_doc"] == "Priya Singh"
    assert document["content"]["total"] == 12000
    assert document["content"]["line_items"] == [
        {"description": "Root Canal Treatment", "amount": 8000},
        {"description": "Teeth Whitening", "amount": 4000},
    ]
    assert document["extraction_source"] == "pdf_text"
    assert any(evidence["field"] == "total" for evidence in document["evidence"])
    assert result["metrics"]["provider_calls"] == 0


def test_pharmacy_claim_does_not_require_printed_prescription_date() -> None:
    prescription = pdf_bytes([
        "PRESCRIPTION",
        "Dr. Meena Rao",
        "Patient: Rajesh Kumar",
        "Diagnosis: Viral Fever",
        "Medicines: Paracetamol 650mg",
    ])
    bill = pdf_bytes([
        "PHARMACY BILL / RECEIPT",
        "Patient: Rajesh Kumar",
        "Date: 01-Nov-2024",
        "Paracetamol 650mg 120.00",
        "Total Amount: 120.00",
    ])
    result = process_uploads(
        [
            {"file_name": "prescription.pdf", "data": prescription},
            {"file_name": "pharmacy.pdf", "data": bill},
        ],
        "PHARMACY",
        "Rajesh Kumar",
        POLICY,
    )
    assert [doc["actual_type"] for doc in result["documents"]] == ["PRESCRIPTION", "PHARMACY_BILL"]
    assert result["issues"] == []


def test_image_provider_returns_typed_fields_and_mismatch() -> None:
    provider = StubProvider("HOSPITAL BILL / RECEIPT\nPatient: Arjun Mehta\nConsultation Fee 1500.00\nTotal Amount: 1500.00\nCity Clinic Bengaluru")
    result = process_uploads([{"file_name": "bill.png", "data": image_bytes()}], "DENTAL", "Rajesh Kumar", POLICY, provider)
    assert result["metrics"]["provider_calls"] == 1
    assert result["metrics"]["sarvam_digitise_pages"] == 1
    assert result["metrics"]["sarvam_extract_calls"] == 0
    assert result["documents"][0]["content"]["total"] == 1500
    assert any(issue["code"] == "MEMBER_MISMATCH" and "Arjun Mehta" in issue["message"] for issue in result["issues"])


def test_other_covered_member_is_distinguished_from_selected_member() -> None:
    provider = StubProvider("HOSPITAL BILL / RECEIPT\nPatient: Mr. Arjun Mehta\nConsultation Fee 1500.00\nTotal Amount: 1500.00\nCity Clinic Bengaluru")
    result = process_uploads(
        [{"file_name": "bill.png", "data": image_bytes()}], "DENTAL", "Rajesh Kumar", POLICY,
        provider, allowed_patient_names=["Rajesh Kumar", "Arjun Mehta"],
    )
    assert result["documents"][0]["identity_match"] == "MATCH_OTHER_COVERED_MEMBER"
    assert any(issue["code"] == "OTHER_COVERED_MEMBER" for issue in result["issues"])


def test_document_alteration_and_duplicate_stamp_are_review_signals() -> None:
    provider = StubProvider(
        "HOSPITAL BILL / RECEIPT\nPatient: Rajesh Kumar\nConsultation and charges are in an obscured table.\nCity Clinic Bengaluru",
        {
            "date": "01-Nov-2024", "total": 1500,
            "line_items": [{"description": "Consultation Fee", "amount": 1500}],
            "alteration_detected": True,
            "crossed_out_amount": True,
            "duplicate_stamp_detected": True,
            "alteration_confidence": 0.91,
        },
    )
    result = process_uploads(
        [{"file_name": "altered.png", "data": image_bytes()}],
        "DENTAL", "Rajesh Kumar", POLICY, provider,
    )
    assert {issue["code"] for issue in result["issues"]} >= {"DOCUMENT_ALTERATION", "DUPLICATE_STAMP"}
    assert result["documents"][0]["document_signals"]["alteration_confidence"] == 0.91


def test_blurry_or_tiny_image_gets_specific_correction() -> None:
    result = process_uploads([{"file_name": "small_bill.png", "data": image_bytes(120, 120)}], "DENTAL", "Rajesh Kumar", POLICY)
    assert any(issue["code"] == "UNREADABLE_IMAGE" and "resolution" in issue["message"] and "small_bill.png" in issue["message"] for issue in result["issues"])


def test_provider_failure_is_visible_and_never_fabricates_evidence() -> None:
    class FailingProvider:
        def digitise(self, data: bytes, mime_type: str) -> str:
            raise TimeoutError("provider timeout")

        def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict:
            raise AssertionError("unreachable")

    result = process_uploads([{"file_name": "bill.png", "data": image_bytes()}], "DENTAL", "Rajesh Kumar", POLICY, FailingProvider())
    assert result["metrics"]["provider_failures"] == 1
    issue = next(issue for issue in result["issues"] if issue["code"] == "EXTRACTION_UNAVAILABLE")
    assert issue["provider_reason"] == "TIMEOUT"
    assert result["documents"][0]["content"] == {}
    assert result["documents"][0]["actual_type"] == "UNKNOWN"


def test_provider_setup_failure_falls_back_to_actionable_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_provider_setup() -> None:
        raise RuntimeError("provider setup failed")

    monkeypatch.setenv("SARVAM_API_KEY", "configured")
    monkeypatch.setattr("claims.documents.SarvamDocumentProvider", fail_provider_setup)

    result = process_uploads(
        [{"file_name": "bill.png", "data": image_bytes()}],
        "DENTAL",
        "Rajesh Kumar",
        POLICY,
    )

    assert result["metrics"]["provider_failures"] == 1
    assert any(issue["code"] == "EXTRACTION_UNAVAILABLE" for issue in result["issues"])
    assert result["documents"][0]["warnings"] == ["Document extraction setup failed: PROVIDER_ERROR"]


def test_conflicting_previous_total_routes_to_manual_review() -> None:
    fixture = RECEIPT_OCR_REGRESSION
    provider = StubProvider(
        f"HOSPITAL BILL\nPatient: {fixture['patient_name']}\nDate: {fixture['date']}\n"
        f"Previous Total: {fixture['previous_total']:.2f}\nFinal Total Amount: {fixture['total']:.2f}\n"
        f"Consultation Fee {fixture['total']:.2f}",
    )

    result = process_uploads(
        [{"file_name": "corrected-bill.png", "data": image_bytes()}], "DENTAL", fixture["patient_name"], POLICY, provider
    )

    assert any(issue["code"] == "DOCUMENT_ALTERATION" for issue in result["issues"])
    assert result["documents"][0]["content"]["alteration_detected"] is True


def test_colored_bill_annotation_routes_to_manual_review() -> None:
    fixture = RECEIPT_OCR_REGRESSION
    provider = StubProvider(
        f"HOSPITAL BILL\nPatient: {fixture['patient_name']}\nDate: {fixture['date']}\n"
        f"Consultation Fee {fixture['total']:.2f}\nFinal Total Amount: {fixture['total']:.2f}",
    )

    result = process_uploads(
        [{"file_name": "annotated-bill.png", "data": annotated_image_bytes()}], "DENTAL", fixture["patient_name"], POLICY, provider
    )

    assert any(issue["code"] == "DOCUMENT_ALTERATION" for issue in result["issues"])
    assert result["documents"][0]["content"]["alteration_confidence"] == 0.4


def test_provider_unreadable_bill_names_file_and_type() -> None:
    provider = StubProvider("blur")
    result = process_uploads([{"file_name": "blurry_bill.png", "data": image_bytes()}], "DENTAL", "Priya Singh", POLICY, provider)
    assert any(issue["code"] == "UNREADABLE_DOCUMENT" and "blurry_bill.png" in issue["message"] for issue in result["issues"])


def test_sarvam_extract_only_when_ocr_lacks_material_bill_fields() -> None:
    provider = StubProvider(
        "HOSPITAL BILL / RECEIPT\nPatient: Rajesh Kumar\nDate: 01-Nov-2024\nCity Clinic Bengaluru\nConsultation and CBC charges are in an obscured table.",
        {"total": 1500, "line_items": [{"description": "Consultation Fee", "amount": 1000}, {"description": "CBC Test", "amount": 500}]},
    )
    result = process_uploads([{"file_name": "bill.png", "data": image_bytes()}], "DENTAL", "Rajesh Kumar", POLICY, provider)
    assert result["issues"] == []
    assert result["metrics"]["sarvam_digitise_calls"] == 1
    assert result["metrics"]["sarvam_extract_calls"] == 1
    assert result["metrics"]["sarvam_extract_pages"] == 1
    assert provider.extract_calls == 1
    assert result["documents"][0]["content"]["total"] == 1500


def test_pharmacy_brand_status_is_extracted_only_with_matching_printed_evidence() -> None:
    bill = pdf_bytes([
        "PHARMACY BILL",
        "Patient: Rajesh Kumar",
        "Date: 01-Nov-2024",
        "Brand: Medicine 1500.00",
        "Total Amount: 1500.00",
    ])
    prescription = pdf_bytes([
        "PRESCRIPTION", "Dr. Arun Sharma", "Patient: Rajesh Kumar", "Diagnosis: Viral Fever",
        "Medicines: Paracetamol 650mg and oral rehydration solution as directed by physician.",
    ])
    provider = StubProvider(
        "",
        {
            "total": 1500,
            "line_items": [{
                "description": "Brand: Medicine",
                "amount": 1500,
                "brand_status": "BRANDED",
                "brand_evidence": "Brand",
            }],
        },
    )
    result = process_uploads(
        [{"file_name": "bill.pdf", "data": bill}, {"file_name": "prescription.pdf", "data": prescription}],
        "PHARMACY", "Rajesh Kumar", POLICY, provider,
    )
    assert result["issues"] == []
    pharmacy_bill = next(doc for doc in result["documents"] if doc["actual_type"] == "PHARMACY_BILL")
    assert pharmacy_bill["content"]["line_items"][0]["brand_status"] == "BRANDED"
    assert pharmacy_bill["content"]["line_items"][0]["brand_evidence"] == "Brand"
    assert provider.extract_calls == 1


def test_sarvam_extract_recovers_patient_name_before_identity_gate() -> None:
    provider = StubProvider(
        "HOSPITAL BILL / RECEIPT\nDate: 01-Nov-2024\nConsultation Fee 1500.00\nTotal Amount: 1500.00\nCity Clinic Bengaluru",
        {
            "patient_name": "Rajesh Kumar",
            "total": 1500,
            "line_items": [{"description": "Consultation Fee", "amount": 1500}],
        },
    )
    result = process_uploads(
        [{"file_name": "bill.png", "data": image_bytes()}],
        "DENTAL",
        "Rajesh Kumar",
        POLICY,
        provider,
    )
    assert provider.extract_calls == 1
    assert result["issues"] == []
    assert result["documents"][0]["patient_name_on_doc"] == "Rajesh Kumar"


def test_cross_document_patient_conflict_names_both_files() -> None:
    prescription = pdf_bytes(["PRESCRIPTION", "Dr. Arun Sharma", "Patient: Rajesh Kumar", "Diagnosis: Viral Fever", "Medicines: Paracetamol 650mg"])
    bill = pdf_bytes(["HOSPITAL BILL", "Patient: Arjun Mehta", "Consultation Fee 1500.00", "Total Amount: 1500.00", "City Clinic, Bengaluru"])
    result = process_uploads([{"file_name": "rx.pdf", "data": prescription}, {"file_name": "bill.pdf", "data": bill}], "CONSULTATION", "Rajesh Kumar", POLICY)
    mismatch = next(issue for issue in result["issues"] if issue["code"] == "PATIENT_MISMATCH")
    assert all(value in mismatch["message"] for value in ("rx.pdf", "Rajesh Kumar", "bill.pdf", "Arjun Mehta"))


def test_magic_bytes_reject_mislabelled_upload() -> None:
    result = process_uploads([{"file_name": "bill.pdf", "data": b"fake PDF text"}], "DENTAL", "Priya Singh", POLICY)
    assert result["issues"][0]["code"] == "UNSUPPORTED_FILE"
    assert result["documents"] == []


def test_synthetic_documents_are_real_files(tmp_path: Path) -> None:
    pdf_path, image_path = create_samples(tmp_path)
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    assert image_path.read_bytes().startswith(b"\x89PNG")
    result = process_uploads([{"file_name": pdf_path.name, "data": pdf_path.read_bytes()}], "CONSULTATION", "Rajesh Kumar", POLICY)
    assert result["documents"][0]["actual_type"] == "PRESCRIPTION"
    assert result["documents"][0]["patient_name_on_doc"] == "Rajesh Kumar"


def test_sarvam_sdk_job_contract_and_bounded_download(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDocAI:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def digitise(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append("digitise")
            assert kwargs["output_format"] == "md"
            assert kwargs["language"] == "en-IN"
            return SimpleNamespace(job_id="job-1")

        def get_status(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append("status")
            assert kwargs["job_id"] == "job-1"
            return SimpleNamespace(status="completed")

        def get_download_url(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append("download_url")
            return SimpleNamespace(method="GET", url="https://example.invalid/signed.zip")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("output/document.md", "HOSPITAL BILL\nPatient: Rajesh Kumar\nTotal Amount: 1500.00")
    captured: dict[str, object] = {}

    def fake_urlopen(url: str, *, timeout: int, context: object) -> io.BytesIO:
        captured.update(url=url, timeout=timeout, context=context)
        return io.BytesIO(buffer.getvalue())

    monkeypatch.setattr("claims.documents.urllib.request.urlopen", fake_urlopen)
    provider = SarvamDocumentProvider.__new__(SarvamDocumentProvider)
    api = FakeDocAI()
    provider.client = SimpleNamespace(doc_ai=api)  # type: ignore[assignment]
    text = provider.digitise(image_bytes(), "image/png")
    assert "Patient: Rajesh Kumar" in text
    assert captured["url"] == "https://example.invalid/signed.zip"
    assert captured["timeout"] == 20
    context = captured["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert api.calls == ["digitise", "status", "download_url"]


def test_sarvam_extract_schema_describes_every_nested_field() -> None:
    captured: dict[str, object] = {}

    class FakeDocAI:
        def extract(self, **kwargs: object) -> SimpleNamespace:
            captured["schema"] = json.loads(str(kwargs["schema"]))
            return SimpleNamespace(job_id="job-1")

        def get_status(self, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(status="completed")

        def get_results(self, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(result={})

    provider = SarvamDocumentProvider.__new__(SarvamDocumentProvider)
    provider.client = SimpleNamespace(doc_ai=FakeDocAI())  # type: ignore[assignment]
    provider.extract_fields(image_bytes(), "image/png", "HOSPITAL_BILL")

    def assert_descriptions(node: dict[str, object], nested: bool = False) -> None:
        if nested:
            assert isinstance(node.get("description"), str) and node["description"].strip()
        for field in node.get("properties", {}).values():  # type: ignore[union-attr]
            assert isinstance(field, dict)
            assert isinstance(field.get("description"), str) and field["description"].strip()
            if field.get("type") == "array":
                items = field.get("items")
                assert isinstance(items, dict)
                assert_descriptions(items, nested=True)

    schema = captured["schema"]
    assert isinstance(schema, dict)
    assert_descriptions(schema)


# --- Audit round 2: mandatory bill date, shared helpers, money rounding ---------


def test_undated_or_unreadable_bill_date_is_a_member_correction() -> None:
    bill = pdf_bytes(["HOSPITAL BILL", "Patient: Rajesh Kumar", "Bill No: B-1", "Consultation Fee 1500.00", "Total Amount: 1500.00"])
    result = process_uploads([{"file_name": "bill.pdf", "data": bill}], "DENTAL", "Rajesh Kumar", POLICY)
    assert [(issue["code"], issue.get("field")) for issue in result["issues"]] == [("MATERIAL_FIELD_UNVERIFIED", "date")]
    documents = [{
        "file_name": "bill.pdf", "actual_type": "HOSPITAL_BILL", "quality": "GOOD",
        "content": {"patient_name": "Rajesh Kumar", "date": "sometime in Nov", "total": 1500, "line_items": [{"description": "Consultation", "amount": 1500}]},
    }]
    issues = revalidate_documents(documents, [], "DENTAL", "Rajesh Kumar", POLICY)
    assert [(issue["code"], issue.get("field")) for issue in issues] == [("MATERIAL_FIELD_UNVERIFIED", "date")]
    assert "bill date" in issues[0]["message"]


def test_undated_ocr_bill_asks_structured_extraction_for_the_date() -> None:
    provider = StubProvider(
        "HOSPITAL BILL / RECEIPT\nPatient: Rajesh Kumar\nConsultation Fee 1500.00\nTotal Amount: 1500.00\nCity Clinic Bengaluru",
        {"date": "01/11/2024"},
    )
    result = process_uploads([{"file_name": "bill.png", "data": image_bytes()}], "DENTAL", "Rajesh Kumar", POLICY, provider)
    assert provider.extract_calls == 1
    assert result["issues"] == []
    assert result["documents"][0]["content"]["date"] == "01/11/2024"


def test_document_date_parser_matches_engine_formats() -> None:
    assert parse_document_date("2024-11-01") == date(2024, 11, 1)
    assert parse_document_date("01-Nov-2024") == date(2024, 11, 1)
    assert parse_document_date("01/11/2024") == date(2024, 11, 1)
    assert parse_document_date("") is None
    assert parse_document_date("32/13/2024") is None


def test_shared_name_and_media_type_helpers() -> None:
    assert normal_name("Mr. RAJESH  kumar") == normal_name("rajesh kumar") == "rajesh kumar"
    assert normal_name("Smt. Sunita-Kumar") == "sunita kumar"
    assert sniff_media_type(b"%PDF-1.4") == "application/pdf"
    assert sniff_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_media_type(b"plain text") is None


def test_money_conversion_rounds_half_up_to_paise() -> None:
    assert to_paise("1500") == 150000
    assert to_paise("10.005") == 1001  # truncation would give 1000
    assert to_paise(0.1) == 10
    assert to_paise("1,250.50") == 125050
    assert to_paise("-5", allow_negative=True) == -500
    for bad in ("-5", "abc", "NaN", "Infinity", True, None):
        with pytest.raises(ValueError):
            to_paise(bad)
    assert has_subpaise_precision("10.005") and not has_subpaise_precision("10.05")
    assert to_rupees(150000) == 1500 and isinstance(to_rupees(150000), int)
    assert to_rupees(150050) == 1500.5


def test_bill_arithmetic_uses_half_up_paise() -> None:
    documents = [{
        "file_name": "bill.pdf", "actual_type": "HOSPITAL_BILL", "quality": "GOOD",
        "content": {"patient_name": "Rajesh Kumar", "date": "2024-11-01", "total": "100.005", "line_items": [{"description": "Consultation", "amount": "100.01"}]},
    }]
    assert revalidate_documents(documents, [], "DENTAL", "Rajesh Kumar", POLICY) == []


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("Bill No: INV 2001", "INV 2001"),
        ("Bill No: INV-2001", "INV-2001"),
        ("Invoice No. AB 12 345 Patient: Priya Singh", "AB 12 345"),
        ("Bill No: 2001 Date: 01-Nov-2024", "2001"),
        ("Receipt #: 1001 01-Nov-2024", "1001"),
    ],
)
def test_bill_number_with_internal_spaces_is_captured_whole(printed: str, expected: str) -> None:
    """Audit B-2: "INV 2001" was extracted as "INV", so reformatting a number evaded duplicates."""
    bill = pdf_bytes(["HOSPITAL BILL / RECEIPT", "Patient: Priya Singh", "Date: 15-Oct-2024", printed, "Consultation Fee 800.00", "Total Amount: 800.00"])
    result = process_uploads([{"file_name": "bill.pdf", "data": bill}], "DENTAL", "Priya Singh", POLICY)
    assert result["documents"][0]["content"]["bill_number"] == expected
