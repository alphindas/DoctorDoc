"""Small HTML builders for the UI (chips, tables, highlighted text).

Pure functions that return strings, so they are easy to test without Streamlit. Anything that
comes from a document is HTML-escaped here: a hostile file must not be able to inject markup.
"""

from __future__ import annotations

import re
from html import escape

from doctordoc.models import Block, BlockType, Decision, Page, PageKind

# ---------------------------------------------------------------------------- chips


def chip(text: str, tone: str = "grey", icon: str = "") -> str:
    """A small rounded label. Tones: green amber red grey blue teal purple orange."""
    mark = f'<span class="dd-chip-icon">{escape(icon)}</span>' if icon else ""
    return f'<span class="dd-chip dd-{tone}">{mark}{escape(text)}</span>'


PAGE_KIND_CHIPS: dict[PageKind, tuple[str, str, str]] = {
    PageKind.DIGITAL: ("Digital", "blue", "▤"),
    PageKind.SCANNED: ("Scanned", "teal", "▣"),
    PageKind.MIXED: ("Mixed", "purple", "◧"),
    PageKind.GARBLED: ("Garbled", "orange", "!"),
    PageKind.BLANK: ("Blank", "grey", "○"),
    PageKind.NOT_PAGED: ("Document", "grey", "▤"),
}


def page_kind_chip(kind: PageKind) -> str:
    label, tone, icon = PAGE_KIND_CHIPS[kind]
    return chip(label, tone, icon)


def confidence_tone(confidence: float, review_threshold: float = 0.7) -> str:
    """green = high, amber = middle, red = below the review threshold."""
    if confidence < review_threshold:
        return "red"
    return "green" if confidence >= 0.9 else "amber"


def confidence_chip(confidence: float, review_threshold: float = 0.7) -> str:
    return chip(f"{confidence:.0%}", confidence_tone(confidence, review_threshold))


# outcome -> (label, tone, icon).  Used = green, failed = red, rejected = amber, skipped = grey.
OUTCOMES: dict[str, tuple[str, str, str]] = {
    "accepted": ("Used", "green", "✓"),
    "rejected": ("Rejected", "amber", "!"),
    "failed": ("Failed", "red", "✕"),
    "timeout": ("Timed out", "red", "✕"),
    "skipped": ("Skipped", "grey", "–"),
    "fallback": ("Kept, low quality", "amber", "!"),
    "merged": ("Merged", "teal", "+"),
}


def outcome_chip(decision: Decision) -> str:
    if decision.engine == "triage":  # the page-kind check is not an engine: do not call it "Used"
        label, tone, icon = (
            ("Checked", "blue", "✓") if decision.outcome == "accepted" else ("Skipped", "grey", "–")
        )
    else:
        label, tone, icon = OUTCOMES.get(decision.outcome, (decision.outcome, "grey", "?"))
    return chip(label, tone, icon)


# ---------------------------------------------------------------------------- tables


def decisions_html(decisions: list[Decision]) -> str:
    """The router log as an HTML table: scope, engine, outcome, reason, quality, seconds."""
    head = "".join(
        f"<th>{name}</th>"
        for name in ("Scope", "Engine", "Outcome", "Reason", "Quality", "Seconds")
    )
    rows = []
    for d in decisions:
        quality = "" if d.quality is None else f"{d.quality:.2f}"
        rows.append(
            "<tr>"
            f"<td>{escape(d.scope)}</td>"
            f"<td><b>{escape(d.engine)}</b></td>"
            f"<td>{outcome_chip(d)}</td>"
            f'<td class="dd-reason">{escape(d.reason)}</td>'
            f"<td>{quality}</td>"
            f"<td>{d.seconds:.2f}</td>"
            "</tr>"
        )
    return f'<table class="dd-table"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def table_html(rows: list[list[str]]) -> str:
    """A real HTML table. The first row is the header."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    padded = [[*r, *[""] * (width - len(r))] for r in rows]
    head = "".join(f"<th>{escape(c)}</th>" for c in padded[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(c)}</td>" for c in r) + "</tr>" for r in padded[1:]
    )
    return f'<table class="dd-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


# ---------------------------------------------------------------------------- blocks


def block_html(block: Block) -> str:
    """One block as HTML: headings, paragraphs, list items and tables."""
    kind = block.type
    if kind == BlockType.TABLE and block.table is not None:
        return table_html(block.table.rows)
    text = escape(block.text)
    if kind == BlockType.HEADING:
        level = min(max((block.level or 2) + 1, 3), 6)  # keep headings small inside a card
        return f"<h{level}>{text}</h{level}>"
    if kind == BlockType.LIST_ITEM:
        return f'<p class="dd-li">• {text}</p>'
    if kind == BlockType.CODE:
        return f"<pre>{text}</pre>"
    if kind == BlockType.IMAGE:
        return f'<p class="dd-muted">[image{": " + text if text else ""}]</p>'
    if kind == BlockType.KEY_VALUE and ": " in block.text:
        key, value = block.text.split(": ", 1)
        return f"<p><b>{escape(key)}:</b> {escape(value)}</p>"
    return f"<p>{text}</p>"


def page_html(page: Page) -> str:
    """The extracted blocks of one page, inside a scrollable box."""
    if not page.blocks:
        note = "This page is blank." if page.kind == PageKind.BLANK else "No text was extracted."
        return f'<div class="dd-scroll"><p class="dd-muted">{note}</p></div>'
    return f'<div class="dd-scroll">{"".join(block_html(b) for b in page.blocks)}</div>'


def pages_html(pages: list[Page]) -> str:
    """Every page, one after the other (for the side-by-side engine comparison)."""
    parts = []
    for page in pages:
        if len(pages) > 1:
            parts.append(f'<p class="dd-muted">— page {page.number} —</p>')
        parts.extend(block_html(b) for b in page.blocks)
    return f'<div class="dd-scroll">{"".join(parts) or "<p class=dd-muted>No text.</p>"}</div>'


# ---------------------------------------------------------------------------- highlight


def highlight_html(text: str, start: int, end: int, context: int = 300) -> str:
    """The text around [start, end), with that exact span marked. Everything is escaped."""
    start = max(0, min(start, len(text)))
    end = max(start, min(end, len(text)))
    left = max(0, start - context)
    right = min(len(text), end + context)
    before = escape(text[left:start])
    hit = escape(text[start:end])
    after = escape(text[end:right])
    lead = "… " if left > 0 else ""
    tail = " …" if right < len(text) else ""
    return f'<div class="dd-source">{lead}{before}<mark>{hit}</mark>{after}{tail}</div>'


def markdown_for_screen(markdown: str) -> str:
    """Markdown ready for st.markdown: page markers become a visible divider, other HTML
    comments are dropped, and '$' is escaped so it is not read as maths."""
    text = re.sub(r"<!-- page (\d+) -->", r"\n\n*— page \1 —*\n\n", markdown)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return text.replace("$", "\\$")
