"""Find the REAL file type from the bytes, never trusting the file extension."""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import PurePath

from pydantic import BaseModel, Field

# file_type -> mime type
MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpg": "image/jpeg",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "gif": "image/gif",
    "webp": "image/webp",
    "html": "text/html",
    "xml": "application/xml",
    "json": "application/json",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "md": "text/markdown",
    "txt": "text/plain",
    "eml": "message/rfc822",
    "msg": "application/vnd.ms-outlook",
    "zip": "application/zip",
    "ole2": "application/x-ole-storage",
    "empty": "application/x-empty",
    "unknown": "application/octet-stream",
}

# Extension -> file_type. Used ONLY to warn when the extension lies.
EXTENSION_TYPES: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".bmp": "bmp",
    ".gif": "gif",
    ".webp": "webp",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".json": "json",
    ".csv": "csv",
    ".tsv": "tsv",
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".log": "txt",
    ".eml": "eml",
    ".msg": "msg",
    ".zip": "zip",
}

IMAGE_TYPES = {"png", "jpg", "tiff", "bmp", "gif", "webp"}
TEXT_TYPES = {"txt", "md", "csv", "tsv", "json", "xml", "html", "eml"}

_EML_HEADERS = ("from", "to", "subject", "date", "mime-version", "message-id", "received")


class FileInfo(BaseModel):
    """What we learned about a file before parsing it."""

    file_type: str = "unknown"
    mime: str = MIME["unknown"]
    extension: str = ""
    encrypted: bool = False
    corrupt: bool = False
    empty: bool = False
    page_count: int | None = None  # filled for PDFs
    warnings: list[str] = Field(default_factory=list)
    problem: str | None = None  # set when the file cannot be parsed at all


def detect(data: bytes, filename: str | None = None) -> FileInfo:
    """Look at the bytes (and the file name, only to spot a wrong extension)."""
    extension = PurePath(filename).suffix.lower() if filename else ""
    info = FileInfo(extension=extension)

    if not data or not data.strip(b"\x00 \t\r\n"):
        return _finish(info, "empty", problem="File is empty.", empty=True)

    file_type = _detect_binary(data, info) or _detect_text(data, extension)
    _finish(info, file_type)
    _warn_if_extension_lies(info)
    return info


def _finish(
    info: FileInfo, file_type: str, problem: str | None = None, empty: bool = False
) -> FileInfo:
    info.file_type = file_type
    info.mime = MIME.get(file_type, MIME["unknown"])
    if problem and not info.problem:
        info.problem = problem
    info.empty = info.empty or empty
    return info


# ---------------------------------------------------------------- binary formats


def _detect_binary(data: bytes, info: FileInfo) -> str | None:
    """Return a file type if the bytes start with a known 'magic' signature."""
    head = data[:16]
    if data[:1024].lstrip().startswith(b"%PDF-") or b"%PDF-" in data[:1024]:
        _inspect_pdf(data, info)
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return _inspect_zip(data, info)
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head.startswith(b"BM") and len(data) > 26 and data[6:10] == b"\x00\x00\x00\x00":
        return "bmp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _inspect_ole2(data, info)
    return None


def _inspect_pdf(data: bytes, info: FileInfo) -> None:
    """Open the PDF just enough to learn: encrypted? corrupt? how many pages?"""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as err:
        if getattr(err, "err_code", None) == 4:  # FPDF_ERR_PASSWORD
            info.encrypted = True
            info.problem = "PDF is password-protected."
        else:
            info.corrupt = True
            info.problem = f"PDF is corrupt or unreadable ({err})."
        return
    try:
        info.page_count = len(pdf)
        if info.page_count == 0:
            info.corrupt = True
            info.problem = "PDF has no pages."
    finally:
        pdf.close()


def _inspect_zip(data: bytes, info: FileInfo) -> str:
    """A ZIP can be a docx, xlsx, pptx or a plain zip. Look inside to tell."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
    except zipfile.BadZipFile:
        info.corrupt = True
        info.problem = "ZIP structure is damaged."
        return "zip"
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    return "zip"


def _inspect_ole2(data: bytes, info: FileInfo) -> str:
    """OLE2 holds Outlook .msg, old .doc/.xls/.ppt, and password-protected Office files."""
    # Outlook messages contain streams named __substg1.0_XXXX (stored as UTF-16 in the file).
    if "__substg1.0_".encode("utf-16-le") in data[: 4 * 1024 * 1024]:
        return "msg"
    info.problem = (
        "Old binary Office file (.doc/.xls/.ppt) or password-protected Office file. "
        "Not supported: convert it to docx/xlsx/pptx first."
    )
    return "ole2"


# ---------------------------------------------------------------- text formats


def decode_text(data: bytes) -> str | None:
    """Decode text bytes. Returns None if the bytes do not look like text."""
    sample = data[:65536]
    if sample.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig", errors="replace")
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if b"\x00" in sample:
        return None  # NUL bytes mean binary data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Not valid UTF-8: accept as legacy Windows text only if almost all bytes are printable.
    printable = sum(1 for b in sample if b in (9, 10, 13) or 32 <= b < 127 or b >= 160)
    if printable / max(len(sample), 1) > 0.95:
        return data.decode("cp1252", errors="replace")
    return None


def _detect_text(data: bytes, extension: str) -> str:
    text = decode_text(data)
    if text is None:
        return "unknown"
    stripped = text.lstrip("﻿ \t\r\n")
    lowered = stripped[:2048].lower()

    if lowered.startswith(("<!doctype html", "<html")) or "<html" in lowered[:500]:
        return "html"
    if lowered.startswith("<?xml") or lowered.startswith("<svg"):
        return "xml"
    if stripped[:1] in "{[" and _is_json(stripped):
        return "json"
    if _looks_like_eml(stripped):
        return "eml"
    delimited = _delimited_type(stripped)
    if delimited:
        return delimited
    if extension in (".md", ".markdown"):
        return "md"
    if extension == ".xml" and stripped.startswith("<"):
        return "xml"
    if extension in (".html", ".htm") and stripped.startswith("<"):
        return "html"
    return "txt"


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _looks_like_eml(text: str) -> bool:
    """An email starts with 'Header: value' lines. We need at least two known headers."""
    header_block = text.split("\n\n", 1)[0].split("\r\n\r\n", 1)[0]
    names = re.findall(r"^([A-Za-z][A-Za-z-]*):", header_block, flags=re.MULTILINE)
    known = {n.lower() for n in names} & set(_EML_HEADERS)
    return len(known) >= 2 and ("from" in known or "received" in known or "message-id" in known)


def _delimited_type(text: str) -> str | None:
    """Detect csv/tsv: several lines that all have the same number of columns (> 1)."""
    lines = [ln for ln in text[:8192].splitlines() if ln.strip()][:50]
    if len(lines) < 2:
        return None
    lines = lines[:-1] if len(text) > 8192 else lines  # the last line may be cut by our sample
    for delimiter, kind in (("\t", "tsv"), (",", "csv"), (";", "csv")):
        try:
            counts = {len(row) for row in csv.reader(lines, delimiter=delimiter)}
        except csv.Error:
            continue
        if len(counts) == 1 and next(iter(counts)) > 1:
            return kind
    return None


# ---------------------------------------------------------------- extension check


def _warn_if_extension_lies(info: FileInfo) -> None:
    expected = EXTENSION_TYPES.get(info.extension)
    if expected is None or expected == info.file_type:
        return
    # Plain text can legitimately hold anything text-like (a .txt with CSV inside is fine).
    if expected in ("txt", "md") and info.file_type in TEXT_TYPES:
        return
    info.warnings.append(
        f"Extension '{info.extension}' says {expected}, but the content is {info.file_type}. "
        f"Treating it as {info.file_type}."
    )
