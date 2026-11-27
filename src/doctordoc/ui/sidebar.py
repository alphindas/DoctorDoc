"""The sidebar: upload box, engine and privacy settings, engine list, Parse button."""

from __future__ import annotations

import streamlit as st

from doctordoc import service
from doctordoc.config import Config
from doctordoc.service import OCR_ENGINES, UiSettings
from doctordoc.ui import state
from doctordoc.ui.state import SidebarState

AUTO = "Auto (router decides)"


def render(base: Config) -> SidebarState:
    with st.sidebar:
        uploads = _upload_box(base)
        examples = _examples()
        local_only = st.session_state.get("local_only", True)
        report = state.engine_report(
            service.config_to_json(service.apply_settings(base, UiSettings(local_only=local_only)))
        )
        forced = _ocr_selector(report, local_only)
        local_only = _local_only_toggle(base)
        model = _model_selector(base)
        merge = st.toggle(
            "Merge mixed pages",
            value=base.merge_mixed_pages,
            key="merge_mixed",
            help="On a page with text AND pictures: also keep text that only the other engine found.",
        )
        disabled = _engine_list(report, local_only)
        clicked = st.button(
            "Parse document", type="primary", key="parse", icon=":material/play_arrow:"
        )

    files = [(f.name, f.getvalue()) for f in uploads]
    available = state.example_files()
    files += [(name, available[name].read_bytes()) for name in examples if name in available]
    settings = UiSettings(
        disabled_engines=disabled,
        forced_ocr=forced,
        local_only=local_only,
        merge_mixed_pages=merge,
        llm_model=model,
    )
    return SidebarState(files=files, settings=settings, parse_clicked=clicked)


def _upload_box(base: Config):
    uploads = st.file_uploader(
        "Upload documents",
        accept_multiple_files=True,
        key="uploads",
        label_visibility="collapsed",
        help=f"Any file type. {service.storage_note(base)}",
    )
    st.caption(
        f"Drop any file here: PDF, Word, Excel, scans, emails, ZIP. Up to {base.max_file_mb:g} MB each."
    )
    return uploads or []


def _examples() -> list[str]:
    available = state.example_files()
    if not available:
        return []
    return st.multiselect("…or try the examples", list(available), key="examples")


def _ocr_selector(report: list[dict[str, str]], local_only: bool) -> str | None:
    installed = [
        r["name"]
        for r in report
        if r["name"] in OCR_ENGINES
        and r["status"] == "ready"
        and not (local_only and r["name"] in service.CLOUD_ENGINES)
    ]
    choice = st.selectbox(
        "OCR engine",
        [AUTO, *installed],
        key="ocr_engine",
        help="Auto: the router tries the OCR engines in order. Or force one engine for scanned pages.",
    )
    return None if choice == AUTO else choice


def _local_only_toggle(base: Config) -> bool:
    local_only = st.toggle(
        "Local only (no cloud OCR)", value=True, key="local_only", help=service.CLOUD_PRIVACY_NOTE
    )
    if not local_only:
        st.warning(service.CLOUD_PRIVACY_NOTE, icon=":material/cloud_upload:")
        st.caption(f"Cloud server: {base.cloud.base_url}")
    llm_note = service.llm_privacy_note(base)
    if llm_note:  # shown with the switch on or off
        st.warning(llm_note, icon=":material/dns:")
    return local_only


def _model_selector(base: Config) -> str | None:
    llm = base.llm
    names, problem = state.local_models(llm.base_url, llm.provider, llm.api_key)
    if names:
        default = names.index(llm.model) if llm.model in names else 0
        return st.selectbox(
            "Model (for Extract)",
            names,
            index=default,
            key="model",
            help=f"Models found on the model server at {llm.base_url}.",
        )
    st.selectbox("Model (for Extract)", [llm.model], key="model", disabled=True)
    st.caption(
        f"No model server found at {llm.base_url}. Start your model server (Ollama by default) "
        "to use Extract."
    )
    return llm.model


def _engine_list(report: list[dict[str, str]], local_only: bool) -> set[str]:
    """Show every engine with an on/off switch. Returns the names switched off."""
    ready = sum(r["status"] == "ready" for r in report)
    off: set[str] = set()
    with st.expander(f"Engines: {ready} of {len(report)} installed"):
        for row in report:
            name, is_ready = row["name"], row["status"] == "ready"
            on = st.toggle(name, value=is_ready, disabled=not is_ready, key=f"engine_{name}")
            if is_ready and not on:
                off.add(name)
            if is_ready:
                note = f" · {row['note']}" if row["note"] else ""
                st.caption(f"installed · {row['license'].split(' (')[0]}{note}")
            elif name in service.CLOUD_ENGINES and local_only:
                st.caption("off: Local only is on")
            else:
                hint = row["install"] or row["status"]
                st.caption(f"not installed · {hint}")
    return off
