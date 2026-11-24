"""Source grounding: find where in the document a value came from.

Idea borrowed from google/langextract (align the extracted text with the source using
difflib and report exact / fuzzy matches), but written for our case:
  * values come from a Pydantic schema, so they are typed (text, number, date);
  * numbers and dates are compared by VALUE ("4620" matches "4,620.00",
    2024-03-15 matches "15 March 2024"), not by characters.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from doctordoc.extract.models import MatchKind, Source
from doctordoc.models import Block, Page

FUZZY_THRESHOLD = 0.85  # minimum similarity for a fuzzy text match
SCORES: dict[str, float] = {"exact": 1.0, "normalized": 0.95, "inferred": 0.8, "none": 0.0}


@dataclass
class PageText:
    """The text of one page, plus where each block starts (to map a span back to a block)."""

    number: int
    text: str
    blocks: list[tuple[int, int, Block]] = field(default_factory=list)  # (start, end, block)

    @classmethod
    def from_page(cls, page: Page) -> PageText:
        parts: list[str] = []
        blocks: list[tuple[int, int, Block]] = []
        position = 0
        for block in page.blocks:
            text = block.plain_text
            if not text:
                continue
            if parts:
                position += 2  # the "\n\n" between blocks (same rule as Page.text)
            blocks.append((position, position + len(text), block))
            parts.append(text)
            position += len(text)
        return cls(number=page.number, text="\n\n".join(parts), blocks=blocks)

    @classmethod
    def from_text(cls, text: str, number: int = 1) -> PageText:
        return cls(number=number, text=text)

    def block_at(self, start: int, end: int) -> tuple[int, Block] | None:
        for index, (b_start, b_end, block) in enumerate(self.blocks):
            if b_start <= start < b_end or (start <= b_start < end):
                return index, block
        return None

    def confidence_between(self, start: int, end: int) -> float:
        """Mean OCR confidence of the blocks touched by the span. 1.0 if there is none."""
        scores = [
            block.confidence
            for b_start, b_end, block in self.blocks
            if b_start < end and start < b_end and block.confidence is not None
        ]
        return sum(scores) / len(scores) if scores else 1.0


@dataclass
class Grounding:
    kind: MatchKind
    score: float
    source: Source | None = None
    ocr_confidence: float = 1.0

    @property
    def grounded(self) -> bool:
        return self.kind in ("exact", "normalized", "fuzzy")


# ---------------------------------------------------------------------------- public


def ground_value(value: Any, pages: list[PageText]) -> Grounding:
    """Find `value` in the pages. The first page (and first place on it) that matches wins."""
    if isinstance(value, bool):
        return Grounding("inferred", SCORES["inferred"])  # true/false is not copied from text
    for page in pages:
        found = _find_on_page(value, page)
        if found is not None:
            start, end, kind, score = found
            return _build(page, start, end, kind, score)
    return Grounding("none", 0.0)


def _find_on_page(value: Any, page: PageText) -> tuple[int, int, MatchKind, float] | None:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return _find_date(value, page.text)
    if isinstance(value, (int, float, Decimal)):
        return _find_number(value, page.text)
    return _find_text(str(value), page.text)


def _build(page: PageText, start: int, end: int, kind: MatchKind, score: float) -> Grounding:
    located = page.block_at(start, end)
    source = Source(
        page=page.number,
        start=start,
        end=end,
        text=page.text[start:end],
        match=kind,
        block_index=located[0] if located else None,
        bbox=located[1].bbox if located else None,
    )
    return Grounding(kind, score, source, page.confidence_between(start, end))


# ---------------------------------------------------------------------------- text

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def _normalize(text: str) -> tuple[str, list[int]]:
    """Lowercase, fold look-alike characters, collapse whitespace.
    Also returns, for every output character, its position in the original text."""
    out: list[str] = []
    origin: list[int] = []
    previous_space = True
    for index, char in enumerate(text):
        for piece in unicodedata.normalize("NFKC", char).translate(_QUOTES).casefold():
            if piece.isspace():
                if not previous_space:
                    out.append(" ")
                    origin.append(index)
                previous_space = True
            else:
                out.append(piece)
                origin.append(index)
                previous_space = False
    while out and out[-1] == " ":
        out.pop()
        origin.pop()
    return "".join(out), origin


def _find_text(value: str, text: str) -> tuple[int, int, MatchKind, float] | None:
    needle, _ = _normalize(value)
    if not needle:
        return None
    haystack, origin = _normalize(text)
    position = _find_with_boundaries(haystack, needle)
    if position is not None:
        start, end = origin[position], origin[position + len(needle) - 1] + 1
        kind: MatchKind = "exact" if text[start:end] == value else "normalized"
        return start, end, kind, SCORES[kind]
    return _find_fuzzy(needle, haystack, origin)


def _find_with_boundaries(haystack: str, needle: str) -> int | None:
    """Find `needle` so that it does not start or end in the middle of a word or a number.

    A match is refused when the character just outside it is the same kind (letter-letter
    or digit-digit): "US" inside "USD", "42" inside "0042". A letter next to a digit is
    fine, because OCR often glues them ("08:30-24:00Drilled")."""
    start = haystack.find(needle)
    while start != -1:
        before = haystack[start - 1] if start > 0 else " "
        after = haystack[start + len(needle)] if start + len(needle) < len(haystack) else " "
        if not (_glued(before, needle[0]) or _glued(needle[-1], after)):
            return start
        start = haystack.find(needle, start + 1)
    return None


def _glued(left: str, right: str) -> bool:
    """True if two neighbouring characters belong to the same word or number."""
    return (left.isalpha() and right.isalpha()) or (left.isdigit() and right.isdigit())


def _find_fuzzy(
    needle: str, haystack: str, origin: list[int]
) -> tuple[int, int, MatchKind, float] | None:
    """Slide a window of the same number of words over the text, keep the most similar one."""
    words = [(m.start(), m.end()) for m in re.finditer(r"\S+", haystack)]
    size = len(needle.split())
    best: tuple[float, int, int] | None = None
    for width in {max(1, size - 1), size, size + 1}:
        for i in range(0, len(words) - width + 1):
            begin, finish = words[i][0], words[i + width - 1][1]
            candidate = haystack[begin:finish]
            matcher = SequenceMatcher(None, needle, candidate, autojunk=False)
            if (
                matcher.real_quick_ratio() < FUZZY_THRESHOLD
                or matcher.quick_ratio() < FUZZY_THRESHOLD
            ):
                continue
            ratio = matcher.ratio()
            if ratio >= FUZZY_THRESHOLD and (best is None or ratio > best[0]):
                best = (ratio, begin, finish)
    if best is None:
        return None
    ratio, begin, finish = best
    return origin[begin], origin[finish - 1] + 1, "fuzzy", round(0.9 * ratio, 3)


# ---------------------------------------------------------------------------- numbers

# a number must stand alone: not glued to a word, and not a piece of an ID like INV-0042
# (and not one piece of a time like 06:00)
_NUMBER = re.compile(r"(?<![\w.,])(?<!\w-)(?<!\d:)(?:\d[\d.,]*\d|\d)(?!:\d)")


def parse_number(token: str) -> Decimal | None:
    """'4,620.00' -> 4620.00 ; '1.234,56' -> 1234.56 ; '12,5' -> 12.5 ; '1,234' -> 1234."""
    has_comma, has_dot = "," in token, "." in token
    if has_comma and has_dot:
        decimal_sep = "," if token.rfind(",") > token.rfind(".") else "."
        thousands = "." if decimal_sep == "," else ","
        cleaned = token.replace(thousands, "").replace(decimal_sep, ".")
    elif has_comma:
        parts = token.split(",")
        thousands_like = all(len(p) == 3 for p in parts[1:])
        cleaned = token.replace(",", "") if thousands_like else token.replace(",", ".")
    elif token.count(".") > 1:
        cleaned = token.replace(".", "")
    else:
        cleaned = token
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _find_number(
    value: int | float | Decimal, text: str
) -> tuple[int, int, MatchKind, float] | None:
    try:
        wanted = abs(Decimal(str(value)))
    except InvalidOperation:
        return None
    for match in _NUMBER.finditer(text):
        parsed = parse_number(match.group())
        if parsed is not None and abs(parsed) == wanted:
            same_text = match.group().replace(",", "") == str(value)
            kind: MatchKind = "exact" if same_text else "normalized"
            return match.start(), match.end(), kind, SCORES[kind]
    return None


# ---------------------------------------------------------------------------- dates

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_MONTH_WORD = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_ISO = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?!\d)")
_DAY_MONTH_YEAR = re.compile(
    rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?[\s\-./]*{_MONTH_WORD}[\s\-.,/]*(\d{{4}})", re.I
)
_MONTH_DAY_YEAR = re.compile(rf"{_MONTH_WORD}\s*(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})", re.I)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def date_candidates(text: str) -> list[tuple[int, int, date]]:
    """Every date-looking piece of text, with the dates it could mean."""
    found: list[tuple[int, int, date]] = []
    for m in _ISO.finditer(text):
        d = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        if d:
            found.append((m.start(), m.end(), d))
    for m in _NUMERIC.finditer(text):
        year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        for day, month in ((int(m[1]), int(m[2])), (int(m[2]), int(m[1]))):  # d/m/y AND m/d/y
            d = _safe_date(year, month, day)
            if d:
                found.append((m.start(), m.end(), d))
    for m in _DAY_MONTH_YEAR.finditer(text):
        d = _safe_date(int(m[3]), _MONTHS[m[2][:3].lower()], int(m[1]))
        if d:
            found.append((m.start(), m.end(), d))
    for m in _MONTH_DAY_YEAR.finditer(text):
        d = _safe_date(int(m[3]), _MONTHS[m[1][:3].lower()], int(m[2]))
        if d:
            found.append((m.start(), m.end(), d))
    return sorted(found, key=lambda item: item[0])


def _find_date(value: date, text: str) -> tuple[int, int, MatchKind, float] | None:
    for start, end, candidate in date_candidates(text):
        if candidate == value:
            kind: MatchKind = "exact" if text[start:end] == value.isoformat() else "normalized"
            return start, end, kind, SCORES[kind]
    return None
