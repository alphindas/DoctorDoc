"""RapidOCR: light OCR that runs on CPU through ONNX Runtime. The default scanned-page fallback.

Models (about 15 MB) are downloaded by the rapidocr package on first use.
"""

from __future__ import annotations

import gc

import numpy as np

from doctordoc.engines._images import iter_page_images
from doctordoc.engines._ocr_layout import OcrBox, boxes_to_blocks
from doctordoc.engines.base import Engine, EngineError
from doctordoc.models import Page, PageKind

IMAGE_TYPES = {"png", "jpg", "tiff", "bmp", "gif", "webp"}


class RapidOcrEngine(Engine):
    name = "rapidocr"
    license = "Apache-2.0 (RapidOCR + ONNX Runtime)"
    extra = "ocr"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._ocr = None

    def missing_reason(self) -> str | None:
        return self.module_missing("rapidocr", "onnxruntime")

    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        if file_type in IMAGE_TYPES:
            return True
        return file_type == "pdf" and page_kind != PageKind.DIGITAL

    def _engine(self):
        if self._ocr is None:
            from rapidocr import RapidOCR

            self._ocr = RapidOCR(params={"Global.log_level": "error"})
        return self._ocr

    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        ocr = self._engine()
        result_pages: list[Page] = []
        for number, image, scale in iter_page_images(data, file_type, pages, self.config.ocr_dpi):
            try:
                output = ocr(np.asarray(image), return_word_box=True)
            except Exception as err:
                raise EngineError(f"RapidOCR failed on page {number}: {err}") from err
            result_pages.append(
                Page(
                    number=number,
                    engine=self.name,
                    blocks=boxes_to_blocks(_boxes(output), self.name, scale),
                )
            )
        return result_pages

    def release(self) -> None:
        self._ocr = None
        gc.collect()


def _boxes(output) -> list[OcrBox]:
    """Convert RapidOCR's output into rectangles.

    RapidOCR sometimes drops the spaces between far-apart columns ("10120.001200.00"). Its
    word-level output keeps them apart, so we use one box per word. Chinese/Japanese text
    has no spaces between words, so lines containing it stay whole.
    """
    if output is None or output.boxes is None or output.txts is None:
        return []
    word_lines = output.word_results or [None] * len(output.txts)
    boxes: list[OcrBox] = []
    for polygon, text, score, words in zip(
        output.boxes, output.txts, output.scores, word_lines, strict=False
    ):
        if words and not _has_cjk(text):
            boxes.extend(_rectangle(w[2], w[0], float(w[1])) for w in words if w[0].strip())
        else:
            boxes.append(_rectangle(polygon, text, float(score)))
    return boxes


def _rectangle(polygon, text: str, score: float) -> OcrBox:
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    return OcrBox(text, min(xs), min(ys), max(xs), max(ys), score)


def _has_cjk(text: str) -> bool:
    return any(ord(c) >= 0x2E80 for c in text)
