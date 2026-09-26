"""Build labelled synthetic documents and exercise the real intake path.

This is a safety/route benchmark, not an OCR-accuracy claim. It verifies that
actual PDF and image bytes reach the document gate and that unsafe inputs route
to correction or review rather than a payment decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from claims.documents import process_uploads
from claims.fixtures import load_policy

ROOT = Path(__file__).resolve().parents[1]


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


def evaluate(output_dir: Path, report_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    policy = load_policy(ROOT / "data" / "policy_terms.json")
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
        ["HOSPITAL BILL", "Patient: Rajesh Kumar", "Consultation Fee 1000.00", "CBC Test 500.00", "Total Amount: 1500.00"],
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
            "metrics": result["metrics"],
            "documents": [{"file_name": doc["file_name"], "actual_type": doc["actual_type"], "quality": doc["quality"], "pages": doc["pages"]} for doc in result["documents"]],
        })
    passed = sum(record["matched"] for record in records)
    summary = {"kind": "synthetic_document_intake_safety_benchmark", "passed": passed, "total": len(records), "records": records}
    (report_dir / "document-evaluation.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Synthetic document intake evaluation",
        "",
        f"**{passed}/{len(records)}** labelled synthetic document scenarios matched their safe expected route.",
        "",
        "This run uses actual generated PDF/image bytes through `claims.documents.process_uploads`. It demonstrates intake routing, not OCR accuracy, handwriting recognition, multilingual extraction, or calibrated blur detection on real-world data.",
        "",
        "| Scenario | Expected safe route | Result | Match |",
        "| --- | --- | --- | --- |",
    ]
    for record in records:
        observed = "ACCEPTED" if not record["issue_codes"] else ", ".join(record["issue_codes"])
        lines.append(f"| {record['scenario']} | {record['expected']} | {observed} | {'Yes' if record['matched'] else 'No'} |")
    lines += [
        "",
        "## Scope and next evidence",
        "",
        "- Clean and multi-page selectable-text PDFs prove the local parsing branch, not visual OCR.",
        "- The blurred photo is not rejected by an uncalibrated local blur heuristic. Without a configured extraction provider it routes to operator review, avoiding a member-facing false positive.",
        "- A provider-backed, consented labelled corpus is still required before reporting field accuracy, handwriting support, multilingual performance, or false-rejection rates.",
    ]
    (report_dir / "document-evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".data" / "document-evaluation")
    parser.add_argument("--report-dir", type=Path, default=ROOT / "docs" / "reports")
    args = parser.parse_args()
    result = evaluate(args.output_dir, args.report_dir)
    print(f"{result['passed']}/{result['total']} document intake scenarios matched")
    return 0 if result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
