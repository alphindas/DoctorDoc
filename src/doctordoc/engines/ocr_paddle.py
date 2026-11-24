"""PaddleOCR-VL (Baidu, Apache-2.0): a 0.9B vision-language model that reads a whole page
(text, tables, formulas) and returns it in reading order. The best scanned-page engine,
but it needs a GPU: DoctorDoc only uses it when a GPU is available.

STATUS: written against paddleocr 3.7 (`PaddleOCRVL.predict` -> result with
`json["res"]["parsing_res_list"]`), read from its source. NOT yet run end to end:
on the 5 GB RAM / CPU-only test machine the model does not fit (it was killed by a memory
watchdog after loading the weights), and the GPU build of paddlepaddle was not installed.
"""

from __future__ import annotations

import gc

import numpy as np

from doctordoc.engines._images import iter_page_images
from doctordoc.engines._markup import html_to_blocks, markdown_to_blocks
from doctordoc.engines.base import Engine, EngineError
from doctordoc.models import BBox, Block, BlockType, Page, PageKind

IMAGE_TYPES = {"png", "jpg", "tiff", "bmp", "gif", "webp"}
RENDER_DPI = 150  # lower than the classic OCR engines: the VLM works on a downscaled page anyway
HEADING_LABELS = {"doc_title": 1, "paragraph_title": 2, "title": 1}
IMAGE_LABELS = {"image", "chart", "figure", "seal"}


class PaddleVlEngine(Engine):
    name = "paddle_vl"
    license = "Apache-2.0 (PaddleOCR, PaddlePaddle; model PaddleOCR-VL under Apache-2.0)"
    extra = "paddle"
    heavy = True
    note = "experimental: needs 8+ GB RAM or a GPU"

    def __init__(self, config=None) -> None:
        super().__init__(config)
        self._pipeline = None

    def missing_reason(self) -> str | None:
        missing = self.module_missing("paddleocr", "paddle")
        if missing:
            return missing
        if not self.config.use_gpu and not self.config.paddle_on_cpu:
            return "needs a GPU (too slow and too big for CPU). Set gpu: on, or paddle_on_cpu: true"
        if self.config.use_gpu:
            import paddle

            if not paddle.device.is_compiled_with_cuda():
                return "paddlepaddle is the CPU build: install paddlepaddle-gpu to use the GPU"
        return None

    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        if file_type in IMAGE_TYPES:
            return True
        return file_type == "pdf" and page_kind != PageKind.DIGITAL

    def _load(self):
        if self._pipeline is None:
            from paddleocr import PaddleOCRVL

            device = "gpu:0" if self.config.use_gpu else "cpu"
            self._pipeline = PaddleOCRVL(device=device)
        return self._pipeline

    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        pipeline = self._load()
        result: list[Page] = []
        for number, image, scale in iter_page_images(data, file_type, pages, RENDER_DPI):
            bgr = np.asarray(image)[:, :, ::-1].copy()  # PaddleOCR expects OpenCV-style BGR
            try:
                outputs = list(pipeline.predict(bgr))
            except Exception as err:
                raise EngineError(f"PaddleOCR-VL failed on page {number}: {err}") from err
            blocks: list[Block] = []
            for output in outputs:
                blocks.extend(_blocks_from_result(output.json["res"], scale, self.name))
            result.append(Page(number=number, engine=self.name, blocks=blocks))
        return result

    def release(self) -> None:
        """Free the model and give GPU memory back (important with only 4 GB of VRAM)."""
        self._pipeline = None
        gc.collect()
        try:
            import paddle

            if paddle.device.is_compiled_with_cuda():
                paddle.device.cuda.empty_cache()
        except ImportError:
            pass


def _blocks_from_result(res: dict, scale: float, engine: str) -> list[Block]:
    """Turn PaddleOCR-VL's list of labelled regions into Blocks, in reading order."""
    blocks: list[Block] = []
    for item in res.get("parsing_res_list", []):
        label, content = item.get("block_label", ""), (item.get("block_content") or "").strip()
        x0, y0, x1, y1 = item.get("block_bbox", [0, 0, 0, 0])
        box = BBox(x0=x0 / scale, y0=y0 / scale, x1=x1 / scale, y1=y1 / scale)
        if label in IMAGE_LABELS:
            made = [Block(type=BlockType.IMAGE, text=content)]
        elif not content:
            continue
        elif label in HEADING_LABELS:
            made = [Block(type=BlockType.HEADING, text=content, level=HEADING_LABELS[label])]
        elif label == "table" or content.lower().startswith("<table"):
            made = (
                html_to_blocks(content)
                if "<table" in content.lower()
                else markdown_to_blocks(content)
            )
        else:
            made = markdown_to_blocks(content) or [Block(type=BlockType.PARAGRAPH, text=content)]
        for block in made:
            block.bbox, block.engine = box, engine
        blocks.extend(made)
    return blocks
