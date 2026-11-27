"""DoctorDoc UI. Run it with:  doctordoc ui    (or: streamlit run src/doctordoc/ui/app.py)

This file only wires things together. The logic is in doctordoc.service / doctordoc.export,
the drawing is in views.py, the look is in style.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from doctordoc import service
from doctordoc.config import Config
from doctordoc.ui import sidebar, state, style, views

CONFIG_ENV = "DOCTORDOC_UI_CONFIG"  # `doctordoc ui --config file.yaml` sets this


def load_base_config() -> Config:
    path = os.environ.get(CONFIG_ENV)
    return Config.from_yaml(Path(path)) if path else Config()


def run_parse(side: state.SidebarState, config: Config) -> None:
    """Parse what the user picked, with a progress bar. Results go to session_state."""
    if not side.files:
        st.sidebar.warning("Add a file (or pick an example) first.")
        return
    bar = st.progress(0.0, text="Starting…")

    def on_progress(done: int, total: int, name: str) -> None:
        text = (
            f"Parsing {name} ({done + 1} of {total})… the first file loads the models."
            if name
            else "Done"
        )
        bar.progress(min(done / max(total, 1), 1.0), text=text)

    try:
        docs = state.parse_files(side.files, config, on_progress)
        st.session_state["parsed"] = state.make_parsed(side.files, docs, config)
        st.session_state["parsed_config"] = service.config_to_json(config)
    except Exception as err:  # the library never raises, but the page must not die either
        st.error(f"Something went wrong while parsing: {type(err).__name__}: {err}")
    finally:
        bar.empty()
    for key in ("selected_file", "sel_field", "extractions", "compare"):
        st.session_state.pop(key, None)
    for key in [k for k in st.session_state if str(k).startswith("page_")]:
        del st.session_state[key]


def main() -> None:
    st.set_page_config(page_title="DoctorDoc", page_icon="🩺", layout="wide")
    style.inject()
    style.top_bar()
    base = load_base_config()
    side = sidebar.render(base)
    config = service.apply_settings(base, side.settings)
    if side.parse_clicked:
        run_parse(side, config)

    parsed: list[state.ParsedFile] = st.session_state.get("parsed", [])
    entry = None
    if parsed:
        entry = views.file_picker(state.entries_of(parsed))
        views.stale_note(st.session_state.get("parsed_config"), config)

    # key + on_change="rerun": the chosen tab survives a rerun, and only the open tab runs.
    tabs = st.tabs(
        ["Parse", "Router log", "Extract", "Compare engines"], key="main_tabs", on_change="rerun"
    )
    if entry is None:
        for number, tab in enumerate(tabs):
            with tab:
                views.empty_state(str(number), service.storage_note(config))
        return
    with tabs[0]:
        if tabs[0].open:
            views.parse_tab(entry, parsed)
    with tabs[1]:
        if tabs[1].open:
            views.router_tab(entry, config)
    with tabs[2]:
        if tabs[2].open:
            views.extract_tab(entry, config)
    with tabs[3]:
        if tabs[3].open:
            views.compare_tab(entry, config, side.settings)


main()
