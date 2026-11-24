"""Build the 4 small SYNTHETIC demo files in examples/ (made-up company, made-up numbers).

    python scripts/make_examples.py

Needs the dev extra (reportlab). Fixed random seed, so the same bytes come out every time.
"""

from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "examples"

VENDOR = "Riverview Industries"
HEADER = [
    ("Invoice No.", "RI-2025-1047"),
    ("Date", "2025-11-16"),
    ("Due Date", "2025-11-30"),
]
BILL_TO = ["Bill To:", "Acme Solutions Pvt. Ltd.", "42 Tech Park Road", "Bengaluru 560001"]
ROWS = [
    ("Consulting Services", "10", "2,000.00", "20,000.00"),
    ("Software License", "1", "15,000.00", "15,000.00"),
    ("Support & Maintenance", "1", "5,000.00", "5,000.00"),
]
TOTAL = "40,000.00"


def invoice_pdf() -> bytes:
    """A digital PDF invoice with a real, ruled table."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title="Invoice RI-2025-1047", author="synthetic")
    story = [
        Paragraph(VENDOR, styles["Title"]),
        Paragraph("Build. Innovate. Grow.", styles["Normal"]),
        Spacer(1, 18),
        Paragraph("INVOICE", styles["Heading1"]),
    ]
    story += [Paragraph(f"{key}: {value}", styles["Normal"]) for key, value in HEADER]
    story.append(Spacer(1, 12))
    story += [Paragraph(line, styles["Normal"]) for line in BILL_TO]
    story.append(Spacer(1, 18))
    data = [["Description", "Qty", "Rate", "Amount"], *[list(r) for r in ROWS]]
    data.append(["", "", "Total", TOTAL])
    table = Table(data, colWidths=[230, 50, 90, 100])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.8, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e3f4f7")),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (2, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    story += [table, Spacer(1, 24), Paragraph("Thank you for your business!", styles["Normal"])]
    doc.build(story)
    return buffer.getvalue()


def scanned_invoice_pdf(source_pdf: bytes, seed: int = 11) -> bytes:
    """Make the invoice look scanned: a picture only, slightly tilted, grainy, no text layer."""
    import pypdfium2 as pdfium

    rng = random.Random(seed)
    page = pdfium.PdfDocument(source_pdf)[0]
    image = page.render(scale=200 / 72).to_pil().convert("L")
    image = image.rotate(1.0, fillcolor=255, resample=Image.BICUBIC)
    image = image.filter(ImageFilter.GaussianBlur(0.7))
    pixels = image.load()
    for _ in range(image.width * image.height // 70):
        x, y = rng.randrange(image.width), rng.randrange(image.height)
        pixels[x, y] = max(0, min(255, pixels[x, y] + rng.randint(-55, 55)))
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=200)
    return buffer.getvalue()


def invoice_docx() -> bytes:
    from docx import Document

    doc = Document()
    doc.core_properties.author = "synthetic"
    doc.add_heading(VENDOR, level=1)
    doc.add_paragraph("Quotation for Acme Solutions Pvt. Ltd.")
    for key, value in HEADER:
        doc.add_paragraph(f"{key}: {value}")
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for cell, text in zip(
        table.rows[0].cells, ("Description", "Qty", "Rate", "Amount"), strict=True
    ):
        cell.text = text
    for row in ROWS:
        for cell, text in zip(table.add_row().cells, row, strict=True):
            cell.text = text
    doc.add_paragraph(f"Total: {TOTAL}")
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def line_items_xlsx() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    book.properties.creator = "synthetic"
    sheet = book.active
    sheet.title = "Line items"
    sheet.append(["Description", "Qty", "Rate", "Amount"])
    for description, qty, rate, amount in ROWS:
        sheet.append(
            [description, int(qty), float(rate.replace(",", "")), float(amount.replace(",", ""))]
        )
    sheet.append(["Total", None, None, 40000.0])
    summary = book.create_sheet("Summary")
    summary.append(["Vendor", VENDOR])
    summary.append(["Invoice No.", HEADER[0][1]])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    pdf = invoice_pdf()
    files = {
        "invoice_digital.pdf": pdf,
        "invoice_scanned.pdf": scanned_invoice_pdf(pdf),
        "quotation.docx": invoice_docx(),
        "line_items.xlsx": line_items_xlsx(),
    }
    for name, data in files.items():
        (OUT / name).write_bytes(data)
        print(f"wrote examples/{name}  ({len(data) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
