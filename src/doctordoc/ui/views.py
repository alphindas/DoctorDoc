"""The four tabs. Each function only draws: the work is done by the library (`service`, `export`)."""

from __future__ import annotations

from html import escape

import streamlit as st

from doctordoc import export, service
from doctordoc.config import Config
from doctordoc.models import Document, EngineRun, Page, PageKind
from doctordoc.service import UiSettings
from doctordoc.ui import state, widgets
from doctordoc.ui.state import Entry, ParsedFile
from doctordoc.ui.style import card_title

MAX_SHOWN_CHARS = 150_000  # long outputs are cut on screen only: downloads are always complete
SCHEMAS = {
    "Invoice": "invoice",
    "Receipt": "receipt",
    "Drilling report": "drilling",
    "Generic form": "form",
}
OUTPUT_VIEWS = ["Rendered Markdown", "Markdown (raw)", "JSON", "Tables"]


def html(markup: str) -> None:
    st.html(markup)


def stem_of(doc: Document) -> str:
    return export._safe_stem(doc.source)


# ---------------------------------------------------------------------------- shared


def empty_state(tab: str, storage_note: str) -> None:
    with st.container(border=True, key=f"card_empty_{tab}"):
        card_title("Start here")
        st.write(
            "Add a file in the sidebar (or pick one of the examples), then click **Parse document**. "
            f"{storage_note}"
        )


def file_picker(entries: list[Entry]) -> Entry:
    """One selector above the tabs. It only shows up when there is more than one file."""
    if len(entries) == 1:
        return entries[0]
    labels = [e.label for e in entries]
    chosen = st.columns([2, 3])[0].selectbox("File", labels, key="selected_file")
    return entries[labels.index(chosen)] if chosen is not None and chosen in labels else entries[0]


def show_error(doc: Document) -> bool:
    """Show Document.error nicely. Returns True if there is one."""
    if not doc.error:
        return False
    st.error(f"**{doc.source}** could not be parsed.\n\n{doc.error}", icon=":material/error:")
    return True


def stale_note(parsed_config: str | None, config: Config) -> None:
    if parsed_config and parsed_config != service.config_to_json(config):
        st.info(
            "Settings changed since this file was parsed. Click **Parse document** to apply them."
        )


# ---------------------------------------------------------------------------- PARSE


def parse_tab(entry: Entry, parsed: list[ParsedFile]) -> None:
    doc = entry.doc
    if len(parsed) > 1 or any(p.doc.children for p in parsed):
        _results_table(parsed)
    if show_error(doc):
        return
    _file_line(doc)
    wrong, other = service.split_warnings(doc)
    for message in wrong:
        st.warning(f"**Wrong file extension.** {message}", icon=":material/warning:")
    if doc.children:
        with st.container(border=True, key=f"card_tree_{entry.key}"):
            card_title("Files inside")
            st.code(export.doc_tree(doc), language=None)
    if doc.pages:
        _viewer(entry)
    if other:
        with st.expander(f"Warnings ({len(other)})", expanded=True):
            for message in other:
                st.warning(message)
    if doc.pages or doc.children:
        _output_panel(entry)


def _file_line(doc: Document) -> None:
    kinds = export.kind_counts(doc)
    parts = [
        widgets.chip(doc.file_type.upper(), "blue"),
        widgets.chip(f"{len(doc.pages)} page(s)", "grey"),
    ]
    parts += [
        widgets.chip(f"{n} {kind}", widgets.PAGE_KIND_CHIPS[PageKind(kind)][1])
        for kind, n in kinds.items()
    ]
    seconds = doc.metadata.get("seconds")
    if seconds is not None:
        parts.append(widgets.chip(f"{seconds}s", "grey"))
    html(f"<div>{''.join(parts)}</div>")


def _results_table(parsed: list[ParsedFile]) -> None:
    docs = [p.doc for p in parsed]
    with st.container(border=True, key="card_results"):
        left, right = st.columns([4, 1])
        with left:
            card_title("Results")
        with right:
            st.download_button(
                "Download all (.zip)",
                export.outputs_zip(docs),
                file_name="doctordoc-results.zip",
                mime="application/zip",
                key="download_all",
                icon=":material/download:",
            )
        rows = [
            {**r, "quality": "-" if r["quality"] is None else f"{r['quality']:.2f}"}
            for r in export.summary_rows(docs)
        ]
        st.dataframe(
            rows, hide_index=True, width="stretch", height=min(35 * (len(rows) + 1) + 6, 420)
        )


def _viewer(entry: Entry) -> None:
    doc = entry.doc
    total = len(doc.pages)
    key = f"page_{entry.key}"
    index = min(max(st.session_state.get(key, 0), 0), total - 1)
    page = doc.pages[index]
    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True, key=f"card_page_{entry.key}"):
        top, count, prev, nxt = st.columns([5, 1.6, 0.7, 0.7], vertical_alignment="center")
        top.markdown(_page_chips(page), unsafe_allow_html=True)
        count.markdown(f"**{index + 1} / {total}**")
        prev.button(
            "",
            key=f"prev_{entry.key}",
            icon=":material/chevron_left:",
            disabled=index == 0,
            on_click=st.session_state.__setitem__,
            args=(key, index - 1),
        )
        nxt.button(
            "",
            key=f"next_{entry.key}",
            icon=":material/chevron_right:",
            disabled=index >= total - 1,
            on_click=st.session_state.__setitem__,
            args=(key, index + 1),
        )
        _page_picture(entry, page)
    with right, st.container(border=True, key=f"card_text_{entry.key}"):
        card_title("Extracted text")
        html(page_summary(page))
        html(widgets.page_html(page))


def page_summary(page: Page) -> str:
    parts = []
    if page.engine:
        parts.append(widgets.chip(page.engine.replace("+", " + "), "teal"))
    if page.quality is not None:
        parts.append(widgets.chip(f"quality {page.quality:.2f}", "grey"))
    return f"<div>{''.join(parts)}</div>"


def _page_chips(page: Page) -> str:
    return widgets.page_kind_chip(page.kind)


def _page_picture(entry: Entry, page: Page) -> None:
    picture = state.page_png(entry.data, entry.doc.file_type, page.number) if entry.data else None
    if picture:
        st.image(picture, width="stretch")
    else:
        st.info("No page picture for this kind of file. The extracted text is on the right.")


def _output_panel(entry: Entry) -> None:
    doc = entry.doc
    markdown, as_json = doc.to_markdown(), doc.to_json()
    tables = export.document_tables(doc, include_children=True)
    workbook = export.tables_to_xlsx(tables)
    with st.container(border=True, key=f"card_output_{entry.key}"):
        card_title("Output")
        view = (
            st.segmented_control(
                "Output view",
                OUTPUT_VIEWS,
                default=OUTPUT_VIEWS[0],
                key="output_view",
                label_visibility="collapsed",
            )
            or OUTPUT_VIEWS[0]
        )
        shown = {"Rendered Markdown": markdown, "Markdown (raw)": markdown, "JSON": as_json}.get(
            view, "\n\n".join(export.table_to_csv(t.table) for t in tables)
        )
        _action_buttons(entry, shown, markdown, as_json, workbook)
        if view == "Rendered Markdown":
            st.markdown(widgets.markdown_for_screen(_cut(markdown)))
        elif view == "Markdown (raw)":
            st.code(_cut(markdown), language="markdown", wrap_lines=True)
        elif view == "JSON":
            st.code(_cut(as_json), language="json")
        else:
            _tables_view(entry, tables)


def _cut(text: str) -> str:
    if len(text) <= MAX_SHOWN_CHARS:
        return text
    return text[:MAX_SHOWN_CHARS] + "\n\n… (cut on screen: the download has everything)"


def _action_buttons(
    entry: Entry, shown: str, markdown: str, as_json: str, workbook: bytes | None
) -> None:
    stem = stem_of(entry.doc)
    copy, md, js, xl, _ = st.columns([1, 1.3, 1.3, 1.6, 4])
    with copy.popover("Copy", icon=":material/content_copy:"):
        st.caption("Click the copy icon at the top right of the box.")
        st.code(_cut(shown), language=None, wrap_lines=True)
    md.download_button(
        "Markdown",
        markdown,
        f"{stem}.md",
        "text/markdown",
        key=f"dl_md_{entry.key}",
        icon=":material/download:",
    )
    js.download_button(
        "JSON",
        as_json,
        f"{stem}.json",
        "application/json",
        key=f"dl_json_{entry.key}",
        icon=":material/download:",
    )
    if workbook:
        xl.download_button(
            "Tables (.xlsx)",
            workbook,
            f"{stem}.tables.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_xlsx_{entry.key}",
            icon=":material/download:",
        )


def _tables_view(entry: Entry, tables: list[export.TableRef]) -> None:
    if not tables:
        st.info("No real tables in this document. (Scanned pages give text lines, not tables.)")
        return
    for number, ref in enumerate(tables):
        head, button = st.columns([5, 1], vertical_alignment="center")
        head.markdown(f"**{ref.file}** · {ref.label}")
        button.download_button(
            "CSV",
            export.table_to_csv(ref.table),
            f"{stem_of(entry.doc)}-p{ref.page}-t{ref.number}.csv",
            "text/csv",
            key=f"dl_csv_{entry.key}_{number}",
            icon=":material/download:",
        )
        html(widgets.table_html(ref.table.rows))


# ---------------------------------------------------------------------------- ROUTER LOG


def router_tab(entry: Entry, config: Config) -> None:
    doc = entry.doc
    with st.container(border=True, key=f"card_why_{entry.key}"):
        card_title("Why this engine was chosen")
        st.markdown(service.explain_routing(doc, config).replace("$", "\\$"))
    if not doc.decisions:
        st.info("This file has no router log (nothing was tried).")
        return
    with st.container(border=True, key=f"card_log_{entry.key}"):
        card_title("Decision log")
        legend = [
            widgets.chip(label, tone, icon) for label, tone, icon in widgets.OUTCOMES.values()
        ]
        html(f'<div class="dd-small">{"".join(legend)}</div>')
        html(widgets.decisions_html(doc.decisions))


# ---------------------------------------------------------------------------- EXTRACT


def extract_tab(entry: Entry, config: Config) -> None:
    doc = entry.doc
    if show_error(doc):
        return
    if not doc.pages:
        st.info("This file has no pages to extract from.")
        return
    with st.container(border=True, key="card_extract_controls"):
        card_title("Extract fields")
        pick, model, run = st.columns([2, 2, 1], vertical_alignment="bottom")
        label = pick.selectbox("Schema", list(SCHEMAS), key="schema") or "Invoice"
        model.text_input("Model", config.llm.model, disabled=True, help="Change it in the sidebar.")
        clicked = run.button("Run extraction", type="primary", key="run_extract")
    store = st.session_state.setdefault("extractions", {})
    key = (entry.key, SCHEMAS[label], config.llm.model)
    if clicked:
        with st.spinner(f"Asking {config.llm.model}… a small model on a CPU can take a minute."):
            store[key] = state.extraction(doc, SCHEMAS[label], config)
    if key in store:
        _show_extraction(doc, store[key], config, entry.key)


def _show_extraction(doc: Document, result, config: Config, entry_key: str) -> None:  # type: ignore[no-untyped-def]
    if result.error:
        st.error(f"Extraction failed.\n\n{result.error}", icon=":material/error:")
        if "Cannot reach" in result.error:
            st.caption(
                "Tip: start Ollama (`ollama serve`) and pull a model, e.g. `ollama pull llama3.2:3b`."
            )
        if not result.fields:
            return
    summary = [
        widgets.chip(result.schema_name, "blue"),
        widgets.chip(result.model, "grey"),
        widgets.chip(f"{result.seconds}s · {result.attempts} model call(s)", "grey"),
    ]
    html(f"<div>{''.join(summary)}</div>")
    _review_box(result)
    left, right = st.columns([5, 3], gap="medium")
    with left, st.container(border=True, key="card_fields"):
        card_title("Fields")
        with st.container(height=620, border=False):
            _field_rows(result, config, entry_key)
    with right, st.container(border=True, key="card_source"):
        card_title("Source text")
        _source_panel(doc, result)
    _issues_box(result)
    st.download_button(
        "Download JSON report",
        result.to_report_json(),
        f"{stem_of(doc)}.extract.json",
        "application/json",
        key="dl_report",
        icon=":material/download:",
    )


def _review_box(result) -> None:  # type: ignore[no-untyped-def]
    with st.container(border=True, key="card_review"):
        if result.needs_review:
            names = "".join(widgets.chip(n, "red", "!") for n in result.needs_review)
            html(
                f"<b>Needs review ({len(result.needs_review)})</b> — a person should check these.<br>{names}"
            )
        else:
            html(widgets.chip("Nothing needs review", "green", "✓"))
        st.caption(
            "Grounded means the value was found in the document text. It does not prove the "
            "value sits in the right field."
        )


def _field_rows(result, config: Config, entry_key: str) -> None:  # type: ignore[no-untyped-def]
    selected = st.session_state.get("sel_field")
    head = st.columns([3.2, 2.6, 1.2, 2.2, 0.7, 1.5])
    for col, text in zip(head, ("Field", "Value", "Conf.", "Grounded", "Page", ""), strict=True):
        col.markdown(f"<span class='dd-small'>{text}</span>", unsafe_allow_html=True)
    for number, item in enumerate(result.fields):
        cols = st.columns([3.2, 2.6, 1.2, 2.2, 0.7, 1.5], vertical_alignment="center")
        mark = "▸ " if item.path == selected else ""
        cols[0].markdown(
            f"<span class='dd-field-name'>{escape(mark + item.path)}</span>", unsafe_allow_html=True
        )
        cols[1].markdown(escape(_show_value(item.value)))
        cols[2].markdown(
            widgets.confidence_chip(item.confidence, config.review_threshold),
            unsafe_allow_html=True,
        )
        grounded = (
            widgets.chip("Grounded", "green", "✓")
            if item.grounded
            else widgets.chip("Not found", "red", "✕")
        )
        review = widgets.chip("Review", "amber", "!") if item.needs_review else ""
        cols[3].markdown(grounded + review, unsafe_allow_html=True)
        cols[4].markdown(str(item.source.page) if item.source else "–")
        cols[5].button(
            "Source",
            key=f"src_{entry_key}_{number}",
            on_click=st.session_state.__setitem__,
            args=("sel_field", item.path),
        )


def _show_value(value: object) -> str:
    return "—" if value is None or value == "" else str(value)


def _source_panel(doc: Document, result) -> None:  # type: ignore[no-untyped-def]
    item = result.field(st.session_state.get("sel_field", ""))
    if item is None:
        st.caption("Click **Source** on a field to see exactly where it came from.")
        return
    html(f"<b>{escape(item.path)}</b> = {escape(_show_value(item.value))}")
    if item.source is None:
        html(widgets.chip("Not found in the document text", "red", "✕"))
        st.caption("The model gave this value but it is not in the text. Treat it as unproven.")
        return
    html(
        widgets.chip(f"page {item.source.page}", "grey")
        + widgets.chip(f"match: {item.source.match}", "teal")
    )
    page = next((p for p in doc.pages if p.number == item.source.page), None)
    if page is not None:
        html(widgets.highlight_html(page.text, item.source.start, item.source.end))


def _issues_box(result) -> None:  # type: ignore[no-untyped-def]
    with st.container(border=True, key="card_issues"):
        card_title(f"Validation issues ({len(result.issues)})")
        if not result.issues:
            html(widgets.chip("All checks passed", "green", "✓"))
            return
        rows = []
        for issue in result.issues:
            tone = "red" if issue.severity == "error" else "amber"
            rows.append(
                f"<tr><td>{widgets.chip(issue.severity, tone)}</td><td><b>{escape(issue.path)}</b></td>"
                f"<td>{escape(issue.message)}</td><td class='dd-muted'>{escape(issue.rule)}</td></tr>"
            )
        html(
            '<table class="dd-table"><thead><tr><th>Level</th><th>Field</th><th>Problem</th>'
            f"<th>Rule</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )


# ---------------------------------------------------------------------------- COMPARE


def compare_tab(entry: Entry, config: Config, settings: UiSettings) -> None:
    doc = entry.doc
    if show_error(doc):
        return
    if entry.data is None:
        st.info(
            "Engine comparison needs the file itself. It works on uploads and on files directly "
            "inside a ZIP or email, not on deeper nesting."
        )
        return
    names = service.engines_enabled(
        service.comparable_engines(entry.data, doc.source, config), settings
    )
    with st.container(border=True, key="card_compare_controls"):
        card_title("Compare engines")
        st.write(
            "Runs the same file through each engine on its own (no fallback) and puts the results "
            "side by side. Switched-off engines in the sidebar are left out."
        )
        label = ", ".join(names) or "none"
        st.caption(f"Engines to run: {label}")
        clicked = st.button("Run comparison", type="primary", key="run_compare", disabled=not names)
    store = st.session_state.setdefault("compare", {})
    key = (entry.key, service.config_to_json(config), tuple(names))
    if clicked:
        store[key] = _run_comparison(entry, config, names)
    if key in store:
        _comparison_results(store[key], service.chosen_engines(doc))


def _run_comparison(entry: Entry, config: Config, names: list[str]) -> list[EngineRun]:
    runs: list[EngineRun] = []
    bar = st.progress(0.0, text="Starting…")
    for number, name in enumerate(names):
        bar.progress(
            number / len(names),
            text=f"Running {name} ({number + 1} of {len(names)})… slow engines load a model first.",
        )
        runs.append(state.engine_run(entry.data or b"", entry.doc.source, config, name))
    bar.empty()
    return runs


def _status_chip(run: EngineRun) -> str:
    tone = {"ok": "green", "empty": "amber", "failed": "red", "timeout": "red", "skipped": "grey"}
    return widgets.chip(run.status, tone.get(run.status, "grey"))


def _comparison_results(runs: list[EngineRun], router_choice: set[str]) -> None:
    ok = [r for r in runs if r.status == "ok"]
    best = max(ok, key=lambda r: r.quality or 0, default=None)
    fastest = min(ok, key=lambda r: r.seconds, default=None)
    rows = []
    for run in runs:
        marks = ""
        if run.engine in router_choice:
            marks += widgets.chip("router's choice", "teal", "★")
        if best is not None and run is best and len(ok) > 1:
            marks += widgets.chip("best quality", "green")
        if fastest is not None and run is fastest and len(ok) > 1:
            marks += widgets.chip("fastest", "blue")
        quality = "" if run.quality is None else f"{run.quality:.2f}"
        rows.append(
            f"<tr><td><b>{escape(run.engine)}</b></td><td>{_status_chip(run)}</td><td>{run.seconds:.2f}</td>"
            f"<td>{quality}</td><td>{run.chars}</td><td>{len(run.pages)}</td><td>{marks}</td>"
            f"<td class='dd-reason'>{escape(run.reason)}</td></tr>"
        )
    with st.container(border=True, key="card_compare_table"):
        card_title("Results")
        html(
            '<table class="dd-table"><thead><tr><th>Engine</th><th>Status</th><th>Seconds</th>'
            "<th>Text quality</th><th>Characters</th><th>Pages</th><th>Notes</th><th>Reason</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    for start in range(0, len(ok), 3):
        columns = st.columns(3, gap="medium")
        for column, run in zip(columns, ok[start : start + 3], strict=False):
            with column, st.container(border=True, key=f"card_engine_{run.engine}"):
                card_title(run.engine)
                html(
                    widgets.chip(f"{run.seconds:.2f}s", "grey")
                    + widgets.chip(f"quality {run.quality:.2f}", "teal")
                )
                html(widgets.pages_html(run.pages))
