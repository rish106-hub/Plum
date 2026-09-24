from __future__ import annotations

import io
import ssl
import zipfile
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image, ImageDraw

from claims.documents import SarvamDocumentProvider, process_uploads
from tools.generate_samples import create_samples

POLICY = {
    "document_requirements": {
        "CONSULTATION": {"required": ["PRESCRIPTION", "HOSPITAL_BILL"], "optional": []},
        "PHARMACY": {"required": ["PRESCRIPTION", "PHARMACY_BILL"], "optional": []},
        "DENTAL": {"required": ["HOSPITAL_BILL"], "optional": ["DENTAL_REPORT"]},
    }
}


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


def test_image_provider_returns_typed_fields_and_mismatch() -> None:
    provider = StubProvider("HOSPITAL BILL / RECEIPT\nPatient: Arjun Mehta\nConsultation Fee 1500.00\nTotal Amount: 1500.00\nCity Clinic Bengaluru")
    result = process_uploads([{"file_name": "bill.png", "data": image_bytes()}], "DENTAL", "Rajesh Kumar", POLICY, provider)
    assert result["metrics"]["provider_calls"] == 1
    assert result["metrics"]["sarvam_digitise_pages"] == 1
    assert result["metrics"]["sarvam_extract_calls"] == 0
    assert result["documents"][0]["content"]["total"] == 1500
    assert any(issue["code"] == "MEMBER_MISMATCH" and "Arjun Mehta" in issue["message"] and "Rajesh Kumar" in issue["message"] for issue in result["issues"])


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
    assert any(issue["code"] == "EXTRACTION_UNAVAILABLE" for issue in result["issues"])
    assert result["documents"][0]["content"] == {}
    assert result["documents"][0]["actual_type"] == "UNKNOWN"


def test_provider_unreadable_bill_names_file_and_type() -> None:
    provider = StubProvider("blur")
    result = process_uploads([{"file_name": "blurry_bill.png", "data": image_bytes()}], "DENTAL", "Priya Singh", POLICY, provider)
    assert any(issue["code"] == "UNREADABLE_DOCUMENT" and "blurry_bill.png" in issue["message"] for issue in result["issues"])


def test_sarvam_extract_only_when_ocr_lacks_material_bill_fields() -> None:
    provider = StubProvider(
        "HOSPITAL BILL / RECEIPT\nPatient: Rajesh Kumar\nCity Clinic Bengaluru\nConsultation and CBC charges are in an obscured table.",
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
        "HOSPITAL BILL / RECEIPT\nConsultation Fee 1500.00\nTotal Amount: 1500.00\nCity Clinic Bengaluru",
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
    provider.client = SimpleNamespace(doc_ai=api)
    text = provider.digitise(image_bytes(), "image/png")
    assert "Patient: Rajesh Kumar" in text
    assert captured["url"] == "https://example.invalid/signed.zip"
    assert captured["timeout"] == 20
    context = captured["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert api.calls == ["digitise", "status", "download_url"]
