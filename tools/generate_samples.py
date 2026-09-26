"""Generate fictional OPD documents for local upload checks.

Only synthetic names and clinic details are used. The files are intentionally
simple evidence samples; they do not stand in for handwritten OCR accuracy.
"""

from __future__ import annotations

import argparse
from pathlib import Path

PRESCRIPTION = [
    "PRESCRIPTION",
    "Dr. Arun Sharma",
    "Reg. No: KA/45678/2015",
    "Patient: Rajesh Kumar",
    "Date: 01-Nov-2024",
    "Diagnosis: Viral Fever",
    "Rx: Paracetamol 650mg for 5 days",
    "City Medical Centre, Bengaluru",
]
BILL = [
    "CITY MEDICAL CENTRE",
    "HOSPITAL BILL / RECEIPT",
    "Patient: Rajesh Kumar",
    "Date: 01-Nov-2024",
    "Bill No: CMC-2024-08321",
    "Consultation Fee 1000.00",
    "CBC Test 300.00",
    "Dengue NS1 Test 200.00",
    "Total Amount: 1500.00",
]


# Common scalable fonts, resolved by Pillow's own font search on each platform.
# None is required: Pillow's bundled scalable default is the final fallback.
FONT_CANDIDATES = ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf", "LiberationSans-Regular.ttf", "Helvetica.ttc")


def _font(size: int):
    from PIL import ImageFont

    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def write_samples(directory: Path) -> list[Path]:
    """Write every sample and return all generated paths."""
    import pymupdf as fitz
    from PIL import Image, ImageDraw

    directory.mkdir(parents=True, exist_ok=True)
    pdf_path = directory / "synthetic_prescription.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    for row, line in enumerate(PRESCRIPTION):
        page.insert_text((52, 65 + row * 30), line, fontsize=14)
    pdf.save(pdf_path)
    pdf.close()

    bill_pdf = fitz.open()
    bill_page = bill_pdf.new_page(width=595, height=842)
    for row, line in enumerate(BILL):
        bill_page.insert_text((52, 65 + row * 30), line, fontsize=14)
    bill_pdf_path = directory / "synthetic_hospital_bill.pdf"
    bill_pdf.save(bill_pdf_path)
    bill_pdf.close()

    image_path = directory / "synthetic_hospital_bill.png"
    image = Image.new("RGB", (1000, 1250), "white")
    draw = ImageDraw.Draw(image)
    font = _font(32)
    for row, line in enumerate(BILL):
        draw.text((72, 90 + row * 90), line, font=font, fill="black")
    image.save(image_path)
    return [pdf_path, bill_pdf_path, image_path]


def create_samples(directory: Path) -> list[Path]:
    """Write every sample; return the prescription PDF and bill image (existing callers' contract)."""
    prescription, _bill_pdf, bill_image = write_samples(directory)
    return [prescription, bill_image]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default=".data/samples")
    for path in write_samples(Path(parser.parse_args().output)):
        print(path)
