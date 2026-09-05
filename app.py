"""
FundaCheck — an AI-assisted fundamental analysis dashboard.

Upload a 3-statement Excel model, pick the sector, and the terminal turns it
into an interactive dashboard plus a STRONG / NEUTRAL / WEAK verdict that is
judged against sector-specific benchmarks rather than one universal rule book.

Run it with:   streamlit run app.py
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from core import charts as C
from core import design_blocks as D
from core import report as REP
from core import sections as S
from core import sector_snapshot as SNAP
from core import shell as SH
from core import viz
from core.llm import LLMConfig, analyse, answer_question, config_from_env
from core.derive import fill_missing_common_size, fill_missing_ratios
from core.interpret import SECTIONS as INTERP_SECTIONS
from core.interpret import fingerprint as interp_fingerprint
from core.interpret import interpret as build_interpretation
from core.parser import ParseError, load_model
from core.scoring import assess, compare_sectors
from core.sectors import (
    PERCENT_METRICS,
    SECTORS,
    detect_sector,
    get_sector,
    sector_choices,
)

# Ratios stored as a decimal that read better as a percentage than as "0.02".
EXTRA_PERCENT_METRICS = {"CFO / Sales", "CFO / Total Assets", "CFO / Total Debt",
                         "Dividend Payout %", "Retained Earnings%"}

# Figures reported in crore in an Indian 3-statement model.
CURRENCY_METRICS = {
    "Sales", "Net Profit", "EBITDA", "EBIT (OPM)", "Gross Margin",
    "Total Asset", "Total Liabilities", "Borrowings", "Reserves",
    "Cash from Operating Activity", "Cash from Investing Activity",
    "Cash from Financing Activity", "Net Cash Flow", "Market Capitalization",
}

LOGGER = logging.getLogger("fundacheck")

APP_DIR = Path(__file__).parent
SAMPLE = APP_DIR / "sample_data" / "3S_model_sample.xlsx"

# Bump this on every deploy-worth change so the sidebar can show which build is
# live — the quickest way to tell a fresh deploy from a stale cached view.
BUILD_TAG = "2026-08-26 r19 (bs hover, sidebar spacing, trim padding)"

# (key, label, material-icon) — outline Material Symbols matching the reference
# sidebar mockup. Passed to st.button(icon=":material/<name>:") so the glyph reads
# as a clean line icon on the dark rail; collapsed mode shows only the icon.
NAV_PAGES = [
    ("overview", "Dashboard", "grid_view"),
    ("ratios", "Ratio deep dive", "bar_chart"),
    ("lens", "Sector lens", "pie_chart"),
    ("statements", "Statements", "description"),
    ("qa", "Ask the analyst", "chat_bubble"),
]

st.set_page_config(
    page_title="FundaCheck · Fundamental Analysis",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# --------------------------------------------------------------------------
# small UI helpers
# --------------------------------------------------------------------------
def inject_css(mode: str = "dark", minimized: bool = False) -> None:
    """
    Load the stylesheet, plus the light overrides when day mode is on, plus
    the narrow-sidebar rules when the Minimize toggle is engaged.
    """
    css = (APP_DIR / "assets" / "style.css").read_text()
    if mode == "light":
        css += "\n" + (APP_DIR / "assets" / "light.css").read_text()
    # The expanded sidebar is always the dark panel (design spec), even when the
    # content area is in day mode — so this layer lands after light.css to win.
    if not minimized:
        css += "\n" + SIDEBAR_DARK
    if minimized:
        css += "\n" + MIN_CSS
    # Force the main chrome to the light page colour with !important so a viewer
    # whose browser/OS is in dark mode cannot leave a dark band around the
    # content cards. The dark sidebar keeps its own (more specific) background.
    css += ("\n:root{color-scheme:light!important}"
            "\n[data-testid=\"stApp\"],[data-testid=\"stAppViewContainer\"],"
            "[data-testid=\"stHeader\"],[data-testid=\"stMain\"]"
            "{background:#e7ebe7!important}"
            # Streamlit's tall top/bottom padding and 100vh minimum leave grey
            # space around the content; trim them so the page hugs the cards.
            "\n[data-testid=\"stMainBlockContainer\"]"
            "{padding-top:0.5rem!important;padding-bottom:0.6rem!important}"
            "\n[data-testid=\"stMain\"]{min-height:auto!important}"
            # Streamlit reserves the component's *estimated* height as a plain
            # (non-!important) inline style on the element container, which leaves
            # a tall empty band once the resizer shrinks the iframe to its content.
            # A stylesheet !important beats React's inline style, so the container
            # hugs the fitted iframe and both the trailing gap and the extra
            # scroll disappear. Covers the 0-height night/resizer helpers too.
            "\n[data-testid=\"stMain\"] [data-testid=\"stElementContainer\"]"
            ":has(> iframe.stIFrame){height:auto!important;min-height:0!important}")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


# The dark expanded rail (reference sidebar mockup): a rounded-square brand mark,
# a "Data source" card, outline Material-icon nav with a green active state, a
# "Collapse sidebar" control, and the night/day toggle at the foot.
SIDEBAR_DARK = """
section[data-testid="stSidebar"]{
  background:linear-gradient(180deg,#0d1d16 0%,#0a1610 100%)!important;
  border-right:1px solid rgba(255,255,255,.06)!important}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.6rem!important}
section[data-testid="stSidebar"]{padding-top:.4rem!important}

/* brand: rounded-square mark + FUNDAMENTAL WORKSPACE */
.side-brand{display:flex;align-items:center;gap:12px;padding:.4rem 2px .7rem}
.side-mark{width:40px!important;height:40px!important;border-radius:12px!important;
  display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#37d67a,#1faa5e)!important;color:#06120c!important;
  font-weight:800!important;font-size:18px!important}
.side-brand .name{color:#f2f7f4!important;font-size:18px!important;font-weight:800!important}
.side-brand .tag{color:#8a948f!important;font-size:9.5px!important;letter-spacing:1.6px!important}

/* collapse control */
section[data-testid="stSidebar"] [class*="st-key-side-min"] button{
  background:transparent!important;border:none!important;color:#aebab3!important;
  justify-content:flex-start!important;font-size:13px!important;font-weight:600!important;
  padding:.35rem .5rem!important}
section[data-testid="stSidebar"] [class*="st-key-side-min"] button *{color:#aebab3!important}
section[data-testid="stSidebar"] [class*="st-key-side-min"] button:hover{
  background:rgba(255,255,255,.04)!important}

/* ---- Data source card (keyed container) ---- */
section[data-testid="stSidebar"] [class*="st-key-ds-card"]{
  background:rgba(255,255,255,.03)!important;border:1px solid rgba(255,255,255,.09)!important;
  border-radius:16px!important;padding:16px 16px 14px!important;margin-bottom:6px!important}
.ds-title{color:#eaf3ee;font-size:15px;font-weight:800;padding-bottom:12px}
/* Streamlit's theme forces markdown spans to dark ink, which is invisible on the
   dark rail; pin the filename text light with !important. */
.ds-file{display:flex;align-items:flex-start;gap:9px;font-size:13px;
  line-height:1.45;font-weight:600}
.ds-file,.ds-file span{color:#c8d1cb!important}
.ds-file .ds-ic{color:#8a948f!important;font-size:15px;flex:none}
.ds-status{display:flex;align-items:center;gap:8px;padding:10px 0 14px}
.ds-dot{width:8px;height:8px;border-radius:50%;background:#4a5551;flex:none}
.ds-dot.ok{background:#37d67a;box-shadow:0 0 0 3px rgba(55,214,122,.18)}
.ds-ready{color:#37d67a;font-size:12.5px;font-weight:700}
.ds-ready.muted{color:#8a948f}

/* upload dropzone -> a single green "Upload new file" button */
section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"]{
  border:none!important;border-radius:12px!important;padding:0!important;
  background:transparent!important;min-height:0!important}
section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"]{
  display:none!important}
section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button{
  width:100%!important;background:linear-gradient(135deg,#2a9c62,#177245)!important;
  border:none!important;border-radius:12px!important;padding:11px 18px!important;
  font-weight:700!important;display:flex!important;justify-content:center!important;
  align-items:center!important}
/* Hide Streamlit's native icon + "Browse files" label entirely (the icon keeps
   width even at font-size:0, which is what pushed our text off-centre); draw our
   own full-width, centred label with ::after. */
section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button>*{
  display:none!important}
section[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button::after{
  content:"\\2191  Upload new file";font-size:14px!important;color:#fff!important;
  width:100%!important;text-align:center!important}
/* drop the little uploaded-file preview strip Streamlit shows under the button */
section[data-testid="stSidebar"] [data-testid="stFileUploaderFile"]{display:none!important}

/* "Change file" / "Load demo model" -> outline button */
section[data-testid="stSidebar"] [class*="st-key-src-action"] button{
  width:100%!important;background:transparent!important;
  border:1px solid rgba(255,255,255,.16)!important;border-radius:12px!important;
  color:#dde4df!important;font-size:13.5px!important;font-weight:600!important;
  padding:.55rem .8rem!important;margin-top:8px!important;justify-content:center!important;
  text-align:center!important}
section[data-testid="stSidebar"] [class*="st-key-src-action"] button *{
  color:#dde4df!important;text-align:center!important}
/* Streamlit wraps the label in a flex container pinned left; force every layer
   (the wrapper div, the markdown container and the <p>) to fill and centre. */
section[data-testid="stSidebar"] [class*="st-key-src-action"] button>div,
section[data-testid="stSidebar"] [class*="st-key-src-action"] button [data-testid="stMarkdownContainer"]{
  justify-content:center!important;width:100%!important;text-align:center!important}
section[data-testid="stSidebar"] [class*="st-key-src-action"] button p{
  width:100%!important;text-align:center!important;margin:0 auto!important}
section[data-testid="stSidebar"] [class*="st-key-src-action"] button:hover{
  background:rgba(255,255,255,.05)!important;border-color:rgba(255,255,255,.24)!important}

/* ---- nav rows (outline Material icon + label) ---- */
section[data-testid="stSidebar"] [class*="st-key-nav-"] button{
  background:transparent!important;border:none!important;
  color:#c3ccc6!important;justify-content:flex-start!important;text-align:left!important;
  font-size:14px!important;font-weight:600!important;padding:.7rem .85rem!important;
  border-radius:12px!important;gap:13px!important;
  transition:background .16s ease,transform .16s ease!important}
section[data-testid="stSidebar"] [class*="st-key-nav-"] button *{color:#c3ccc6!important}
section[data-testid="stSidebar"] [class*="st-key-nav-"] button [data-testid="stIconMaterial"]{
  font-size:20px!important;color:#9aa8a1!important}
section[data-testid="stSidebar"] [class*="st-key-nav-"] button:hover{
  background:rgba(255,255,255,.06)!important;transform:translateX(2px)!important}
section[data-testid="stSidebar"] [class*="st-key-nav-"] button[kind="primary"]{
  background:rgba(31,170,94,.16)!important;font-weight:700!important;
  box-shadow:inset 3px 0 0 #37d67a!important}
section[data-testid="stSidebar"] [class*="st-key-nav-"] button[kind="primary"],
section[data-testid="stSidebar"] [class*="st-key-nav-"] button[kind="primary"] *{
  color:#7fe3a6!important}
section[data-testid="stSidebar"] [class*="st-key-nav-"] button[kind="primary"] [data-testid="stIconMaterial"]{
  color:#37d67a!important}

/* night-mode toggle (HTML button, wired client-side for a smooth invert) */
.fc-dntoggle{display:flex;align-items:center;gap:10px;width:100%;
  background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.10);
  border-radius:12px;padding:.55rem .8rem;color:#dde4df;font-family:inherit;
  font-size:14px;font-weight:600;cursor:pointer;margin:6px 0 2px}
.fc-dntoggle:hover{background:rgba(255,255,255,.08)}
.fc-dntoggle .dnic{font-size:16px;width:20px;text-align:center}
.fc-dntoggle,.fc-dntoggle *{color:#dde4df!important}
"""


MIN_CSS = """
/* ---- collapsed: a narrow DARK icon rail (reference mockup) ---- */
section[data-testid="stSidebar"]{
  min-width:88px!important;max-width:88px!important;padding-top:.6rem!important}
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.5rem!important}

/* hide everything but the expand control and the icon nav */
.side-brand .txtwrap,
section[data-testid="stSidebar"] [class*="st-key-ds-card"],
section[data-testid="stSidebar"] .dhd,
section[data-testid="stSidebar"] .fc-dntoggle{display:none!important}
.side-brand{justify-content:center;padding:.2rem 0 .4rem}

/* expand control: a rounded outlined tile */
section[data-testid="stSidebar"] [class*="st-key-side-min"] button{
  width:44px!important;height:44px!important;min-height:44px!important;
  margin:.1rem auto!important;padding:0!important;justify-content:center!important;
  border:1px solid rgba(255,255,255,.12)!important;border-radius:12px!important}

/* nav: icon-only tiles; active = green tint + left rail */
section[data-testid="stSidebar"] [class*="st-key-nav-"] button{
  width:48px!important;height:48px!important;min-height:48px!important;
  padding:0!important;margin:.28rem auto!important;justify-content:center!important;
  border-radius:14px!important;gap:0!important;transform:none!important}
section[data-testid="stSidebar"] [class*="st-key-nav-"] button [data-testid="stMarkdownContainer"]{
  display:none!important}
"""


# The night-mode control from the reference design. The button is plain HTML in
# the sidebar; a 0-height component iframe (same-origin) wires its click to a
# smooth CSS invert of the main content — no Streamlit rerun, so it animates.
NIGHT_TOGGLE_HTML = (
    '<button class="fc-dntoggle" type="button">'
    '<span class="dnic">☾</span><span class="dnlb">Night mode</span></button>'
)

NIGHT_JS = """
<script>
(function(){
  var doc; try{ doc=window.parent.document; }catch(e){ return; }
  if(!doc.getElementById('fc-night-style')){
    var s=doc.createElement('style'); s.id='fc-night-style';
    s.textContent='[data-testid=\\"stMain\\"]{transition:filter .35s ease}'+
      'html.fcnight [data-testid=\\"stMain\\"]{filter:invert(1) hue-rotate(180deg)}';
    doc.head.appendChild(s);
  }
  function upd(on){ var b=doc.querySelector('.fc-dntoggle'); if(!b)return;
    var i=b.querySelector('.dnic'), l=b.querySelector('.dnlb');
    if(i)i.textContent=on?'\\u2600':'\\u263e';
    if(l)l.textContent=on?'Day mode':'Night mode'; }
  function setNight(on){ doc.documentElement.classList.toggle('fcnight',on);
    try{localStorage.setItem('fc_night',on?'1':'0');}catch(e){} upd(on); }
  var btn=doc.querySelector('.fc-dntoggle');
  if(btn && !btn._fcw){ btn._fcw=1; btn.addEventListener('click',function(){
    setNight(!doc.documentElement.classList.contains('fcnight')); }); }
  var saved=false; try{ saved=localStorage.getItem('fc_night')==='1'; }catch(e){}
  setNight(saved);
})();
</script>
"""


# Parent-side frame fit. The in-frame fit relies on window.frameElement, which
# can be blocked in some deployed setups; this helper runs in the app document
# (via window.parent, the same mechanism the night toggle uses successfully) and
# resizes every content frame to its #shell height, collapsing the Streamlit
# wrappers so the page ends exactly at the content — no empty band, no scrollbar.
RESIZER_JS = """
<script>
(function(){
  var doc; try{ doc=window.parent.document; }catch(e){ return; }
  function fitAll(){
    try{
      var frames=doc.querySelectorAll('iframe');
      frames.forEach(function(fr){
        var d; try{ d=fr.contentDocument; }catch(e){ return; }
        if(!d) return;
        var sh=d.getElementById('shell'); if(!sh) return;
        // +buffer covers the body's own top+bottom padding so the frame shows the
        // full shell (incl. its rounded bottom corners) with no inner scroll.
        var h=Math.ceil(sh.getBoundingClientRect().height)+28;
        if(h<120) return;
        if(Math.abs((parseInt(fr.style.height)||0)-h)>1){
          fr.style.setProperty('height',h+'px','important'); fr.setAttribute('height',h); }
        var el=fr.parentElement;
        for(var i=0;i<6 && el;i++){
          var t=el.getAttribute && el.getAttribute('data-testid');
          // Streamlit reserves the *initial estimated* height on the element
          // container with !important, so plain height:auto cannot shrink it and
          // a tall empty band trails the frame. Pin the container to the frame's
          // real height (also !important) and let the vertical blocks be auto.
          if(t==='stElementContainer'){
            el.style.setProperty('height',h+'px','important');
            el.style.setProperty('min-height','0px','important'); }
          else if(t==='stVerticalBlock'||t==='stVerticalBlockBorderWrapper'){
            el.style.setProperty('height','auto','important');
            el.style.setProperty('min-height','0px','important'); }
          if(t==='stMain'||t==='stAppViewContainer') break;
          el=el.parentElement; }
      });
    }catch(e){}
  }
  fitAll();
  for(var k=1;k<=12;k++) setTimeout(fitAll, k*250);
  setInterval(fitAll, 700);
  try{ var vv=(doc.defaultView||window).visualViewport;
    if(vv){ vv.addEventListener('resize',fitAll); vv.addEventListener('scroll',fitAll); } }catch(e){}
  window.addEventListener('resize',fitAll);
})();
</script>
"""


@contextmanager
def card(title: str):
    """
    A titled panel.

    Streamlit widgets cannot be written inside a raw HTML <div>, so the panel is
    a real bordered container and the styling is applied from style.css.
    """
    with st.container(border=True):
        st.markdown(f'<div class="card-title">{title}</div>', unsafe_allow_html=True)
        yield


def vcomp(html: str, height: int) -> None:
    """Render one design-exact HTML/SVG block (iframe => hover tooltips work)."""
    components.html(viz.doc(html), height=height, scrolling=False)


def analyst_config() -> LLMConfig:
    """
    Build the analyst connection from Streamlit secrets or the environment.

    Nothing about this is user-facing: the app either has keys or it does not,
    and falls back to the deterministic note when it does not.
    """
    try:
        secrets = dict(st.secrets)
    except Exception:                       # noqa: BLE001 - no secrets file present
        secrets = {}
    # Groq is primary; Gemini is the automatic fallback if Groq fails/rate-limits.
    # If only one is configured, that one is used; if neither, the offline note.
    groq = config_from_env("groq", secrets=secrets)
    gemini = config_from_env("gemini", secrets=secrets)
    live = [c for c in (groq, gemini) if c.is_live]
    if not live:
        return groq                         # offline path unchanged
    primary, fallbacks = live[0], live[1:]
    primary.fallbacks = fallbacks
    return primary


def step(number: int, label: str) -> None:
    st.markdown(
        f'<div class="step"><span class="n">{number}</span>{label}'
        f'<span class="rule"></span></div>',
        unsafe_allow_html=True,
    )


def sidebar() -> tuple[object, str, str]:
    collapsed = bool(st.session_state.get("nav_min", False))
    # The uploader is reset by bumping this nonce into its widget key: "Change
    # file" increments it so Streamlit forgets the previously chosen file.
    nonce = st.session_state.setdefault("upload_nonce", 0)
    st.session_state.setdefault("dark_mode", False)

    with st.sidebar:
        st.markdown(
            '<div class="side-brand"><div class="side-mark">F</div>'
            '<div class="txtwrap"><div class="name">FundaCheck</div>'
            '<div class="tag">FUNDAMENTAL WORKSPACE</div></div></div>',
            unsafe_allow_html=True,
        )

        # ---- collapse / expand ----
        # The click is recorded but the rerun is deferred to the very end of the
        # sidebar. Rerunning here, before the uploader is instantiated, makes
        # Streamlit discard its state and throw away the loaded file.
        toggle_min = st.button(
            "" if collapsed else "Collapse sidebar", key="side-min",
            use_container_width=True,
            icon=":material/chevron_right:" if collapsed
            else ":material/chevron_left:",
            help="Expand sidebar" if collapsed else None,
        )

        # ---- Data source card (upload / demo / change) ----
        with st.container(border=True, key="ds-card"):
            head = st.empty()                       # header filled once source known
            upload = st.file_uploader(
                "3-statement model (.xlsx)", type=["xlsx", "xlsm"],
                label_visibility="collapsed", key=f"upload_{nonce}",
                help="Any Screener.in-style workbook.",
            )
            if upload is not None:
                st.session_state.demo_on = False

            if upload is not None:
                source, source_label = upload.getvalue(), upload.name
            elif st.session_state.get("demo_on"):
                source, source_label = SAMPLE, "Demo model - 3S_model_sample.xlsx"
            else:
                source, source_label = None, ""

            loaded = source is not None
            src_action = st.button(
                "Change file" if loaded else "Load demo model",
                key="src-action", use_container_width=True,
            )

            # Fill the header now that we know what is loaded.
            if loaded:
                status = ('<span class="ds-dot ok"></span>'
                          '<span class="ds-ready">Data ready</span>')
                fname = source_label
            else:
                status = ('<span class="ds-dot"></span>'
                          '<span class="ds-ready muted">No data</span>')
                fname = "Upload a model to begin"
            head.markdown(
                '<div class="ds-title">Data source</div>'
                f'<div class="ds-file"><span class="ds-ic">&#9636;</span>'
                f'<span>{fname}</span></div>'
                f'<div class="ds-status">{status}</div>',
                unsafe_allow_html=True,
            )

        # ---- navigation ----
        current = st.session_state.setdefault("page", "overview")
        # Clicks are recorded here and acted on at the very end of the sidebar so
        # the uploader's state is never discarded mid-run (see deferred reruns).
        navigate_to = None
        for key, label, icon in NAV_PAGES:
            active = key == current
            if st.button(label, key=f"nav-{key}", use_container_width=True,
                         type="primary" if active else "secondary",
                         icon=f":material/{icon}:",
                         help=label if collapsed else None):
                navigate_to = key

        # ---- night / day toggle (client-side invert of the main content) ----
        # Only the button lives here; its wiring script is a 0-height component
        # rendered in the main area (see main()).
        if not collapsed:
            st.markdown(NIGHT_TOGGLE_HTML, unsafe_allow_html=True)

    # Sector is auto-detected (in main, after the workbook loads) and held in
    # this plain state key -- the manual dropdown was removed from the sidebar.
    sector_key = st.session_state.setdefault("sector_pref", "generic")

    # Reruns are deferred to here, after every sidebar widget has been
    # instantiated, so their state survives.
    if toggle_min:
        st.session_state.nav_min = not collapsed
        st.rerun()
    if src_action:
        # From an empty card or the demo, the secondary button toggles the demo;
        # with a real upload loaded it drops that file (bump the uploader nonce).
        if upload is not None:
            st.session_state.upload_nonce = nonce + 1
            st.session_state.demo_on = False
        else:
            st.session_state.demo_on = not st.session_state.get("demo_on", False)
        st.rerun()
    if navigate_to and navigate_to != current:
        st.session_state.page = navigate_to
        st.rerun()

    C.set_theme("dark" if st.session_state.get("dark_mode") else "light")
    return source, sector_key, source_label


# --------------------------------------------------------------------------
# page sections
# --------------------------------------------------------------------------
def _export_report(model, result) -> bytes:
    """The Export Report button: one PDF snapshotting every section."""
    try:
        return REP.build_pdf(model, result)
    except Exception as exc:                        # noqa: BLE001 - never block UI
        LOGGER.exception("PDF export failed")
        st.warning(f"PDF export failed, falling back to a plain-text report. ({exc})")
        lines = [
            f"FundaCheck report - {model.company.title()}",
            f"Sector lens : {result.sector.name}",
            f"Score       : {result.total_score:.0f}/100 ({result.verdict})",
            "",
        ]
        for m in result.metrics:
            lines.append(f"{S.short_name(m.metric):<28}{m.display(m.latest):>14}"
                         f"  score {round(m.score)}")
        return "\n".join(lines).encode()


def _get_note(model, result, sector_key: str, config: LLMConfig) -> dict:
    """
    The analyst note is written once per loaded workbook: it is cached in
    session state against a fingerprint of the model, so reruns and page
    switches never re-call the LLM. Upload a different file (or change the
    sector lens) and it writes a fresh one.
    """
    fingerprint = "|".join([
        model.company, sector_key, f"{result.total_score:.1f}",
        str(len(result.metrics)), str(model.years[0]), str(model.latest_year),
    ])
    if st.session_state.get("note_fp") == fingerprint \
            and isinstance(st.session_state.get("note"), dict):
        return st.session_state["note"]
    with st.spinner("Writing the analyst note…"):
        note = analyse(result, config)
    st.session_state["note"] = note
    st.session_state["note_fp"] = fingerprint
    return note


def _precompute_llm(model, result, sector_key: str, config: LLMConfig) -> None:
    """Write all LLM text (analyst note + model interpretation) eagerly on upload.

    Both calls are fingerprint-cached, so the actual generation happens once per
    workbook/sector and every later rerun or page switch reuses the stored text.
    Calling it from main() — before the page is chosen — means whichever section
    the user opens first already has its text ready instead of waiting for it."""
    _get_note(model, result, sector_key, config)
    _ensure_interpretation(model, sector_key, config)


def _render_shell(html: str, height: int) -> None:
    """
    One design-exact page, top to bottom, in a single frame.

    scrolling=False keeps the frame free of an inner scrollbar; the shells now
    pass a mild under-estimate and the parent-side fit grows the frame to the full
    content height, so nothing is clipped.
    """
    components.html(html, height=height, scrolling=False)
    # Parent-side fit runs right after the frame exists, growing it to content.
    components.html(RESIZER_JS, height=0)


def _page_header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div style="display:flex;align-items:baseline;gap:14px;'
        f'padding:2px 4px 0;flex-wrap:wrap">'
        f'<span style="font-size:26px;font-weight:800;'
        f'letter-spacing:-.7px;color:#15201a">{title}</span>'
        f'<span style="font-size:13.5px;color:#8b918e">{subtitle}</span>'
        '</div>',
        unsafe_allow_html=True,
    )

def ratios_tab(model, result) -> None:
    """Ratio deep dive - the reference section, one shell, hover everywhere."""
    html, height = SH.ratios_shell(model, result)
    _render_shell(html, height)

def statements_tab(model) -> None:
    """Statements - pill tabs + % change table inside the design shell."""
    query = (st.session_state.get("search_q") or "").strip().lower()
    html, height = SH.statements_shell(model, query)
    _render_shell(html, height)

def sector_lens_tab(model, result) -> None:
    """Sector lens - sector benchmarks from the monthly snapshot + company comparison."""
    snap_all = SNAP.load_snapshot()
    key = st.session_state.get("sector_pref", "generic")
    sector_snap = SNAP.get_sector(snap_all, key) if snap_all else None
    meta = SNAP.snapshot_meta(snap_all) if snap_all else None
    html, height = SH.sector_shell(model, result, sector_snap, sector_key=key, meta=meta)
    _render_shell(html, height)


INTERP_CSS = """<style>
.interp{font-family:'Plus Jakarta Sans',system-ui,sans-serif;color:#15201a}
.interp .ititle{font-size:18px;font-weight:800;color:#15201a;padding:2px 2px 2px}
.interp .isub{font-size:12px;color:#8b918e;padding:2px 2px 10px}
.interp .icard{background:#fff;border:1px solid #e6ebe7;border-radius:16px;
  padding:14px 18px;margin-top:10px;
  box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06)}
.interp .ih{font-size:14.5px;font-weight:800;color:#177245;margin-bottom:6px;
  letter-spacing:.2px}
.interp .ibody{font-size:13px;line-height:1.62;color:#3f4744}
.interp .il{padding:5px 0 5px 16px;position:relative}
.interp .il:before{content:"";position:absolute;left:2px;top:11px;width:5px;
  height:5px;border-radius:50%;background:#37a06a}
.interp .il b{color:#15201a}
.interp .inone{font-size:12.5px;color:#8b918e;font-style:italic;padding:3px 0}
.interp .iext{font-size:11.5px;color:#8b918e;font-style:italic;padding:6px 0 0}
.interp .inote{font-size:13px;color:#8b918e;padding:10px 2px;line-height:1.55}
.interp .srcs{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:6px 2px 2px}
.interp .srcl{font-family:ui-monospace,Menlo,monospace;font-size:10px;
  letter-spacing:1.4px;color:#8b918e;font-weight:700}
.interp .src{font-size:12px;font-weight:600;color:#177245;
  background:#eef4f0;border:1px solid #cfe2d7;border-radius:20px;padding:4px 11px}
</style>"""

# Sections that carry their own "nothing here" wording, so a single-string body
# is a legitimate final answer rather than a missing one.
_INTERP_FALLBACK_PREFIXES = ("nothing material", "valuation data not available")


def _interp_bullets(bullets: list[str]) -> str:
    """Render one section's bullets to HTML.

    A bold **lead-in** becomes <b>…</b>; a lone "Nothing material…"/"Valuation
    data not available…" line renders as an italic note, not a bullet."""
    if not bullets:
        return '<div class="inone">Nothing material changed in this area.</div>'
    if len(bullets) == 1 and bullets[0].lower().startswith(_INTERP_FALLBACK_PREFIXES):
        return f'<div class="inone">{html.escape(bullets[0])}</div>'
    rows = []
    for raw in bullets:
        text = html.escape(raw)
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        # An "External context: X" tail is styled as a muted source line.
        m = re.search(r"(External context:\s*.+)$", text)
        if m:
            head = text[: m.start()].rstrip()
            rows.append(f'<div class="il">{head}<div class="iext">{m.group(1)}</div></div>')
        else:
            rows.append(f'<div class="il">{text}</div>')
    return "".join(rows)


def _ensure_interpretation(model, sector_key: str, config: LLMConfig,
                           spinner: bool = True):
    """Generate (or reuse) the model interpretation, cached in session state
    against a fingerprint of the model's own numbers. No rendering — so it can be
    called eagerly on upload as well as by the section that displays it. Only a
    different or edited workbook (a new fingerprint) regenerates."""
    sector_name = get_sector(sector_key).name
    key = "__interp__"
    fp = f"{model.company}|{sector_name}|" + interp_fingerprint(model, sector_name)
    entry = st.session_state.get(key)
    if entry and entry.get("fp") == fp:
        return entry["interp"]
    if spinner:
        with st.spinner("Interpreting the financial model…"):
            interp = build_interpretation(model, sector_name, config)
    else:
        interp = build_interpretation(model, sector_name, config)
    # Only memoise a usable result; a transient failure is retried next visit.
    if not interp.error:
        st.session_state[key] = {"fp": fp, "interp": interp}
    return interp


def _model_interpretation_block(model, sector_key: str, config: LLMConfig) -> None:
    """FINANCIAL MODEL INTERPRETATION — generated ONCE per loaded workbook (see
    _ensure_interpretation); this only renders it."""
    sector_name = get_sector(sector_key).name
    interp = _ensure_interpretation(model, sector_key, config)

    head = ('<div class="ititle">Financial Model Interpretation</div>'
            f'<div class="isub">{html.escape(model.company.title())} · '
            f'{html.escape(sector_name)} · read directly from the uploaded model</div>')

    if interp.offline:
        note = ("No Groq or Gemini API key is detected in this app's Secrets, so the "
                "written interpretation can't be generated. Your parsed model data "
                "is available in the tabs above and the chat below still works.")
        vcomp(INTERP_CSS + f'<div class="interp">{head}'
              f'<div class="inote">{note}</div></div>', 220)
        return
    if interp.error:
        vcomp(INTERP_CSS + f'<div class="interp">{head}'
              f'<div class="inote">{html.escape(interp.error)}</div></div>', 220)
        return

    cards = []
    for skey, heading in INTERP_SECTIONS:
        body = _interp_bullets(interp.sections.get(skey, []))
        cards.append(f'<div class="icard"><div class="ih">{heading}</div>'
                     f'<div class="ibody">{body}</div></div>')
    body_html = "".join(cards)

    if interp.sources:
        chips = "".join(f'<span class="src">{html.escape(s)}</span>'
                        for s in interp.sources)
        srcs = f'<div class="srcs"><span class="srcl">Sources</span>{chips}</div>'
    else:
        srcs = ('<div class="srcs"><span class="srcl">Sources</span>'
                '<span class="inone">Uploaded model only — no external sources used.</span></div>')
    body_html += f'<div class="icard">{srcs}</div>'

    vcomp(INTERP_CSS + f'<div class="interp">{head}{body_html}</div>', 1500)


def qa_tab(model, result, config: LLMConfig) -> None:
    _page_header("Ask the analyst",
                 "Answers grounded only in the loaded model.")
    sector_key = st.session_state.get("sector_pref", "generic")
    # Interpretation on the left, the working ask box on the right — instead of a
    # full-width ask box stranded at the bottom of the page.
    left, right = st.columns([2, 1], gap="large")
    with left:
        _model_interpretation_block(model, sector_key, config)
    with right:
        st.markdown(
            '<div style="background:linear-gradient(135deg,#0d1d16,#0a1610);'
            'border-radius:16px;padding:16px 18px;color:#eaf3ee">'
            '<div style="font-size:16px;font-weight:800">Ask the analyst</div>'
            '<div style="font-size:11.5px;color:#9fb4a8;padding-top:3px;line-height:1.5">'
            'Groq · Gemini — grounded only in the scored ratios and sector profile of '
            'the loaded model, so it cannot invent outside facts.</div></div>',
            unsafe_allow_html=True,
        )
        suggestions = [
            "Is the debt load sustainable given the cash flows?",
            "What single ratio would change the verdict fastest?",
            "How would this company look if it were an IT services firm instead?",
        ]
        picked = st.radio("Suggested questions", suggestions, index=None,
                          label_visibility="collapsed")
        question = st.text_input(
            "Your question", value=picked or "",
            placeholder="e.g. why is the return profile weak despite profit growth?",
            label_visibility="collapsed",
        )
        if st.button("Ask", type="primary") and question.strip():
            with st.spinner("Analysing…"):
                answer = answer_question(result, question.strip(), config)
            safe = html.escape(answer).replace("\n", "<br>")
            st.markdown(
                '<div style="background:#fff;border:1px solid #e6ebe7;border-radius:14px;'
                'padding:14px 16px;margin-top:12px;font-size:13.5px;line-height:1.65;'
                f'color:#3f4744"><div style="font-size:11px;font-weight:700;letter-spacing:.6px;'
                f'color:#177245;padding-bottom:6px">ANSWER</div>{safe}</div>',
                unsafe_allow_html=True,
            )


# SPLICE_END

# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _load(file_bytes: bytes | None, path: str | None):
    return load_model(path) if path else load_model(pd.io.common.BytesIO(file_bytes))


@st.cache_data(ttl=60 * 60 * 24, show_spinner=False)
def _live_quote(company: str) -> dict | None:
    """
    Last traded price and market cap pulled directly from Screener.in, cached for
    24 hours so it refreshes once a day and hits Screener at most once per
    company per day. Returns None if Screener is unreachable, so the header falls
    back to the workbook's own figures.
    """
    try:
        from core.screener import fetch_quote
        return fetch_quote((company or "").strip())
    except Exception:                              # noqa: BLE001 - never block the app
        return None


def _apply_live_quote(model) -> None:
    """Overlay the live daily price / market cap onto the model's metadata."""
    quote = _live_quote(model.company)
    if not quote:
        return
    if quote.get("current_price") is not None:
        model.meta["current_price"] = quote["current_price"]
    if quote.get("market_cap") is not None:
        model.meta["market_cap"] = quote["market_cap"]
    model.meta["price_source"] = quote.get("source", "")
    model.meta["price_as_of"] = quote.get("as_of", "")


def main() -> None:
    mode = "dark" if st.session_state.get("dark_mode", False) else "light"
    inject_css(mode, minimized=bool(st.session_state.get("nav_min", False)))
    source, sector_key, source_label = sidebar()
    # Night-mode wiring: a 0-height helper in the main area (not the sidebar) that
    # reaches the sidebar button through the parent document and toggles the
    # invert. Kept out of the sidebar so it adds no gap/overlap there.
    components.html(NIGHT_JS, height=0)
    # Keys live in the deployment's secret store, never in the UI or the repo.
    config = analyst_config()

    if source is None:
        st.markdown(
            '<div class="masthead"><h1>FundaCheck</h1>'
            '<div class="sub">UPLOAD A 3-STATEMENT MODEL TO BEGIN</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="empty-hero"><div class="glyph">◈</div>'
            "<h3>Drop a 3-statement model into the sidebar</h3>"
            "<p>Any Screener.in-style workbook works — it needs a <b>HistoricalFS</b> "
            "sheet and, ideally, a <b>Ratio Analysis</b> sheet. Nothing is uploaded "
            "anywhere: the file is parsed in memory for this session only.</p>"
            '<div class="empty-steps"><div>1 · Upload the .xlsx</div>'
            "<div>2 · Pick the sector</div><div>3 · Read the verdict</div></div></div>",
            unsafe_allow_html=True,
        )
        return

    try:
        if isinstance(source, (bytes, bytearray)):
            model = _load(bytes(source), None)
        else:
            model = _load(None, str(source))
    except ParseError as exc:
        st.error(f"That workbook could not be read: {exc}")
        return
    except Exception as exc:                       # noqa: BLE001
        st.error(f"Unexpected problem reading the workbook: {exc}")
        return

    # Refresh last traded price and market cap from Screener.in so the header
    # shows today's value, not the workbook's stale one. Done before the ratios
    # below because the trailing P/E is priced off current_price.
    _apply_live_quote(model)

    # Fill in any benchmark ratio the workbook did not supply, computed from
    # its own statements, so a formulas-only export still analyses.
    derived = fill_missing_ratios(model)
    if derived:
        LOGGER.info("derived %d ratios for %s", len(derived), model.company)

    # Likewise rebuild the common-size statement (used by the Common Size tab and
    # the "₹100 of sales" card) when the workbook's own sheet reads empty.
    derived_cs = fill_missing_common_size(model)
    if derived_cs:
        LOGGER.info("derived %d common-size rows for %s", len(derived_cs), model.company)

    # A new workbook gets its sector detected once; after that the dropdown is
    # the source of truth, so changing it by hand sticks.
    if st.session_state.get("detected_for") != model.company:
        detected, why = detect_sector(model.company, {
            "Debt to Equity Ratio": model.latest("Debt to Equity Ratio"),
            "Interest % Sales": model.latest("Interest % Sales"),
            "EBITDA Margin": model.latest("EBITDA Margin"),
            "Fixed Asset Turnover": model.latest("Fixed Asset Turnover"),
            "Net Profit Margin": model.latest("Net Profit Margin"),
        })
        st.session_state.detected_for = model.company
        st.session_state.sector_pref = detected
        LOGGER.info("sector detected for %s: %s (%s)", model.company, detected, why)
        st.rerun()

    sector = get_sector(sector_key)
    try:
        result = assess(model, sector)
    except ValueError as exc:
        st.error(str(exc))
        return

    # Write every LLM text once, up front — as soon as the file is loaded — so the
    # analyst note and the model interpretation are ready no matter which section
    # the user opens first, instead of being generated (and waited on) only when
    # that section is visited. Both are fingerprint-cached, so this runs a single
    # time per workbook/sector and every later rerun and page switch reuses it.
    _precompute_llm(model, result, sector_key, config)

    page = st.session_state.get("page", "overview")

    if page == "overview":
        note = _get_note(model, result, sector_key, config)
        peers = st.session_state.setdefault("peers", [])
        html, height = SH.dashboard_shell(model, result, note, peers)
        _render_shell(html, height)
        # The rule-based note already reads as a complete analysis on its own, so
        # a failed AI call falls back to it silently rather than printing a raw
        # provider error over the dashboard. Details still go to the server log.
        if note.get("_error"):
            LOGGER.info("AI analyst fell back to rule-based note: %s", note["_error"])
        if result.data_gaps:
            st.caption(
                "Metrics not found in this workbook (excluded from the score): "
                + ", ".join(result.data_gaps)
            )
    elif page == "ratios":
        ratios_tab(model, result)
    elif page == "lens":
        sector_lens_tab(model, result)
    elif page == "statements":
        statements_tab(model)
    else:
        qa_tab(model, result, config)


if __name__ == "__main__":
    main()
