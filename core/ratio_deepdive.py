"""
ratio_deepdive.py
-----------------
Dynamic Ratio Deep Dive page that visually reproduces
``assets/ratio_deepdive_template.html`` exactly (same layout, spacing,
typography, colours, cards, score bars, charts, legends, tooltips,
responsive/hover/sticky behaviour) while replacing every hardcoded example
value with the currently loaded company/model's real data.

Design reuse: the template's <style> CSS, the SVG chart-engine / tooltip /
scorecard / bullet JS, and the Streamlit frame-fit script are loaded verbatim
from the template file at runtime -- never re-typed -- so the page cannot
drift from the design source of truth. Only the DATA block, the render calls,
and the body copy are generated dynamically.

Scoring: scores come from ``core.scoring.assess`` (sector weak/strong bands,
higher/lower-is-better aware, continuous 1-100 with weak->40 / strong->66
anchors, 1-39 Weak / 40-65 Neutral / 66-100 Strong). Pillars and the Funda
Score reuse the existing sector-weighted methodology. Missing inputs are never
invented: unavailable ratios/charts render as "unavailable" with no score.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

_TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "ratio_deepdive_template.html"
_CSS_CACHE: str | None = None
_ENGINE_CACHE: tuple[str, str] | None = None  # (head_links, fit_js)
_BIGJS_MID_CACHE: str | None = None  # scorecard+tooltip+engine+bullets, verbatim


def _template_text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def _css() -> str:
    global _CSS_CACHE
    if _CSS_CACHE is None:
        t = _template_text()
        _CSS_CACHE = t.split("<style>", 1)[1].split("</style>", 1)[0]
    return _CSS_CACHE


def _scripts() -> tuple[str, str]:
    """Return (big_js, fit_js) from the template, verbatim."""
    global _ENGINE_CACHE
    if _ENGINE_CACHE is None:
        t = _template_text()
        parts = re.findall(r"<script>(.*?)</script>", t, re.S)
        big = parts[0] if len(parts) > 0 else ""
        fit = parts[1] if len(parts) > 1 else ""
        _ENGINE_CACHE = (big, fit)
    return _ENGINE_CACHE


def _bigjs_mid(big_js: str) -> str:
    """The reusable middle of the template script: scorecard renderer, tooltip,
    chart engine and bullet renderer -- everything between the DATA block and
    the final render calls. Returned verbatim so hover/tooltip behaviour is
    identical to the design source."""
    global _BIGJS_MID_CACHE
    if _BIGJS_MID_CACHE is None:
        # Start at the engine (bandOf/BAND + the scorecard IIFE), AFTER the
        # template's hardcoded DATA block — otherwise the template re-declares
        # var Y / var SCORECARD / var MARGINS / … and clobbers the dynamic data
        # emitted by _data_js (which also drops the scorecard explanations).
        start_marks = ["function bandOf", "var BAND ", "/*  SCORECARD"]
        end_marks = ["/* ---- render everything", "var pc "]
        start = 0
        for m in start_marks:
            i = big_js.find(m)
            if i != -1:
                start = i
                break
        end = len(big_js)
        for m in end_marks:
            i = big_js.find(m)
            if i != -1:
                end = i
                break
        _BIGJS_MID_CACHE = big_js[start:end]
    return _BIGJS_MID_CACHE


# --------------------------------------------------------------------------
# data helpers (reuse parser / derive output via model.series)
# --------------------------------------------------------------------------

# canonical metric key -> short scorecard label + raw-value kind
SPECS: list[tuple[str, str, str]] = [
    ("Cash Conversion Cycle", "Cash conversion cycle", "days"),
    ("Net Profit Growth", "Net profit growth", "pct"),
    ("Net Profit Margin", "Net profit margin", "pct"),
    ("Debt to Equity Ratio", "Debt to equity", "x"),
    ("Return on Equity (ROE) %", "Return on equity", "pct"),
    ("EBITDA Margin", "EBITDA margin", "pct"),
    ("Return on Assets (ROA) %", "Return on assets", "pct"),
    ("CFO / PAT", "CFO / PAT", "num"),
    ("Fixed Asset Turnover", "Fixed asset turnover", "x"),
    ("Interest Coverage Ratio", "Interest coverage", "x"),
    ("Return on Capital Employed (ROCE) %", "Return on capital", "pct"),
    ("Sales Growth", "Sales growth", "pct"),
]

SHORT_PILLAR = {"growth": "Growth", "profitability": "Profitability",
                "returns": "Returns", "leverage": "Leverage", "efficiency": "Efficiency"}


def full_years(model) -> list[str]:
    return [str(y) for y in model.years
            if str(y).upper() not in ("TTM", "TREND", "MEAN", "MEDIAN")][-10:]


def _num_series(model, *names: str) -> pd.Series:
    for n in names:
        try:
            s = pd.to_numeric(model.series(n), errors="coerce")
        except Exception:
            continue
        s = s.replace([np.inf, -np.inf], np.nan).dropna()
        if not s.empty:
            return s
    return pd.Series(dtype=float)


def _tail_complete(model, years: list[str], *names: str) -> list[float] | None:
    """Values for every year in ``years`` or None (never invent missing)."""
    s = _num_series(model, *names)
    if s.empty:
        return None
    vals: list[float] = []
    for y in years:
        if y not in s.index or pd.isna(s.get(y)):
            return None
        vals.append(float(s.get(y)))
    return vals


def _latest2(model, *names: str,
             years: list[str] | None = None) -> tuple[float | None, float | None]:
    """Latest two values of a line. When `years` is given, the series is
    restricted to those (annual) periods first — so the trailing TTM column in
    the historical sheet is never used as 'latest' on the Ratio Deep Dive."""
    s = _num_series(model, *names)
    if s.empty:
        return None, None
    if years is not None:
        s = s.reindex(years).dropna()
        if s.empty:
            return None, None
    a = float(s.iloc[-1])
    b = float(s.iloc[-2]) if len(s) > 1 else None
    return a, b


def _fmt_raw(value: float | None, kind: str) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "unavailable"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "days":
        return f"{value:.0f} days"
    if kind == "x":
        return f"{value:.2f}x"
    return f"{value:.2f}"


def _fmt_cr(v: float | None) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:.2f}L cr"
    return f"₹{v:,.0f} cr"


def _band(score: float) -> str:
    return "strong" if score >= 66 else "neutral" if score >= 40 else "weak"


_BAND_WORD = {"strong": "Strong", "neutral": "Neutral", "weak": "Weak"}
_BAND_COL = {"Strong": "#2F9E63", "Neutral": "#D9A441", "Weak": "#B4483C"}


def _explain(r: dict) -> tuple[str, str]:
    """Return (inline_panel_html, hover_tooltip_html) explaining how a ratio earns
    its 0-100 score. Uses the SAME sector thresholds and scoring semantics as
    core.scoring (weak→40, strong→66, beyond strong→toward 100) — it explains the
    existing score, it does not recompute or invent one.
    """
    kind = r.get("kind", "num")
    raw = r["display"]
    weak = _fmt_raw(r["weak"], kind)
    strong = _fmt_raw(r["strong"], kind)
    lower = bool(r["lower_better"])
    score = r["score"]
    band = _BAND_WORD.get(r["band"], "Neutral")
    col = _BAND_COL[band]
    v = r["raw"]

    better = "Lower is better" if lower else "Higher is better"
    if lower:
        band_desc = f"weak ≥ {weak} · strong ≤ {strong}"
        if v <= r["strong"]:
            pos = f"at or beyond the strong end ({strong})"
        elif v >= r["weak"]:
            pos = f"at or past the weak end ({weak})"
        else:
            pos = "between the weak and strong marks"
    else:
        band_desc = f"weak ≤ {weak} · strong ≥ {strong}"
        if v >= r["strong"]:
            pos = f"at or beyond the strong end ({strong})"
        elif v <= r["weak"]:
            pos = f"at or below the weak end ({weak})"
        else:
            pos = "between the weak and strong marks"

    why = (
        f"On this sector's scale, a value at the weak mark scores <b>40</b> and at "
        f"the strong mark <b>66</b>; going past the strong mark pushes toward "
        f"<b>100</b>. Your <b>{escape(raw)}</b> sits {escape(pos)}, so this ratio "
        f"scores <b>{score}/100</b> — <b style=\"color:{col}\">{band}</b>."
    )
    avg_txt = _fmt_raw(r["avg"], kind) if r.get("avg") is not None else None
    blend = ("The score blends this year (60%) with the 3-year average"
             + (f" of {avg_txt}" if avg_txt else "") + " (40%) and the recent trend.")

    panel = (
        '<div class="exgrid">'
        f'<div><div class="exk">Company</div><div class="exv">{escape(raw)}</div></div>'
        f'<div><div class="exk">Sector band</div><div class="exv">{escape(band_desc)}</div></div>'
        f'<div><div class="exk">Direction</div><div class="exv">{better}</div></div>'
        f'<div><div class="exk">Result</div><div class="exv" style="color:{col}">{band} · {score}/100</div></div>'
        '</div>'
        f'<div class="exwhy">{why} {escape(blend)}</div>'
    )
    # concise dark-tooltip version for hover
    tip = (
        f'<div class="yr">{escape(r["label"].upper())} · SCORE {score}</div>'
        f'Company <b>{escape(raw)}</b> · sector {escape(band_desc)} '
        f'({better.lower()}). Sits {escape(pos)} → <b>{score}/100</b>, {band}.'
    )
    return panel, tip


# --------------------------------------------------------------------------
# context builder
# --------------------------------------------------------------------------

def build_context(model, result, model_filename: str = "") -> dict:
    years = full_years(model)
    by_metric = {m.metric: m for m in result.metrics}

    scorecard: list[dict] = []
    for metric, label, kind in SPECS:
        m = by_metric.get(metric)
        if m is None or m.latest is None or pd.isna(m.latest):
            scorecard.append({"label": label, "metric": metric, "score": None,
                              "raw": None, "display": "unavailable",
                              "band": None, "available": False})
            continue
        sc = int(round(max(1.0, min(100.0, float(m.score)))))
        avg = (float(m.average_3y) if m.average_3y is not None
               and not pd.isna(m.average_3y) else None)
        scorecard.append({"label": label, "metric": metric, "score": sc,
                          "raw": float(m.latest), "display": _fmt_raw(float(m.latest), kind),
                          "band": _band(sc), "available": True, "kind": kind, "avg": avg,
                          "weak": float(m.weak_at), "strong": float(m.strong_at),
                          "lower_better": bool(m.lower_is_better)})
    avail = sorted([r for r in scorecard if r["available"]],
                   key=lambda r: -r["score"])
    for r in avail:                     # per-ratio "how the 0-100 score is built"
        r["explain"], r["tip"] = _explain(r)
    missing = [r for r in scorecard if not r["available"]]

    pillars = sorted(
        [{"key": k, "label": SHORT_PILLAR.get(k, k), "score": round(float(v))}
         for k, v in (result.pillar_scores or {}).items()],
        key=lambda r: -r["score"])
    total = int(round(float(result.total_score)))
    verdict_word = str(result.verdict).capitalize()  # Strong/Neutral/Weak

    # growth strip — ANNUAL periods only (never the trailing TTM column)
    sales_a, sales_b = _latest2(model, "Sales", "Revenue", "Net Sales", years=years)
    cogs_a, cogs_b = _latest2(model, "COGS", "Cost of Goods Sold", years=years)
    np_a, np_b = _latest2(model, "Net Profit", "Net profit", "PAT", years=years)
    oi_a, _ = _latest2(model, "Other Income", "Other Income ", years=years)
    ebit_a, _ = _latest2(model, "EBIT (OPM)", "EBIT", "Operating Profit", years=years)

    def _g(a, b):
        if a is None or b is None or b == 0 or pd.isna(a) or pd.isna(b):
            return None
        return (a - b) / abs(b) * 100

    growth = {
        "sales_g": _g(sales_a, sales_b), "sales_a": sales_a, "sales_b": sales_b,
        "cogs_g": _g(cogs_a, cogs_b), "cogs_a": cogs_a, "cogs_b": cogs_b,
        "np_g": _g(np_a, np_b), "np_a": np_a, "np_b": np_b,
        "oi": oi_a, "ebit": ebit_a,
    }

    # chart series (complete windows only; None => unavailable, never zero-filled)
    pct = lambda vals: [v * 100 for v in vals] if vals else None  # noqa: E731
    gm = _tail_complete(model, years, "Gross Margin", "Gross Profit")
    # Gross Margin may be absent as a ratio but derivable as Gross Profit/Sales
    if gm is None:
        gp = _tail_complete(model, years, "Gross Profit")
        sl = _tail_complete(model, years, "Sales", "Revenue", "Net Sales")
        if gp is not None and sl is not None and len(gp) == len(sl) == len(years):
            gm = [g / s if s else None for g, s in zip(gp, sl)]
            if any(v is None or pd.isna(v) for v in gm):
                gm = None
    em = _tail_complete(model, years, "EBITDA Margin", "EBITDA Margins")
    if em is None:
        eb = _tail_complete(model, years, "EBITDA")
        sl = _tail_complete(model, years, "Sales", "Revenue", "Net Sales")
        if eb is not None and sl is not None and len(eb) == len(sl) == len(years):
            em = [e / s if s else None for e, s in zip(eb, sl)]
            if any(v is None or pd.isna(v) for v in em):
                em = None
    om = _tail_complete(model, years, "EBIT Margin")
    if om is None:
        eb = _tail_complete(model, years, "EBIT (OPM)", "EBIT", "Operating Profit")
        sl = _tail_complete(model, years, "Sales", "Revenue", "Net Sales")
        if eb is not None and sl is not None and len(eb) == len(sl) == len(years):
            om = [e / s if s else None for e, s in zip(eb, sl)]
            if any(v is None or pd.isna(v) for v in om):
                om = None
    nm = _tail_complete(model, years, "Net Profit Margin", "Net Margins", "Net Margin")
    roe = _tail_complete(model, years, "Return on Equity (ROE) %", "ROE", "Return on Equity")
    roce = _tail_complete(model, years, "Return on Capital Employed (ROCE) %", "ROCE",
                          "Return on Capital Employed")
    roa = _tail_complete(model, years, "Return on Assets (ROA) %", "ROA", "Return on Assets")
    de = _tail_complete(model, years, "Debt to Equity Ratio", "Debt to Equity", "D/E")
    ic = _tail_complete(model, years, "Interest Coverage Ratio", "Interest Coverage")
    dd = _tail_complete(model, years, "Debtor Days")
    iv = _tail_complete(model, years, "Inventory Days")
    pay = _tail_complete(model, years, "Payable Days")
    ccc = _tail_complete(model, years, "Cash Conversion Cycle", "Cash Conversion Cycle (Days)")
    cfo = _tail_complete(model, years, "Cash from Operating Activity", "Cash from Operations")
    cfi = _tail_complete(model, years, "Cash from Investing Activity")
    cff = _tail_complete(model, years, "Cash from Financing Activity")

    # balance-sheet layers
    def _layer(*names):
        return _tail_complete(model, years, *names)

    assets = [("Net block", "#0F5B34", _layer("Net Block", "Fixed Assets")),
              ("Capital WIP", "#177245", _layer("Capital Work in Progress", "CWIP")),
              ("Investments", "#3D9E6B", _layer("Investments")),
              ("Other assets", "#6DBD93", _layer("Other Assets", "Other assets",
                                                 "Total Current Assets", "Current Assets"))]
    assets = [(n, c, v) for n, c, v in assets if v is not None]
    liab = [("Borrowings", "#8F3B31", _layer("Borrowings", "Total Debt")),
            ("Other liabilities", "#C9803A", _layer("Other Liabilities")),
            ("Reserves", "#3D9E6B", _layer("Reserves", "Reserves and Surplus"))]
    liab = [(n, c, v) for n, c, v in liab if v is not None]

    turnover: list[tuple[str, float, float]] = []
    for label, key in (("Debtor turnover", "Debtor Turnover Ratio"),
                       ("Creditor turnover", "Creditor Turnover Ratio"),
                       ("Inventory turnover", "Inventory Turnover"),
                       ("Fixed asset turnover", "Fixed Asset Turnover"),
                       ("Capital turnover", "Capital Turnover Ratio")):
        s = _num_series(model, key)
        if not s.empty:                         # annual periods only (drop TTM)
            s = s.reindex(years).dropna()
        s = s.tail(10)
        if len(s) >= 4:
            turnover.append((label, float(s.iloc[-1]), float(s.median())))

    period_txt = f"{years[0]}–{years[-1]} · {len(years)} years" if years else "—"
    try:
        derived = sorted([k for k, v in (getattr(model, "sections", {}) or {}).items()
                          if str(v).upper() == "DERIVED"])
    except Exception:
        derived = []

    return {
        "years": years, "scorecard": avail, "missing": missing,
        "pillars": pillars, "total": total, "verdict": verdict_word,
        "sector": result.sector.name, "company": model.company,
        "filename": model_filename or str(getattr(model.meta, "get", lambda *a: "")("filename", "") or ""),
        "period_txt": period_txt, "growth": growth,
        "margins": {"Gross": pct(gm), "EBITDA": pct(em), "EBIT": pct(om), "Net": pct(nm)},
        "returns": {"ROE": pct(roe), "ROCE": pct(roce), "ROA": pct(roa)},
        "de": de, "ic": ic, "dd": dd, "iv": iv, "pay": pay, "ccc": ccc,
        "cfo": cfo, "cfi": cfi, "cff": cff,
        "assets": assets, "liab": liab, "turnover": turnover,
        "gaps": list(getattr(result, "data_gaps", []) or []),
        "derived": derived,
    }


# --------------------------------------------------------------------------
# narratives (derived from the model; fall back honestly when data is missing)
# --------------------------------------------------------------------------

def _hero_why(ctx: dict) -> str:
    pillars = ctx["pillars"]
    if not pillars:
        return "Not enough scored data to explain this score yet."
    top, low = pillars[0], pillars[-1]
    # strongest / weakest scored ratios overall
    sc = ctx["scorecard"]
    top_r = sc[0] if sc else None
    low_r = sc[-1] if sc else None
    if top_r and low_r:
        return (f"A mixed picture: real strengths, but clear weak spots. <b>{escape(top['label'])}</b> carries the "
                f"score at {top['score']}, led by {escape(top_r['label'].lower())}. <b>{escape(low['label'])}</b> drags it "
                f"down at {low['score']}, where <b>{escape(low_r['label'])} scores {low_r['score']} of 100</b> — the single "
                f"largest deduction on the page.")
    return (f"<b>{escape(top['label'])}</b> leads at {top['score']}, while "
            f"<b>{escape(low['label'])}</b> trails at {low['score']}.")


def _read_margins(ctx: dict) -> str:
    m = ctx["margins"]
    if not any(m.values()):
        return "Margin history is unavailable in this workbook."
    ebit = (m.get("EBIT") or [])
    net = (m.get("Net") or [])
    if len(ebit) >= 2 and len(net) >= 2:
        ebit_down = ebit[-1] < ebit[-2]
        net_up = net[-1] > net[-2]
        if ebit_down and net_up:
            return ("Every tier widened over the history. But <b>EBIT fell this year while net margin rose</b> — the gap "
                    "between them is other income, not operations.")
        if ebit[-1] >= ebit[0] and net[-1] >= net[0]:
            return ("Margins widened over the period. The latest year kept that trend — "
                    f"<b>net margin closed at {net[-1]:.1f}%</b>.")
        return (f"Margins moved with the cycle and closed at <b>net {net[-1]:.1f}%</b> "
                f"against EBIT of {ebit[-1]:.1f}%.")
    return "Margin tiers are drawn straight from the model's history."


def _read_returns(ctx: dict) -> str:
    r = ctx["returns"]
    if not any(r.values()):
        return "Return ratios are unavailable in this workbook."
    def _last(k):
        v = r.get(k) or []
        return v[-1] if v else None
    roe, roce = _last("ROE"), _last("ROCE")
    if roe is not None and roce is not None:
        return (f"The spread that matters: <b>ROE closed at {roe:.1f}% while ROCE closed at {roce:.1f}%</b>. "
                "Equity returns and returns on the full capital base rarely move together here.")
    bits = ", ".join(f"{k} {v[-1]:.1f}%" for k, v in r.items() if v)
    return f"Latest returns: <b>{escape(bits)}</b>." if bits else "Return ratios are unavailable."


def _read_leverage(ctx: dict) -> str:
    de, ic = ctx["de"], ctx["ic"]
    if de is None and ic is None:
        return "Leverage history is unavailable in this workbook."
    parts = []
    if de:
        parts.append(f"gearing closed at {de[-1]:.2f}x")
    if ic:
        parts.append(f"<b>interest cover sits at {ic[-1]:.2f}x</b>")
    base = ", ".join(parts)
    if ic and max(ic) < 1.9 + 1e-9:
        return f"{base[0].upper() + base[1:]} — operating profit barely clears the interest bill, and it has little headroom across the history."
    return f"{base[0].upper() + base[1:]} across the model's history." if base else "Leverage history is unavailable."


def _read_cash(ctx: dict) -> str:
    cfo, cfi = ctx["cfo"], ctx["cff"]
    if not cfo:
        return "Cash-flow history is unavailable in this workbook."
    op, fin = cfo[-1], (ctx["cff"] or [None])[-1]
    inv = (ctx["cfi"] or [None])[-1]
    if op is not None and inv is not None:
        return (f"Operating cash closed at {_fmt_cr(op)} while investing outflows hit {_fmt_cr(inv)}. "
                "<b>The gap is being filled by financing</b>, not by the business." if (fin or 0) > 0
                else f"Operating cash closed at {_fmt_cr(op)} against investing flows of {_fmt_cr(inv)}.")
    return f"Operating cash closed at {_fmt_cr(op)}."


def _read_wc(ctx: dict) -> str:
    if ctx["ccc"] is None:
        return "Working-capital days are unavailable in this workbook."
    cyc = ctx["ccc"][-1]
    pay = (ctx["pay"] or [None])[-1]
    inv = (ctx["iv"] or [None])[-1]
    extra = f", with payables stretched to {pay:.0f} days" if pay is not None else ""
    watch = f" Inventory days at {inv:.0f} is the thing to watch." if inv is not None else ""
    fund = " Suppliers fund the operation." if cyc < 0 else ""
    return (f"A <b>cash cycle of {cyc:.0f} days</b>{extra}.{fund}{watch}")


def _read_turnover(ctx: dict) -> str:
    rows = ctx["turnover"]
    if not rows:
        return "Turnover history is unavailable in this workbook."
    below = sum(1 for _, a, m in rows if a < m)
    fat = next(((a, m) for n, a, m in rows if "Fixed" in n), None)
    if fat:
        a, m = fat
        return (f"{below} of {len(rows)} turns sit below their own history. "
                f"<b>Fixed-asset turnover at {a:.2f}x against a {m:.2f}x median</b> is what constrains capital returns.")
    return f"{below} of {len(rows)} turns sit below their own history."


def _read_assets(ctx: dict) -> str:
    layers = {n: v for n, _, v in ctx["assets"]}
    nb = layers.get("Net block")
    cwip = layers.get("Capital WIP")
    if nb is None:
        return "Asset history is unavailable in this workbook."
    if cwip is not None:
        return (f"Net block grew from {_fmt_cr(nb[0])} to {_fmt_cr(nb[-1])} across the window, with a further "
                f"{_fmt_cr(cwip[-1])} still in capital work in progress — not yet earning.")
    return f"Net block moved from {_fmt_cr(nb[0])} to {_fmt_cr(nb[-1])} across the window."


def _read_liab(ctx: dict) -> str:
    layers = {n: v for n, _, v in ctx["liab"]}
    bo = layers.get("Borrowings")
    if bo is None:
        return "Funding history is unavailable in this workbook."
    de = ctx["de"]
    if de and de[-1] < de[0] and bo[-1] > bo[0]:
        return (f"Borrowings rose from {_fmt_cr(bo[0])} to {_fmt_cr(bo[-1])}. Reserves grew too, which is what pulled D/E back down "
                "— the debt itself never fell.")
    return f"Borrowings moved from {_fmt_cr(bo[0])} to {_fmt_cr(bo[-1])} across the window."


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------

def _js_arr(vals: list | None, scale: float = 1.0, nd: int = 2) -> str:
    if not vals:
        return "[]"
    return "[" + ",".join(f"{v * scale:.{nd}f}" for v in vals) + "]"


def _data_js(ctx: dict) -> str:
    Y = ctx["years"]
    sc = ctx["scorecard"]
    # 4th/5th fields carry the per-ratio score explanation (inline panel) and a
    # short hover tooltip. The template's scorecard builder reads only r[0..2];
    # the (i)-button script in this module consumes r[3]/r[4].
    sco = [[r["label"], r["score"], r["display"], r.get("explain", ""), r.get("tip", "")]
           for r in sc]
    m = ctx["margins"]
    margins = [[n, c, vals] for n, c, vals in
               [("Gross", "#3D9E6B", m.get("Gross")), ("EBITDA", "#177245", m.get("EBITDA")),
                ("EBIT", "#0F3D27", m.get("EBIT")), ("Net", "#D9A441", m.get("Net"))]
               if vals]
    r = ctx["returns"]
    rets = [[n, c, vals] for n, c, vals in
            [("ROE", "#177245", r.get("ROE")), ("ROCE", "#D9A441", r.get("ROCE")),
             ("ROA", "#9ECFB4", r.get("ROA"))] if vals]
    cash = [[n, c, vals] for n, c, vals in
            [("Operating", "#3D9E6B", ctx["cfo"]), ("Investing", "#B4483C", ctx["cfi"]),
             ("Financing", "#9ECFB4", ctx["cff"])] if vals]
    wc_up = [[n, c, vals] for n, c, vals in
             [("Debtor days", "#3D9E6B", ctx["dd"]), ("Inventory days", "#9ECFB4", ctx["iv"])]
             if vals]
    assets = [[n, c, vals] for n, c, vals in ctx["assets"]]
    liab = [[n, c, vals] for n, c, vals in ctx["liab"]]
    to = [[n, round(a, 2), round(med, 2), "x"] for n, a, med in ctx["turnover"]]

    def _s(v):
        return json.dumps(v)

    lines = [
        f"var Y = {_s(Y)};",
        f"var SCORECARD = {_s(sco)};",
        "var MARGINS = [%s];" % ",".join(
            f'[{_s(n)},{_s(c)},{_js_arr(v, 1.0, 2)}]' for n, c, v in margins) if margins else "var MARGINS = [];",
        "var RETURNS = [%s];" % ",".join(
            f'[{_s(n)},{_s(c)},{_js_arr(v, 1.0, 2)}]' for n, c, v in rets) if rets else "var RETURNS = [];",
        f"var LEV_BAR  = {json.dumps(['Debt / equity', '#9ECFB4', ctx['de'] or []])};",
        f"var LEV_LINE = {json.dumps(['Interest cover', '#177245', [round(v, 2) for v in (ctx['ic'] or [])]])};",
        "var CASH = [%s];" % ",".join(
            f'[{_s(n)},{_s(c)},{_js_arr(v, 1.0, 0)}]' for n, c, v in cash) if cash else "var CASH = [];",
        "var WC_UP = [%s];" % ",".join(
            f'[{_s(n)},{_s(c)},{_js_arr(v, 1.0, 1)}]' for n, c, v in wc_up) if wc_up else "var WC_UP = [];",
        f"var WC_DOWN = {json.dumps(['Payable days', '#D9A441', [round(v, 1) for v in (ctx['pay'] or [])]])};",
        f"var WC_LINE = {json.dumps(['Cash cycle', '#0F3D27', [round(v, 1) for v in (ctx['ccc'] or [])]])};",
        "var ASSETS = [%s];" % ",".join(
            f'[{_s(n)},{_s(c)},{_js_arr(v, 1.0, 0)}]' for n, c, v in assets) if assets else "var ASSETS = [];",
        "var LIAB = [%s];" % ",".join(
            f'[{_s(n)},{_s(c)},{_js_arr(v, 1.0, 0)}]' for n, c, v in liab) if liab else "var LIAB = [];",
        f"var TURNOVER = {_s(to)};",
    ]
    return "\n".join(lines)


def _render_js(ctx: dict) -> str:
    """Conditional render calls mirroring the template; unavailable charts are
    skipped (their card shows an 'unavailable' note instead)."""
    has = {
        "margins": bool(ctx["margins"].get("Gross") or ctx["margins"].get("EBITDA")),
        "returns": bool(ctx["returns"].get("ROE") or ctx["returns"].get("ROCE")),
        "lev": bool(ctx["de"] or ctx["ic"]),
        "cash": bool(ctx["cfo"]),
        "wc": bool(ctx["dd"] or ctx["iv"] or ctx["ccc"]),
        "assets": len(ctx["assets"]) >= 1,
        "liab": len(ctx["liab"]) >= 1,
    }
    parts = [
        'var pc  = function(v){ return v.toFixed(1) + "%"; };',
        'var pcY = function(v){ return Math.round(v) + "%"; };',
        'var cr  = function(v){ return Math.round(v).toLocaleString("en-IN"); };',
        'var crY = function(v){ return Math.abs(v) >= 1000 ? Math.round(v / 1000) + "k" : Math.round(v); };',
        'var xx  = function(v){ return v.toFixed(2) + "x"; };',
        'var xxY = function(v){ return v.toFixed(1) + "x"; };',
        "",
    ]
    if has["margins"]:
        parts.append('legend("lg-margins", MARGINS);              lineChart("ch-margins", "svMargins", MARGINS, pc, pcY);')
    if has["returns"]:
        parts.append('legend("lg-returns", RETURNS);              lineChart("ch-returns", "svReturns", RETURNS, pc, pcY);')
    if has["lev"] and ctx["de"] and ctx["ic"]:
        parts.append('legend("lg-lev", [LEV_BAR, LEV_LINE], "sq");barLineChart("ch-lev", "svLev", LEV_BAR, LEV_LINE, xx, xxY);')
    if has["cash"]:
        parts.append('legend("lg-cash", CASH, "sq");              groupedBars("ch-cash", "svCash", CASH, cr, crY);')
    if has["wc"] and ctx["ccc"]:
        parts.append('legend("lg-wc", [WC_UP[0], WC_UP[1], WC_DOWN, WC_LINE].filter(function(s,i,a){return s && s[2] && s[2].length;}), "sq"); try{wcChart("ch-wc", "svWc");}catch(e){}')
    elif has["wc"]:
        parts.append('legend("lg-wc", [WC_UP[0], WC_UP[1], WC_DOWN].filter(function(s){return s && s[2] && s[2].length;}), "sq");')
    if has["assets"]:
        parts.append('legend("lg-assets", ASSETS, "sq");          stackedArea("ch-assets", "svAssets", ASSETS, cr, crY);')
    if has["liab"]:
        parts.append('legend("lg-liab", LIAB, "sq");              stackedArea("ch-liab", "svLiab", LIAB, cr, crY);')
    return "\n".join(parts)


# Score-explanation UI — appended only to the scorecard ("Every ratio, scored
# against its sector band"). No other section shows these explanations.
_EXP_CSS = """
.scr .nm{display:flex;align-items:center;justify-content:flex-end;gap:7px}
.scr .exi{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;
  border-radius:50%;border:1.3px solid #C4CCC6;background:#fff;color:#8B918E;font-size:10px;
  font-weight:700;font-style:italic;font-family:Georgia,'Times New Roman',serif;line-height:1;
  cursor:pointer;flex:none;padding:0;transition:border-color .12s ease,color .12s ease,background .12s ease}
.scr .exi:hover,.scr.open .exi{border-color:var(--brand);color:var(--brand);background:var(--pos-tint)}
.scr.open{background:#F4F8F5;border-radius:7px}
.scr-exp{background:#FBFCFB;border:1px solid var(--line);border-radius:12px;
  padding:12px 14px;margin:2px 0 8px;font-size:12px;line-height:1.6;color:var(--ink-3)}
.scr-exp .exgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
  gap:9px 16px;padding-bottom:9px;border-bottom:1px solid var(--line-2)}
.scr-exp .exk{font-family:var(--mono);font-size:8.5px;font-weight:700;letter-spacing:1px;
  text-transform:uppercase;color:var(--mute-2)}
.scr-exp .exv{font-size:12px;font-weight:700;color:var(--ink-2);padding-top:2px}
.scr-exp .exwhy{padding-top:9px}
.scr-exp .exwhy b{color:var(--ink);font-weight:700}
"""

_EXP_JS = r"""
(function(){
  if (typeof SCORECARD === 'undefined') return;
  var host = document.getElementById('scorecard'); if (!host) return;
  var EX = SCORECARD.map(function(r){ return r[3] || ""; });
  var k = 0;
  host.querySelectorAll('.scr').forEach(function(row){
    var panel = EX[k++]; if (!panel) return;
    var nm = row.querySelector('.nm') || row;

    var btn = document.createElement('button');
    btn.type = 'button'; btn.className = 'exi'; btn.textContent = 'i';
    btn.setAttribute('aria-label', 'How this score is built');
    btn.setAttribute('aria-expanded', 'false');
    nm.appendChild(btn);

    var det = document.createElement('div');
    det.className = 'scr-exp'; det.hidden = true; det.innerHTML = panel;
    row.parentNode.insertBefore(det, row.nextSibling);

    btn.addEventListener('click', function(e){
      e.stopPropagation();
      var open = det.hidden;            // currently hidden -> opening
      det.hidden = !open;
      row.classList.toggle('open', open);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  });
})();
"""


def _unavail(msg: str = "Unavailable — not in this workbook") -> str:
    return (f'<div style="padding:26px 6px;color:#9AA09D;font-size:12.5px;line-height:1.6">'
            f'{escape(msg)} — no score assigned, nothing assumed.</div>')


def render(model, result, model_filename: str = "") -> tuple[str, int]:
    ctx = build_context(model, result, model_filename)
    css = _css()
    big_js, fit_js = _scripts()
    mid = _bigjs_mid(big_js)
    data_js = _data_js(ctx)
    render_js = _render_js(ctx)

    total, verdict = ctx["total"], ctx["verdict"]
    band_cls = verdict.lower()
    band_colors = {"strong": ("#2F9E63", "#0F5B34"), "neutral": ("#D9A441", "#B5761F"),
                   "weak": ("#B4483C", "#B4483C")}
    _bar, band_txt = band_colors.get(band_cls, ("#D9A441", "#B5761F"))

    pillars_html = "".join(
        f'<div class="prow"><span>{escape(p["label"])}</span>'
        f'<div class="ptrack"><i style="width:{p["score"]}%;background:{_bar if False else ("#B4483C" if p["score"] < 40 else "#D9A441" if p["score"] < 66 else "#2F9E63")}"></i></div>'
        f'<b style="color:{("#B4483C" if p["score"] < 40 else "#D9A441" if p["score"] < 66 else "#2F9E63")}">{p["score"]}</b></div>'
        for p in ctx["pillars"])

    g = ctx["growth"]

    def _tile(label, gv, av, bv):
        if gv is None or av is None or bv is None:
            return (f'<div class="gt"><div class="gt-l">{label}</div>'
                    f'<div class="gt-v"><b>—</b></div>'
                    f'<div class="gt-f"><span>unavailable</span></div></div>')
        col = "var(--pos-deep)" if gv >= 0 else "var(--neg)"
        if "COGS" in label:
            col = "var(--warn-deep)" if gv >= 0 else "var(--pos-deep)"
        return (f'<div class="gt"><div class="gt-l">{label}</div>'
                f'<div class="gt-v"><b style="color:{col}">{gv:+.1f}%</b></div>'
                f'<div class="gt-f"><b>{_fmt_cr(av)}</b><i></i><span>from {_fmt_cr(bv)}</span></div></div>')

    oi_tile: str
    if g["oi"] is None:
        oi_tile = ('<div class="gt"><div class="gt-l">Other income</div>'
                   '<div class="gt-v"><b>—</b></div>'
                   '<div class="gt-f"><span>unavailable</span></div></div>')
    else:
        flag = g["ebit"] is not None and g["oi"] is not None and g["oi"] >= (g["ebit"] or 0)
        note = (f'<p class="gt-note">Exceeds EBIT of {_fmt_cr(g["ebit"])}. Profit growth this year is not coming '
                'from the operating business.</p>') if flag else ""
        cls = "gt flag" if flag else "gt"
        oi_tile = (f'<div class="{cls}"><div class="gt-l">Other income, {escape(ctx["years"][-1]) if ctx["years"] else ""}</div>'
                   f'<div class="gt-v"><b style="color:#B5761F">{_fmt_cr(g["oi"])}</b></div>{note}</div>')

    head_sub = f"Why the score reads {total}, and the evidence behind it."
    if ctx["company"] and ctx["company"].lower() != "unknown company":
        head_sub = f"{escape(ctx['company'].title())} — why the score reads {total}, and the evidence behind it."

    fname = escape(ctx["filename"] or model_filename or "uploaded model")
    period = escape(ctx["period_txt"])
    sector = escape(ctx["sector"])

    def _card(title, sub, read, legend_id, chart_id, ok: bool, empty_msg: str = "") -> str:
        inner = f'<div class="legend" id="{legend_id}"></div><div class="chart" id="{chart_id}"></div>' if ok \
            else _unavail(empty_msg or f"{title} history is unavailable in this workbook")
        return (f'<section class="card"><div class="c-h"><div class="c-t">{title}</div>'
                f'<div class="c-s">{sub}</div></div>'
                f'<p class="c-read">{read}</p>{inner}</section>')

    body = f"""
  <div class="phead">
    <div>
      <h1>Ratio deep dive</h1>
      <div class="sub">{head_sub}</div>
    </div>
    <div class="feed">
      <div><div class="feed-l">Model</div><div class="feed-v">{fname}</div></div>
      <div><div class="feed-l">Periods</div><div class="feed-v">{period}</div></div>
      <div><div class="feed-l">Sector bands</div><div class="feed-v">{sector}</div></div>
    </div>
  </div>

  <section class="hero">
    <div class="h-left">
      <div class="h-eyebrow">Funda score</div>
      <div class="score"><b>{total}</b><small>/100</small><span class="band">{escape(verdict)}</span></div>
      <p class="h-why">{_hero_why(ctx)}</p>
      <div class="pillars">
        <div class="p-lab">Area scores, weighted for this sector</div>
        {pillars_html}
      </div>
    </div>
    <div class="h-right">
      <div class="h-right-h">
        <div class="h-right-t">Every ratio, scored against its sector band</div>
        <div class="h-right-s">Score 0–100, sorted strongest first</div>
      </div>
      <div class="sc-axis">
        <span></span>
        <div class="ticks"><u style="left:0">0</u><u style="left:40%">Weak 40</u><u style="left:66%">Strong 66</u><u style="left:100%">100</u></div>
        <span></span>
      </div>
      <div id="scorecard"></div>
      <div class="sc-foot">
        <b>How to read it</b>
        <span>— the bar is the 0–100 score; the figure on the right is the raw ratio. Band tints mark where this sector's weak and strong thresholds sit. <b>Click any ratio</b> to see how its score is built.</span>
      </div>
    </div>
  </section>

  <div class="sechead"><h2>Growth</h2><span class="rule"></span><span class="sc" style="color:#B5761F">Growth {next((p['score'] for p in ctx['pillars'] if p['key'] == 'growth'), '—')}</span></div>
  <section class="gstrip">
    {_tile("Revenue growth", g["sales_g"], g["sales_a"], g["sales_b"])}
    {_tile("COGS growth", g["cogs_g"], g["cogs_a"], g["cogs_b"])}
    {_tile("Net profit growth", g["np_g"], g["np_a"], g["np_b"])}
    {oi_tile}
  </section>

  <div class="sechead"><h2>Profitability &amp; returns</h2><span class="rule"></span><span class="sc" style="color:#B5761F">Profitability {next((p['score'] for p in ctx['pillars'] if p['key'] == 'profitability'), '—')}</span><span class="sc" style="color:#B4483C">Returns {next((p['score'] for p in ctx['pillars'] if p['key'] == 'returns'), '—')}</span></div>
  <div class="g2">
    {_card("Margin ladder", "Gross → EBITDA → EBIT → Net", _read_margins(ctx), "lg-margins", "ch-margins", bool(ctx["margins"].get("Gross") or ctx["margins"].get("EBITDA")))}
    {_card("Returns on capital", "ROE · ROCE · ROA", _read_returns(ctx), "lg-returns", "ch-returns", bool(ctx["returns"].get("ROE") or ctx["returns"].get("ROCE")))}
  </div>

  <div class="sechead"><h2>Leverage &amp; cash</h2><span class="rule"></span><span class="sc" style="color:#B5761F">Leverage {next((p['score'] for p in ctx['pillars'] if p['key'] == 'leverage'), '—')}</span></div>
  <div class="g2">
    {_card("Leverage &amp; solvency", "Debt/equity bars · interest cover line", _read_leverage(ctx), "lg-lev", "ch-lev", bool(ctx["de"] and ctx["ic"]))}
    {_card("Cash flow mix", "Operating · investing · financing, ₹ cr", _read_cash(ctx), "lg-cash", "ch-cash", bool(ctx["cfo"]))}
  </div>

  <div class="sechead"><h2>Efficiency</h2><span class="rule"></span><span class="sc" style="color:#B5761F">Efficiency {next((p['score'] for p in ctx['pillars'] if p['key'] == 'efficiency'), '—')}</span></div>
  <div class="g2">
    {_card("Working capital cycle", "Debtor + inventory − payable, days", _read_wc(ctx), "lg-wc", "ch-wc", bool(ctx["ccc"]))}
    <section class="card">
      <div class="c-h"><div class="c-t">Turnover</div><div class="c-s">Latest versus the ten-year median</div></div>
      <p class="c-read">{_read_turnover(ctx)}</p>
      <div id="bullets" style="padding-top:6px"></div>
    </section>
  </div>

  <div class="sechead"><h2>Balance sheet</h2><span class="rule"></span><span class="sc" style="color:var(--mute-2)">Context</span></div>
  <div class="g2">
    {_card("Total assets, by component", "Stacked, ₹ crore", _read_assets(ctx), "lg-assets", "ch-assets", len(ctx["assets"]) >= 1)}
    {_card("Total liabilities &amp; equity", "Stacked, ₹ crore", _read_liab(ctx), "lg-liab", "ch-liab", len(ctx["liab"]) >= 1)}
  </div>

  <div class="foot">
    <span class="foot-l">Source</span>
    <span class="foot-v">Uploaded model only — {fname}, {period}{" · includes derived rows (" + escape(", ".join(ctx["derived"][:6])) + (", …" if len(ctx["derived"]) > 6 else "") + ")" if ctx.get("derived") else ""}{" · unavailable: " + escape(", ".join(ctx["missing"][i]["label"] for i in range(min(4, len(ctx["missing"])))) ) + ("…" if len(ctx["missing"]) > 4 else "") if ctx.get("missing") else ""}</span>
    <span class="foot-r">Scores blended by sector weight · higher is better throughout</span>
  </div>"""

    doc = ( '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>FundaCheck — Ratio deep dive</title>'
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            '<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">'
            f"<style>{css}{_EXP_CSS}</style></head><body><div id=\"shell\">{body}</div>"
            '<div id="tip"></div>'
            f"<script>\n{data_js}\n{mid}\n{render_js}\n</script>"
            f"<script>{_EXP_JS}</script>"
            f"<script>{fit_js}</script>"
            "</body></html>")
    n_cards = 8
    height = 1500 + 180 * n_cards
    return doc, min(4200, max(1800, height))
