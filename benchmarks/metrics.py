"""How we score an engine's output against the ground truth. Pure Python, easy to test.

* text accuracy  = 1 - normalised edit distance, on lower-case letters and digits only
                   (spacing and punctuation are ignored; wrong or missing characters and
                   wrong ORDER are punished)
* word recall    = share of the true words that appear in the output (order ignored)
* table accuracy = share of true table cells found at the right (row, column)
* field accuracy = share of true fields extracted correctly
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from rapidfuzz.distance import Levenshtein

Table = list[list[str]]
NUMBER_TOLERANCE = 0.005


def squash(text: str) -> str:
    """Lower-case letters and digits only."""
    return "".join(c for c in text.casefold() if c.isalnum())


def text_accuracy(output: str, truth: str) -> float:
    """1.0 = identical, 0.0 = nothing in common."""
    a, b = squash(output), squash(truth)
    if not b:
        return 1.0 if not a else 0.0
    return round(1.0 - Levenshtein.normalized_distance(a, b), 4)


def word_recall(output: str, truth: str) -> float:
    """How many of the true words were found (each word counted as often as it is true)."""
    wanted = Counter(re.findall(r"\w+", truth.casefold()))
    if not wanted:
        return 1.0
    found = Counter(re.findall(r"\w+", output.casefold()))
    return round(sum((wanted & found).values()) / sum(wanted.values()), 4)


# ---------------------------------------------------------------------------- tables


def table_accuracy(output_tables: list[Table], truth_tables: list[Table]) -> float:
    """Average over the true tables of the best cell-by-cell match with any output table."""
    if not truth_tables:
        return 1.0
    candidates = list(output_tables)
    if len(output_tables) > 1:
        candidates.append(stitch(output_tables))
    scores = [max((_cell_score(c, t) for c in candidates), default=0.0) for t in truth_tables]
    return round(sum(scores) / len(scores), 4)


def stitch(tables: list[Table]) -> Table:
    """Join tables that continue on the next page: one after the other, repeated header
    rows dropped."""
    joined = list(tables[0])
    header = [squash(c) for c in tables[0][0]] if tables[0] else None
    for table in tables[1:]:
        rows = list(table)
        if rows and header is not None and [squash(c) for c in rows[0]] == header:
            rows = rows[1:]
        joined.extend(rows)
    return joined


def _cell_score(candidate: Table, truth: Table) -> float:
    total = hits = 0
    for r, row in enumerate(truth):
        for c, cell in enumerate(row):
            wanted = squash(cell)
            if not wanted:
                continue
            total += 1
            try:
                hits += squash(candidate[r][c]) == wanted
            except IndexError:
                pass
    return hits / total if total else 1.0


# ---------------------------------------------------------------------------- fields


def values_match(got: Any, want: Any) -> bool:
    """Compare an extracted value with the true one, ignoring formatting."""
    if got is None:
        return want is None
    if isinstance(got, (date, datetime)):
        got = got.isoformat()[:10]
    if isinstance(want, (date, datetime)):
        want = want.isoformat()[:10]
    if isinstance(want, (int, float)) and not isinstance(want, bool):
        try:
            return abs(float(got) - float(want)) <= NUMBER_TOLERANCE
        except (TypeError, ValueError):
            return False
    return squash(str(got)) == squash(str(want))


def field_accuracy(extracted: dict[str, Any], truth: dict[str, Any]) -> float:
    """Correct fields / true fields."""
    if not truth:
        return 1.0
    right = sum(values_match(extracted.get(key), want) for key, want in truth.items())
    return round(right / len(truth), 4)


# ---------------------------------------------------------------------------- helpers


def mean(values: Iterable[float | None]) -> float | None:
    numbers = [v for v in values if v is not None]
    return round(sum(numbers) / len(numbers), 4) if numbers else None
