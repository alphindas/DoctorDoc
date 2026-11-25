"""Build the SYNTHETIC benchmark set, with ground truth, from a fixed seed.

    python scripts/make_samples.py [--out benchmarks/data] [--seed 1234]

Every company, well, person and number is made up. The same seed gives byte-identical files.
Needs the dev extra (reportlab). Writes files/ and manifest.json into the output folder.

Each manifest entry holds the ground truth for one file:
    truth_text    the text a perfect reader would return, in reading order
    truth_tables  the tables, as lists of rows
    fields        the values we expect field extraction to find (and the schema to use)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "benchmarks" / "data"
FIXED_TIME = datetime(2025, 1, 1, 12, 0, 0)  # embedded in files, so bytes do not change

VENDORS = [
    "Harbor Point Supplies",
    "Cedar Ridge Tools",
    "Northgate Oilfield Services",
    "Blue Mesa Equipment",
]
CUSTOMERS = ["Westfield Energy Partners", "Granite Basin Drilling Co", "Lakeshore Petroleum"]
ITEMS = [
    ("Drill bit 8.5 in", 1850.0),
    ("Mud additive drum", 240.0),
    ("Casing joint 9.625 in", 960.0),
    ("Cement sack 50 kg", 18.5),
    ("Wellhead valve", 2300.0),
    ("Hydraulic oil drum", 310.0),
    ("Pump seal kit", 145.0),
    ("Rig mat", 520.0),
    ("Stabiliser 8.25 in", 1480.0),
    ("Safety harness", 95.0),
]
WELLS = ["Falcon-7", "Osprey-12", "Kestrel-3", "Heron-9"]
RIGS = ["Nordic 14", "Titan 3", "Atlas 21"]
ACTIVITIES = [
    "Drilled {size} in hole from {a} m to {b} m",
    "Circulated and conditioned mud",
    "Tripped out of hole to change bit",
    "Waited on weather",
    "Repaired top drive",
    "Ran casing and cemented",
    "Rigged up BOP and pressure tested",
]
MEMO_SENTENCES = [
    "The crew tripped out of the hole at {t} and changed the bit.",
    "Mud weight was held at {w} ppg through the sand section.",
    "No safety incidents were reported during the shift.",
    "The pump pressure rose slowly and was checked twice.",
    "Supplies for the next section arrive on {d}.",
    "A short weather delay is expected on {d}.",
    "The rate of penetration stayed steady near {r} metres per hour.",
]


# ======================================================================= content (with truth)


@dataclass
class Spec:
    """One logical document: ordered blocks plus the ground truth that follows from them."""

    kind: str  # invoice | ddr | memo | rates
    blocks: list[tuple[str, Any]]  # ("title", str) | ("text", str) | ("table", rows)
    fields: dict[str, Any] = field(default_factory=dict)
    schema: str | None = None

    @property
    def truth_text(self) -> str:
        parts = []
        for kind, value in self.blocks:
            parts.append("\n".join(" ".join(row) for row in value) if kind == "table" else value)
        return "\n".join(parts)

    @property
    def truth_tables(self) -> list[list[list[str]]]:
        return [value for kind, value in self.blocks if kind == "table"]

    @property
    def text_lines(self) -> list[str]:
        return [v for k, v in self.blocks if k != "table"]


def money(x: float) -> str:
    return f"{x:,.2f}"


def make_invoice(rng: random.Random, n_items: int) -> Spec:
    vendor, customer = rng.choice(VENDORS), rng.choice(CUSTOMERS)
    number = f"INV-{rng.randint(2024, 2025)}-{rng.randint(1000, 9999)}"
    issued = date(2025, 1, 1) + timedelta(days=rng.randint(0, 300))
    due = issued + timedelta(days=30)
    rows = [["Description", "Qty", "Unit price", "Amount"]]
    subtotal = 0.0
    for name, price in (
        rng.sample(ITEMS, n_items) if n_items <= len(ITEMS) else rng.choices(ITEMS, k=n_items)
    ):
        qty = rng.randint(1, 12)
        amount = round(qty * price, 2)
        subtotal += amount
        rows.append([name, str(qty), money(price), money(amount)])
    subtotal = round(subtotal, 2)
    tax = round(subtotal * 0.10, 2)
    total = round(subtotal + tax, 2)
    blocks: list[tuple[str, Any]] = [
        ("title", vendor),
        ("text", f"Invoice number: {number}"),
        ("text", f"Invoice date: {issued.isoformat()}"),
        ("text", f"Due date: {due.isoformat()}"),
        ("text", f"Bill to: {customer}"),
        ("table", rows),
        ("text", f"Subtotal: {money(subtotal)}"),
        ("text", f"Tax (10%): {money(tax)}"),
        ("text", f"Total: {money(total)} USD"),
    ]
    fields = {
        "invoice_number": number,
        "invoice_date": issued.isoformat(),
        "due_date": due.isoformat(),
        "vendor_name": vendor,
        "customer_name": customer,
        "subtotal": subtotal,
        "tax_amount": tax,
        "total": total,
    }
    return Spec("invoice", blocks, fields, "invoice")


def make_ddr(rng: random.Random) -> Spec:
    day = date(2025, 1, 1) + timedelta(days=rng.randint(0, 300))
    well, rig, operator = rng.choice(WELLS), rng.choice(RIGS), rng.choice(CUSTOMERS)
    start = rng.randrange(1500, 4000, 5)
    progress = rng.randrange(60, 220, 5)
    end = start + progress
    rop = round(rng.uniform(12, 45), 1)
    mud = round(rng.uniform(8.8, 12.5), 1)
    cuts = sorted(rng.sample([4, 6, 8, 12, 14, 18, 20], 3))
    bounds = [0, *cuts, 24]
    rows = [["Time", "Hours", "Operation"]]
    depth = start
    for a, b in zip(bounds, bounds[1:], strict=False):
        text = rng.choice(ACTIVITIES)
        if "{size}" in text:
            step = rng.randrange(10, 60, 5)
            text = text.format(size=rng.choice(["8.5", "12.25"]), a=depth, b=depth + step)
            depth += step
        rows.append([f"{a:02d}:00-{b % 24:02d}:00", f"{b - a:.1f}", text])
    blocks: list[tuple[str, Any]] = [
        ("title", "DAILY DRILLING REPORT"),
        ("text", f"Report date: {day.isoformat()}"),
        ("text", f"Well: {well}"),
        ("text", f"Rig: {rig}"),
        ("text", f"Operator: {operator}"),
        ("text", f"Depth start: {start} m"),
        ("text", f"Depth end: {end} m"),
        ("text", f"Progress: {progress} m"),
        ("text", f"Average ROP: {rop} m/hr"),
        ("text", f"Mud weight: {mud} ppg"),
        ("table", rows),
    ]
    fields = {
        "report_date": day.isoformat(),
        "well_name": well,
        "rig_name": rig,
        "operator": operator,
        "depth_start_m": float(start),
        "depth_end_m": float(end),
        "progress_m": float(progress),
        "avg_rop_m_per_hr": rop,
        "mud_weight_ppg": mud,
    }
    return Spec("ddr", blocks, fields, "drilling")


def make_memo(rng: random.Random, paragraphs: int = 3) -> Spec:
    blocks: list[tuple[str, Any]] = [("title", "Site memo")]
    for _ in range(paragraphs):
        sentences = rng.sample(MEMO_SENTENCES, 3)
        text = " ".join(
            s.format(
                t=f"{rng.randint(1, 23):02d}:{rng.choice(['00', '15', '30', '45'])}",
                w=round(rng.uniform(8.8, 12.5), 1),
                r=rng.randint(15, 45),
                d=(date(2025, 1, 1) + timedelta(days=rng.randint(0, 300))).isoformat(),
            )
            for s in sentences
        )
        blocks.append(("text", text))
    return Spec("memo", blocks)


def make_rates(rng: random.Random, n_rows: int) -> Spec:
    rows = [["Depth m", "ROP m/hr", "WOB kN", "RPM", "Torque kNm"]]
    depth = rng.randrange(1000, 2000, 50)
    for _ in range(n_rows):
        depth += rng.randrange(5, 30)
        rows.append(
            [
                str(depth),
                f"{round(rng.uniform(10, 50), 1):g}",
                str(rng.randint(60, 160)),
                str(rng.randint(80, 140)),
                f"{round(rng.uniform(5, 25), 1):g}",
            ]
        )
    return Spec("rates", [("table", rows)])


# ======================================================================= renderers


def pdf_bytes(spec: Spec, rows_per_page_hint: int | None = None) -> bytes:
    """A digital PDF with a real text layer and ruled tables (header repeats on each page)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    story: list[Any] = []
    for kind, value in spec.blocks:
        if kind == "title":
            story.append(Paragraph(escape(value), styles["Title"]))
        elif kind == "text":
            story += [Paragraph(escape(value), styles["BodyText"]), Spacer(1, 4)]
        else:
            table = Table(value, repeatRows=1, hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story += [table, Spacer(1, 10)]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, invariant=1).build(story)
    return buf.getvalue()


def pdf_page_count(data: bytes) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def _degrade(img: Image.Image, seed: int, angle: float, blur: float, noise: int) -> Image.Image:
    """Make a clean page look scanned: tilt, blur, speckle noise."""
    gray = img.convert("L").rotate(angle, fillcolor=255, resample=Image.BICUBIC)
    gray = gray.filter(ImageFilter.GaussianBlur(blur))
    rng = np.random.default_rng(seed)
    pixels = np.asarray(gray, dtype=np.int16)
    mask = rng.random(pixels.shape) < 0.04  # 4% of the pixels get noise
    pixels = np.where(mask, pixels + rng.integers(-noise, noise + 1, pixels.shape), pixels)
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))


SCAN_LEVELS = {  # angle (degrees), blur radius, noise strength, dpi
    "light": (0.5, 0.5, 20, 200),
    "medium": (1.2, 0.8, 60, 200),
    "heavy": (2.5, 1.3, 90, 150),
}


def scanned_bytes(source_pdf: bytes, level: str, seed: int) -> bytes:
    """Render every page of a PDF to a picture, degrade it, save as a picture-only PDF."""
    import pypdfium2 as pdfium

    angle, blur, noise, dpi = SCAN_LEVELS[level]
    pdf = pdfium.PdfDocument(source_pdf)
    images = [
        _degrade(pdf[i].render(scale=dpi / 72).to_pil(), seed + i, angle, blur, noise)
        for i in range(len(pdf))
    ]
    pdf.close()
    buf = io.BytesIO()
    images[0].save(
        buf,
        format="PDF",
        save_all=True,
        append_images=images[1:],
        resolution=dpi,
        creationDate=FIXED_TIME.timetuple(),
        modDate=FIXED_TIME.timetuple(),
    )
    return buf.getvalue()


def caesar(text: str, shift: int = 3) -> str:
    """Shift letters, like a font whose character map is wrong: reads as junk."""
    out = []
    for c in text:
        if "a" <= c <= "z":
            out.append(chr((ord(c) - 97 + shift) % 26 + 97))
        elif "A" <= c <= "Z":
            out.append(chr((ord(c) - 65 + shift) % 26 + 65))
        else:
            out.append(c)
    return "".join(out)


def garbled_bytes(spec: Spec, seed: int) -> bytes:
    """Looks right on screen (a scan), but the hidden text layer is junk, as in a bad OCR layer."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    scan = scanned_bytes(pdf_bytes(spec), "medium", seed)
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(scan)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for i in range(len(pdf)):
        picture = pdf[i].render(scale=200 / 72).to_pil().convert("RGB")
        c.drawImage(ImageReader(picture), 0, 0, width=A4[0], height=A4[1])
        hidden = c.beginText(40, 800)
        hidden.setTextRenderMode(3)  # 3 = invisible
        for line in (caesar(spec.truth_text)).splitlines()[:25]:
            hidden.textLine(line)
        c.drawText(hidden)
        c.showPage()
    c.save()
    pdf.close()
    return buf.getvalue()


def stamp_lines(rng: random.Random) -> list[str]:
    return [
        f"Permit no: PT-{rng.randint(10000, 99999)}",
        f"Approved by: {rng.choice(['R. Delgado', 'M. Okafor', 'S. Lindqvist'])}",
        f"Valid until: {(date(2026, 1, 1) + timedelta(days=rng.randint(0, 200))).isoformat()}",
    ]


def mixed_bytes(spec: Spec, stamp: list[str], seed: int) -> bytes:
    """A page with a real text layer on top and a scanned picture of text below it."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    picture = Image.new("L", (1100, 320), 255)
    draw = ImageDraw.Draw(picture)
    font = ImageFont.load_default(size=44)  # bundled with Pillow: same result on every machine
    for i, line in enumerate(stamp):
        draw.text((40, 30 + i * 90), line, fill=0, font=font)
    picture = _degrade(picture, seed, 1.0, 0.7, 40).convert("RGB")

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    y = 800
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, spec.text_lines[0])
    c.setFont("Helvetica", 10)
    for paragraph in spec.text_lines[1:]:
        words, line = paragraph.split(), ""
        for word in words:
            if len(line) + len(word) > 95:
                y -= 14
                c.drawString(50, y, line)
                line = ""
            line = f"{line} {word}".strip()
        y -= 14
        c.drawString(50, y, line)
        y -= 10
    c.drawImage(ImageReader(picture), 50, 120, width=495, height=144)
    c.showPage()
    c.save()
    return buf.getvalue()


def docx_bytes(spec: Spec) -> bytes:
    import docx

    document = docx.Document()
    document.core_properties.created = document.core_properties.modified = FIXED_TIME
    for kind, value in spec.blocks:
        if kind == "title":
            document.add_heading(value, level=1)
        elif kind == "text":
            document.add_paragraph(value)
        else:
            table = document.add_table(rows=len(value), cols=len(value[0]), style="Table Grid")
            for r, row in enumerate(value):
                for c, cell in enumerate(row):
                    table.cell(r, c).text = cell
    buf = io.BytesIO()
    document.save(buf)
    return _normalise_zip(buf.getvalue())


def xlsx_bytes(spec: Spec) -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    book.properties.created = book.properties.modified = FIXED_TIME
    sheet = book.active
    sheet.title = "Rates"
    for row in spec.truth_tables[0]:
        sheet.append([_number_or_text(c) for c in row])
    buf = io.BytesIO()
    book.save(buf)
    return _normalise_zip(buf.getvalue())


def _number_or_text(cell: str) -> Any:
    try:
        return int(cell) if cell.lstrip("-").isdigit() else float(cell)
    except ValueError:
        return cell


def pptx_bytes(spec: Spec) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches

    deck = Presentation()
    deck.core_properties.created = deck.core_properties.modified = FIXED_TIME
    table_at = next(i for i, (k, _) in enumerate(spec.blocks) if k == "table")
    before, after = spec.blocks[:table_at], spec.blocks[table_at + 1 :]
    first = deck.slides.add_slide(deck.slide_layouts[1])
    first.shapes.title.text = before[0][1]
    first.placeholders[1].text = "\n".join(v for _, v in before[1:])
    second = deck.slides.add_slide(deck.slide_layouts[6])  # blank: no extra title text
    rows = spec.blocks[table_at][1]
    shape = second.shapes.add_table(
        len(rows), len(rows[0]), Inches(0.5), Inches(1.6), Inches(9), Inches(3)
    )
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            shape.table.cell(r, c).text = cell
    if after:
        box = second.shapes.add_textbox(Inches(0.5), Inches(5.2), Inches(9), Inches(1.5))
        box.text_frame.text = "\n".join(v for _, v in after)
    buf = io.BytesIO()
    deck.save(buf)
    return _normalise_zip(buf.getvalue())


def csv_bytes(spec: Spec) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(spec.truth_tables[0])
    return buf.getvalue().encode()


def json_bytes(spec: Spec) -> bytes:
    header, *rows = spec.truth_tables[0]
    return json.dumps([dict(zip(header, row, strict=True)) for row in rows], indent=2).encode()


def html_bytes(spec: Spec) -> bytes:
    parts = [
        "<html><head><title>Report</title><style>td{border:1px solid #999}</style></head><body>"
    ]
    for kind, value in spec.blocks:
        if kind == "title":
            parts.append(f"<h1>{escape(value)}</h1>")
        elif kind == "text":
            parts.append(f"<p>{escape(value)}</p>")
        else:
            head, *rows = value
            parts.append("<table><tr>" + "".join(f"<th>{escape(c)}</th>" for c in head) + "</tr>")
            parts += ["<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in rows]
            parts.append("</table>")
    parts.append("</body></html>")
    return "".join(parts).encode()


def eml_bytes(subject: str, body: str, attachments: dict[str, tuple[bytes, str, str]]) -> bytes:
    message = EmailMessage()
    message["From"] = "rig-office@example.com"
    message["To"] = "planning@example.com"
    message["Subject"] = subject
    message["Date"] = "Wed, 15 Jan 2025 08:00:00 +0000"
    message.set_content(body)
    for name, (data, maintype, subtype) in attachments.items():
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    if message.is_multipart():
        message.set_boundary("=====doctordoc-benchmark-boundary=====")  # the default is random
    return message.as_bytes()


_FIXED_DATES = re.compile(
    rb"(<dcterms:(?:created|modified)[^>]*>)([^<]*)(</dcterms:(?:created|modified)>)"
)


def _set_dates(xml: bytes) -> bytes:
    fixed = FIXED_TIME.isoformat().encode() + b"Z"
    return _FIXED_DATES.sub(lambda m: m.group(1) + fixed + m.group(3), xml)


def _normalise_zip(data: bytes) -> bytes:
    """Rewrite a zip with a fixed timestamp on every member, so the bytes are repeatable."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as src,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            item = zipfile.ZipInfo(info.filename, (2025, 1, 1, 12, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            content = src.read(info.filename)
            if info.filename == "docProps/core.xml":  # some libraries write the time of saving
                content = _set_dates(content)
            dst.writestr(item, content)
    return out.getvalue()


def zip_bytes(members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            item = zipfile.ZipInfo(name, (2025, 1, 1, 12, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(item, content)
    return out.getvalue()


# ======================================================================= the corpus


@dataclass
class Entry:
    category: str
    filename: str
    data: bytes
    truth_text: str = ""
    truth_tables: list[list[list[str]]] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)
    schema: str | None = None
    pages: int = 1
    expect_error: bool = False
    note: str = ""


def entry_from(
    spec: Spec, category: str, filename: str, data: bytes, pages: int = 1, **kw: Any
) -> Entry:
    return Entry(
        category,
        filename,
        data,
        spec.truth_text,
        spec.truth_tables,
        spec.fields,
        spec.schema,
        pages,
        **kw,
    )


def build_corpus(seed: int = 1234) -> list[Entry]:
    """Every benchmark file, in a fixed order. Each category draws from its own random stream."""
    entries: list[Entry] = []
    n = 0

    def rng() -> random.Random:
        nonlocal n
        n += 1
        return random.Random(seed * 1000 + n)

    # ---- digital PDFs
    for i, spec in enumerate(
        [make_invoice(rng(), 4), make_invoice(rng(), 6), make_ddr(rng()), make_memo(rng())]
    ):
        data = pdf_bytes(spec)
        entries.append(
            entry_from(
                spec, "digital_pdf", f"digital_{i + 1}_{spec.kind}.pdf", data, pdf_page_count(data)
            )
        )

    # ---- scanned PDFs: three noise levels
    for i, (spec, level) in enumerate(
        [
            (make_invoice(rng(), 5), "light"),
            (make_invoice(rng(), 4), "medium"),
            (make_ddr(rng()), "medium"),
            (make_memo(rng()), "heavy"),
        ]
    ):
        data = scanned_bytes(pdf_bytes(spec), level, seed + i)
        entries.append(
            entry_from(
                spec,
                "scanned_pdf",
                f"scanned_{i + 1}_{spec.kind}_{level}.pdf",
                data,
                pdf_page_count(data),
                note=f"noise={level}",
            )
        )

    # ---- garbled PDFs: looks right, hidden text layer is junk
    for i, spec in enumerate([make_invoice(rng(), 4), make_ddr(rng()), make_memo(rng())]):
        data = garbled_bytes(spec, seed + 50 + i)
        entries.append(
            entry_from(
                spec, "garbled_pdf", f"garbled_{i + 1}_{spec.kind}.pdf", data, pdf_page_count(data)
            )
        )

    # ---- mixed PDFs: text layer plus a scanned picture of more text
    for i in range(3):
        r = rng()
        spec, stamp = make_memo(r, paragraphs=2), None
        stamp = stamp_lines(r)
        truth = Spec("mixed", [*spec.blocks, ("text", "\n".join(stamp))])
        entries.append(
            Entry(
                "mixed_pdf",
                f"mixed_{i + 1}.pdf",
                mixed_bytes(spec, stamp, seed + 80 + i),
                truth.truth_text,
                [],
                {},
                None,
                1,
                note="text layer + picture of text",
            )
        )

    # ---- tables that run over two pages
    for i, spec in enumerate([make_rates(rng(), 60), make_invoice(rng(), 45)]):
        data = pdf_bytes(spec)
        pages = pdf_page_count(data)
        assert pages >= 2, "the two-page table must really cross a page"
        entries.append(
            entry_from(spec, "table_2page", f"table2p_{i + 1}_{spec.kind}.pdf", data, pages)
        )

    # ---- Office files and structured data
    for i, spec in enumerate([make_invoice(rng(), 5), make_ddr(rng())]):
        entries.append(entry_from(spec, "docx", f"office_{i + 1}.docx", docx_bytes(spec)))
    for i in range(2):
        spec = make_rates(rng(), 25)
        entries.append(entry_from(spec, "xlsx", f"sheet_{i + 1}.xlsx", xlsx_bytes(spec)))
    for i, spec in enumerate([make_invoice(rng(), 4), make_ddr(rng())]):
        entries.append(entry_from(spec, "pptx", f"deck_{i + 1}.pptx", pptx_bytes(spec), pages=2))
    for i in range(2):
        spec = make_rates(rng(), 20)
        entries.append(entry_from(spec, "csv", f"data_{i + 1}.csv", csv_bytes(spec)))
    for i in range(2):
        spec = make_rates(rng(), 15)
        entries.append(entry_from(spec, "json", f"data_{i + 1}.json", json_bytes(spec)))
    for i, spec in enumerate([make_invoice(rng(), 4), make_ddr(rng())]):
        entries.append(entry_from(spec, "html", f"page_{i + 1}.html", html_bytes(spec)))

    # ---- emails with attachments, and zips of mixed files
    for i in range(2):
        invoice, rates = make_invoice(rng(), 4), make_rates(rng(), 10)
        memo = make_memo(rng(), 1)
        attachments = {
            "invoice.pdf": (pdf_bytes(invoice), "application", "pdf"),
            "rates.csv": (csv_bytes(rates), "text", "csv"),
        }
        body = memo.blocks[1][1]
        headers = (
            "From: rig-office@example.com\nTo: planning@example.com\n"
            f"Subject: Weekly pack {i + 1}\nDate: Wed, 15 Jan 2025 08:00:00 +0000"
        )
        truth = "\n".join(
            [
                headers,
                body,
                "Attachments: invoice.pdf, rates.csv",
                invoice.truth_text,
                rates.truth_text,
            ]
        )
        entries.append(
            Entry(
                "email",
                f"mail_{i + 1}.eml",
                eml_bytes(f"Weekly pack {i + 1}", body, attachments),
                truth,
                [*invoice.truth_tables, *rates.truth_tables],
                {},
                None,
                pdf_page_count(attachments["invoice.pdf"][0]) + 2,
                note="body + 2 attachments",
            )
        )
    for i in range(2):
        invoice, memo, rates = make_invoice(rng(), 4), make_memo(rng(), 2), make_rates(rng(), 10)
        members = {
            "invoice.pdf": pdf_bytes(invoice),
            "memo.docx": docx_bytes(memo),
            "rates.csv": csv_bytes(rates),
        }
        truth = "\n".join([invoice.truth_text, memo.truth_text, rates.truth_text])
        entries.append(
            Entry(
                "zip",
                f"bundle_{i + 1}.zip",
                zip_bytes(members),
                truth,
                [*invoice.truth_tables, *rates.truth_tables],
                {},
                None,
                pdf_page_count(members["invoice.pdf"]) + 2,
                note="pdf + docx + csv",
            )
        )

    # ---- files that lie about themselves
    spec = make_invoice(rng(), 4)
    entries.append(
        entry_from(
            spec,
            "wrong_extension",
            "really_a_docx.pdf",
            docx_bytes(spec),
            note="docx saved as .pdf",
        )
    )
    spec = make_invoice(rng(), 4)
    data = pdf_bytes(spec)
    entries.append(
        entry_from(
            spec,
            "wrong_extension",
            "really_a_pdf.txt",
            data,
            pdf_page_count(data),
            note="pdf saved as .txt",
        )
    )

    # ---- broken files: the right answer is a clear error, not a crash
    good_pdf = pdf_bytes(make_invoice(rng(), 4))
    entries.append(
        Entry(
            "corrupt",
            "truncated.pdf",
            good_pdf[: len(good_pdf) // 3],
            expect_error=True,
            note="PDF cut off",
        )
    )
    good_docx = docx_bytes(make_memo(rng()))
    entries.append(
        Entry(
            "corrupt",
            "truncated.docx",
            good_docx[: len(good_docx) // 2],
            expect_error=True,
            note="DOCX cut off",
        )
    )
    return entries


def write_corpus(out_dir: Path, seed: int = 1234) -> list[dict[str, Any]]:
    files = out_dir / "files"
    files.mkdir(parents=True, exist_ok=True)
    manifest = []
    for entry in build_corpus(seed):
        (files / entry.filename).write_bytes(entry.data)
        manifest.append(
            {
                "id": entry.filename,
                "category": entry.category,
                "truth_text": entry.truth_text,
                "truth_tables": entry.truth_tables,
                "fields": entry.fields,
                "schema": entry.schema,
                "pages": entry.pages,
                "expect_error": entry.expect_error,
                "note": entry.note,
            }
        )
    (out_dir / "manifest.json").write_text(json.dumps({"seed": seed, "files": manifest}, indent=1))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()
    manifest = write_corpus(args.out, args.seed)
    counts: dict[str, int] = {}
    for item in manifest:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    size = sum(p.stat().st_size for p in (args.out / "files").iterdir())
    print(f"wrote {len(manifest)} files ({size / 1e6:.1f} MB) to {args.out}")
    print(", ".join(f"{k}: {v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
