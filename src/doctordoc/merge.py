"""Mixed pages: keep the winner's text, and add what only the other engine found.

Example: a page has a real text layer AND a scanned stamp inside a picture. The text layer
wins on quality, but only OCR can read the stamp. We add the stamp so nothing is lost.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from doctordoc.models import Block, Page

SAME_TEXT = 0.85  # blocks at least this similar count as "the same text"
MIN_CHARS = 3


def merge_missing_blocks(base: Page, other: Page) -> int:
    """Add to `base` every block of `other` whose text is not already on `base`.
    Blocks are put back in reading order (top to bottom) when all of them have a position.
    Returns how many blocks were added."""
    known = [_squash(b.plain_text) for b in base.blocks if b.plain_text]
    whole = "".join(known)
    added: list[Block] = []
    for block in other.blocks:
        text = _squash(block.plain_text)
        if len(text) >= MIN_CHARS and not _is_covered(text, known, whole):
            added.append(block)
    if not added:
        return 0
    base.blocks = _in_reading_order(base.blocks + added)
    return len(added)


def _squash(text: str) -> str:
    """Lowercase letters and digits only, so spacing and punctuation differences vanish."""
    return re.sub(r"[\W_]+", "", text.casefold())


def _is_covered(text: str, known_blocks: list[str], whole: str) -> bool:
    if text in whole:
        return True
    return any(
        SequenceMatcher(None, text, other, autojunk=False).ratio() >= SAME_TEXT
        for other in known_blocks
        if abs(len(other) - len(text)) <= max(len(text), len(other)) * 0.5
    )


def _in_reading_order(blocks: list[Block]) -> list[Block]:
    if all(b.bbox is not None for b in blocks):
        return sorted(blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))  # type: ignore[union-attr]
    return blocks  # no positions: the new blocks simply stay at the end
