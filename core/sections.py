"""
sections.py
-----------
Page sections ported one-for-one from the FundaCheck reference page. Python
does every calculation; the HTML mirrors the reference markup so the rendered
result is a faithful copy of the design.
"""

from __future__ import annotations

from html import escape

import numpy as np
import pandas as pd

from . import viz
from .scoring import Assessment

INK = viz.INK
BODY = viz.BODY
MUTED = viz.MUTED
FAINT = viz.FAINT
GREEN = viz.GREEN
AMBER_TXT = viz.AMBER_TXT
MONO = viz.MONO


# --------------------------------------------------------------------------
# data helpers
# --------------------------------------------------------------------------
def full_years(model) -> list[str]:
    """Fiscal-year columns only - summary columns like TTM are dropped."""
    return [str(y) for y in model.years
            if str(y).upper() not in ("TTM", "TREND", "MEAN", "MEDIAN")][-10:]


def ser(model, *names: str) -> pd.Series:
    for name in names:
        s = pd.to_numeric(model.series(name), errors="coerce").dropna()
        if not s.empty:
            return s
    return pd.Series(dtype=float)


def pct_series(s: pd.Series) -> pd.Series:
    """Ratios stored as fractions (0.12) read better scaled up to 12."""
    if s.empty or float(np.nanmax(np.abs(s.tail(10)))) <= 3:
        return s * 100
    return s


def cr(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    if abs(v) >= 1e5:
        return f"\u20b9{v / 1e5:.2f}L"
    return f"\u20b9{v:,.0f}"


def last_two(s: pd.Series) -> tuple[float | None, float | None]:
    if s.empty:
        return None, None
    latest = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) > 1 else None
    return latest, prev


def corr(a: list[float], b: list[float]) -> float:
    if len(a) < 3:
        return 0.0
    r = np.corrcoef(a, b)[0, 1]
    return 0.0 if np.isnan(r) else float(r)


SHORT: dict[str, str] = {
    "Return on Equity (ROE) %": "ROE",
    "Return on Capital Employed (ROCE) %": "ROCE",
    "Return on Assets (ROA) %": "ROA",
    "Return on Invested Capital (ROIC) %": "ROIC",
    "Debt to Equity Ratio": "Debt to Equity",
    "Interest Coverage Ratio": "Interest Coverage",
}


def short_name(metric: str) -> str:
    return SHORT.get(metric, metric.replace(" Ratio", "").replace(" %", ""))


# --------------------------------------------------------------------------
# ratio deep dive cards
# --------------------------------------------------------------------------
def stat_card(arrow: str, tone: str, big: str, sub: str,
              left_val: str | None, left_lab: str,
              right_val: str | None, right_lab: str) -> str:
    rule = '<div style="width:1px;background:#f1f3f1"></div>'
    foot = ""
    if left_val is not None:
        foot = ('<div style="display:flex;gap:12px;border-top:1px solid #f1f3f1;'
                'padding-top:10px">'
                f'<div><div style="font-size:13px;font-weight:700;color:{INK}">{left_val}</div>'
                f'<div style="font-size:10.5px;color:{FAINT}">{left_lab}</div></div>{rule}'
                f'<div><div style="font-size:13px;font-weight:700;color:{INK}">{right_val}</div>'
                f'<div style="font-size:10.5px;color:{FAINT}">{right_lab}</div></div></div>')
    arrow_colour = GREEN if tone == "good" else "#b5761f" if tone == "warn" else "#a4483f"
    return ('<div style="min-width:0;background:#fff;border-radius:18px;padding:16px 18px">'
            '<div style="display:flex;align-items:center;gap:9px">'
            f'<span style="color:{arrow_colour};font-size:15px">{arrow}</span>'
            f'<span style="font-size:24px;font-weight:800;letter-spacing:-1px;'
            f'color:{INK}">{big}</span></div>'
            f'<div style="font-size:12.5px;color:{MUTED};padding:2px 0 10px">{sub}</div>'
            f'{foot}</div>')


def simple_card(label: str, big: str, note: str,
                note_tone: str = AMBER_TXT) -> str:
    colour = {"good": GREEN, "warn": AMBER_TXT,
              "bad": "#a4483f"}.get(note_tone, note_tone)
    return ('<div style="min-width:0;background:#fff;border-radius:18px;'
            'padding:16px 18px">'
            f'<div style="font-size:12.5px;color:{MUTED}">{label}</div>'
            f'<div style="font-size:22px;font-weight:800;letter-spacing:-.9px;'
            f'color:{INK};padding:5px 0 8px">{big}</div>'
            f'<div style="font-size:11px;color:{colour};border-top:1px solid #f1f3f1;'
            f'padding-top:10px">{note}</div></div>')


def roce_card(model, result: Assessment) -> str:
    s = pct_series(ser(model, "Return on Capital Employed (ROCE) %"))
    weak, strong = result.sector.benchmarks.get(
        "Return on Capital Employed (ROCE) %", (9.0, 15.0))
    if s.empty:
        return simple_card("Return on capital employed", "n/a", "not in this workbook")
    latest = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) > 1 else None
    yoy = f"{(latest - prev) / abs(prev) * 100:+.1f}% YoY" if prev else ""
    spark, _ = viz.spark(list(s.tail(10)))
    year = full_years(model)[-1]
    tone_c = AMBER_TXT if latest < weak else GREEN
    arrow = "\u25bc" if (prev is not None and latest < prev) else "\u25b2"
    return ('<div style="background:#fff;border-radius:18px;padding:16px 18px 14px;'
            'min-width:0;box-sizing:border-box">'
            '<div style="display:flex;align-items:center;justify-content:space-between">'
            f'<span style="font-size:12.5px;color:{MUTED}">'
            'Return on capital employed</span>'
            f'<span style="font-size:10.5px;color:{FAINT}">{year}</span></div>'
            '<div style="display:flex;align-items:center;gap:9px;padding:5px 0 10px">'
            f'<span style="color:{tone_c};font-size:15px">{arrow}</span>'
            f'<span style="font-size:24px;font-weight:800;letter-spacing:-1px;'
            f'color:{INK}">{latest:.1f}%</span>'
            f'<span style="font-size:12px;font-weight:700;color:{tone_c}">{yoy}</span></div>'
            f'<div style="padding-bottom:10px">{spark}</div>'
            '<div style="display:flex;gap:12px;border-top:1px solid #f1f3f1;padding-top:10px">'
            f'<div><div style="font-size:13px;font-weight:700;color:{INK}">{weak:.1f}%</div>'
            '<div style="font-size:10.5px;color:' + FAINT + '">sector weak band</div></div>'
            '<div style="width:1px;background:#f1f3f1"></div>'
            f'<div><div style="font-size:13px;font-weight:700;color:{INK}">{strong:.1f}%</div>'
            f'<div style="font-size:10.5px;color:{FAINT}">sector strong band</div></div>'
            '</div></div>')


def dials_row(model) -> tuple[str, int]:
    def val_of(*names):
        s = pct_series(ser(model, names[0], *names[1:]))
        return float(s.iloc[-1]) if not s.empty else 0.0

    gross_v = val_of("Gross Margin")
    net_v = val_of("Net Profit Margin")
    gp = last_two(ser(model, "Sales"))[0] and None  # placeholder, replaced below
    gp_val = last_two(ser(model, "Gross Profit"))[0]
    np_val = last_two(ser(model, "Net Profit"))[0]
    items = [
        ("Gross profit margin", max(0.0, gross_v), 30,
         f"{cr(gp_val)} gross profit", ("#e6c25f", GREEN)),
        ("Net profit margin", max(0.0, net_v), 30,
         f"{cr(np_val)} net profit", ("#d98d80", GREEN)),
    ]
    return viz.dials_row(items)


def cost_card(model) -> tuple[str, int]:
    """'Where each \u20b9100 of sales goes' \u2014 the cost split straight from the latest
    year of the Common Size statement (each line is already a % of sales, i.e. the
    rupees spent per \u20b9100 of sales), shown with the \u20b9 symbol rather than a percent."""
    cs = model.common_size

    def per100(*names: str) -> float:
        for n in names:
            if n in cs.index:
                s = pd.to_numeric(cs.loc[n], errors="coerce").dropna()
                if len(s):
                    return round(float(s.iloc[-1]) * 100, 1)
        return 0.0

    cogs = per100("COGS", "Cost of Goods Sold")
    other = per100("Selling & General Expenses", "Other Operating Expenses",
                   "Other Expenses")
    dep = per100("Depreciation")
    interest = per100("Interest", "Finance Cost")
    tax = per100("Tax")
    kept = per100("Net Profit", "Net profit", "PAT", "Profit after tax")
    segs = [("Cost of goods", cogs, "#177245"),
            ("Other operating", other, "#3d9e6b"),
            ("Depreciation", dep, "#9ecfb4"),
            ("Interest", interest, "#d9a441"),
            ("Tax", tax, "#c98a7f")]
    segs = [s for s in segs if s[1] > 0.05]
    return viz.donut(segs, f"\u20b9{kept:.1f}", "kept per \u20b9100", size=136, sw=18,
                     fmt=lambda v: f"\u20b9{v:.1f}")


def flows_card(model) -> str:
    """Four tiles: how sales, costs and profit moved year-on-year (in \u20b9 crore),
    plus the latest other income versus operating profit."""
    yrs = full_years(model)
    if len(yrs) < 2:
        return ""
    fy1, fy0 = yrs[-1], yrs[-2]

    def val(y, *names):
        s = pd.to_numeric(ser(model, *names), errors="coerce")
        return float(s.get(y)) if y in s.index and pd.notna(s.get(y)) else None

    def cr(v):
        if v is None:
            return "\u2014"
        return f"\u20b9{v / 1e5:.2f}L" if abs(v) >= 1e5 else f"\u20b9{v:,.0f}"

    def growth_tile(label, names):
        a, b = val(fy1, *names), val(fy0, *names)
        if a is None or not b:
            return ""
        g = (a - b) / abs(b) * 100
        up = g >= 0
        arrow, col = ("\u25b2", "#2f9e63") if up else ("\u25bc", "#d0554a")
        return (f'<div class="ftile"><div class="fg">'
                f'<span class="farr" style="color:{col}">{arrow}</span> {g:+.1f}%</div>'
                f'<div class="flbl">{label}</div>'
                f'<div class="frow"><span>{cr(a)}<small>{fy1} cr</small></span>'
                f'<i></i><span>{cr(b)}<small>{fy0} cr</small></span></div></div>')

    oi = val(fy1, "Other Income", "Other Income ")
    eb = val(fy1, "EBIT (OPM)", "EBIT", "EBIT (Operating Profit)")
    oi_tile = ""
    if oi is not None:
        note = ""
        if eb is not None:
            rel = "above" if oi >= eb else "below"
            note = f'<div class="fnote">{rel} EBIT of {cr(eb)}</div>'
        oi_tile = (f'<div class="ftile"><div class="flbl2">Other income \u00b7 {fy1}'
                   f'</div><div class="fbig">{cr(oi)}</div>{note}</div>')

    tiles = "".join([
        growth_tile("Net profit growth", ("Net Profit", "Net profit")),
        oi_tile,
        growth_tile("Revenue growth", ("Sales",)),
        growth_tile("COGS growth", ("COGS",)),
    ])
    return f'<div class="ftiles">{tiles}</div>'


def deepdive_charts(model) -> dict[str, tuple[str, int]]:
    years = full_years(model)

    def tail(s: pd.Series) -> list[float]:
        s = s.reindex([y for y in years if y in s.index]).dropna()
        return [float(v) for v in s]

    gm = tail(pct_series(ser(model, "Gross Margin")))
    em = tail(pct_series(ser(model, "EBITDA Margin")))
    om = tail(pct_series(ser(model, "EBIT Margin")))
    nm = tail(pct_series(ser(model, "Net Profit Margin")))
    roe = tail(pct_series(ser(model, "Return on Equity (ROE) %")))
    roce = tail(pct_series(ser(model, "Return on Capital Employed (ROCE) %")))
    roa = tail(pct_series(ser(model, "Return on Assets (ROA) %")))
    de = tail(ser(model, "Debt to Equity Ratio"))
    ic = tail(ser(model, "Interest Coverage Ratio"))
    dd = tail(ser(model, "Debtor Days"))
    iv = tail(ser(model, "Inventory Days"))
    pay = tail(ser(model, "Payable Days"))
    ccc = tail(ser(model, "Cash Conversion Cycle"))
    cfo = tail(ser(model, "Cash from Operating Activity", "CFO / Total Assets") * 0 +
               ser(model, "Cash from Operating Activity")) \
        if not ser(model, "Cash from Operating Activity").empty else []
    cfi_raw = ser(model, "Cash from Investing Activity")
    cff_raw = ser(model, "Cash from Financing Activity")

    def aligned(seq_list):
        """Trim every series to the shortest common year window."""
        seqs = [list(s) for s in seq_list]
        lens = [len(s) for s in seqs if len(s)]
        if not lens:
            k = 0
        else:
            k = min(lens)
        ys = years[-k:] if k else []
        out = []
        for s in seqs:
            if not s:
                out.append([0.0] * k if k else [])
            elif len(s) >= k:
                out.append(s[-k:])
            else:
                out.append(s + [0.0] * (k - len(s)))
        return ys, out

    ys_m, (gm, em, om, nm) = aligned([gm, em, om, nm])
    ys_r, (roe, roce, roa) = aligned([roe, roce, roa])
    ys_l, (de, ic) = aligned([de, ic])
    ys_w, (dd, iv, pay, ccc) = aligned([dd, iv, pay, ccc])
    cfo_s = ser(model, "Cash from Operating Activity")
    cfi_s, cff_s = cfi_raw, cff_raw
    ys_c, (cfo, cfi, cff) = aligned([
        cfo_s if not cfo_s.empty else pd.Series(dtype=float),
        cfi_s if not cfi_s.empty else pd.Series(dtype=float),
        cff_s if not cff_s.empty else pd.Series(dtype=float)])

    charts: dict[str, tuple[str, int]] = {}
    if len(gm) >= 3:
        charts["margins"] = viz.line_chart(
            "fcMargins", ys_m,
            [("Gross", viz.MID, gm), ("EBITDA", viz.GREEN, em),
             ("EBIT", "#0f3d27", om), ("Net", viz.AMBER, nm)],
            fmt_js="v=>v.toFixed(1)+'%'",
            fmt_py=lambda v: f"{v:.0f}%")
    if len(roe) >= 3:
        charts["returns"] = viz.line_chart(
            "fcReturns", ys_r,
            [("ROE", viz.GREEN, roe), ("ROCE", viz.AMBER, roce),
             ("ROA", viz.LIGHT, roa)],
            fmt_js="v=>v.toFixed(1)+'%'",
            fmt_py=lambda v: f"{v:.0f}%")
    if len(de) >= 3:
        charts["leverage"] = viz.leverage_chart(ys_l, de, ic)
    if len(dd) >= 3:
        charts["wc"] = viz.wc_chart(ys_w, dd, iv, pay, ccc)
    if len(cfo) >= 3 and any(cfo) :
        charts["cash"] = viz.cash_chart(ys_c, cfo, cfi, cff)
    turn_rows = []
    for label, key in (("Debtor turnover", "Debtor Turnover Ratio"),
                       ("Creditor turnover", "Creditor Turnover Ratio"),
                       ("Inventory turnover", "Inventory Turnover"),
                       ("Fixed asset turnover", "Fixed Asset Turnover"),
                       ("Capital turnover", "Capital Turnover Ratio")):
        s = ser(model, key).tail(10)
        if len(s) >= 4:
            mean = float(s.mean())
            turn_rows.append((label, float(s.iloc[-1]), mean))
    if turn_rows:
        charts["turnover"] = viz.turnover_rows(turn_rows)
    asset_layers = _bs_layers(model, True)
    liab_layers = _bs_layers(model, False)
    if len(asset_layers) >= 2:
        charts["assets"] = viz.area_chart("fcAssets", years[-len(asset_layers[0][2]):],
                                          asset_layers, hover=True)
    if len(liab_layers) >= 2:
        charts["liab"] = viz.area_chart("fcLiab", years[-len(liab_layers[0][2]):],
                                        liab_layers, hover=True)
    return charts


_BS_ALIASES_ASSETS = [
    ("Net block", ["Net Block"]),
    ("Capital WIP", ["Capital Work in Progress", "CWIP"]),
    ("Investments", ["Investments"]),
    ("Other assets", ["Other Assets", "Long Term Investments"]),
    ("Current assets", ["Total Current Assets", "Current Assets"]),
]
_BS_ALIASES_LIAB = [
    ("Borrowings", ["Total Debt", "Borrowings"]),
    ("Other liabilities", ["Other Liabilities", "Total Liabilities"]),
    ("Reserves", ["Reserves", "Equity Capital"]),
    ("Share capital", ["Share Capital", "Equity Capital"]),
]


def _bs_layers(model, assets: bool) -> list[tuple[str, str, list[float]]]:
    palette_a = ["#0f5b34", "#177245", "#3d9e6b", "#6dbd93", "#a9d3bd"]
    palette_l = ["#8f3b31", "#c9803a", "#3d9e6b", "#a9d3bd"]
    aliases = _BS_ALIASES_ASSETS if assets else _BS_ALIASES_LIAB
    palette = palette_a if assets else palette_l
    years = full_years(model)
    layers = []
    for i, (name, alts) in enumerate(aliases):
        s = ser(model, *alts).reindex(years).dropna()
        if len(s) >= 3 and float(s.abs().max()) > 1:
            vals = [float(v) for v in s]
            layers.append((name, palette[i % len(palette)], vals))
    if len(layers) < 2:
        # fall back to whatever big rows exist so the tile still renders
        total = ser(model, "Total Asset" if assets else "Total Liabilities")
        if total.empty:
            return []
        vals = [float(v) for v in total.tail(len(years))]
        other = [("Total", palette[0], vals)]
        return other
    k = min(len(l[2]) for l in layers)
    return [(nm, c, v[-k:]) for nm, c, v in layers]


# --------------------------------------------------------------------------
# sector lens
# --------------------------------------------------------------------------
def why_card() -> str:
    return ('<div style="background:#fff;border-radius:18px;padding:20px 24px;'
            'border-left:4px solid #7f7de0">'
            '<div style="font-family:ui-monospace,monospace;font-size:10.5px;font-weight:700;'
            'letter-spacing:1.4px;color:#6f6dd0;padding-bottom:10px">WHY THIS MATTERS</div>'
            '<p style="margin:0;font-size:14.5px;line-height:1.65;color:' + BODY +
            ';text-wrap:pretty">The financials never change - only the yardstick does. '
            'A debt/equity of 8x is routine in a bank and a solvency alarm for a software '
            'firm, so the same company can be strong under one lens and weak under another. '
            'This view runs every sector rule book over the loaded model at once.</p></div>')


def sector_scores(model, result: Assessment) -> tuple[list[tuple[str, float]], str]:
    from .scoring import compare_sectors
    from .sectors import SECTORS
    frame = compare_sectors(model, list(SECTORS.values()))
    rows = [(str(r["Sector lens"]), float(r["Score"])) for _, r in frame.iterrows()]
    return rows, result.sector.name


def heatmap_block(model) -> tuple[str, int]:
    series_defs = [
        ("Sales Growth", "Sales Growth", True),
        ("EBITDA Margin", "EBITDA Margin", True),
        ("Net Profit Margin", "Net Profit Margin", True),
        ("Return on Equity", "Return on Equity (ROE) %", True),
        ("Debt to Equity", "Debt to Equity Ratio", False),
        ("Interest Coverage", "Interest Coverage Ratio", False),
    ]
    labels, cols = [], []
    for label, metric, is_pct in series_defs:
        s = pct_series(ser(model, metric)) if is_pct else ser(model, metric)
        vals = [float(v) for v in s.tail(10)]
        if len(vals) >= 3:
            labels.append(label)
            cols.append(vals)
    n = len(labels)
    matrix = [[1.0] * n for _ in range(n)]
    for r in range(n):
        for c in range(n):
            if r != c:
                k = min(len(cols[r]), len(cols[c]))
                matrix[r][c] = corr(cols[r][-k:], cols[c][-k:])
    return viz.heatmap(labels, matrix)


def bench_table(result: Assessment) -> str:
    th_style = ('padding:10px 12px;font-size:11.5px;letter-spacing:.8px;color:#8b918e;'
                'font-weight:700;text-align:left;border-bottom:1px solid #eceeec;'
                f'font-family:{MONO}')
    td_base = ('padding:11px 12px;font-size:13.5px;border-bottom:1px solid #f4f5f3')
    head = "".join(f'<th style="{th_style}">{t}</th>'
                   for t in ("METRIC", "LATEST", "WEAK AT", "STRONG AT", "SCORE"))
    body_rows = ""
    for m in result.metrics:
        cells = ""
        for i, v in enumerate((m.display(m.latest), m.display(m.weak_at),
                               m.display(m.strong_at))):
            colour = INK if i == 0 else "#8b918e"
            weight = 700 if i == 0 else 400
            cells += (f'<td style="{td_base};color:{colour};font-weight:{weight};'
                      f'font-family:{MONO}">{v}</td>')
        chip_bg = viz.band(m.score)
        body_rows += (
            f'<tr><td style="{td_base};color:#3f4744">'
            f'{short_name(m.metric)}</td>{cells}'
            f'<td style="padding:9px 11px;border-bottom:1px solid #f4f5f3">'
            f'<span style="font-size:11px;font-weight:700;color:#fff;background:'
            f'{chip_bg};border-radius:6px;padding:3px 7px;font-family:{MONO}">'
            f'{round(m.score)}</span></td></tr>')
    return ('<div style="overflow:auto"><table style="width:100%;border-collapse:collapse">'
            f'<thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table></div>')


# --------------------------------------------------------------------------
# statements
# --------------------------------------------------------------------------
STMT_HEADS = {"Sales", "Gross Profit", "EBITDA", "EBIT (OPM)", "Profit Before Tax",
              "Earnings Before Tax", "Net Profit", "Operating Cash Flow",
              "Net Change in Cash", "Total Assets", "Total Liabilities & Equity"}

STMT_ORDER = [
    ("Sales", ["Sales"]), ("COGS", ["COGS"]),
    ("Gross Profit", ["Gross Margin", "Gross Profit"]),
    ("Selling & General Expenses", ["Selling & General Expenses"]),
    ("EBITDA", ["EBITDA"]), ("Depreciation", ["Depreciation"]),
    ("EBIT (OPM)", ["EBIT (OPM)", "EBIT (Operating Profit)"]),
    ("Other Income", ["Other Income"]), ("Interest", ["Interest"]),
    ("Profit Before Tax", ["Earnings Before Tax"]),
    ("Tax", ["Tax"]), ("Net Profit", ["Net Profit"]),
]

# Balance sheet, split into the two sides a reader expects. Each line lists the
# names it may appear under so it works across workbooks. "Total Assets" and the
# equity+liabilities total are rendered as bold subtotal rows (see STMT_HEADS).
BS_LIABILITIES = [
    ("Equity Share Capital", ["Equity Share Capital", "Share Capital"]),
    ("Reserves", ["Reserves", "Reserves and Surplus"]),
    ("Borrowings", ["Borrowings", "Total Debt"]),
    ("Other Liabilities", ["Other Liabilities"]),
]
BS_ASSETS = [
    ("Net Block", ["Net Block", "Fixed Assets"]),
    ("Capital Work in Progress", ["Capital Work in Progress"]),
    ("Investments", ["Investments"]),
    ("Other Assets", ["Other Assets"]),
    ("Receivables", ["Receivables", "Trade Receivables", "Debtors"]),
    ("Inventory", ["Inventory", "Inventories"]),
    ("Cash & Bank", ["Cash & Bank", "Cash and Bank"]),
]


def _fmt_n(v: float) -> str:
    if abs(v) >= 1000:
        return f"{round(v):,.0f}"
    return f"{v:.1f}"


# Ratio Analysis / Common Size values carry an implied unit that the source
# workbook stores in the cell number-format ("0.00%", "#,##0.0\x", "0.0 days",
# plain). The parser flattens that away, so re-derive it from the row name and
# tab and render each value the way the Excel sheet would.
def _value_kind(tab: str, name: str) -> str:
    if tab in ("Income Statement", "Balance Sheet"):
        return "currency"
    if tab == "Common Size":
        return "percent"                       # every line is a % of sales/total
    n = name.lower()
    if "days" in n or "conversion cycle" in n:
        return "days"
    if ("turnover" in n or "interest coverage" in n or "interest cover" in n
            or "pe ratio" in n or "p/e" in n or "price to" in n
            or "cfo / pat" in n or "cfo/pat" in n):
        return "times"
    if ("margin" in n or "growth" in n or "return on" in n or "roce" in n
            or "roe" in n or "roa" in n or "roic" in n or "% sales" in n
            or "%sales" in n or "payout" in n or "retained earnings" in n
            or "cfo / sales" in n or "cfo/sales" in n or "cfo / total" in n
            or "cfo/total" in n or "self sustained" in n or "yield" in n
            or n.rstrip().endswith("%")):
        return "percent"
    return "value"                             # d/e, d/a, peg -> plain multiple


def _fmt_stmt_value(kind: str, v: float) -> str:
    if kind == "currency":
        return _fmt_n(v)
    if kind == "percent":
        return f"{v * 100:.1f}%"
    if kind == "times":
        return f"{v:,.2f}x"
    if kind == "days":
        return f"{v:,.1f} days"
    return f"{v:,.2f}"


def _delta_text(kind: str, v, prev) -> tuple[str, str] | None:
    """(text, colour) for the year-on-year sub-line, or None when undefined.

    Income-statement lines keep the signed % growth in green/red; ratio and
    common-size lines show a neutral-grey delta in their own unit (percentage
    points for %, days, or x), matching the reference statements table.
    """
    if v is None or prev is None:
        return None
    # Colour every delta green when it rose and red when it fell, matching the
    # income statement — a rise is green, a fall is red, across all tabs.
    up, down = "#2f9e63", "#d0554a"
    if kind == "currency":
        if not prev:
            return None
        ch = (v - prev) / abs(prev) * 100
        return (f"{ch:+.1f} %", up if ch >= 0 else down)
    col = up if v >= prev else down
    if kind == "percent":
        return (f"{(v - prev) * 100:+.1f} pp", col)
    if kind == "days":
        return (f"{v - prev:+.1f} d", col)
    if kind == "times":
        return (f"{v - prev:+.2f}x", col)
    return (f"{v - prev:+.2f}", col)


# Ratio Analysis is shown grouped by the kind of ratio. First keyword match wins,
# so the order matters (e.g. "interest coverage" lands in solvency before the bare
# "% sales" expense bucket).
_RATIO_CATS = [
    ("Growth", ("growth",)),
    ("Profitability & margins", ("margin", "opm", "gross profit")),
    ("Return ratios", ("return on", "roce", "roe", "roa")),
    # Efficiency is tested before solvency so "Debtor Turnover/Days" is not
    # swallowed by the "debt" keyword below.
    ("Efficiency & turnover", ("turnover", "days", "cycle", "conversion", "debtor",
                               "creditor", "inventory", "receivable", "payable",
                               "working capital")),
    ("Solvency & leverage", ("debt", "interest coverage", "interest cover",
                             "gearing", "d/e", "interest % sales", "interest%")),
    ("Cash flow", ("cfo", "cash from", "cash flow", "fcf", "free cash")),
    ("Valuation", ("p/e", "pe ratio", "peg", "price to", "p/b", "p/s", "ev/",
                   "yield", "market cap", "price/")),
    ("Payout & expense", ("payout", "dividend", "retained earnings",
                          "% sales", "%sales")),
]
_RATIO_ORDER = [c[0] for c in _RATIO_CATS] + ["Other ratios"]

# Common Size mixes income-statement and balance-sheet lines; split into three
# clear sections. Asset keywords are tested before liability ones.
_CS_ASSET = ("asset", "block", "capital work", "cwip", "investment", "receivable",
             "inventor", "goodwill", "loans & advances", "cash &", "current asset")
_CS_LIAB = ("equity", "reserve", "borrow", "liabilit", "payable", "provision",
            "share capital", "net worth", "networth")
_CS_ORDER = ["Income statement (% of sales)",
             "Liabilities & equity (% of total)", "Assets (% of total)"]


def _ratio_cat(name: str) -> str:
    n = name.lower()
    for label, kws in _RATIO_CATS:
        if any(k in n for k in kws):
            return label
    return "Other ratios"


def _cs_group(name: str) -> str:
    n = name.lower()
    if any(k in n for k in _CS_ASSET):
        return "Assets (% of total)"
    if any(k in n for k in _CS_LIAB):
        return "Liabilities & equity (% of total)"
    return "Income statement (% of sales)"


def _grouped(frame, model, classify, order):
    """Rows for a workbook frame, split into sections with header rows.

    A section header is emitted as ``(label, [], "section")`` before its rows;
    empty sections are skipped and the section order follows ``order``.
    """
    buckets: dict[str, list] = {}
    for name in frame.index:
        s = pd.to_numeric(frame.loc[name], errors="coerce").reindex(list(model.years))
        vals = [float(v) if pd.notna(v) else None for v in s]
        if not any(v is not None for v in vals):
            continue
        buckets.setdefault(classify(str(name)), []).append((str(name), vals, False))
    rows: list[tuple[str, list, object]] = []
    for label in order + [k for k in buckets if k not in order]:
        if buckets.get(label):
            rows.append((label, [], "section"))
            rows.extend(buckets[label])
    return rows


def stmt_source(model, tab: str) -> list[tuple[str, list[float | None], object]]:
    years = full_years(model)
    rows: list[tuple[str, list[float | None], object]] = []

    def pull(name, aliases, head):
        for a in aliases:
            s = pd.to_numeric(model.series(a), errors="coerce")
            s = s.reindex([y for y in model.years]).dropna(how="all")
            if not s.empty:
                vals = [float(s[y]) if y in s.index and pd.notna(s[y]) else None
                        for y in years]
                rows.append((name, vals, head))
                return

    def vals_for(aliases):
        for a in aliases:
            s = pd.to_numeric(model.series(a), errors="coerce")
            s = s.reindex([y for y in model.years]).dropna(how="all")
            if not s.empty:
                return [float(s[y]) if y in s.index and pd.notna(s[y]) else None
                        for y in years]
        return None

    if tab == "Income Statement":
        for name, aliases in STMT_ORDER:
            pull(name, aliases, name in STMT_HEADS)
    elif tab == "Balance Sheet":
        def section(title, items):
            block = [(n, v, False) for n, v in
                     ((n, vals_for(a)) for n, a in items) if v is not None]
            if block:
                rows.append((title, [], "section"))
                rows.extend(block)
        section("Liabilities & equity", BS_LIABILITIES)
        total = vals_for(["Total Liabilities", "Total Asset", "Total Assets"])
        if total is not None:
            rows.append(("Total Liabilities & Equity", total, True))
        section("Assets", BS_ASSETS)
        total_a = vals_for(["Total Asset", "Total Assets"])
        if total_a is not None:
            rows.append(("Total Assets", total_a, True))
    elif tab == "Ratio Analysis":
        rows = _grouped(model.ratios, model, _ratio_cat, _RATIO_ORDER)
    else:  # Common size
        rows = _grouped(model.common_size, model, _cs_group, _CS_ORDER)
    return rows


def statements_html(model, tab: str, show_pct: bool, query: str) -> str:
    rows = stmt_source(model, tab)
    years = full_years(model)
    if query:
        rows = [r for r in rows if query in r[0].lower()]
    if not rows:
        return (f'<p class="tile-sub" style="padding:14px 4px">'
                f'No rows{" match the search" if query else " were found"}.</p>')

    th = ('padding:10px 12px;font-size:11.5px;letter-spacing:.8px;color:#8b918e;'
          'font-weight:700;text-align:right;border-bottom:1px solid #eceeec;'
          f'font-family:{MONO}')
    head_cells = (f'<th style="{th};position:sticky;left:0;background:#fafbfa;'
                  f'min-width:200px;font-size:14;letter-spacing:0;color:{INK};'
                  f'text-align:left">{tab}</th>'
                  + "".join(f'<th style="{th};background:#fafbfa;font-size:14;'
                            f'letter-spacing:0;color:{INK}">{y}</th>' for y in years))
    body = ""
    for name, vals, head_row in rows:
        if head_row == "section":
            body += (f'<tr class="secrow"><td colspan="{len(years) + 1}" '
                     f'style="padding:15px 14px 7px;font-size:11px;font-weight:800;'
                     f'letter-spacing:1.1px;color:#177245;background:#eef4f0;'
                     f'text-transform:uppercase;font-family:{MONO};'
                     f'border-bottom:1px solid #dce7e0">{name}</td></tr>')
            continue
        bg = "#f5f9f7" if head_row else "#fff"
        kind = _value_kind(tab, name)
        tds = ""
        prev = None
        for v in vals:
            pct_html = ""
            if show_pct:
                delta = _delta_text(kind, v, prev)
                if delta is not None:
                    txt, colour = delta
                    pct_html = (f'<div class="pctsub" style="font-size:13px;'
                                f'padding-top:3px;font-weight:600;color:{colour};'
                                f'font-family:{MONO}">{txt}</div>')
                else:
                    pct_html = ('<div class="pctsub" style="font-size:13px;'
                                'padding-top:3px;font-weight:600;color:#c8ccc9;'
                                f'font-family:{MONO}">—</div>')
            val_txt = _fmt_stmt_value(kind, v) if v is not None else "—"
            tds += (f'<td style="padding:10px 14px;text-align:right;'
                    f'border-bottom:1px solid #f1f3f1;white-space:nowrap">'
                    f'<div style="font-size:17px;font-weight:{800 if head_row else 600};'
                    f'color:{INK};letter-spacing:-.2px;font-family:{MONO}">{val_txt}</div>'
                    f'{pct_html}</td>')
            prev = v
        body += (f'<tr data-name="{escape(name.lower(), quote=True)}"'
                 + (' class="head"' if head_row else "")
                 + f' style="background:{bg}">'
                 f'<td style="padding:10px 14px;font-size:15px;'
                 f'font-weight:{700 if head_row else 500};color:{INK if head_row else "#3f4744"};'
                 f'border-bottom:1px solid #f1f3f1;position:sticky;left:0;background:{bg};'
                 f'white-space:nowrap">{name}</td>{tds}</tr>')
    return ('<div style="overflow:auto;border:1px solid #eceeec;border-radius:12px">'
            '<table style="width:100%;border-collapse:collapse">'
            f'<thead><tr style="background:#fafbfa">{head_cells}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def tip_text(name: str, years: list[str], vals: list[float | None]) -> str:
    parts = []
    for y, v in zip(years, vals):
        if v is not None:
            parts.append(f"{y}: {_fmt_n(v)}")
    return escape(f"{name} - " + " · ".join(parts[:10]), quote=True)


# APPEND_MARKER_1
