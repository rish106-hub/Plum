"""Bounded document intake, evidence extraction, and early correction gate.

The public adapter accepts in-memory uploads and returns plain dictionaries so the
web path and fixture path can feed the same claim evaluator. A document model may
describe visible facts; this module never asks it to decide insurance coverage.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import ssl
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import certifi

from claims.money import to_paise

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
    "PRE_AUTHORIZATION",
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
    "PRE_AUTHORIZATION": "pre-authorization approval",
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


_HONORIFICS = frozenset({"mr", "mrs", "ms", "miss", "dr", "shri", "smt"})
BILL_TYPES = frozenset({"HOSPITAL_BILL", "PHARMACY_BILL"})
_DOCUMENT_DATE_FORMATS = ("%d-%b-%Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y")


def normal_name(value: Any) -> str:
    """Compare person names case-insensitively, ignoring punctuation and routine Indian honorifics."""
    return " ".join(
        word for word in re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE)
        if word not in _HONORIFICS
    )


def parse_document_date(value: Any) -> date | None:
    """Parse the unambiguous printed-date formats local extraction emits; None if unreadable.

    The accepted formats match the policy engine's document-date parser, so a date
    that passes intake is always comparable to the claimed treatment date.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    for date_format in _DOCUMENT_DATE_FORMATS:
        try:
            return datetime.strptime(raw, date_format).date()
        except ValueError:
            continue
    return None


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


def sniff_media_type(data: bytes) -> str | None:
    """Media type from magic bytes; the client-declared content type is never trusted."""
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
    if re.search(r"(?:pre[- ]?authorization|pre[- ]?auth(?:orisation)?\s+approval)", t):
        return "PRE_AUTHORIZATION"
    if re.search(r"discharge\s+summary", t):
        return "DISCHARGE_SUMMARY"
    if re.search(r"(?:pharmacy|chemist|drug\s+lic(?:en[cs]e)?|batch\s+exp)", t) and re.search(r"(?:bill|invoice|receipt|total|net\s+amount)", t):
        return "PHARMACY_BILL"
    if re.search(r"(?:lab\s+report|test\s+result|reference\s+range|normal\s+range|sample\s+(?:id|date)|diagnostic\s+report)", t):
        return "LAB_REPORT" if "diagnostic report" not in t else "DIAGNOSTIC_REPORT"
    if re.search(r"(?:bill|invoice|receipt|subtotal|total\s+amount|net\s+amount)", t) and re.search(r"(?:patient|clinic|hospital|amount|total)", t):
        return "HOSPITAL_BILL"
    if re.search(
        r"(?:tooth\s*(?:no\.?|number)?\s*\d+|dental\s+(?:report|examination|diagnosis)|odontogram|root\s+canal|periodontal)",
        t,
    ):
        return "DENTAL_REPORT"
    if re.search(r"(?:prescription|\brx\b|diagnosis|medicines|investigations)", t) and re.search(r"(?:doctor|\bdr\.?\b|reg\.?\s*no|patient)", t):
        return "PRESCRIPTION"
    return "UNKNOWN"


def _strip_embedded_images(text: str) -> str:
    """Remove OCR Markdown data URIs before parsing or retaining source text."""
    return re.sub(r"!\[[^\]]*\]\(data:image/[^;\s]+;base64,[^)]+\)", "", text, flags=re.IGNORECASE)


def _html_table_line_items(text: str) -> list[dict[str, Any]]:
    """Read item rows from Sarvam's Markdown HTML tables, if present."""
    items: list[dict[str, Any]] = []
    for table in re.findall(r"<table\b[^>]*>(.*?)</table>", text, flags=re.IGNORECASE | re.DOTALL):
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, flags=re.IGNORECASE | re.DOTALL)
        parsed_rows = [
            [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.IGNORECASE | re.DOTALL)]
            for row in rows
        ]
        description_headers = {"description", "particulars", "service", "services", "procedure", "item"}
        amount_headers = {"amount", "charges", "charge", "value", "price", "net amount", "rate"}
        def header_matches(cell: str, choices: set[str]) -> bool:
            normalized = " ".join(cell.casefold().split())
            return any(normalized == choice or normalized.startswith(f"{choice} ") for choice in choices)
        header_index = next((index for index, row in enumerate(parsed_rows) if any(header_matches(cell, description_headers) for cell in row)), None)
        if header_index is None:
            continue
        header = parsed_rows[header_index]
        description_index = next((index for index, cell in enumerate(header) if header_matches(cell, description_headers)), None)
        amount_index = next((index for index, cell in enumerate(header) if header_matches(cell, amount_headers)), None)
        if description_index is None or amount_index is None:
            continue
        for row in parsed_rows[header_index + 1:]:
            if len(row) <= max(description_index, amount_index):
                continue
            description = " ".join(row[description_index].split())
            amount = _amount(row[amount_index])
            if description and amount is not None and amount > 0:
                items.append({"description": description[:160], "amount": amount})
    return items


_NON_CHARGE_LINE = re.compile(
    r"\b(?:address|road|street|lane|nagar|city|phone|email|gstin|patient\s+name|bill\s*no|receipt\s*no|visit\s+type|department)\b",
    re.IGNORECASE,
)
_CHARGE_CUE = re.compile(
    r"\b(?:consult(?:ation)?|fee|charge|test|medicine|tablet|capsule|syrup|injection|scan|x[ -]?ray|procedure|treatment|service|room|lab|diagnostic)\b",
    re.IGNORECASE,
)


def _loose_line_item(line: str) -> dict[str, Any] | None:
    """Parse a deliberately narrow non-table item line.

    A bare trailing integer is often an address, postal code, account number or
    header value. It is not enough evidence for a financial charge. Tables are
    preferred; this fallback accepts a currency symbol or a decimal amount only.
    """
    if _NON_CHARGE_LINE.search(line):
        return None
    match = re.match(
        r"\s*(?:\d+[.)]\s*)?(.{4,70}?)\s+(?:\d+\s+)?(?:₹|Rs\.?\s*)?([\d,]+(?:\.\d{1,2})?)\s*$",
        line,
    )
    if match is None:
        return None
    raw_amount = match.group(2)
    if (
        "." not in raw_amount
        and not re.search(r"(?:₹|Rs\.?)", line, re.IGNORECASE)
        and not _CHARGE_CUE.search(match.group(1))
    ):
        return None
    amount = _amount(raw_amount)
    if amount is None or amount <= 0:
        return None
    return {"description": match.group(1).strip(), "amount": amount}


def _text_content(text: str, kind: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Conservative extraction for clear digital text, with source snippets."""
    text = _strip_embedded_images(text)
    content: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]

    def field(name: str, pattern: str, transform: Any = None, trim: str = " :;,-") -> None:
        for page_line in lines:
            match = re.search(pattern, page_line, re.IGNORECASE)
            if match:
                value: Any = match.group(1).strip(trim)
                if transform:
                    value = transform(value)
                content[name] = value
                evidence.append({"field": name, "source": "pdf_text", "snippet": page_line[:180], "confidence": 0.9})
                return

    field("patient_name", r"(?:patient(?:\s+name)?|name\s+of\s+patient)\s*[:\-]\s*([A-Za-z][A-Za-z .'-]{2,70}?)(?=\s{2,}|\s+Date\s*:|\s+Age\s*:|$)")
    field("patient_age", r"\b(?:patient\s+)?age\s*[:\-]\s*([0-9]{1,3}(?:\s*(?:years?|yrs?|months?|mos?))?)")
    field("patient_gender", r"\b(?:gender|sex)\s*[:\-]\s*(male|female|other|m|f)\b")
    field("doctor_name", r"\b(Dr\.?\s+[A-Za-z][A-Za-z .'-]{2,65})(?=\s{2,}|\s+Reg\.?|\s+MBBS|$)")
    field("doctor_registration", r"\b((?:AYUR/)?(?:KA|MH|DL|TN|GJ|AP|UP|WB|KL)/\d{4,6}/\d{4})\b")
    field("doctor_specialization", r"\b(?:MBBS|MD|MS)\s*\(?([A-Za-z][A-Za-z ]{2,50})\)?")
    field("approval_reference", r"\b(?:approval|authorization|pre[- ]?auth)\s*(?:ref(?:erence)?|no\.?|number|#)\s*[:\-]?\s*([\w/-]{3,60})")
    field("approved_amount", r"\b(?:approved|authorized)\s+amount\s*[:₹Rs. ]+([\d,]+(?:\.\d{1,2})?)", _amount)
    field("date", r"\b(?:date|bill\s+date|report\s+date)\s*[:\-]\s*(\d{1,2}[-/]\w{2,9}[-/]\d{2,4}|\d{4}-\d{2}-\d{2})")
    field("sample_date", r"\bsample\s+date\s*[:\-]\s*(\d{1,2}[-/]\w{2,9}[-/]\d{2,4}|\d{4}-\d{2}-\d{2})")
    field("report_date", r"\breport\s+date\s*[:\-]\s*(\d{1,2}[-/]\w{2,9}[-/]\d{2,4}|\d{4}-\d{2}-\d{2})")
    field("diagnosis", r"\bdiagnosis\s*[:\-]\s*(.{3,100})")
    field("hospital_name", r"\b(?:hospital|clinic)\s*[:\-]\s*(.{3,90})")
    # A bill number may be printed with internal spaces ("INV 2001"). Continuation
    # tokens must carry a digit, so a following label ("Date:", "Patient") or a
    # printed date is never absorbed into the number.
    field(
        "bill_number",
        r"\b(?:bill|invoice|receipt)\s*(?:no\.?|number|#)\s*[:\-]?\s*"
        r"((?:[\w/-]{3,40}|[\w/-]{1,2}(?=\s[\w/-]*\d))(?:\s(?!\d{1,2}[-/]\w{2,9}[-/]\d{2,4}\b)[\w/-]*\d[\w/-]*){0,4})",
    )
    field("gstin", r"\bGSTIN\s*[:\-]?\s*([A-Z0-9]{10,20})")
    field("drug_license_number", r"\bdrug\s+lic(?:en[cs]e)?\s*(?:no\.?|number|#)?\s*[:\-]?\s*([\w/-]{3,50})")
    if kind == "PRESCRIPTION":
        for output_field, label in (("medicines", r"medicines?|medications?"), ("tests_ordered", r"investigations?|tests?\s+ordered")):
            for page_line in lines:
                match = re.search(rf"\b(?:{label})\s*[:\-]\s*(.{{3,300}})", page_line, re.IGNORECASE)
                if match:
                    values = [value.strip(" .") for value in re.split(r"\s*(?:,|;|\band\b)\s*", match.group(1), flags=re.IGNORECASE) if value.strip(" .")]
                    if values:
                        content[output_field] = values
                        evidence.append({"field": output_field, "source": "pdf_text", "snippet": page_line[:180], "confidence": 0.85})
                    break
    if kind in {"HOSPITAL_BILL", "PHARMACY_BILL"}:
        field("subtotal", r"\bsub\s*-?\s*total\s*[:₹Rs. ]+([\d,]+(?:\.\d{1,2})?)", _amount)
        field("discount", r"\b(?:discount|less)\s*[:₹Rs. -]+([\d,]+(?:\.\d{1,2})?)", _amount)
        # A printed GST total takes precedence over its split Indian tax
        # components. Keep the components as evidence, but never add both the
        # combined total and CGST/SGST/IGST during reconciliation.
        field("gst_amount", r"\b(?:gst|total\s+gst|total\s+tax|tax)\b(?:\s*@\s*\d+(?:\.\d+)?%?)?\s*[:₹Rs. ]+([\d,]+(?:\.\d{1,2})?)", _amount)
        field("cgst_amount", r"\bcgst\b(?:\s*@\s*\d+(?:\.\d+)?%?)?\s*[:₹Rs. ]+([\d,]+(?:\.\d{1,2})?)", _amount)
        field("sgst_amount", r"\bsgst\b(?:\s*@\s*\d+(?:\.\d+)?%?)?\s*[:₹Rs. ]+([\d,]+(?:\.\d{1,2})?)", _amount)
        field("igst_amount", r"\bigst\b(?:\s*@\s*\d+(?:\.\d+)?%?)?\s*[:₹Rs. ]+([\d,]+(?:\.\d{1,2})?)", _amount)
        field("round_off", r"\bround\s*-?\s*off\s*[:₹Rs. ]*([+-]?[\d,]+(?:\.\d{1,2})?)", _amount, " :;,")
        field("total", r"\b(?:final\s+total(?:\s+amount)?|grand\s+total|net\s+amount|total\s+amount)\s*[:₹Rs. ]+([\d,]+(?:\.\d{1,2})?)", _amount)
        if "total" not in content:
            field("total", r"\btotal\s*[:₹Rs. ]+([\d,]+(?:\.\d{1,2})?)", _amount)
        items = _html_table_line_items(text)
        if not items:
            for line in lines:
                if "<table" in line.casefold() or re.search(r"\b(?:total|subtotal|discount|(?:c|s|i)?gst|tax|date|bill\s*no)\b", line, re.IGNORECASE):
                    continue
                item = _loose_line_item(line)
                if item is not None:
                    items.append(item)
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


def _reconciles_bill_arithmetic(fields: dict[str, Any]) -> bool:
    """Reconcile itemized bill arithmetic, including printed tax and reductions.

    A subtotal is informative, but a final amount is only accepted when the item
    lines reconcile after the bill's explicit discount, GST/tax, and round-off.
    Missing adjustment fields mean zero; an unreadable adjustment is not guessed.
    """
    items, total = fields.get("line_items"), fields.get("total")
    if not isinstance(items, list) or not items or total is None:
        return False
    try:
        item_total = sum(to_paise(item.get("amount")) for item in items if isinstance(item, dict))
        if "subtotal" in fields and to_paise(fields["subtotal"]) != item_total:
            return False
        combined_tax = to_paise(fields.get("gst_amount", 0))
        split_tax = sum(to_paise(fields.get(component, 0)) for component in ("cgst_amount", "sgst_amount", "igst_amount"))
        # Structured providers use zero for an absent optional number. Treat a
        # non-zero printed combined GST as authoritative; otherwise use the
        # split components. This also leaves genuinely zero-rated bills at zero.
        tax = combined_tax if combined_tax else split_tax
        adjustments = tax - to_paise(fields.get("discount", 0))
        adjustments += to_paise(fields.get("round_off", 0), allow_negative=True)
        return item_total + adjustments == to_paise(total)
    except (InvalidOperation, TypeError, ValueError):
        return False


def _duplicate_stamp_in_text(text: str) -> bool:
    """Recognise explicit duplicate/reprint contexts, never ordinary copy labels."""
    normalized = _normalized_words(text)
    return bool(re.search(r"\b(?:duplicate(?:\s+bill)?|copy\s+duplicate|reprint)\b", normalized))


def _has_conflicting_previous_total(text: str, total: Any) -> bool:
    """Flag a prior-total value that conflicts with the claimed final total.

    This is intentionally narrow: a receipt that labels a different amount as
    ``Previous Total`` can reflect a crossed-out or handwritten correction. It
    is a review signal, not a fraud determination.
    """
    if total is None:
        return False
    match = re.search(
        r"\bprevious\s+total\b[^\d₹]{0,24}(?:₹|Rs\.?\s*)?([\d,]+(?:\.\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if match is None:
        return False
    previous = _amount(match.group(1))
    try:
        return previous is not None and to_paise(previous) != to_paise(total)
    except (InvalidOperation, TypeError, ValueError):
        return False


def _has_colored_bill_annotation(data: bytes) -> bool:
    """Detect likely blue/purple pen marks on a photographed bill.

    It is deliberately a review-only signal. We cannot reliably distinguish a
    handwritten correction from a signature or stamp without field bounding
    boxes, so any material coloured annotation in the bill body holds the claim
    for an operator instead of making a financial decision.
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as source:
            image = source.convert("RGB")
            image.thumbnail((900, 1200))
            top, bottom = int(image.height * 0.12), int(image.height * 0.85)
            pixels = list(image.crop((0, top, image.width, bottom)).getdata())
    except (OSError, ValueError):
        return False
    if not pixels:
        return False
    annotation_pixels = sum(
        blue >= red + 20 and blue >= green + 10 and blue >= 60
        for red, green, blue in pixels
    )
    return annotation_pixels >= max(12, int(len(pixels) * 0.0002))


def _provider_failure_reason(exc: Exception) -> str:
    """Classify provider failures without retaining provider response text."""
    body = getattr(exc, "body", None)
    code = str(body.get("code", "")) if isinstance(body, dict) else ""
    status = getattr(exc, "status_code", None)
    message = str(exc).casefold()
    if code == "SCHEMA_INVALID" or "schema_invalid" in message:
        return "SCHEMA_INVALID"
    if status in {401, 403} or "authentication" in message or "api key" in message:
        return "AUTHENTICATION_FAILED"
    if status == 429 or "rate limit" in message:
        return "RATE_LIMITED"
    if isinstance(exc, TimeoutError) or "timeout" in message or "timed out" in message:
        return "TIMEOUT"
    if isinstance(exc, ConnectionError) or "connection" in message:
        return "CONNECTION_ERROR"
    if status is not None and int(status) >= 500:
        return "PROVIDER_UNAVAILABLE"
    return "PROVIDER_ERROR"


_PROVIDER_FAILURE_MESSAGES = {
    "SCHEMA_INVALID": "the extraction schema needs a service-configuration fix",
    "AUTHENTICATION_FAILED": "provider authentication needs configuration",
    "RATE_LIMITED": "the provider is rate-limiting requests",
    "TIMEOUT": "the provider did not finish in time",
    "CONNECTION_ERROR": "the provider could not be reached",
    "PROVIDER_UNAVAILABLE": "the provider is temporarily unavailable",
    "PROVIDER_ERROR": "the provider returned an unexpected error",
    "PROVIDER_NOT_CONFIGURED": "document extraction is not configured",
}


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
                "patient_age": {"type": "string", "description": "Patient age exactly as printed, including its unit when shown; empty if absent"},
                "patient_gender": {"type": "string", "description": "Patient gender or sex exactly as printed; empty if absent"},
                "doctor_name": {"type": "string", "description": "Treating doctor name exactly as printed; empty if absent"},
                "doctor_registration": {"type": "string", "description": "Doctor registration number exactly as printed; empty if absent"},
                "doctor_specialization": {"type": "string", "description": "Doctor specialization exactly as printed; empty if absent"},
                "diagnosis": {"type": "string", "description": "Diagnosis explicitly shown; empty if absent"},
                "treatment": {"type": "string", "description": "Treatment explicitly shown; empty if absent"},
                "date": {"type": "string", "description": "Document date exactly as printed; empty if absent"},
                "sample_date": {"type": "string", "description": "Lab sample date exactly as printed; empty if absent"},
                "report_date": {"type": "string", "description": "Lab report date exactly as printed; empty if absent"},
                "hospital_name": {"type": "string", "description": "Hospital, clinic, or provider name exactly as printed; empty if absent"},
                "provider_address": {"type": "string", "description": "Clinic, hospital, lab, or pharmacy address exactly as printed; empty if absent"},
                "bill_number": {"type": "string", "description": "Bill or receipt number exactly as printed; empty if absent"},
                "approved_amount": {"type": "number", "description": "Pre-authorization approved amount in Indian rupees; 0 if absent or unreadable"},
                "gstin": {"type": "string", "description": "GSTIN exactly as printed; empty if absent"},
                "nabl_status": {"type": "string", "description": "NABL accreditation status exactly as printed; empty if absent"},
                "pathologist_name": {"type": "string", "description": "Pathologist name exactly as printed; empty if absent"},
                "pathologist_registration": {"type": "string", "description": "Pathologist registration exactly as printed; empty if absent"},
                "drug_license_number": {"type": "string", "description": "Pharmacy drug licence number exactly as printed; empty if absent"},
                "subtotal": {"type": "number", "description": "Bill subtotal before discounts, tax, and round-off in Indian rupees; 0 if absent"},
                "discount": {"type": "number", "description": "Bill discount in Indian rupees; 0 if absent"},
                "gst_amount": {"type": "number", "description": "Combined or total GST amount in Indian rupees; 0 if no combined total is printed"},
                "cgst_amount": {"type": "number", "description": "CGST amount in Indian rupees; 0 if absent"},
                "sgst_amount": {"type": "number", "description": "SGST amount in Indian rupees; 0 if absent"},
                "igst_amount": {"type": "number", "description": "IGST amount in Indian rupees; 0 if absent"},
                "round_off": {"type": "number", "description": "Signed bill round-off adjustment in Indian rupees; 0 if absent"},
                "alteration_detected": {"type": "boolean", "description": "True only when a crossed-out, overwritten, erased, or handwritten-corrected financial amount is visibly present"},
                "crossed_out_amount": {"type": "boolean", "description": "True only when a financial amount is visibly crossed out"},
                "handwritten_amount_correction": {"type": "boolean", "description": "True only when a financial amount has a visible handwritten correction"},
                "duplicate_stamp_detected": {"type": "boolean", "description": "True only for a visible DUPLICATE, DUPLICATE BILL, COPY/DUPLICATE, or REPRINT stamp. False for ordinary labels such as Customer Copy, Patient Copy, or Office Copy."},
                "original_stamp_detected": {"type": "boolean", "description": "True only when an ORIGINAL stamp is visibly present"},
                "alteration_confidence": {"type": "number", "description": "Confidence from 0 to 1 for the visible alteration signals; 0 when none"},
                "test_results": {
                    "type": "array",
                    "description": "Visible lab results only; empty if absent",
                    "items": {
                        "type": "object",
                        "description": "One visible laboratory test result",
                        "properties": {
                            "test_name": {"type": "string", "description": "Test name exactly as printed"},
                            "result": {"type": "string", "description": "Test result exactly as printed"},
                            "unit": {"type": "string", "description": "Result unit exactly as printed"},
                            "reference_range": {"type": "string", "description": "Reference range exactly as printed"},
                        },
                    },
                },
                "test_name": {"type": "string", "description": "Primary test or investigation name exactly as printed; empty if absent"},
                "tests_ordered": {
                    "type": "array",
                    "description": "Investigations or tests explicitly ordered on a prescription; empty if absent",
                    "items": {"type": "string", "description": "One ordered test exactly as printed"},
                },
                "medicines": {
                    "type": "array",
                    "description": "Medicines explicitly prescribed, including strength, dosage, and duration when printed; empty if absent",
                    "items": {"type": "string", "description": "One prescribed medicine exactly as printed"},
                },
                "total": {"type": "number", "description": "Final bill total in Indian rupees; 0 if absent or unreadable"},
                "line_items": {
                    "type": "array",
                    "description": "Each visible billed treatment or product and its price; empty if not itemized",
                    "items": {
                        "type": "object",
                        "description": "One visible itemized treatment or product charge",
                        "properties": {
                            "description": {"type": "string", "description": "Line item description exactly as printed"},
                            "amount": {"type": "number", "description": "Line item amount in Indian rupees"},
                            "brand_status": {"type": "string", "enum": ["BRANDED", "GENERIC", "UNKNOWN"], "description": "Use BRANDED or GENERIC only when the bill explicitly identifies that status; otherwise UNKNOWN"},
                            "brand_evidence": {"type": "string", "description": "Exact printed phrase from this line that explicitly identifies the brand status; empty when absent"},
                            "batch_number": {"type": "string", "description": "Medicine batch exactly as printed; empty if absent"},
                            "expiry": {"type": "string", "description": "Medicine expiry exactly as printed; empty if absent"},
                            "quantity": {"type": "number", "description": "Visible quantity; 0 if absent"},
                            "mrp": {"type": "number", "description": "Visible MRP in Indian rupees; 0 if absent"},
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
    allowed = {"patient_name", "patient_age", "patient_gender", "doctor_name", "doctor_registration", "doctor_specialization", "diagnosis", "treatment", "date", "sample_date", "report_date", "hospital_name", "provider_address", "total", "subtotal", "round_off", "approved_amount", "line_items", "test_name", "test_results", "medicines", "tests_ordered", "bill_number", "approval_reference", "gstin", "gst_amount", "cgst_amount", "sgst_amount", "igst_amount", "nabl_status", "pathologist_name", "pathologist_registration", "drug_license_number", "discount", "alteration_detected", "crossed_out_amount", "handwritten_amount_correction", "duplicate_stamp_detected", "original_stamp_detected", "alteration_confidence"}
    content = {key: fields[key] for key in allowed if key in fields and fields[key] not in (None, "")}
    for flag in ("alteration_detected", "crossed_out_amount", "handwritten_amount_correction", "duplicate_stamp_detected", "original_stamp_detected"):
        if flag in content:
            content[flag] = content[flag] is True
    if "alteration_confidence" in content:
        try:
            content["alteration_confidence"] = min(1.0, max(0.0, float(content["alteration_confidence"])))
        except (TypeError, ValueError):
            content.pop("alteration_confidence")
    if "total" in content:
        total = _amount(str(content["total"]))
        if total is None or total <= 0:
            content.pop("total")
        else:
            content["total"] = total
    if "approved_amount" in content:
        approved = _amount(str(content["approved_amount"]))
        if approved is None or approved <= 0:
            content.pop("approved_amount")
        else:
            content["approved_amount"] = approved
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
                        clean_item = {"description": description, "amount": amount, "brand_status": brand_status, "brand_evidence": brand_evidence}
                        for field in ("batch_number", "expiry"):
                            if isinstance(item.get(field), str) and item[field].strip():
                                clean_item[field] = item[field].strip()[:100]
                        for field in ("quantity", "mrp"):
                            number = _amount(str(item.get(field, "")))
                            if number is not None and number >= 0:
                                clean_item[field] = number
                        clean_items.append(clean_item)
        content["line_items"] = clean_items
    for field in ("medicines", "tests_ordered"):
        if field in content:
            content[field] = [str(item).strip()[:200] for item in content[field][:50] if isinstance(item, str) and item.strip()] if isinstance(content[field], list) else []
    warnings = value.get("warnings")
    return kind, quality, content, [str(w)[:200] for w in warnings[:10]] if isinstance(warnings, list) else []


MATERIAL_FIELDS: dict[str, tuple[str, ...]] = {
    "PRESCRIPTION": ("patient_name", "diagnosis"),
    # Printed bill dates anchor both the treatment episode and duplicate checks.
    "HOSPITAL_BILL": ("patient_name", "date", "total", "line_items"),
    "PHARMACY_BILL": ("patient_name", "date", "total", "line_items"),
    "LAB_REPORT": ("patient_name", "date", "test_name"),
    "DIAGNOSTIC_REPORT": ("patient_name", "date", "test_name"),
    "DENTAL_REPORT": ("patient_name", "date", "diagnosis"),
}


def _material_field_present(kind: str, field: str, content: dict[str, Any]) -> bool:
    value = content.get(field)
    if field == "total":
        return value is not None and value != ""
    return bool(value)


def _needs_extract(kind: str, content: dict[str, Any]) -> bool:
    if any(not _material_field_present(kind, field, content) for field in MATERIAL_FIELDS.get(kind, ())):
        return True
    if kind in BILL_TYPES:
        if parse_document_date(content.get("date")) is None:
            return True
        if kind == "PHARMACY_BILL" and any(item.get("brand_status") not in {"BRANDED", "GENERIC"} for item in content["line_items"]):
            return True
        return not _reconciles_bill_arithmetic(content)
    if kind == "PRESCRIPTION":
        return not content.get("medicines") and not content.get("tests_ordered")
    return False


_DERIVED_ISSUE_CODES = {
    "UNIDENTIFIED_DOCUMENT", "PARTIAL_DOCUMENT", "DETAILS_UNVERIFIED", "AMOUNT_UNVERIFIED",
    "MISSING_DOCUMENT", "PATIENT_MISMATCH", "MEMBER_MISMATCH", "PATIENT_UNVERIFIED",
    "MATERIAL_FIELD_UNVERIFIED", "BILL_ARITHMETIC_CONFLICT", "OTHER_COVERED_MEMBER",
    "DOCUMENT_ALTERATION", "DUPLICATE_STAMP",
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
        for field in MATERIAL_FIELDS.get(kind, ()):
            present = _material_field_present(kind, field, fields)
            if field == "date" and present and kind in BILL_TYPES and parse_document_date(fields.get("date")) is None:
                issues.append(_issue(
                    "MATERIAL_FIELD_UNVERIFIED", name,
                    f"The bill date on {name} could not be read. Upload a clearer bill showing its date.",
                    field="date",
                ))
                continue
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
                conflict = not _reconciles_bill_arithmetic(fields)
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
        selected = normal_name(member_name)
        roster = {normal_name(value) for value in (allowed_patient_names or [member_name]) if normal_name(value)}
        observed = normal_name(patient_name)
        doc["identity_match"] = (
            "MATCH_SELECTED_MEMBER" if observed and observed == selected
            else "MATCH_OTHER_COVERED_MEMBER" if observed and observed in roster
            else "UNKNOWN_PATIENT" if observed
            else "NOT_AVAILABLE"
        )
        if kind in BILL_TYPES:
            signals = {
                key: fields.get(key)
                for key in (
                    "alteration_detected", "crossed_out_amount", "handwritten_amount_correction",
                    "duplicate_stamp_detected", "original_stamp_detected", "alteration_confidence",
                )
                if fields.get(key) not in (None, False, "")
            }
            doc["document_signals"] = signals
            if any(fields.get(key) is True for key in ("alteration_detected", "crossed_out_amount", "handwritten_amount_correction")):
                issues.append(_issue(
                    "DOCUMENT_ALTERATION", name,
                    f"{name} shows a material financial alteration. An operator must inspect the original document before payment.",
                    signals=signals,
                ))
            if fields.get("duplicate_stamp_detected") is True:
                issues.append(_issue(
                    "DUPLICATE_STAMP", name,
                    f"{name} is marked DUPLICATE or COPY. An operator must confirm that it has not already been reimbursed.",
                    signals=signals,
                ))

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

    if len({normal_name(name) for _, name in named}) > 1:
        detail = "; ".join(f"{filename}: {name}" for filename, name in named)
        issues.append(_issue("PATIENT_MISMATCH", "", f"The uploaded documents name different patients ({detail}). Re-upload documents for {member_name}."))
    elif named and member_name and normal_name(named[0][1]) != normal_name(member_name):
        covered = normal_name(named[0][1]) in {normal_name(name) for name in (allowed_patient_names or [member_name])}
        if covered:
            issues.append(_issue(
                "OTHER_COVERED_MEMBER", named[0][0],
                f"{named[0][0]} names {named[0][1]}, another covered family member. Submit this claim under that patient's member ID.",
                identity_match="MATCH_OTHER_COVERED_MEMBER",
            ))
        else:
            issues.append(_issue("MEMBER_MISMATCH", named[0][0], f"{named[0][0]} names {named[0][1]}, but this claim is not for the selected member or a covered dependent. Upload the correct patient's document."))
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
    provider_setup_reason: str | None = None
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
            provider_setup_reason = _provider_failure_reason(exc)
            provider_setup_warning = f"Document extraction setup failed: {provider_setup_reason}"

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
        mime = sniff_media_type(data)
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
        provider_failure_reason = provider_setup_reason
        source = "unavailable"
        text = _strip_embedded_images(text)
        if len(text.strip()) >= 80:
            kind = _classify_text(text)
            content, evidence = _text_content(text, kind)
            if kind in BILL_TYPES:
                normalized_text = _normalized_words(text)
                content["duplicate_stamp_detected"] = _duplicate_stamp_in_text(normalized_text)
                content["original_stamp_detected"] = bool(re.search(r"\boriginal\b", normalized_text))
            quality = "GOOD" if kind != "UNKNOWN" else "PARTIAL"
            source = "pdf_text"
        if source == "unavailable" and provider:
            try:
                metrics["provider_calls"] += 1
                metrics["sarvam_digitise_calls"] += 1
                metrics["sarvam_digitise_pages"] += pages
                recognized = _strip_embedded_images(provider.digitise(data, mime))
                text = recognized
                if len(recognized.strip()) < 30:
                    kind, quality = "UNKNOWN", "UNREADABLE"
                    source = "sarvam_digitise"
                else:
                    kind = _classify_text(recognized)
                    content, evidence = _text_content(recognized, kind)
                    if kind in BILL_TYPES:
                        normalized_text = _normalized_words(recognized)
                        content["duplicate_stamp_detected"] = _duplicate_stamp_in_text(normalized_text)
                        content["original_stamp_detected"] = bool(re.search(r"\boriginal\b", normalized_text))
                    quality = "GOOD" if kind != "UNKNOWN" else "PARTIAL"
                    evidence = [{**entry, "source": "sarvam_digitise", "confidence": min(entry["confidence"], 0.75)} for entry in evidence]
                    source = "sarvam_digitise"
            except Exception as exc:  # noqa: BLE001 - provider/network failures become traceable issues
                metrics["provider_failures"] += 1
                provider_failure_reason = _provider_failure_reason(exc)
                warnings.append(f"Document extraction failed: {provider_failure_reason}")
        if (
            provider
            and source in {"pdf_text", "sarvam_digitise"}
            and kind != "UNKNOWN"
            and quality == "GOOD"
            and (
                not content.get("patient_name")
                or normal_name(str(content["patient_name"])) in {
                    normal_name(name) for name in (allowed_patient_names or [member_name])
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
                extracted_total = additional.get("total")
                local_total = content.get("total")
                if kind in BILL_TYPES and _reconciles_bill_arithmetic(additional):
                    try:
                        if local_total is None or to_paise(local_total) == to_paise(extracted_total):
                            merged_content["line_items"] = additional["line_items"]
                    except (InvalidOperation, TypeError, ValueError):
                        pass
                for signal in (
                    "alteration_detected", "crossed_out_amount", "handwritten_amount_correction",
                    "duplicate_stamp_detected", "original_stamp_detected",
                ):
                    if additional.get(signal) is True:
                        merged_content[signal] = True
                if "alteration_confidence" in additional:
                    merged_content["alteration_confidence"] = max(
                        float(content.get("alteration_confidence") or 0), float(additional["alteration_confidence"])
                    )
                if kind == "PHARMACY_BILL" and additional.get("line_items") and content.get("line_items"):
                    extracted_by_key = {
                        (_normalized_words(str(item.get("description", ""))), item.get("amount")): item
                        for item in additional["line_items"]
                    }
                    local_items_with_brand_evidence = [
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
                    if not _reconciles_bill_arithmetic(merged_content):
                        merged_content["line_items"] = local_items_with_brand_evidence
                content = merged_content
                warnings.extend(provider_warnings)
                evidence.extend(
                    {"field": key, "source": "sarvam_extract", "confidence": 0.75}
                    for key in additional if key not in {entry["field"] for entry in evidence}
                )
                source = "sarvam_extract" if source == "pdf_text" else "sarvam_digitise+extract"
            except Exception as exc:  # noqa: BLE001 - provider/network failures become traceable issues
                metrics["provider_failures"] += 1
                provider_failure_reason = _provider_failure_reason(exc)
                warnings.append(f"Structured extraction failed: {provider_failure_reason}")
        if source == "unavailable":
            reason = provider_failure_reason or "PROVIDER_NOT_CONFIGURED"
            issues.append(_issue(
                "EXTRACTION_UNAVAILABLE", name,
                f"{name} needs image reading, but document extraction is unavailable because {_PROVIDER_FAILURE_MESSAGES[reason]}. Ask an operator to review it or retry later.",
                provider_reason=reason,
            ))
        elif any(warning.startswith("Structured extraction failed:") for warning in warnings):
            reason = provider_failure_reason or "PROVIDER_ERROR"
            issues.append(_issue(
                "EXTRACTION_UNAVAILABLE", name,
                f"Structured extraction for {name} is unavailable because {_PROVIDER_FAILURE_MESSAGES[reason]}. An operator must inspect the uploaded document or retry later.",
                provider_reason=reason,
            ))
        elif quality == "UNREADABLE":
            descriptor = TYPE_NAMES[kind] if kind != "UNKNOWN" else "document"
            issues.append(_issue("UNREADABLE_DOCUMENT", name, f"The {descriptor} in {name} cannot be read. Re-upload a clear image of that document."))
        elif kind == "UNKNOWN":
            issues.append(_issue("UNIDENTIFIED_DOCUMENT", name, f"Could not identify {name} as a medical document. Upload a clear photo or PDF showing the document heading."))
        elif quality == "PARTIAL":
            issues.append(_issue("PARTIAL_DOCUMENT", name, f"The {TYPE_NAMES[kind]} in {name} is partly unreadable. Re-upload the full page with names and amounts visible."))
        if kind in {"HOSPITAL_BILL", "PHARMACY_BILL"} and not content.get("line_items") and quality == "GOOD":
            issues.append(_issue("DETAILS_UNVERIFIED", name, f"The itemized charges on {name} could not be verified. Upload a clearer bill showing each charged item."))
        if kind in BILL_TYPES and _has_conflicting_previous_total(text, content.get("total")):
            content["alteration_detected"] = True
            content["alteration_confidence"] = max(float(content.get("alteration_confidence") or 0), 0.6)
        if kind in BILL_TYPES and _has_colored_bill_annotation(data):
            content["alteration_detected"] = True
            content["alteration_confidence"] = max(float(content.get("alteration_confidence") or 0), 0.4)
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
