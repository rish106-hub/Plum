"""Generate the labelled synthetic "dirty document" corpus.

Every document is fictional and reuses names from the supplied policy and guide.
Pages are laid out as HTML with PyMuPDF (which shapes Devanagari correctly with
its bundled Noto fonts), rasterised, and then degraded with seeded Pillow
operations so that the files carry no selectable text layer. The OCR path is
therefore the only way to read them.

The corpus is committed under ``tests/fixtures/documents``. ``labels.json`` is
the ground truth; its per-file SHA-256 ties each label to the exact bytes the
benchmark reads. Regeneration is deterministic on one machine, but font and
codec versions can change bytes elsewhere, so ``--check`` reports drift instead
of silently rewriting labels.

    .venv/bin/python -m tools.generate_dirty_documents            # rewrite corpus
    .venv/bin/python -m tools.generate_dirty_documents --check    # compare only
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "tests" / "fixtures" / "documents"
LABELS_FILE = "labels.json"
A4 = (595, 842)
A5 = (420, 595)
INK = (24, 32, 110)
HAND_FONTS = (
    "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    "/System/Library/Fonts/Noteworthy.ttc",
    "/System/Library/Fonts/MarkerFelt.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
)
STAMP_FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

# Ground truth. ``fields`` holds values a correct reader should return;
# ``abstain_allowed_fields`` may be left empty without penalty (obscured on
# purpose); ``absent_fields`` must NOT be returned (not visible on the page);
# ``forbidden_values`` are visible-but-wrong values (e.g. struck-through).
# ``expected_behavior``: extract | abstain | request_reupload.
LABELS: list[dict[str, Any]] = [
    {
        "id": "rx_clean_scan",
        "file": "rx_clean_scan.png",
        "conditions": ["image_only", "clean_scan"],
        "document_type": "PRESCRIPTION",
        "claim_category": "CONSULTATION",
        "member_name": "Rajesh Kumar",
        "fields": {
            "patient_name": "Rajesh Kumar",
            "doctor_name": "Dr. Arun Sharma",
            "doctor_registration": "KA/45678/2015",
            "date": "01-Nov-2024",
            "diagnosis": "Viral Fever",
        },
        "critical_fields": ["patient_name", "diagnosis"],
        "expected_behavior": "extract",
        "acceptable_behaviors": ["extract"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Straight, clean greyscale scan with no text layer. Baseline for the OCR path.",
    },
    {
        "id": "rx_handwritten",
        "file": "rx_handwritten.jpg",
        "conditions": ["image_only", "handwriting", "preprinted_template"],
        "document_type": "PRESCRIPTION",
        "claim_category": "CONSULTATION",
        "member_name": "Priya Singh",
        "fields": {
            "patient_name": "Priya Singh",
            "doctor_name": "Dr. S. Iyer",
            "doctor_registration": "TN/56789/2013",
            "date": "12-Oct-2024",
            "diagnosis": "Acute Bronchitis",
        },
        "critical_fields": ["patient_name", "diagnosis"],
        "expected_behavior": "extract",
        "acceptable_behaviors": ["extract", "abstain", "request_reupload"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Printed letterhead with handwriting-style fill-ins (jittered, rotated words). Holding is safe; a wrong confident name or diagnosis is not.",
    },
    {
        "id": "rx_stamp_over_registration",
        "file": "rx_stamp_over_registration.jpg",
        "conditions": ["image_only", "rubber_stamp", "obscured_registration"],
        "document_type": "PRESCRIPTION",
        "claim_category": "CONSULTATION",
        "member_name": "Deepak Shah",
        "fields": {
            "patient_name": "Deepak Shah",
            "doctor_name": "Dr. Venkat Rao",
            "doctor_registration": "AP/67890/2017",
            "date": "05-Nov-2024",
            "diagnosis": "Migraine",
        },
        "abstain_allowed_fields": ["doctor_registration"],
        "critical_fields": ["patient_name", "diagnosis"],
        "expected_behavior": "extract",
        "acceptable_behaviors": ["extract"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Violet clinic stamp printed over the registration number. Guide: low-confidence field, do not fail the whole document.",
    },
    {
        "id": "rx_hindi_english",
        "file": "rx_hindi_english.jpg",
        "conditions": ["image_only", "multilingual", "devanagari"],
        "document_type": "PRESCRIPTION",
        "claim_category": "CONSULTATION",
        "member_name": "Sunita Kumar",
        "fields": {
            "patient_name": "Sunita Kumar",
            "doctor_name": "Dr. R. Gupta",
            "doctor_registration": "DL/34567/2016",
            "date": "20-Oct-2024",
            "diagnosis": "Type 2 Diabetes",
        },
        "critical_fields": ["patient_name", "diagnosis"],
        "expected_behavior": "extract",
        "acceptable_behaviors": ["extract"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Bilingual Hindi/English prescription. English fields must be extracted; Hindi-only lines are not adjudication material.",
    },
    {
        "id": "rx_scanned_pdf",
        "file": "rx_scanned_pdf.pdf",
        "conditions": ["scanned_pdf", "no_text_layer", "scan_noise"],
        "document_type": "PRESCRIPTION",
        "claim_category": "CONSULTATION",
        "member_name": "Vikram Joshi",
        "fields": {
            "patient_name": "Vikram Joshi",
            "doctor_name": "Dr. Arun Sharma",
            "doctor_registration": "KA/45678/2015",
            "date": "15-Sep-2024",
            "diagnosis": "Hypertension",
        },
        "critical_fields": ["patient_name", "diagnosis"],
        "expected_behavior": "extract",
        "acceptable_behaviors": ["extract"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Image-only PDF: the local PDF text parser finds nothing, so this must route to OCR.",
    },
    {
        "id": "bill_phone_photo_skewed",
        "file": "bill_phone_photo_skewed.jpg",
        "conditions": ["image_only", "phone_photo", "skew", "uneven_lighting", "blur", "noise"],
        "document_type": "HOSPITAL_BILL",
        "claim_category": "CONSULTATION",
        "member_name": "Rajesh Kumar",
        "fields": {
            "patient_name": "Rajesh Kumar",
            "date": "01-Nov-2024",
            "bill_number": "CMC/2024/08321",
            "line_item_amounts": [1000.0, 200.0, 300.0],
            "total": 1500.0,
        },
        "critical_fields": ["patient_name", "total", "line_item_amounts"],
        "expected_behavior": "extract",
        "acceptable_behaviors": ["extract", "request_reupload"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Rotated 6 degrees on a dark table, shadow gradient, Gaussian blur and sensor noise.",
    },
    {
        "id": "bill_multipage_scan",
        "file": "bill_multipage_scan.pdf",
        "conditions": ["scanned_pdf", "multi_page", "no_text_layer"],
        "document_type": "HOSPITAL_BILL",
        "claim_category": "CONSULTATION",
        "member_name": "Sneha Reddy",
        "pages": 2,
        "fields": {
            "patient_name": "Sneha Reddy",
            "date": "18-Oct-2024",
            "bill_number": "APL/2024/55120",
            "line_item_amounts": [800.0, 700.0, 500.0],
            "total": 2000.0,
        },
        "critical_fields": ["patient_name", "total", "line_item_amounts"],
        "expected_behavior": "extract",
        "acceptable_behaviors": ["extract"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Two scanned pages; items continue on page 2 where the total appears. Line items must be aggregated across pages.",
    },
    {
        "id": "bill_cropped_partial",
        "file": "bill_cropped_partial.jpg",
        "conditions": ["image_only", "partial_page", "cropped"],
        "document_type": "HOSPITAL_BILL",
        "claim_category": "CONSULTATION",
        "member_name": "Rajesh Kumar",
        "fields": {"patient_name": "Rajesh Kumar", "date": "01-Nov-2024"},
        "absent_fields": ["total"],
        "critical_fields": ["patient_name", "total"],
        "expected_behavior": "request_reupload",
        "acceptable_behaviors": ["request_reupload", "abstain"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Page torn below the second line item: the total is not visible, so any extracted total is fabricated.",
    },
    {
        "id": "bill_struck_correction",
        "file": "bill_struck_correction.jpg",
        "conditions": ["image_only", "struck_through_amount", "handwritten_correction"],
        "document_type": "HOSPITAL_BILL",
        "claim_category": "CONSULTATION",
        "member_name": "Priya Singh",
        "fields": {"patient_name": "Priya Singh", "date": "12-Oct-2024", "total": 1300.0},
        "forbidden_values": {"total": [1500.0, 1200.0], "line_item_amounts": [1200.0]},
        "critical_fields": ["patient_name", "total"],
        "expected_behavior": "abstain",
        "acceptable_behaviors": ["abstain", "request_reupload"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Printed 1200.00 consultation fee struck through and hand-corrected to 1000; guide says flag as a document alteration, so it should not pay automatically.",
    },
    {
        "id": "bill_low_contrast",
        "file": "bill_low_contrast.jpg",
        "conditions": ["image_only", "very_low_contrast", "faded_photocopy"],
        "document_type": "HOSPITAL_BILL",
        "claim_category": "CONSULTATION",
        "member_name": "Rajesh Kumar",
        "fields": {"patient_name": "Rajesh Kumar", "total": 1500.0, "line_item_amounts": [1000.0, 200.0, 300.0]},
        "critical_fields": ["patient_name", "total", "line_item_amounts"],
        "expected_behavior": "request_reupload",
        "acceptable_behaviors": ["request_reupload", "abstain", "extract"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Faded photocopy that passes the local contrast gate. Re-upload is preferred; extraction is acceptable only if every critical field is right.",
    },
    {
        "id": "blank_page",
        "file": "blank_page.jpg",
        "conditions": ["image_only", "blank", "no_content"],
        "document_type": None,
        "claim_category": "CONSULTATION",
        "member_name": "Rajesh Kumar",
        "fields": {},
        "absent_fields": ["patient_name", "total"],
        "critical_fields": [],
        "expected_behavior": "request_reupload",
        "acceptable_behaviors": ["request_reupload"],
        "offline_expected_code": "UNREADABLE_IMAGE",
        "notes": "Near-blank photo of paper. Rejected locally before any provider call.",
    },
    {
        "id": "lab_report_scan",
        "file": "lab_report_scan.jpg",
        "conditions": ["image_only", "scan_noise", "tabular"],
        "document_type": "LAB_REPORT",
        "claim_category": "DIAGNOSTIC",
        "member_name": "Rajesh Kumar",
        "fields": {
            "patient_name": "Rajesh Kumar",
            "date": "01-Nov-2024",
            "test_name": "Complete Blood Count",
        },
        "critical_fields": ["patient_name", "date"],
        "expected_behavior": "extract",
        "acceptable_behaviors": ["extract"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "NABL lab report with a results table, sample/report dates and pathologist registration.",
    },
    {
        "id": "pharmacy_bill_scan",
        "file": "pharmacy_bill_scan.jpg",
        "conditions": ["image_only", "scan_noise", "tabular"],
        "document_type": "PHARMACY_BILL",
        "claim_category": "PHARMACY",
        "member_name": "Rajesh Kumar",
        "fields": {
            "patient_name": "Rajesh Kumar",
            "date": "01-Nov-2024",
            "bill_number": "HFP-24-09821",
            "line_item_amounts": [37.5, 40.0],
            "total": 77.5,
        },
        "critical_fields": ["patient_name", "total", "line_item_amounts"],
        "expected_behavior": "extract",
        "acceptable_behaviors": ["extract"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Pharmacy bill with drug licence, batch/expiry columns and explicit Generic/Branded markers.",
    },
    {
        "id": "non_medical_receipt",
        "file": "non_medical_receipt.jpg",
        "conditions": ["image_only", "wrong_document", "non_medical"],
        "document_type": "UNKNOWN",
        "claim_category": "CONSULTATION",
        "member_name": "Rajesh Kumar",
        "fields": {},
        "absent_fields": ["patient_name"],
        "critical_fields": [],
        "expected_behavior": "request_reupload",
        "acceptable_behaviors": ["request_reupload"],
        "offline_expected_code": "EXTRACTION_UNAVAILABLE",
        "notes": "Restaurant receipt with a total. Must be classified UNKNOWN, never as a medical bill.",
    },
]


# --------------------------------------------------------------------- rendering


def _render(html: str, size: tuple[int, int] = A5, dpi: int = 110, find: tuple[str, ...] = ()) -> tuple[Image.Image, dict[str, tuple[float, float, float, float]]]:
    """Lay out HTML on a page, rasterise it, and return pixel boxes for ``find``."""
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=size[0], height=size[1])
    page.insert_htmlbox(pymupdf.Rect(28, 28, size[0] - 28, size[1] - 28), html)
    scale = dpi / 72
    boxes = {}
    for needle in find:
        hits = page.search_for(needle)
        if hits:
            rect = hits[0]
            boxes[needle] = (rect.x0 * scale, rect.y0 * scale, rect.x1 * scale, rect.y1 * scale)
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    document.close()
    return image, boxes


def _font(paths: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _handwrite(image: Image.Image, xy: tuple[float, float], text: str, rng: random.Random, size: int = 26) -> None:
    """Paste word tiles with seeded jitter and rotation to mimic handwriting."""
    font = _font(HAND_FONTS, size)
    x, y = xy
    for word in text.split():
        left, top, right, bottom = font.getbbox(word)
        tile = Image.new("RGBA", (int(right - left) + 12, int(bottom - top) + 12), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((6 - left, 6 - top), word, font=font, fill=INK + (235,))
        tile = tile.rotate(rng.uniform(-4.0, 4.0), resample=Image.Resampling.BICUBIC, expand=True)
        image.paste(tile, (int(x + rng.uniform(-2, 2)), int(y + rng.uniform(-4, 4))), tile)
        x += tile.width + rng.uniform(4, 10)


def _noise(image: Image.Image, rng: random.Random, amount: float) -> Image.Image:
    grain = Image.frombytes("L", image.size, rng.randbytes(image.size[0] * image.size[1])).convert(image.mode)
    return Image.blend(image, grain, amount)


def _scan(image: Image.Image, rng: random.Random, angle: float = 0.5, grain: float = 0.05) -> Image.Image:
    gray = image.convert("L").rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=250)
    return _noise(gray, rng, grain)


def _jpeg(image: Image.Image, quality: int = 68) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _scanned_pdf(pages: list[Image.Image]) -> bytes:
    """Image-only PDF with no text layer and no volatile metadata."""
    import pymupdf

    document = pymupdf.open()
    for image in pages:
        page = document.new_page(width=A4[0], height=A4[1])
        page.insert_image(page.rect, stream=_jpeg(image, 62))
    document.set_metadata({})
    data = document.tobytes(garbage=4, deflate=True, no_new_id=True)
    document.close()
    return data


STYLE = "font-family:sans-serif;font-size:11px;line-height:1.35"


def _rx_html(doctor: str, qualification: str, registration: str, clinic: str, patient: str, date: str, diagnosis: str, medicines: list[str], tests: str = "") -> str:
    meds = "<br>".join(f"{index}. {item}" for index, item in enumerate(medicines, 1))
    investigations = f"<p>Investigations: {tests}</p>" if tests else ""
    return (
        f'<div style="{STYLE}"><p style="font-size:15px"><b>{doctor}</b>, {qualification}</p>'
        f"<p>Reg. No: {registration}</p><p>{clinic}</p><hr>"
        f"<p>Patient: {patient} &nbsp;&nbsp;&nbsp; Date: {date}</p><hr>"
        f"<p>Diagnosis: {diagnosis}</p><p><b>Rx:</b><br>{meds}</p>{investigations}"
        f'<p style="text-align:right"><br><br>[Signature]</p></div>'
    )


def _bill_html(header: str, address: str, bill_no: str, date: str, patient: str, doctor: str, items: list[tuple[str, str]], total: str | None, extra: str = "") -> str:
    rows = "".join(f'<tr><td>{name}</td><td align="right">{amount}</td></tr>' for name, amount in items)
    total_html = f'<hr><p><b>Total Amount: {total}</b></p><p>Payment Mode: UPI</p>' if total else ""
    return (
        f'<div style="{STYLE}"><p style="font-size:16px"><b>{header}</b></p><p>{address}</p><hr>'
        f"<p><b>BILL / RECEIPT</b><br>Bill No: {bill_no} &nbsp;&nbsp; Date: {date}</p>"
        f"<p>Patient Name: {patient}<br>Referring Doctor: {doctor}</p><hr>"
        f'<table style="width:100%"><tr><th align="left">DESCRIPTION</th><th align="right">AMOUNT</th></tr>{rows}</table>'
        f"{total_html}{extra}</div>"
    )


CMC_ITEMS = [("Consultation Fee (OPD)", "1000.00"), ("CBC (Complete Blood Count)", "200.00"), ("Dengue NS1 Antigen Test", "300.00")]


def _rx_clean_scan(rng: random.Random) -> bytes:
    image, _ = _render(_rx_html(
        "Dr. Arun Sharma", "MBBS, MD (Internal Medicine)", "KA/45678/2015", "City Medical Centre, 12 MG Road, Bengaluru",
        "Rajesh Kumar", "01-Nov-2024", "Viral Fever", ["Tab Paracetamol 650mg 1-1-1 x 5 days", "Tab Vitamin C 500mg 0-0-1 x 7 days"], "CBC, Dengue NS1",
    ))
    return _png(image.convert("L").rotate(0.4, resample=Image.Resampling.BICUBIC, fillcolor=255))


def _rx_handwritten(rng: random.Random) -> bytes:
    template = (
        f'<div style="{STYLE}"><p style="font-size:15px"><b>Dr. S. Iyer</b>, MBBS, MD (Pulmonology)</p>'
        "<p>Reg. No: TN/56789/2013</p><p>Iyer Chest Clinic, 8 Anna Salai, Chennai</p><hr>"
        "<p>Patient:</p><p><br></p><p>Date:</p><hr><p>Diagnosis:</p><p><br></p><p><b>Rx:</b></p></div>"
    )
    image, boxes = _render(template, find=("Patient:", "Date:", "Diagnosis:", "Rx:"))
    fills = {
        "Patient:": "Priya Singh",
        "Date:": "12-Oct-2024",
        "Diagnosis:": "Acute Bronchitis",
    }
    for label, text in fills.items():
        x0, y0, x1, y1 = boxes[label]
        _handwrite(image, (x1 + 10, y0 - 12), text, rng)
    x0, y0, x1, y1 = boxes["Rx:"]
    for row, line in enumerate(["Tab Azithromycin 500mg OD x 3 days", "Syp Ambroxol 5ml TDS x 5 days"]):
        _handwrite(image, (x0 + 20, y1 + 10 + row * 40), line, rng, size=22)
    image = image.filter(ImageFilter.GaussianBlur(0.6))
    return _jpeg(_scan(image, rng, angle=-1.2, grain=0.06).convert("RGB"), 70)


def _stamp(image: Image.Image, center: tuple[float, float], rng: random.Random) -> None:
    diameter = 150
    tile = Image.new("RGBA", (diameter + 20, diameter + 20), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    violet = (92, 52, 170, 190)
    draw.ellipse((10, 10, diameter + 10, diameter + 10), outline=violet, width=5)
    draw.ellipse((28, 28, diameter - 8, diameter - 8), outline=violet, width=2)
    font = _font(STAMP_FONTS, 17)
    for row, text in enumerate(["RAO CLINIC", "VIJAYAWADA", "* VERIFIED *"]):
        width = draw.textlength(text, font=font)
        draw.text(((diameter + 20 - width) / 2, 52 + row * 24), text, font=font, fill=violet)
    tile = tile.rotate(rng.uniform(-18, -12), resample=Image.Resampling.BICUBIC, expand=True)
    image.paste(tile, (int(center[0] - tile.width / 2), int(center[1] - tile.height / 2)), tile)


def _rx_stamp_over_registration(rng: random.Random) -> bytes:
    image, boxes = _render(_rx_html(
        "Dr. Venkat Rao", "MBBS, DM (Neurology)", "AP/67890/2017", "Rao Neuro Clinic, 4 MG Road, Vijayawada",
        "Deepak Shah", "05-Nov-2024", "Migraine", ["Tab Naproxen 250mg SOS", "Tab Propranolol 20mg 0-0-1 x 30 days"],
    ), find=("AP/67890/2017",))
    x0, y0, x1, y1 = boxes["AP/67890/2017"]
    _stamp(image, ((x0 + x1) / 2 + 10, (y0 + y1) / 2), rng)
    return _jpeg(_noise(image, rng, 0.04), 72)


def _rx_hindi_english(rng: random.Random) -> bytes:
    html = (
        f'<div style="{STYLE}"><p style="font-size:15px"><b>Dr. R. Gupta</b>, MBBS, MD (Medicine)</p>'
        "<p>डॉ. आर. गुप्ता — मधुमेह एवं थायरॉइड क्लिनिक</p>"
        "<p>Reg. No: DL/34567/2016</p><p>Gupta Clinic, 21 Lajpat Nagar, New Delhi</p><hr>"
        "<p>Patient: Sunita Kumar &nbsp;&nbsp; Date: 20-Oct-2024</p><p>मरीज़ का नाम: सुनीता कुमार &nbsp; आयु: 36 वर्ष</p><hr>"
        "<p>Diagnosis: Type 2 Diabetes (T2DM)</p><p>निदान: मधुमेह (टाइप 2)</p>"
        "<p><b>Rx:</b><br>1. Tab Metformin 500mg 1-0-1 — भोजन के बाद<br>2. Tab Vitamin B12 0-0-1 x 30 days</p>"
        "<p>सलाह: रोज़ 30 मिनट पैदल चलें। चीनी कम लें।</p>"
        '<p style="text-align:right"><br>[हस्ताक्षर / Signature]</p></div>'
    )
    image, _ = _render(html)
    return _jpeg(_scan(image, rng, angle=0.8, grain=0.05).convert("RGB"), 72)


def _rx_scanned_pdf(rng: random.Random) -> bytes:
    image, _ = _render(_rx_html(
        "Dr. Arun Sharma", "MBBS, MD (Internal Medicine)", "KA/45678/2015", "City Medical Centre, 12 MG Road, Bengaluru",
        "Vikram Joshi", "15-Sep-2024", "Hypertension (HTN)", ["Tab Amlodipine 5mg 1-0-0 x 30 days"], "Lipid Profile",
    ), size=A4, dpi=100)
    return _scanned_pdf([_scan(image, rng, angle=-0.7, grain=0.07)])


def _phone_photo(image: Image.Image, rng: random.Random, angle: float) -> Image.Image:
    shadow = Image.linear_gradient("L").transpose(Image.Transpose.ROTATE_90).resize(image.size).point(lambda v: 255 - v * 90 // 255)
    lit = ImageChops.multiply(image, Image.merge("RGB", (shadow, shadow, shadow)))
    table = (58, 50, 44)
    rotated = lit.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=table)
    canvas = Image.new("RGB", (rotated.width + 60, rotated.height + 60), table)
    canvas.paste(rotated, (30, 30))
    return _noise(canvas.filter(ImageFilter.GaussianBlur(1.1)), rng, 0.07)


def _bill_phone_photo_skewed(rng: random.Random) -> bytes:
    image, _ = _render(_bill_html(
        "CITY MEDICAL CENTRE", "12 MG Road, Bengaluru - 560001 | GSTIN: 29ABCDE1234F1Z5", "CMC/2024/08321", "01-Nov-2024",
        "Rajesh Kumar", "Dr. Arun Sharma", CMC_ITEMS, "1500.00",
    ))
    return _jpeg(_phone_photo(image, rng, 6.0), 66)


def _bill_multipage_scan(rng: random.Random) -> bytes:
    first, _ = _render(_bill_html(
        "APOLLO HOSPITALS", "Jubilee Hills, Hyderabad - 500033", "APL/2024/55120", "18-Oct-2024", "Sneha Reddy", "Dr. S. Khan",
        [("Consultation Fee (OPD)", "800.00"), ("X-Ray Chest PA View", "700.00")], None,
        '<p style="text-align:right"><i>continued on page 2</i></p>',
    ), size=A4, dpi=100)
    second, _ = _render(
        f'<div style="{STYLE}"><p><b>APOLLO HOSPITALS</b> — Bill No: APL/2024/55120 (page 2 of 2)</p><p>Patient Name: Sneha Reddy</p><hr>'
        '<table style="width:100%"><tr><th align="left">DESCRIPTION</th><th align="right">AMOUNT</th></tr>'
        '<tr><td>ECG (12 lead)</td><td align="right">500.00</td></tr></table>'
        "<hr><p>Subtotal: 2000.00<br>GST (0% on medical): 0.00</p><p><b>Total Amount: 2000.00</b></p></div>",
        size=A4, dpi=100,
    )
    return _scanned_pdf([_scan(first, rng, angle=0.6), _scan(second, rng, angle=-0.9)])


def _bill_cropped_partial(rng: random.Random) -> bytes:
    image, boxes = _render(_bill_html(
        "CITY MEDICAL CENTRE", "12 MG Road, Bengaluru - 560001", "CMC/2024/08377", "01-Nov-2024",
        "Rajesh Kumar", "Dr. Arun Sharma", CMC_ITEMS, "1500.00",
    ), find=("CBC (Complete Blood Count)",))
    cut = int(boxes["CBC (Complete Blood Count)"][3] + 6)
    torn = image.crop((0, 0, image.width, cut + 30))
    draw = ImageDraw.Draw(torn)
    edge = [(x, cut + rng.randint(-8, 14)) for x in range(0, torn.width + 20, 20)]
    draw.polygon(edge + [(torn.width, torn.height), (0, torn.height)], fill=(40, 36, 32))
    return _jpeg(_noise(torn, rng, 0.05), 70)


def _bill_struck_correction(rng: random.Random) -> bytes:
    image, boxes = _render(_bill_html(
        "CITY CLINIC, BENGALURU", "7 Residency Road, Bengaluru - 560025", "CC/2024/1187", "12-Oct-2024",
        "Priya Singh", "Dr. S. Iyer", [("Consultation Fee (OPD)", "1200.00"), ("Chest Examination", "300.00")], "1500.00",
    ), find=("1200.00", "1500.00"))
    draw = ImageDraw.Draw(image)
    for needle, correction in (("1200.00", "1000/-"), ("1500.00", "1300/-")):
        x0, y0, x1, y1 = boxes[needle]
        middle = (y0 + y1) / 2
        draw.line((x0 - 4, middle + 1, x1 + 4, middle - 2), fill=INK, width=3)
        draw.line((x0 - 4, middle - 3, x1 + 4, middle + 2), fill=INK, width=2)
        _handwrite(image, (x0 - 95, y0 - 10) if needle == "1200.00" else (x1 + 12, y0 - 10), correction, rng, size=24)
    return _jpeg(_noise(image, rng, 0.04), 72)


def _bill_low_contrast(rng: random.Random) -> bytes:
    image, _ = _render(_bill_html(
        "CITY MEDICAL CENTRE", "12 MG Road, Bengaluru - 560001", "CMC/2024/08399", "01-Nov-2024",
        "Rajesh Kumar", "Dr. Arun Sharma", CMC_ITEMS, "1500.00",
    ))
    faded = image.convert("L").point(lambda v: 232 + v * 18 // 255)
    return _jpeg(_noise(faded, rng, 0.03), 60)


def _blank_page(rng: random.Random) -> bytes:
    paper = Image.new("L", (640, 900), 247)
    return _jpeg(_noise(paper, rng, 0.008), 60)


def _lab_report_scan(rng: random.Random) -> bytes:
    rows = [
        ("Hemoglobin", "13.2", "g/dL", "13.0 - 17.0"),
        ("WBC Count", "9,800", "/uL", "4,500 - 11,000"),
        ("Platelet Count", "185,000", "/uL", "150,000 - 450,000"),
        ("Dengue NS1 Antigen", "NEGATIVE", "", "-"),
    ]
    table = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in rows)
    html = (
        f'<div style="{STYLE}"><p style="font-size:16px"><b>PRECISION DIAGNOSTICS PVT LTD</b></p>'
        "<p>NABL Accredited Lab | Lab ID: KA-NABL-1234<br>45 Jayanagar, Bengaluru</p><hr>"
        "<p><b>LAB REPORT</b></p>"
        "<p>Patient: Rajesh Kumar &nbsp;&nbsp; Age/Sex: 39 / Male<br>Ref Doctor: Dr. Arun Sharma<br>"
        "Sample Date: 01-Nov-2024 &nbsp;&nbsp; Report Date: 01-Nov-2024<br>Sample ID: PD-2024-18723</p><hr>"
        "<p>Test Name: Complete Blood Count (CBC) with Dengue NS1</p>"
        '<table style="width:100%"><tr><th align="left">TEST</th><th align="left">RESULT</th><th align="left">UNIT</th><th align="left">REFERENCE RANGE</th></tr>'
        f"{table}</table><hr><p>Remarks: WBC count is towards upper normal limit. Clinical correlation advised.</p>"
        "<p>Dr. Meena Pillai, MD (Pathology)<br>Reg. No: KA/89012/2018</p></div>"
    )
    image, _ = _render(html, size=A4, dpi=100)
    return _jpeg(_scan(image, rng, angle=-0.5, grain=0.05).convert("RGB"), 70)


def _pharmacy_bill_scan(rng: random.Random) -> bytes:
    html = (
        f'<div style="{STYLE}"><p style="font-size:16px"><b>HEALTH FIRST PHARMACY</b></p>'
        "<p>Drug Lic. No: KA-BLR-20-1234<br>22 Brigade Road, Bengaluru</p><hr>"
        "<p>Bill No: HFP-24-09821 &nbsp;&nbsp; Date: 01-Nov-2024<br>Patient: Rajesh Kumar &nbsp;&nbsp; Dr: Dr. Arun Sharma</p><hr>"
        '<table style="width:100%"><tr><th align="left">MEDICINE</th><th>BATCH</th><th>EXP</th><th>QTY</th><th>MRP</th><th align="right">AMT</th></tr>'
        '<tr><td>Paracetamol 650 (Generic)</td><td>A2341</td><td>03/26</td><td>15</td><td>2.50</td><td align="right">37.50</td></tr>'
        '<tr><td>Vitamin C 500 (Branded)</td><td>B7821</td><td>06/26</td><td>10</td><td>4.00</td><td align="right">40.00</td></tr></table>'
        "<hr><p><b>Net Amount: 77.50</b></p><p>Pharmacist: R. Sharma</p></div>"
    )
    image, _ = _render(html)
    return _jpeg(_scan(image, rng, angle=0.9, grain=0.05).convert("RGB"), 70)


def _non_medical_receipt(rng: random.Random) -> bytes:
    html = (
        f'<div style="{STYLE}"><p style="font-size:16px"><b>UDUPI GARDEN RESTAURANT</b></p>'
        "<p>3 Church Street, Bengaluru<br>GSTIN: 29AAAPL1234C1Z9</p><hr>"
        "<p>Table: 12 &nbsp;&nbsp; Date: 02-Nov-2024<br>Invoice No: UG-7781</p><hr>"
        '<table style="width:100%"><tr><td>Masala Dosa x2</td><td align="right">240.00</td></tr>'
        '<tr><td>Filter Coffee x3</td><td align="right">150.00</td></tr>'
        '<tr><td>Service</td><td align="right">60.00</td></tr></table>'
        "<hr><p><b>Total: 450.00</b></p><p>Thank you! Visit again.</p></div>"
    )
    image, _ = _render(html)
    return _jpeg(_phone_photo(image, rng, -3.0), 66)


BUILDERS: dict[str, Callable[[random.Random], bytes]] = {
    "rx_clean_scan": _rx_clean_scan,
    "rx_handwritten": _rx_handwritten,
    "rx_stamp_over_registration": _rx_stamp_over_registration,
    "rx_hindi_english": _rx_hindi_english,
    "rx_scanned_pdf": _rx_scanned_pdf,
    "bill_phone_photo_skewed": _bill_phone_photo_skewed,
    "bill_multipage_scan": _bill_multipage_scan,
    "bill_cropped_partial": _bill_cropped_partial,
    "bill_struck_correction": _bill_struck_correction,
    "bill_low_contrast": _bill_low_contrast,
    "blank_page": _blank_page,
    "lab_report_scan": _lab_report_scan,
    "pharmacy_bill_scan": _pharmacy_bill_scan,
    "non_medical_receipt": _non_medical_receipt,
}


def _mime(data: bytes) -> str:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    raise ValueError("unexpected generated format")


def build_corpus() -> tuple[dict[str, bytes], dict[str, Any]]:
    """Return ``{file_name: bytes}`` and the labels document, without writing."""
    files: dict[str, bytes] = {}
    documents = []
    for index, label in enumerate(LABELS):
        data = BUILDERS[label["id"]](random.Random(f"plum-dirty-{index}-{label['id']}"))
        files[label["file"]] = data
        documents.append({
            **label,
            "pages": label.get("pages", 1),
            "mime_type": _mime(data),
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    labels = {
        "schema_version": 1,
        "generator": "tools/generate_dirty_documents.py",
        "description": "Synthetic, fictional OPD documents without a text layer. Ground truth for the provider-backed OCR benchmark.",
        "documents": documents,
    }
    return files, labels


def write_corpus(directory: Path = CORPUS_DIR) -> dict[str, Any]:
    files, labels = build_corpus()
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    (directory / LABELS_FILE).write_text(json.dumps(labels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return labels


def check_corpus(directory: Path = CORPUS_DIR) -> list[str]:
    """Return drift messages between a fresh build and the committed corpus."""
    _, fresh = build_corpus()
    committed = json.loads((directory / LABELS_FILE).read_text(encoding="utf-8"))
    by_id = {doc["id"]: doc for doc in committed["documents"]}
    drift = []
    for doc in fresh["documents"]:
        old = by_id.get(doc["id"])
        if old is None:
            drift.append(f"{doc['id']}: missing from committed labels")
        elif old["sha256"] != doc["sha256"]:
            drift.append(f"{doc['id']}: bytes differ from committed file (fonts or codecs changed)")
    return drift


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=CORPUS_DIR)
    parser.add_argument("--check", action="store_true", help="compare a fresh build with the committed corpus without writing")
    args = parser.parse_args()
    if args.check:
        drift = check_corpus(args.output)
        for line in drift:
            print(line)
        print("corpus matches a fresh build" if not drift else f"{len(drift)} document(s) drifted")
        return 1 if drift else 0
    labels = write_corpus(args.output)
    total = sum(doc["bytes"] for doc in labels["documents"])
    for doc in labels["documents"]:
        print(f"{doc['file']:34} {doc['bytes']:>8} bytes  {doc['expected_behavior']}")
    print(f"{len(labels['documents'])} documents, {total / 1024:.0f} KiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
