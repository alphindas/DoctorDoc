"""All settings in one place. Load from a doctordoc.yaml file, env vars (DOCTORDOC_*), or code."""

from __future__ import annotations

import shutil
import subprocess
from copy import copy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Which engines to try, in order, for each kind of PDF page.
DEFAULT_PAGE_CHAINS: dict[str, list[str]] = {
    "digital": ["docling", "pdf_text"],
    "mixed": ["docling", "pdf_text"],  # the router also runs OCR and keeps the better one
    "scanned": ["paddle_vl", "rapidocr", "tesseract", "cloud_vlm"],
    "garbled": ["paddle_vl", "rapidocr", "tesseract", "cloud_vlm", "pdf_text"],
}

# Which engines to try, in order, for each non-PDF file type.
_OFFICE = ["docling", "native", "markitdown"]
_PLAIN = ["native", "markitdown"]
_IMAGE = ["paddle_vl", "rapidocr", "tesseract", "cloud_vlm"]
DEFAULT_FILE_CHAINS: dict[str, list[str]] = {
    "docx": _OFFICE,
    "pptx": _OFFICE,
    "html": ["native", "docling", "markitdown"],  # native keeps inline formatting together
    "xlsx": _PLAIN,
    "txt": _PLAIN,
    "md": _PLAIN,
    "csv": _PLAIN,
    "tsv": _PLAIN,
    "json": _PLAIN,
    "xml": _PLAIN,
    "eml": ["native"],
    **{t: _IMAGE for t in ("png", "jpg", "tiff", "bmp", "gif", "webp")},
}

# Seconds allowed PER PAGE for each engine (plus 60 s extra to load the model).
DEFAULT_TIMEOUTS: dict[str, float] = {
    "docling": 60,
    "paddle_vl": 60,
    "rapidocr": 90,  # OCR reads each page 4 times to find which way up it is
    "tesseract": 90,
    "cloud_vlm": 60,
    "pdf_text": 10,
    "native": 30,
    "markitdown": 30,
}


class CloudConfig(BaseModel):
    """Optional cloud vision model (any OpenAI-compatible endpoint). OFF by default."""

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"


class LLMConfig(BaseModel):
    """The LLM used for field extraction. The default is a local Ollama model."""

    provider: Literal["ollama", "openai"] = "ollama"  # "openai" = any OpenAI-compatible server
    base_url: str = "http://localhost:11434"  # for "openai", include the /v1 part
    api_key: str = ""  # Ollama ignores it, hosted servers need one
    model: str = "llama3.2:3b"
    temperature: float = 0.0
    max_retries: int = 1  # extra tries when the model returns invalid JSON
    # Limits so one bad document cannot keep the model busy for ever. For scale, measured with
    # llama3.2:3b on a 4 GB laptop GPU (about 16 tokens/s): a 45-line invoice needed 1831
    # answer tokens and about 2 minutes. Raise them for longer lists or a slower machine.
    max_output_tokens: int = 4096  # longest answer per call
    timeout: float = 300.0  # seconds per call
    max_seconds_per_document: float = 600.0  # all calls and retries for one document
    max_chars_per_call: int = 12000  # longer documents are split by page
    num_ctx: int = 8192  # context window asked from Ollama (keeps memory small)


class IntakeLimits(BaseModel):
    """Zip-bomb protection for containers (zip, eml, msg)."""

    max_depth: int = 3
    max_files: int = 200
    max_total_mb: float = 200.0
    max_member_mb: float = 100.0


class ApiConfig(BaseModel):
    """The HTTP API (`doctordoc api`). None of this is a login."""

    model_config = ConfigDict(validate_assignment=True)  # also check values set from code

    # /parse and /extract requests that may wait while one works. The next one gets a 503.
    max_waiting: int = Field(4, ge=0)
    docs: bool = True  # serve /docs, /redoc and /openapi.json
    # Extra Host names to accept besides localhost, 127.0.0.1 and ::1. Names only, no port.
    allowed_hosts: list[str] = Field(default_factory=list)

    @field_validator("allowed_hosts")
    @classmethod
    def _names_only(cls, hosts: list[str]) -> list[str]:
        """Refuse entries that could never match, so a mistake is not silent."""
        cleaned = []
        for entry in hosts:
            name = entry.strip().lower()
            if name.startswith("[") and name.endswith("]"):
                name = name[1:-1]
            # exactly one ":" means "name:port" (an IPv6 address has at least two)
            if not name or name.count(":") == 1 or any(c in name for c in "/*[]@ "):
                raise ValueError(
                    f"{entry!r} is not a host name: write names or addresses only, "
                    "with no port, no http:// and no wildcard."
                )
            cleaned.append(name)
        return cleaned


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCTORDOC_", env_nested_delimiter="__")

    # --- quality thresholds ---
    quality_threshold: float = 0.55  # engine output below this is rejected, next engine is tried
    garbled_threshold: float = 0.5  # triage: a text layer below this is called "garbled"
    min_page_chars: int = 10  # triage: fewer characters than this means "no text layer"
    mixed_image_coverage: float = 0.10  # triage: pictures covering this much of the page => "mixed"
    merge_mixed_pages: bool = True  # mixed pages: also add text that only the losing engine found

    # --- routing ---
    page_chains: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_PAGE_CHAINS))
    file_chains: dict[str, list[str]] = Field(default_factory=lambda: dict(DEFAULT_FILE_CHAINS))
    timeouts: dict[str, float] = Field(default_factory=lambda: dict(DEFAULT_TIMEOUTS))

    # --- hardware ---
    gpu: Literal["auto", "on", "off"] = "auto"
    paddle_on_cpu: bool = False  # PaddleOCR-VL on CPU is very slow and needs several GB of RAM
    free_models_after_use: bool = True  # unload heavy models when a file is done (saves VRAM)
    ocr_dpi: int = 200
    ocr_languages: str = "eng"  # Tesseract-style language code
    # find which way up each OCR page is: try 0/90/180/270 degrees and keep the upright one
    fix_rotation: bool = True
    # ...but accept the page as it is, after one read, if it scores at least this (0..1).
    # Set it above 1 to always try all four angles.
    rotation_early_stop: float = 0.75

    # --- optional cloud fallback ---
    cloud: CloudConfig = Field(default_factory=CloudConfig)

    # --- field extraction (phase 2) ---
    llm: LLMConfig = Field(default_factory=LLMConfig)
    review_threshold: float = 0.7  # fields with confidence below this go to needs_review

    # --- HTTP API ---
    api: ApiConfig = Field(default_factory=ApiConfig)

    # --- files ---
    intake: IntakeLimits = Field(default_factory=IntakeLimits)
    max_file_mb: float = 200.0
    cache_dir: Path | None = None  # set it to keep parsed results on disk, keyed by file hash

    @field_validator("page_chains", "file_chains", "timeouts")
    @classmethod
    def _over_the_defaults(cls, given: dict, info: ValidationInfo) -> dict:
        """A file or variable changes only the entries it names; the others keep their default.
        An empty list is an entry like any other: no engines for that kind of page or file."""
        defaults = {
            "page_chains": DEFAULT_PAGE_CHAINS,
            "file_chains": DEFAULT_FILE_CHAINS,
            "timeouts": DEFAULT_TIMEOUTS,
        }[info.field_name]
        # every list is copied on its own: in the defaults, several file types share one list
        return {**{name: copy(value) for name, value in defaults.items()}, **given}

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        data: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls(**data)

    @property
    def use_gpu(self) -> bool:
        """Resolve 'auto': use the GPU only if an NVIDIA driver answers."""
        if self.gpu == "on":
            return True
        if self.gpu == "off":
            return False
        return _nvidia_gpu_present()

    def fingerprint(self) -> str:
        """A short string that changes when settings that affect parse output change."""
        import hashlib

        keys = {
            "quality_threshold",
            "garbled_threshold",
            "min_page_chars",
            "mixed_image_coverage",
            "page_chains",
            "file_chains",
            "gpu",
            "ocr_dpi",
            "ocr_languages",
            "fix_rotation",
            "rotation_early_stop",
            "cloud",
            "merge_mixed_pages",
        }
        blob = self.model_dump_json(include=keys)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


_GPU_CACHE: list[bool] = []


def _nvidia_gpu_present() -> bool:
    if _GPU_CACHE:
        return _GPU_CACHE[0]
    found = False
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(
                ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10, check=False
            )
            found = result.returncode == 0 and "GPU" in result.stdout
        except (OSError, subprocess.SubprocessError):
            found = False
    _GPU_CACHE.append(found)
    return found
