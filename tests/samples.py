"""Builds small SYNTHETIC test files (no real documents anywhere). Fixed seed => same bytes."""

from __future__ import annotations

import csv
import io
import json
import random
import zipfile
from email.message import EmailMessage

from PIL import Image, ImageFilter

INVOICE_LINES = [
    "ACME Drilling Supplies Ltd",
    "Invoice number: INV-2024-0042",
    "Invoice date: 2024-03-15",
    "Due date: 2024-04-14",
    "Bill to: Northern Star Energy",
    "Description        Qty   Unit price   Amount",
    "Drill bit 8.5 inch   2     1500.00     3000.00",
    "Mud additive         10     120.00     1200.00",
    "Subtotal: 4200.00",
    "Tax (10%): 420.00",
    "Total: 4620.00 USD",
]

PARAGRAPH = (
    "The drilling crew tripped out of the hole at 14:30 and changed the bit. "
    "Mud weight was 10.2 ppg and the rate of penetration stayed steady through the sand."
)


def digital_pdf(pages: list[list[str]] | None = None, encrypt: str | None = None) -> bytes:
    """A PDF with a real text layer, made with reportlab."""
    from reportlab.lib import pdfencrypt
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pages = pages or [INVOICE_LINES]
    buf = io.BytesIO()
    kwargs = {}
    if encrypt:
        kwargs["encrypt"] = pdfencrypt.StandardEncryption(encrypt, canPrint=1)
    c = canvas.Canvas(buf, pagesize=A4, **kwargs)
    for lines in pages:
        y = 800
        for i, line in enumerate(lines):
            c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 16 if i == 0 else 11)
            c.drawString(60, y, line)
            y -= 26
        c.showPage()
    c.save()
    return buf.getvalue()


def blank_pdf() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.showPage()
    c.save()
    return buf.getvalue()


def junk_text_pdf() -> bytes:
    """Text that is junk even when you LOOK at it. No engine can fix this one."""
    junk = [
        "Wkh txlfn eurzq ira mxpsv ryhu wkh odcb grj dw wkh uljkw wlph",
        "Xq#$k%z^& a3$f9 q@w!e#r $%^&*( zx9#k2 pl,mn bvcx qwrtp",
        "Kjhg fdsa zxcv qwrt plkm njbh vgcf xdrs zsxdc wsxcd rfvbg",
        "Bvcxz mnbvc lkjhg poiuy trewq qazws xedcr fvtgb yhnuj mikol",
    ]
    return digital_pdf([junk])


def garbled_pdf() -> bytes:
    """The realistic garbled case: the page LOOKS fine (it is a picture of an invoice), but
    its hidden text layer is junk, like a scan with a broken OCR layer. OCR can fix it."""
    import pypdfium2 as pdfium
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    scan = pdfium.PdfDocument(scanned_pdf())[0].render(scale=200 / 72).to_pil().convert("RGB")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawImage(ImageReader(scan), 0, 0, width=A4[0], height=A4[1])
    hidden = c.beginText(60, 780)
    hidden.setTextRenderMode(3)  # 3 = invisible text
    for line in (
        "Wkh txlfn eurzq ira mxpsv ryhu wkh odcb grj dw wkh uljkw wlph",
        "Xq#$k%z^& a3$f9 q@w!e#r $%^&*( zx9#k2 pl,mn bvcx qwrtp",
        "Kjhg fdsa zxcv qwrt plkm njbh vgcf xdrs zsxdc wsxcd rfvbg",
    ):
        hidden.textLine(line)
    c.drawText(hidden)
    c.showPage()
    c.save()
    return buf.getvalue()


def scanned_pdf(source_pdf: bytes | None = None, seed: int = 7, angle: float = 1.2) -> bytes:
    """Render a digital PDF to an image, add noise, blur and a tilt, save as an image-only PDF."""
    import pypdfium2 as pdfium

    source_pdf = source_pdf or digital_pdf([INVOICE_LINES])
    pdf = pdfium.PdfDocument(source_pdf)
    rng = random.Random(seed)
    images: list[Image.Image] = []
    for i in range(len(pdf)):
        img = pdf[i].render(scale=200 / 72).to_pil().convert("L")
        img = img.rotate(angle, expand=False, fillcolor=255, resample=Image.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(0.8))
        px = img.load()
        for _ in range(img.width * img.height // 60):  # speckle noise
            x, y = rng.randrange(img.width), rng.randrange(img.height)
            px[x, y] = max(0, min(255, px[x, y] + rng.randint(-60, 60)))
        images.append(img)
    pdf.close()
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:], resolution=200)
    return buf.getvalue()


def mixed_pdf() -> bytes:
    """One page: a real text paragraph on top, and a big scanned picture of a table below."""
    import pypdfium2 as pdfium
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    picture_pdf = scanned_pdf(digital_pdf([["Scanned stamp: APPROVED", "Reviewed by: J. Smith"]]))
    picture = pdfium.PdfDocument(picture_pdf)[0].render(scale=1.5).to_pil().convert("RGB")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica", 11)
    c.drawString(60, 800, "Daily summary: " + PARAGRAPH[:70])
    c.drawString(60, 780, PARAGRAPH[70:])
    c.drawImage(ImageReader(picture), 30, 20, width=535, height=700)
    c.showPage()
    c.save()
    return buf.getvalue()


def docx_bytes() -> bytes:
    import docx

    d = docx.Document()
    d.add_heading("Daily Report", level=1)
    d.add_paragraph(PARAGRAPH)
    d.add_paragraph("First item", style="List Bullet")
    d.add_paragraph("Second item", style="List Bullet")
    t = d.add_table(rows=3, cols=2)
    for r, (a, b) in enumerate([("Depth", "Meters"), ("1200", "35.2"), ("1300", "40.1")]):
        t.cell(r, 0).text, t.cell(r, 1).text = a, b
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def xlsx_bytes() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Rates"
    for row in [["Depth", "ROP"], [1200, 35.2], [1300, 40.1]]:
        ws.append(row)
    ws2 = wb.create_sheet("Crew")
    for row in [["Name", "Role"], ["A. Rig", "Driller"]]:
        ws2.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def pptx_bytes() -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[1])
    s1.shapes.title.text = "Well Summary"
    s1.placeholders[1].text = "Reached 3200 m\nNo incidents"
    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "Table slide"
    tbl = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    for r, row in enumerate([["Item", "Value"], ["Depth", "3200"]]):
        for c, val in enumerate(row):
            tbl.cell(r, c).text = val
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def csv_bytes() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerows([["depth", "rop", "wob"], [1200, 35.2, 12], [1300, 40.1, 14], [1400, 38.7, 13]])
    return buf.getvalue().encode()


def json_bytes() -> bytes:
    return json.dumps(
        [{"well": "A-1", "depth": 3200}, {"well": "B-2", "depth": 2800}], indent=2
    ).encode()


def html_bytes() -> bytes:
    return (
        b"<html><head><title>t</title><style>p{color:red}</style></head><body>"
        b"<h1>Report</h1><p>Hello <b>world</b>, this is a paragraph.</p>"
        b"<ul><li>one</li><li>two</li></ul>"
        b"<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        b"<script>alert(1)</script></body></html>"
    )


def eml_bytes(with_attachment: bool = True) -> bytes:
    msg = EmailMessage()
    msg["From"] = "rig@example.com"
    msg["To"] = "office@example.com"
    msg["Subject"] = "Daily report attached"
    msg["Date"] = "Fri, 15 Mar 2024 08:00:00 +0000"
    msg.set_content("Hello,\n\nPlease find the invoice attached.\n\nThanks")
    if with_attachment:
        msg.add_attachment(
            digital_pdf(), maintype="application", subtype="pdf", filename="invoice.pdf"
        )
        msg.add_attachment(csv_bytes(), maintype="text", subtype="csv", filename="rates.csv")
    return msg.as_bytes()


def zip_bytes(members: dict[str, bytes] | None = None) -> bytes:
    members = members or {
        "invoice.pdf": digital_pdf(),
        "notes/readme.txt": b"Plain notes about the well.\nSecond line.",
        "data.csv": csv_bytes(),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


DDR_LINES = [
    "DAILY DRILLING REPORT",
    "Report date: 2024-05-12",
    "Well: Falcon-7   Rig: Nordic 14   Operator: Northern Star Energy",
    "Depth start: 2850 m    Depth end: 3015 m    Progress: 165 m",
    "Bit size: 8.5 in    Average ROP: 27.5 m/hr",
    "Mud type: Water-based    Mud weight: 10.2 ppg",
    "NPT: 2.5 hours    Weather: Clear, light wind",
    "Operations (24 hr)",
    "00:00-06:00  Drilled 8.5 in hole from 2850 m to 2930 m",
    "06:00-08:30  Waited on weather and repaired top drive",
    "08:30-24:00  Drilled 8.5 in hole from 2930 m to 3015 m",
]


def ddr_pdf() -> bytes:
    """A synthetic daily drilling report (made-up well, rig and numbers)."""
    return digital_pdf([DDR_LINES])
