"""
viz.py
------
Design-exact HTML/SVG chart builders, ported geometry-for-geometry from the
FundaCheck reference page, plus one shared hover-tooltip engine.

Every builder returns ``(html, height_px)`` where *html* is a fragment meant to
be wrapped by :func:`doc` and rendered through ``st.components.v1.html`` — an
iframe, which is the only place Streamlit allows JavaScript, so the hover
tooltips work exactly like the reference page.
"""

from __future__ import annotations

import json
import math
from html import escape

# --- palette (fixed, from the design) ---------------------------------------
GREEN = "#177245"
GREEN_DARK = "#0f5b34"
MID = "#3d9e6b"
LIGHT = "#9ecfb4"
AMBER = "#d9a441"
AMBER_TXT = "#b5761f"
RED = "#b4483c"
INK = "#15201a"
BODY = "#5f6663"
MUTED = "#8b918e"
FAINT = "#9aa09d"
GRID = "#eef0ee"
ZERO = "#dfe2df"

MONO = "ui-monospace,SFMono-Regular,Menlo,monospace"


def band(v: float) -> str:
    """The design's score-band colour."""
    return MID if v >= 66 else AMBER if v >= 40 else RED


def band_word(v: float) -> str:
    return "STRONG" if v >= 66 else "NEUTRAL" if v >= 40 else "WEAK"


# --- document shell ----------------------------------------------------------
_BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{background:#fff;font-family:'Plus Jakarta Sans',system-ui,sans-serif;color:#15201a}
svg{display:block;width:100%;height:auto;overflow:visible}
text{font-family:'Plus Jakarta Sans',system-ui,sans-serif}
.mono{font-family:ui-monospace,Menlo,monospace}
#wrap{position:relative}
#fctip{position:absolute;display:none;pointer-events:none;background:#0f2a1e;
  opacity:.97;border-radius:8px;padding:8px 10px;z-index:50;min-width:96px;
  font-size:10.5px;line-height:15px;color:#cfe0d7;white-space:nowrap;
  box-shadow:0 6px 18px rgba(0,0,0,.25)}
#fctip b{color:#fff;font-weight:700}
#fctip .yr{font-size:11px;font-weight:700;margin-bottom:4px}
#fctip span{display:flex;align-items:center;gap:6px}
#fctip i{width:7px;height:7px;border-radius:50%;flex:none}
#fctip .v{margin-left:auto;padding-left:12px;color:#fff;font-weight:700;
  font-family:ui-monospace,Menlo,monospace}
.hit{cursor:crosshair}
"""

_BASE_JS = """
// This engine script is emitted BEFORE #wrap / #fctip exist, so look them up
// lazily -- capturing them now would leave both null and every tooltip would
// throw. The [data-tt] binding is likewise deferred until the body is parsed.
function _fctip(){var t=document.getElementById('fctip');
  if(!t){t=document.createElement('div');t.id='fctip';
    (document.getElementById('wrap')||document.body).appendChild(t);}return t;}
function fcShow(x,y,html){var tip=_fctip(),wrap=document.getElementById('wrap')||document.body;
  tip.innerHTML=html;tip.style.display='block';
  let px=x+14;if(px+tip.offsetWidth>wrap.clientWidth-6)px=x-tip.offsetWidth-14;
  tip.style.left=Math.max(2,px)+'px';
  tip.style.top=Math.max(2,Math.min(y-tip.offsetHeight/2,wrap.clientHeight-tip.offsetHeight-2))+'px';}
function fcHide(){var tip=document.getElementById('fctip');if(tip)tip.style.display='none';}
function fcBindTips(){var wrap=document.getElementById('wrap')||document.body;
  document.querySelectorAll('[data-tt]').forEach(el=>{
    el.addEventListener('mousemove',e=>{const r=wrap.getBoundingClientRect();
      fcShow(e.clientX-r.left,e.clientY-r.top,el.getAttribute('data-tt'));});
    el.addEventListener('mouseleave',fcHide);});}
if(document.readyState!=='loading')fcBindTips();
else document.addEventListener('DOMContentLoaded',fcBindTips);
function fcColumns(cid,Y,L,fmt){
  const svg=document.getElementById(cid);
  const cols=svg.querySelectorAll('.hit');
  const xl=svg.querySelectorAll('.xline');
  cols.forEach((el,i)=>{
    el.addEventListener('mousemove',e=>{const r=svg.getBoundingClientRect();
      const sc=svg.viewBox.baseVal.width/r.width;
      const rows=L.map(l=>'<span><i style="background:'+l[1]+'"></i>'+l[0]+
        '<span class="v">'+fmt(l[2][i])+'</span></span>').join('');
      fcShow((e.clientX-r.left)*sc,(e.clientY-r.top)*sc,'<div class="yr">'+Y[i]+'</div>'+rows);
      xl.forEach(x=>x.style.display='none');if(xl[i])xl[i].style.display='block';});
    el.addEventListener('mouseleave',()=>{fcHide();xl.forEach(x=>x.style.display='none');});});
}
"""


def doc(body: str, extra_css: str = "") -> str:
    """Wrap a fragment in a standalone document with fonts + tooltip engine.

    The engine script is emitted BEFORE the body so chart scripts that run
    inline right after their SVG can call fcColumns immediately.
    """
    css = _BASE_CSS + extra_css
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:'
        'wght@400;500;600;700;800&display=swap" rel="stylesheet">'
        f"<style>{css}</style></head><body>"
        f"<script>{_BASE_JS}</script>"
        f'<div id="wrap">{body}<div id="fctip"></div></div>'
        "</body></html>"
    )


def _svg(w: int | float, h: int | float, inner: str, cid: str = "") -> str:
    id_attr = f' id="{cid}"' if cid else ""
    return (f'<svg{id_attr} viewBox="0 0 {w} {h}" '
            f'style="width:100%;height:auto;display:block;overflow:visible">{inner}</svg>')


def _tt(text: str) -> str:
    return f' data-tt="{escape(str(text), quote=True)}"'


# --- shared pieces -----------------------------------------------------------
def crosshair(w: int, h: int, xs: list[float], cls: str = "") -> str:
    lines = [
        f'<line class="xline{cls}" data-i="{i}" x1="{x:.1f}" x2="{x:.1f}" y1="0" '
        f'y2="{h}" stroke="#b8beba" stroke-dasharray="3 3" style="display:none"/>'
        for i, x in enumerate(xs)
    ]
    return "".join(lines)


def hit_columns(xs: list[float], band_w: float, h: float) -> str:
    return "".join(
        f'<rect class="hit" x="{x - band_w / 2:.1f}" y="0" width="{band_w:.1f}" '
        f'height="{h}" fill="transparent"/>'
        for x in xs
    )


# --- multi-series line chart --------------------------------------------------
def line_chart(cid: str, years: list[str],
               series: list[tuple[str, str, list[float]]],
               fmt_js: str = 'v=>v.toFixed(2)+"%"',
               fmt_py=lambda v: f"{v:.2f}%") -> tuple[str, int]:
    """fmt_py renders the axis labels; fmt_js is the identical format in JS
    for the hover tooltip. They must agree."""
    w, hh, p = 420, 250, {"l": 42, "r": 14, "t": 30, "b": 30}
    all_vals = [v for _, _, vals in series for v in vals]
    mn, mx = min(0.0, *all_vals), max(all_vals)
    sp = (mx - mn) or 1.0
    n = len(years)
    X = lambda i: p["l"] + i * (w - p["l"] - p["r"]) / max(1, n - 1)
    Yp = lambda v: hh - p["b"] - (v - mn) / sp * (hh - p["t"] - p["b"])
    xs = [X(i) for i in range(n)]

    grid = "".join(
        f'<line x1="{p["l"]}" x2="{w - p["r"]}" y1="{p["t"] + f_ * (hh - p["t"] - p["b"]):.1f}" '
        f'y2="{p["t"] + f_ * (hh - p["t"] - p["b"]):.1f}" stroke="{GRID}"/>'
        f'<text x="{p["l"] - 6}" y="{p["t"] + f_ * (hh - p["t"] - p["b"]) + 3.5:.1f}" '
        f'text-anchor="end" font-size="10.5" fill="{FAINT}" class="mono">{fmt_py(mx - f_ * sp)}</text>'
        for f_ in (0, .5, 1)
    )
    paths = ""
    for si, (_, colour, vals) in enumerate(series):
        d = " ".join(("L" if i else "M") + f"{X(i):.1f} {Yp(v):.1f}"
                     for i, v in enumerate(vals))
        last = vals[-1]
        tip = _tt(f"{series[si][0]} · {years[-1]}: {fmt_py(last)}")
        paths += (f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="2.2" '
                  f'stroke-linejoin="round"/><circle {tip} cx="{xs[-1]:.1f}" '
                  f'cy="{Yp(last):.1f}" r="3.2" fill="{colour}" style="cursor:pointer"/>')
    xlabels = "".join(
        f'<text x="{x:.1f}" y="{hh - 8}" text-anchor="middle" font-size="10.5" '
        f'fill="{FAINT}" class="mono">{y}</text>'
        for i, (y, x) in enumerate(zip(years, xs)) if i % 2 == 0
    )
    legend = "".join(
        f'<g><circle cx="{p["l"] + i * 96 + 4}" cy="8" r="4" fill="{c}"/>'
        f'<text x="{p["l"] + i * 96 + 13}" y="12" font-size="11.5" fill="{BODY}">{lb}</text></g>'
        for i, (lb, c, _) in enumerate(series)
    )
    inner = (grid + paths + crosshair(w, hh - p["b"], xs) +
             xlabels + legend + hit_columns(xs, (w - p["l"] - p["r"]) / max(1, n - 1),
                                            hh - p["b"]))
    rows_js = json.dumps([[lb, c, vals] for lb, c, vals in series])
    script = (f"<script>(function(){{const Y={json.dumps(years)};"
              f"const L={rows_js};const fmt={fmt_js};"
              f"fcColumns('{cid}',Y,L,fmt);}})();</script>")
    return (_svg(w, hh, inner, cid) + script, 300)


def js_fmt(expr: str):
    """Deprecated: kept so older callers still import. Line charts now take
    fmt_js / fmt_py directly."""
    def _f(v):
        return expr
    _f._js = expr
    return _f


# --- leverage -----------------------------------------------------------------
def leverage_chart(years: list[str], de: list[float], ic: list[float]) -> tuple[str, int]:
    lw, lh, lp = 420, 250, {"l": 42, "r": 30, "t": 30, "b": 30}
    n = len(years)
    lbw = (lw - lp["l"] - lp["r"]) / n
    mx_de, mx_ic = max(de), max(ic) or 1
    bars = ""
    for i, y in enumerate(years):
        bh = de[i] / mx_de * (lh - lp["t"] - lp["b"])
        x = lp["l"] + i * lbw + lbw * .2
        fill = "#a7c9b6" if de[i] > 1.5 else LIGHT
        bars += (f'<rect{_tt(f"D/E {y}: {de[i]:.2f}x")} x="{x:.1f}" '
                 f'y="{lh - lp["b"] - bh:.1f}" width="{lbw * .6:.1f}" height="{bh:.1f}" '
                 f'rx="4" fill="{fill}" style="cursor:pointer"/>')
    ic_d = " ".join(
        ("L" if i else "M") + f'{lp["l"] + i * lbw + lbw / 2:.1f} '
        f'{lh - lp["b"] - v / mx_ic * (lh - lp["t"] - lp["b"]) * .9:.1f}'
        for i, v in enumerate(ic))
    ic_dots = "".join(
        f'<circle{_tt(f"Interest cover {years[i]}: {v:.2f}x")} '
        f'cx="{lp["l"] + i * lbw + lbw / 2:.1f}" '
        f'cy="{lh - lp["b"] - v / mx_ic * (lh - lp["t"] - lp["b"]) * .9:.1f}" '
        f'r="3" fill="{GREEN}" style="cursor:pointer"/>' for i, v in enumerate(ic))
    xlabels = "".join(
        f'<text x="{lp["l"] + i * lbw + lbw / 2:.1f}" y="{lh - 8}" text-anchor="middle" '
        f'font-size="10.5" fill="{FAINT}" class="mono">{y}</text>'
        for i, y in enumerate(years) if i % 3 == 0)
    legend = (f'<rect x="{lp["l"]}" y="4" width="9" height="9" rx="2" fill="{LIGHT}"/>'
              f'<text x="{lp["l"] + 14}" y="12.5" font-size="11.5" fill="{BODY}">Debt / equity</text>'
              f'<circle cx="{lp["l"] + 112}" cy="8" r="4" fill="{GREEN}"/>'
              f'<text x="{lp["l"] + 121}" y="12" font-size="11.5" fill="{BODY}">Interest cover</text>')
    xs = [lp["l"] + i * lbw + lbw / 2 for i in range(n)]
    L = json.dumps([["D/E", LIGHT, de], ["Interest cover", GREEN, ic]])
    script = (f"<script>(function(){{const Y={json.dumps(years)};const L={L};"
              f"fcColumns('lev',Y,L,v=>v.toFixed(2)+'x');}})();</script>")
    inner = (bars + f'<path d="{ic_d}" fill="none" stroke="{GREEN}" stroke-width="2.2"/>'
             + ic_dots + crosshair(lw, lh - lp["b"], xs) + xlabels + legend
             + hit_columns(xs, lbw, lh - lp["b"]))
    return _svg(lw, lh, inner, "lev") + script, 300


# --- working capital ----------------------------------------------------------
def wc_chart(years, debtor, inv, pay, ccc) -> tuple[str, int]:
    ww, wh, wp = 420, 268, {"l": 44, "r": 14, "t": 30, "b": 30}
    n = len(years)
    wbw = (ww - wp["l"] - wp["r"]) / n
    mx_up = max(d + iv for d, iv in zip(debtor, inv))
    mx_dn = max(pay)
    up_h = (wh - wp["t"] - wp["b"]) * (mx_up / (mx_up + mx_dn))
    zero = wp["t"] + up_h
    body = f'<line x1="{wp["l"]}" x2="{ww - wp["r"]}" y1="{zero:.1f}" y2="{zero:.1f}" stroke="{ZERO}"/>'
    for i, y in enumerate(years):
        x = wp["l"] + i * wbw + wbw * .2
        bw = wbw * .6
        dh = debtor[i] / mx_up * up_h
        ih = inv[i] / mx_up * up_h
        ph = pay[i] / mx_dn * ((wh - wp["t"] - wp["b"]) - up_h)
        body += (
            f'<rect{_tt(f"Debtor days {y}: {debtor[i]:.1f}")} x="{x:.1f}" y="{zero - dh:.1f}" '
            f'width="{bw:.1f}" height="{dh:.1f}" fill="{MID}" style="cursor:pointer"/>'
            f'<rect{_tt(f"Inventory days {y}: {inv[i]:.1f}")} x="{x:.1f}" y="{zero - dh - ih:.1f}" '
            f'width="{bw:.1f}" height="{ih:.1f}" rx="3" fill="{LIGHT}" style="cursor:pointer"/>'
            f'<rect{_tt(f"Payable days {y}: {pay[i]:.1f}")} x="{x:.1f}" y="{zero:.1f}" '
            f'width="{bw:.1f}" height="{ph:.1f}" rx="3" fill="{AMBER}" style="cursor:pointer"/>')
    ccc_d = " ".join(("L" if i else "M") + f'{wp["l"] + i * wbw + wbw / 2:.1f} '
                      f'{zero - v / mx_up * up_h:.1f}' for i, v in enumerate(ccc))
    legend = "".join(
        f'<g><rect x="{wp["l"] + i * 84}" y="4" width="9" height="9" rx="2" fill="{c}"/>'
        f'<text x="{wp["l"] + i * 84 + 13}" y="12.5" font-size="11.5" fill="{BODY}">{t}</text></g>'
        for i, (t, c) in enumerate([("Debtor", MID), ("Inventory", LIGHT),
                                    ("Payable", AMBER), ("CCC", "#0f3d27")]))
    xlabels = "".join(
        f'<text x="{wp["l"] + i * wbw + wbw / 2:.1f}" y="{wh - 8}" text-anchor="middle" '
        f'font-size="10.5" fill="{FAINT}" class="mono">{y}</text>'
        for i, y in enumerate(years) if i % 3 == 0)
    xs = [wp["l"] + i * wbw + wbw / 2 for i in range(n)]
    L = json.dumps([["Debtor", MID, debtor], ["Inventory", LIGHT, inv],
                    ["Payable", AMBER, pay], ["Cash cycle", "#0f3d27", ccc]])
    script = (f"<script>(function(){{const Y={json.dumps(years)};const L={L};"
              f"fcColumns('wc',Y,L,v=>v.toFixed(1));}})();</script>")
    inner = (body + f'<path d="{ccc_d}" fill="none" stroke="#0f3d27" stroke-width="2" '
             f'stroke-dasharray="4 3"/>' + crosshair(ww, wh - wp["b"], xs)
             + legend + xlabels + hit_columns(xs, wbw, wh - wp["b"]))
    return _svg(ww, wh, inner, "wc") + script, 320


# --- cash flow diverging bars ---------------------------------------------------
def cash_chart(years, cfo, cfi, cff) -> tuple[str, int]:
    cw, ch, cp = 420, 250, {"l": 46, "r": 14, "t": 30, "b": 30}
    n = len(years)
    cbw = (cw - cp["l"] - cp["r"]) / n
    cmax = max(abs(v) for s in (cfo, cfi, cff) for v in s) or 1
    czero = cp["t"] + (ch - cp["t"] - cp["b"]) / 2
    body = f'<line x1="{cp["l"]}" x2="{cw - cp["r"]}" y1="{czero:.1f}" y2="{czero:.1f}" stroke="{ZERO}"/>'
    names = ["Operating", "Investing", "Financing"]
    colours = [MID, RED, LIGHT]
    for i, y in enumerate(years):
        for j, v in enumerate((cfo[i], cfi[i], cff[i])):
            bh = abs(v) / cmax * ((ch - cp["t"] - cp["b"]) / 2)
            x = cp["l"] + i * cbw + cbw * .12 + j * cbw * .25
            yy = czero - bh if v >= 0 else czero
            body += (f'<rect{_tt(f"{names[j]} {y}: ₹{v:,.0f} cr")} x="{x:.1f}" y="{yy:.1f}" '
                     f'width="{cbw * .22:.1f}" height="{max(1,bh):.1f}" rx="2" '
                     f'fill="{colours[j]}" style="cursor:pointer"/>')
    legend = "".join(
        f'<g><rect x="{cp["l"] + i * 62}" y="4" width="9" height="9" rx="2" fill="{c}"/>'
        f'<text x="{cp["l"] + i * 62 + 13}" y="12.5" font-size="11.5" fill="{BODY}">{t}</text></g>'
        for i, (t, c) in enumerate(zip(["CFO", "CFI", "CFF"], colours)))
    xlabels = "".join(
        f'<text x="{cp["l"] + i * cbw + cbw / 2:.1f}" y="{ch - 8}" text-anchor="middle" '
        f'font-size="10.5" fill="{FAINT}" class="mono">{y}</text>'
        for i, y in enumerate(years) if i % 3 == 0)
    xs = [cp["l"] + i * cbw + cbw / 2 for i in range(n)]
    L = json.dumps([["CFO", MID, cfo], ["CFI", RED, cfi], ["CFF", LIGHT, cff]])
    script = (f"<script>(function(){{const Y={json.dumps(years)};const L={L};"
              "fcColumns('cash',Y,L,v=>Math.round(v).toLocaleString('en-IN'));})();</script>")
    inner = (body + crosshair(cw, ch - cp["b"], xs) + legend + xlabels
             + hit_columns(xs, cbw, ch - cp["b"]))
    return _svg(cw, ch, inner, "cash") + script, 300


# --- stacked area ----------------------------------------------------------------
def area_chart(cid, years, layers: list[tuple[str, str, list[float]]],
               hover: bool = True) -> tuple[str, int]:
    w, hh, p = 420, 250, {"l": 46, "r": 14, "t": 30, "b": 44}
    n = len(years)
    totals = [sum(l[2][i] for l in layers) for i in range(n)]
    mx = max(totals) * 1.04 or 1
    X = lambda i: p["l"] + i * (w - p["l"] - p["r"]) / max(1, n - 1)
    Yp = lambda v: hh - p["b"] - v / mx * (hh - p["t"] - p["b"])
    xs = [X(i) for i in range(n)]
    grid = "".join(
        f'<line x1="{p["l"]}" x2="{w - p["r"]}" y1="{p["t"] + fr * (hh - p["t"] - p["b"]):.1f}" '
        f'y2="{p["t"] + fr * (hh - p["t"] - p["b"]):.1f}" stroke="{GRID}"/>'
        f'<text x="{p["l"] - 6}" y="{p["t"] + fr * (hh - p["t"] - p["b"]) + 3.5:.1f}" '
        f'text-anchor="end" font-size="10" fill="{FAINT}" class="mono">'
        f'{round(mx * (1 - fr) / 1000)}k</text>'
        for fr in (0, .5, 1))
    base = [0.0] * n
    bands = ""
    for name, colour, vals in layers:
        top = [b + v for b, v in zip(base, vals)]
        d = " ".join(("L" if i else "M") + f"{xs[i]:.1f} {Yp(v):.1f}" for i, v in enumerate(top))
        d += " " + " ".join(f"L{xs[n - 1 - k]:.1f} {Yp(b):.1f}"
                            for k, b in enumerate(reversed(base)))
        # Tooltip + pointer cursor only when the chart is interactive. The
        # balance-sheet areas render with hover=False and must stay fully static
        # (no hover tooltip, no cursor change) per the design request.
        tt = _tt(name) if hover else ""
        cur = "cursor:pointer" if hover else "cursor:default"
        bands += (f'<path{tt} d="{d} Z" fill="{colour}" stroke="#fff" '
                  f'stroke-width=".6" style="{cur}"/>')
        base = top
    xlabels = "".join(
        f'<text x="{x:.1f}" y="{hh - p["b"] + 15}" text-anchor="middle" font-size="10.5" '
        f'fill="{FAINT}" class="mono">{y}</text>'
        for i, (y, x) in enumerate(zip(years, xs)) if i % 2 == 0)
    legend = "".join(
        f'<g transform="translate({p["l"] + (i % 3) * 118},{6 if i < 3 else 20})">'
        f'<rect x="0" y="-7" width="9" height="9" rx="2" fill="{c}"/>'
        f'<text x="14" y="1.5" font-size="10" fill="{BODY}">{nm}</text></g>'
        for i, (nm, c, _) in enumerate(layers))
    L = json.dumps([[nm, c, vals] for nm, c, vals in layers])
    script = ""
    inter = ""
    if hover:
        script = (f"<script>(function(){{const Y={json.dumps(years)};const L={L};"
                  "fcColumns('" + cid
                  + "',Y,L,v=>Math.round(v).toLocaleString('en-IN'));})();</script>")
        inter = (crosshair(w, hh - p["b"], xs)
                 + hit_columns(xs, (w - p["l"] - p["r"]) / max(1, n - 1),
                               hh - p["b"]))
    inner = grid + bands + inter + xlabels + legend
    return _svg(w, hh, inner, cid) + script, 300


# --- scorecard --------------------------------------------------------------------
def scorecard_chart(rows: list[tuple[str, str, float]]) -> tuple[str, int]:
    ordered = sorted(rows, key=lambda r: r[2], reverse=True)
    sw, row_h, lx = 430, 15, 150
    sh = len(ordered) * row_h + 18
    sx = lambda v: lx + v / 100 * (sw - lx - 44)
    marks = "".join(
        f'<line x1="{sx(v):.1f}" x2="{sx(v):.1f}" y1="0" y2="{len(ordered) * row_h + 1}" '
        f'stroke="{"#e0b6b0" if v == 40 else "#b6d6c3"}" stroke-width="1.5" stroke-dasharray="4 4"/>'
        f'<text x="{sx(v):.1f}" y="{sh - 5}" text-anchor="middle" font-size="9.5" '
        f'fill="{FAINT}" class="mono">{"WEAK 40" if v == 40 else "STRONG 66"}</text>'
        for v in (40, 66))
    bars = ""
    for i, (name, latest, sc) in enumerate(ordered):
        y = i * row_h + 4
        bars += (f'<g>'
                 f'<text x="{lx - 9}" y="{y + 8}" text-anchor="end" font-size="10" '
                 f'fill="#3f4744">{escape(name)}</text>'
                 f'<rect x="{lx}" y="{y}" width="{max(2, sx(sc) - lx):.1f}" height="9.5" '
                 f'rx="3" fill="{band(sc)}" opacity=".9"/>'
                 f'<text x="{sx(sc) + 6:.1f}" y="{y + 8}" font-size="9.5" font-weight="700" '
                 f'fill="{BODY}" class="mono">{latest}</text></g>')
    return _svg(sw, sh, marks + bars), len(ordered) * 15 + 30


# --- sector lens bars ---------------------------------------------------------------
def sector_bars(sectors: list[tuple[str, float]], hot: str) -> tuple[str, int]:
    sw, srh, slx = 430, 17, 178
    n = len(sectors)
    marks = "".join(
        f'<line x1="{slx + v / 100 * (sw - slx - 66):.1f}" '
        f'x2="{slx + v / 100 * (sw - slx - 66):.1f}" y1="0" y2="{n * srh}" '
        f'stroke="{"#e0b6b0" if v == 40 else "#b6d6c3"}" stroke-dasharray="4 4"/>'
        for v in (40, 66))
    rows = ""
    for i, (name, sc) in enumerate(sectors):
        y = i * srh + 4
        x2 = slx + sc / 100 * (sw - slx - 66)
        is_hot = name.strip().lower().startswith(hot.strip().lower()[:12])
        rows += (f'<g>'
                 f'<text x="{slx - 8}" y="{y + 8.5}" text-anchor="end" font-size="9.5" '
                 f'fill="{"#15201a" if is_hot else "#3f4744"}" '
                 f'font-weight="{"700" if is_hot else "400"}">{escape(name)}</text>'
                 f'<rect x="{slx}" y="{y}" width="{max(2, x2 - slx):.1f}" height="10" '
                 f'rx="3" fill="{band(sc)}" opacity="{1 if is_hot else .75}"/>'
                 f'<text x="{x2 + 6:.1f}" y="{y + 8.5}" font-size="9" font-weight="700" '
                 f'fill="{BODY}" class="mono">{sc:.0f} · {band_word(sc)}</text></g>')
    return _svg(sw, n * srh + 6, marks + rows), n * 17 + 26


# --- correlation heatmap --------------------------------------------------------------
def heatmap(labels: list[str], matrix: list[list[float]]) -> tuple[str, int]:
    cell, lab = 46, 118
    w, h = lab + len(labels) * cell + 6, len(labels) * cell + 112
    cells = ""
    for r, rn in enumerate(labels):
        for c, cn in enumerate(labels):
            v = matrix[r][c]
            if v >= 0:
                bg = (f"oklch({0.96 - abs(v) * .42:.3f} {0.02 + abs(v) * .11:.3f} 158)")
            else:
                bg = (f"oklch({0.96 - abs(v) * .40:.3f} {0.02 + abs(v) * .12:.3f} 28)")
            fg = "#fff" if abs(v) > .55 else BODY
            cells += (
                f'<g>'
                f'<rect x="{lab + c * cell}" y="{r * cell}" width="{cell - 2}" '
                f'height="{cell - 2}" rx="4" fill="{bg}"/>'
                f'<text x="{lab + c * cell + (cell - 2) / 2}" '
                f'y="{r * cell + (cell - 2) / 2 + 4}" text-anchor="middle" font-size="10.5" '
                f'font-weight="700" fill="{fg}" class="mono">{v:.2f}</text></g>')
    row_lab = "".join(
        f'<text x="{lab - 10}" y="{r * cell + cell / 2}" text-anchor="end" '
        f'font-size="11" fill="#3f4744">{escape(n)}</text>'
        for r, n in enumerate(labels))
    col_lab = "".join(
        f'<text x="{lab + c * cell + cell / 2}" y="{len(labels) * cell + 14}" '
        f'font-size="10" fill="{MUTED}" text-anchor="end" '
        f'transform="rotate(-35 {lab + c * cell + cell / 2} '
        f'{len(labels) * cell + 14})">{escape(n)}</text>'
        for c, n in enumerate(labels))
    leg_y = h - 16
    legend = (f'<g transform="translate({lab},{leg_y})">'
              f'<rect x="0" y="-9" width="12" height="12" rx="3" fill="{GREEN}"/>'
              f'<text x="18" y="1" font-size="10.5" fill="{BODY}">moves together</text>'
              f'<rect x="128" y="-9" width="12" height="12" rx="3" fill="{RED}"/>'
              f'<text x="146" y="1" font-size="10.5" fill="{BODY}">moves opposite</text></g>')
    svg = _svg(w, h, cells + row_lab + col_lab + legend)
    # never let a narrow card shrink the labels into oblivion - scroll instead
    wrap = ('<div style="overflow-x:auto"><div style="min-width:440px">'
            + svg + "</div></div>")
    return wrap, h + 14


# --- margin dial ------------------------------------------------------------------------
def dial(label: str, val: float, mx: float, sub: str,
         arc_colours: tuple[str, str]) -> tuple[str, int]:
    cx, cy, r = 96.0, 96.0, 66.0

    def pt(frac: float, rr: float) -> tuple[float, float]:
        a = math.pi + frac * math.pi
        return cx + math.cos(a) * rr, cy + math.sin(a) * rr

    def seg(f0: float, f1: float, colour: str) -> str:
        x0, y0 = pt(f0, r)
        x1, y1 = pt(f1, r)
        return (f'<path d="M {x0:.2f} {y0:.2f} A {r} {r} 0 0 1 {x1:.2f} {y1:.2f}" '
                f'fill="none" stroke="{colour}" stroke-width="17" stroke-linecap="butt"/>')

    frac = max(0.0, min(1.0, val / mx))
    nx, ny = pt(frac, r - 13)
    tip = _tt(f"{label}: {val:.2f}% on a 0-{mx:.0f}% scale")
    svg = _svg(192, 118,
               seg(0, .34, "#f0f2f0") + seg(.34, .67, arc_colours[0])
               + seg(.67, 1, arc_colours[1])
               + f'<g{tip} style="cursor:pointer">'
               + f'<line x1="{cx}" y1="{cy}" x2="{nx:.2f}" y2="{ny:.2f}" stroke="#1d2622" '
               + 'stroke-width="3.4" stroke-linecap="round"/>'
               + f'<circle cx="{cx}" cy="{cy}" r="7" fill="#1d2622"/></g>'
               + f'<text x="22" y="112" font-size="10" fill="{FAINT}" class="mono">0</text>'
               + f'<text x="170" y="112" font-size="10" fill="{FAINT}" '
               + f'text-anchor="end" class="mono">{mx:.0f}%</text>')
    html = (f'<div style="flex:1 1 0;min-width:120px;text-align:center">'
            f'<div style="font-size:13.5px;font-weight:700;color:#3f4744;padding-bottom:4px">'
            f'{label}</div>{svg}'
            f'<div style="font-size:23px;font-weight:800;letter-spacing:-.9px;'
            f'color:{INK};padding-top:4px">{val:.2f}%</div>'
            f'<div style="font-size:11.5px;color:{MUTED}">{sub}</div></div>')
    return html, 210


def dials_row(items: list[tuple[str, float, float, str, tuple[str, str]]]) -> tuple[str, int]:
    parts = [dial(*it)[0] for it in items]
    height = max(dial(*it)[1] for it in items)
    return ('<div style="display:flex;gap:14px;padding-top:10px;flex-wrap:wrap">'
            + "".join(parts) + "</div>", height)


# --- donuts -------------------------------------------------------------------------------
def donut(segs: list[tuple[str, float, str]], centre_big: str, centre_small: str,
          size: int = 140, sw: int = 17, fmt=None) -> tuple[str, int]:
    """fmt(v) renders each segment's value in the legend and tooltip; defaults to
    the crore-aware _human formatter."""
    fmt = fmt or _human
    r = 54 if size >= 140 else 52
    total = sum(v for _, v, _ in segs) or 1
    circ = 2 * 3.141592653589793 * r
    acc = 0.0
    rings = ""
    for nm, v, c in segs:
        dash = v / total * circ
        rings += (f'<circle{_tt(f"{nm}: {fmt(v)}")} cx="{size/2}" cy="{size/2}" r="{r}" '
                  f'fill="none" stroke="{c}" stroke-width="{sw}" '
                  f'stroke-dasharray="{dash:.2f} {circ - dash:.2f}" '
                  f'transform="rotate({-90 + acc / total * 360:.2f} {size/2} {size/2})" '
                  f'style="cursor:pointer"/>')
        acc += v
    half = size / 2
    ring = (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
            f'style="width:{size}px;height:{size}px;flex:none">'
            f'<circle cx="{half}" cy="{half}" r="{r}" fill="none" stroke="#f0f2f0" '
            f'stroke-width="{sw}"/>{rings}</svg>')
    centre = (f'<div style="position:absolute;inset:0;display:flex;flex-direction:column;'
              f'align-items:center;justify-content:center">'
              f'<div style="font-size:19px;font-weight:800;letter-spacing:-.6px;color:{INK}">'
              f'{centre_big}</div>'
              f'<div style="font-size:10.5px;color:{FAINT}">{centre_small}</div></div>')
    legend = "".join(
        f'<div style="display:flex;align-items:center;justify-content:space-between;'
        f'gap:10px"><span style="font-size:12.5px;color:{BODY}">{nm}</span>'
        f'<b style="font-size:13px;color:{INK};font-family:{MONO}">{fmt(v)}</b></div>'
        for nm, v, _ in segs)
    html = ('<div style="display:flex;align-items:center;gap:20px;padding:14px 0 6px;'
            'flex-wrap:wrap">'
            f'<div style="position:relative;width:{size}px;height:{size}px;flex:none">'
            f'{ring}{centre}</div>'
            f'<div style="flex:1;min-width:130px;display:flex;flex-direction:column;'
            f'gap:10px">{legend}</div></div>')
    return html, size + 60


def _human(v: float) -> str:
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:.2f}L cr"
    if abs(v) >= 1:
        return f"₹{v:,.0f} cr"
    return f"{v:.1f}"


# --- gauge ---------------------------------------------------------------------------------
def gauge(score: float, label: str, compact: bool = False) -> tuple[str, int]:
    score = max(0.0, min(100.0, float(score)))
    length = 3.141592653589793 * 92
    filled = length * score / 100
    dark = length * min(score, 22) / 100
    box_w, box_h = (190, 108) if compact else (236, 132)

    def arc(colour, dash):
        return (f'<path d="M 26 118 A 92 92 0 0 1 210 118" fill="none" stroke="{colour}" '
                f'stroke-width="30" stroke-linecap="round" stroke-dasharray="{dash:.1f} {length:.1f}"/>')

    hatch = ('<defs><pattern id="fcHatch" width="8" height="8" patternUnits="userSpaceOnUse"'
             ' patternTransform="rotate(-45)">'
             '<rect width="8" height="8" fill="#f2f3f1"/>'
             '<rect width="4" height="8" fill="#dcdfdc"/></pattern></defs>')
    svg = (_svg(236, 132,
                f'<g{_tt(f"Health index {score:.0f}/100 — {label}")} style="cursor:pointer">'
                + hatch
                + arc("url(#fcHatch)", length) + arc(MID, filled) + arc(GREEN_DARK, dark)
                + "</g>")
           .replace("<svg ", '<svg preserveAspectRatio="xMidYMid meet" ', 1))
    big = 32 if compact else 40
    small_fs = 11.5 if compact else 12.5
    html = (f'<div style="position:relative;width:100%;max-width:{box_w}px;'
            f'margin:10px auto 4px">{svg}'
            f'<div style="position:absolute;left:0;right:0;bottom:2px;text-align:center">'
            f'<div style="font-size:{big}px;font-weight:800;letter-spacing:-1.2px;'
            f'color:{INK};line-height:1">{label}</div>'
            f'<div style="font-size:{small_fs}px;color:{GREEN};font-weight:600">'
            f'Health index · {score:.0f}/100</div></div></div>')
    return html, box_h + 30


def gauge_legend(compact: bool = False) -> str:
    dot = 'width:11px;height:11px;border-radius:50%;display:inline-block;flex:none'
    risk_bg = 'repeating-linear-gradient(-45deg,#d9dcd9 0 3px,#f2f3f1 3px 6px)'
    fs = "12.5px" if not compact else "12.5px"
    strong_txt = "Strong" if not compact else "Strong — margins, cash cycle, profit growth"
    stable_txt = "Stable" if not compact else "Stable — leverage within sector norms"
    risk_txt = "Risk" if not compact else "Risk — interest cover, ROCE, cash conversion"
    return ('<div style="display:flex;flex-direction:column;gap:9px;flex:1 1 240px;'
            'min-width:200px">'
            f'<div style="display:flex;align-items:center;gap:8px"><i style="{dot};'
            f'background:{MID}"></i><span style="font-size:{fs};color:{BODY}">{strong_txt}</span></div>'
            f'<div style="display:flex;align-items:center;gap:8px"><i style="{dot};'
            f'background:{GREEN_DARK}"></i><span style="font-size:{fs};color:{BODY}">{stable_txt}</span></div>'
            f'<div style="display:flex;align-items:center;gap:8px"><i style="{dot};'
            f'background:{risk_bg}"></i><span style="font-size:{fs};color:{BODY}">{risk_txt}</span></div>'
            "</div>")


# --- ROCE spark ---------------------------------------------------------------------------
def spark(vals: list[float], colour_line: str = AMBER_TXT) -> tuple[str, int]:
    rw, rh = 300, 62
    mn, mx = min(vals), max(vals)
    px = lambda i: i / max(1, len(vals) - 1) * rw
    py = lambda v: rh - 4 - (v - mn) / ((mx - mn) or 1) * (rh - 10)
    line = " ".join(("L" if i else "M") + f"{px(i):.1f} {py(v):.1f}"
                    for i, v in enumerate(vals))
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(vals))
    svg = (_svg(rw, rh,
                '<defs><linearGradient id="spk" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0%" stop-color="#c9903a" stop-opacity=".3"/>'
                f'<stop offset="100%" stop-color="#c9903a" stop-opacity="0"/></defs>'
                f'<polygon points="{pts} {rw},{rh} 0,{rh}" fill="url(#spk)"/>'
                f'<path d="{line}" fill="none" stroke="{colour_line}" stroke-width="2" '
                f'stroke-linejoin="round" stroke-linecap="round"/>')
           )
    return svg, 70


# --- turnover rows --------------------------------------------------------------------------
def turnover_rows(rows: list[tuple[str, float, float]]) -> tuple[str, int]:
    out = ['<div style="display:flex;flex-direction:column;gap:16px;padding-top:10px">']
    for name, latest, mean in rows:
        colour = MID if latest >= mean else AMBER
        out.append(
            f'<div><div style="display:flex;justify-content:space-between;gap:10px;'
            f'font-size:14px;color:#3f4744;padding-bottom:7px"><span>{name}</span>'
            f'<span style="font-size:13px;flex:none;font-family:{MONO}">'
            f'<b style="color:{INK}">{latest:.2f}x</b>'
            f'<span style="color:{FAINT}">  mean {mean:.2f}x</span></span></div>'
            f'<div style="position:relative;height:11px;border-radius:11px;background:#f1f3f1">'
            f'<div{_tt(f"{name}: {latest:.2f}x vs mean {mean:.2f}x")} '
            f'style="width:{min(100, latest / 13 * 100):.1f}%;height:100%;border-radius:11px;'
            f'background:{colour};cursor:pointer"></div>'
            f'<div style="position:absolute;left:{min(100, mean / 13 * 100):.1f}%;'
            f'top:-3px;width:2px;height:17px;background:#0f3d27"></div></div></div>')
    out.append("</div>")
    return "".join(out), len(rows) * 44 + 20
