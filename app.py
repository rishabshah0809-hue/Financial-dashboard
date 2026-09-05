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
from core import screener as SC
from core import market_context as MC
from core import sector_snapshot as SNAP
from core import sector_universe as U
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

@st.cache_data(show_spinner=False)
def _company_name_index() -> dict[str, str]:
    """normalized company name -> NSE symbol, from data/company_master.csv."""
    import csv
    from pathlib import Path
    path = Path(__file__).resolve().parent / "data" / "company_master.csv"
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                sym = (row.get("nse_symbol") or row.get("symbol") or "").strip().upper()
                name = (row.get("name") or row.get("company") or "").strip()
                if sym and name:
                    out[_norm_name(name)] = sym
    except Exception:
        return {}
    return out


def _norm_name(s: str) -> str:
    s = (s or "").lower()
    for junk in (" ltd.", " ltd", " limited", " the ", "&", ".", ","):
        s = s.replace(junk, " ")
    return " ".join(s.split())


def _resolve_nse_symbol(company_name: str) -> str | None:
    """Best-effort resolve an uploaded company name to its NSE symbol."""
    idx = _company_name_index()
    if not idx:
        return None
    key = _norm_name(company_name)
    if key in idx:
        return idx[key]
    try:
        from rapidfuzz import process, fuzz
        match = process.extractOne(key, idx.keys(), scorer=fuzz.WRatio, score_cutoff=90)
        if match:
            return idx[match[0]]
    except Exception:
        pass
    return None


@st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
def _screener_live_rows(symbols: tuple[str, ...], is_financial: bool = False) -> dict:
    """Live-fetch constituents straight from Screener.in (cached ~6h). Returns a
    full constituent row per symbol — P/E, P/B, ROE, ROA, ROCE, growth, OPM, NPM,
    D/E, interest cover, CMP, market cap — all computed by core.screener."""
    import requests
    out: dict = {}
    if not symbols:
        return out
    sess = requests.Session()
    for sym in symbols:
        try:
            c = SC.fetch(sym, sess)
        except Exception:                     # noqa: BLE001 - never break the page
            c = None
        if c:
            out[sym] = SC.constituent_row(c, is_financial=is_financial)
    return out


def sector_lens_tab(model, result) -> None:
    """Sector lens - niche NSE-index peer universe + company comparison."""
    fundamentals = SNAP.load_snapshot()      # IndianAPI periodic (PEG/EPS/Piotroski/ROA)
    screener = SNAP.load_screener()          # daily Screener market snapshot

    # Classify the analysed company into a niche NSE sectoral index by symbol.
    sym = _resolve_nse_symbol(model.company)
    auto_key, auto_type = U.classify_symbol(sym) if sym else (None, "unclassified")

    options = list(U.ORDER)  # 25 niche + 2 broad fallbacks
    labels = {k: U.UNIVERSES[k].sector_name for k in options}
    default_key = auto_key if auto_key in options else options[0]

    # Peer universe is auto-detected from the company's NSE symbol; the manual
    # override banner + selector are intentionally hidden per user preference.
    chosen = default_key

    sector_snap, src_meta = SNAP.merge_sector(screener, fundamentals, chosen)

    # Constituents table = full NSE-index membership, every row sourced from
    # Screener. Rows in the daily snapshot are used as-is; any member missing (or
    # lacking ratios) is fetched live from Screener. IndianAPI is never used here.
    members = list(U.all_unique_symbols()[0].get(chosen, []))
    is_fin = bool(U.UNIVERSES[chosen].is_financial) if chosen in U.UNIVERSES else False
    # Rows come from the SCREENER snapshot only — never the IndianAPI fallback
    # (which lacks per-company ROE). Anything missing is live-fetched below.
    scr_sector = SNAP.get_sector(screener, chosen) if screener else None
    snap_rows = {r.get("nse_symbol"): dict(r)
                 for r in ((scr_sector or {}).get("constituents") or []) if r.get("nse_symbol")}
    need = tuple(sorted({s for s in members
                         if s not in snap_rows
                         or snap_rows[s].get("pe") is None
                         or snap_rows[s].get("roe") is None}))
    live = {}
    if need:
        with st.spinner("Fetching latest data from Screener…"):
            live = _screener_live_rows(need, is_fin)
    rows = []
    for s in members:
        row = snap_rows.get(s) or {"nse_symbol": s}
        lr = live.get(s)
        if lr:
            for k, v in lr.items():
                if v is not None and row.get(k) is None:
                    row[k] = v
        if row.get("market_cap") is not None or row.get("pe") is not None:
            rows.append(row)
    rows.sort(key=lambda r: (r.get("market_cap") or 0), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    if sector_snap is not None:
        sector_snap["constituents"] = rows

    meta = SNAP.snapshot_meta(fundamentals) if fundamentals else {}
    meta.update(src_meta)
    context = _sector_market_context(chosen, labels.get(chosen, chosen))
    html, height = SH.sector_shell(model, result, sector_snap, sector_key=chosen,
                                   meta=meta, context=context)
    _render_shell(html, height)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _sector_market_context(sector_key: str, sector_name: str) -> dict | None:
    """Current-cycle context for the selected sector. core.market_context caches
    to disk per sector per day; this st.cache_data wrapper additionally avoids
    re-entering it on every Streamlit rerun within the session. Fails soft to
    None so the Sector Lens still renders if news/LLM are unavailable."""
    try:
        return MC.get_context(sector_key, sector_name, config=analyst_config())
    except Exception:                                   # noqa: BLE001 — never break the page
        LOGGER.warning("market context unavailable for %s", sector_key, exc_info=True)
        return None


INTERP_CSS = """<style>
*{box-sizing:border-box}
.interp{font-family:'Plus Jakarta Sans',system-ui,sans-serif;color:#15201a}
.interp .ititle{font-size:18px;font-weight:800;color:#15201a;padding:2px 2px 2px}
.interp .isub{font-size:12px;color:#8b918e;padding:2px 2px 12px}
.interp .inone{font-size:12.5px;color:#8b918e;font-style:italic;padding:3px 0}
.interp .iext{font-size:11.5px;color:#8b918e;font-style:italic;padding:6px 0 0}
.interp .inote{font-size:13px;color:#8b918e;padding:10px 2px;line-height:1.55}
/* ===== two-column layout: interpretation grid + AI rail ===== */
#layout{display:flex;gap:18px;align-items:flex-start}
#main{flex:1;min-width:0}
#aside{flex:0 0 306px;max-width:306px;position:sticky;top:8px}
@media(max-width:900px){#layout{flex-direction:column}#aside{flex:1 1 auto;max-width:100%;width:100%;position:static}}
.hero2{text-align:left;padding:6px 2px 2px}
.hero2 h1{font-size:34px;font-weight:800;letter-spacing:-1px;color:#15201a}
.hero2 p{font-size:14.5px;color:#8b918e;padding-top:7px;max-width:640px}
.mainlbl{font-size:12px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;
  color:#9aa09d;padding:14px 2px 10px;font-family:ui-monospace,Menlo,monospace}
/* ===== feature-card grid (icon top-right · corner deco) ===== */
.interp .igrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
  gap:16px;align-items:stretch}
.interp .icard{position:relative;overflow:hidden;background:#fff;border:1px solid #e6ebe7;
  border-radius:16px;padding:20px 22px 22px;min-height:172px;display:flex;flex-direction:column;
  box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06);
  transition:transform .18s ease,box-shadow .18s ease}
.interp .icard:hover{transform:translateY(-3px);
  box-shadow:0 2px 4px rgba(21,32,26,.05),0 14px 32px rgba(21,32,26,.11)}
.interp .icard.wide{grid-column:1/-1;min-height:0}
.interp .icard.srccard{grid-column:1/-1;min-height:0;padding:14px 18px;overflow:visible;margin-top:16px}
.interp .cIcon{position:absolute;top:18px;right:18px;width:40px;height:40px;border-radius:12px;
  background:var(--tint,#e9efec);border:1px solid rgba(21,32,26,.05);display:flex;
  align-items:center;justify-content:center;z-index:2}
.interp .cIcon svg{width:22px;height:22px;fill:none;stroke:var(--hue,#2f5545);stroke-width:1.7;
  stroke-linecap:round;stroke-linejoin:round}
.interp .ih{font-size:16.5px;font-weight:800;letter-spacing:-.3px;color:#15201a;
  padding-right:56px;min-height:40px;display:flex;align-items:flex-start;margin-bottom:2px}
.interp .ibody{font-size:13px;line-height:1.66;color:#3f4744;padding-top:4px;position:relative;z-index:1}
.interp .il{padding:6px 0 6px 16px;position:relative}
.interp .il:before{content:"";position:absolute;left:2px;top:13px;width:5px;height:5px;
  border-radius:50%;background:#37a06a}
.interp .il b{color:#15201a}
.interp .cDeco{position:absolute;right:-4px;bottom:-6px;width:120px;height:90px;
  pointer-events:none;z-index:0}
.interp .srcs{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:2px}
.interp .srcl{font-family:ui-monospace,Menlo,monospace;font-size:10px;
  letter-spacing:1.4px;color:#8b918e;font-weight:700}
.interp .src{font-size:12px;font-weight:600;color:#177245;
  background:#eef4f0;border:1px solid #cfe2d7;border-radius:20px;padding:4px 11px}
/* ===== right AI rail ===== */
.airail .aicard{background:#fff;border:1px solid #e6ebe7;border-radius:18px;padding:20px 18px;
  box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06);text-align:center}
.airail .aiav{width:58px;height:58px;border-radius:17px;margin:0 auto;
  background:radial-gradient(130% 130% at 20% 0%,#2a9c62,#0d4a2c);
  display:flex;align-items:center;justify-content:center;box-shadow:0 6px 16px rgba(13,74,44,.28)}
.airail .aiav svg{width:30px;height:30px;stroke:#fff;stroke-width:1.6;fill:none;
  stroke-linecap:round;stroke-linejoin:round}
.airail .ainame{font-size:16.5px;font-weight:800;color:#15201a;padding-top:12px}
.airail .airole{font-size:11.5px;color:#8b918e;padding-top:3px;line-height:1.4}
.airail .aibar{height:5px;border-radius:4px;background:#eef1ee;margin:14px 6px 6px;overflow:hidden}
.airail .aibar i{display:block;height:100%;width:66%;border-radius:4px;
  background:linear-gradient(90deg,#37a06a,#177245)}
.airail .aihint{font-size:9.5px;letter-spacing:1.3px;text-transform:uppercase;color:#a4a9a6;
  font-weight:700;font-family:ui-monospace,Menlo,monospace}
.airail .sechead{font-size:18px;font-weight:800;color:#177245;letter-spacing:-.3px;padding:20px 4px 1px}
.airail .sesub{font-size:12px;color:#8b918e;padding:0 4px 6px}
.airail .qitem{display:flex;gap:13px;align-items:center;padding:13px 6px;
  border-top:1px solid #eef0ed;border-radius:10px}
.airail .qn{font-size:27px;font-weight:800;color:#d3e2da;line-height:1;flex:none;width:36px;
  font-family:ui-monospace,Menlo,monospace}
.airail .qt{font-size:13px;font-weight:600;color:#15201a;line-height:1.42}
.airail .ans{margin-top:14px;background:#f6f9f7;border:1px solid #e6ebe7;border-radius:14px;
  padding:13px 15px;font-size:12.5px;line-height:1.62;color:#3f4744}
.airail .ans .albl{font-size:10.5px;font-weight:700;letter-spacing:1.2px;color:#9aa09d;
  text-transform:uppercase;padding-bottom:6px}
</style>"""

# Per-section feature-card icon + colour (hue = stroke, tint = icon-chip bg),
# matched to the ask_ai.html reference. Keyed by core.interpret SECTION key.
INTERP_CARD_STYLE = {
    "growth": ("#2F9E63", "#E7F2EC",
               '<polyline points="3 16 9 10 13 14 21 6"/><polyline points="15 6 21 6 21 12"/>'),
    "margins": ("#177245", "#E6F0EA",
                '<line x1="5" y1="21" x2="5" y2="12"/><line x1="12" y1="21" x2="12" y2="4"/><line x1="19" y1="21" x2="19" y2="15"/>'),
    "costs": ("#B5761F", "#FBF1DF",
              '<path d="M6 3h12v18l-2-1.4-2 1.4-2-1.4-2 1.4-2-1.4L6 21Z"/><line x1="9" y1="8.5" x2="15" y2="8.5"/><line x1="9" y1="12.5" x2="15" y2="12.5"/>'),
    "returns": ("#2F9E63", "#E7F2EC",
                '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="0.7"/>'),
    "efficiency": ("#3D9E6B", "#E7F2EC",
                   '<path d="M20 11a8 8 0 0 0-13.3-4.5L4 9"/><polyline points="4 4 4 9 9 9"/><path d="M4 13a8 8 0 0 0 13.3 4.5L20 15"/><polyline points="20 20 20 15 15 15"/>'),
    "leverage_cashflow": ("#B4483C", "#FBEDEB",
                          '<line x1="3" y1="21" x2="21" y2="21"/><path d="M12 3 21 8H3Z"/><line x1="6.5" y1="10.5" x2="6.5" y2="18"/><line x1="12" y1="10.5" x2="12" y2="18"/><line x1="17.5" y1="10.5" x2="17.5" y2="18"/>'),
    "valuation": ("#C68A2E", "#FBF1DF",
                  '<path d="M20.5 12.5 12 21l-9-9V3h9z"/><circle cx="7.4" cy="7.4" r="1.5"/>'),
}


def _interp_deco(hue: str) -> str:
    """Corner decoration circles echoing the card's hue (ask_ai.html reference)."""
    return (
        '<svg class="cDeco" viewBox="0 0 130 100" preserveAspectRatio="xMaxYMax meet">'
        f'<circle cx="95" cy="80" r="40" fill="none" stroke="{hue}" stroke-width="1.5" opacity=".15"/>'
        f'<circle cx="101" cy="73" r="25" fill="{hue}" opacity=".10"/>'
        f'<circle cx="79" cy="82" r="16" fill="{hue}" opacity=".13"/>'
        f'<circle cx="105" cy="88" r="8" fill="{hue}" opacity=".20"/></svg>'
    )

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


# Suggested questions — shown in the AI rail (visual) and the functional ask box.
_SUGGESTED_QS = [
    "Is the debt load sustainable given the cash flows?",
    "What single ratio would change the verdict fastest?",
    "How would this company look if it were an IT services firm instead?",
]


def _ai_rail_html() -> str:
    """Right-hand AI Analyst rail (identity + suggested questions), design-exact."""
    qitems = "".join(
        f'<div class="qitem"><span class="qn">{i:02d}</span>'
        f'<div class="qt">{html.escape(q)}</div></div>'
        for i, q in enumerate(_SUGGESTED_QS, 1)
    )
    return (
        '<aside id="aside" class="airail"><div class="aicard"><div class="aiav">'
        '<svg viewBox="0 0 24 24"><path d="M12 3l1.7 4.6L18.3 9l-4.6 1.4L12 15l-1.7-4.6L5.7 9l4.6-1.4z"/>'
        '<circle cx="18.2" cy="17.4" r="1.5"/><circle cx="6.4" cy="16.6" r="1.1"/></svg></div>'
        '<div class="ainame">AI Analyst</div>'
        '<div class="airole">Groq · Gemini — grounded in your model</div>'
        '<div class="aibar"><i></i></div>'
        '<div class="aihint">Reads scored ratios only</div></div>'
        '<div class="sechead">Suggested</div>'
        '<div class="sesub">Pick one in the ask box below</div>'
        f'{qitems}'
        '<div class="ans"><div class="albl">Answer</div>'
        'Type a question in the ask box beneath this panel — the analyst’s grounded '
        'reply is written only from the scored ratios and the sector profile of the loaded '
        'model.</div></aside>'
    )


def _model_interpretation_block(model, sector_key: str, config: LLMConfig) -> None:
    """FINANCIAL MODEL INTERPRETATION — generated ONCE per loaded workbook.

    Cached in session state against a fingerprint of the model's own numbers, so
    tab switches, chat questions and Streamlit reruns all reuse the same text.
    Only a *different* (or edited) workbook — a new fingerprint — regenerates."""
    sector_name = get_sector(sector_key).name
    key = "__interp__"
    fp = f"{model.company}|{sector_name}|" + interp_fingerprint(model, sector_name)
    entry = st.session_state.get(key)
    if entry and entry.get("fp") == fp:
        interp = entry["interp"]
    else:
        with st.spinner("Interpreting the financial model…"):
            interp = build_interpretation(model, sector_name, config)
        # Only memoise a usable result; a transient failure is retried next visit.
        if not interp.error:
            st.session_state[key] = {"fp": fp, "interp": interp}

    hero = ('<div class="hero2"><h1>Ask the analyst</h1>'
            '<p>Ask anything about the loaded model — answers are grounded only in its '
            'scored ratios and sector profile.</p></div>'
            '<div class="mainlbl">Model interpretation</div>')
    head = ('<div class="ititle">Financial Model Interpretation</div>'
            f'<div class="isub">{html.escape(model.company.title())} · '
            f'{html.escape(sector_name)} · read directly from the uploaded model</div>')
    rail = _ai_rail_html()

    def _layout(main_inner: str, height: int) -> None:
        vcomp(INTERP_CSS
              + f'<div id="layout"><div id="main">{hero}{main_inner}</div>{rail}</div>',
              height)

    if interp.offline:
        note = ("No Groq or Gemini API key is detected in this app's Secrets, so the "
                "written interpretation can't be generated. Your parsed model data "
                "is available in the tabs above and the ask box below still works.")
        _layout(f'<div class="interp">{head}<div class="inote">{note}</div></div>', 560)
        return
    if interp.error:
        _layout(f'<div class="interp">{head}'
                f'<div class="inote">{html.escape(interp.error)}</div></div>', 560)
        return

    cards = []
    for skey, heading in INTERP_SECTIONS:
        body = _interp_bullets(interp.sections.get(skey, []))
        hue, tint, glyph = INTERP_CARD_STYLE.get(skey, ("#2f5545", "#e9efec", ""))
        icon = (f'<span class="cIcon"><svg viewBox="0 0 24 24">{glyph}</svg></span>'
                if glyph else "")
        wide = " wide" if skey == "valuation" else ""
        cards.append(
            f'<div class="icard{wide}" style="--hue:{hue};--tint:{tint}">{icon}'
            f'<div class="ih">{heading}</div>'
            f'<div class="ibody">{body}{_interp_deco(hue)}</div></div>'
        )
    grid = f'<div class="igrid">{"".join(cards)}</div>'

    if interp.sources:
        chips = "".join(f'<span class="src">{html.escape(s)}</span>'
                        for s in interp.sources)
        srcs = f'<div class="srcs"><span class="srcl">Sources</span>{chips}</div>'
    else:
        srcs = ('<div class="srcs"><span class="srcl">Sources</span>'
                '<span class="inone">Uploaded model only — no external sources used.</span></div>')
    srccard = f'<div class="icard srccard">{srcs}</div>'

    _layout(f'<div class="interp">{head}{grid}{srccard}</div>', 1600)


def qa_tab(model, result, config: LLMConfig) -> None:
    _model_interpretation_block(model, st.session_state.get("sector_pref", "generic"), config)
    with card("Ask the analyst"):
        st.caption(
            "Free-text questions about the loaded company. The model only sees the "
            "scored ratios and the sector profile, so it cannot invent outside facts."
        )
        picked = st.radio("Suggested questions", _SUGGESTED_QS, horizontal=False, index=None,
                          label_visibility="collapsed")
        question = st.text_input(
            "Your question", value=picked or "",
            placeholder="e.g. why is the return profile weak despite profit growth?",
            label_visibility="collapsed",
        )
        if st.button("Ask", type="primary") and question.strip():
            with st.spinner("Analysing…"):
                answer = answer_question(result, question.strip(), config)
                vcomp(f'<div style="font-size:13.5px;line-height:1.65;'
                      f'color:#3f4744;padding:4px 2px">{answer}</div>', 180)


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
