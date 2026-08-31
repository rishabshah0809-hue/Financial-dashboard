"""
design_blocks.py
----------------
HTML panels drawn to match the FundaCheck design canvas.

Some of the design's panels are simpler and sharper as hand-built HTML than as
Plotly figures: the revenue trend is a row of rounded pill bars, the key-ratio
list is a label/value stack, the health gauge is one arc. Building them the way
the design does keeps the radii, weights and spacing exactly on-spec, and they
render instantly.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .scoring import Assessment

GREEN = "#177245"
GREEN_DARK = "#0f5b34"
INK = "#15201a"
MUTED = "#8b918e"
FAINT = "#9aa09d"
# the design's green ramp, oldest (palest) to latest (deepest)
RAMP = ["#cde5d8", "#a9d3bd", "#6dbd93", "#2b8b57", "#177245", "#0f5b34"]


def _crore(value: float) -> str:
    """Indian money formatting: a lakh crore reads better than eight digits."""
    if abs(value) >= 1e5:
        return f"{value / 1e5:.2f}L cr"
    return f"{value:,.0f} cr"


def revenue_trend(model, years: int = 6) -> str:
    """
    Sales as rounded pill bars, the tallest one labelled.

    Bar height is proportional to the value, so the shape is honest; the colour
    ramp only reinforces recency and carries no separate meaning.
    """
    sales = pd.to_numeric(model.series("Sales"), errors="coerce").dropna()
    # summary columns (TTM etc.) never belong in a fiscal-year trend
    sales = sales[[str(i).upper() not in ("TTM", "TREND", "MEAN", "MEDIAN")
                   for i in sales.index]].tail(years)
    if sales.empty:
        return ""

    peak = float(sales.max()) or 1.0
    tallest = sales.idxmax()
    bars = []
    for index, (period, value) in enumerate(sales.items()):
        height = max(16, round(float(value) / peak * 120))
        colour = RAMP[min(index, len(RAMP) - 1)]
        if period == tallest:
            colour = GREEN_DARK
        label = ""
        if period == tallest:
            label = (
                f'<div class="pill-tag">{_crore(float(value))}</div>'
            )
        last = "font-weight:700;color:#15201a" if index == len(sales) - 1 else ""
        tip = (f' data-tt="{period}: {_crore(float(value))} sales'
               f'{"" if value != peak else " - peak year"}"')
        bars.append(
            f'<div class="pill-col">'
            f'<div class="pill-wrap">{label}'
            f'<div class="pill"{tip} style="height:{height}px;background:{colour};'
            f'cursor:pointer"></div></div>'
            f'<span style="{last}">{period}</span></div>'
        )

    # The card supplies the heading, so this block only carries the note.
    return (
        f'<div class="pill-head"><span class="pill-note">₹ crore · '
        f'{sales.index[0]}–{sales.index[-1]}</span></div>'
        f'<div class="pill-row">{"".join(bars)}</div>'
    )


def key_ratios(model, result: Assessment) -> str:
    """The design's Key Ratios list: name, the family it belongs to, and value."""
    rows = [
        ("Gross Margin", "Profitability", "Gross Margin", "pct"),
        ("ROCE", "Efficiency", "Return on Capital Employed (ROCE) %", "pct"),
        ("Interest Coverage", "Solvency", "Interest Coverage Ratio", "x"),
        ("EBITDA Margin", "Operating", "EBITDA Margin", "pct"),
        ("Cash Cycle", "Working capital", "Cash Conversion Cycle", "days"),
        ("Debtor Days", "Collections", "Debtor Days", "days"),
    ]
    items = []
    for label, family, metric, unit in rows:
        series = pd.to_numeric(model.series(metric), errors="coerce").dropna()
        if series.empty:
            continue
        value = float(series.iloc[-1])
        if unit == "pct":
            text = f"{value * 100:.1f}%"
        elif unit == "x":
            text = f"{value:.2f}x"
        else:
            text = f"{value:.0f} d"
        items.append(
            f'<div class="kr-row"><div><div class="kr-name">{label}</div>'
            f'<div class="kr-fam">{family}</div></div>'
            f'<span class="kr-val">{text}</span></div>'
        )
    return f'<div class="kr-list">{"".join(items)}</div>'


def health_gauge(result: Assessment) -> str:
    """
    The composite score as the design's half-arc health gauge.

    A semicircle rather than a full ring: it reads as a dial, and the three
    bands under it name what the colours mean so the reading never rests on
    colour alone.
    """
    score = max(0.0, min(100.0, float(result.total_score)))
    radius, cx, cy = 100.0, 118.0, 118.0
    length = np.pi * radius                      # half circumference
    filled = length * score / 100.0
    label = "Strong" if score >= 66 else "Stable" if score >= 40 else "At risk"

    return f'''
    <div class="gauge-wrap">
      <svg viewBox="0 0 236 132" role="img"
           aria-label="Health index {score:.0f} of 100 — {label}">
        <path d="M18,118 A100,100 0 0,1 218,118" fill="none"
              stroke="#eceeec" stroke-width="17" stroke-linecap="round"/>
        <path d="M18,118 A100,100 0 0,1 218,118" fill="none"
              stroke="{result.colour}" stroke-width="17" stroke-linecap="round"
              stroke-dasharray="{filled:.1f} {length:.1f}"/>
      </svg>
      <div class="gauge-read">
        <div class="gauge-score">{label}</div>
        <div class="gauge-sub">Health index · {score:.0f}/100</div>
      </div>
    </div>
    <div class="gauge-legend">
      <span><i style="background:#3d9e6b"></i>Strong 66+</span>
      <span><i style="background:#d9a441"></i>Stable 40–66</span>
      <span><i style="background:#a4483f"></i>At risk &lt;40</span>
    </div>'''


def valuation_panel(model) -> str:
    """
    Valuation: the design's market-cap donut when the workbook supplies one,
    above the multiples against the company's own 10-year median.
    """
    items = []
    for label, metric, unit in (("P/E Ratio", "PE Ratio", "x"),
                                ("Price to Sales", "Price to Sales", "x")):
        series = pd.to_numeric(model.series(metric), errors="coerce").dropna()
        series = series[(series > 0) & (series < 1000)]
        if series.empty:
            continue
        latest, median = float(series.iloc[-1]), float(series.median())
        cheaper = latest < median
        arrow = "▼" if cheaper else "▲"
        tone = "kr-good" if cheaper else "kr-warn"
        items.append(
            f'<div class="kr-row"><div><div class="kr-name">{label}</div>'
            f'<div class="kr-fam">own median {median:.1f}{unit}</div></div>'
            f'<span class="kr-val">{latest:.1f}{unit} '
            f'<span class="{tone}">{arrow}</span></span></div>'
        )

    # --- market cap donut: cap / revenue / net income ---
    mcap = model.meta.get("market_cap")
    sales = pd.to_numeric(model.series("Sales"), errors="coerce").dropna()
    profit = pd.to_numeric(model.series("Net Profit"), errors="coerce").dropna()
    donut = ""
    if mcap and not sales.empty and not profit.empty:
        rev, net = float(sales.iloc[-1]), float(profit.iloc[-1])
        if min(rev, net) > 0 < mcap:
            segs = [("Market Cap", mcap, "#9aa09d"),
                    ("Revenue", rev, "#177245"),
                    ("Net income", net, "#5fd0a0")]
            total = sum(v for _, v, _ in segs)
            r, circ = 54.0, 2 * np.pi * 54.0
            acc = 0.0
            rings = []
            for name, value, colour in segs:
                dash = value / total * circ
                rings.append(
                    f'<circle cx="70" cy="70" r="{r}" fill="none" stroke="{colour}" '
                    f'stroke-width="17" stroke-dasharray="{dash:.2f} {circ - dash:.2f}" '
                    f'transform="rotate({-90 + acc / total * 360:.2f} 70 70)"/>'
                )
                acc += value
            centre = _crore(mcap).replace(" cr", "")
            donut = (
                '<div class="val-donut">'
                '<div class="val-ring"><svg width="140" height="140" viewBox="0 0 140 140">'
                f'<circle cx="70" cy="70" r="{r}" fill="none" stroke="#f0f2f0" stroke-width="17"/>'
                f'{"".join(rings)}</svg>'
                f'<div class="val-centre"><div class="val-big">₹{centre}</div>'
                '<div class="val-small">crore mcap</div></div></div>'
                '<div class="val-rows">' + "".join(
                    f'<div class="kr-row"><div><div class="kr-name">{n}</div></div>'
                    f'<span class="val-leg"><i style="background:{c}"></i>'
                    f'{_crore(v)}</span></div>'
                    for n, v, c in segs) + "</div></div>"
            )

    if not items and not donut:
        return ""
    return donut + (f'<div class="kr-list">{"".join(items)}</div>' if items else "")


def _latest(model, *names: str) -> float | None:
    for name in names:
        series = pd.to_numeric(model.series(name), errors="coerce").dropna()
        if not series.empty:
            return float(series.iloc[-1])
    return None


def _full_year_col(model) -> str | None:
    """Most recent complete fiscal year — TTM/summary columns are skipped."""
    for col in reversed(model.years):
        if str(col).upper() in ("TTM", "TREND", "MEAN", "MEDIAN"):
            continue
        return col
    return model.latest_year or None


def _col(model, col, *names):
    for name in names:
        for frame in (model.historical, model.ratios):
            if not frame.empty and name in frame.index and col in frame.columns:
                v = pd.to_numeric(frame.loc[name, col], errors="coerce")
                if pd.notna(v):
                    return float(v)
    return None


def _escape_tt(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def income_sankey(model):
    """
    'How <FY> revenue becomes profit' — the income-statement flow as inline SVG,
    matching the FundaCheck design. Uses the last full fiscal year and derives
    gross as sales - COGS so a blank Gross Margin cell cannot zero the flow.
    Returns (title, svg).
    """
    year = _full_year_col(model)
    if year is None:
        return ("", "")
    sales = _col(model, year, "Sales")
    other = _col(model, year, "Other Income ", "Other Income") or 0.0
    cogs = _col(model, year, "COGS")
    pbt = _col(model, year, "Earnings Before Tax", "Profit before tax")
    net = _col(model, year, "Net Profit", "Net profit")
    tax = _col(model, year, "Tax")
    if None in (sales, cogs, pbt, net) or not sales:
        return ("", "")
    if tax is None:
        tax = max(0.0, pbt - net)
    gross = sales - cogs
    bucket = max(0.0, gross + other - pbt)
    title = f"How {year} revenue becomes profit"

    w, hh, top, gap = 620, 340, 34, 40
    peak = sales + other or 1.0
    scale = 232.0 / peak
    S = lambda v: v * scale
    x0, x1, x2, x3 = 96, 204, 336, 468

    def flow(xa, ya, xb, yb, ht, colour, op):
        mid = (xa + xb) / 2
        d = (f"M {xa} {ya} C {mid} {ya}, {mid} {yb}, {xb} {yb} "
             f"L {xb} {yb + ht} C {mid} {yb + ht}, {mid} {ya + ht}, {xa} {ya + ht} Z")
        return f'<path d="{d}" fill="{colour}" opacity="{op}"/>'

    def bar(x, y, ht, colour, label, val, sub, anchor):
        """
        Reference-clean labels: the name always shows, but the number is
        reserved for hover - except Sales and Net Profit, which carry their
        value on the bar at all times.
        """
        tx = x - 7 if anchor == "end" else x + 15
        show_value = label in ("Sales", "Net profit")
        tip_text = f"{label} \u00b7 {val}" + (f" \u00b7 {sub}" if sub else "")
        parts = [
            f'<rect data-tt="{_escape_tt(tip_text)}" x="{x}" y="{y}" width="9" '
            f'height="{max(2, ht):.1f}" rx="2" fill="{colour}" '
            f'style="cursor:pointer"/>',
            f'<text x="{tx}" y="{y + 10:.1f}" text-anchor="{anchor}" '
            f'font-size="10.5" font-weight="700" fill="{INK}">{label}</text>',
        ]
        if show_value:
            parts.append(
                f'<text x="{tx}" y="{y + 22:.1f}" text-anchor="{anchor}" '
                f'font-size="10" fill="#7d847f" '
                f'font-family="ui-monospace,monospace">{val}</text>'
            )
        return "".join(parts)

    y_sales, y_other = top, top + S(sales) + gap
    g_top, c_top = top, top + S(gross) + gap
    p_top, b_top = top, top + S(pbt) + gap
    n_top, t_top = top, top + S(net) + gap

    flows = "".join([
        flow(x0 + 9, y_sales, x1, g_top, S(gross), "#3d9e6b", 0.30),
        flow(x0 + 9, y_sales + S(gross), x1, c_top, S(cogs), "#c9803a", 0.26),
        flow(x0 + 9, y_other, x2, p_top, S(other), "#5fd0a0", 0.30),
        flow(x1 + 9, g_top, x2, p_top + S(other), S(pbt) - S(other), "#177245", 0.32),
        flow(x1 + 9, g_top + S(pbt) - S(other), x2, b_top, S(bucket), "#b4483c", 0.24),
        flow(x2 + 9, p_top, x3, n_top, S(net), "#177245", 0.38),
        flow(x2 + 9, p_top + S(net), x3, t_top, S(tax), "#b4483c", 0.30),
    ])
    rate = f"{tax / pbt * 100:.1f}% rate" if pbt else None
    g_margin = f"{gross / sales * 100:.1f}% margin" if sales else None
    p_margin = f"{pbt / sales * 100:.1f}% margin" if sales else None
    r = lambda v: f"₹{v:,.0f}"
    bars = "".join([
        bar(x0, y_sales, S(sales), "#9aa09d", "Sales", r(sales), None, "end"),
        bar(x0, y_other, S(other), "#5fd0a0", "Other income", r(other), None, "end"),
        bar(x1, g_top, S(gross), "#3d9e6b", "Gross profit", r(gross), g_margin, "start"),
        bar(x1, c_top, S(cogs), "#c9803a", "Cost of goods", r(cogs), None, "start"),
        bar(x2, p_top, S(pbt), "#177245", "Profit before tax", r(pbt), p_margin, "start"),
        bar(x2, b_top, S(bucket), "#b4483c", "Opex, dep. & interest", r(bucket), None, "start"),
        bar(x3, n_top, S(net), "#177245", "Net profit", r(net), None, "start"),
        bar(x3, t_top, S(tax), "#b4483c", "Tax", r(tax), rate, "start"),
    ])
    svg = (
        f'<div style="display:flex;justify-content:center">'
        f'<svg viewBox="0 0 {w} {hh}" width="100%" '
        f'style="max-width:760px;height:auto;display:block;margin:0 auto" '
        f'role="img" aria-label="Income statement flow">{flows}{bars}</svg></div>'
    )
    return (title, svg)


def score_drivers(result: Assessment) -> str:
    """
    'What moves the score' — each ratio's 0-100 sub-score as a mini bar.

    The panel beside the verdict in the design: the metrics that pull the
    composite up and down, strongest first.
    """
    ranked = sorted(result.metrics, key=lambda m: m.score, reverse=True)
    rows = []
    for m in ranked:
        colour = GREEN if m.score >= 70 else "#b5761f" if m.score >= 45 else "#a4483f"
        rows.append(
            f'<div class="drv-row"><span class="drv-name">{m.metric}</span>'
            f'<span class="drv-track"><span class="drv-fill" '
            f'style="width:{m.score:.0f}%;background:{colour}"></span></span>'
            f'<span class="drv-val">{m.score:.0f}</span></div>'
        )
    return f'<div class="drv-list">{"".join(rows)}</div>'
