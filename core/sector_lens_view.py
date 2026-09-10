"""
sector_lens_view.py
-------------------
Sector Lens presentation, ported from assets/sector_lens_template.html (the
design source of truth) with every example number replaced by real FundaCheck
data. Self-contained Streamlit component: reference CSS (read verbatim from the
template) + real-data body + small JS engines (positioning plot, constituents
table sort, seasonality heatmap) + the existing frame-fit.

Design-only: all data/methodology come from the existing pipeline —
core.shell._company_vr / _sect_values (pooled sector metrics), the Screener-
sourced constituents, core.seasonality.market_heatmap (real IndianAPI index
history), core.tilt (structural profile) and core.market_context (current cycle).
Nothing here recomputes a sector metric or invents a value.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date
from html import escape
from pathlib import Path

_TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "sector_lens_template.html"
_CSS_CACHE: str | None = None


def _css() -> str:
    global _CSS_CACHE
    if _CSS_CACHE is None:
        t = _TEMPLATE.read_text(encoding="utf-8")
        _CSS_CACHE = t.split("<style>", 1)[1].split("</style>", 1)[0]
    return _CSS_CACHE


def _num(v):
    return v if isinstance(v, (int, float)) and v == v else None


def _pctx(v):  # "18.41x"
    v = _num(v)
    return f"{v:.2f}x" if v is not None else None


def _pct(v):   # "16.73%"
    v = _num(v)
    return f"{v:.2f}%" if v is not None else None


# --------------------------------------------------------------------------
# valuation / earnings headline — colour is driven by the comparison STATE,
# never by parsing the words. valuation below sector = cheaper = positive;
# earnings above sector = positive. Each clause is coloured independently, so
# "Priced below the sector. Earning above it." shows two green clauses.
# --------------------------------------------------------------------------
def _val_clause(word: str, state: str) -> str:
    cls = {"pos": "vpos", "neg": "vneg"}.get(state, "vneu")
    return f'<span class="{cls}">{escape(word)}</span>'


def _valuation_head(priced: str | None, earning: str | None,
                    sector_name: str) -> str:
    """priced ∈ {below, above, inline, None}; earning ∈ {above, below, inline, None}."""
    def _priced_frag(p):
        state = "pos" if p == "below" else "neg" if p == "above" else "neu"
        if p == "inline":
            return f'Priced {_val_clause("in line", state)} with the sector.'
        return f'Priced {_val_clause(p, state)} the sector.'

    def _earn_frag(e):
        state = "pos" if e == "above" else "neg" if e == "below" else "neu"
        word = "in line" if e == "inline" else e
        return f'Earning {_val_clause(word, state)} it.'

    if priced and earning:
        return f'{_priced_frag(priced)} {_earn_frag(earning)}'
    if priced:
        return _priced_frag(priced)
    return f'{escape(sector_name)} — company ratios unavailable.'


def _cycle_list(items: list[dict]) -> str:
    """Render one column of cycle headlines. Source name + date are PLAIN TEXT;
    the clickable links live ONLY in the Sources strip (the url stays in the data
    for that strip — it is never rendered as an <a> here)."""
    out = []
    for it in items or []:
        title = escape(str(it.get("title") or "").strip())
        expl = escape(str(it.get("explain") or "").strip())
        src = escape(str(it.get("source") or "").strip())
        dt = escape(str(it.get("date") or "").strip())
        url = escape(str(it.get("url") or "").strip())
        if not title and not expl:
            continue
        # The headline itself is the article link (opens in a new tab); the
        # source + date line below is clickable too, with a small ↗ affordance.
        if title:
            body = (f'<a class="h-ttl" href="{url}" target="_blank" '
                    f'rel="noopener noreferrer">{title}</a>' if url else f'<b>{title}</b>')
        else:
            body = ""
        if expl:
            body += (" &mdash; " if title else "") + expl
        meta = ""
        if src:
            lab = f'{src}{(" &middot; " + dt) if dt else ""}'
            if url:
                meta = (f'<a class="tsrc" href="{url}" target="_blank" rel="noopener noreferrer">'
                        f'{lab}<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                        f'stroke-width="2.2" stroke-linecap="round"><path d="M7 17L17 7M9 7h8v8"/>'
                        f'</svg></a>')
            else:
                meta = f'<span class="tsrc">{lab}</span>'
        out.append(f'<li>{body}{meta}</li>')
    return "".join(out)


# --------------------------------------------------------------------------
# the "gap, measure by measure" deviation bars (server-side; centre = sector)
# --------------------------------------------------------------------------
def _dev(name: str, you, sector, kind: str, note: str = "") -> str:
    you, sector = _num(you), _num(sector)
    val = lambda v: (_pctx(v) if kind == "x" else _pct(v))          # noqa: E731
    if you is None or sector is None:
        why = note or ("Book value missing from the uploaded model" if kind == "x"
                       else "Not in the uploaded model")
        return (f'<div class="dev"><div class="dev-h"><span class="dev-n">{escape(name)}</span>'
                f'<span class="dev-you">you <b>&mdash;</b></span>'
                f'<span class="dev-d d-na">Not available</span></div>'
                f'<div class="dev-track"><div class="dev-empty"></div></div>'
                f'<div class="dev-f"><span>{escape(why)}</span>'
                f'<span>sector {val(sector) or "&mdash;"}</span></div></div>')
    # Unified rule: BETTER than sector → green bar to the RIGHT; WORSE → red bar to
    # the LEFT. Centre line is the sector. "Better" is lower for valuation (cheaper)
    # and higher for returns/growth.
    if kind == "x":                       # valuation: lower (cheaper) is better
        gap = (you / sector - 1) * 100 if sector else 0.0     # +ve = pricier
        better = gap < 0
        width = min(abs(gap) / 50.0, 1.0) * 50.0
        mag = f'{abs(gap):.1f}%'
        word = " cheaper" if better else " pricier"
    else:                                 # returns/growth: higher is better
        cap = 40.0 if kind == "g" else 12.0   # growth swings wider than return ratios
        gap = you - sector
        better = gap >= 0
        width = min(abs(gap) / cap, 1.0) * 50.0
        mag = f'{abs(gap):.2f} pp'
        word = ""
    side = "right" if better else "left"
    barcls = "good" if better else "bad"
    arrow = "&#9650;" if better else "&#9660;"
    dcls = "d-pos" if better else "d-neg"
    dtxt = f'{arrow} {mag}{word}'
    dcolor = ' style="color:#0F5B34;background:#EEF4F0"' if better else ""
    fnote = f'<span>{escape(note)}</span>' if note else '<span></span>'
    return (f'<div class="dev"><div class="dev-h"><span class="dev-n">{escape(name)}</span>'
            f'<span class="dev-you">you <b>{val(you)}</b></span>'
            f'<span class="dev-d {dcls}"{dcolor}>{dtxt}</span></div>'
            f'<div class="dev-track"><div class="dev-bar {side} {barcls}" style="width:{width:.1f}%"></div></div>'
            f'<div class="dev-f">{fnote}<span>sector {val(sector)}</span></div></div>')


# --------------------------------------------------------------------------
# structural month strip (qualitative states -> bar height + colour). Never a
# percentage — the real returns live in the heatmap below.
# --------------------------------------------------------------------------
# Filled-block seasonality strip (height + colour encode the qualitative state).
_STATE_BLOCK = {"Strong": (48, "#177245"), "Positive": (34, "#2F9E63"),
                "Neutral": (20, "#C9D3CC"), "Soft": (26, "#E0B876"),
                "Weak": (34, "#C56B5C")}
_STATE_ORDER = ("Strong", "Positive", "Neutral", "Soft", "Weak")


def _months_strip(qual: dict) -> str:
    cells = (qual or {}).get("cells") or []
    out = []
    for c in cells:
        h, col = _STATE_BLOCK.get(c.get("state"), _STATE_BLOCK["Neutral"])
        out.append(f'<div class="mo" title="{escape(c["month"]+": "+c["state"])}">'
                   f'<i style="height:{h}px;background:{col}"></i>'
                   f'<span>{escape(c["month"]).upper()}</span></div>')
    return "".join(out)


def _seas_legend() -> str:
    return "".join(
        f'<span class="slg"><i style="background:{_STATE_BLOCK[s][1]}"></i>{s}</span>'
        for s in _STATE_ORDER)


# --------------------------------------------------------------------------
# current cycle mapping (label -> phase band position)
# --------------------------------------------------------------------------
_PHASE_POS = {"trough": 12, "recovery": 30, "early": 30, "early expansion": 30,
              "mid": 55, "mid-cycle": 55, "mid expansion": 55, "expansion": 68,
              "late": 81, "late expansion": 81, "slowdown": 92, "mixed": 50,
              "mixed / transitional": 50, "stable": 50}
_PHASE_NAME = {"trough": "Trough", "recovery": "Early", "early": "Early",
               "mid": "Mid", "mid-cycle": "Mid", "expansion": "Late expansion",
               "late": "Late expansion", "slowdown": "Slowdown", "mixed": "Mid",
               "stable": "Mid"}


def _cycle_phase(label: str) -> tuple[str, float]:
    k = (label or "").strip().lower()
    pos = _PHASE_POS.get(k)
    name = None
    if pos is None:
        for key in _PHASE_POS:
            if key in k:
                pos, name = _PHASE_POS[key], _PHASE_NAME.get(key, label)
                break
    if pos is None:
        return (label or "Mid expansion"), 55.0
    return (name or _PHASE_NAME.get(k, label or "Mid expansion")), float(pos)


# --------------------------------------------------------------------------
# decorative map backgrounds for the two cycle columns (inline SVG, no fetch)
# --------------------------------------------------------------------------
_MAP_WORLD = (
    '<span class="tmap world" aria-hidden="true">'
    '<svg viewBox="0 0 360 180" fill="#2B7A4B" xmlns="http://www.w3.org/2000/svg">'
    '<path d="M55 40 Q40 46 42 60 Q30 66 38 78 Q34 92 50 96 L64 84 Q58 70 70 66 '
    'Q66 52 82 52 L96 44 Q78 34 66 38 Z"/>'  # N America
    '<path d="M96 104 Q88 112 94 128 Q92 146 104 150 Q112 138 108 122 Q116 112 106 104 Z"/>'  # S America
    '<path d="M170 44 Q158 48 162 58 L176 56 Q186 48 176 42 Z"/>'  # Europe
    '<path d="M172 66 Q164 84 176 104 Q180 126 194 124 Q198 104 190 88 Q200 74 186 66 Z"/>'  # Africa
    '<path d="M198 40 Q212 34 240 40 Q276 40 300 54 Q288 70 268 66 Q252 78 236 68 '
    'Q214 70 206 58 Q196 50 198 40 Z"/>'  # Asia
    '<path d="M292 116 Q306 110 320 118 Q322 130 308 132 Q296 128 292 116 Z"/>'  # Australia
    '</svg></span>')
_MAP_INDIA = (
    '<span class="tmap india" aria-hidden="true">'
    '<svg viewBox="0 0 120 140" fill="#177245" xmlns="http://www.w3.org/2000/svg">'
    '<path d="M58 8 L70 11 L75 6 L82 12 L79 20 L90 22 L98 32 L93 40 L100 46 L95 55 '
    'L86 60 L84 72 L74 94 L66 114 L60 128 L54 112 L46 94 L40 76 L30 66 L23 55 '
    'L30 49 L25 40 L34 33 L41 39 L45 30 L51 20 Z"/>'
    '<circle cx="96" cy="118" r="3.4"/><circle cx="90" cy="126" r="2.4"/>'  # Sri Lanka hint
    '</svg></span>')

_ROOT = _TEMPLATE.parent.parent
_MAP_DIRS = (_ROOT / "images", _ROOT / "assets")   # prefer images/, then assets/


def _map_span(cls: str, base_names: tuple[str, ...], svg_fallback: str,
              max_w: int = 640) -> str:
    """Prefer a user-supplied map image (searched in images/ then assets/, over the
    given base names and common extensions); fall back to the inline SVG. Drop the
    dotted map images at images/assetsmap_world.png / images/assetsmap_india.png
    (or map_world.png / map_india.png) and they are embedded here verbatim."""
    for d in _MAP_DIRS:
        for base in base_names:
            for ext in ("png", "jpg", "jpeg", "webp"):
                p = d / f"{base}.{ext}"
                if p.exists():
                    try:
                        raw, mime = p.read_bytes(), ("jpeg" if ext == "jpg" else ext)
                        small = _downscale_png(raw, max_w)   # keep the payload light
                        if small is not None:
                            raw, mime = small, "png"
                        b64 = base64.b64encode(raw).decode("ascii")
                        return (f'<span class="tmap {cls}" aria-hidden="true">'
                                f'<img alt="" src="data:image/{mime};base64,{b64}"></span>')
                    except OSError:
                        pass
    return svg_fallback


def _downscale_png(raw: bytes, max_w: int = 760) -> bytes | None:
    """Shrink an oversized map to ~display width (2× for retina), preserving
    transparency. Returns None if Pillow is unavailable or no resize is needed."""
    try:
        import io
        from PIL import Image
    except Exception:                                    # noqa: BLE001
        return None
    try:
        im = Image.open(io.BytesIO(raw))
        if im.width <= max_w:
            return None
        im = im.convert("RGBA")
        h = round(im.height * max_w / im.width)
        im = im.resize((max_w, h), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:                                    # noqa: BLE001
        return None

_EXTRA_CSS = """
/* heatmap empty-state (index history not in the dataset yet) */
.hm.hm-empty{margin-top:14px}
.hm-empty .hm-note{margin-top:10px;padding:22px 18px;border:1px dashed #D7DED8;border-radius:12px;
  background:#FBFCFB;color:#8B918E;font-size:13px;line-height:1.6;max-width:70ch}
/* gap bars: green = better than sector (right), red = worse (left) */
.dev-bar.good.left{background:linear-gradient(270deg,#2F9E63,#7CC49A);border-radius:3px 0 0 3px}
.dev-bar.good.right{background:linear-gradient(90deg,#2F9E63,#7CC49A);border-radius:0 3px 3px 0}
.dev-bar.bad.left{background:linear-gradient(270deg,#B4483C,#C86A5F);border-radius:3px 0 0 3px}
.dev-bar.bad.right{background:linear-gradient(90deg,#B4483C,#C86A5F);border-radius:0 3px 3px 0}
/* filled-block seasonality strip + legend */
.months{display:flex;gap:6px;align-items:flex-end;padding:6px 0 2px}
.months .mo{flex:1;display:flex;flex-direction:column;align-items:stretch;gap:6px}
.months .mo i{display:block;width:100%;border-radius:5px 5px 3px 3px}
.months .mo span{font-family:var(--mono,inherit);font-size:9px;font-weight:700;
  letter-spacing:.4px;color:#8b918e;text-align:center}
.seas-lg{display:flex;flex-wrap:wrap;gap:6px 16px;padding:12px 0 2px}
.slg{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:#5b625d}
.slg i{width:11px;height:11px;border-radius:3px;display:inline-block}
/* map svgs behind the cycle columns */
.tmap svg{display:block;width:100%;height:auto}
/* headline links to the article; source + date below is clickable too */
.tlist li a.h-ttl{color:inherit;font-weight:800;text-decoration:none}
.tlist li a.h-ttl:hover{text-decoration:underline}
.tlist li .tsrc{display:block;margin-top:5px;font-size:11.5px;font-weight:600;
  color:var(--ink-3,#8b918e);text-decoration:none}
.tlist li a.tsrc:hover{color:var(--pos-deep,#0F5B34);text-decoration:underline}
.tlist li a.tsrc svg{width:11px;height:11px;vertical-align:-1px;margin-left:3px}
/* valuation headline: colour each clause by its comparison state */
.v-head .vpos{font-style:normal;color:var(--pos-deep,#0F5B34)}
.v-head .vneg{font-style:normal;color:var(--neg,#B4483C)}
.v-head .vneu{font-style:normal;color:inherit}
"""


def build(model, result, snap, sector_key, meta, context) -> tuple[str, int]:
    from . import shell as SH
    from . import tilt as TILT
    from . import seasonality as SEASON
    from . import sector_universe as U

    comp = SH._company_vr(model)
    sect, applic = SH._sect_values(snap)
    roce_app = applic["roce_applicable"]
    company = (model.company or "This company").title()

    sector_name = ((snap.get("sector_name") if snap else None)
                   or (U.UNIVERSES[sector_key].sector_name if sector_key in U.UNIVERSES else None)
                   or result.sector.name)
    idx_name = (snap or {}).get("reference_index", "") or (
        U.UNIVERSES[sector_key].index_label if sector_key in U.UNIVERSES else "")
    map_type = (snap or {}).get("mapping_type", "") or (
        U.UNIVERSES[sector_key].mapping_type if sector_key in U.UNIVERSES else "")
    inc = (snap or {}).get("included_count") or 0
    att = (snap or {}).get("constituent_count") or inc
    period = (snap or {}).get("financial_period") or (snap or {}).get("data_period") or ""

    match_label = {"exact": "Exact match", "approximate": "Approximate",
                   "proxy": "Broad proxy"}.get(map_type, "Standard basket")
    match_exact = map_type == "exact"

    constituents = list((snap or {}).get("constituents") or [])
    # identify the analysed company inside its own universe (for the You row)
    def _norm(s):
        return re.sub(r"[^a-z0-9]", "", str(s or "").lower())
    self_norm = _norm(model.company)
    total_mcap = sum(_num(r.get("market_cap")) or 0 for r in constituents) or 0.0
    self_row = next((r for r in constituents
                     if _norm(r.get("name")) and (_norm(r.get("name")) in self_norm
                     or self_norm in _norm(r.get("name")))), None)
    self_mcap = _num((self_row or {}).get("market_cap"))
    index_wt = (self_mcap / total_mcap * 100) if (self_mcap and total_mcap) else None

    # The uploaded model often omits P/B (needs book value) and sometimes P/E.
    # Fill them from this company's own Screener row so the gap panel isn't blank.
    if self_row:
        if comp.get("pb") is None and _num(self_row.get("pb")) is not None:
            comp["pb"] = _num(self_row.get("pb"))
        if comp.get("pe") is None and _num(self_row.get("pe")) is not None:
            comp["pe"] = _num(self_row.get("pe"))

    # ---- verdict facts + reading -----------------------------------------
    pe_gap = ((comp["pe"] / sect.get("pe") - 1) * 100
              if _num(comp["pe"]) and _num(sect.get("pe")) else None)
    roce_gap = (comp["roce"] - sect.get("roce")
                if _num(comp["roce"]) and _num(sect.get("roce")) else None)
    rets = [("ROE", comp["roe"], sect.get("roe")),
            ("ROCE", comp["roce"], sect.get("roce")),
            ("ROA", comp["roa"], sect.get("roa"))]
    rets_av = [(n, _num(y), _num(s)) for n, y, s in rets if _num(y) is not None and _num(s) is not None]
    below = sum(1 for _, y, s in rets_av if y < s)

    # Valuation vs sector: below (cheaper) is positive, above is negative; a
    # near-zero gap (±2%) is neutral. Earnings vs sector: driven by how many
    # return measures sit below the aggregate (a clean tie is neutral).
    if pe_gap is None:
        priced = None
    elif pe_gap > 2.0:
        priced = "above"
    elif pe_gap < -2.0:
        priced = "below"
    else:
        priced = "inline"
    if not rets_av:
        earning = None
    elif below > len(rets_av) / 2:
        earning = "below"
    elif below < len(rets_av) / 2:
        earning = "above"
    else:
        earning = "inline"
    head = _valuation_head(priced, earning, sector_name)

    body_bits = []
    if pe_gap is not None:
        body_bits.append(f'{company} trades <b>{abs(pe_gap):.1f}% '
                         f'{"above" if pe_gap>=0 else "below"}</b> the universe on earnings')
    if roce_gap is not None:
        body_bits.append(f'while returning <b>{abs(roce_gap):.2f} points '
                         f'{"less" if roce_gap<0 else "more"}</b> on capital employed')
    verdict_body = (". ".join(body_bits[:1]) + (" " + body_bits[1] if len(body_bits) > 1 else "")
                    + ". ") if body_bits else ""
    if rets_av:
        verdict_body += (f'{below} of {len(rets_av)} returns measures sit '
                         f'{"below" if below else "at or above"} the aggregate. ')
    verdict_body += ('This is an <b>exact sector match</b> — the comparison set is the '
                     'company’s own index.' if match_exact else
                     'This is a <b>broad-proxy</b> comparison set.')

    facts = []
    facts.append(("Valuation gap",
                  (f'{"+" if pe_gap>=0 else "&minus;"}{abs(pe_gap):.1f}', "%",
                   pe_gap is not None and pe_gap >= 0) if pe_gap is not None else ("&mdash;", "", False)))
    facts.append(("ROCE gap",
                  (f'{"+" if roce_gap>=0 else "&minus;"}{abs(roce_gap):.2f}', " pp",
                   roce_gap is not None and roce_gap < 0) if roce_gap is not None else ("&mdash;", "", False)))
    facts.append(("Measures below sector",
                  (str(below), f" of {len(rets_av)}", False) if rets_av else ("&mdash;", "", False)))
    facts.append(("Index weight",
                  (f'{index_wt:.1f}', "%", False) if index_wt is not None else ("&mdash;", "", False)))
    facts_html = "".join(
        f'<div class="v-fact"><div class="v-fact-l">{escape(l)}</div>'
        f'<div class="v-fact-v{" is-neg" if neg else ""}">{v}<small>{u}</small></div></div>'
        for l, (v, u, neg) in facts)

    # ---- positioning plot payload ----------------------------------------
    # The vertical axis is a return-on-capital measure. ROCE is not meaningful
    # for lenders (banks/exchanges), so fall back to ROE, then ROA — whichever the
    # sector aggregate actually carries. This lets ANY loaded company be plotted
    # from its own model values, even when it is not itself an index constituent.
    ret_key, ret_label, sec_ret = "roce", "ROCE", (_num(sect.get("roce")) if roce_app else None)
    if sec_ret is None or _num(comp.get("roce")) is None:
        for _k, _lab in (("roe", "ROE"), ("roa", "ROA")):
            if _num(sect.get(_k)) is not None and _num(comp.get(_k)) is not None:
                ret_key, ret_label, sec_ret = _k, _lab, _num(sect.get(_k))
                break
    self_ret_gap = (_num(comp.get(ret_key)) - sec_ret) \
        if (sec_ret is not None and _num(comp.get(ret_key)) is not None) else None

    peers = []
    for r in constituents:
        if r is self_row:
            continue
        pe, ret = _num(r.get("pe")), _num(r.get(ret_key))
        if pe is None or ret is None:
            continue
        peers.append({"n": r.get("name"), "tk": r.get("nse_symbol"),
                      "cmp": _num(r.get("cmp")), "pe": pe, "roce": ret})
    self_pt = None
    if _num(comp.get("pe")) is not None and _num(comp.get(ret_key)) is not None \
            and _num(sect.get("pe")) is not None and sec_ret is not None:
        self_pt = {"n": company, "tk": (self_row or {}).get("nse_symbol") or "",
                   "cmp": _num((self_row or {}).get("cmp")),
                   "pe": comp["pe"], "roce": _num(comp.get(ret_key)),
                   "peGap": pe_gap, "roceGap": self_ret_gap}

    # ---- vitals ----------------------------------------------------------
    vitals = [
        ("#177245", "Constituents", f"{inc}", "", f"{inc} of {att} in the universe matched"),
        ("#C68A2E", "Sector P/E", _pctx(sect.get("pe")) or "&mdash;", "", "Loss-makers excluded"),
        ("#C68A2E", "Sector P/B", _pctx(sect.get("pb")) or "&mdash;", "", "On year-end equity"),
        ("#2F9E63", "Sector ROE", _pct(sect.get("roe")) or "&mdash;", "", "Pooled aggregate"),
        ("#2F9E63", "Sector ROCE", (_pct(sect.get("roce")) if roce_app else "&mdash;") or "&mdash;",
         "", "Pooled aggregate" if roce_app else "Not meaningful for lenders"),
        ("#2F9E63", "Sector ROA", _pct(sect.get("roa")) or "&mdash;", "", "Pooled aggregate"),
    ]
    vitals_html = ""
    for col, lab, v, u, s in vitals:
        vv = v.replace("x", '<small>x</small>').replace("%", '<small>%</small>')
        vitals_html += (f'<div class="vital"><div class="vital-l"><i style="background:{col}"></i>'
                        f'{escape(lab)}</div><div class="vital-v">{vv}</div>'
                        f'<div class="vital-s">{escape(s)}</div></div>')

    # ---- growth: company (annual, from the model) vs sector --------------
    from . import sections as S

    def _comp_growth(*names):
        s = S.pct_series(S.ser(model, *names))
        return float(s.iloc[-1]) if not s.empty else None
    comp_sales_g = _comp_growth("Sales Growth", "Revenue Growth")
    comp_profit_g = _comp_growth("Net Profit Growth", "PAT Growth", "Net Profit Growth %")

    def _sector_median(key):
        vals = [_num(r.get(key)) for r in constituents]
        vals = sorted(v for v in vals if v is not None)
        if not vals:
            return None
        n = len(vals)
        return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    sec_sales_g = _sector_median("revenue_growth_yoy")
    sec_profit_g = _num(sect.get("growth"))
    if sec_profit_g is None:
        sec_profit_g = _sector_median("eps_ttm_growth")

    # ---- gap panel -------------------------------------------------------
    gap_html = (
        '<div class="dev-grp">What you pay</div>'
        + _dev("P/E", comp["pe"], sect.get("pe"), "x")
        + _dev("P/B", comp["pb"], sect.get("pb"), "x")
        + '<div class="dev-grp">What you get</div>'
        + _dev("ROE", comp["roe"], sect.get("roe"), "pct")
        + _dev("ROCE", comp["roce"], (sect.get("roce") if roce_app else None), "pct",
               note="The widest gap on the page" if (roce_gap is not None and abs(roce_gap) >= 8) else "")
        + _dev("ROA", comp["roa"], sect.get("roa"), "pct")
        + '<div class="dev-grp">How it’s growing</div>'
        + _dev("Sales growth", comp_sales_g, sec_sales_g, "g",
               note="Latest annual YoY vs sector median")
        + _dev("Profit growth", comp_profit_g, sec_profit_g, "g",
               note="Latest annual YoY vs sector aggregate")
    )

    # ---- behaves: tags + structural text + month strip -------------------
    prof = TILT.profile(sector_key or "generic")
    qual = SEASON.qualitative(sector_key)
    months_html = _months_strip(qual)
    legend_html = _seas_legend()
    seas_caption = escape(qual.get("methodology") or
                          "Structural business pattern, not a price backtest.")
    seas_sub = ("Q4-weighted &mdash; a structural pattern, not a price backtest"
                if not qual.get("flat") else
                "Low intra-year seasonality &mdash; driven by the price cycle, not the calendar")
    tilt_state = (snap or {}).get("current_tilt") or "Stable"
    tags_html = (f'<span class="tag t-warn">{escape(prof.get("nature","Cyclical"))}</span>'
                 f'<span class="tag t-pos">{escape(tilt_state)} tilt</span>')

    # ---- historical heatmap (real IndianAPI index history, latest 6 yrs) --
    hm = SEASON.market_heatmap(sector_key)
    hm_payload = None
    if hm.get("sufficient"):
        rows = hm.get("rows") or []
        # The index price history begins 3 Dec 2021, so 2021 carries December only —
        # a near-empty row that reads as blank. Drop 2021 and any year too thin to
        # be meaningful (fewer than 3 real months); the window starts at 2022.
        rows = [r for r in rows
                if r.get("year") != 2021
                and sum(c is not None for c in r["cells"]) >= 3]
        rows6 = rows[:6]                      # newest-first
        yrs = [r["year"] for r in rows6]
        win = f"{min(yrs)}–{max(yrs)}" if yrs else ""
        n_yrs = len(yrs)
        # average across the shown window, per calendar month (real obs only)
        avg = []
        for mi in range(12):
            col = [r["cells"][mi] for r in rows6 if r["cells"][mi] is not None]
            avg.append(round(sum(col) / len(col), 2) if col else None)
        hm_payload = {
            "index": hm.get("index"), "window": win, "n_years": n_yrs,
            "months": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
            "avg": avg,
            "rows": [{"year": r["year"], "cells": r["cells"],
                      "yearly": r["yearly"], "ytd": r["ytd"]} for r in rows6],
        }

    # ---- current cycle ---------------------------------------------------
    ctx = context or {}
    phase, phase_pos = _cycle_phase(ctx.get("label") or tilt_state)
    glob_items = list(ctx.get("global_items") or [])
    india_items = list(ctx.get("india_items") or [])
    have_news = bool(glob_items or india_items)

    if have_news:
        global_html = _cycle_list(glob_items) or f'<li>{escape(prof.get("text",""))}</li>'
        india_html = _cycle_list(india_items) or (
            '<li>Indian producers in this sector track the same structural cycle; '
            'domestic demand, policy and the rupee shape the pass-through.</li>')
    else:
        global_html = f'<li>{escape(prof.get("text",""))}</li>'
        india_html = ('<li>Indian producers in this sector track the same structural '
                      'cycle; domestic demand, policy and the rupee shape the '
                      'pass-through.</li>')

    watch = [str(x) for x in (ctx.get("drivers") or []) if str(x).strip()]

    # Market tilt — 2-3 sentences (LLM current read; structural fallback).
    mkt_tilt = str(ctx.get("tilt") or "").strip()
    if len(mkt_tilt) < 40:
        mkt_tilt = (f"{phase}. The current read is built from the live headlines above; "
                    "watch the named drivers for the next turn in the cycle.")

    # Fundamental tilt — 2-3 sentences from the pooled snapshot itself.
    sec_g = _num(sect.get("growth"))
    fun_bits = []
    base_reason = (snap or {}).get("tilt_reason")
    if base_reason:
        fun_bits.append(str(base_reason).rstrip("."). rstrip() + ".")
    if sec_g is not None:
        fun_bits.append(f"The pooled snapshot shows sector earnings growth at "
                        f"{'+' if sec_g >= 0 else '&minus;'}{abs(sec_g):.1f}% year on year.")
    if pe_gap is not None:
        fun_bits.append(f"{company} trades {abs(pe_gap):.1f}% "
                        f"{'above' if pe_gap >= 0 else 'below'} the universe on earnings, "
                        "so the reported numbers are the read that carries the risk.")
    fun_tilt = " ".join(fun_bits) or (
        "Baseline established from the latest snapshot fundamentals.")

    cyc_note = ("What is moving the sector globally, and how it reaches Indian producers."
                if have_news else "Structural read — live news context was not available, so "
                "this is the sector's standing cycle profile.")
    map_world_html = _map_span("world", ("assetsmap_world", "map_world"), _MAP_WORLD, 640)
    map_india_html = _map_span("india", ("assetsmap_india", "map_india"), _MAP_INDIA, 380)
    watch_html = "".join(f'<span class="wtag">{escape(w)}</span>' for w in watch[:6])

    # ---- constituents table payload --------------------------------------
    def _w(r):
        mc = _num(r.get("market_cap"))
        return round(mc / total_mcap * 100, 2) if (mc and total_mcap) else None
    cons_rows = []
    for r in sorted(constituents, key=lambda x: _num(x.get("market_cap")) or 0, reverse=True):
        cons_rows.append({
            "rank": r.get("rank"), "name": r.get("name"), "tk": r.get("nse_symbol"),
            "url": r.get("screener_url") or (f"https://www.screener.in/company/{r.get('nse_symbol')}/"
                                             if r.get("nse_symbol") else "https://www.screener.in/"),
            "self": r is self_row,
            "cmp": _num(r.get("cmp")), "mcap": _num(r.get("market_cap")),
            "pe": _num(r.get("pe")), "pb": _num(r.get("pb")), "roce": _num(r.get("roce")),
            "roa": _num(r.get("roa")), "opm": _num(r.get("opm")), "npm": _num(r.get("npm")),
            "rev": _num(r.get("revenue_growth_yoy")), "eps": _num(r.get("eps_ttm_growth")),
            "de": _num(r.get("debt_to_equity")), "ic": _num(r.get("interest_coverage")),
            "dy": _num(r.get("dividend_yield")), "wt": _w(r),
        })

    payload = {
        "plot": {"sec_pe": _num(sect.get("pe")), "sec_roce": sec_ret,
                 "ret_label": ret_label, "peers": peers, "self": self_pt},
        "hm": hm_payload,
        "cons": {"sec_roce": _num(sect.get("roce")) or 0, "rows": cons_rows, "n": len(cons_rows)},
    }

    mkt_src = (meta or {}).get("market_source") or "Screener.in"
    fund_src = (meta or {}).get("fundamentals_source") or "IndianAPI + NSE"
    mkt_date = (meta or {}).get("market_snapshot_date") or (snap or {}).get("snapshot_date") or ""

    hm_years = (hm_payload or {}).get("n_years", 0)
    hm_index = (hm_payload or {}).get("index") or idx_name
    hm_earliest = (hm or {}).get("earliest") or ""
    hm_meth = (f"Monthly return = (last close ÷ first close − 1) of each month for "
               f"{hm_index}; average row = mean of each month across {hm_years} years. "
               f"Real IndianAPI index history from 2022 — all returns computed in Python.")

    # The heatmap section is permanent: when the reference index has no stored
    # price history yet (not every NIFTY sub-index is in the dataset), show an
    # honest note in its place rather than leaving the panel blank.
    if hm_payload:
        hm_html = f'''<div class="hm">
          <div class="hm-h"><span class="hm-t">Historical market seasonality</span>
            <span class="hm-s">{escape(hm_index)} &middot; monthly price returns by calendar year</span>
            <span class="hm-c">{hm_years}-year window</span></div>
          <div class="hm-w"><table class="hmt" id="hmt"></table></div>
          <div class="hm-f"><b>Read down a column, not across a row.</b>
            <span>{escape(hm_meth)}</span>
            <span class="hm-key" style="margin-left:auto"><i style="background:#B4483C"></i>Worst</span>
            <span class="hm-key"><i style="background:#E6BDB6"></i></span>
            <span class="hm-key"><i style="background:#F2F4F2"></i>Flat</span>
            <span class="hm-key"><i style="background:#A9D6BE"></i></span>
            <span class="hm-key"><i style="background:#2F9E63"></i>Best</span></div>
        </div>'''
    else:
        ref = escape(hm_index or "the sector index")
        hm_html = (f'<div class="hm hm-empty"><div class="hm-h">'
                   f'<span class="hm-t">Historical market seasonality</span>'
                   f'<span class="hm-s">{ref} &middot; monthly price returns by calendar year</span></div>'
                   f'<div class="hm-note">Not available for this sector yet — the monthly price '
                   f'history for {ref} has not been fetched into the seasonality dataset, so no '
                   f'real returns can be shown. The structural pattern above still applies.</div></div>')

    body = f"""<div id="shell">
  <div class="phead">
    <div><h1>Sector lens</h1>
      <div class="sub">Where {escape(company)} sits inside its reference universe, and what that universe is doing.</div>
    </div>
    <div class="feed">
      <div class="feed-i"><div class="feed-l">Market data</div><div class="feed-v">{escape(str(mkt_src))} &middot; <i>daily</i></div></div>
      <div class="feed-i"><div class="feed-l">Fundamentals</div><div class="feed-v">{escape(str(fund_src))} &middot; periodic</div></div>
      <div class="feed-i"><div class="feed-l">Period</div><div class="feed-v">{escape(str(period) or "&mdash;")}</div></div>
    </div>
  </div>

  <section class="verdict">
    <div class="v-left">
      <div class="v-sector">
        <span class="v-name">{escape(sector_name)}</span>
        <span class="v-match"><b></b>{escape(match_label)}</span>
        <span class="v-univ">{escape(idx_name)} &middot; {inc} of {att} matched</span>
      </div>
      <div class="v-eyebrow">The reading</div>
      <h2 class="v-head">{head}</h2>
      <p class="v-body">{verdict_body}</p>
      <div class="v-facts">{facts_html}</div>
    </div>
    <div class="v-right">
      <div class="v-right-h"><span class="v-right-t">Position against the universe</span>
        <span class="v-right-s">Hover any point</span></div>
      <svg class="plot" id="plot" viewBox="0 0 440 300" role="img" aria-label="Positioning plot: P/E vs sector on the x-axis, ROCE vs sector on the y-axis.">
        <rect x="46" y="26" width="164" height="112" fill="#2F9E63" opacity=".055"/>
        <rect x="210" y="26" width="164" height="112" fill="#C68A2E" opacity=".055"/>
        <rect x="46" y="138" width="164" height="112" fill="#C68A2E" opacity=".05"/>
        <rect x="210" y="138" width="164" height="112" fill="#B4483C" opacity=".075"/>
        <g stroke="#E6EBE7" stroke-width="1">
          <line x1="46" y1="63" x2="374" y2="63"/><line x1="46" y1="101" x2="374" y2="101"/>
          <line x1="46" y1="175" x2="374" y2="175"/><line x1="46" y1="213" x2="374" y2="213"/>
          <line x1="113" y1="26" x2="113" y2="250"/><line x1="177" y1="26" x2="177" y2="250"/>
          <line x1="243" y1="26" x2="243" y2="250"/><line x1="308" y1="26" x2="308" y2="250"/></g>
        <rect x="46" y="26" width="328" height="224" fill="none" stroke="#DCE3DD"/>
        <line x1="210" y1="26" x2="210" y2="250" stroke="#9AA09D" stroke-width="1.5"/>
        <line x1="46" y1="138" x2="374" y2="138" stroke="#9AA09D" stroke-width="1.5"/>
        <text class="qlab" x="54" y="40" fill="#2F9E63">Cheaper &middot; earning more</text>
        <text class="qlab" x="366" y="40" fill="#C68A2E" text-anchor="end">Pricier &middot; earning more</text>
        <text class="qlab" x="54" y="244" fill="#C68A2E">Cheaper &middot; earning less</text>
        <text class="qlab" x="366" y="244" fill="#B4483C" text-anchor="end">Pricier &middot; earning less</text>
        <text class="tick" x="113" y="264" text-anchor="middle">&minus;60%</text>
        <text class="tick" x="177" y="264" text-anchor="middle">&minus;20%</text>
        <text class="tick" x="210" y="264" text-anchor="middle">0</text>
        <text class="tick" x="243" y="264" text-anchor="middle">+20%</text>
        <text class="tick" x="308" y="264" text-anchor="middle">+60%</text>
        <text class="tick" x="40" y="66" text-anchor="end">+20pp</text>
        <text class="tick" x="40" y="104" text-anchor="end">+10pp</text>
        <text class="tick" x="40" y="141" text-anchor="end">0</text>
        <text class="tick" x="40" y="178" text-anchor="end">&minus;10pp</text>
        <text class="tick" x="40" y="216" text-anchor="end">&minus;20pp</text>
        <text class="axlab" x="210" y="284" text-anchor="middle">P/E versus sector aggregate &rarr;</text>
        <text class="axlab" x="14" y="138" text-anchor="middle" transform="rotate(-90 14 138)">{ret_label} versus sector &rarr;</text>
        <g id="peers"></g><g id="selfg"></g>
        <g stroke="#8B918E" stroke-width="1.6">
          <line x1="203" y1="138" x2="217" y2="138"/><line x1="210" y1="131" x2="210" y2="145"/></g>
      </svg>
      <div id="ptip"></div>
      <div class="plot-key">
        <span class="pk"><i class="dot"></i>This company</span>
        <span class="pk"><i class="pdot"></i>{len(peers)} constituents</span>
        <span class="pk"><i class="cross"></i>Sector aggregate</span>
      </div>
    </div>
  </section>

  <section class="vitals">{vitals_html}
    <div class="vitals-foot"><b>Pooled, not averaged</b>
      <span>&mdash; totals are summed across the universe before the ratio is taken, so large constituents carry proportionate weight (&Sigma; market cap &divide; &Sigma; earnings, equity, etc.).</span></div>
  </section>

  <div class="cols">
    <section class="panel">
      <div class="p-h"><div><div class="p-t">The gap, measure by measure</div>
        <div class="p-note">Sector aggregate is the centre line. Bars run left when the company falls below it, right when it clears it.</div></div></div>
      <div class="dev-scale"><span>Worse than sector</span><span>Sector aggregate</span><span>Better than sector</span></div>
      {gap_html}
      <div class="dev-legend">
        <span><i style="background:linear-gradient(90deg,#2F9E63,#7CC49A)"></i>Better than sector &middot; right</span>
        <span><i style="background:linear-gradient(90deg,#B4483C,#C86A5F)"></i>Worse than sector &middot; left</span>
        <span style="color:#9AA09D">Valuation scales to &plusmn;50%; returns to &plusmn;12 pp; growth to &plusmn;40 pp.</span></div>
    </section>

    <section class="panel">
      <div class="p-h"><div class="p-t">How this sector behaves</div><div class="tags">{tags_html}</div></div>
      <p class="cy-body">{escape(prof.get("text",""))}</p>
      <div class="seas">
        <div class="seas-h"><span class="seas-t">Typical business seasonality</span>
          <span class="seas-s">{seas_sub}</span></div>
        <div class="months">{months_html}</div>
        <div class="seas-lg">{legend_html}</div>
        <p class="seas-note">{seas_caption}</p>
        {hm_html}
      </div>
    </section>
  </div>

  <section class="cycle">
    <div class="cy-head"><div><div class="p-t">Where the cycle sits now</div>
      <div class="p-note">{escape(cyc_note)}</div></div>
      <div class="tags"><span class="tag t-pos">{escape(phase)}</span></div></div>
    <div class="cy-band">
      <i style="background:#B4483C;opacity:.5"></i><i style="background:#D9A441;opacity:.55"></i>
      <i style="background:#2F9E63;opacity:.7"></i><i style="background:#C68A2E;opacity:.75"></i>
      <span class="cy-mark" style="left:{phase_pos:.0f}%"></span></div>
    <div class="cy-names"><span>Trough</span><span>Early</span><span>Mid</span><span class="on">{escape(phase)}</span></div>
    <div class="tsplit">
      <div class="tcol global">{map_world_html}<div class="tc-h">
        <span class="tc-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 2.5 15.4 0 18M12 3c-2.5 2.6-2.5 15.4 0 18"/></svg></span>
        <div><div class="tc-t">Global drivers</div><div class="tc-s">Sector complex</div></div></div>
        <ul class="tlist">{global_html}</ul></div>
      <div class="tcol india">{map_india_html}<div class="tc-h">
        <span class="tc-ic"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s7-5.7 7-11a7 7 0 1 0-14 0c0 5.3 7 11 7 11z"/><circle cx="12" cy="10" r="2.6"/></svg></span>
        <div><div class="tc-t">Indian read-through</div><div class="tc-s">Domestic producers</div></div></div>
        <ul class="tlist">{india_html}</ul></div>
    </div>
    {"" if not watch_html else f'<div class="watch"><span class="watch-l">Watch</span>{watch_html}</div>'}
    <div class="tilts">
      <div class="tilt mkt"><span class="tilt-c">Market tilt</span><div class="tilt-b">{escape(mkt_tilt)}</div></div>
      <div class="tilt fun"><span class="tilt-c">Fundamental tilt</span><div class="tilt-b">{fun_tilt}</div></div>
    </div>
  </section>

  <section class="cons">
    <div class="cons-h"><div><div class="p-t">The universe, company by company</div>
      <div class="p-note">Ranked by market cap. ROCE is shaded against the sector aggregate. Sort any column; company names open the Screener page.</div></div>
      <div class="cons-ctl"><button class="tbtn on" id="btn10">Top 10</button>
        <button class="tbtn" id="btnAll">All {len(cons_rows)}</button></div></div>
    <div class="twrap"><table class="ct" id="ctab"><thead><tr>
      <th class="lft" data-k="rank" data-t="n">#</th>
      <th class="lft" data-k="name" data-t="s">Company</th>
      <th class="cmp" data-k="cmp" data-t="n">CMP &#8377;</th>
      <th data-k="mcap" data-t="n" class="srt">Market cap</th>
      <th data-k="pe" data-t="n">P/E</th><th data-k="pb" data-t="n">P/B</th>
      <th data-k="roce" data-t="n">ROCE</th><th data-k="roa" data-t="n">ROA</th>
      <th data-k="opm" data-t="n">OPM</th><th data-k="npm" data-t="n">NPM</th>
      <th data-k="rev" data-t="n">Rev gr</th><th data-k="eps" data-t="n">EPS gr</th>
      <th data-k="de" data-t="n">D/E</th><th data-k="ic" data-t="n">Int cov</th>
      <th data-k="dy" data-t="n">Div yld</th><th data-k="wt" data-t="n">Index wt</th>
    </tr></thead><tbody id="ctbody"></tbody></table></div>
    <div class="cons-f"><b>Live market data</b>
      <span>CMP, P/E, ROCE, growth and dividend yield from the Screener.in snapshot{(", " + escape(str(mkt_date))) if mkt_date else ""}. Sector aggregation uses this same universe.</span>
      <span class="r">Values absent from the snapshot show &mdash;.</span></div>
  </section>

  <div class="foot"><span class="foot-l">Sources</span>
    <span class="foot-v">Screener.in &middot; IndianAPI &middot; NSE &middot; uploaded model</span>
    <span class="foot-r">Matched to {escape(idx_name)}{(" &middot; " + escape(str(period))) if period else ""}</span></div>
</div>"""

    data_json = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    html = (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:'
        'wght@400;500;600;700;800&display=swap" rel="stylesheet">'
        f"<style>{_css()}{_EXTRA_CSS}</style></head><body>{body}"
        f"<script>window.__FC_SL__ = {data_json};</script>"
        f"<script>{_SL_JS}</script><script>{_FIT_JS}</script></body></html>"
    )
    return html, 1600


# --------------------------------------------------------------------------
# client engines: positioning plot, constituents table, seasonality heatmap
# --------------------------------------------------------------------------
_SL_JS = r"""
(function(){
var D = window.__FC_SL__ || {};
/* ---------- positioning plot ---------- */
var P = D.plot || {}, XR = 100, YR = 30;
function clamp(v,a,b){ return Math.max(a, Math.min(b, v)); }
function px(g){ return 210 + clamp(g,-XR,XR) * (164/XR) * 0.955; }
function py(g){ return 138 - clamp(g,-YR,YR) * (112/YR) * 0.945; }
function sgn(v,u,dp){ return (v>=0?"+":"−") + Math.abs(v).toFixed(dp||1) + u; }
var tip = document.getElementById("ptip"), plot = document.getElementById("plot");
var RL = (P.ret_label || "ROCE");
var pts = [];
if (P.sec_pe && P.sec_roce != null){
  pts = (P.peers||[]).map(function(r){
    var pg = (r.pe/P.sec_pe - 1)*100, rg = r.roce - P.sec_roce;
    return {n:r.n, tk:r.tk, cmp:r.cmp, pe:r.pe, roce:r.roce, peGap:pg, roceGap:rg,
            x:px(pg), y:py(rg), edge: Math.abs(pg)>XR || Math.abs(rg)>YR};
  });
}
var peersEl = document.getElementById("peers");
if (peersEl) peersEl.innerHTML = pts.map(function(p,i){
  return '<g class="pt'+(p.edge?" edge":"")+'" data-i="'+i+'">'
    + '<circle class="core" cx="'+p.x.toFixed(1)+'" cy="'+p.y.toFixed(1)+'" r="4"/>'
    + '<circle class="hit" cx="'+p.x.toFixed(1)+'" cy="'+p.y.toFixed(1)+'" r="12"/></g>';
}).join("");
var SELF = P.self;
if (SELF){
  var sx = px(SELF.peGap), sy = py(SELF.roceGap);
  document.getElementById("selfg").innerHTML =
      '<line x1="210" y1="'+sy.toFixed(1)+'" x2="'+sx.toFixed(1)+'" y2="'+sy.toFixed(1)+'" stroke="#B4483C" stroke-width="1" stroke-dasharray="3 3" opacity=".55"/>'
    + '<line x1="'+sx.toFixed(1)+'" y1="138" x2="'+sx.toFixed(1)+'" y2="'+sy.toFixed(1)+'" stroke="#B4483C" stroke-width="1" stroke-dasharray="3 3" opacity=".55"/>'
    + '<g class="pt" id="selfpt"><circle cx="'+sx.toFixed(1)+'" cy="'+sy.toFixed(1)+'" r="15" fill="#B4483C" opacity=".13"/>'
    + '<circle cx="'+sx.toFixed(1)+'" cy="'+sy.toFixed(1)+'" r="7.5" fill="#B4483C" stroke="#fff" stroke-width="2.2"/>'
    + '<circle class="hit" cx="'+sx.toFixed(1)+'" cy="'+sy.toFixed(1)+'" r="16"/></g>'
    + '<text class="dotlab" x="'+(sx-12).toFixed(1)+'" y="'+(sy+23).toFixed(1)+'" text-anchor="end">'+SELF.n+'</text>';
}
function tipHTML(p){
  return '<div class="n">'+p.n+'</div><div class="tk">'+(p.tk||"")+'</div>'
    + (p.cmp!=null?'<div class="r"><span>CMP</span><b>₹'+p.cmp.toLocaleString("en-US",{minimumFractionDigits:2})+'</b></div>':"")
    + '<div class="r"><span>P/E</span><b>'+p.pe.toFixed(2)+'x</b></div>'
    + '<div class="r"><span>'+RL+'</span><b>'+p.roce.toFixed(2)+'%</b></div>'
    + '<div class="r gap"><span>vs sector P/E</span><b class="'+(p.peGap>0?"dn":"up")+'">'+sgn(p.peGap,"%")+'</b></div>'
    + '<div class="r"><span>vs sector '+RL+'</span><b class="'+(p.roceGap>=0?"up":"dn")+'">'+sgn(p.roceGap," pp",2)+'</b></div>';
}
function showTip(p, ev){
  tip.innerHTML = tipHTML(p); tip.style.display = "block";
  var host = plot.parentElement.getBoundingClientRect();
  var x = ev.clientX-host.left+14, y = ev.clientY-host.top-tip.offsetHeight/2;
  if (x+tip.offsetWidth > host.width-6) x = ev.clientX-host.left-tip.offsetWidth-14;
  tip.style.left = Math.max(6,x)+"px";
  tip.style.top = clamp(y,6,host.height-tip.offsetHeight-6)+"px";
}
function bindPt(el,p){ if(!el) return;
  el.addEventListener("mousemove", function(ev){ showTip(p,ev); });
  el.addEventListener("mouseleave", function(){ tip.style.display="none"; }); }
if (peersEl) peersEl.querySelectorAll(".pt").forEach(function(g){ bindPt(g, pts[+g.dataset.i]); });
if (SELF) bindPt(document.getElementById("selfpt"), SELF);

/* ---------- constituents table ---------- */
var CO = D.cons || {rows:[]}, SEC_ROCE = CO.sec_roce || 0;
var showAll = false, sortKey = "mcap", sortAsc = false;
function na(){ return '<td class="pend">&mdash;</td>'; }
function x2(v,u){ return v==null ? na() : '<td>'+v.toFixed(2)+(u||"")+'</td>'; }
function pctc(v){ if(v==null) return na();
  var c=v>=0?"above":"below"; return '<td><span class="'+c+'">'+(v>=0?"+":"−")+Math.abs(v).toFixed(2)+'%</span></td>'; }
function roceCell(v){ return v==null?na():'<td class="'+(v>=SEC_ROCE?"above":"below")+'">'+v.toFixed(2)+'%</td>'; }
function render(){
  var rows = CO.rows.slice();
  rows.sort(function(a,b){ var x=a[sortKey], y=b[sortKey];
    if (x==null) return 1; if (y==null) return -1;
    if (typeof x==="string") return sortAsc?(x<y?-1:x>y?1:0):(x>y?-1:x<y?1:0);
    return sortAsc? x-y : y-x; });
  if (!showAll) rows = rows.slice(0,10);
  document.getElementById("ctbody").innerHTML = rows.map(function(r){
    return '<tr class="'+(r.self?"self":"")+'">'
      + '<td class="rk">'+String(r.rank==null?"":r.rank).padStart(2,"0")+'</td>'
      + '<td class="nm"><a href="'+r.url+'" target="_blank" rel="noopener noreferrer">'+r.name+'</a>'
        + (r.self?'<span class="you">You</span>':'') + '<em>'+(r.tk||"")+' ↗</em></td>'
      + (r.cmp==null?na():'<td class="cmp">₹'+r.cmp.toLocaleString("en-US",{minimumFractionDigits:2})+'</td>')
      + (r.mcap==null?na():'<td>₹'+Math.round(r.mcap).toLocaleString("en-US")+' Cr</td>')
      + x2(r.pe,'x') + x2(r.pb,'x') + roceCell(r.roce) + x2(r.roa,'%')
      + x2(r.opm,'%') + x2(r.npm,'%') + pctc(r.rev) + pctc(r.eps)
      + x2(r.de,'') + x2(r.ic,'x') + x2(r.dy,'%')
      + (r.wt==null?na():'<td class="wt">'+r.wt.toFixed(2)+'%</td>')
      + '</tr>';
  }).join("");
  document.querySelectorAll("#ctab th").forEach(function(th){
    th.classList.toggle("srt", th.dataset.k===sortKey);
    th.classList.toggle("asc", th.dataset.k===sortKey && sortAsc); });
}
document.querySelectorAll("#ctab th").forEach(function(th){
  th.addEventListener("click", function(){ var k=th.dataset.k;
    if (k===sortKey) sortAsc=!sortAsc; else { sortKey=k; sortAsc=(th.dataset.t==="s"); }
    render(); }); });
var b10=document.getElementById("btn10"), bAll=document.getElementById("btnAll");
if(b10) b10.addEventListener("click", function(){ showAll=false; b10.classList.add("on"); bAll.classList.remove("on"); render(); });
if(bAll) bAll.addEventListener("click", function(){ showAll=true; bAll.classList.add("on"); b10.classList.remove("on"); render(); });
render();

/* ---------- seasonality heatmap ---------- */
var HM = D.hm;
function hmMix(a,b,t){ return "rgb("+[0,1,2].map(function(i){return Math.round(a[i]+(b[i]-a[i])*t);}).join(",")+")"; }
function hmCell(v, cap){
  if (v==null) return '<td style="background:#FCFDFC;color:#C9CFCB">&middot;</td>';
  var t = Math.min(Math.abs(v)/(cap||30),1), base=[255,255,255];
  var bg = v>=0 ? hmMix(base,[47,158,99],t) : hmMix(base,[180,72,60],t);
  var fg = t>0.58 ? "#FFFFFF" : "#2B332F";
  return '<td style="background:'+bg+';color:'+fg+'">'+(v>=0?"":"−")+Math.abs(v).toFixed(2)+'%</td>';
}
if (HM){
  var t = document.getElementById("hmt");
  if (t){
    var h = '<thead><tr><th class="yr">Year</th>'
      + HM.months.map(function(m){ return '<th>'+m+'</th>'; }).join("") + '<th class="tot">Year</th></tr></thead><tbody>';
    h += '<tr class="avg"><td class="yr">Average<br>by month</td>'
      + HM.avg.map(function(v){ return hmCell(v,12); }).join("")
      + '<td class="tot" style="background:#FBFCFB;color:#9AA09D">&mdash;</td></tr>';
    HM.rows.forEach(function(r){
      var yc = (r.yearly==null) ? '<td class="tot" style="background:#FBFCFB;color:#9AA09D">&mdash;</td>'
        : hmCell(r.yearly,120).replace('<td ','<td class="tot" ');
      var ylab = r.year + (r.ytd?' <span style="font-size:8px;color:#9AA09D">YTD</span>':'');
      h += '<tr><td class="yr">'+ylab+'</td>' + r.cells.map(function(v){ return hmCell(v,30); }).join("") + yc + '</tr>';
    });
    t.innerHTML = h + '</tbody>';
  }
}
})();
"""

_FIT_JS = r"""
(function(){
  function fit(){
    try{ var s=document.getElementById('shell'); if(!s) return;
      var h=Math.ceil(s.getBoundingClientRect().height)+58;
      try{ window.parent.postMessage({isStreamlitMessage:true,type:'streamlit:setFrameHeight',height:h},'*'); }catch(e){}
      try{ var fe=window.frameElement;
        if(fe){ var cur=parseInt(fe.style.height)||0;
          if(Math.abs(cur-h)>1){ fe.style.setProperty('height',h+'px','important'); fe.setAttribute('height',h); }
          var el=fe.parentElement;
          for(var i=0;i<5&&el;i++){ var tid=el.getAttribute&&el.getAttribute('data-testid');
            if(tid==='stElementContainer'||tid==='stVerticalBlock'||tid==='stVerticalBlockBorderWrapper'){
              if(el.style.height!=='auto') el.style.height='auto'; el.style.minHeight='0px'; }
            if(tid==='stMain'||tid==='stAppViewContainer') break; el=el.parentElement; } }
      }catch(e){}
    }catch(e){}
  }
  function schedule(){fit();for(var k=1;k<=12;k++)setTimeout(fit,k*250);}
  window.addEventListener('load',schedule); schedule();
  if(window.ResizeObserver){ var ro=new ResizeObserver(fit); var s=document.getElementById('shell');
    if(s) ro.observe(s); ro.observe(document.documentElement); if(document.body) ro.observe(document.body); }
  window.addEventListener('resize',fit);
  if(window.visualViewport){ window.visualViewport.addEventListener('resize',fit); window.visualViewport.addEventListener('scroll',fit); }
  setInterval(fit,1000);
})();
"""
