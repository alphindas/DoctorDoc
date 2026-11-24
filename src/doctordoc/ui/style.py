"""The look of the UI: a small CSS block on top of .streamlit/config.toml, and the top bar.

Colors come from docs/design/mockup.png. Cards are `st.container(border=True, key="card_...")`;
the key gives each one a CSS class, so we never depend on Streamlit's internal markup much.
"""

from __future__ import annotations

import streamlit as st

TEAL = "#03959d"
NAVY = "#0b123c"

CSS = f"""
<style>
/* page */
.stApp {{ background: linear-gradient(135deg, #f1fafd 0%, #e3eefa 100%); color: {NAVY}; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1500px; }}
h1, h2, h3, h4 {{ color: {NAVY}; }}

/* top bar */
.dd-topbar {{
  display: flex; align-items: center; gap: 14px; background: #fff; border-radius: 18px;
  padding: 12px 22px; box-shadow: 0 4px 18px rgba(30, 70, 120, .08); margin-bottom: 14px;
}}
.dd-brand {{ font-size: 1.7rem; font-weight: 700; color: {NAVY}; letter-spacing: -.5px; }}
.dd-brand span {{ color: {TEAL}; }}
.dd-divider {{ width: 1px; height: 26px; background: #cfd8e6; margin: 0 6px; }}
.dd-tagline {{ color: #3b4668; font-size: 1.02rem; }}

/* sidebar */
section[data-testid="stSidebar"] {{ background: #f7f9fd; border-right: 1px solid #e3e9f3; }}
[data-testid="stFileUploaderDropzone"] {{
  border: 2px dashed #8fd6dc; background: #eaf9fc; border-radius: 16px; padding: 1.4rem 1rem;
  flex-direction: column; gap: .6rem; justify-content: center; align-items: center; min-height: 170px;
}}
[data-testid="stFileUploaderDropzone"]::before {{
  content: "☁ Drop any file here"; font-weight: 700; font-size: 1.1rem; color: {NAVY}; order: -1;
}}
[data-testid="stFileUploaderDropzoneInstructions"] {{ display: none; }}
section[data-testid="stSidebar"] [data-testid="stButton"] {{ width: 100%; }}
section[data-testid="stSidebar"] [data-testid="stButton"] button {{ width: 100%; }}
section[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {{
  width: 100%; border-radius: 14px; padding: .65rem 1rem; font-weight: 600;
  box-shadow: 0 6px 14px rgba(3, 149, 157, .28);
}}

/* tabs as pills */
[role="tablist"] {{
  background: #f0f5fb; border-radius: 16px; padding: 6px; gap: 6px; width: fit-content;
  box-shadow: inset 0 0 0 1px #e1e9f4; border-bottom: 0 !important;
}}
[role="tablist"] > *:not([data-testid="stTab"]) {{ display: none; }}
[data-testid="stTab"] {{ border-radius: 12px; padding: .55rem 1.3rem; height: auto; }}
[data-testid="stTab"][aria-selected="true"] {{ background: {TEAL}; box-shadow: 0 4px 10px rgba(3,149,157,.3); }}
[data-testid="stTab"][aria-selected="true"] p, [data-testid="stTab"][aria-selected="true"] span {{ color: #fff !important; font-weight: 600; }}
[data-testid="stTabs"] > div > div:first-child {{ border-bottom: 0 !important; }}

/* cards */
[class*="st-key-card"] {{
  background: #fff; border: 1px solid #e6edf6; border-radius: 18px; padding: 1rem 1.2rem;
  box-shadow: 0 4px 16px rgba(30, 70, 120, .07);
}}
.dd-card-title {{ font-weight: 700; font-size: 1.1rem; color: {TEAL}; border-bottom: 2px solid #b3dee0;
  display: inline-block; padding-bottom: 2px; margin-bottom: .6rem; }}

/* chips */
.dd-chip {{ display: inline-flex; align-items: center; gap: 6px; padding: 3px 12px;
  border-radius: 999px; font-weight: 600; font-size: .85rem; white-space: nowrap; margin-right: 6px; }}
.dd-chip-icon {{ font-size: .8rem; }}
.dd-green {{ background: #cef5e2; color: #12704f; }}
.dd-amber {{ background: #fee9c4; color: #8a5a00; }}
.dd-red {{ background: #fde0e0; color: #b42318; }}
.dd-grey {{ background: #eceff4; color: #4e5a70; }}
.dd-blue {{ background: #e1ecff; color: #2468e8; }}
.dd-teal {{ background: #d9f8f3; color: #0b7f78; }}
.dd-purple {{ background: #ece6ff; color: #5b3fd0; }}
.dd-orange {{ background: #fee9da; color: #b8500a; }}

/* tables and extracted text */
.dd-scroll {{ max-height: 640px; overflow: auto; padding-right: 6px; line-height: 1.5; }}
.dd-scroll p {{ margin: 0 0 .55rem 0; }}
.dd-scroll h3, .dd-scroll h4, .dd-scroll h5, .dd-scroll h6 {{ margin: .6rem 0 .3rem 0; }}
.dd-table {{ border-collapse: separate; border-spacing: 0; width: 100%; margin: .4rem 0 .8rem 0;
  border: 1px solid #dbe6f1; border-radius: 12px; overflow: hidden; font-size: .93rem; }}
.dd-table th {{ background: #e3f4f7; text-align: left; padding: 8px 12px; border-bottom: 1px solid #cfe3ec; }}
.dd-table td {{ padding: 8px 12px; border-bottom: 1px solid #edf2f8; vertical-align: top; }}
.dd-table tr:last-child td {{ border-bottom: 0; }}
.dd-reason {{ color: #3b4668; }}
.dd-muted {{ color: #7a86a1; }}
.dd-source {{ background: #f7fafd; border: 1px solid #e1e9f4; border-radius: 12px; padding: 12px 14px;
  white-space: pre-wrap; line-height: 1.55; }}
.dd-source mark {{ background: #ffe58a; padding: 1px 3px; border-radius: 4px; }}
.dd-field-name {{ font-weight: 600; overflow-wrap: anywhere; }}
.dd-small {{ font-size: .85rem; color: #5b667a; }}
</style>
"""

LOGO = f"""
<svg width="40" height="44" viewBox="0 0 40 44" fill="none" xmlns="http://www.w3.org/2000/svg"
     role="img" aria-label="DoctorDoc logo">
  <path d="M7 3h17l9 9v18a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3z"
        stroke="{TEAL}" stroke-width="2.4" fill="#fff"/>
  <path d="M24 3v9h9" stroke="{TEAL}" stroke-width="2.4" fill="none"/>
  <path d="M11 17h10M11 22h14" stroke="{TEAL}" stroke-width="2.4" stroke-linecap="round"/>
  <path d="M14 33v3a7 7 0 0 0 14 0v-5" stroke="{NAVY}" stroke-width="2.4" stroke-linecap="round" fill="none"/>
  <circle cx="28" cy="29" r="2.6" stroke="{NAVY}" stroke-width="2.2" fill="#fff"/>
</svg>
"""


def inject() -> None:
    """Add the stylesheet to the page. Call once, near the top of the script."""
    st.markdown(CSS, unsafe_allow_html=True)


def top_bar() -> None:
    st.markdown(
        f'<div class="dd-topbar">{LOGO}<div class="dd-brand">Doctor<span>Doc</span></div>'
        '<div class="dd-divider"></div>'
        '<div class="dd-tagline">One door for any document</div></div>',
        unsafe_allow_html=True,
    )


def card_title(text: str) -> None:
    st.markdown(f'<div class="dd-card-title">{text}</div>', unsafe_allow_html=True)
