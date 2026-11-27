"""Say at startup which pages could be sent to the cloud server.

This only reads the settings. It does not change which engines run.
"""

from __future__ import annotations

import logging

from doctordoc.config import Config
from doctordoc.detect import IMAGE_TYPES
from doctordoc.engines.cloud_vlm import CloudVlmEngine
from doctordoc.models import PageKind

log = logging.getLogger("doctordoc.cloud")

UI_NOTE = (
    ' In the web UI the "Local only" switch decides this, not cloud.enabled: keep it on to keep '
    "pages on this computer. With it off and cloud_vlm switched on in the Engines list, the "
    "Compare engines tab runs the cloud engine whatever the chains say."
)


def cloud_exposure(config: Config) -> tuple[list[str], list[str]]:
    """(PDF page kinds, file types) that the router could hand to the cloud engine."""
    engine = CloudVlmEngine(config)
    if engine.missing_reason() is not None:  # cloud off or not set up: nothing is sent
        return [], []
    kinds = []
    for kind in (PageKind.DIGITAL, PageKind.MIXED, PageKind.SCANNED, PageKind.GARBLED):
        chain = config.page_chains.get(kind.value, [])
        if kind == PageKind.MIXED:  # the router also runs the scanned chain on mixed pages
            chain = [*chain, *config.page_chains.get("scanned", [])]
        # the engine itself says which pages it accepts (it refuses digital ones)
        if engine.name in chain and engine.supports("pdf", kind):
            kinds.append(kind.value)
    file_types = [  # PDFs are routed by page kind, every other file by its type
        file_type
        for file_type, chain in config.file_chains.items()
        if file_type != "pdf" and engine.name in chain and engine.supports(file_type)
    ]
    return kinds, file_types


def cloud_warning(config: Config) -> str | None:
    """The warning to show, or None if the router cannot send anything to the cloud."""
    kinds, file_types = cloud_exposure(config)
    parts = []
    if kinds:
        parts.append(f"page kinds {', '.join(kinds)}")
    if file_types:
        images = "image files" if set(file_types) <= IMAGE_TYPES else "files of type"
        parts.append(f"{images} {', '.join(file_types)}")
    if not parts:
        return None
    # the address in cloud.base_url is left out on purpose: it can hold a password or key
    return (
        "Cloud fallback is ON: images of pages could be sent to the cloud server in "
        f"cloud.base_url, for {' and '.join(parts)}. To prevent all of it, set "
        "cloud.enabled: false. To prevent it for one entry, take cloud_vlm out of its chain in "
        "page_chains (page kinds) or file_chains (file types); `kind: []` uses no engine at all. "
        "Mixed pages also use the scanned chain, so `mixed: []` alone does not stop them: take "
        "cloud_vlm out of the scanned chain too."
    )


def warn_about_cloud(config: Config, note: str = "") -> None:
    """Log the warning, if there is one. Call it once, when a program starts."""
    message = cloud_warning(config)
    if message:
        log.warning("%s%s", message, note)
