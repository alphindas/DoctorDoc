"""Cloud vision model fallback. OFF by default: nothing leaves your machine unless you
turn it on (config: cloud.enabled = true) and give it an OpenAI-compatible endpoint.
"""

from __future__ import annotations

import base64
import io

import httpx

from doctordoc.engines._images import iter_page_images
from doctordoc.engines._markup import markdown_to_blocks
from doctordoc.engines.base import Engine, EngineError
from doctordoc.models import Page, PageKind

IMAGE_TYPES = {"png", "jpg", "tiff", "bmp", "gif", "webp"}
PROMPT = (
    "Transcribe this document page to Markdown. Keep headings, lists and tables "
    "(use GitHub pipe tables). Copy text exactly. Output only the Markdown."
)


class CloudVlmEngine(Engine):
    name = "cloud_vlm"
    license = "n/a (calls a remote service you configure)"
    extra = ""

    def missing_reason(self) -> str | None:
        cloud = self.config.cloud
        if not cloud.enabled:
            return "cloud fallback is off (set cloud.enabled: true to allow it)"
        if not cloud.base_url or not cloud.model:
            return "cloud.base_url and cloud.model must be set"
        return None

    def supports(self, file_type: str, page_kind: PageKind | None = None) -> bool:
        if file_type in IMAGE_TYPES:
            return True
        return file_type == "pdf" and page_kind != PageKind.DIGITAL

    def parse(self, data: bytes, file_type: str, pages: list[int] | None = None) -> list[Page]:
        result = []
        for number, image, _scale in iter_page_images(data, file_type, pages, dpi=150):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            markdown = self._ask(base64.b64encode(buffer.getvalue()).decode())
            blocks = markdown_to_blocks(markdown, engine=self.name)
            result.append(Page(number=number, engine=self.name, blocks=blocks))
        return result

    def _ask(self, image_b64: str) -> str:
        cloud = self.config.cloud
        payload = {
            "model": cloud.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": PROMPT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                    ],
                }
            ],
        }
        headers = {"Authorization": f"Bearer {cloud.api_key}"} if cloud.api_key else {}
        try:
            response = httpx.post(
                cloud.base_url.rstrip("/") + "/chat/completions",
                json=payload,
                headers=headers,
                timeout=120,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as err:
            raise EngineError(f"cloud VLM request failed: {err}") from err
        return str(content or "")
