"""Generate and run a labelled, live Sarvam outcome benchmark.

The generated PDFs contain raster pages only, so the local selectable-text
parser cannot satisfy the claim. A successful extraction therefore exercises
the configured Sarvam Document AI path.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "output" / "pdf" / "live-ocr-outcomes"
ARTIFACT_DIR = ROOT / "output" / "live-ocr-benchmark"
REPORT_PATH = ROOT / "docs" / "reports" / "live-ocr-outcome-benchmark.md"
PAGE_SIZE = (1240, 1754)


@dataclass(frozen=True)
class Scenario:
    name: str
    member_id: str
    member_name: str
    category: str
    treatment_date: str
    claimed_amount: str
    documents: tuple[str, ...]
    expected: str


SCENARIOS = (
    Scenario(
        "approved",
        "EMP001",
        "Rajesh Kumar",
        "CONSULTATION",
        "2024-11-01",
        "1500",
        ("approved_prescription.pdf", "approved_receipt.pdf"),
        "APPROVED",
    ),
    Scenario(
        "doubtful",
        "EMP002",
        "Priya Singh",
        "CONSULTATION",
        "2024-11-02",
        "900",
        ("doubtful_prescription.pdf", "doubtful_receipt.pdf"),
        "SAFE_HOLD",
    ),
    Scenario(
        "rejected",
        "EMP003",
        "Amit Verma",
        "DENTAL",
        "2024-11-03",
        "1200",
        ("rejected_receipt.pdf",),
        "REJECTED",
    ),
    Scenario(
        "human_review",
        "EMP004",
        "Sneha Reddy",
        "CONSULTATION",
        "2024-11-04",
        "1000",
        ("human_review_prescription.pdf", "human_review_receipt.pdf"),
        "MANUAL_REVIEW",
    ),
)


def _fonts() -> tuple[Any, Any, Any]:
    from PIL import ImageFont

    candidates = (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    )
    for path in candidates:
        try:
            return (
                ImageFont.truetype(path, 30),
                ImageFont.truetype(path, 42),
                ImageFont.truetype(path, 24),
            )
        except OSError:
            continue
    return ImageFont.load_default(), ImageFont.load_default(), ImageFont.load_default()


def _base_page(title: str, provider: str) -> tuple[Any, Any, Any, Any, Any]:
    from PIL import Image, ImageDraw

    regular, heading, small = _fonts()
    image = Image.new("RGB", PAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 55, 1170, 1690), radius=18, outline="#17324d", width=4)
    draw.rectangle((70, 55, 1170, 220), fill="#edf4fa", outline="#17324d", width=4)
    draw.text((110, 85), provider, font=heading, fill="#102a43")
    draw.text((110, 155), title, font=regular, fill="#334e68")
    return image, draw, regular, heading, small


def _line(draw: Any, y: int) -> None:
    draw.line((105, y, 1135, y), fill="#9fb3c8", width=2)


def _prescription(name: str, date: str, diagnosis: str, identifier: str) -> Any:
    image, draw, regular, _heading, small = _base_page(
        "MEDICAL PRESCRIPTION", "CITY MEDICAL CENTRE"
    )
    female = name in {"Priya Singh", "Sneha Reddy"}
    rows = (
        ("Doctor", "Dr. Arun Sharma, MBBS, MD"),
        ("Registration", "KA/45678/2015"),
        ("Patient", name),
        ("Age", "34 years" if female else "39 years"),
        ("Gender", "Female" if female else "Male"),
        ("Date", date),
        ("Diagnosis", diagnosis),
    )
    y = 285
    for label, value in rows:
        draw.text((115, y), f"{label}:", font=small, fill="#486581")
        draw.text((360, y - 4), value, font=regular, fill="#102a43")
        y += 78
    _line(draw, y + 5)
    draw.text((115, y + 45), "Rx", font=regular, fill="#102a43")
    draw.text(
        (180, y + 45),
        "Paracetamol 650 mg - one tablet after food for 5 days",
        font=small,
        fill="#102a43",
    )
    draw.text(
        (180, y + 105), "Vitamin C 500 mg - one tablet daily for 7 days", font=small, fill="#102a43"
    )
    draw.text((115, y + 210), "Investigations: CBC, Dengue NS1", font=small, fill="#102a43")
    draw.text((115, 1570), f"Synthetic benchmark record: {identifier}", font=small, fill="#627d98")
    draw.text((825, 1500), "Dr. Arun Sharma", font=small, fill="#102a43")
    draw.text((825, 1540), "Digitally signed", font=small, fill="#627d98")
    return image


def _receipt(
    *,
    provider: str,
    patient: str,
    date: str,
    bill_number: str,
    items: tuple[tuple[str, int, int], ...],
    total: int | None,
    identifier: str,
    duplicate_stamp: bool = False,
    cropped: bool = False,
) -> Any:
    image, draw, regular, heading, small = _base_page("HOSPITAL BILL / RECEIPT", provider)
    draw.text((110, 275), f"Bill No: {bill_number}", font=small, fill="#102a43")
    draw.text((760, 275), f"Date: {date}", font=small, fill="#102a43")
    draw.text((110, 335), f"Patient: {patient}", font=regular, fill="#102a43")
    _line(draw, 410)
    draw.text((115, 445), "DESCRIPTION", font=small, fill="#486581")
    draw.text((760, 445), "QTY", font=small, fill="#486581")
    draw.text((950, 445), "AMOUNT INR", font=small, fill="#486581")
    _line(draw, 495)
    y = 535
    for description, quantity, amount in items:
        draw.text((115, y), description, font=regular, fill="#102a43")
        draw.text((790, y), str(quantity), font=regular, fill="#102a43")
        if not cropped:
            draw.text((980, y), f"{amount}.00", font=regular, fill="#102a43")
        y += 78
    _line(draw, y + 15)
    subtotal = sum(amount for _description, _quantity, amount in items)
    if not cropped:
        draw.text((720, y + 55), f"Subtotal: INR {subtotal}.00", font=regular, fill="#102a43")
    if total is not None:
        draw.text((720, y + 125), "GST: INR 0.00", font=regular, fill="#102a43")
        total_text = f"TOTAL AMOUNT: INR {total}.00"
        total_box = draw.textbbox((0, 0), total_text, font=regular)
        draw.text(
            (1110 - (total_box[2] - total_box[0]), y + 205),
            total_text,
            font=regular,
            fill="#0b6e4f",
        )
    if cropped:
        draw.rectangle((82, y + 35, 1158, 1680), fill="#d9e2ec")
        draw.line((82, y + 35, 1158, y + 35), fill="#7b8794", width=8)
        draw.text((250, y + 80), "BOTTOM OF RECEIPT NOT CAPTURED", font=regular, fill="#52616b")
    if duplicate_stamp:
        stamp = "DUPLICATE BILL"
        draw.rounded_rectangle((315, 1040, 935, 1180), radius=18, outline="#b42318", width=12)
        draw.text((405, 1085), stamp, font=heading, fill="#b42318")
    draw.text((115, 1570), f"Synthetic benchmark record: {identifier}", font=small, fill="#627d98")
    return image


def _image_pdf(image: Any, target: Path) -> None:
    from io import BytesIO

    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    target.parent.mkdir(parents=True, exist_ok=True)
    png = BytesIO()
    image.save(png, format="PNG", optimize=True)
    png.seek(0)
    pdf = canvas.Canvas(str(target), pagesize=(595, 842), pageCompression=1)
    pdf.drawImage(ImageReader(png), 0, 0, width=595, height=842)
    pdf.showPage()
    pdf.save()


def generate() -> None:
    screenshots = ARTIFACT_DIR / "screenshots"
    screenshots.mkdir(parents=True, exist_ok=True)
    documents = {
        "approved_prescription.pdf": _prescription(
            "Rajesh Kumar", "01-Nov-2024", "Viral Fever", "APPROVED-RX-001"
        ),
        "approved_receipt.pdf": _receipt(
            provider="CITY MEDICAL CENTRE",
            patient="Rajesh Kumar",
            date="01-Nov-2024",
            bill_number="CMC-LIVE-1001",
            items=(
                ("Consultation Fee", 1, 1000),
                ("CBC Test", 1, 300),
                ("Dengue NS1 Test", 1, 200),
            ),
            total=1500,
            identifier="APPROVED-BILL-001",
        ),
        "doubtful_prescription.pdf": _prescription(
            "Priya Singh", "02-Nov-2024", "Acute Bronchitis", "DOUBTFUL-RX-001"
        ),
        "doubtful_receipt.pdf": _receipt(
            provider="NEIGHBOURHOOD CLINIC",
            patient="Priya Singh",
            date="02-Nov-2024",
            bill_number="NC-LIVE-2001",
            items=(("Consultation Fee", 1, 900),),
            total=None,
            identifier="DOUBTFUL-BILL-001",
            cropped=True,
        ),
        "rejected_receipt.pdf": _receipt(
            provider="SMILE DENTAL CARE",
            patient="Amit Verma",
            date="03-Nov-2024",
            bill_number="SDC-LIVE-3001",
            items=(("Teeth Whitening - Cosmetic", 1, 1200),),
            total=1200,
            identifier="REJECTED-BILL-001",
        ),
        "human_review_prescription.pdf": _prescription(
            "Sneha Reddy", "04-Nov-2024", "Tension Headache", "REVIEW-RX-001"
        ),
        "human_review_receipt.pdf": _receipt(
            provider="CITY MEDICAL CENTRE",
            patient="Sneha Reddy",
            date="04-Nov-2024",
            bill_number="CMC-LIVE-4001",
            items=(("Consultation Fee", 1, 1000),),
            total=1000,
            identifier="REVIEW-BILL-001",
            duplicate_stamp=True,
        ),
    }
    for filename, image in documents.items():
        _image_pdf(image, PDF_DIR / filename)
        image.save(screenshots / f"input-{Path(filename).stem}.png", format="PNG", optimize=True)
    manifest = {
        "schema": "plum.live_ocr_outcome_benchmark.v1",
        "documents_are_synthetic": True,
        "pdfs_are_image_only": True,
        "scenarios": [scenario.__dict__ for scenario in SCENARIOS],
    }
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"pdf_dir": str(PDF_DIR), "documents": sorted(documents)}, indent=2))


def _decision(claim: dict[str, Any]) -> str | None:
    result = claim.get("result") or {}
    return result.get("decision")


def _matches_expected(scenario: Scenario, claim: dict[str, Any]) -> bool:
    state = claim.get("state")
    decision = _decision(claim)
    if scenario.expected == "SAFE_HOLD":
        return state in {"DOCUMENT_CORRECTION_REQUIRED", "MANUAL_REVIEW"} and decision in {
            None,
            "MANUAL_REVIEW",
        }
    return decision == scenario.expected


def _extracted_documents(claim: dict[str, Any]) -> list[dict[str, Any]]:
    for step in (claim.get("result") or {}).get("trace") or []:
        if step.get("rule_id") == "extracted_facts":
            return list(step.get("evidence") or [])
    return []


def _field(documents: list[dict[str, Any]], document_type: str, name: str) -> Any:
    document = next((item for item in documents if item.get("document_type") == document_type), {})
    return (document.get("fields") or {}).get(name)


def _evidence_checks(scenario: Scenario, claim: dict[str, Any]) -> list[dict[str, Any]]:
    result = claim.get("result") or {}
    metrics = result.get("document_metrics") or result.get("metrics") or {}
    documents = _extracted_documents(claim)
    checks = [
        {
            "name": "live_provider_completed",
            "passed": metrics.get("provider_calls", 0) >= len(scenario.documents)
            and metrics.get("provider_failures") == 0,
            "observed": {
                "provider_calls": metrics.get("provider_calls"),
                "provider_failures": metrics.get("provider_failures"),
            },
        }
    ]
    if scenario.name == "approved":
        checks += [
            {
                "name": "diagnosis_clean",
                "passed": _field(documents, "PRESCRIPTION", "diagnosis") == "Viral Fever",
                "observed": _field(documents, "PRESCRIPTION", "diagnosis"),
            },
            {
                "name": "patient_age_preserved",
                "passed": _field(documents, "PRESCRIPTION", "patient_age") == "39 years",
                "observed": _field(documents, "PRESCRIPTION", "patient_age"),
            },
            {
                "name": "patient_gender_preserved",
                "passed": str(_field(documents, "PRESCRIPTION", "patient_gender")).casefold()
                == "male",
                "observed": _field(documents, "PRESCRIPTION", "patient_gender"),
            },
            {
                "name": "bill_total",
                "passed": _field(documents, "HOSPITAL_BILL", "total") == 1500.0,
                "observed": _field(documents, "HOSPITAL_BILL", "total"),
            },
        ]
    elif scenario.name == "doubtful":
        checks.append(
            {
                "name": "missing_total_not_invented",
                "passed": _field(documents, "HOSPITAL_BILL", "total") is None,
                "observed": _field(documents, "HOSPITAL_BILL", "total"),
            }
        )
    elif scenario.name == "rejected":
        lines = _field(documents, "HOSPITAL_BILL", "line_items") or []
        checks.append(
            {
                "name": "excluded_service_extracted",
                "passed": any(
                    "teeth whitening" in str(item.get("description", "")).casefold()
                    for item in lines
                ),
                "observed": lines,
            }
        )
    else:
        codes = [reason.get("code") for reason in result.get("reasons") or []]
        checks.append(
            {
                "name": "duplicate_stamp_detected",
                "passed": "DUPLICATE_STAMP" in codes,
                "observed": codes,
            }
        )
    return checks


def run(base_url: str, timeout_seconds: int) -> None:
    from playwright.sync_api import sync_playwright

    review_token = os.getenv("PLUM_REVIEW_TOKEN", "")
    if not review_token:
        raise RuntimeError(
            "PLUM_REVIEW_TOKEN is required so the benchmark can verify and screenshot the review queue."
        )
    responses = ARTIFACT_DIR / "responses"
    screenshots = ARTIFACT_DIR / "screenshots"
    responses.mkdir(parents=True, exist_ok=True)
    screenshots.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        for scenario in SCENARIOS:
            page_errors.clear()
            page.goto(base_url, wait_until="networkidle")
            page.locator('select[name="member_id"]').select_option(scenario.member_id)
            page.locator('select[name="claim_category"]').select_option(scenario.category)
            page.locator('input[name="treatment_date"]').fill(scenario.treatment_date)
            page.locator('input[name="claimed_amount"]').fill(scenario.claimed_amount)
            page.locator('select[name="pre_authorization_obtained"]').select_option("")
            page.locator('input[name="files"]').set_input_files(
                [str(PDF_DIR / filename) for filename in scenario.documents]
            )
            page.screenshot(path=str(screenshots / f"form-{scenario.name}.png"), full_page=True)
            page.locator("#submit-button").click()
            page.wait_for_url("**/claims/*", timeout=30000)
            claim_id = page.url.rsplit("/", 1)[-1]
            started = time.monotonic()
            while True:
                response = page.request.get(f"{base_url}/api/claims/{claim_id}")
                if not response.ok:
                    raise RuntimeError(f"Claim API returned HTTP {response.status}")
                claim = response.json()
                if claim["state"] not in {"QUEUED", "PROCESSING"}:
                    break
                if time.monotonic() - started > timeout_seconds:
                    raise TimeoutError(
                        f"{scenario.name} did not finish within {timeout_seconds} seconds"
                    )
                time.sleep(3)
            elapsed = round(time.monotonic() - started, 3)
            (responses / f"{scenario.name}.json").write_text(
                json.dumps(claim, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            page.goto(f"{base_url}/claims/{claim_id}", wait_until="networkidle")
            page.locator("#result-content").wait_for(state="visible", timeout=30000)
            page.screenshot(path=str(screenshots / f"outcome-{scenario.name}.png"), full_page=True)
            result = claim.get("result") or {}
            document_metrics = result.get("document_metrics") or result.get("metrics")
            evidence_checks = _evidence_checks(scenario, claim)
            record = {
                "scenario": scenario.name,
                "expected": scenario.expected,
                "matched": _matches_expected(scenario, claim)
                and all(check["passed"] for check in evidence_checks),
                "submitted_through_website": True,
                "verified_through_api": True,
                "browser_errors": list(page_errors),
                "claim_id": claim_id,
                "state": claim.get("state"),
                "decision": result.get("decision"),
                "approved_amount": result.get("approved_amount"),
                "reason_codes": [reason.get("code") for reason in result.get("reasons") or []],
                "document_metrics": document_metrics,
                "extracted_documents": _extracted_documents(claim),
                "evidence_checks": evidence_checks,
                "elapsed_seconds": elapsed,
                "response_file": str(responses / f"{scenario.name}.json"),
                "screenshot": str(screenshots / f"outcome-{scenario.name}.png"),
            }
            record["matched"] = record["matched"] and not record["browser_errors"]
            results.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        review_context = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            extra_http_headers={
                "X-Reviewer-ID": "live-ocr-benchmark",
                "X-Reviewer-Token": review_token,
            },
        )
        review_page = review_context.new_page()
        review_response = review_page.goto(f"{base_url}/ops", wait_until="networkidle")
        if review_response is None or not review_response.ok:
            status = "no response" if review_response is None else str(review_response.status)
            raise RuntimeError(f"Authenticated review queue returned {status}")
        review_page.screenshot(path=str(screenshots / "review-queue.png"), full_page=True)
        review_context.close()
        browser.close()
    summary = {
        "schema": "plum.live_ocr_outcome_results.v1",
        "provider": "sarvam_document_ai",
        "status": "PASS" if all(item["matched"] for item in results) else "FAIL",
        "passed": sum(item["matched"] for item in results),
        "total": len(results),
        "results": results,
    }
    (ARTIFACT_DIR / "benchmark-results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_summary(summary)
    print(
        json.dumps(
            {"status": summary["status"], "passed": summary["passed"], "total": summary["total"]}
        )
    )
    if summary["status"] != "PASS":
        raise SystemExit(1)


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Live Sarvam outcome benchmark",
        "",
        f"Status: **{summary['status']}** ({summary['passed']}/{summary['total']} scenarios matched).",
        "",
        "All inputs are synthetic image-only PDFs. Each claim was submitted through the website controls, polled through the local API, and visually retained as screenshots.",
        "",
        "**Scope boundary:** this is a bounded end-to-end outcome regression. It verifies these four synthetic workflows and selected critical fields; it is not a production OCR-accuracy, handwriting, multilingual, calibration, or population-level benchmark.",
        "",
        "| Scenario | Expected | State | Decision | Reasons | Provider calls/failures | Browser errors | Seconds | Match |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |",
    ]
    for item in summary["results"]:
        metrics = item.get("document_metrics") or {}
        calls = f"{metrics.get('provider_calls', 0)}/{metrics.get('provider_failures', 0)}"
        lines.append(
            f"| {item['scenario']} | {item['expected']} | {item['state']} | {item['decision']} | "
            f"{', '.join(item['reason_codes']) or 'none'} | {calls} | {len(item.get('browser_errors') or [])} | "
            f"{item['elapsed_seconds']} | {'Yes' if item['matched'] else 'NO'} |"
        )
    total_calls = sum(
        int((item.get("document_metrics") or {}).get("provider_calls", 0))
        for item in summary["results"]
    )
    total_failures = sum(
        int((item.get("document_metrics") or {}).get("provider_failures", 0))
        for item in summary["results"]
    )
    total_browser_errors = sum(len(item.get("browser_errors") or []) for item in summary["results"])
    lines += [
        "",
        f"Provider verification: **{total_calls} calls, {total_failures} failures**. Browser verification: **{total_browser_errors} page errors**.",
        "",
        "Raw API responses are under `output/live-ocr-benchmark/responses/`; input, filled-form, outcome, and authenticated review-queue screenshots are under `output/live-ocr-benchmark/screenshots/`.",
        "",
        "The broader labelled dirty-document accuracy benchmark remains `scripts.evaluate_documents --providers live`; its latest offline verification status is reported separately and must not be inferred from this outcome run.",
    ]
    return "\n".join(lines) + "\n"


def _write_summary(summary: dict[str, Any]) -> None:
    markdown = _summary_markdown(summary)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_DIR / "benchmark-summary.md").write_text(markdown, encoding="utf-8")
    REPORT_PATH.write_text(markdown, encoding="utf-8")


def render() -> None:
    results_path = ARTIFACT_DIR / "benchmark-results.json"
    if not results_path.exists():
        raise FileNotFoundError(f"Run the benchmark first; missing {results_path}")
    summary = json.loads(results_path.read_text(encoding="utf-8"))
    _write_summary(summary)
    print(json.dumps({"status": summary["status"], "report": str(REPORT_PATH)}))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("generate")
    subparsers.add_parser("render")
    runner = subparsers.add_parser("run")
    runner.add_argument("--base-url", default="http://127.0.0.1:8000")
    runner.add_argument("--timeout-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.command == "generate":
        generate()
    elif args.command == "render":
        render()
    else:
        run(args.base_url, args.timeout_seconds)


if __name__ == "__main__":
    main()
