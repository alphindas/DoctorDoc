"""Tesseract: classic OCR. Optional fallback. Needs the `tesseract` program installed on the system.

We call the program directly (no Python wrapper), and read its TSV output to get
one line of text plus a confidence per line.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from collections import defaultdict

from doctordoc.engines._images import iter_page_images
from doctordoc.engines._ocr_layout import OcrBox, boxes_to_blocks
from doctordoc.engines.base import Engine, EngineError
from doctordoc.models import Page, PageKind

IMAGE_TYPES = {"png", "jpg", "tiff", "bmp", "gif", "webp"}


class TesseractEngine(Engine):
    name = "tesseract"
    license = "Apache-2.0 (Tesseract)"
    extra = ""  # it is a system program: apt install tesseract-ocr

    def missing_reason(self) -> str | None:
        if shutil.which("tesseract") is None:
            return "the 'tesseract' program is not installed (e.g. apt install tesseract-ocr)"
        return None

    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        if file_type in IMAGE_TYPES:
            return True
        return file_type == "pdf" and page_kind != PageKind.DIGITAL

    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        result: list[Page] = []
        for number, image, scale in iter_page_images(data, file_type, pages, self.config.ocr_dpi):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            tsv = self._run(buffer.getvalue())
            blocks = boxes_to_blocks(_tsv_to_boxes(tsv), self.name, scale)
            result.append(Page(number=number, engine=self.name, blocks=blocks))
        return result

    def _run(self, png: bytes) -> str:
        command = ["tesseract", "stdin", "stdout", "-l", self.config.ocr_languages, "tsv"]
        try:
            done = subprocess.run(command, input=png, capture_output=True, timeout=300, check=False)
        except (OSError, subprocess.SubprocessError) as err:
            raise EngineError(f"could not run tesseract: {err}") from err
        if done.returncode != 0:
            raise EngineError(f"tesseract failed: {done.stderr.decode(errors='replace')[:200]}")
        return done.stdout.decode("utf-8", errors="replace")


def _tsv_to_boxes(tsv: str) -> list[OcrBox]:
    """Join the words of each Tesseract line into one box with an average confidence."""
    lines: dict[tuple[str, str, str, str], list[list[str]]] = defaultdict(list)
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():  # level 5 = a word
            continue
        lines[(cols[1], cols[2], cols[3], cols[4])].append(cols)
    boxes = []
    for words in lines.values():
        x0 = min(int(w[6]) for w in words)
        y0 = min(int(w[7]) for w in words)
        x1 = max(int(w[6]) + int(w[8]) for w in words)
        y1 = max(int(w[7]) + int(w[9]) for w in words)
        confidences = [float(w[10]) for w in words if float(w[10]) >= 0]
        score = sum(confidences) / len(confidences) / 100 if confidences else None
        boxes.append(OcrBox(" ".join(w[11] for w in words), x0, y0, x1, y1, score))
    return boxes
