"""Document benchmarks: offline intake/routing and a provider-backed OCR run.

Offline mode (default, no network, no keys) proves three things and nothing more:

* ``intake_routing``: generated selectable-text PDFs and one image reach the
  real ``claims.documents.process_uploads`` gate and route safely. This is
  parsing/routing evidence, not OCR evidence.
* ``image_fail_closed``: every image-only document in the labelled dirty corpus
  (``tests/fixtures/documents``) is held for correction or manual review when
  OCR is unavailable. No image yields a confident extraction without a reader.
* ``provider_failure_injection``: stub providers that raise, time out, or
  return nothing show that a *mandatory* OCR failure (Sarvam digitise/extract)
  always blocks adjudication, while an *optional* Gemini evidence failure never
  clears an issue and never blocks an otherwise complete claim.

Live mode (``--providers live``) sends the same corpus through the real Sarvam
Document AI path (and optionally Gemini evidence review) and scores
classification, per-field accuracy, abstention and unsafe confident errors, with
provider call counts and latency. Without ``SARVAM_API_KEY`` it produces no
metrics at all: every scenario is reported as ``NOT_RUN``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from claims.agent_pipeline import resolve_document_handoff
from claims.documents import DocumentProvider, process_uploads
from claims.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "tests" / "fixtures" / "documents"
PROVIDER_ENV = ("SARVAM_API_KEY", "GEMINI_API_KEY", "GEMINI_EVIDENCE_REVIEW_ENABLED")
# Single-document scoring cannot satisfy a category's full document matrix, so a
# missing *other* required document is not a property of the file under test.
NON_DOCUMENT_CODES = {"MISSING_DOCUMENT"}
ADJUDICATION_ELIGIBLE = "ADJUDICATION_ELIGIBLE"


def _load_policy() -> dict[str, Any]:
    # The same validated, canonical configuration the web worker and the policy
    # engine use (for example, requires_prescription is enforced through it).
    return load_policy(ROOT / "data" / "policy_terms.json")


@contextmanager
def _offline_environment() -> Iterator[None]:
    """Hide provider keys so ``process_uploads`` cannot build a live provider."""
    saved = {name: os.environ.pop(name) for name in PROVIDER_ENV if name in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def route(issues: list[dict[str, Any]]) -> str:
    """Mirror of the web worker's routing on document issues.

    Any issue stops adjudication; provider unavailability goes to an operator,
    everything else to an actionable member correction.
    """
    codes = {str(issue.get("code")) for issue in issues}
    if not codes:
        return ADJUDICATION_ELIGIBLE
    if "EXTRACTION_UNAVAILABLE" in codes:
        return "MANUAL_REVIEW"
    return "DOCUMENT_CORRECTION_REQUIRED"


# ---------------------------------------------------------------- corpus


def load_corpus(corpus_dir: Path = CORPUS_DIR) -> list[dict[str, Any]]:
    """Load labels and verify every file matches its recorded SHA-256."""
    labels = json.loads((corpus_dir / "labels.json").read_text(encoding="utf-8"))
    documents = []
    for label in labels["documents"]:
        path = corpus_dir / label["file"]
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != label["sha256"]:
            raise ValueError(f"{label['file']} does not match labels.json (sha256 {digest[:12]} != {label['sha256'][:12]})")
        documents.append({**label, "data": data})
    return documents


def _upload(document: dict[str, Any]) -> dict[str, Any]:
    return {"file_name": document["file"], "data": document["data"]}


# ---------------------------------------------------------------- suite 1: intake/routing (selectable text)


def _pdf(lines: list[str], path: Path, *, pages: int = 1) -> Path:
    document = fitz.open()
    chunks = [lines[index::pages] for index in range(pages)]
    for chunk in chunks:
        page = document.new_page(width=595, height=842)
        for row, line in enumerate(chunk):
            page.insert_text((52, 65 + row * 28), line, fontsize=13)
    document.save(path)
    document.close()
    return path


def _blurred_bill(path: Path) -> Path:
    image = Image.new("RGB", (1000, 1250), "white")
    draw = ImageDraw.Draw(image)
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 52)
    except OSError:
        font = ImageFont.load_default()
    for row, line in enumerate(["HOSPITAL BILL", "Patient: Rajesh Kumar", "Total Amount: 1500.00"]):
        draw.text((70, 130 + row * 110), line, fill="black", font=font)
    image.filter(ImageFilter.GaussianBlur(radius=3)).save(path)
    return path


def _intake_routing(output_dir: Path, policy: dict[str, Any]) -> dict[str, Any]:
    prescription = _pdf(
        ["PRESCRIPTION", "Dr. Arun Sharma", "Patient: Rajesh Kumar", "Date: 01-Nov-2024", "Diagnosis: Viral Fever"],
        output_dir / "clean-prescription.pdf",
    )
    bill = _pdf(
        ["CITY MEDICAL CENTRE", "HOSPITAL BILL / RECEIPT", "Patient: Rajesh Kumar", "Date: 01-Nov-2024", "Consultation Fee 1500.00", "Total Amount: 1500.00"],
        output_dir / "clean-bill.pdf",
    )
    mismatch_bill = _pdf(
        ["HOSPITAL BILL", "Patient: Arjun Mehta", "Consultation Fee 1500.00", "Total Amount: 1500.00"],
        output_dir / "patient-mismatch-bill.pdf",
    )
    altered_bill = _pdf(
        ["HOSPITAL BILL", "Patient: Rajesh Kumar", "Consultation Fee 1000.00", "Total Amount: 1500.00"],
        output_dir / "amount-conflict-bill.pdf",
    )
    multipage_bill = _pdf(
        ["HOSPITAL BILL", "Patient: Rajesh Kumar", "Date: 01-Nov-2024", "Consultation Fee 1000.00", "CBC Test 500.00", "Total Amount: 1500.00"],
        output_dir / "multi-page-bill.pdf",
        pages=2,
    )
    blurry_bill = _blurred_bill(output_dir / "blurred-phone-photo.png")

    def upload(path: Path) -> dict[str, Any]:
        return {"file_name": path.name, "data": path.read_bytes()}

    scenarios: list[tuple[str, str, list[dict[str, Any]], str]] = [
        ("clean_consultation", "CONSULTATION", [upload(prescription), upload(bill)], "ACCEPTED"),
        ("wrong_document_type", "CONSULTATION", [upload(prescription)], "MISSING_DOCUMENT"),
        ("patient_mismatch", "CONSULTATION", [upload(prescription), upload(mismatch_bill)], "PATIENT_MISMATCH"),
        ("amount_conflict", "DENTAL", [upload(altered_bill)], "BILL_ARITHMETIC_CONFLICT"),
        ("multi_page_bill", "DENTAL", [upload(multipage_bill)], "ACCEPTED"),
        ("blurred_phone_photo", "DENTAL", [upload(blurry_bill)], "EXTRACTION_UNAVAILABLE"),
    ]
    records = []
    for name, category, uploads, expected in scenarios:
        result = process_uploads(uploads, category, "Rajesh Kumar", policy, provider=None)
        codes = {str(issue.get("code")) for issue in result["issues"]}
        matched = not codes if expected == "ACCEPTED" else expected in codes
        records.append({
            "scenario": name,
            "category": category,
            "expected": expected,
            "matched": matched,
            "issue_codes": sorted(codes),
            "route": route(result["issues"]),
            "metrics": result["metrics"],
            "documents": [{"file_name": doc["file_name"], "actual_type": doc["actual_type"], "quality": doc["quality"], "pages": doc["pages"], "extraction_source": doc["extraction_source"]} for doc in result["documents"]],
        })
    return _suite(
        "Intake and routing on generated selectable-text PDFs (local text layer, no OCR) plus one image without a reader.",
        records,
    )


# ---------------------------------------------------------------- suite 2: image fail-closed without OCR


def _image_fail_closed(corpus: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    records = []
    for document in corpus:
        result = process_uploads([_upload(document)], document["claim_category"], document["member_name"], policy, provider=None)
        codes = {str(issue.get("code")) for issue in result["issues"]}
        observed_route = route(result["issues"])
        confident = [doc["file_name"] for doc in result["documents"] if doc["quality"] == "GOOD"]
        fabricated = [doc["file_name"] for doc in result["documents"] if doc["content"]]
        matched = (
            document["offline_expected_code"] in codes
            and observed_route != ADJUDICATION_ELIGIBLE
            and not confident
            and not fabricated
            and result["metrics"]["provider_calls"] == 0
        )
        records.append({
            "scenario": document["id"],
            "file": document["file"],
            "conditions": document["conditions"],
            "expected": document["offline_expected_code"],
            "matched": matched,
            "issue_codes": sorted(codes),
            "route": observed_route,
            "confident_documents": confident,
            "documents_with_content": fabricated,
            "metrics": result["metrics"],
        })
    return _suite(
        "Every image-only corpus document with OCR unavailable: must be held (manual review or re-upload) with no extracted content.",
        records,
    )


# ---------------------------------------------------------------- suite 3: provider failure injection


class _ScriptedProvider:
    """Offline stand-in for Sarvam with scripted behaviour; never used in live mode."""

    def __init__(self, digitise: Callable[[], str], extract: Callable[[], dict[str, Any]] | None = None):
        self._digitise = digitise
        self._extract = extract

    def digitise(self, data: bytes, mime_type: str) -> str:
        return self._digitise()

    def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict[str, Any]:
        if self._extract is None:
            raise RuntimeError("extract not scripted")
        return self._extract()


def _raise(exc: Exception) -> Callable[..., Any]:
    def run(*_: Any, **__: Any) -> Any:
        raise exc
    return run


# OCR text for a bill whose total is readable but whose line items are not, so
# the mandatory structured-extract step is required.
_BILL_TEXT_WITHOUT_ITEMS = "CITY MEDICAL CENTRE\nHOSPITAL BILL / RECEIPT\nPatient Name: Rajesh Kumar\nDate: 01-Nov-2024\nTotal Amount: 1500.00\n"


def _fabricated_gemini(*_: Any, **__: Any) -> dict[str, Any]:
    return {
        "schema_version": 1, "producer": "gemini_evidence", "task": "resolve_document_facts",
        "source_file_ids": ["UPLOAD-1"], "status": "CANDIDATES_VALIDATED",
        "candidates": [{
            "file_id": "UPLOAD-1",
            "fields": {"document_type": "HOSPITAL_BILL", "patient_name": "Rajesh Kumar", "total_paise": 150000,
                       "line_items": [{"description": "Consultation Fee (OPD)", "amount_paise": 150000}]},
            "evidence": [{"field": "total_paise", "page": 1, "quote": "Total Amount: 1500.00"}],
        }],
        "trace": {"stage": "gemini_evidence", "status": "CANDIDATES_VALIDATED"},
        "metrics": {"calls": 1, "pages": 1},
    }


def _provider_failure_injection(corpus: list[dict[str, Any]], output_dir: Path, policy: dict[str, Any]) -> dict[str, Any]:
    photo = next(doc for doc in corpus if doc["id"] == "bill_phone_photo_skewed")
    uploads = [_upload(photo)]
    member = photo["member_name"]
    category = "DENTAL"  # needs only a hospital bill, so a single upload can be complete

    def run(provider: DocumentProvider | None) -> dict[str, Any]:
        return process_uploads(uploads, category, member, policy, provider=provider)

    cases: list[tuple[str, str, dict[str, Any], str, str]] = []
    cases.append(("mandatory_digitise_exception", "mandatory", run(_ScriptedProvider(_raise(ConnectionError("network down")))), "EXTRACTION_UNAVAILABLE", "MANUAL_REVIEW"))
    cases.append(("mandatory_digitise_timeout", "mandatory", run(_ScriptedProvider(_raise(TimeoutError("Sarvam job exceeded 90 seconds")))), "EXTRACTION_UNAVAILABLE", "MANUAL_REVIEW"))
    cases.append(("mandatory_digitise_empty_text", "mandatory", run(_ScriptedProvider(lambda: "  ")), "UNREADABLE_DOCUMENT", "DOCUMENT_CORRECTION_REQUIRED"))
    cases.append(("mandatory_extract_exception", "mandatory", run(_ScriptedProvider(lambda: _BILL_TEXT_WITHOUT_ITEMS, _raise(RuntimeError("Sarvam job ended with failed")))), "EXTRACTION_UNAVAILABLE", "MANUAL_REVIEW"))
    cases.append(("mandatory_non_medical_text", "mandatory", run(_ScriptedProvider(lambda: "UDUPI GARDEN RESTAURANT\nMasala Dosa x2 240.00\nFilter Coffee x3 150.00\nThank you, visit again\n")), "UNIDENTIFIED_DOCUMENT", "DOCUMENT_CORRECTION_REQUIRED"))

    failed = run(_ScriptedProvider(_raise(ConnectionError("network down"))))
    files_by_id = {"UPLOAD-1": {"data": photo["data"], "mime_type": photo["mime_type"]}}
    for name, resolver in (("optional_gemini_exception_after_mandatory_failure", _raise(RuntimeError("gemini down"))),
                           ("optional_gemini_fabricated_candidate_after_mandatory_failure", _fabricated_gemini)):
        handoff = resolve_document_handoff(failed, files_by_id, failed["ocr_text_by_file_id"], category, member, [member], policy, resolver)
        cases.append((name, "optional", {**failed, "issues": handoff["issues"], "documents": handoff["documents"], "gemini_status": handoff["status"]}, "EXTRACTION_UNAVAILABLE", "MANUAL_REVIEW"))

    # An optional evidence failure must not block a complete, locally verified claim.
    clean = _pdf(
        ["CITY MEDICAL CENTRE", "HOSPITAL BILL / RECEIPT", "Patient: Rajesh Kumar", "Date: 01-Nov-2024", "Consultation Fee 1500.00", "Total Amount: 1500.00"],
        output_dir / "optional-failure-clean-bill.pdf",
    )
    clean_result = process_uploads([{"file_name": clean.name, "data": clean.read_bytes()}], category, member, policy, provider=None)
    handoff = resolve_document_handoff(clean_result, {}, clean_result["ocr_text_by_file_id"], category, member, [member], policy, _raise(RuntimeError("gemini down")))
    cases.append(("optional_gemini_exception_on_complete_claim", "optional", {**clean_result, "issues": handoff["issues"], "documents": handoff["documents"], "gemini_status": handoff["status"]}, "", ADJUDICATION_ELIGIBLE))

    records = []
    for name, stage, result, expected_code, expected_route in cases:
        codes = {str(issue.get("code")) for issue in result["issues"]}
        observed_route = route(result["issues"])
        # A mandatory failure may leave readable OCR text behind, but it must never
        # leave the claim eligible for an automated financial decision.
        decision_blocked = stage != "mandatory" or observed_route != ADJUDICATION_ELIGIBLE
        matched = observed_route == expected_route and (not expected_code or expected_code in codes) and decision_blocked
        records.append({
            "scenario": name,
            "failure_class": stage,
            "expected": expected_code or "ACCEPTED",
            "expected_route": expected_route,
            "route": observed_route,
            "matched": matched,
            "issue_codes": sorted(codes),
            "gemini_status": result.get("gemini_status"),
            "provider_failures": result.get("metrics", {}).get("provider_failures", 0),
        })
    return _suite(
        "Scripted provider failures. Mandatory (Sarvam OCR) failures must block adjudication; optional (Gemini evidence) failures must neither clear an issue nor block a complete claim.",
        records,
    )


def _suite(scope: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {"scope": scope, "passed": sum(bool(record["matched"]) for record in records), "total": len(records), "records": records}


def evaluate(output_dir: Path, report_dir: Path, corpus_dir: Path = CORPUS_DIR) -> dict[str, Any]:
    """Offline benchmark. Never calls a provider, even when keys are configured."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    policy = _load_policy()
    corpus = load_corpus(corpus_dir)
    with _offline_environment():
        suites = {
            "intake_routing": _intake_routing(output_dir, policy),
            "image_fail_closed": _image_fail_closed(corpus, policy),
            "provider_failure_injection": _provider_failure_injection(corpus, output_dir, policy),
        }
    passed = sum(suite["passed"] for suite in suites.values())
    total = sum(suite["total"] for suite in suites.values())
    summary = {
        "kind": "offline_document_intake_routing_and_fail_closed_benchmark",
        "ocr_evaluated": False,
        "live_ocr_report": "document-ocr-evaluation.json (python -m scripts.evaluate_documents --providers live)",
        "passed": passed,
        "total": total,
        "suites": suites,
    }
    (report_dir / "document-evaluation.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (report_dir / "document-evaluation.md").write_text(_offline_markdown(summary), encoding="utf-8")
    return summary


def _offline_markdown(summary: dict[str, Any]) -> str:
    suites = summary["suites"]
    lines = [
        "# Document intake, routing and fail-closed evaluation (offline)",
        "",
        f"**{summary['passed']}/{summary['total']}** offline scenarios matched their safe expected route.",
        "",
        "**This is not an OCR-accuracy report.** No provider was called (`provider_calls = 0` throughout). It shows that selectable-text PDFs parse and route correctly, that image-only documents are never turned into a confident decision without a reader, and that provider failures fail closed. OCR accuracy on the dirty corpus is measured only by `--providers live`; see `document-ocr-evaluation.md`.",
        "",
    ]
    for key, title in (
        ("intake_routing", "Intake and routing (selectable-text PDFs)"),
        ("image_fail_closed", "Image-only dirty corpus with OCR unavailable"),
        ("provider_failure_injection", "Mandatory vs optional provider failure"),
    ):
        suite = suites[key]
        lines += [f"## {title}: {suite['passed']}/{suite['total']}", "", suite["scope"], "", "| Scenario | Expected | Route | Issue codes | Match |", "| --- | --- | --- | --- | --- |"]
        for record in suite["records"]:
            codes = ", ".join(record["issue_codes"]) or "none"
            lines.append(f"| {record['scenario']} | {record['expected']} | {record['route']} | {codes} | {'Yes' if record['matched'] else 'No'} |")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- live OCR benchmark


class _TimedProvider:
    """Wrap a real provider to record per-call latency without changing behaviour."""

    def __init__(self, inner: DocumentProvider):
        self.inner = inner
        self.calls: list[dict[str, Any]] = []

    def _timed(self, operation: str, call: Callable[[], Any]) -> Any:
        started = time.perf_counter()
        status = "ok"
        try:
            return call()
        except Exception as exc:
            status = type(exc).__name__
            raise
        finally:
            self.calls.append({"operation": operation, "seconds": round(time.perf_counter() - started, 3), "status": status})

    def digitise(self, data: bytes, mime_type: str) -> str:
        return self._timed("sarvam_digitise", lambda: self.inner.digitise(data, mime_type))

    def extract_fields(self, data: bytes, mime_type: str, document_type: str) -> dict[str, Any]:
        return self._timed("sarvam_extract", lambda: self.inner.extract_fields(data, mime_type, document_type))


_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "smt"}
_DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d.%m.%Y", "%d/%m/%y", "%d-%b-%y")


def _words(value: Any) -> list[str]:
    return re.findall(r"\w+", str(value).casefold(), flags=re.UNICODE)


def _date(value: Any) -> str | None:
    text = " ".join(str(value).split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _amount(value: Any) -> float | None:
    try:
        return round(float(str(value).replace(",", "").replace("₹", "").strip()), 2)
    except ValueError:
        return None


def _field_value(content: dict[str, Any], field: str) -> Any:
    if field == "line_item_amounts":
        items = content.get("line_items") or []
        amounts = [_amount(item.get("amount")) for item in items if isinstance(item, dict)]
        return sorted(amount for amount in amounts if amount is not None) or None
    value = content.get(field)
    return None if value in (None, "", []) else value


def _line_items_reconcile(content: dict[str, Any]) -> bool | None:
    """Return whether observed bill items exactly reconcile to its observed total.

    A missing total or no readable item amounts is deliberately ``None`` rather
    than a pass: the report distinguishes unavailable evidence from bad maths.
    """
    total = _amount(content.get("total"))
    amounts = _field_value(content, "line_item_amounts")
    if total is None or not amounts:
        return None
    return round(sum(amounts), 2) == total


def _matches(field: str, truth: Any, observed: Any) -> bool:
    if field == "line_item_amounts":
        return sorted(round(float(x), 2) for x in truth) == observed
    if field == "total":
        return _amount(observed) == round(float(truth), 2)
    if field in {"date", "sample_date", "report_date"}:
        expected, seen = _date(truth), _date(observed)
        return expected is not None and expected == seen or _words(truth) == _words(observed)
    if field in {"doctor_registration", "bill_number"}:
        return re.sub(r"\s+", "", str(truth)).upper() == re.sub(r"\s+", "", str(observed)).upper()
    if field in {"patient_name", "doctor_name"}:
        return [w for w in _words(truth) if w not in _HONORIFICS] == [w for w in _words(observed) if w not in _HONORIFICS]
    expected_words, seen_words = set(_words(truth)), set(_words(observed))
    return bool(seen_words) and (expected_words <= seen_words or seen_words <= expected_words)


def _forbidden_hit(content: dict[str, Any], forbidden: dict[str, list[float]]) -> list[str]:
    hits = []
    for field, values in forbidden.items():
        observed = _field_value(content, field)
        if observed is None:
            continue
        observed_amounts = observed if isinstance(observed, list) else [_amount(observed)]
        hits += [f"{field}={value}" for value in values if round(float(value), 2) in observed_amounts]
    return hits


def score_document(label: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Score one document's pipeline result against its ground-truth label."""
    issues = [issue for issue in result["issues"] if issue.get("code") not in NON_DOCUMENT_CODES]
    codes = sorted({str(issue.get("code")) for issue in issues})
    observed_route = route(issues)
    behavior = "extract" if observed_route == ADJUDICATION_ELIGIBLE else "abstain" if observed_route == "MANUAL_REVIEW" else "request_reupload"
    document = result["documents"][0] if result["documents"] else None
    content = document["content"] if document else {}
    fields: dict[str, dict[str, Any]] = {}
    for field, truth in label["fields"].items():
        observed = _field_value(content, field)
        if observed is None:
            outcome = "abstained" if field in label.get("abstain_allowed_fields", []) else "missing"
        else:
            outcome = "correct" if _matches(field, truth, observed) else "wrong"
        fields[field] = {"expected": truth, "observed": observed, "outcome": outcome}
    for field in label.get("absent_fields", []):
        observed = _field_value(content, field)
        fields[field] = {"expected": None, "observed": observed, "outcome": "correct" if observed is None else "hallucinated"}
    forbidden = _forbidden_hit(content, label.get("forbidden_values", {}))
    held = behavior != "extract"
    expected_hold = label["expected_behavior"] != "extract"
    expects_alteration_hold = bool({"struck_through_amount", "handwritten_correction"} & set(label["conditions"]))
    critical_errors = [name for name in label.get("critical_fields", []) if fields.get(name, {}).get("outcome") in {"wrong", "hallucinated"}]
    observed_type = document["actual_type"] if document else None
    return {
        "scenario": label["id"],
        "file": label["file"],
        "conditions": label["conditions"],
        "status": "RUN",
        "expected_type": label["document_type"],
        "observed_type": observed_type,
        "classification_correct": None if label["document_type"] is None else observed_type == label["document_type"],
        "expected_behavior": label["expected_behavior"],
        "observed_behavior": behavior,
        "behavior_acceptable": behavior in label["acceptable_behaviors"],
        "abstention_correct": held == expected_hold,
        "route": observed_route,
        "issue_codes": codes,
        "fields": fields,
        "forbidden_values_used": forbidden,
        "unsafe_confident_error": not held and bool(critical_errors or forbidden),
        "critical_field_errors": critical_errors,
        "wrong_total": fields.get("total", {}).get("outcome") in {"wrong", "hallucinated"},
        "line_item_reconciles": _line_items_reconcile(content),
        "expects_alteration_hold": expects_alteration_hold,
        "alteration_detected": "DOCUMENT_ALTERATION" in codes,
        "missed_alteration": expects_alteration_hold and not held,
        "quality": document["quality"] if document else None,
        "extraction_source": document["extraction_source"] if document else None,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _aggregate(
    records: list[dict[str, Any]],
    *,
    digitise_cost_inr_per_page: float | None = None,
    extract_cost_inr_per_page: float | None = None,
) -> dict[str, Any]:
    typed = [r for r in records if r["classification_correct"] is not None]
    outcomes = [f for r in records for f in r["fields"].values()]
    scored_fields = [f for f in outcomes if f["outcome"] != "abstained"]
    per_field: dict[str, dict[str, int]] = {}
    for record in records:
        for name, field in record["fields"].items():
            bucket = per_field.setdefault(name, {"correct": 0, "wrong": 0, "missing": 0, "abstained": 0, "hallucinated": 0})
            bucket[field["outcome"]] += 1
    latencies = [r["latency_seconds"] for r in records]
    expected_extract = [r for r in records if r["expected_behavior"] == "extract"]
    expected_hold = [r for r in records if r["expected_behavior"] != "extract"]
    observed_reconciliation = [r for r in records if r["line_item_reconciles"] is not None]
    costs_configured = digitise_cost_inr_per_page is not None and extract_cost_inr_per_page is not None
    digitise_pages = sum(r["metrics"].get("sarvam_digitise_pages", 0) for r in records)
    extract_pages = sum(r["metrics"].get("sarvam_extract_pages", 0) for r in records)
    return {
        "documents": len(records),
        "classification_accuracy": _rate(sum(r["classification_correct"] for r in typed), len(typed)),
        "field_accuracy": _rate(sum(f["outcome"] == "correct" for f in scored_fields), len(scored_fields)),
        "per_field": per_field,
        "abstention_accuracy": _rate(sum(r["abstention_correct"] for r in records), len(records)),
        "behavior_acceptable_rate": _rate(sum(r["behavior_acceptable"] for r in records), len(records)),
        "false_hold_rate": _rate(sum(r["observed_behavior"] != "extract" for r in expected_extract), len(expected_extract)),
        "missed_hold_count": sum(r["observed_behavior"] == "extract" for r in expected_hold),
        "wrong_total_rate": _rate(sum(r["wrong_total"] for r in records), len(records)),
        "line_item_reconciliation_rate": _rate(sum(r["line_item_reconciles"] is True for r in observed_reconciliation), len(observed_reconciliation)),
        "line_item_reconciliation_unavailable_count": sum(r["line_item_reconciles"] is None for r in records),
        "missed_alteration_count": sum(r["missed_alteration"] for r in records),
        "alteration_false_hold_count": sum(
            r["alteration_detected"] and not r["expects_alteration_hold"] for r in records
        ),
        "unsafe_confident_errors": sum(r["unsafe_confident_error"] for r in records),
        "provider_calls": sum(r["metrics"].get("provider_calls", 0) for r in records),
        "provider_failures": sum(r["metrics"].get("provider_failures", 0) for r in records),
        "sarvam_digitise_calls": sum(r["metrics"].get("sarvam_digitise_calls", 0) for r in records),
        "sarvam_digitise_pages": digitise_pages,
        "sarvam_extract_calls": sum(r["metrics"].get("sarvam_extract_calls", 0) for r in records),
        "sarvam_extract_pages": extract_pages,
        "gemini_calls": sum(int((r.get("gemini") or {}).get("calls", 0) or 0) for r in records),
        "latency_seconds_p50": round(statistics.median(latencies), 3) if latencies else None,
        "latency_seconds_max": round(max(latencies), 3) if latencies else None,
        "configured_cost_inr": (
            round(digitise_pages * digitise_cost_inr_per_page + extract_pages * extract_cost_inr_per_page, 2)
            if costs_configured else None
        ),
        "cost_rate_source": "CLI supplied per-page rates" if costs_configured else "NOT_CONFIGURED",
    }


def evaluate_live(
    report_dir: Path,
    corpus_dir: Path = CORPUS_DIR,
    *,
    gemini: bool = False,
    provider_factory: Callable[[], DocumentProvider] | None = None,
    digitise_cost_inr_per_page: float | None = None,
    extract_cost_inr_per_page: float | None = None,
) -> dict[str, Any]:
    """Provider-backed OCR benchmark over the labelled dirty corpus.

    ``provider_factory`` exists for unit tests of the scoring code only; the CLI
    always uses the real Sarvam provider, and the report records which was used.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    corpus = load_corpus(corpus_dir)
    policy = _load_policy()
    missing = [] if provider_factory is not None or os.getenv("SARVAM_API_KEY") else ["SARVAM_API_KEY"]
    gemini_blocker = None
    if gemini and not os.getenv("GEMINI_API_KEY"):
        gemini_blocker = "NOT_RUN (missing GEMINI_API_KEY)"
    if missing:
        reason = f"NOT_RUN (missing {', '.join(missing)})"
        summary: dict[str, Any] = {
            "kind": "live_document_ocr_benchmark",
            "status": "NOT_RUN",
            "reason": reason,
            "provider": None,
            "metrics": None,
            "records": [{"scenario": doc["id"], "file": doc["file"], "conditions": doc["conditions"], "status": reason} for doc in corpus],
        }
        _write_live(report_dir, summary)
        return summary

    if provider_factory is None:
        from claims.documents import SarvamDocumentProvider

        def provider_factory() -> DocumentProvider:
            return SarvamDocumentProvider()
        provider_name = "sarvam_document_ai"
    else:
        provider_name = "injected_test_provider"

    resolver = None
    if gemini and gemini_blocker is None:
        from claims.ai_review import resolve_evidence

        os.environ["GEMINI_EVIDENCE_REVIEW_ENABLED"] = "true"  # --gemini is the explicit opt-in
        resolver = resolve_evidence

    records = []
    for document in corpus:
        provider = _TimedProvider(provider_factory())
        started = time.perf_counter()
        result = process_uploads([_upload(document)], document["claim_category"], document["member_name"], policy, provider=provider)
        gemini_record: dict[str, Any] | None = {"status": gemini_blocker} if gemini_blocker else None
        if resolver is not None and result["documents"]:
            handoff = resolve_document_handoff(
                result, {"UPLOAD-1": {"data": document["data"], "mime_type": document["mime_type"]}},
                result["ocr_text_by_file_id"], document["claim_category"], document["member_name"],
                [document["member_name"]], policy, resolver,
            )
            result = {**result, "documents": handoff["documents"], "issues": handoff["issues"]}
            gemini_record = {**handoff["metrics"].get("gemini", {}), "trace_reason": handoff["trace"][0].get("reason")}
        elapsed = round(time.perf_counter() - started, 3)
        record = score_document(document, result)
        record.update({"latency_seconds": elapsed, "provider_call_log": provider.calls, "metrics": result["metrics"], "gemini": gemini_record})
        records.append(record)

    summary = {
        "kind": "live_document_ocr_benchmark",
        "status": "RUN",
        "provider": provider_name,
        "gemini": "enabled" if resolver else (gemini_blocker or "disabled"),
        "metrics": _aggregate(
            records,
            digitise_cost_inr_per_page=digitise_cost_inr_per_page,
            extract_cost_inr_per_page=extract_cost_inr_per_page,
        ),
        "records": records,
    }
    _write_live(report_dir, summary)
    return summary


def _write_live(report_dir: Path, summary: dict[str, Any]) -> None:
    (report_dir / "document-ocr-evaluation.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    lines = ["# Provider-backed OCR evaluation (dirty synthetic corpus)", ""]
    if summary["status"] != "RUN":
        lines += [
            f"**{summary['reason']}.** No provider was called and no accuracy, abstention or latency figures exist for this run.",
            "",
            "| Scenario | Conditions | Status |",
            "| --- | --- | --- |",
        ]
        lines += [f"| {r['scenario']} | {', '.join(r['conditions'])} | {r['status']} |" for r in summary["records"]]
        (report_dir / "document-ocr-evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return
    metrics = summary["metrics"]
    lines += [
        f"Provider: `{summary['provider']}`; Gemini evidence review: {summary['gemini']}.",
        "",
        "| Metric | Value |",
        "| --- | --- |",
    ]
    for key in ("documents", "unsafe_confident_errors", "classification_accuracy", "field_accuracy", "abstention_accuracy",
                "behavior_acceptable_rate", "false_hold_rate", "missed_hold_count", "wrong_total_rate",
                "line_item_reconciliation_rate", "line_item_reconciliation_unavailable_count", "missed_alteration_count",
                "alteration_false_hold_count", "provider_calls", "provider_failures", "sarvam_digitise_calls",
                "sarvam_digitise_pages", "sarvam_extract_calls", "sarvam_extract_pages", "gemini_calls",
                "latency_seconds_p50", "latency_seconds_max", "configured_cost_inr", "cost_rate_source"):
        lines.append(f"| {key} | {metrics[key]} |")
    lines += ["", "| Scenario | Type (exp / got) | Behaviour (exp / got) | Fields correct | Unsafe | Latency s |", "| --- | --- | --- | --- | --- | --- |"]
    for r in summary["records"]:
        correct = sum(f["outcome"] == "correct" for f in r["fields"].values())
        lines.append(
            f"| {r['scenario']} | {r['expected_type']} / {r['observed_type']} | {r['expected_behavior']} / {r['observed_behavior']} | "
            f"{correct}/{len(r['fields'])} | {'YES' if r['unsafe_confident_error'] else 'no'} | {r['latency_seconds']} |"
        )
    lines += [
        "",
        "An unsafe confident error is a document that would proceed to adjudication with a wrong or fabricated critical field. The target is zero.",
        "Configured cost is an estimate only, using the per-page rates passed to this run; it is omitted until rates are supplied.",
    ]
    (report_dir / "document-ocr-evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Document intake/routing benchmark (offline) or OCR benchmark (live).")
    parser.add_argument("--providers", choices=("offline", "live"), default="offline")
    parser.add_argument("--gemini", action="store_true", help="live mode: also run the optional Gemini evidence review (needs GEMINI_API_KEY)")
    parser.add_argument("--corpus-dir", type=Path, default=CORPUS_DIR)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".data" / "document-evaluation")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "docs" / "reports")
    parser.add_argument("--digitise-cost-inr-per-page", type=float, help="live mode: measured or current rate for each Sarvam Digitise page")
    parser.add_argument("--extract-cost-inr-per-page", type=float, help="live mode: measured or current rate for each Sarvam Extract page")
    args = parser.parse_args()
    if args.providers == "live":
        live = evaluate_live(
            args.report_dir,
            args.corpus_dir,
            gemini=args.gemini,
            digitise_cost_inr_per_page=args.digitise_cost_inr_per_page,
            extract_cost_inr_per_page=args.extract_cost_inr_per_page,
        )
        if live["status"] != "RUN":
            print(f"{live['reason']}: {len(live['records'])} live OCR scenarios not run; no metrics were produced.")
            return 2
        metrics = live["metrics"]
        print(
            f"live OCR: {metrics['documents']} documents, unsafe confident errors {metrics['unsafe_confident_errors']}, "
            f"classification {metrics['classification_accuracy']}, fields {metrics['field_accuracy']}, "
            f"abstention {metrics['abstention_accuracy']}, provider calls {metrics['provider_calls']}"
        )
        return 0 if metrics["unsafe_confident_errors"] == 0 else 1
    result = evaluate(args.output_dir, args.report_dir, args.corpus_dir)
    for name, suite in result["suites"].items():
        print(f"{name}: {suite['passed']}/{suite['total']}")
    print(f"{result['passed']}/{result['total']} offline document scenarios matched (intake/routing and fail-closed; not OCR)")
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
