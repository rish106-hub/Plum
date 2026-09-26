"""Bounded document intake, evidence extraction, and early correction gate.

The public adapter accepts in-memory uploads and returns plain dictionaries so the
web path and fixture path can feed the same claim evaluator. A document model may
describe visible facts; this module never asks it to decide insurance coverage.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import ssl
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import certifi

MAX_FILES = 10
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 10
MAX_IMAGE_PIXELS = 20_000_000
MAX_OCR_ZIP_BYTES = 8 * 1024 * 1024
VALID_TYPES = {
    "PRESCRIPTION",
    "HOSPITAL_BILL",
    "PHARMACY_BILL",
    "LAB_REPORT",
    "DIAGNOSTIC_REPORT",
    "DENTAL_REPORT",
    "DISCHARGE_SUMMARY",
    "UNKNOWN",
}
TYPE_NAMES = {
    "PRESCRIPTION": "prescription",
    "HOSPITAL_BILL": "hospital or clinic bill",
    "PHARMACY_BILL": "pharmacy bill",
    "LAB_REPORT": "lab report",
    "DIAGNOSTIC_REPORT": "diagnostic report",
    "DENTAL_REPORT": "dental report",
    "DISCHARGE_SUMMARY": "discharge summary",
    "UNKNOWN": "unidentified document",
}


class DocumentProvider(Protocol):
    def digitise(self, data: bytes, mime_type: str) -> str: ...

    def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _Upload:
    file_name: str
    data: bytes
    content_type: str | None


def _issue(code: str, file_name: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "file_name": file_name, "message": message, **extra}


def _normal_name(value: str) -> str:
    return " ".join(
        token for token in re.findall(r"[a-z]+", value.casefold())
        if token not in {"mr", "mrs", "ms", "miss", "dr", "shri", "smt"}
    )


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))


def _evidence_in_text(text: str, evidence: str) -> bool:
    normalized_text = _normalized_words(text)
    normalized_evidence = _normalized_words(evidence)
    return bool(normalized_evidence and f" {normalized_evidence} " in f" {normalized_text} ")


def _safe_name(value: Any) -> str:
    name = str(value or "upload").replace("\\", "/").split("/")[-1]
    return name[:180] or "upload"


def _coerce_upload(raw: Any) -> _Upload:
    if isinstance(raw, dict):
        name = raw.get("file_name") or raw.get("filename") or raw.get("name")
        data = raw.get("data")
        if data is None:
            data = raw.get("content")
        mime = raw.get("content_type") or raw.get("mime_type")
    else:
        name = getattr(raw, "filename", None) or getattr(raw, "file_name", None)
        data = getattr(raw, "data", None)
        mime = getattr(raw, "content_type", None)
    if not isinstance(data, bytes):
        raise TypeError("Upload data must be bytes.")
    return _Upload(_safe_name(name), data, str(mime) if mime else None)


def _mime(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"RIFF",)) and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _pdf_text(data: bytes) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    pages = len(reader.pages)
    if pages > MAX_PDF_PAGES:
        raise ValueError(f"PDF has {pages} pages; the limit is {MAX_PDF_PAGES}.")
    return "\f".join(page.extract_text() or "" for page in reader.pages), pages


def _image_quality(data: bytes) -> tuple[bool, str | None]:
    from PIL import Image, ImageStat, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                return False, f"Image is too large; use an image under {MAX_IMAGE_PIXELS:,} pixels."
            if width < 400 or height < 400:
                return False, "Image resolution is too low to read; upload a clearer photo showing the full page."
            gray = image.convert("L")
            if ImageStat.Stat(gray).stddev[0] < 2:
                return False, "Image has too little contrast to read; retake the photo in even light."
            return True, None
    except (UnidentifiedImageError, OSError, ValueError):
        return False, "Image could not be opened; upload a valid JPEG, PNG, or WebP file."


def _classify_text(text: str) -> str:
    t = text.casefold()
    if re.search(r"discharge\s+summary", t):
        return "DISCHARGE_SUMMARY"
    if re.search(r"(?:pharmacy|chemist|drug\s+lic(?:en[cs]e)?|batch\s+exp)", t) and re.search(r"(?:bill|invoice|receipt|total|net\s+amount)", t):
        return "PHARMACY_BILL"
    if re.search(r"(?:lab\s+report|test\s+result|reference\s+range|normal\s+range|sample\s+(?:id|date)|diagnostic\s+report)", t):
        return "LAB_REPORT" if "diagnostic report" not in t else "DIAGNOSTIC_REPORT"
    if re.search(r"(?:bill|invoice|receipt|subtotal|total\s+amount|net\s+amount)", t) and re.search(r"(?:patient|clinic|hospital|amount|total)", t):
        return "HOSPITAL_BILL"
    if re.search(r"(?:prescription|\brx\b|diagnosis|medicines|investigations)", t) and re.search(r"(?:doctor|\bdr\.?\b|reg\.?\s*no|patient)", t):
        return "PRESCRIPTION"
    return "UNKNOWN"


def _text_content(text: str, kind: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Conservative extraction for clear digital text, with source snippets."""
    content: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]

    def field(name: str, pattern: str, transform: Any = None) -> None:
        for page_line in lines:
            match = re.search(pattern, page_line, re.IGNORECASE)
            if match:
                value: Any = match.group(1).strip(" :;,-")
                if transform:
                    value = transform(value)
                content[name] = value
                evidence.append({"field": name, "source": "pdf_text", "snippet": page_line[:180], "confidence": 0.9})
                return

    field("patient_name", r"(?:patient(?:\s+name)?|name\s+of\s+patient)\s*[:\-]\s*([A-Za-z][A-Za-z .'-]{2,70}?)(?=\s{2,}|\s+Date\s*:|\s+Age\s*:|$)")
    field("doctor_name", r"\b(Dr\.?\s+[A-Za-z][A-Za-z .'-]{2,65})(?=\s{2,}|\s+Reg\.?|\s+MBBS|$)")
    field("doctor_registration", r"\b((?:AYUR/)?(?:KA|MH|DL|TN|GJ|AP|UP|WB|KL)/\d{4,6}/\d{4})\b")
    field("date", r"\b(?:date|bill\s+date|report\s+date)\s*[:\-]\s*(\d{1,2}[-/]\w{2,9}[-/]\d{2,4}|\d{4}-\d{2}-\d{2})")
    field("diagnosis", r"\bdiagnosis\s*[:\-]\s*(.{3,100})")
    field("hospital_name", r"\b(?:hospital|clinic)\s*[:\-]\s*(.{3,90})")
    field("bill_number", r"\b(?:bill|invoice|receipt)\s*(?:no\.?|number|#)\s*[:\-]?\s*([\w/-]{3,40})")
    if kind in {"HOSPITAL_BILL", "PHARMACY_BILL"}:
        field("total", r"\b(?:grand\s+total|total\s+amount|net\s+amount|total)\s*[:₹Rs. ]+([\d,]+(?:\.\d{1,2})?)", _amount)
        items: list[dict[str, Any]] = []
        for line in lines:
            if re.search(r"\b(?:total|subtotal|discount|gst|tax|date|bill\s*no)\b", line, re.IGNORECASE):
                continue
            match = re.match(r"\s*(?:\d+[.)]\s*)?(.{4,70}?)\s+(?:\d+\s+)?(?:₹|Rs\.?\s*)?([\d,]+(?:\.\d{1,2})?)\s*$", line)
            if match:
                amount = _amount(match.group(2))
                if amount is not None and amount > 0:
                    items.append({"description": match.group(1).strip(), "amount": amount})
        if items:
            content["line_items"] = items
            evidence.append({"field": "line_items", "source": "pdf_text", "snippet": f"{len(items)} item lines", "confidence": 0.75})
    if kind in {"LAB_REPORT", "DIAGNOSTIC_REPORT"}:
        field("test_name", r"\b(?:test\s+name|investigation)\s*[:\-]\s*(.{3,90})")
    return content, evidence


def _amount(value: str) -> float | None:
    try:
        return float(Decimal(value.replace(",", "")))
    except InvalidOperation:
        return None


class SarvamDocumentProvider:
    """Sarvam Document AI: cheap Digitise first, Extract only for missing fields."""

    def __init__(self, api_key: str | None = None):
        key = api_key or os.getenv("SARVAM_API_KEY")
        if not key:
            raise RuntimeError("SARVAM_API_KEY is unavailable")
        from sarvamai import SarvamAI

        self.client = SarvamAI(api_subscription_key=key, timeout=20.0)

    @staticmethod
    def _input(data: bytes, mime_type: str) -> tuple[str, io.BytesIO, str]:
        if mime_type == "image/webp":
            from PIL import Image

            image = Image.open(io.BytesIO(data))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            buffer.seek(0)
            return "document.png", buffer, "image/png"
        name = "document.pdf" if mime_type == "application/pdf" else "document.jpg" if mime_type == "image/jpeg" else "document.png"
        return name, io.BytesIO(data), mime_type

    def _await_job(self, job_id: str) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = self.client.doc_ai.get_status(job_id=job_id)
            state = str(status.status).lower()
            if state == "completed":
                return
            if state in {"partially_completed", "failed", "rejected"}:
                raise RuntimeError(f"Sarvam job ended with {state}")
            time.sleep(3)
        raise TimeoutError("Sarvam document job exceeded 90 seconds")

    def digitise(self, data: bytes, mime_type: str) -> str:
        name, payload, actual_mime = self._input(data, mime_type)
        job = self.client.doc_ai.digitise(file=[(name, payload, actual_mime)], language="en-IN", output_format="md")
        self._await_job(str(job.job_id))
        link = self.client.doc_ai.get_download_url(job_id=job.job_id)
        if str(link.method).upper() != "GET" or not str(link.url).startswith("https://"):
            raise ValueError("Sarvam returned an unsupported download URL")
        # Use a maintained CA bundle explicitly. Some macOS Python installs do
        # not initialize the system roots for urllib, which can reject
        # Sarvam's signed Azure Blob result URL after a job has completed.
        tls_context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(str(link.url), timeout=20, context=tls_context) as response:
            archive = response.read(MAX_OCR_ZIP_BYTES + 1)
        if len(archive) > MAX_OCR_ZIP_BYTES:
            raise ValueError("Sarvam OCR archive is too large")
        with zipfile.ZipFile(io.BytesIO(archive)) as result_zip:
            documents = [entry for entry in result_zip.infolist() if entry.filename.lower().endswith(".md") and not entry.is_dir()]
            if not documents or sum(entry.file_size for entry in documents) > MAX_OCR_ZIP_BYTES:
                raise ValueError("Sarvam OCR archive has no bounded Markdown result")
            return "\f".join(result_zip.read(entry).decode("utf-8", errors="replace") for entry in documents)

    def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "patient_name": {"type": "string", "description": "Patient name exactly as printed on the document; empty if unreadable"},
                "doctor_name": {"type": "string", "description": "Treating doctor name exactly as printed; empty if absent"},
                "doctor_registration": {"type": "string", "description": "Doctor registration number exactly as printed; empty if absent"},
                "diagnosis": {"type": "string", "description": "Diagnosis explicitly shown; empty if absent"},
                "treatment": {"type": "string", "description": "Treatment explicitly shown; empty if absent"},
                "date": {"type": "string", "description": "Document date exactly as printed; empty if absent"},
                "hospital_name": {"type": "string", "description": "Hospital, clinic, or provider name exactly as printed; empty if absent"},
                "total": {"type": "number", "description": "Final bill total in Indian rupees; 0 if absent or unreadable"},
                "line_items": {
                    "type": "array",
                    "description": "Each visible billed treatment or product and its price; empty if not itemized",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string", "description": "Line item description exactly as printed"},
                            "amount": {"type": "number", "description": "Line item amount in Indian rupees"},
                            "brand_status": {"type": "string", "enum": ["BRANDED", "GENERIC", "UNKNOWN"], "description": "Use BRANDED or GENERIC only when the bill explicitly identifies that status; otherwise UNKNOWN"},
                            "brand_evidence": {"type": "string", "description": "Exact printed phrase from this line that explicitly identifies the brand status; empty when absent"},
                        },
                    },
                },
            },
        }
        name, payload, actual_mime = self._input(data, mime_type)
        job = self.client.doc_ai.extract(
            file=[(name, payload, actual_mime)], schema=json.dumps(schema), language="en-IN", output_format="json"
        )
        self._await_job(str(job.job_id))
        response = self.client.doc_ai.get_results(job_id=job.job_id)
        result = response.result
        if not isinstance(result, dict):
            raise TypeError("Sarvam extraction returned a non-object")
        return {"document_type": document_type, "quality": "GOOD", "fields": result}


def _provider_result(value: Any) -> tuple[str, str, dict[str, Any], list[str]]:
    if not isinstance(value, dict):
        raise TypeError("Provider result must be an object")
    kind = str(value.get("document_type") or value.get("actual_type") or "UNKNOWN").upper()
    if kind not in VALID_TYPES:
        kind = "UNKNOWN"
    quality = str(value.get("quality") or "PARTIAL").upper()
    if quality not in {"GOOD", "PARTIAL", "UNREADABLE"}:
        quality = "PARTIAL"
    fields: dict[str, Any] = value["fields"] if isinstance(value.get("fields"), dict) else value
    allowed = {"patient_name", "doctor_name", "doctor_registration", "diagnosis", "treatment", "date", "hospital_name", "total", "line_items", "test_name", "medicines", "tests_ordered", "bill_number"}
    content = {key: fields[key] for key in allowed if key in fields and fields[key] not in (None, "")}
    if "total" in content:
        total = _amount(str(content["total"]))
        if total is None or total <= 0:
            content.pop("total")
        else:
            content["total"] = total
    if "line_items" in content:
        clean_items = []
        if isinstance(content["line_items"], list):
            for item in content["line_items"][:30]:
                if isinstance(item, dict) and isinstance(item.get("description"), str):
                    amount = _amount(str(item.get("amount", "")))
                    if amount is not None and amount >= 0:
                        description = item["description"][:160]
                        brand_status = str(item.get("brand_status", "UNKNOWN")).upper()
                        brand_evidence = str(item.get("brand_evidence", ""))[:100]
                        if brand_status not in {"BRANDED", "GENERIC"} or not _evidence_in_text(description, brand_evidence):
                            brand_status, brand_evidence = "UNKNOWN", ""
                        clean_items.append({"description": description, "amount": amount, "brand_status": brand_status, "brand_evidence": brand_evidence})
        content["line_items"] = clean_items
    warnings = value.get("warnings")
    return kind, quality, content, [str(w)[:200] for w in warnings[:10]] if isinstance(warnings, list) else []


def _needs_extract(kind: str, content: dict[str, Any]) -> bool:
    if kind in {"HOSPITAL_BILL", "PHARMACY_BILL"}:
        if "total" not in content or not content.get("line_items"):
            return True
        if kind == "PHARMACY_BILL" and any(item.get("brand_status") not in {"BRANDED", "GENERIC"} for item in content["line_items"]):
            return True
        return abs(sum(item["amount"] for item in content["line_items"]) - content["total"]) > 1
    if kind == "PRESCRIPTION":
        return "diagnosis" not in content
    return False


_DERIVED_ISSUE_CODES = {
    "UNIDENTIFIED_DOCUMENT", "PARTIAL_DOCUMENT", "DETAILS_UNVERIFIED", "AMOUNT_UNVERIFIED",
    "MISSING_DOCUMENT", "PATIENT_MISMATCH", "MEMBER_MISMATCH", "PATIENT_UNVERIFIED",
    "MATERIAL_FIELD_UNVERIFIED", "BILL_ARITHMETIC_CONFLICT",
}


def revalidate_documents(
    documents: list[dict[str, Any]],
    existing_issues: list[dict[str, Any]],
    claim_category: str,
    member_name: str,
    policy: dict[str, Any],
    allowed_patient_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Rebuild evidence-dependent gates after OCR or accepted evidence correction."""
    issues = [issue for issue in existing_issues if issue.get("code") not in _DERIVED_ISSUE_CODES]
    requirements = policy.get("document_requirements", {}).get(claim_category, {})
    actual_good: set[str] = set()
    named: list[tuple[str, str]] = []
    material_fields = {
        "PRESCRIPTION": ("patient_name", "diagnosis"),
        "HOSPITAL_BILL": ("patient_name", "total", "line_items"),
        "PHARMACY_BILL": ("patient_name", "total", "line_items"),
        "LAB_REPORT": ("patient_name", "date", "test_name"),
        "DIAGNOSTIC_REPORT": ("patient_name", "date", "test_name"),
        "DENTAL_REPORT": ("patient_name", "date", "diagnosis"),
    }
    for doc in documents:
        kind = str(doc.get("actual_type") or "UNKNOWN").upper()
        quality = str(doc.get("quality") or "PARTIAL").upper()
        fields = doc.get("content") or {}
        name = str(doc.get("file_name") or doc.get("file_id") or "document")
        if kind == "UNKNOWN":
            issues.append(_issue("UNIDENTIFIED_DOCUMENT", name, f"Could not identify {name} as a medical document. Upload a clear photo or PDF showing the document heading."))
        elif quality == "UNREADABLE":
            issues.append(_issue("UNREADABLE_DOCUMENT", name, f"The {TYPE_NAMES.get(kind, 'document')} in {name} cannot be read. Re-upload a clear image of that document."))
        elif quality == "PARTIAL":
            issues.append(_issue("PARTIAL_DOCUMENT", name, f"The {TYPE_NAMES.get(kind, 'document')} in {name} is partly unreadable. Re-upload the full page with names and amounts visible."))
        elif kind in VALID_TYPES:
            actual_good.add(kind)
        for field in material_fields.get(kind, ()):
            present = bool(fields.get(field))
            if field == "total":
                present = fields.get(field) is not None and fields.get(field) != ""
            if not present:
                messages = {
                    "patient_name": "patient name",
                    "date": "document date",
                    "diagnosis": "diagnosis",
                    "total": "bill total",
                    "line_items": "itemized charges",
                    "test_name": "test name",
                }
                issues.append(_issue(
                    "MATERIAL_FIELD_UNVERIFIED", name,
                    f"The {messages[field]} on {name} could not be verified. Upload a clearer document showing it.",
                    field=field,
                ))
        if kind in {"HOSPITAL_BILL", "PHARMACY_BILL"} and fields.get("total") is not None and fields.get("line_items"):
            try:
                total_paise = int(Decimal(str(fields["total"])) * 100)
                items_paise = sum(int(Decimal(str(item.get("amount"))) * 100) for item in fields["line_items"])
                conflict = total_paise != items_paise
            except (InvalidOperation, TypeError, ValueError):
                conflict = True
            if conflict:
                issues.append(_issue(
                    "BILL_ARITHMETIC_CONFLICT", name,
                    f"The total on {name} does not match its itemized charges. Upload a clearer itemized bill or ask an operator to review it.",
                ))
        patient_name = doc.get("patient_name_on_doc") or fields.get("patient_name")
        if patient_name:
            named.append((name, str(patient_name)))

    for required_type in requirements.get("required", []):
        kind = str(required_type).upper()
        if kind not in actual_good:
            wrong = next((doc for doc in documents if str(doc.get("actual_type") or "UNKNOWN").upper() not in {kind, "UNKNOWN"}), None)
            if wrong:
                wrong_type = str(wrong.get("actual_type") or "UNKNOWN").upper()
                message = f"{wrong.get('file_name', 'The uploaded file')} is a {TYPE_NAMES.get(wrong_type, 'different document')}; this {claim_category.lower()} claim needs a {TYPE_NAMES.get(kind, kind.lower())}. Upload that document."
                file_name = str(wrong.get("file_name") or "")
            else:
                message = f"This {claim_category.lower()} claim needs a {TYPE_NAMES.get(kind, kind.lower())}. Upload that document."
                file_name = ""
            issues.append(_issue("MISSING_DOCUMENT", file_name, message, required_type=kind))

    if len({_normal_name(name) for _, name in named}) > 1:
        detail = "; ".join(f"{filename}: {name}" for filename, name in named)
        issues.append(_issue("PATIENT_MISMATCH", "", f"The uploaded documents name different patients ({detail}). Re-upload documents for {member_name}."))
    elif named and member_name and _normal_name(named[0][1]) not in {
        _normal_name(name) for name in (allowed_patient_names or [member_name])
    }:
        issues.append(_issue("MEMBER_MISMATCH", named[0][0], f"{named[0][0]} names {named[0][1]}, but this claim is not for the member or a covered dependent. Upload the correct patient's document."))
    elif not named:
        issues.append(_issue("PATIENT_UNVERIFIED", "", "No readable patient name was found on the uploaded documents. Upload a document showing the patient's name or request manual review."))
    return issues


def apply_evidence_candidates(
    documents: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    ocr_text_by_file_id: dict[str, str | list[str] | tuple[str, ...]],
) -> list[dict[str, Any]]:
    """Apply only prevalidated evidence candidates, preserving source provenance."""
    import copy

    updated = copy.deepcopy(documents)
    by_id = {str(doc.get("file_id")): doc for doc in updated}
    for candidate in candidates:
        file_id = str(candidate.get("file_id") or "")
        document = by_id.get(file_id)
        fields = candidate.get("fields")
        provenance = candidate.get("evidence")
        if document is None or not isinstance(fields, dict) or not isinstance(provenance, list):
            continue
        content = document.setdefault("content", {})
        kind = fields.get("document_type")
        if kind in VALID_TYPES and kind != "UNKNOWN":
            document["actual_type"] = kind
            pages = ocr_text_by_file_id.get(file_id, "")
            full_text = "\n".join(pages) if isinstance(pages, (list, tuple)) else str(pages).replace("\f", "\n")
            parsed, parsed_evidence = _text_content(full_text, kind)
            for key, value in parsed.items():
                content.setdefault(key, value)
            for item in parsed_evidence:
                document.setdefault("evidence", []).append({**item, "source": "gemini_assisted_local_parse"})
            if document.get("quality") == "PARTIAL":
                document["quality"] = "GOOD"
        safe_fields = {"patient_name", "date", "diagnosis", "hospital_name", "test_name", "total_paise", "line_items"}
        for name, value in fields.items():
            if name not in safe_fields:
                continue
            if name == "total_paise":
                content["total"] = Decimal(int(value)) / Decimal(100)
                evidence_field = "total"
            elif name == "line_items":
                content["line_items"] = [
                    {"description": str(item["description"]), "amount": Decimal(int(item["amount_paise"])) / Decimal(100)}
                    for item in value
                ]
                evidence_field = "line_items"
            else:
                content[name] = value
                evidence_field = name
            for proof in provenance:
                if proof.get("field") == name:
                    document.setdefault("evidence", []).append({
                        "field": evidence_field,
                        "source": "gemini_candidate",
                        "page": int(proof["page"]),
                        "snippet": str(proof["quote"]),
                    })
        document["patient_name_on_doc"] = content.get("patient_name")
        document["extraction_source"] = f"{document.get('extraction_source', 'unknown')}+gemini_candidate"
        document["confidence"] = min(float(document.get("confidence") or 0.35), 0.75)
    return updated


def process_uploads(
    files: list[Any],
    claim_category: str,
    member_name: str,
    policy: dict[str, Any],
    provider: DocumentProvider | None = None,
    allowed_patient_names: list[str] | None = None,
) -> dict[str, Any]:
    """Return ``documents``, actionable ``issues``, and usage ``metrics``.

    Any issue stops adjudication. In particular, a missing provider on a scanned
    file is an explicit inability to verify, never fabricated document evidence.
    """
    documents: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    ocr_text_by_file_id: dict[str, str] = {}
    provider_setup_warning: str | None = None
    metrics: dict[str, Any] = {
        "files": len(files), "pages": 0, "provider_calls": 0, "provider_failures": 0,
        "sarvam_digitise_calls": 0, "sarvam_digitise_pages": 0,
        "sarvam_extract_calls": 0, "sarvam_extract_pages": 0,
    }
    matrix = policy.get("document_requirements", {})
    rules = matrix.get(claim_category)
    if not isinstance(rules, dict):
        return {"documents": [], "issues": [_issue("UNKNOWN_CATEGORY", "", f"No document requirements are defined for {claim_category}.")], "metrics": metrics}
    if not files:
        required = [str(x) for x in rules.get("required", [])]
        return {"documents": [], "issues": [_issue("MISSING_DOCUMENT", "", f"Upload a {TYPE_NAMES.get(kind, kind.lower())} for this {claim_category.lower()} claim.", required_type=kind) for kind in required], "metrics": metrics}
    if len(files) > MAX_FILES:
        return {"documents": [], "issues": [_issue("TOO_MANY_FILES", "", f"Upload at most {MAX_FILES} documents per claim.")], "metrics": metrics}

    if provider is None and os.getenv("SARVAM_API_KEY"):
        try:
            provider = SarvamDocumentProvider()
        except Exception as exc:  # noqa: BLE001 - optional provider setup must not abort intake
            metrics["provider_failures"] += 1
            provider_setup_warning = f"Document extraction setup failed: {type(exc).__name__}"

    for index, raw in enumerate(files, 1):
        try:
            upload = _coerce_upload(raw)
        except (TypeError, ValueError) as exc:
            issues.append(_issue("INVALID_UPLOAD", f"upload {index}", str(exc)))
            continue
        name, data = upload.file_name, upload.data
        if not data:
            issues.append(_issue("EMPTY_FILE", name, f"{name} is empty. Upload a readable PDF or image."))
            continue
        if len(data) > MAX_FILE_BYTES:
            issues.append(_issue("FILE_TOO_LARGE", name, f"{name} exceeds 10 MB. Compress or split it and upload again."))
            continue
        mime = _mime(data)
        if mime is None:
            issues.append(_issue("UNSUPPORTED_FILE", name, f"{name} is not a valid PDF, JPEG, PNG, or WebP file. Upload one of those formats."))
            continue
        digest = hashlib.sha256(data).hexdigest()
        if any(doc.get("sha256") == digest for doc in documents):
            issues.append(_issue("DUPLICATE_DOCUMENT", name, f"{name} duplicates another uploaded document. Upload the missing document type instead."))
            continue
        text = ""
        pages = 1
        if mime == "application/pdf":
            try:
                text, pages = _pdf_text(data)
            except Exception as exc:  # noqa: BLE001 - malformed or encrypted PDFs must not crash intake
                issues.append(_issue("UNREADABLE_PDF", name, f"{name} could not be read ({type(exc).__name__}). Upload a clear PDF or image."))
                continue
        else:
            try:
                good, detail = _image_quality(data)
            except ImportError:
                good, detail = False, "Image validation dependency is unavailable."
            if not good:
                issues.append(_issue("UNREADABLE_IMAGE", name, f"{name}: {detail}"))
                continue
        metrics["pages"] += pages
        kind = "UNKNOWN"
        quality = "PARTIAL"
        content: dict[str, Any] = {}
        evidence: list[dict[str, Any]] = []
        warnings = [provider_setup_warning] if provider_setup_warning else []
        source = "unavailable"
        if len(text.strip()) >= 80:
            kind = _classify_text(text)
            content, evidence = _text_content(text, kind)
            quality = "GOOD" if kind != "UNKNOWN" else "PARTIAL"
            source = "pdf_text"
        if source == "unavailable" and provider:
            try:
                metrics["provider_calls"] += 1
                metrics["sarvam_digitise_calls"] += 1
                metrics["sarvam_digitise_pages"] += pages
                recognized = provider.digitise(data, mime)
                text = recognized
                if len(recognized.strip()) < 30:
                    kind, quality = "UNKNOWN", "UNREADABLE"
                    source = "sarvam_digitise"
                else:
                    kind = _classify_text(recognized)
                    content, evidence = _text_content(recognized, kind)
                    quality = "GOOD" if kind != "UNKNOWN" else "PARTIAL"
                    evidence = [{**entry, "source": "sarvam_digitise", "confidence": min(entry["confidence"], 0.75)} for entry in evidence]
                    source = "sarvam_digitise"
            except Exception as exc:  # noqa: BLE001 - provider/network failures become traceable issues
                metrics["provider_failures"] += 1
                warnings.append(f"Document extraction failed: {type(exc).__name__}")
        if (
            provider
            and source in {"pdf_text", "sarvam_digitise"}
            and kind != "UNKNOWN"
            and quality == "GOOD"
            and (
                not content.get("patient_name")
                or _normal_name(str(content["patient_name"])) in {
                    _normal_name(name) for name in (allowed_patient_names or [member_name])
                }
            )
            and (_needs_extract(kind, content) or not content.get("patient_name"))
        ):
            try:
                metrics["provider_calls"] += 1
                metrics["sarvam_extract_calls"] += 1
                metrics["sarvam_extract_pages"] += pages
                parsed = provider.extract_fields(data, mime, kind)
                _, _, additional, provider_warnings = _provider_result(parsed)
                merged_content = {**additional, **content}
                if kind == "PHARMACY_BILL" and additional.get("line_items") and content.get("line_items"):
                    extracted_by_key = {
                        (_normalized_words(str(item.get("description", ""))), item.get("amount")): item
                        for item in additional["line_items"]
                    }
                    merged_content["line_items"] = [
                        {
                            **local_item,
                            "brand_status": extracted_by_key.get(
                                (_normalized_words(str(local_item.get("description", ""))), local_item.get("amount")), {}
                            ).get("brand_status", "UNKNOWN"),
                            "brand_evidence": extracted_by_key.get(
                                (_normalized_words(str(local_item.get("description", ""))), local_item.get("amount")), {}
                            ).get("brand_evidence", ""),
                        }
                        for local_item in content["line_items"]
                    ]
                content = merged_content
                warnings.extend(provider_warnings)
                evidence.extend(
                    {"field": key, "source": "sarvam_extract", "confidence": 0.75}
                    for key in additional if key not in {entry["field"] for entry in evidence}
                )
                source = "sarvam_extract" if source == "pdf_text" else "sarvam_digitise+extract"
            except Exception as exc:  # noqa: BLE001 - provider/network failures become traceable issues
                metrics["provider_failures"] += 1
                warnings.append(f"Structured extraction failed: {type(exc).__name__}")
        if source == "unavailable":
            issues.append(_issue("EXTRACTION_UNAVAILABLE", name, f"{name} needs image reading, but document extraction is unavailable. Ask an operator to review it or retry when extraction is restored."))
        elif quality == "UNREADABLE":
            descriptor = TYPE_NAMES[kind] if kind != "UNKNOWN" else "document"
            issues.append(_issue("UNREADABLE_DOCUMENT", name, f"The {descriptor} in {name} cannot be read. Re-upload a clear image of that document."))
        elif kind == "UNKNOWN":
            issues.append(_issue("UNIDENTIFIED_DOCUMENT", name, f"Could not identify {name} as a medical document. Upload a clear photo or PDF showing the document heading."))
        elif quality == "PARTIAL":
            issues.append(_issue("PARTIAL_DOCUMENT", name, f"The {TYPE_NAMES[kind]} in {name} is partly unreadable. Re-upload the full page with names and amounts visible."))
        if kind in {"HOSPITAL_BILL", "PHARMACY_BILL"} and not content.get("line_items") and quality == "GOOD":
            issues.append(_issue("DETAILS_UNVERIFIED", name, f"The itemized charges on {name} could not be verified. Upload a clearer bill showing each charged item."))
        doc = {
            "file_id": f"UPLOAD-{index}",
            "file_name": name,
            "sha256": digest,
            "actual_type": kind,
            "quality": quality,
            "patient_name_on_doc": content.get("patient_name"),
            "content": content,
            "evidence": evidence,
            "extraction_source": source,
            "confidence": 0.9 if source == "pdf_text" and quality == "GOOD" else 0.75 if quality == "GOOD" else 0.35,
            "warnings": warnings,
            "pages": pages,
        }
        documents.append(doc)
        if text.strip():
            ocr_text_by_file_id[str(doc["file_id"])] = text
        if kind in {"HOSPITAL_BILL", "PHARMACY_BILL"} and "total" not in content and quality == "GOOD":
            issues.append(_issue("AMOUNT_UNVERIFIED", name, f"The total amount on {name} could not be verified. Upload a clearer bill showing its total."))

    issues = revalidate_documents(documents, issues, claim_category, member_name, policy, allowed_patient_names)
    return {
        "documents": documents,
        "issues": issues,
        "metrics": metrics,
        # Ephemeral hand-off to the claim worker. The caller must remove this
        # value before storing the inspection or serializing any event/result.
        "ocr_text_by_file_id": ocr_text_by_file_id,
    }
