"""
Export standalone HTML previews of two FundaCheck sections.

Renders the **Sector lens** and **Ask the analyst (Ask AI)** sections to
self-contained .html files using the bundled demo model, so the design can be
reviewed, shared, or embedded outside the running Streamlit app.

    python scripts/export_html.py            # writes into preview/

- Sector lens  -> preview/sector_lens.html  (real data: demo model + snapshot)
- Ask the analyst -> preview/ask_ai.html    (section design; the interpretation
  cards carry illustrative content because the live text is written by an LLM
  at runtime and no Groq/Gemini key is bundled with the repo)
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

# Allow `python scripts/export_html.py` from anywhere by putting the repo root
# (which holds the `core` package) on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import sector_snapshot as SNAP
from core import shell as SH
from core import viz
from core.interpret import SECTIONS as INTERP_SECTIONS
from core.parser import load_model
from core.scoring import assess
from core.sectors import detect_sector, get_sector

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data" / "3S_model_sample.xlsx"
OUT = ROOT / "preview"


# --------------------------------------------------------------------------
# Ask-the-analyst rendering (mirrors app.py: interpretation block + ask card)
# --------------------------------------------------------------------------
INTERP_CSS = """
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

/* --- page header + ask card (mirrors the Streamlit chrome) --- */
.pg{display:flex;align-items:baseline;gap:14px;padding:2px 4px 12px;flex-wrap:wrap}
.pg .pt{font-size:26px;font-weight:800;letter-spacing:-.7px;color:#15201a}
.pg .ps{font-size:13.5px;color:#8b918e}
.demo-note{background:#eef4f0;border:1px solid #cfe2d7;border-radius:12px;
  color:#3f6b52;font-size:12.5px;line-height:1.5;padding:10px 14px;margin:2px 2px 14px}
.demo-note b{color:#177245}
.askcard{background:#fff;border:1px solid #e6ebe7;border-radius:18px;
  padding:18px 20px;margin-top:14px;
  box-shadow:0 1px 2px rgba(21,32,26,.04),0 6px 18px rgba(21,32,26,.06)}
.askcard .card-title{font-size:16px;font-weight:800;color:#15201a;padding-bottom:4px}
.askcard .cap{font-size:12.5px;color:#8b918e;line-height:1.55;padding-bottom:12px}
.askcard .sglbl{font-size:11px;font-weight:700;letter-spacing:1.2px;color:#9aa09d;
  text-transform:uppercase;padding:2px 2px 8px}
.suggest{display:flex;flex-direction:column;gap:8px;padding-bottom:14px}
.suggest .q{display:flex;align-items:center;gap:10px;font-size:13.5px;color:#3f4744;
  background:#f6f9f7;border:1px solid #e6ebe7;border-radius:12px;padding:11px 14px;
  cursor:pointer;transition:border-color .15s ease,background .15s ease}
.suggest .q:hover{border-color:#37a06a;background:#eef4f0}
.suggest .q .dot{width:7px;height:7px;border-radius:50%;background:#37a06a;flex:none}
.askrow{display:flex;gap:10px;align-items:stretch}
.askrow input{flex:1;font-family:inherit;font-size:14px;color:#15201a;
  background:#fff;border:1px solid #d8e0da;border-radius:12px;padding:12px 14px;outline:none}
.askrow input:focus{border-color:#37a06a;box-shadow:0 0 0 3px rgba(55,160,106,.15)}
.askrow input::placeholder{color:#a7b0aa}
.askbtn{background:linear-gradient(135deg,#2a9c62,#177245);color:#fff;border:none;
  border-radius:12px;font-family:inherit;font-size:14px;font-weight:700;
  padding:12px 26px;cursor:pointer;white-space:nowrap}
.askbtn:hover{filter:brightness(1.05)}
.answer{margin-top:14px;background:#f6f9f7;border:1px solid #e6ebe7;border-radius:14px;
  padding:14px 16px;font-size:13.5px;line-height:1.65;color:#3f4744}
.answer .albl{font-size:11px;font-weight:700;letter-spacing:1.2px;color:#9aa09d;
  text-transform:uppercase;padding-bottom:6px}
"""

# Illustrative interpretation, grounded in the demo model (Adani Enterprises,
# infrastructure). In the live app this text is written by Groq/Gemini from the
# uploaded model; here it stands in so the seven-card layout renders in full.
DEMO_INTERP: dict[str, list[str]] = {
    "growth": [
        "**Sales** compounded strongly across FY17–TTM as the incubator kept "
        "spinning up new-energy, airports and logistics ventures.",
        "**Net profit** growth has been lumpier than revenue — gains from asset "
        "monetisation and associate income swing the bottom line year to year.",
    ],
    "margins": [
        "**EBITDA margin** sits below the infrastructure aggregate, consistent with a "
        "trading-and-incubation mix carrying thin gross spreads.",
        "**Net profit margin** is modest on a large revenue base; scale, not margin, "
        "drives the absolute profit line.",
    ],
    "costs": [
        "Cost of materials dominates the P&L, so operating leverage is tied to "
        "commodity and freight pass-through rather than fixed-cost absorption.",
    ],
    "returns": [
        "**ROE** is middling versus the sector — heavy reinvestment into "
        "gestation-stage projects holds returns down while they scale.",
        "**ROCE** is weighed by capital work-in-progress that is not yet earning.",
    ],
    "efficiency": [
        "Working-capital intensity is high, typical of an EPC-and-trading footprint "
        "with long receivable and inventory cycles.",
    ],
    "leverage_cashflow": [
        "**Debt-to-equity** is elevated — the group funds long-dated infrastructure "
        "build-outs with a large borrowing book.",
        "**Cash from operations** must be read against heavy investing outflows; free "
        "cash flow is thin during the current capex cycle.",
    ],
    "valuation": [
        "The stock trades at a premium **P/E** and **P/B** to the infrastructure "
        "aggregate, pricing in optionality across the incubated businesses.",
    ],
}

DEMO_SOURCES = ["Uploaded model", "Sector snapshot (IndianAPI + NSE)"]

_FALLBACK_PREFIXES = ("nothing material", "valuation data not available")


def _interp_bullets(bullets: list[str]) -> str:
    """Render one section's bullets to HTML (mirrors app.py)."""
    if not bullets:
        return '<div class="inone">Nothing material changed in this area.</div>'
    if len(bullets) == 1 and bullets[0].lower().startswith(_FALLBACK_PREFIXES):
        return f'<div class="inone">{html.escape(bullets[0])}</div>'
    rows = []
    for raw in bullets:
        text = html.escape(raw)
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        m = re.search(r"(External context:\s*.+)$", text)
        if m:
            head = text[: m.start()].rstrip()
            rows.append(f'<div class="il">{head}<div class="iext">{m.group(1)}</div></div>')
        else:
            rows.append(f'<div class="il">{text}</div>')
    return "".join(rows)


def ask_ai_html(model, sector_name: str) -> str:
    head = (
        '<div class="ititle">Financial Model Interpretation</div>'
        f'<div class="isub">{html.escape(model.company.title())} · '
        f'{html.escape(sector_name)} · read directly from the uploaded model</div>'
    )

    cards = []
    for skey, heading in INTERP_SECTIONS:
        body = _interp_bullets(DEMO_INTERP.get(skey, []))
        cards.append(
            f'<div class="icard"><div class="ih">{heading}</div>'
            f'<div class="ibody">{body}</div></div>'
        )
    chips = "".join(f'<span class="src">{html.escape(s)}</span>' for s in DEMO_SOURCES)
    cards.append(
        '<div class="icard"><div class="srcs"><span class="srcl">Sources</span>'
        f"{chips}</div></div>"
    )
    interp_block = f'<div class="interp">{head}{"".join(cards)}</div>'

    suggestions = [
        "Is the debt load sustainable given the cash flows?",
        "What single ratio would change the verdict fastest?",
        "How would this company look if it were an IT services firm instead?",
    ]
    sugg_html = "".join(
        f'<div class="q"><span class="dot"></span>{html.escape(q)}</div>'
        for q in suggestions
    )

    ask_card = f"""
    <div class="pg"><span class="pt">Ask the analyst</span>
      <span class="ps">Answers grounded only in the loaded model.</span></div>
    <div class="demo-note"><b>Preview.</b> This is a static export of the
      &ldquo;Ask the analyst&rdquo; section rendered from the bundled demo model
      ({html.escape(model.company.title())}). In the live app the interpretation
      cards below are written by the AI analyst (Groq / Gemini) and the box is
      interactive.</div>
    {interp_block}
    <div class="askcard">
      <div class="card-title">Ask the analyst</div>
      <div class="cap">Free-text questions about the loaded company. The model only
        sees the scored ratios and the sector profile, so it cannot invent outside
        facts.</div>
      <div class="sglbl">Suggested questions</div>
      <div class="suggest">{sugg_html}</div>
      <div class="askrow">
        <input type="text" placeholder="e.g. why is the return profile weak despite profit growth?">
        <button class="askbtn" type="button">Ask</button>
      </div>
      <div class="answer">
        <div class="albl">Answer</div>
        In the live app, the analyst's grounded response appears here — written
        only from the scored ratios and the sector profile of the loaded model.
      </div>
    </div>
    """
    return viz.doc(ask_card, extra_css=INTERP_CSS)


# --------------------------------------------------------------------------
def main() -> None:
    OUT.mkdir(exist_ok=True)
    model = load_model(str(SAMPLE))

    sector_key, _why = detect_sector(model.company, {})
    sector = get_sector(sector_key)
    result = assess(model, sector)

    snap_all = SNAP.load_snapshot()
    sector_snap = SNAP.get_sector(snap_all, sector_key) if snap_all else None
    meta = SNAP.snapshot_meta(snap_all) if snap_all else None

    # ---- Sector lens (real data) ----
    lens_html, _h = SH.sector_shell(
        model, result, sector_snap, sector_key=sector_key, meta=meta
    )
    (OUT / "sector_lens.html").write_text(lens_html, encoding="utf-8")

    # ---- Ask the analyst (section design) ----
    (OUT / "ask_ai.html").write_text(
        ask_ai_html(model, sector.name), encoding="utf-8"
    )

    print(f"wrote {OUT / 'sector_lens.html'}")
    print(f"wrote {OUT / 'ask_ai.html'}")


if __name__ == "__main__":
    main()
