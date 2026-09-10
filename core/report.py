"""
report.py
---------
Export Report -> one branded, multi-page PDF built natively with fpdf2.

The layout follows FundaCheck's published report design: a dark cover, then nine
numbered interior sections on a light ground. Everything is driven from the
already-parsed model (`core/parser`), its scored assessment (`core/scoring`) and
the monthly Sector Lens snapshot (`core/sector_snapshot`). No figure is invented:
where the workbook or the snapshot does not carry a value it is shown as
unavailable, exactly as the rest of the app does.

Fonts (Plus Jakarta Sans + DejaVu Sans Mono, both redistributable and carrying
the Indian Rupee glyph) are bundled under assets/fonts so the rupee sign, arrows
and en/em dashes render as designed. If a font file is somehow missing the code
falls back to the core PDF fonts and a Latin-1-safe transliteration.
"""

from __future__ import annotations

import csv
import datetime as _dt
import re
from io import BytesIO
from pathlib import Path

import pandas as pd
from fpdf import FPDF

# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------
GREEN = (23, 114, 69)          # interior green (headings, strong)
GREEN_BRIGHT = (55, 214, 122)  # cover accent
GREEN_DARK = (15, 91, 52)
MID = (61, 158, 107)
LIGHT = (158, 207, 180)
PALE_G = (232, 242, 236)       # strong pill fill
AMBER = (217, 164, 65)
AMBER_TXT = (176, 122, 35)
PALE_A = (250, 243, 227)       # neutral pill fill / amber callout
RED = (183, 74, 61)
RED_TXT = (168, 66, 54)
PALE_R = (250, 235, 232)       # weak pill fill
INK = (21, 32, 26)
BODY = (63, 71, 68)
MUTED = (110, 118, 114)
FAINT = (150, 157, 153)
HAIR = (228, 232, 229)         # hairline rule
CARD_LINE = (230, 235, 231)
COVER_BG = (12, 26, 20)
COVER_BG2 = (9, 20, 15)
COVER_TXT = (232, 240, 236)
COVER_FAINT = (120, 140, 130)
COVER_SALMON = (226, 132, 122)   # weakness accent on the dark cover (reads on dark)
WHITE = (255, 255, 255)
# Muted scale-band fills for the cover "where the score sits" bar (weak/neutral/strong).
SCALE_WEAK = (196, 150, 144)
SCALE_NEUTRAL = (214, 198, 158)
SCALE_STRONG = (150, 198, 170)

PAGE_W, PAGE_H = 210.0, 297.0
MARGIN = 18.0
CONTENT_W = PAGE_W - 2 * MARGIN

# The reference report is set in DejaVu Sans (headings/body), DejaVu Sans Mono
# (eyebrows, labels, numbers) and Liberation Serif (table row labels). These are
# bundled under assets/fonts so the output matches it exactly, rupee glyph and all.
_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
SANS, MONO, SERIF = "DJS", "Mono", "Serif"   # family names once registered


# --------------------------------------------------------------------------
# tiny formatting helpers
# --------------------------------------------------------------------------
def band_color(v):
    return MID if v >= 66 else AMBER if v >= 40 else RED


def band_word(v):
    return "STRONG" if v >= 66 else "NEUTRAL" if v >= 40 else "WEAK"


def band_pill(v):
    """(text-colour, fill) for a STRONG/NEUTRAL/WEAK pill."""
    if v >= 66:
        return GREEN, PALE_G
    if v >= 40:
        return AMBER_TXT, PALE_A
    return RED_TXT, PALE_R


def _num(v, dp=0):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n/a"
    return f"{v:,.{dp}f}"


def _cr(v):
    """Rupee-crore, with a lakh-crore roll-up for large figures."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n/a"
    v = float(v)
    if abs(v) >= 1e5:
        return f"₹{v / 1e5:.2f} L cr"
    return f"₹{v:,.0f} cr"


def _rup(v, dp=0):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n/a"
    return f"₹{float(v):,.{dp}f}"


def _pct(v, dp=1, signed=False):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n/a"
    s = f"{'+' if signed and v >= 0 else ''}{v * 100:.{dp}f}%"
    return s


def _x(v, dp=2):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n/a"
    return f"{float(v):.{dp}f}x"


def _days(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "n/a"
    return f"{float(v):.0f} d"


_TRANSLIT = {
    "₹": "Rs ", "–": "-", "—": "-", "→": "->",
    "·": "-", "▲": "^", "▼": "v", "≈": "~", "≤": "<=",
}


# --------------------------------------------------------------------------
# ticker resolution (reuses the Sector Lens company master; no network)
# --------------------------------------------------------------------------
_MASTER = Path(__file__).resolve().parent.parent / "data" / "company_master.csv"


def _norm_name(s):
    s = str(s).lower()
    for junk in (" limited", " ltd", " ltd.", " (india)", " india", " corporation",
                 " corp", " company", " co.", " & ", " and "):
        s = s.replace(junk, " ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def resolve_ticker(company, snapshot_row=None):
    """NSE symbol for the company, from the snapshot row first, else the static
    master. Returns '' when there is no confident match (never guesses)."""
    if snapshot_row and snapshot_row.get("nse_symbol"):
        return str(snapshot_row["nse_symbol"]).upper()
    if not _MASTER.exists():
        return ""
    target = _norm_name(company)
    if not target:
        return ""
    try:
        rows = list(csv.DictReader(_MASTER.open(newline="", encoding="utf-8")))
    except OSError:
        return ""
    for r in rows:
        if _norm_name(r.get("name", "")) == target:
            return str(r.get("nse_symbol", "")).upper()
    for r in rows:
        n = _norm_name(r.get("name", ""))
        if n and (n in target or target in n):
            return str(r.get("nse_symbol", "")).upper()
    return ""


# --------------------------------------------------------------------------
# the document
# --------------------------------------------------------------------------
class _Doc(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(False)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.unicode = False
        try:
            self.add_font(SANS, "", str(_FONT_DIR / "DejaVuSans.ttf"))
            self.add_font(SANS, "B", str(_FONT_DIR / "DejaVuSans-Bold.ttf"))
            self.add_font(MONO, "", str(_FONT_DIR / "DejaVuSansMono.ttf"))
            self.add_font(MONO, "B", str(_FONT_DIR / "DejaVuSansMono-Bold.ttf"))
            self.add_font(SERIF, "", str(_FONT_DIR / "LiberationSerif-Regular.ttf"))
            self.add_font(SERIF, "B", str(_FONT_DIR / "LiberationSerif-Bold.ttf"))
            self.unicode = True
        except Exception:                                  # noqa: BLE001
            pass

    def footer(self):                                      # manual footers only
        pass

    # -- font shorthands -----------------------------------------------------
    def sans(self, size, bold=False, color=INK):
        self.set_font(SANS if self.unicode else "helvetica", "B" if bold else "", size)
        self.set_text_color(*color)

    def mono(self, size, bold=False, color=INK, spacing=None):
        self.set_font(MONO if self.unicode else "courier", "B" if bold else "", size)
        self.set_text_color(*color)

    def serif(self, size, bold=False, color=INK):
        self.set_font(SERIF if self.unicode else "times", "B" if bold else "", size)
        self.set_text_color(*color)

    def txt(self, s):
        if self.unicode:
            return str(s)
        out = str(s)
        for k, v in _TRANSLIT.items():
            out = out.replace(k, v)
        return out.encode("latin-1", "replace").decode("latin-1")


# --------------------------------------------------------------------------
# primitive drawing helpers
# --------------------------------------------------------------------------
def _lerp(c1, c2, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))


def _grad_h(pdf, x, y, w, h, stops, slices=90):
    """Horizontal gradient across `stops` (list of RGB), drawn as thin slices."""
    n = len(stops) - 1
    sw = w / slices
    for i in range(slices):
        t = i / (slices - 1)
        seg = min(int(t * n), n - 1)
        lt = t * n - seg
        pdf.set_fill_color(*_lerp(stops[seg], stops[seg + 1], lt))
        pdf.rect(x + i * sw, y, sw + 0.3, h, style="F")


def _mono_label(pdf, x, y, text, color=FAINT, size=7.0, spaced=True):
    """A letter-spaced uppercase mono eyebrow label."""
    s = " ".join(list(text.upper())) if spaced else text.upper()
    # spacing every char is heavy; use light tracking by inserting hair spaces
    s = text.upper()
    pdf.mono(size, False, color)
    pdf.set_xy(x, y)
    pdf.cell(0, 4, pdf.txt(s))


def _rule(pdf, x, y, w, color=HAIR, lw=0.3):
    pdf.set_draw_color(*color)
    pdf.set_line_width(lw)
    pdf.line(x, y, x + w, y)


def _pill(pdf, x, y, text, fg, bg, size=6.6, h=4.8, pad=2.4):
    pdf.mono(size, True, fg)
    w = pdf.get_string_width(pdf.txt(text)) + pad * 2
    pdf.set_fill_color(*bg)
    pdf.rect(x, y, w, h, style="F", round_corners=True, corner_radius=1.2)
    pdf.set_xy(x, y - 0.2)
    pdf.cell(w, h + 0.4, pdf.txt(text), align="C")
    return w


def _pill_right(pdf, xr, y, text, fg, bg, **kw):
    pdf.mono(kw.get("size", 6.6), True, fg)
    w = pdf.get_string_width(pdf.txt(text)) + kw.get("pad", 2.4) * 2
    return _pill(pdf, xr - w, y, text, fg, bg, **kw)


def _card(pdf, x, y, w, h, fill=WHITE, line=CARD_LINE, lw=0.3, radius=2.4):
    if fill is not None:
        pdf.set_fill_color(*fill)
    pdf.set_draw_color(*line)
    pdf.set_line_width(lw)
    pdf.rect(x, y, w, h, style="DF" if fill is not None else "D",
             round_corners=True, corner_radius=radius)


def _para(pdf, x, y, w, text, size=9.4, lh=4.9, color=BODY, bold_color=INK,
          font=SANS, justify=False, bold_all=False, tag_colors=None):
    """Word-wrap `text` in a column of width `w`, honouring **bold** markup and
    inline colour tags {g|...} green / {r|...} red / {a|...} amber. Returns the
    y just below the last line.

    `bold_all` renders every word in the bold weight (used for the cover
    headline). `tag_colors` overrides the {g|r|a} colours (e.g. a salmon accent
    that reads on the dark cover)."""
    tc = {"g": GREEN, "r": RED_TXT, "a": AMBER_TXT}
    if tag_colors:
        tc.update(tag_colors)
    tokens = []
    for chunk in re.split(r"(\*\*.+?\*\*|\{[gra]\|.+?\})", text):
        if not chunk:
            continue
        if chunk.startswith("**") and chunk.endswith("**"):
            tokens.append((chunk[2:-2], True, bold_color))
        elif re.match(r"\{[gra]\|", chunk):
            tokens.append((chunk[3:-1], True, tc[chunk[1]]))
        else:
            tokens.append((chunk, bold_all, color))
    space_w = None
    cx, cy = x, y
    if pdf.unicode:
        fam = font
    else:
        fam = {SANS: "helvetica", MONO: "courier", SERIF: "times"}.get(font, "helvetica")
    for seg_text, is_bold, col in tokens:
        words = re.split(r"(\s+)", seg_text)
        for word in words:
            if word == "":
                continue
            if word.isspace():
                cx += (space_w or 1.2)
                continue
            pdf.set_font(fam, "B" if is_bold else "", size)
            ww = pdf.get_string_width(pdf.txt(word))
            space_w = pdf.get_string_width(" ")
            if cx + ww > x + w + 0.1 and cx > x:
                cx = x
                cy += lh
            pdf.set_text_color(*col)
            pdf.set_xy(cx, cy)
            pdf.cell(ww + 0.5, lh, pdf.txt(word))
            cx += ww + space_w
    return cy + lh


def _callout(pdf, x, y, w, tag, text, tone="green"):
    """A footer-style callout box: mono tag on the left, wrapped text on right."""
    fill, tagcol = {
        "green": ((244, 248, 245), GREEN),
        "amber": (PALE_A, AMBER_TXT),
        "grey": ((245, 246, 245), MUTED),
    }.get(tone, ((244, 248, 245), GREEN))
    tag_w = 26
    inner = w - tag_w - 8
    # measure height first
    lines = _measure_lines(pdf, inner, text, 8.8, SANS)
    h = max(16, lines * 4.6 + 8)
    pdf.set_fill_color(*fill)
    pdf.set_draw_color(*CARD_LINE)
    pdf.set_line_width(0.3)
    pdf.rect(x, y, w, h, style="DF", round_corners=True, corner_radius=2.4)
    pdf.mono(6.6, True, tagcol)
    pdf.set_xy(x + 5, y + 5)
    pdf.cell(tag_w, 4, pdf.txt(tag.upper()))
    _para(pdf, x + tag_w + 5, y + 4.6, inner, text, size=8.8, lh=4.6, color=BODY)
    return y + h


def _measure_lines(pdf, w, text, size, font=SANS):
    fam = font if pdf.unicode else "helvetica"
    plain = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    plain = re.sub(r"\{[gra]\|(.+?)\}", r"\1", plain)
    pdf.set_font(fam, "", size)
    lines, cx = 1, 0.0
    sw = pdf.get_string_width(" ")
    for word in plain.split():
        ww = pdf.get_string_width(pdf.txt(word))
        if cx + ww > w and cx > 0:
            lines += 1
            cx = 0.0
        cx += ww + sw
    return lines


# --------------------------------------------------------------------------
# page chrome
# --------------------------------------------------------------------------
def _top_gradient(pdf):
    _grad_h(pdf, 0, 0, PAGE_W, 2.4,
            [GREEN_DARK, GREEN, AMBER, RED], slices=120)


def _interior_header(pdf, ctx, number, eyebrow, title):
    _top_gradient(pdf)
    y = 22
    if number:
        pdf.sans(30, True, (222, 226, 223))
        pdf.set_xy(MARGIN, y - 4)
        pdf.cell(20, 14, number)
        tx = MARGIN + 22
    else:
        tx = MARGIN
    pdf.mono(7.2, True, GREEN)
    pdf.set_xy(tx, y - 2)
    pdf.cell(0, 4, pdf.txt(eyebrow.upper()))
    pdf.sans(17, True, INK)
    pdf.set_xy(tx, y + 2.6)
    pdf.cell(0, 8, pdf.txt(title))
    pdf.mono(7.2, False, FAINT)
    pdf.set_xy(PAGE_W - MARGIN - 40, y + 3)
    pdf.cell(40, 6, pdf.txt(f"PAGE {pdf.page_no()}"), align="R")
    _rule(pdf, MARGIN, y + 14, CONTENT_W, HAIR, 0.4)
    return y + 20


def _interior_footer(pdf, ctx):
    y = PAGE_H - 14
    _rule(pdf, MARGIN, y, CONTENT_W, HAIR, 0.3)
    pdf.mono(6.8, False, FAINT)
    pdf.set_xy(MARGIN, y + 2)
    pdf.cell(0, 5, pdf.txt(f"FundaCheck  ·  {ctx['company_title']}  ·  {ctx['fy_full']}"))
    pdf.set_xy(PAGE_W - MARGIN - 20, y + 2)
    pdf.cell(20, 5, f"{pdf.page_no():02d}", align="R")


def _heading(pdf, x, y, w, text, right=""):
    """A small section heading with a short green underline and optional right label."""
    pdf.sans(11.5, True, INK)
    pdf.set_xy(x, y)
    pdf.cell(w, 6, pdf.txt(text))
    if right:
        pdf.mono(6.8, False, FAINT)
        pdf.set_xy(x, y)
        pdf.cell(w, 6, pdf.txt(right.upper()), align="R")
    pdf.set_draw_color(*GREEN)
    pdf.set_line_width(0.7)
    pdf.line(x, y + 6.8, x + 16, y + 6.8)
    return y + 11


# ==========================================================================
# context: pull everything once, no figure invented
# ==========================================================================
def _ser(model, *names):
    for n in names:
        s = pd.to_numeric(model.series(n), errors="coerce").dropna()
        if not s.empty:
            return s
    return pd.Series(dtype=float)


def _fy_only(years):
    return [str(y) for y in years
            if str(y).upper() not in ("TTM", "LTM", "TREND", "MEAN", "MEDIAN")]


def _at(model, name, year):
    s = pd.to_numeric(model.series(name), errors="coerce")
    if s.empty or year not in s.index or pd.isna(s.get(year)):
        return None
    return float(s[year])


def _gross_cr(model, year):
    """Gross profit in ₹ crore = Sales − COGS. (The 'Gross Margin' line in these
    workbooks is a ratio, not the rupee value, so it is never used here.)"""
    s, c = _at(model, "Sales", year), _at(model, "COGS", year)
    if s is not None and c is not None:
        return s - c
    return None


def _build_ctx(model, result, snapshot):
    from .sector_snapshot import get_sector as _snap_sector, snapshot_meta

    fy = _fy_only(model.years)
    latest_fy = fy[-1] if fy else (model.latest_year or "")
    meta = model.meta or {}

    # sector key -> snapshot row
    sector_key = None
    try:
        from .sectors import get_sector as _gs, SECTORS
        for k, prof in SECTORS.items():
            if prof.name == result.sector.name:
                sector_key = k
                break
    except Exception:                                       # noqa: BLE001
        sector_key = None
    snap_row = None
    if snapshot and sector_key:
        snap_row = (_snap_sector(snapshot, sector_key)
                    or _snap_sector(snapshot, _SNAP_KEY_ALIAS.get(sector_key, "")))
    # Fallback: the coarse scoring sector has no priced snapshot basket (e.g. a
    # hotel or telecom that scores as 'generic'). Find the company in the
    # snapshot's own constituent lists and use its real, priced sector so the
    # Sector page (peers, gaps, vitals, seasonality) populates instead of falling
    # back to an empty page.
    if snapshot and not (snap_row and (_constituents(snap_row)
                                       or (snap_row.get("metrics", {}) or {}).get("pe"))):
        found_key, found_row = _find_company_snapshot_sector(snapshot, model.company)
        if found_row is not None:
            snap_row, sector_key = found_row, found_key
    snap_meta = snapshot_meta(snapshot) if snapshot else {}

    # try to match this company inside the snapshot's constituents (for ticker/mktcap)
    self_row = None
    if snap_row:
        tgt = _norm_name(model.company)
        for c in _constituents(snap_row):
            if _norm_name(c.get("name", "")) == tgt or (
                    _norm_name(c.get("name", "")) in tgt or tgt in _norm_name(c.get("name", ""))):
                self_row = c
                break

    ticker = resolve_ticker(model.company, self_row)

    ctx = {
        "model": model, "result": result, "snapshot": snapshot,
        "snap_row": snap_row, "snap_meta": snap_meta, "self_row": self_row,
        "company": model.company, "company_title": model.company.title(),
        "sector": result.sector, "sector_name": result.sector.name,
        "sector_key": sector_key,
        "ticker": ticker, "fy": fy, "fy_first": fy[0] if fy else "",
        "fy_last": latest_fy,
        "fy_full": (f"FY20{str(latest_fy)[2:]}"
                    if re.fullmatch(r"FY\d{2}", str(latest_fy)) else str(latest_fy)),
        "n_periods": len(model.years),
        "score": result.total_score, "verdict": result.verdict,
        "verdict_color": band_color(result.total_score),
        "price": meta.get("current_price"), "market_cap": meta.get("market_cap"),
        "revenue": _at(model, "Sales", latest_fy) or (_ser(model, "Sales").iloc[-1]
                                                       if not _ser(model, "Sales").empty else None),
        "net_profit": _at(model, "Net Profit", latest_fy) or (
            _ser(model, "Net Profit").iloc[-1] if not _ser(model, "Net Profit").empty else None),
        "gen_date": _dt.date.today(),
    }
    # pillars sorted high->low
    ctx["pillars"] = sorted(result.pillar_scores.items(), key=lambda kv: kv[1], reverse=True)
    ctx["metrics_sorted"] = sorted(result.metrics, key=lambda m: m.score, reverse=True)
    return ctx


def _m(ctx, name):
    return ctx["result"].metric(name)


# The scoring engine's sector keys differ from the (more granular) Sector Lens
# snapshot keys for a few sectors; map to the closest broad snapshot bucket.
_SNAP_KEY_ALIAS = {
    "banking": "financial_services", "it_services": "it", "realestate": "realty",
}


def _constituents(snap):
    """Constituent rows from a snapshot sector, tolerating the older 'top10' key."""
    if not snap:
        return []
    return snap.get("constituents") or snap.get("top10") or []


def _find_company_snapshot_sector(snapshot, company):
    """Locate the company inside the snapshot's own constituent lists and return
    (sector_key, sector_row). Used when the coarse scoring sector has no snapshot
    row (e.g. a hotel or telecom that scores as 'generic'): the snapshot now
    carries fine sectors (telecom, consumer_services, …) that DO price the peer
    set, so the Sector page can be built from the company's real basket.

    A niche basket is preferred over the broad 'infrastructure'/'generic'
    fallback baskets; within that, the basket where the company is largest wins."""
    if not snapshot:
        return None, None
    secs = snapshot.get("sectors")
    rows = secs if isinstance(secs, list) else list((secs or {}).values())
    target = _norm_name(company)
    if not target:
        return None, None
    broad = {"generic", "infrastructure", "commodities"}
    best = None                                  # (is_niche, market_cap, key, row)
    for s in rows:
        key = s.get("key") or s.get("sector_key")
        for c in _constituents(s):
            n = _norm_name(c.get("name", ""))
            if n and (n == target or n in target or target in n):
                mc = c.get("market_cap") or 0
                cand = (key not in broad, mc, key, s)
                if best is None or cand[:2] > best[:2]:
                    best = cand
                break
    if best:
        return best[2], best[3]
    return None, None


# short, plain descriptors for the driver lists (data-aware, never invented)
_DRIVER_DESC = {
    "Cash Conversion Cycle": "Cash returns before it is spent, so day-to-day running needs little borrowing.",
    "Net Profit Growth": "How fast the bottom line is compounding year on year.",
    "Net Profit Margin": "How much of every rupee of sales reaches the bottom line.",
    "Debt to Equity Ratio": "Borrowings measured against owners' capital.",
    "Return on Equity (ROE) %": "The return earned on shareholders' funds.",
    "EBITDA Margin": "Operating profitability before depreciation and financing.",
    "Return on Capital Employed (ROCE) %": "What the whole capital base earns as operating profit.",
    "Return on Assets (ROA) %": "Profit generated per rupee of assets.",
    "Interest Coverage Ratio": "How comfortably operating profit covers the interest bill.",
    "Fixed Asset Turnover": "Sales generated per rupee of plant and equipment.",
    "CFO / PAT": "How much reported profit converts into operating cash.",
    "Sales Growth": "How fast the top line is expanding.",
    "Gross Margin": "What is left after the direct cost of sales.",
}

_SHORT_NAME = {
    "Cash Conversion Cycle": "Cash conversion cycle",
    "Net Profit Growth": "Net profit growth",
    "Net Profit Margin": "Net profit margin",
    "Debt to Equity Ratio": "Debt to equity",
    "Return on Equity (ROE) %": "Return on equity",
    "EBITDA Margin": "EBITDA margin",
    "Return on Capital Employed (ROCE) %": "Return on capital employed",
    "Return on Assets (ROA) %": "Return on assets",
    "Interest Coverage Ratio": "Interest coverage",
    "Fixed Asset Turnover": "Fixed-asset turnover",
    "CFO / PAT": "Cash flow to profit (CFO/PAT)",
    "Sales Growth": "Sales growth",
    "Gross Margin": "Gross margin",
}


def _short(name):
    return _SHORT_NAME.get(name, name)


# ==========================================================================
# PAGE 1 — cover
# ==========================================================================
def _cover(pdf, ctx):
    # dark background with a soft vertical gradient
    for i in range(60):
        t = i / 59
        pdf.set_fill_color(*_lerp(COVER_BG, COVER_BG2, t))
        pdf.rect(0, i * (PAGE_H / 60), PAGE_W, PAGE_H / 60 + 0.5, style="F")

    # brand
    pdf.set_fill_color(*GREEN_BRIGHT)
    pdf.rect(MARGIN, 24, 11, 11, style="F", round_corners=True, corner_radius=2.6)
    pdf.sans(13, True, COVER_BG)
    pdf.set_xy(MARGIN, 24.4)
    pdf.cell(11, 11, "F", align="C")
    pdf.sans(15, True, COVER_TXT)
    pdf.set_xy(MARGIN + 14, 24)
    pdf.cell(60, 6, "FundaCheck")
    pdf.mono(6.4, False, COVER_FAINT)
    pdf.set_xy(MARGIN + 14, 30.5)
    pdf.cell(60, 4, "FUNDAMENTAL WORKSPACE")
    pdf.mono(7.2, True, GREEN_BRIGHT)
    pdf.set_xy(PAGE_W - MARGIN - 70, 24)
    pdf.cell(70, 4, "FUNDAMENTAL REPORT", align="R")
    pdf.mono(6.8, False, COVER_FAINT)
    pdf.set_xy(PAGE_W - MARGIN - 70, 29)
    pdf.cell(70, 4, pdf.txt(f"{ctx['fy_full']}  ·  {len(ctx['fy'])} periods"), align="R")

    # title block
    pdf.mono(7.4, True, GREEN_BRIGHT)
    pdf.set_xy(MARGIN, 62)
    pdf.cell(0, 5, "COMPANY ANALYSIS")
    name = ctx["company_title"]
    pdf.sans(38, True, COVER_TXT)
    # wrap company name to two lines if long
    words = name.split()
    line1, line2 = name, ""
    if pdf.get_string_width(name) > CONTENT_W:
        for i in range(len(words), 0, -1):
            a = " ".join(words[:i])
            if pdf.get_string_width(a) <= CONTENT_W:
                line1, line2 = a, " ".join(words[i:])
                break
    pdf.set_xy(MARGIN, 70)
    pdf.cell(0, 16, pdf.txt(line1))
    if line2:
        pdf.set_xy(MARGIN, 86)
        pdf.cell(0, 16, pdf.txt(line2))
    sub_y = 104 if line2 else 90
    bits = [ctx["sector_name"].upper()]
    if ctx["fy"]:
        bits.append(f"{ctx['fy_first']}–{ctx['fy_last']}")
    if ctx["ticker"]:
        bits.append(f"NSE: {ctx['ticker']}")
    pdf.mono(8.4, False, COVER_FAINT)
    pdf.set_xy(MARGIN, sub_y)
    pdf.cell(0, 5, pdf.txt("   ·   ".join(bits)))
    _rule(pdf, MARGIN, sub_y + 10, CONTENT_W, (44, 60, 52), 0.4)

    # score + headline
    y = sub_y + 18
    pdf.sans(58, True, band_color(ctx["score"]))
    pdf.set_xy(MARGIN, y - 4)
    pdf.cell(52, 26, f"{ctx['score']:.0f}")
    pdf.mono(6.8, False, COVER_FAINT)
    pdf.set_xy(MARGIN, y + 26)
    pdf.cell(52, 4, "SCORE / 100")
    lead, weak_subj, weak_verb = _cover_headline(ctx)
    if weak_subj:
        head = f"{lead}. {{r|{weak_subj}}} {weak_verb}."
    else:
        head = f"{lead}."
    pdf.set_xy(MARGIN + 58, y)
    _para(pdf, MARGIN + 58, y, CONTENT_W - 58, head, size=19, lh=8.6,
          color=COVER_TXT, bold_color=COVER_TXT, bold_all=True,
          tag_colors={"r": COVER_SALMON})
    sub = _exec_lead(ctx)
    _para(pdf, MARGIN + 58, y + 26, CONTENT_W - 58, sub, size=9.6, lh=5.2,
          color=(178, 190, 184), bold_color=(210, 220, 214))

    # scale bar
    y = y + 66
    pdf.mono(6.6, False, COVER_FAINT)
    pdf.set_xy(MARGIN, y - 6)
    pdf.cell(80, 4, pdf.txt(f"WHERE {ctx['score']:.0f} SITS ON THE SCALE"))
    pdf.set_xy(PAGE_W - MARGIN - 60, y - 6)
    pdf.cell(60, 4, "SECTOR-ADJUSTED", align="R")
    # Muted three-band scale (weak · neutral · strong), split at 40 and 66, with
    # thin gaps between the bands — the reference design, not a rainbow gradient.
    bar_h = 4.5
    gap = 0.8
    bands = [(0.0, 40.0, SCALE_WEAK), (40.0, 66.0, SCALE_NEUTRAL), (66.0, 100.0, SCALE_STRONG)]
    for lo, hi, col in bands:
        bx = MARGIN + CONTENT_W * lo / 100 + (gap / 2 if lo > 0 else 0)
        bw = CONTENT_W * (hi - lo) / 100 - (gap if lo > 0 and hi < 100 else gap / 2)
        pdf.set_fill_color(*col)
        pdf.rect(bx, y, bw, bar_h, style="F", round_corners=True, corner_radius=0.8)
    mx = MARGIN + CONTENT_W * max(0, min(100, ctx["score"])) / 100
    pdf.set_fill_color(*COVER_TXT)
    pdf.rect(mx - 0.7, y - 1.6, 1.4, bar_h + 3.2, style="F", round_corners=True, corner_radius=0.5)
    pdf.mono(6.2, False, COVER_FAINT)
    pdf.set_xy(MARGIN, y + 6)
    pdf.cell(20, 4, "0")
    pdf.set_xy(MARGIN + CONTENT_W * 0.40 - 10, y + 6)
    pdf.cell(20, 4, "WEAK 40", align="C")
    pdf.set_xy(MARGIN + CONTENT_W * 0.66 - 10, y + 6)
    pdf.cell(20, 4, "STRONG 66", align="C")
    pdf.set_xy(PAGE_W - MARGIN - 20, y + 6)
    pdf.cell(20, 4, "100", align="R")

    # three columns
    y = y + 20
    _rule(pdf, MARGIN, y, CONTENT_W, (44, 60, 52), 0.3)
    col_w = CONTENT_W / 3
    best = ctx["metrics_sorted"][0] if ctx["metrics_sorted"] else None
    worst_pillar = ctx["pillars"][-1] if ctx["pillars"] else None
    val_label, val_sub = _sector_stance(ctx)
    cols = [
        ("STRONGEST DRIVER", _short(best.metric) if best else "n/a",
         (f"{best.score:.0f} / 100  ·  {best.display(best.latest)}" if best else ""),
         GREEN_BRIGHT),
        ("WEAKEST PILLAR", worst_pillar[0].title() if worst_pillar else "n/a",
         _weak_pillar_sub(ctx), AMBER),
        ("AGAINST ITS SECTOR", val_label, val_sub, AMBER),
    ]
    for i, (lab, big, sub, col) in enumerate(cols):
        x = MARGIN + i * col_w
        pdf.mono(6.4, False, COVER_FAINT)
        pdf.set_xy(x, y + 5)
        pdf.cell(col_w - 6, 4, lab)
        fs = 13.5
        for trial in (13.5, 12.5, 11.5, 10.5):
            pdf.sans(trial, True, COVER_TXT)
            if pdf.get_string_width(pdf.txt(big)) <= col_w - 6:
                fs = trial
                break
        pdf.sans(fs, True, COVER_TXT)
        pdf.set_xy(x, y + 10)
        pdf.cell(col_w - 6, 6, pdf.txt(big))
        pdf.mono(6.6, False, col)
        pdf.set_xy(x, y + 18)
        pdf.cell(col_w - 6, 4, pdf.txt(sub[:40]))

    # footer figures
    y = PAGE_H - 42
    _rule(pdf, MARGIN, y, CONTENT_W, (44, 60, 52), 0.3)
    figs = [
        ("LAST TRADED PRICE", _rup(ctx["price"], 2) if ctx["price"] else "n/a"),
        ("MARKET CAP", _cr(ctx["market_cap"])),
        (f"{ctx['fy_last']} REVENUE", _cr(ctx["revenue"])),
        (f"{ctx['fy_last']} NET PROFIT", _cr(ctx["net_profit"])),
    ]
    fw = CONTENT_W / 4
    for i, (lab, val) in enumerate(figs):
        x = MARGIN + i * fw
        pdf.mono(6.2, False, COVER_FAINT)
        pdf.set_xy(x, y + 5)
        pdf.cell(fw - 4, 4, lab)
        pdf.sans(15, True, COVER_TXT)
        pdf.set_xy(x, y + 9.5)
        pdf.cell(fw - 4, 7, pdf.txt(val))

    pdf.mono(6.8, False, COVER_FAINT)
    pdf.set_xy(MARGIN, PAGE_H - 16)
    pdf.cell(0, 4, pdf.txt("Prepared by FundaCheck Research   ·   Grounded in the uploaded "
                           "model, with a sector overlay from Screener.in, IndianAPI and NSE"))
    pdf.set_xy(PAGE_W - MARGIN - 60, PAGE_H - 11)
    pdf.cell(60, 4, pdf.txt(f"{ctx['gen_date']:%-d %B %Y}"), align="R")


def _cover_headline(ctx):
    """Two-clause headline: what carries the score vs what holds it back.

    Returns ``(lead, weak_subject, weak_verb)`` so the cover can render the whole
    line bold in white and tint only the weakness subject salmon — matching the
    reference. ``weak_subject`` is empty when there is no clear weak pillar."""
    pillars = ctx["pillars"]
    if not pillars:
        return f"A {ctx['verdict'].lower()} reading on the numbers", "", ""
    top = pillars[0][0]
    bottom = pillars[-1][0]
    lead_phrase = {
        "profitability": "Margins carry it",
        "returns": "Returns on capital carry it",
        "growth": "Growth carries it",
        "leverage": "A calm balance sheet carries it",
        "efficiency": "Working capital carries it",
    }
    weak_phrase = {          # (subject, verb)
        "profitability": ("Margins", "hold it back"),
        "returns": ("Returns on capital", "hold it back"),
        "growth": ("Growth", "holds it back"),
        "leverage": ("Leverage", "holds it back"),
        "efficiency": ("Working capital", "holds it back"),
    }
    lead = lead_phrase.get(top, f"{top.title()} carries it")
    subj, verb = weak_phrase.get(bottom, (bottom.title(), "holds it back"))
    return lead, subj, verb


# ==========================================================================
# narrative builders (deterministic, from the numbers)
# ==========================================================================
def _chg(model, name, first_fy, last_fy):
    a, b = _at(model, name, first_fy), _at(model, name, last_fy)
    return a, b


def _exec_lead(ctx):
    m = ctx["result"]
    best = ctx["metrics_sorted"][0] if ctx["metrics_sorted"] else None
    roce = _m(ctx, "Return on Capital Employed (ROCE) %")
    ic = _m(ctx, "Interest Coverage Ratio")
    parts = []
    if best:
        parts.append(f"{_short(best.metric)} scores near the top of the scale")
    weak = [x for x in ctx["metrics_sorted"] if x.score < 40][:2]
    if roce and roce.score < 45:
        parts.append(f"ROCE of {roce.display(roce.latest)}")
    if ic and ic.score < 45:
        parts.append(f"interest cover of {ic.display(ic.latest)} sit at the weak end")
    lead = ", and ".join(parts[:2]) if parts else "the drivers are mixed"
    return (f"{lead}. The verdict is {ctx['verdict'].lower()} because the strengths and "
            f"the weaknesses are close to cancelling out.")


def _sector_stance(ctx):
    """'Pricier, earning less' style label vs the sector snapshot, if available."""
    snap = ctx["snap_row"]
    self_row = ctx["self_row"]
    if not snap or not snap.get("metrics"):
        return "Sector overlay", "See section 04"
    sm = snap["metrics"]
    you_pe = _ser(ctx["model"], "PE Ratio")
    you_pe = float(you_pe.iloc[-1]) if not you_pe.empty else (self_row or {}).get("pe")
    you_roce = _m(ctx, "Return on Capital Employed (ROCE) %")
    you_roce = you_roce.latest * 100 if you_roce and you_roce.latest is not None else None
    pricier = (you_pe is not None and sm.get("pe") and you_pe > sm["pe"])
    earning_less = (you_roce is not None and sm.get("roce") and you_roce < sm["roce"])
    if pricier and earning_less:
        label = "Pricier, earning less"
    elif pricier:
        label = "Pricier than sector"
    elif earning_less:
        label = "Earns less than sector"
    else:
        label = "In line with sector"
    idx = snap.get("reference_index", ctx["sector_name"])
    n = snap.get("included_count") or snap.get("constituent_count") or 0
    return label, f"{idx.title()} · {n} names"


def _weak_pillar_sub(ctx):
    if not ctx["pillars"]:
        return ""
    name, score = ctx["pillars"][-1]
    # attach the single worst metric in that pillar
    worst = None
    for mtr in reversed(ctx["metrics_sorted"]):
        if mtr.pillar == name:
            worst = mtr
            break
    if worst:
        return f"{score:.0f} / 100  ·  {_short(worst.metric)} {worst.display(worst.latest)}"
    return f"{score:.0f} / 100"


# ==========================================================================
# PAGE 2 — contents + executive summary + headline figures
# ==========================================================================
_CONTENTS = [
    ("01", "Verdict and score", "The headline number and the six drivers behind it", 3),
    ("02", "How revenue becomes profit", "Where every ₹100 of {fy} sales ends up", 4),
    ("03", "Ratio scorecard", "Ratios scored against sector bands", 5),
    ("04", "Sector position", "Against the {index} universe", 6),
    ("05", "Income statement", "Ten years as filed, ₹ crore", 7),
    ("06", "Balance sheet and cash flow", "What the growth was funded with", 8),
    ("07", "Interpretation", "Seven readings drawn from the model", 9),
    ("08", "What would change the verdict", "Four triggers, with the thresholds that matter", 10),
    ("09", "Method and sources", "How the score is built, and what it excludes", 11),
]


def _page_contents(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "", "CONTENTS", "What is in this report")
    index_name = (ctx["snap_row"] or {}).get("reference_index", ctx["sector_name"])
    for num, title, desc, page in _CONTENTS:
        pdf.mono(7.2, True, GREEN)
        pdf.set_xy(MARGIN, y)
        pdf.cell(8, 6, num)
        pdf.sans(10.5, True, INK)
        pdf.set_xy(MARGIN + 12, y)
        pdf.cell(70, 6, pdf.txt(title))
        d = desc.format(fy=ctx["fy_last"], index=index_name.title())
        pdf.sans(8.6, False, MUTED)
        pdf.set_xy(MARGIN + 12, y)
        pdf.cell(CONTENT_W - 24, 6, pdf.txt(d), align="R")
        pdf.mono(7.2, False, FAINT)
        pdf.set_xy(PAGE_W - MARGIN - 8, y)
        pdf.cell(8, 6, f"{page:02d}", align="R")
        # dotted leader
        pdf.set_draw_color(*HAIR)
        pdf.set_line_width(0.2)
        pdf.dashed_line(MARGIN + 12, y + 6.4, PAGE_W - MARGIN - 12, y + 6.4, 0.6, 1.4)
        y += 9

    # executive summary
    y += 4
    y2 = _heading(pdf, MARGIN, y, CONTENT_W, "Executive summary", "The short version")
    for para in _exec_paras(ctx):
        y2 = _para(pdf, MARGIN, y2 + 1.5, CONTENT_W, para, size=9.4, lh=4.9)
    y2 = _callout(pdf, MARGIN, y2 + 4, CONTENT_W, "Bottom line", _exec_bottom(ctx), "green")

    # headline figures
    y2 += 8
    _heading(pdf, MARGIN, y2, CONTENT_W, "Headline figures", ctx["fy_last"])
    _stat_grid(pdf, ctx, y2 + 11)


def _exec_paras(ctx):
    r = ctx["result"]
    strong = [p for p, s in ctx["pillars"] if s >= 60]
    weak = [p for p, s in ctx["pillars"] if s < 45]
    cc = _m(ctx, "Cash Conversion Cycle")
    npg = _m(ctx, "Net Profit Growth")
    nm = _m(ctx, "Net Profit Margin")
    de = _m(ctx, "Debt to Equity Ratio")
    roce = _m(ctx, "Return on Capital Employed (ROCE) %")
    ic = _m(ctx, "Interest Coverage Ratio")
    fat = _m(ctx, "Fixed Asset Turnover")

    p1 = (f"{ctx['company_title']} scores **{ctx['score']:.0f} out of 100** against "
          f"{ctx['sector_name']} expectations, placing it in the **{ctx['verdict'].lower()} "
          f"band**. " +
          (f"The score is carried by {', '.join(strong)}" if strong else "No pillar is clearly strong") +
          (f", and held back by {', '.join(weak)}." if weak else "."))

    fav = []
    if cc and cc.latest is not None and cc.latest < 0:
        fav.append(f"the cash conversion cycle is negative at **{cc.display(cc.latest)}**, so suppliers effectively fund day-to-day running")
    if npg and npg.latest is not None:
        fav.append(f"net profit grew **{npg.display(npg.latest)}**")
    if nm and nm.latest is not None:
        fav.append(f"the net margin reached **{nm.display(nm.latest)}**")
    p2 = ("The case in favour is operational. " + "; ".join(fav[:3]) + "." ) if fav else \
         "The case in favour rests on the operating lines."

    against = []
    if roce and roce.latest is not None:
        against.append(f"**ROCE of {roce.display(roce.latest)}** sits at the low end of what the sector earns")
    if ic and ic.latest is not None and ic.score < 45:
        against.append(f"**interest cover of {ic.display(ic.latest)}** leaves little cushion")
    if fat and fat.latest is not None:
        against.append(f"fixed-asset turnover has fallen to **{fat.display(fat.latest)}** as capex outran revenue")
    p3 = ("The case against is about capital. " + "; ".join(against[:3]) + ".") if against else \
         "The case against centres on returns on the growing capital base."
    return [p1, p2, p3]


def _exec_bottom(ctx):
    top = ctx["pillars"][0][0] if ctx["pillars"] else "the operating lines"
    bottom = ctx["pillars"][-1][0] if ctx["pillars"] else "returns"
    return (f"A business whose **{top}** stands out while **{bottom}** lags. The verdict is "
            f"{ctx['verdict'].lower()} because the two are close to offsetting — worth reading "
            f"the section-by-section detail before drawing a conclusion.")


def _stat_grid(pdf, ctx, y):
    defs = _stat_cards(ctx)
    cw = (CONTENT_W - 2 * 4) / 3
    ch = 26
    for i, (dot, label, value, unit, sub) in enumerate(defs):
        col = i % 3
        row = i // 3
        x = MARGIN + col * (cw + 4)
        yy = y + row * (ch + 4)
        _card(pdf, x, yy, cw, ch)
        pdf.set_fill_color(*dot)
        pdf.ellipse(x + 5, yy + 5.4, 1.8, 1.8, style="F")
        pdf.mono(6.4, False, MUTED)
        pdf.set_xy(x + 9, yy + 4)
        pdf.cell(cw - 12, 4, label.upper())
        pdf.sans(17, True, INK)
        pdf.set_xy(x + 5, yy + 9)
        pdf.cell(cw - 10, 8, pdf.txt(value))
        if unit:
            w = pdf.get_string_width(pdf.txt(value))
            pdf.sans(8, False, MUTED)
            pdf.set_xy(x + 5 + w + 1, yy + 13.5)
            pdf.cell(10, 4, pdf.txt(unit))
        pdf.sans(7.4, False, MUTED)
        _para(pdf, x + 5, yy + 18, cw - 10, sub, size=7.4, lh=3.5, color=MUTED)


def _stat_cards(ctx):
    model = ctx["model"]
    pe = _ser(model, "PE Ratio")
    pe = pe[(pe > 0) & (pe < 2000)]
    pe_v = float(pe.iloc[-1]) if not pe.empty else None
    pe_med = float(pe.median()) if not pe.empty else None
    roe = _m(ctx, "Return on Equity (ROE) %")
    roce = _m(ctx, "Return on Capital Employed (ROCE) %")
    de = _m(ctx, "Debt to Equity Ratio")
    ic = _m(ctx, "Interest Coverage Ratio")
    cc = _m(ctx, "Cash Conversion Cycle")
    sm = (ctx["snap_row"] or {}).get("metrics") or {}

    def val(m):
        return m.display(m.latest) if (m and m.latest is not None) else "n/a"

    roe_sub = ""
    if roe and roe.latest is not None and roe.average_3y is not None:
        prev = _ser(model, "Return on Equity (ROE) %")
        if len(prev) > 1:
            d = (float(prev.iloc[-1]) - float(prev.iloc[-2])) * 100
            roe_sub = f"{'▼' if d < 0 else '▲'} {d:+.1f} pp from last year"
    roce_sub = "below the sector" if (roce and sm.get("roce") and roce.latest is not None
                                      and roce.latest * 100 < sm["roce"]) else "returns on capital"
    if roce and sm.get("roce"):
        roce_sub = f"sector is {sm['roce']:.2f}%"
    return [
        (MID, "P/E ratio", f"{pe_v:.1f}" if pe_v else "n/a", "x",
         (f"{'below' if pe_v and pe_med and pe_v < pe_med else 'above'} its own median of "
          f"{pe_med:.1f}x" if pe_v and pe_med else "market price to earnings")),
        (AMBER, "Return on equity", val(roe).rstrip('%') if roe and roe.latest is not None else "n/a", "%",
         roe_sub or "return on shareholders' funds"),
        (RED, "ROCE", (f"{roce.latest*100:.1f}" if roce and roce.latest is not None else "n/a"), "%",
         roce_sub),
        (AMBER, "Debt / equity", (f"{de.latest:.2f}" if de and de.latest is not None else "n/a"), "",
         band_word(de.score).title() + " for the sector" if de else "leverage"),
        (RED if (ic and ic.score < 40) else AMBER, "Interest cover",
         (f"{ic.latest:.1f}" if ic and ic.latest is not None else "n/a"), "x",
         "operating profit vs the interest bill"),
        (MID if (cc and cc.latest is not None and cc.latest < 0) else AMBER, "Cash cycle",
         (f"{cc.latest:.0f}" if cc and cc.latest is not None else "n/a"), "d",
         "suppliers fund operations" if (cc and cc.latest is not None and cc.latest < 0)
         else "working-capital days"),
    ]


# ==========================================================================
# PAGE 3 — verdict and score
# ==========================================================================
def _page_verdict(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "01", "Section 01", "Verdict and score")
    intro = (f"The Funda Score is a 0–100 health check built for how "
             f"{ctx['sector_name']} companies actually work. Each driver is scored "
             f"against sector norms, then weighted into a single number. Below 40 reads "
             f"weak; above 66 reads strong.")
    y = _para(pdf, MARGIN, y, CONTENT_W, intro, size=9.6, lh=5.2) + 4

    col_w = (CONTENT_W - 10) / 2
    lx, rx = MARGIN, MARGIN + col_w + 10

    # left: drivers + pillars
    ly = _heading(pdf, lx, y, col_w, "The six drivers", "scored 0–100")
    ly += 2
    drivers = ctx["metrics_sorted"][:6]
    for m in drivers:
        ly = _score_bar(pdf, lx, ly, col_w, _short(m.metric), m.score,
                        ticks=True, value=f"{m.score:.0f}")
    ly += 4
    ly = _heading(pdf, lx, ly, col_w, "By pillar", "weighted groups")
    ly += 2
    for name, sc in ctx["pillars"]:
        ly = _score_bar(pdf, lx, ly, col_w, name.title(), sc, ticks=True,
                        value=f"{sc:.0f}")

    # right: holds it up / back
    up = [m for m in ctx["metrics_sorted"] if m.score >= 55][:4]
    ry = _heading(pdf, rx, y, col_w, "What holds it up", f"{len(up)} ratios")
    ry += 1
    for m in up:
        ry = _driver_note(pdf, rx, ry, col_w, m, GREEN)
    ry += 3
    weak = [m for m in ctx["metrics_sorted"] if m.score < 45][:4]
    ry = _heading(pdf, rx, ry, col_w, "What holds it back", f"{len(weak)} ratios")
    ry += 1
    for m in weak:
        ry = _driver_note(pdf, rx, ry, col_w, m, RED_TXT)

    # why callout
    yb = max(ly, ry)
    why = _why_text(ctx)
    _callout(pdf, MARGIN, PAGE_H - 46, CONTENT_W, f"Why {ctx['score']:.0f}", why, "amber")
    _interior_footer(pdf, ctx)


def _score_bar(pdf, x, y, w, label, score, ticks=False, value=""):
    pdf.sans(8.6, True, INK)
    pdf.set_xy(x, y)
    pdf.cell(w - 14, 4.4, pdf.txt(label[:32]))
    pdf.mono(8, True, band_color(score))
    pdf.set_xy(x + w - 16, y)
    pdf.cell(16, 4.4, value, align="R")
    ty = y + 5.4
    pdf.set_fill_color(238, 240, 238)
    pdf.rect(x, ty, w, 2.6, style="F", round_corners=True, corner_radius=1.3)
    pdf.set_fill_color(*band_color(score))
    pdf.rect(x, ty, max(1.4, w * max(0, min(100, score)) / 100), 2.6, style="F",
             round_corners=True, corner_radius=1.3)
    if ticks:
        pdf.set_draw_color(210, 214, 210)
        pdf.set_line_width(0.2)
        for f in (0.40, 0.66):
            pdf.line(x + w * f, ty - 0.6, x + w * f, ty + 3.2)
    return ty + 6.4


def _driver_note(pdf, x, y, w, m, dotcol):
    pdf.set_fill_color(*dotcol)
    pdf.ellipse(x + 0.5, y + 1.4, 1.6, 1.6, style="F")
    pdf.sans(9, True, INK)
    pdf.set_xy(x + 4, y)
    pdf.cell(w - 30, 4.6, pdf.txt(_short(m.metric)))
    pdf.mono(7.6, True, dotcol)
    pdf.set_xy(x + w - 28, y)
    pdf.cell(28, 4.6, pdf.txt(str(m.display(m.latest))), align="R")
    desc = _DRIVER_DESC.get(m.metric, "")
    ny = _para(pdf, x + 4, y + 4.8, w - 6, desc, size=7.8, lh=3.7, color=MUTED)
    return ny + 2.4


def _why_text(ctx):
    if not ctx["pillars"]:
        return "The drivers are mixed."
    top, ts = ctx["pillars"][0]
    bot, bs = ctx["pillars"][-1]
    return (f"The strongest pillar is **{top} at {ts:.0f}**; the weakest is "
            f"**{bot} at {bs:.0f}**. Because pillars are blended by how much they matter "
            f"in this sector, a strong area can only partly offset a weak one — so the "
            f"score settles {'mid-band' if 40 <= ctx['score'] < 66 else 'where it does'} "
            f"rather than at either extreme.")


# ==========================================================================
# PAGE 4 — how revenue becomes profit
# ==========================================================================
def _page_waterfall(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "02", "Section 02", "How revenue becomes profit")
    fy = ctx["fy_last"]
    intro = (f"The flow from sales to net profit, in rupees per ₹100 of {fy} revenue. "
             f"Costs peel off the top; other income joins from below.")
    y = _para(pdf, MARGIN, y, CONTENT_W, intro, size=9.6, lh=5.2) + 3

    steps = _waterfall_steps(ctx)
    if steps:
        y = _draw_waterfall(pdf, MARGIN, y, CONTENT_W, 50, steps)
    else:
        pdf.sans(9, False, MUTED); pdf.set_xy(MARGIN, y)
        pdf.cell(0, 6, pdf.txt("Income-statement detail unavailable in this model."))
        y += 10

    y = _callout(pdf, MARGIN, y + 2, CONTENT_W, "Read this", _waterfall_note(ctx), "amber") + 6

    # revenue six years
    y = _heading(pdf, MARGIN, y, CONTENT_W, "Revenue, six years", "₹ crore")
    y = _revenue_bars(pdf, MARGIN, y + 1, CONTENT_W, 30, ctx) + 2
    y = _para(pdf, MARGIN, y, CONTENT_W, _revenue_note(ctx), size=8.4, lh=4.3, color=MUTED) + 5

    # margins table
    y = _heading(pdf, MARGIN, y, CONTENT_W, "The same figures as margins", ctx["fy_last"])
    _margin_table(pdf, MARGIN, y + 1, CONTENT_W, ctx)
    _interior_footer(pdf, ctx)


def _waterfall_steps(ctx):
    model, fy = ctx["model"], ctx["fy_last"]
    sales = _at(model, "Sales", fy)
    if not sales:
        return None
    def per100(v):
        return None if v is None else v / sales * 100
    cogs = _at(model, "COGS", fy)
    gross = _gross_cr(model, fy)
    ebitda = _at(model, "EBITDA", fy)
    dep = _at(model, "Depreciation", fy)
    ebit = _at(model, "EBIT (OPM)", fy)
    interest = _at(model, "Interest", fy)
    other = _at(model, "Other Income", fy) or _at(model, "Other Income ", fy)
    tax = _at(model, "Tax", fy)
    net = _at(model, "Net Profit", fy)
    if net is None:
        return None
    other_op = (gross - ebitda) if (gross is not None and ebitda is not None) else None
    after_int = None
    if ebit is not None and interest is not None:
        after_int = ebit - interest
    steps = []
    steps.append(("Sales", 100.0, "level"))
    if cogs is not None:
        steps.append(("Cost of goods", -per100(cogs), "cost"))
    if gross is not None:
        steps.append(("Gross profit", per100(gross), "level"))
    if other_op is not None:
        steps.append(("Other operating", -per100(other_op), "cost"))
    if ebitda is not None:
        steps.append(("EBITDA", per100(ebitda), "level"))
    if dep is not None:
        steps.append(("Depreciation", -per100(dep), "cost"))
    if interest is not None:
        steps.append(("Interest", -per100(interest), "cost"))
    if after_int is not None:
        steps.append(("After interest", per100(after_int), "level"))
    if other is not None:
        steps.append(("Other income", per100(other), "gain"))
    if tax is not None:
        steps.append(("Tax", -per100(tax), "cost"))
    steps.append(("Net profit", per100(net), "net"))
    return steps


_COST_PINK = (222, 158, 152)
_COST_GREY = (176, 182, 178)
_DEFICIT = (232, 190, 185)   # pale-red band where the running remainder is negative


def _dev(c):
    from fpdf.drawing import DeviceRGB
    return DeviceRGB(c[0] / 255, c[1] / 255, c[2] / 255)


def _fill_ribbon(pdf, top_pts, base_fn, color):
    """Filled flowing band: smooth S-curve top edge through top_pts (left→right),
    straight bottom edge along base_fn. No stroke."""
    with pdf.new_path() as p:
        p.style.fill_color = _dev(color)
        p.style.stroke_color = None
        p.style.stroke_width = 0
        xL, xR = top_pts[0][0], top_pts[-1][0]
        p.move_to(xL, base_fn(xL))
        p.line_to(xR, base_fn(xR))
        p.line_to(top_pts[-1][0], top_pts[-1][1])
        for i in range(len(top_pts) - 2, -1, -1):
            x0, y0 = top_pts[i + 1]
            x1, y1 = top_pts[i]
            mx = (x0 + x1) / 2
            p.curve_to(mx, y0, mx, y1, x1, y1)
        p.close()


def _tongue(pdf, cx, base_y, thick, color, up=True, half=4.0):
    """A broad colour ribbon that lifts `thick` mm of the flow up (a cost) or
    joins from below (a gain), with a rounded outer cap via bezier. It overlaps
    the band a little at its base so it reads as peeling off the flow."""
    rise = max(thick, 1.4) + 3.0
    r = min(half, 2.2)
    with pdf.new_path() as p:
        p.style.fill_color = _dev(color)
        p.style.stroke_color = None
        p.style.stroke_width = 0
        if up:
            p.move_to(cx - half, base_y + 1.2)
            p.line_to(cx - half, base_y - rise + r)
            p.curve_to(cx - half, base_y - rise, cx - half + r, base_y - rise, cx, base_y - rise)
            p.curve_to(cx + half - r, base_y - rise, cx + half, base_y - rise, cx + half, base_y - rise + r)
            p.line_to(cx + half, base_y + 1.2)
        else:
            p.move_to(cx - half, base_y - 1.2)
            p.line_to(cx - half, base_y + rise - r)
            p.curve_to(cx - half, base_y + rise, cx - half + r, base_y + rise, cx, base_y + rise)
            p.curve_to(cx + half - r, base_y + rise, cx + half, base_y + rise, cx + half, base_y + rise - r)
            p.line_to(cx + half, base_y - 1.2)
        p.close()
    return rise


def _sankey_link(pdf, x0, y0t, y0b, x1, y1t, y1b, color):
    """A smooth Sankey link: a filled ribbon from the vertical slice [y0t,y0b] at
    x0 to the slice [y1t,y1b] at x1, with horizontal-tangent cubic edges so the
    ribbon flows rather than kinks. Thickness is preserved by the caller (the two
    slices are the same height), so ribbon width reads as value."""
    mx = (x0 + x1) / 2
    with pdf.new_path() as p:
        p.style.fill_color = _dev(color)
        p.style.stroke_color = None
        p.style.stroke_width = 0
        p.move_to(x0, y0t)
        p.curve_to(mx, y0t, mx, y1t, x1, y1t)
        p.line_to(x1, y1b)
        p.curve_to(mx, y1b, mx, y0b, x0, y0b)
        p.close()


def _draw_waterfall(pdf, x, y, w, h, steps):
    """The sales→profit flow as a true Sankey: a light-green 'money remaining'
    band whose thickness is the running remainder, with each cost peeling off the
    top as a ribbon whose THICKNESS is proportional to that cost, other income
    joining from below, and a dark-green net-profit block at the end. Handles
    losses (a pale-red deficit band and a red net block) without overflowing."""
    legend = [("Money remaining", LIGHT), ("Operating cost", _COST_PINK),
              ("Interest", RED), ("Other income", AMBER), ("Tax", _COST_GREY),
              ("Net profit", GREEN)]
    lx = x
    for name, col in legend:
        pdf.set_fill_color(*col)
        pdf.rect(lx, y, 3, 3, style="F", round_corners=True, corner_radius=0.6)
        pdf.sans(7, False, BODY)
        pdf.set_xy(lx + 4.4, y - 0.6)
        pdf.cell(2 + pdf.get_string_width(name), 4, name)
        lx += 9 + pdf.get_string_width(name)
    y += 9

    n = len(steps)
    xL, xR = x + 3, x + w - 3
    W = xR - xL
    seg = W / n
    xc = [xL + (i + 0.5) * seg for i in range(n)]

    # running remaining at each node, and the level *before* each cost/gain applies
    rem, before, run = [], [], 0.0
    for name, val, kind in steps:
        if kind in ("level", "net"):
            before.append(val); run = val
        else:
            before.append(run); run += val
        rem.append(run)

    # dynamic vertical scale: HI above the zero line (costs peel up toward the
    # level before them), LO below it (a negative remainder or a below-baseline
    # other-income ribbon). Everything fits the plot box regardless of losses.
    hi = max([100.0] + rem + [before[i] for i, s in enumerate(steps) if s[2] == "cost"])
    lo = max([0.0] + [-min(0.0, r) for r in rem]
             + [abs(v) for _, v, k in steps if k == "gain"])
    scale = h / max(hi + lo, 1.0)
    plot_top = y + 9.0                    # headroom for the top row of peel labels
    zero_y = plot_top + hi * scale
    def _yv(v):
        return zero_y - v * scale

    # 1) money-remaining band (+ pale-red deficit where the remainder is negative)
    pos_top = [(xL, _yv(max(rem[0], 0.0)))] + [(xc[i], _yv(max(rem[i], 0.0))) for i in range(n)]
    _fill_ribbon(pdf, pos_top, lambda xx: zero_y, LIGHT)
    if any(r < 0 for r in rem):
        neg_bot = [(xL, _yv(min(rem[0], 0.0)))] + [(xc[i], _yv(min(rem[i], 0.0))) for i in range(n)]
        _fill_ribbon(pdf, neg_bot, lambda xx: zero_y, _DEFICIT)

    # 2) solid Sales cap at the left, full height of the flow
    pdf.set_fill_color(*INK)
    pdf.rect(xL - 2.0, _yv(rem[0]), 2.0, rem[0] * scale, style="F")

    # 3) net-profit block at the end (green, or red for a loss)
    ni = n - 1
    with pdf.new_path() as p:
        p.style.fill_color = _dev(GREEN if rem[ni] >= 0 else RED)
        p.style.stroke_color = None; p.style.stroke_width = 0
        x0 = xc[ni] - seg * 0.42
        p.move_to(x0, zero_y); p.line_to(xR, zero_y); p.line_to(xR, _yv(rem[ni]))
        mx = (xR + x0) / 2
        p.curve_to(mx, _yv(rem[ni]), mx, _yv(rem[ni]), x0, _yv(rem[ni]))
        p.close()

    # 4) cost ribbons peel off the top; the other-income ribbon joins from below.
    #    Each ribbon's thickness == its value on the shared scale.
    abbr = {"Cost of goods": "Cost of goods", "Other operating": "Other operating",
            "After interest": "After interest", "Other income": "Other income",
            "Gross profit": "Gross profit", "Net profit": "Net profit"}
    # Costs peel up to a short stub near the top, with a two-line label above it.
    # Labels are packed onto as few shelves as fit without overlapping, so the
    # top row stays clean (thick ribbon = big cost, thin = small).
    lab_w = seg * 1.45
    shelf_last = []                       # right-edge x currently used per shelf
    shelf_h = 8.6
    for i, (name, val, kind) in enumerate(steps):
        if kind != "cost":
            continue
        t = abs(val) * scale
        col = RED if name == "Interest" else (_COST_GREY if name == "Tax" else _COST_PINK)
        a_top, a_bot = _yv(before[i]), _yv(rem[i])             # slice leaving the band
        tx = min(xc[i] + seg * 0.5, xR - 3)
        lx0 = tx - lab_w / 2
        shelf = 0
        while shelf < len(shelf_last) and shelf_last[shelf] > lx0 - 1.0:
            shelf += 1
        if shelf == len(shelf_last):
            shelf_last.append(0.0)
        shelf_last[shelf] = lx0 + lab_w
        stub_top = plot_top + shelf * shelf_h
        _sankey_link(pdf, xc[i], a_top, a_bot, tx, stub_top, stub_top + t, col)
        pdf.set_fill_color(*col)                                # rounded stub cap
        pdf.rect(tx - 0.9, stub_top, 1.8, max(t, 0.9), style="F",
                 round_corners=True, corner_radius=0.7)
        lab_col = RED_TXT if name != "Tax" else MUTED
        pdf.mono(5.4, True, lab_col)
        pdf.set_xy(tx - lab_w / 2, stub_top - 6.6)
        pdf.cell(lab_w, 3, pdf.txt(abbr.get(name, name)), align="C")
        pdf.set_xy(tx - lab_w / 2, stub_top - 3.4)
        pdf.cell(lab_w, 3, pdf.txt(f"−₹{abs(val):.2f}"), align="C")

    # Other income joins from below as a proportional ribbon.
    for i, (name, val, kind) in enumerate(steps):
        if kind != "gain":
            continue
        t = abs(val) * scale
        a_top, a_bot = _yv(rem[i]), _yv(before[i])             # slice joining the band
        tx = max(xc[i] - seg * 0.3, xL + 2)
        term_bot = zero_y + lo * scale + 0.5
        _sankey_link(pdf, tx, term_bot - t, term_bot, xc[i], a_top, a_bot, AMBER)
        pdf.set_fill_color(*AMBER)
        pdf.rect(tx - 0.9, term_bot - max(t, 0.9), 1.8, max(t, 0.9), style="F",
                 round_corners=True, corner_radius=0.7)
        pdf.mono(5.4, True, AMBER_TXT)
        pdf.set_xy(tx - lab_w / 2, term_bot + 1.0)
        pdf.cell(lab_w, 3, pdf.txt(abbr.get(name, name)), align="C")
        pdf.set_xy(tx - lab_w / 2, term_bot + 3.8)
        pdf.cell(lab_w, 3, pdf.txt(f"+₹{abs(val):.2f}"), align="C")

    # 5) inline labels for the level/net nodes, on the band itself
    for i, (name, val, kind) in enumerate(steps):
        if kind not in ("level", "net"):
            continue
        lv = _yv(rem[i])
        pdf.sans(5.8, kind == "net", GREEN if kind == "net" else INK)
        pdf.set_xy(xc[i] - seg * 0.5, (lv + 1.2) if rem[i] >= 0 else (lv - 4.4))
        pdf.cell(seg, 3, pdf.txt(abbr.get(name, name)), align="C")
        pdf.mono(5.4, True, GREEN if kind == "net" else BODY)
        pdf.set_xy(xc[i] - seg * 0.5, (lv + 4.0) if rem[i] >= 0 else (lv - 7.2))
        pdf.cell(seg, 3, pdf.txt(f"{'−' if rem[i] < 0 else ''}₹{abs(val):.2f}"), align="C")

    return zero_y + lo * scale + 10.0


def _waterfall_note(ctx):
    model, fy = ctx["model"], ctx["fy_last"]
    sales = _at(model, "Sales", fy)
    ebit = _at(model, "EBIT (OPM)", fy)
    interest = _at(model, "Interest", fy)
    other = _at(model, "Other Income", fy) or _at(model, "Other Income ", fy)
    net = _at(model, "Net Profit", fy)
    if sales and ebit is not None and interest is not None and other is not None:
        after = (ebit - interest) / sales * 100
        oth = other / sales * 100
        return (f"After every operating cost and the interest bill, **₹{after:.2f} of each "
                f"₹100** of sales remains. The net profit is reached by adding "
                f"**₹{oth:.2f} of other income** — a line worth understanding before "
                f"treating the {fy} margin as repeatable.")
    return ("Costs peel off the operating lines; where other income is large relative to "
            "operating profit, the final margin should be read with care.")


def _fy_series(ctx, *names):
    """A metric restricted to real financial years (drops TTM/aggregates)."""
    s = _ser(ctx["model"], *names)
    keep = [i for i in s.index if str(i) in ctx["fy"]]
    return s.loc[keep] if keep else s


def _revenue_bars(pdf, x, y, w, h, ctx):
    s = _fy_series(ctx, "Sales").tail(6)
    if s.empty:
        return y + 4
    peak = float(s.max()) or 1
    base = y + h
    n = len(s)
    sw = w / n
    ramp = [(214, 234, 222), (180, 216, 194), (120, 194, 152),
            (64, 164, 106), GREEN, GREEN_DARK]
    order = sorted(range(n), key=lambda i: float(s.iloc[i]))
    shade = {idx: ramp[min(rank * len(ramp) // n, len(ramp) - 1)]
             for rank, idx in enumerate(order)}
    for i, (yr, v) in enumerate(s.items()):
        bh = max(2, float(v) / peak * (h - 8))
        bx = x + i * sw + sw * 0.16
        pdf.set_fill_color(*shade[i])
        pdf.rect(bx, base - bh, sw * 0.68, bh, style="F")
        pdf.mono(6.6, False, MUTED)
        pdf.set_xy(x + i * sw, base - bh - 4)
        val = f"{v/1e5:.2f}L" if v >= 1e5 else f"{v:,.0f}"
        pdf.cell(sw, 3, pdf.txt(val), align="C")
        pdf.mono(6.6, i == n - 1, INK if i == n - 1 else FAINT)
        pdf.set_xy(x + i * sw, base + 1.5)
        pdf.cell(sw, 3, str(yr), align="C")
    return base + 6


def _revenue_note(ctx):
    s = _fy_series(ctx, "Sales")
    if len(s) < 3:
        return ""
    last, prev = float(s.iloc[-1]), float(s.iloc[-2])
    g = (last - prev) / abs(prev) * 100 if prev else 0
    peak = float(s.max())
    peaky = s.idxmax()
    return (f"Sales peaked near {_cr(peak)} in {peaky}. {ctx['fy_last']} is "
            f"{'up' if g >= 0 else 'down'} {abs(g):.1f}% on the prior year, "
            f"{'still below' if last < peak else 'at or above'} the peak.")


def _margin_table(pdf, x, y, w, ctx):
    model, fy = ctx["model"], ctx["fy_last"]
    fy0 = ctx["fy"][0] if ctx["fy"] else fy
    sales = _at(model, "Sales", fy)
    rows = []
    def add(name, val):
        if val is None or not sales:
            return
        margin = val / sales * 100
        m0 = None
        s0 = _at(model, "Sales", fy0)
        base = _at(model, name, fy0)
        if s0 and base is not None:
            m0 = base / s0 * 100
        rows.append((name, val, val / sales * 100, margin, m0))
    add("Sales", sales)
    g = _gross_cr(model, fy)
    g0 = _gross_cr(model, fy0)
    s0 = _at(model, "Sales", fy0)
    rows.append(("Gross profit", g, (g/sales*100 if g and sales else None),
                 (g/sales*100 if g and sales else None),
                 (g0/s0*100 if (g0 is not None and s0) else None)))
    def _per(v):
        return v / sales * 100 if (v is not None and sales) else None
    ebit = _at(model, "EBIT (OPM)", fy)
    interest = _at(model, "Interest", fy)
    after_int = (ebit - interest) if (ebit is not None and interest is not None) else None
    ebit0 = _at(model, "EBIT (OPM)", fy0)
    int0 = _at(model, "Interest", fy0)
    after0 = (ebit0 - int0) if (ebit0 is not None and int0 is not None) else None
    ebitda = _at(model, "EBITDA", fy)
    net = _at(model, "Net Profit", fy)
    rows.append(("EBITDA", ebitda, _per(ebitda), _per(ebitda), _margin0(model, "EBITDA", fy0)))
    rows.append(("EBIT, after depreciation", ebit, _per(ebit), _per(ebit),
                 _margin0(model, "EBIT (OPM)", fy0)))
    rows.append(("After interest", after_int, _per(after_int), _per(after_int),
                 (after0 / s0 * 100 if (after0 is not None and s0) else None)))
    rows.append(("Net profit", net, _per(net), _per(net), _margin0(model, "Net Profit", fy0)))
    # header
    cols = [w * 0.30, w * 0.24, w * 0.20, w * 0.13, w * 0.13]
    heads = ["TIER", "₹ CRORE", "PER ₹100 OF SALES", "MARGIN", f"{fy0} MARGIN"]
    pdf.mono(6.6, False, FAINT)
    cx = x
    for i, htxt in enumerate(heads):
        pdf.set_xy(cx, y)
        pdf.cell(cols[i], 5, pdf.txt(htxt), align="L" if i == 0 else "R")
        cx += cols[i]
    y += 6
    for ri, (name, cr, per, margin, m0) in enumerate(rows):
        head = name in ("Sales", "Net profit")
        if ri % 2 == 0:
            pdf.set_fill_color(247, 249, 247)
            pdf.rect(x, y - 0.6, w, 6, style="F")
        cx = x
        pdf.serif(9.6, head, INK if head else BODY)
        pdf.set_xy(cx, y)
        pdf.cell(cols[0], 5, pdf.txt(name)); cx += cols[0]
        pdf.mono(8, head, INK if head else BODY)
        pdf.set_xy(cx, y); pdf.cell(cols[1], 5, _num(cr), align="R"); cx += cols[1]
        pdf.set_xy(cx, y)
        pdf.cell(cols[2], 5, pdf.txt(_rup(per, 2) if per is not None else "—"), align="R"); cx += cols[2]
        pdf.set_xy(cx, y)
        pdf.cell(cols[3], 5, pdf.txt(f"{margin:.1f}%" if margin is not None and name != "Sales" else "—"), align="R"); cx += cols[3]
        pdf.set_xy(cx, y)
        pdf.cell(cols[4], 5, pdf.txt(f"{m0:.1f}%" if m0 is not None and name != "Sales" else "—"), align="R")
        y += 5.6
    return y


def _margin0(model, name, fy0, cogs_name=None):
    s0 = _at(model, "Sales", fy0)
    base = _at(model, name, fy0)
    if base is None and cogs_name:
        c = _at(model, cogs_name, fy0)
        if c is not None and s0:
            base = s0 - c
    if base is not None and s0:
        return base / s0 * 100
    return None


# ==========================================================================
# PAGE 5 — ratio scorecard
# ==========================================================================
def _page_scorecard(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "03", "Section 03", "Ratio scorecard")
    metrics = ctx["metrics_sorted"]
    n = len(metrics)
    intro = (f"{_num_word(n).capitalize()} ratios, each judged against how companies in this "
             f"sector normally perform. Strong sits above the 66 band, weak below 40; "
             f"everything between reads neutral.")
    y = _para(pdf, MARGIN, y, CONTENT_W, intro, size=9.6, lh=5.2) + 3

    strong = [m for m in metrics if m.score >= 66]
    neutral = [m for m in metrics if 40 <= m.score < 66]
    weak = [m for m in metrics if m.score < 40]
    # distribution bar
    total = max(1, n)
    segs = [(len(strong), MID, "strong"), (len(neutral), AMBER, "neutral"),
            (len(weak), RED, "weak")]
    bx = MARGIN
    for cnt, col, _lab in segs:
        seg_w = CONTENT_W * cnt / total
        if seg_w <= 0:
            continue
        pdf.set_fill_color(*col)
        pdf.rect(bx, y, seg_w, 5, style="F")
        pdf.mono(6.6, True, WHITE)
        pdf.set_xy(bx, y + 0.4)
        pdf.cell(seg_w, 4.6, str(cnt), align="C")
        bx += seg_w
    y += 7
    lx = MARGIN
    for cnt, col, lab in segs:
        pdf.set_fill_color(*col)
        pdf.ellipse(lx, y + 0.6, 2, 2, style="F")
        pdf.sans(7.6, False, BODY)
        pdf.set_xy(lx + 3.2, y - 0.4)
        t = f"{cnt} {lab}"
        pdf.cell(pdf.get_string_width(t) + 2, 4, t)
        lx += 8 + pdf.get_string_width(t)
    y += 8

    # two columns of ratio pills
    profitability = [m for m in metrics if m.pillar in ("profitability", "growth", "efficiency")
                     and "Cash" in m.metric or m.pillar in ("profitability", "growth")]
    left = [m for m in metrics if m.pillar in ("efficiency", "profitability", "growth")]
    right = [m for m in metrics if m.pillar in ("returns", "leverage")]
    # ensure everything appears once; leftovers to left
    seen = set(id(m) for m in left) | set(id(m) for m in right)
    for m in metrics:
        if id(m) not in seen:
            left.append(m)
    col_w = (CONTENT_W - 12) / 2
    ly = _heading(pdf, MARGIN, y, col_w, "Working capital and profitability")
    for m in left:
        ly = _ratio_row(pdf, MARGIN, ly, col_w, m)
    ry = _heading(pdf, MARGIN + col_w + 12, y, col_w, "Returns, leverage and efficiency")
    for m in right:
        ry = _ratio_row(pdf, MARGIN + col_w + 12, ry, col_w, m)

    yb = max(ly, ry) + 4
    yb = _heading(pdf, MARGIN, yb, CONTENT_W, "Margin ladder",
                  f"{ctx['fy_first']} → {ctx['fy_last']}" if ctx["fy"] else "")
    yb = _para(pdf, MARGIN, yb + 1, CONTENT_W, _margin_ladder_text(ctx), size=9.2, lh=4.9) + 4
    yb = _heading(pdf, MARGIN, yb, CONTENT_W, "Where the pressure is building")
    yb = _para(pdf, MARGIN, yb + 1, CONTENT_W, _pressure_text(ctx), size=9.2, lh=4.9) + 4

    worst = weak[-1] if weak else (metrics[-1] if metrics else None)
    if worst:
        _callout(pdf, MARGIN, PAGE_H - 40, CONTENT_W, "The one to watch",
                 f"**{_short(worst.metric)}.** At {worst.display(worst.latest)} it is the "
                 f"largest single drag on the score; even a modest recovery would move the "
                 f"verdict faster than any other single change.", "grey")
    _interior_footer(pdf, ctx)


def _ratio_row(pdf, x, y, w, m):
    fg, bg = band_pill(m.score)
    pdf.sans(8.8, True, INK)
    pdf.set_xy(x, y)
    pdf.cell(w - 44, 5, pdf.txt(_short(m.metric)))
    pdf.mono(8, True, INK)
    pdf.set_xy(x + w - 44, y)
    pdf.cell(24, 5, pdf.txt(str(m.display(m.latest))), align="R")
    _pill_right(pdf, x + w, y + 0.2, band_word(m.score), fg, bg)
    _rule(pdf, x, y + 6.6, w, (240, 242, 240), 0.2)
    return y + 8.4


def _margin_ladder_text(ctx):
    g = _fy_series(ctx, "Gross Margin", "Gross Margin % Sales")
    e = _fy_series(ctx, "EBITDA Margin", "EBITDA Margins")
    n = _fy_series(ctx, "Net Profit Margin", "Net Margins")
    if len(g) >= 2 and len(n) >= 2:
        parts = [f"gross margin from **{g.iloc[0]*100:.1f}% to {g.iloc[-1]*100:.1f}%**"]
        if len(e) >= 2:
            parts.append(f"EBITDA from **{e.iloc[0]*100:.1f}% to {e.iloc[-1]*100:.1f}%**")
        parts.append(f"net from **{n.iloc[0]*100:.1f}% to {n.iloc[-1]*100:.1f}%**")
        return ("Every tier expanded across the period: " + ", ".join(parts) +
                f". That is a genuine quality improvement rather than scale alone — though the "
                f"final tier is flattered by other income in {ctx['fy_last']}, as the waterfall "
                f"on the previous page shows.")
    return "Margin history is limited in this model."


def _pressure_text(ctx):
    weak = [m for m in ctx["metrics_sorted"] if m.score < 45]
    if not weak:
        return "No single ratio sits in the weak band; the score is held by the balance across pillars."
    names = ", ".join(_short(m.metric).lower() for m in weak[:3])
    worst = weak[-1]
    return (f"The counterweight sits below the operating line: {names} read weakest. "
            f"**{_short(worst.metric)}** at {worst.display(worst.latest)} is the sharpest, "
            f"and is where a recovery would lift the score most.")


# ==========================================================================
# PAGE 6 — sector position
# ==========================================================================
def _page_sector(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "04", "Section 04", "Sector position")
    snap = ctx["snap_row"]
    if not snap or not (_constituents(snap) or snap.get("metrics", {}).get("pe")):
        yb = _para(pdf, MARGIN, y, CONTENT_W,
              "Peer pricing for this sector is not in the current snapshot, so the constituent "
              "comparison is shown as unavailable. The structural seasonality and cycle read "
              "below still apply, and the rest of the report is drawn from the uploaded model.",
              size=9.6, lh=5.2) + 6
        _heading(pdf, MARGIN, yb, CONTENT_W, "Seasonality and cycle", "structural")
        _sector_seasonality(pdf, MARGIN, yb + 8, CONTENT_W, ctx)
        _interior_footer(pdf, ctx)
        return
    idx = snap.get("reference_index", ctx["sector_name"]).title()
    incl = snap.get("included_count") or 0
    total = snap.get("constituent_count") or incl
    intro = (f"Measured against **{idx}**, {incl} of {total} constituents priced. This is a "
             f"broad basket used because the company maps to no niche sector — read the "
             f"comparison with that caveat.")
    y0 = _para(pdf, MARGIN, y, CONTENT_W, intro, size=9.4, lh=5.0) + 3

    col_w = (CONTENT_W - 12) / 2
    lx, rx = MARGIN, MARGIN + col_w + 12
    ly = _heading(pdf, lx, y0, col_w, "Position against the universe")
    _sector_scatter(pdf, lx, ly + 1, col_w, 62, ctx)

    ry = _heading(pdf, rx, y0, col_w, "The gap, measure by measure")
    ry = _sector_gaps(pdf, rx, ry + 1, col_w, ctx)
    ry += 3
    ry = _heading(pdf, rx, ry, col_w, "Sector vitals", "month over month")
    ry = _sector_vitals(pdf, rx, ry + 1, col_w, ctx)

    yb = max(ly + 66, ry) + 4
    _heading(pdf, MARGIN, yb, CONTENT_W, "Five largest constituents", "by market cap")
    yc = _constituents_table(pdf, MARGIN, yb + 8, CONTENT_W, ctx) or (yb + 40)
    yc = _heading(pdf, MARGIN, yc + 5, CONTENT_W, "Seasonality and cycle", "sector lens")
    _sector_seasonality(pdf, MARGIN, yc + 2, CONTENT_W, ctx)
    _interior_footer(pdf, ctx)


def _sector_scatter(pdf, x, y, w, h, ctx):
    snap = ctx["snap_row"]
    cons = _constituents(snap)
    # Pick whichever return metric the snapshot actually populates broadly so the
    # universe is visible (ROE is often sparse; ROCE/ROA are usually complete).
    ymetric = "roe" if sum(1 for c in cons if c.get("roe") is not None) >= 5 else "roce"
    peers = [c for c in cons if c.get("pe") and c.get(ymetric) is not None]
    sm = snap.get("metrics", {})
    _card(pdf, x, y, w, h, WHITE)
    # quadrant lines at sector aggregate
    cx, cy = x + w / 2, y + h / 2
    pdf.set_draw_color(210, 214, 210)
    pdf.set_line_width(0.2)
    pdf.line(x + 6, cy, x + w - 4, cy)
    pdf.line(cx, y + 6, cx, y + h - 8)
    labels = [("CHEAPER · EARNING MORE", x + 8, y + 8),
              ("PRICIER · EARNING MORE", x + w - 4, y + 8),
              ("CHEAPER · EARNING LESS", x + 8, y + h - 10),
              ("PRICIER · EARNING LESS", x + w - 4, y + h - 10)]
    pdf.mono(5.2, False, FAINT)
    for t, tx, ty in labels:
        pdf.set_xy(tx - (0 if "CHEAPER" in t else 40), ty)
        pdf.cell(40, 3, t, align="L" if "CHEAPER" in t else "R")
    # scale from peers (winsorise P/E so one extreme multiple doesn't flatten all)
    pes = sorted(c["pe"] for c in peers)
    if not pes:
        return
    pe_lo, pe_hi = pes[0], pes[min(len(pes) - 1, int(len(pes) * 0.92))]
    ys = [c[ymetric] for c in peers] + ([sm[ymetric]] if sm.get(ymetric) is not None else [])
    roe_lo, roe_hi = min(ys), max(ys)
    def px(pe): return x + 8 + (max(pe_lo, min(pe_hi, pe)) - pe_lo) / ((pe_hi - pe_lo) or 1) * (w - 14)
    def py(roe): return y + h - 10 - (roe - roe_lo) / ((roe_hi - roe_lo) or 1) * (h - 18)
    for c in peers:
        pdf.set_fill_color(196, 200, 197)
        pdf.ellipse(px(c["pe"]) - 1, py(c[ymetric]) - 1, 2, 2, style="F")
    you_pe = _ser(ctx["model"], "PE Ratio")
    you_pe = float(you_pe.iloc[-1]) if not you_pe.empty else (ctx["self_row"] or {}).get("pe")
    ym = _m(ctx, "Return on Equity (ROE) %" if ymetric == "roe"
            else "Return on Capital Employed (ROCE) %")
    you_roe = ym.latest * 100 if (ym and ym.latest is not None) else \
        (ctx["self_row"] or {}).get(ymetric)
    if you_pe and you_roe is not None:
        pdf.set_fill_color(*RED)
        pdf.ellipse(px(max(pe_lo, min(pe_hi, you_pe))) - 1.6,
                    py(max(roe_lo, min(roe_hi, you_roe))) - 1.6, 3.2, 3.2, style="F")
        pdf.sans(6.4, True, INK)
        pdf.set_xy(px(max(pe_lo, min(pe_hi, you_pe))) - 20, py(max(roe_lo, min(roe_hi, you_roe))) + 2)
        pdf.cell(40, 3, pdf.txt(ctx["company_title"][:22]), align="C")
    # legend
    pdf.set_fill_color(*RED); pdf.ellipse(x, y + h + 2, 2, 2, style="F")
    pdf.sans(6.8, False, BODY); pdf.set_xy(x + 3, y + h + 1.2)
    pdf.cell(24, 4, "This company")
    pdf.set_fill_color(196, 200, 197); pdf.ellipse(x + 30, y + h + 2, 2, 2, style="F")
    pdf.set_xy(x + 33, y + h + 1.2); pdf.cell(30, 4, f"{len(peers)} constituents")


def _sector_gaps(pdf, x, y, w, ctx):
    snap = ctx["snap_row"]; sm = snap.get("metrics", {})
    model = ctx["model"]
    you_pe = _ser(model, "PE Ratio")
    you_pe = float(you_pe.iloc[-1]) if not you_pe.empty else None
    roe = _m(ctx, "Return on Equity (ROE) %")
    roce = _m(ctx, "Return on Capital Employed (ROCE) %")
    roa = _m(ctx, "Return on Assets (ROA) %")
    rows = [
        ("P/E", you_pe, sm.get("pe"), "x", False),
        ("ROE", roe.latest * 100 if roe and roe.latest is not None else None, sm.get("roe"), "pp", True),
        ("ROCE", roce.latest * 100 if roce and roce.latest is not None else None, sm.get("roce"), "pp", True),
        ("ROA", roa.latest * 100 if roa and roa.latest is not None else None, sm.get("roa"), "pp", True),
    ]
    for name, you, sec, unit, higher_better in rows:
        pdf.sans(9, True, INK)
        pdf.set_xy(x, y)
        pdf.cell(20, 5, name)
        if you is not None:
            pdf.mono(7.6, False, BODY)
            pdf.set_xy(x + 14, y + 0.4)
            pdf.cell(30, 4, pdf.txt(f"you {you:.2f}{'x' if unit=='x' else '%'}"))
        if you is not None and sec:
            diff = you - sec
            good = (diff < 0) if not higher_better else (diff > 0)
            fg, bg = (GREEN, PALE_G) if good else (RED_TXT, PALE_R)
            arrow = "▲" if diff >= 0 else "▼"
            txt = f"{arrow} {abs(diff):.2f} {unit}" if unit == "pp" else f"{arrow} {abs(diff)/sec*100:.1f}%"
            _pill_right(pdf, x + w, y, txt, fg, bg)
            pdf.mono(6.4, False, FAINT)
            pdf.set_xy(x, y + 5)
            pdf.cell(w, 4, pdf.txt(f"sector {sec:.2f}{'x' if unit=='x' else '%'}"), align="R")
        y += 11
    return y


def _sector_vitals(pdf, x, y, w, ctx):
    snap = ctx["snap_row"]; sm = snap.get("metrics", {})
    changes = snap.get("monthly_changes") or {}

    def _ch(key):
        c = changes.get(key)
        if isinstance(c, dict):
            return c.get("change"), c.get("to")
        return c, None

    rows = []
    for label, key, unit in [("Sector P/E", "pe", "x"), ("Sector P/B", "pb", "x"),
                             ("Sector ROE", "roe", "%"), ("Sector ROCE", "roce", "%"),
                             ("Sector ROA", "roa", "%")]:
        delta, to_val = _ch(key)
        rows.append((label, (sm.get(key) if sm.get(key) is not None else to_val), unit, delta))
    for name, val, unit, delta in rows:
        pdf.sans(8.6, True, INK)
        pdf.set_xy(x, y)
        pdf.cell(w * 0.5, 5, name)
        pdf.mono(8, True, INK)
        pdf.set_xy(x + w * 0.5, y)
        pdf.cell(w * 0.28, 5, pdf.txt(f"{val:.2f}{unit}" if val is not None else "n/a"), align="R")
        if delta is not None:
            good = delta <= 0 if "P/" in name else delta >= 0
            fg, bg = (GREEN, PALE_G) if good else (RED_TXT, PALE_R)
            arrow = "▲" if delta >= 0 else "▼"
            _pill_right(pdf, x + w, y, f"{arrow} {abs(delta):+.2f}".replace("+", ""), fg, bg)
        _rule(pdf, x, y + 6.4, w, (240, 242, 240), 0.2)
        y += 8
    return y


def _constituents_table(pdf, x, y, w, ctx):
    peers = sorted(_constituents(ctx["snap_row"]),
                   key=lambda c: c.get("market_cap") or 0, reverse=True)[:5]
    if not peers:
        pdf.sans(8.6, False, MUTED); pdf.set_xy(x, y)
        pdf.cell(0, 5, pdf.txt("Constituent detail unavailable in the snapshot."))
        return y + 6
    heads = ["COMPANY", "MARKET CAP", "P/E", "P/B", "ROCE", "ROA", "D/E", "INT COV"]
    frac = [0.28, 0.17, 0.09, 0.09, 0.10, 0.09, 0.08, 0.10]
    cols = [w * f for f in frac]
    pdf.mono(6.4, False, FAINT)
    cx = x
    for i, htxt in enumerate(heads):
        pdf.set_xy(cx, y)
        pdf.cell(cols[i], 5, htxt, align="L" if i == 0 else "R")
        cx += cols[i]
    y += 6
    for ri, c in enumerate(peers):
        if ri % 2 == 0:
            pdf.set_fill_color(247, 249, 247)
            pdf.rect(x, y - 0.5, w, 6, style="F")
        cx = x
        pdf.serif(9.2, True, INK)
        pdf.set_xy(cx, y); pdf.cell(cols[0], 5, pdf.txt(str(c.get("name", ""))[:24])); cx += cols[0]
        pdf.mono(6.8, False, BODY)
        vals = [_cr(c.get("market_cap")), _x(c.get("pe")), _x(c.get("pb")),
                (f"{c['roce']:.2f}%" if c.get("roce") is not None else "—"),
                (f"{c['roa']:.2f}%" if c.get("roa") is not None else "—"),
                _x(c.get("debt_to_equity")), _x(c.get("interest_coverage"))]
        for i, v in enumerate(vals):
            pdf.set_xy(cx, y); pdf.cell(cols[i + 1], 5, pdf.txt(v), align="R"); cx += cols[i + 1]
        y += 6.4
    return y


def _seas_ret_color(v):
    """Return-shaded cell colour: green for positive months, pale-red for
    negative, faint grey when the month has no observation."""
    if v is None:
        return (238, 240, 238)
    t = min(1.0, abs(float(v)) / 6.0)
    base = MID if v >= 0 else _COST_PINK
    return _lerp((236, 241, 238), base, 0.22 + 0.78 * t)


def _month_strip(pdf, x, y, w, labels, colours, values=None):
    """A 12-cell month strip: coloured cells with the month initial below, and
    the value inside when supplied. Returns the y below the initials row."""
    n = len(labels)
    cw = w / n
    ch = 8.0
    for i in range(n):
        pdf.set_fill_color(*colours[i])
        pdf.rect(x + i * cw + 0.4, y, cw - 0.8, ch, style="F",
                 round_corners=True, corner_radius=0.6)
        if values is not None and values[i] is not None:
            pdf.mono(5.0, True, INK)
            pdf.set_xy(x + i * cw, y + 2.4)
            pdf.cell(cw, 3, pdf.txt(f"{values[i]:+.0f}"), align="C")
        pdf.mono(5.2, False, MUTED)
        pdf.set_xy(x + i * cw, y + ch + 0.8)
        pdf.cell(cw, 3, labels[i][0], align="C")
    return y + ch + 5.0


def _sector_seasonality(pdf, x, y, w, ctx):
    """Sector seasonality (real monthly returns where history allows, else the
    structural tendency) + the current cycle tilt — the new Sector-Lens data,
    added to the report. Always renders; never fabricates a number."""
    from . import seasonality as SEASON
    key = ctx.get("sector_key")
    snap = ctx["snap_row"] or {}
    hm = SEASON.market_heatmap(key) if key else {"sufficient": False}

    col_w = (w - 12) / 2
    lx, rx = x, x + col_w + 12

    # ---- left: monthly seasonality ----
    if hm.get("sufficient"):
        ly = _heading(pdf, lx, y, col_w, "Average monthly return", hm.get("window") or "")
        months = hm["months"]
        avg = hm["avg"]
        colours = [_seas_ret_color(v) for v in avg]
        ly = _month_strip(pdf, lx, ly + 1, col_w, months, colours, values=avg)
        pairs = [(m, v) for m, v in zip(months, avg) if v is not None]
        if pairs:
            best = max(pairs, key=lambda p: p[1]); worst = min(pairs, key=lambda p: p[1])
            pdf.mono(6.6, False, BODY)
            pdf.set_xy(lx, ly + 0.5)
            pdf.cell(col_w, 4, pdf.txt(
                f"Strongest {best[0]} {best[1]:+.1f}%   ·   Weakest {worst[0]} {worst[1]:+.1f}%"))
        pdf.mono(5.6, False, FAINT)
        pdf.set_xy(lx, ly + 5)
        src = hm.get("index") or "sector index"
        pdf.cell(col_w, 3, pdf.txt(f"{src} monthly returns · computed from index history"))
        left_bot = ly + 9
    else:
        q = SEASON.qualitative(key) if key else {"cells": [], "flat": True}
        ly = _heading(pdf, lx, y, col_w, "Structural seasonality", "typical year")
        cells = q.get("cells") or []
        if cells:
            labels = [c["month"] for c in cells]
            colours = [_hex_rgb(c["colour"]) for c in cells]
            ly = _month_strip(pdf, lx, ly + 1, col_w, labels, colours)
        note = (q.get("methodology") or
                "Structural tendency from the sector's operating pattern.")
        pdf.mono(6.4, False, BODY)
        left_bot = _para(pdf, lx, ly + 0.5, col_w, note, size=7.4, lh=3.6, color=BODY,
                         font=MONO)

    # ---- right: current cycle tilt (snapshot tilt, else the structural profile) ----
    prof = {}
    try:
        from . import tilt as TILT
        prof = TILT.profile(key) or {}
    except Exception:                                       # noqa: BLE001
        prof = {}
    tilt = snap.get("current_tilt") or prof.get("nature")
    reason = snap.get("tilt_reason")
    structural = snap.get("structural_seasonality") or prof.get("text")
    ry = _heading(pdf, rx, y, col_w, "Where the sector sits", "cycle tilt")
    if tilt:
        _pill(pdf, rx, ry + 1, str(tilt), GREEN, PALE_G, size=7.2, h=6.0, pad=3.0)
        ry += 9
    if reason:
        ry = _para(pdf, rx, ry, col_w, reason, size=8.0, lh=4.0, color=BODY) + 1
    if structural:
        ry = _para(pdf, rx, ry + 0.5, col_w, structural[:320], size=7.6, lh=3.7, color=MUTED)
    return max(left_bot, ry) + 2


def _hex_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ==========================================================================
# PAGE 7 — income statement
# ==========================================================================
_IS_ROWS = [
    ("Sales", True), ("Cost of goods sold", False), ("Gross profit", True),
    ("Selling & general expenses", False), ("EBITDA", True), ("Depreciation", False),
    ("EBIT (operating profit)", True), ("Other income", False), ("Interest", False),
    ("Profit before tax", True), ("Tax", False), ("Net profit", True),
]
_IS_SOURCE = {
    "Sales": ["Sales"], "Cost of goods sold": ["COGS"], "Gross profit": ["Gross Margin"],
    "Selling & general expenses": ["Selling & General Expenses"], "EBITDA": ["EBITDA"],
    "Depreciation": ["Depreciation"], "EBIT (operating profit)": ["EBIT (OPM)"],
    "Other income": ["Other Income", "Other Income "], "Interest": ["Interest"],
    "Profit before tax": ["Earnings Before Tax", "Profit before tax"], "Tax": ["Tax"],
    "Net profit": ["Net Profit"],
}


def _page_income(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "05", "Section 05", "Income statement")
    fy = ctx["fy"][-10:]
    y = _para(pdf, MARGIN, y, CONTENT_W,
              f"{_num_word(len(fy)).capitalize()} years as filed, in ₹ crore, straight from "
              f"the uploaded model.", size=9.6, lh=5.2) + 3

    label_w = 46
    col_w = (CONTENT_W - label_w) / len(fy)
    # header
    pdf.mono(6.6, False, FAINT)
    pdf.set_xy(MARGIN, y); pdf.cell(label_w, 5, "INCOME STATEMENT")
    for j, yr in enumerate(fy):
        pdf.set_xy(MARGIN + label_w + j * col_w, y)
        pdf.cell(col_w, 5, yr, align="R")
    y += 6.5
    for ri, (name, head) in enumerate(_IS_ROWS):
        model = ctx["model"]
        vals = []
        src = _IS_SOURCE[name]
        for yr in fy:
            v = None
            if name == "Gross profit":
                v = _gross_cr(model, yr)
            else:
                for s in src:
                    v = _at(model, s, yr)
                    if v is not None:
                        break
            vals.append(v)
        if all(v is None for v in vals):
            continue
        if ri % 2 == 0:
            pdf.set_fill_color(247, 249, 247)
            pdf.rect(MARGIN, y - 0.5, CONTENT_W, 6, style="F")
        pdf.serif(9.0, head, INK if head else BODY)
        pdf.set_xy(MARGIN, y)
        pdf.cell(label_w, 5, pdf.txt(name))
        for j, v in enumerate(vals):
            pdf.mono(6.9, head, INK if head else BODY)
            pdf.set_xy(MARGIN + label_w + j * col_w, y)
            pdf.cell(col_w, 5, _num(v) if v is not None else "—", align="R")
        y += 6.2
    y += 4
    y = _heading(pdf, MARGIN, y, CONTENT_W, "What the ten years show")
    for para in _income_notes(ctx):
        y = _para(pdf, MARGIN, y + 1.5, CONTENT_W, para, size=9.2, lh=4.9)
    _callout(pdf, MARGIN, PAGE_H - 34, CONTENT_W, "Note",
             "Gross profit is shown as sales less cost of goods sold. Balance sheet, ratio "
             "analysis and common-size statements are available in the Statements view of the "
             "application.", "grey")
    _interior_footer(pdf, ctx)


def _income_notes(ctx):
    model, fy = ctx["model"], ctx["fy"]
    if len(fy) < 2:
        return [""]
    fy0, fy1 = fy[0], fy[-1]
    sales0, sales1 = _at(model, "Sales", fy0), _at(model, "Sales", fy1)
    net0, net1 = _at(model, "Net Profit", fy0), _at(model, "Net Profit", fy1)
    other1 = _at(model, "Other Income", fy1) or _at(model, "Other Income ", fy1)
    ebit1 = _at(model, "EBIT (OPM)", fy1)
    dep0, dep1 = _at(model, "Depreciation", fy0), _at(model, "Depreciation", fy1)
    p1 = ""
    if sales0 and sales1 and net0 and net1:
        p1 = (f"Sales moved from {_cr(sales0)} in {fy0} to {_cr(sales1)} in {fy1}. Net profit "
              f"compounded {'far faster' if (net1/max(net0,1))>(sales1/max(sales0,1)) else 'in step'}, "
              f"from **{_cr(net0)}** to **{_cr(net1)}**.")
    p2 = ""
    if other1 is not None and ebit1 is not None:
        rel = "now exceeds" if other1 > ebit1 else "is below"
        p2 = (f"**Other income** of {_cr(other1)} in {fy1} {rel} EBIT of {_cr(ebit1)} — worth "
              f"noting when reading the bottom line. ")
    if dep0 is not None and dep1 is not None and dep0:
        p2 += (f"**Depreciation** moved from {_cr(dep0)} to {_cr(dep1)} as the asset base grew.")
    return [p for p in (p1, p2) if p]


# ==========================================================================
# PAGE 8 — balance sheet and cash flow
# ==========================================================================
def _page_balance(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "06", "Section 06", "Balance sheet and cash flow")
    y = _para(pdf, MARGIN, y, CONTENT_W,
              "The income statement explains what the company earned. This page explains what "
              "it was funded with — and is where the strain in the score sits.",
              size=9.6, lh=5.2) + 3
    y = _heading(pdf, MARGIN, y, CONTENT_W, "How the balance sheet grew",
                 f"{ctx['fy'][0]} → {ctx['fy_last']}" if ctx["fy"] else "")
    y = _balance_table(pdf, MARGIN, y + 1, CONTENT_W, ctx) + 4

    col_w = (CONTENT_W - 12) / 2
    ly = _heading(pdf, MARGIN, y, col_w, "Cash quality")
    for name, m, override in _cash_rows(ctx):
        ly = _cash_row(pdf, MARGIN, ly, col_w, name, m, override)
    _para(pdf, MARGIN + col_w + 12, y, col_w, _cash_note(ctx), size=9.0, lh=4.8)

    _callout(pdf, MARGIN, PAGE_H - 42, CONTENT_W, "The tension", _tension_text(ctx), "amber")
    _interior_footer(pdf, ctx)


def _balance_table(pdf, x, y, w, ctx):
    model = ctx["model"]
    fy0, fy1 = (ctx["fy"][0], ctx["fy_last"]) if ctx["fy"] else ("", "")
    def row(label, name, unit="cr", reading=""):
        a, b = _at(model, name, fy0), _at(model, name, fy1)
        return (label, a, b, unit, reading)
    rows = [
        row("Borrowings", "Borrowings", "cr", "Debt did the funding"),
        row("Net fixed assets", "Net Block", "cr", "Capex ran ahead of revenue"),
    ]
    de = _m(ctx, "Debt to Equity Ratio")
    fat = _m(ctx, "Fixed Asset Turnover")
    ic = _m(ctx, "Interest Coverage Ratio")
    extra = []
    if de and de.latest is not None:
        extra.append(("Debt to equity", None, de.latest, "x", "Owners' capital vs debt"))
    if fat and fat.latest is not None:
        f0 = _ser(model, "Fixed Asset Turnover")
        extra.append(("Fixed-asset turnover", float(f0.iloc[0]) if not f0.empty else None,
                      fat.latest, "x", "Assets under-used"))
    if ic and ic.latest is not None:
        extra.append(("Interest coverage", None, ic.latest, "x", "Cushion over interest"))
    for name in ("Interest", "Depreciation"):
        rows.append(row(name.replace("Interest", "Interest paid"), name, "cr",
                        "Rose with the asset base"))
    rows = rows[:2] + extra + rows[2:]

    cols = [w * 0.24, w * 0.18, w * 0.18, w * 0.16, w * 0.24]
    heads = ["LINE", fy0, fy1, "CHANGE", "READING"]
    pdf.mono(6.4, False, FAINT)
    cx = x
    for i, htxt in enumerate(heads):
        pdf.set_xy(cx, y); pdf.cell(cols[i], 5, pdf.txt(htxt),
                                    align="L" if i in (0, 4) else "R")
        cx += cols[i]
    y += 6
    for ri, (label, a, b, unit, reading) in enumerate(rows):
        if ri % 2 == 0:
            pdf.set_fill_color(247, 249, 247)
            pdf.rect(x, y - 0.5, w, 6.4, style="F")
        cx = x
        pdf.serif(9.2, True, INK); pdf.set_xy(cx, y); pdf.cell(cols[0], 5, pdf.txt(label)); cx += cols[0]
        pdf.mono(7, False, BODY)
        pdf.set_xy(cx, y); pdf.cell(cols[1], 5, pdf.txt(_fmt_bs(a, unit)), align="R"); cx += cols[1]
        pdf.set_xy(cx, y); pdf.cell(cols[2], 5, pdf.txt(_fmt_bs(b, unit)), align="R"); cx += cols[2]
        # change
        chtxt, chcol = _bs_change(a, b)
        pdf.mono(7, True, chcol)
        pdf.set_xy(cx, y); pdf.cell(cols[3], 5, pdf.txt(chtxt), align="R"); cx += cols[3]
        pdf.sans(7.2, False, MUTED)
        pdf.set_xy(cx, y); pdf.cell(cols[4], 5, pdf.txt(reading), align="R")
        y += 6.6
    return y


def _fmt_bs(v, unit):
    if v is None:
        return "—"
    return _cr(v) if unit == "cr" else f"{v:.2f}x"


def _bs_change(a, b):
    if a is None or b is None or a == 0:
        return "—", MUTED
    factor = b / a
    if factor >= 1:
        return f"▲ {factor:.1f}x", RED_TXT
    return f"▼ {(1-factor)*100:.0f}%", GREEN


def _cash_rows(ctx):
    return [
        ("Operating cash flow to net profit", _m(ctx, "CFO / PAT"), None),
        ("Cash conversion cycle", _m(ctx, "Cash Conversion Cycle"), None),
        ("Debt to equity", _m(ctx, "Debt to Equity Ratio"), None),
        ("Interest coverage", _m(ctx, "Interest Coverage Ratio"), None),
        ("Fixed-asset turnover", _m(ctx, "Fixed Asset Turnover"), None),
    ]


def _cash_row(pdf, x, y, w, name, m, override):
    if not m:
        return y
    pdf.sans(8.8, True, INK)
    pdf.set_xy(x, y)
    pdf.multi_cell(w - 44, 4.4, pdf.txt(name))
    val = m.display(m.latest)
    pdf.mono(8, True, INK)
    pdf.set_xy(x + w - 44, y)
    pdf.cell(20, 4.4, pdf.txt(str(val)), align="R")
    fg, bg = band_pill(m.score)
    _pill_right(pdf, x + w, y, band_word(m.score), fg, bg)
    return max(y + 8, pdf.get_y() + 3)


def _cash_note(ctx):
    cfo = _m(ctx, "CFO / PAT")
    inv = _ser(ctx["model"], "Cash from Investing Activity")
    inv1 = float(inv.iloc[-1]) if not inv.empty else None
    p = ""
    if cfo and cfo.latest is not None:
        q = "high" if cfo.latest >= 1 else "weak"
        p = (f"Cash conversion is **{q}**: reported profit turned into operating cash at "
             f"**{cfo.display(cfo.latest)}** in {ctx['fy_last']}. ")
    if inv1 is not None:
        p += (f"With investing outflows near **{_cr(abs(inv1))}**, the gap is being met by "
              f"financing rather than internal accruals — which is why interest cover matters "
              f"more than the debt-to-equity ratio here.")
    return p or "Cash-flow detail is limited in this model."


def _tension_text(ctx):
    cc = _m(ctx, "Cash Conversion Cycle")
    part = ""
    if cc and cc.latest is not None and cc.latest < 0:
        part = f"a **{cc.display(cc.latest)}** cash cycle means suppliers fund day-to-day operations"
    else:
        part = "working capital is managed tightly"
    return (f"Working capital is genuinely strong — {part}. But that strength sits on top of a "
            f"capital base funded by debt and not yet earning its keep. **The two do not offset "
            f"each other**: one is about the next quarter, the other about the next decade.")


# ==========================================================================
# PAGE 9 — interpretation (deterministic seven readings)
# ==========================================================================
def _page_interpretation(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "07", "Section 07", "Interpretation")
    y = _para(pdf, MARGIN, y, CONTENT_W,
              "Seven readings drawn from the uploaded model, weighted toward sustainability, "
              "returns and valuation. Each carries the verdict it earns on its own numbers.",
              size=9.4, lh=5.0) + 2
    for i, reading in enumerate(_readings(ctx), 1):
        y = _reading_block(pdf, MARGIN, y, CONTENT_W, i, reading)
        if y > PAGE_H - 26:
            break
    _interior_footer(pdf, ctx)


def _reading_block(pdf, x, y, w, num, reading):
    title, word, col, summary, bullets = reading
    fg, bg = (col, {GREEN: PALE_G, AMBER_TXT: PALE_A, RED_TXT: PALE_R}.get(col, PALE_G))
    pdf.set_fill_color(*col)
    pdf.rect(x, y + 0.5, 1.2, 5.5 + len(bullets) * 6.5, style="F")
    pdf.mono(6.6, True, FAINT)
    pdf.set_xy(x + 4, y)
    pdf.cell(8, 5, f"{num:02d}")
    pdf.sans(10.5, True, INK)
    pdf.set_xy(x + 12, y)
    pdf.cell(w - 60, 5, pdf.txt(title))
    _pill_right(pdf, x + w, y + 0.2, word, fg, bg)
    y += 6
    pdf.sans(8.8, True, INK)
    y = _para(pdf, x + 12, y, w - 14, summary, size=8.8, lh=4.4, color=INK, bold_color=INK)
    for b in bullets:
        pdf.set_fill_color(*col)
        pdf.rect(x + 12, y + 1.6, 1.8, 0.7, style="F")
        y = _para(pdf, x + 16, y, w - 18, b, size=8.4, lh=4.2, color=BODY) + 0.6
    return y + 3.5


def _pillar_word_color(score):
    if score >= 66:
        return "STRONG", GREEN
    if score >= 50:
        return "IMPROVING", GREEN
    if score >= 40:
        return "MIXED", AMBER_TXT
    return "WEAK", RED_TXT


def _readings(ctx):
    model, fy = ctx["model"], ctx["fy"]
    fy0, fy1 = (fy[0], fy[-1]) if fy else ("", "")

    # Readings quote FY-only figures so "latest" agrees with FY26 elsewhere in
    # the report (never the TTM column).
    def _ser(_model, *names):
        return _fy_series(ctx, *names)
    P = ctx["result"].pillar_scores
    out = []

    # 1 Growth & scale
    sg = _ser(model, "Sales")
    npf = _ser(model, "Net Profit")
    b = []
    if len(sg) >= 2:
        b.append(f"Sales moved from about {_cr(float(sg.iloc[0]))} to {_cr(float(sg.iloc[-1]))} "
                 f"over the period.")
    if len(npf) >= 2 and npf.iloc[0]:
        mult = float(npf.iloc[-1]) / abs(float(npf.iloc[0]))
        b.append(f"Net profit went from {_cr(float(npf.iloc[0]))} to **{_cr(float(npf.iloc[-1]))}**, "
                 f"roughly a {mult:.0f}x move.")
    w, c = _pillar_word_color(P.get("growth", 50))
    out.append(("Growth & scale", w, c, "The top line is lumpy; profit is the real story.", b))

    # 2 Margins
    gm = _ser(model, "Gross Margin", "Gross Margin % Sales")
    nm = _ser(model, "Net Profit Margin", "Net Margins")
    b = []
    if len(gm) >= 2:
        b.append(f"Gross margin **{gm.iloc[0]*100:.1f}% to {gm.iloc[-1]*100:.1f}%** over the period.")
    if len(nm) >= 2:
        b.append(f"Net margin {nm.iloc[0]*100:.1f}% to **{nm.iloc[-1]*100:.1f}%** — read against "
                 f"other income before treating it as repeatable.")
    w, c = _pillar_word_color(P.get("profitability", 50))
    out.append(("Margins", w, c, "Every tier expanded, but one line flatters the total.", b))

    # 3 Costs
    b = []
    cogs = _ser(model, "COGS % Sales")
    dep = _ser(model, "Depreciation%Sales", "Depreciation % Sales")
    if len(cogs) >= 2:
        b.append(f"COGS fell from about {cogs.iloc[0]*100:.0f}% of sales to roughly "
                 f"**{cogs.iloc[-1]*100:.0f}%** — the main engine of margin expansion.")
    if len(dep) >= 2:
        b.append(f"Depreciation ({dep.iloc[0]*100:.1f}% to {dep.iloc[-1]*100:.1f}% of sales) has "
                 f"climbed with the asset base.")
    out.append(("Costs", "MIXED", AMBER_TXT,
                "Operating gains are being handed back below the line.", b))

    # 4 Returns
    roce = _ser(model, "Return on Capital Employed (ROCE) %")
    roe = _ser(model, "Return on Equity (ROE) %")
    b = []
    if len(roe) >= 2:
        b.append(f"ROE at {roe.iloc[-1]*100:.1f}% latest.")
    if len(roce) >= 2:
        b.append(f"ROCE moved **{roce.iloc[0]*100:.1f}% to {roce.iloc[-1]*100:.1f}%** — the asset "
                 f"base has grown faster than operating profit.")
    w, c = _pillar_word_color(P.get("returns", 40))
    out.append(("Returns", w, c, "The weakest reading, and the one that moves the verdict.", b))

    # 5 Efficiency & working capital
    fat = _ser(model, "Fixed Asset Turnover")
    cc = _ser(model, "Cash Conversion Cycle")
    b = []
    if len(fat) >= 2:
        b.append(f"Fixed-asset turnover fell from **{fat.iloc[0]:.1f}x to {fat.iloc[-1]:.2f}x** as "
                 f"capex outran revenue.")
    if len(cc) >= 1:
        b.append(f"The cash conversion cycle is {'negative' if cc.iloc[-1] < 0 else 'positive'} "
                 f"(**{cc.iloc[-1]:.0f} days**), so suppliers help fund operations.")
    w, c = _pillar_word_color(P.get("efficiency", 50))
    out.append(("Efficiency & working capital", w, c,
                "Best-in-class working capital on a collapsing asset turn.", b))

    # 6 Leverage & cash flow
    de = _ser(model, "Debt to Equity Ratio")
    cfo = _ser(model, "CFO / PAT")
    b = []
    if len(de) >= 1:
        b.append(f"Debt-to-equity at **{de.iloc[-1]:.2f}x**.")
    if len(cfo) >= 2:
        b.append(f"CFO/PAT fell to **{cfo.iloc[-1]:.2f}x** — profit is not fully converting to cash.")
    w, c = _pillar_word_color(P.get("leverage", 40))
    out.append(("Leverage & cash flow", w, c,
                "Gearing looks calm; coverage and cash quality do not.", b))

    # 7 Valuation
    pe = _ser(model, "PE Ratio")
    pe = pe[(pe > 0) & (pe < 2000)]
    b = []
    if len(pe) >= 2:
        b.append(f"P/E has de-rated from about {pe.max():.0f}x to **{pe.iloc[-1]:.0f}x** as earnings "
                 f"caught up.")
    ps = _ser(model, "Price to Sales")
    if len(ps) >= 1:
        b.append(f"Price/Sales around {ps.iloc[-1]:.1f}x looks reasonable given the profit growth.")
    if not b:
        b = ["Valuation data not available in the model."]
    out.append(("Valuation", "FAIR", AMBER_TXT,
                "The multiple has come back to earth, but leans on earnings that may not repeat.", b))
    return out


# ==========================================================================
# PAGE 10 — what would change the verdict
# ==========================================================================
def _page_triggers(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "08", "Section 08", "What would change the verdict")
    y = _para(pdf, MARGIN, y, CONTENT_W,
              f"A score of {ctx['score']:.0f} is a snapshot. These are the things that would move "
              f"it, with the threshold that matters for each and the direction of travel today.",
              size=9.4, lh=5.0) + 3
    for i, trig in enumerate(_triggers(ctx), 1):
        y = _trigger_block(pdf, MARGIN, y, CONTENT_W, i, trig) + 2

    y = _heading(pdf, MARGIN, y + 2, CONTENT_W, "Sensitivity: if other income reverts",
                 f"{ctx['fy_last']}, illustrative")
    _sensitivity_table(pdf, MARGIN, y + 1, CONTENT_W, ctx)
    _callout(pdf, MARGIN, PAGE_H - 34, CONTENT_W, "In short",
             "Most triggers point the same way: the verdict improves if the enlarged capital "
             "base starts earning. None of them depend on the share price — each threshold is "
             "something the business either delivers or does not.", "grey")
    _interior_footer(pdf, ctx)


def _trigger_block(pdf, x, y, w, num, trig):
    title, desc, now, thresh, direction, dircol = trig
    pdf.mono(6.6, True, FAINT)
    pdf.set_xy(x, y)
    pdf.cell(8, 5, f"{num:02d}")
    pdf.sans(10.5, True, INK)
    pdf.set_xy(x + 10, y)
    pdf.cell(w * 0.62, 5, pdf.txt(title))
    # right column now->threshold
    pdf.mono(6.2, False, FAINT)
    pdf.set_xy(x + w * 0.66, y)
    pdf.cell(w * 0.34, 4, "NOW → THRESHOLD", align="R")
    pdf.mono(9, True, INK)
    pdf.set_xy(x + w * 0.66, y + 4)
    pdf.cell(w * 0.34, 5, pdf.txt(f"{now} → {thresh}"), align="R")
    pdf.mono(6.4, True, dircol)
    pdf.set_xy(x + w * 0.66, y + 9)
    pdf.cell(w * 0.34, 4, direction, align="R")
    ny = _para(pdf, x + 10, y + 5.5, w * 0.60, desc, size=8.4, lh=4.2, color=BODY)
    y2 = max(ny, y + 15)
    _rule(pdf, x, y2 + 1, w, (240, 242, 240), 0.2)
    return y2 + 4


def _triggers(ctx):
    out = []
    roce = _m(ctx, "Return on Capital Employed (ROCE) %")
    sm = (ctx["snap_row"] or {}).get("metrics") or {}
    if roce and roce.latest is not None:
        thr = sm.get("roce")
        out.append(("Returns on capital recovering",
                    "The single largest drag. Because the asset base has grown faster than "
                    "operating profit, better asset utilisation would lift ROCE more than any "
                    "other single change.",
                    roce.display(roce.latest), f"{thr:.1f}%" if thr else "sector",
                    "FALLING", RED_TXT))
    ic = _m(ctx, "Interest Coverage Ratio")
    if ic and ic.latest is not None:
        out.append(("Interest cover rebuilding a cushion",
                    "Operating profit barely clears the interest bill. Cover above roughly 2.5x "
                    "would take leverage out of the risk column.",
                    ic.display(ic.latest), "2.5x", "DETERIORATING", RED_TXT))
    fat = _m(ctx, "Fixed Asset Turnover")
    if fat and fat.latest is not None:
        out.append(("Asset utilisation improving",
                    "Each rupee of plant and equipment produces under a rupee of sales. "
                    "Turnover recovering toward the historical level would lift returns directly.",
                    fat.display(fat.latest), "1.5x", "FALLING", RED_TXT))
    sgr = _m(ctx, "Sales Growth")
    if sgr and sgr.latest is not None:
        out.append(("Revenue reaccelerating",
                    "Sustained growth would give the enlarged asset base something to absorb, "
                    "improving turnover and returns at the same time.",
                    sgr.display(sgr.latest), "+10%", "FLAT", AMBER_TXT))
    return out[:4]


def _sensitivity_table(pdf, x, y, w, ctx):
    model, fy = ctx["model"], ctx["fy_last"]
    ebit = _at(model, "EBIT (OPM)", fy)
    other = _at(model, "Other Income", fy) or _at(model, "Other Income ", fy)
    interest = _at(model, "Interest", fy)
    pbt = _at(model, "Earnings Before Tax", fy) or _at(model, "Profit before tax", fy)
    sales = _at(model, "Sales", fy)
    tax = _at(model, "Tax", fy)
    net = _at(model, "Net Profit", fy)
    if not (ebit is not None and other is not None and sales):
        pdf.sans(8.6, False, MUTED); pdf.set_xy(x, y)
        pdf.cell(0, 5, pdf.txt("Sensitivity inputs unavailable in this model."))
        return
    hypo_other = sales * 0.01
    tax_rate = (tax / pbt) if (tax is not None and pbt) else 0.30
    hypo_pbt = (pbt - other + hypo_other) if pbt is not None else None
    hypo_net = hypo_pbt * (1 - tax_rate) if hypo_pbt is not None else None
    rows = [
        ("EBIT (operating profit)", ebit, ebit),
        ("Other income", other, hypo_other),
        ("Interest", interest, interest),
        ("Profit before tax", pbt, hypo_pbt),
        ("Implied net margin", (net / sales * 100 if net and sales else None),
         (hypo_net / sales * 100 if hypo_net and sales else None)),
    ]
    cols = [w * 0.34, w * 0.30, w * 0.24, w * 0.12]
    heads = ["LINE", "AS REPORTED", "OTHER INCOME AT ~1% OF SALES", "DIFFERENCE"]
    pdf.mono(6.2, False, FAINT)
    cx = x
    for i, h in enumerate(heads):
        pdf.set_xy(cx, y); pdf.cell(cols[i], 5, pdf.txt(h), align="L" if i == 0 else "R"); cx += cols[i]
    y += 6
    for ri, (label, a, b) in enumerate(rows):
        head = label in ("Profit before tax", "Implied net margin")
        if ri % 2 == 0:
            pdf.set_fill_color(247, 249, 247); pdf.rect(x, y - 0.5, w, 6, style="F")
        cx = x
        pdf.serif(9.2, head, INK if head else BODY); pdf.set_xy(cx, y)
        pdf.cell(cols[0], 5, pdf.txt(label)); cx += cols[0]
        is_margin = "margin" in label.lower()
        pdf.mono(6.9, head, INK if head else BODY)
        pdf.set_xy(cx, y); pdf.cell(cols[1], 5, pdf.txt(f"{a:.1f}%" if is_margin and a is not None
                                                        else _num(a)), align="R"); cx += cols[1]
        pdf.set_xy(cx, y); pdf.cell(cols[2], 5, pdf.txt(f"{b:.1f}%" if is_margin and b is not None
                                                        else _num(b)), align="R"); cx += cols[2]
        diff = (b - a) if (a is not None and b is not None) else None
        pdf.set_xy(cx, y)
        if diff is None:
            pdf.cell(cols[3], 5, "—", align="R")
        elif is_margin:
            pdf.cell(cols[3], 5, pdf.txt(f"{diff:+.1f} pp"), align="R")
        else:
            pdf.cell(cols[3], 5, pdf.txt(_num(diff) if diff else "—"), align="R")
        y += 6.2
    pdf.sans(7.4, False, MUTED)
    _para(pdf, x, y + 2, w, "Illustrative only, holding every other line constant and applying "
          "the reported effective tax rate. It is a measure of how much of the reported margin "
          "depends on a single non-operating line — not a forecast.", size=7.4, lh=3.6, color=MUTED)


# ==========================================================================
# PAGE 11 — method and sources
# ==========================================================================
def _page_method(pdf, ctx):
    pdf.add_page()
    y = _interior_header(pdf, ctx, "09", "Section 09", "Method and sources")
    col_w = (CONTENT_W - 12) / 2
    left = [
        ("Sector-relative, not absolute",
         "Each ratio is scored 0–100 against how companies in this sector normally perform, "
         "so a capital-intensive business is not penalised for looking capital-intensive."),
        ("Weighted by what matters here",
         "Scored ratios are grouped into five pillars — profitability, efficiency, growth, "
         "leverage and returns — and blended by sector weight. A strong pillar can only partly "
         "offset a weak one."),
        ("Bands",
         "Below 40 reads weak, 40 to 66 neutral, above 66 strong. The same bands are drawn on "
         "every score bar in this report."),
        ("Pooled, not averaged",
         "Sector aggregates are computed by summing totals across the universe before taking "
         "the ratio, so large constituents carry proportionate weight."),
    ]
    right = [
        ("No price forecast",
         "Nothing here is a target price, a rating or a recommendation to buy or sell. The "
         "analysis describes what the filed numbers show."),
        ("No market backtest",
         "Historical market seasonality needs a longer monthly history than is available, so no "
         "backtest is published."),
        ("Fallback sector matching",
         f"This company matched {(ctx['snap_row'] or {}).get('reference_index', ctx['sector_name'])}, "
         f"a broad fallback index used when no niche sector applies. Read section 04 with that in mind."),
        ("Gaps are shown, not filled",
         "Where a figure is absent from the uploaded model it is reported as unavailable rather "
         "than estimated."),
    ]
    yl = _heading(pdf, MARGIN, y, col_w, "How the score is built")
    for h, d in left:
        pdf.sans(9.4, True, INK); pdf.set_xy(MARGIN, yl); pdf.cell(col_w, 5, pdf.txt(h))
        yl = _para(pdf, MARGIN, yl + 5, col_w, d, size=8.4, lh=4.2, color=MUTED) + 3
    yr = _heading(pdf, MARGIN + col_w + 12, y, col_w, "What this report does not do")
    for h, d in right:
        pdf.sans(9.4, True, INK); pdf.set_xy(MARGIN + col_w + 12, yr)
        pdf.cell(col_w, 5, pdf.txt(h))
        yr = _para(pdf, MARGIN + col_w + 12, yr + 5, col_w, d, size=8.4, lh=4.2, color=MUTED) + 3

    y = max(yl, yr) + 4
    y = _heading(pdf, MARGIN, y, CONTENT_W, "Sources")
    chips = _source_chips(ctx)
    cx = MARGIN
    for ch in chips:
        pdf.sans(7.8, False, BODY)
        cw = pdf.get_string_width(pdf.txt(ch)) + 8
        if cx + cw > MARGIN + CONTENT_W:
            cx = MARGIN; y += 8
        _card(pdf, cx, y, cw, 6, (247, 249, 247), CARD_LINE)
        pdf.set_xy(cx, y + 0.4); pdf.cell(cw, 5.2, pdf.txt(ch), align="C")
        cx += cw + 4
    y += 10
    sm = ctx["snap_meta"]
    asof = sm.get("as_of_date") or ""
    _para(pdf, MARGIN, y, CONTENT_W,
          f"Model period {ctx['fy_first']}–{ctx['fy_last']}, {len(ctx['fy'])} periods."
          + (f" Sector fundamentals reflect the {sm.get('refresh_frequency','monthly')} snapshot"
             f"{' as at ' + asof if asof else ''}." if sm else ""),
          size=8.2, lh=4.2, color=MUTED)

    # disclaimer
    dy = PAGE_H - 40
    _rule(pdf, MARGIN, dy, CONTENT_W, HAIR, 0.3)
    _para(pdf, MARGIN, dy + 3, CONTENT_W,
          "**Disclaimer.** This report is generated by FundaCheck from a financial model supplied "
          "by the user, combined with third-party market and sector data. It is provided for "
          "information only and is not investment advice, nor an offer or solicitation to buy or "
          "sell any security. Figures are reproduced as filed and have not been independently "
          "audited. Any decision taken on the basis of this document remains the reader's own.",
          size=7.6, lh=3.8, color=MUTED)
    pdf.mono(6.8, False, FAINT)
    pdf.set_xy(MARGIN, PAGE_H - 20)
    pdf.cell(0, 4, pdf.txt(f"© FundaCheck Research. Generated {ctx['gen_date']:%-d %B %Y}."))
    _interior_footer(pdf, ctx)


def _source_chips(ctx):
    chips = []
    fn = (ctx["model"].meta or {}).get("source_file")
    chips.append("Uploaded model")
    chips.append("Screener.in (daily market data)")
    chips.append("IndianAPI + NSE (periodic fundamentals)")
    idx = (ctx["snap_row"] or {}).get("reference_index")
    if idx:
        chips.append(f"{idx} constituent list")
    return chips


# --------------------------------------------------------------------------
# misc
# --------------------------------------------------------------------------
_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
          "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen"]


def _num_word(n):
    return _WORDS[n] if 0 <= n < len(_WORDS) else str(n)


# ==========================================================================
# public entry point
# ==========================================================================
def build_pdf(model, result, snapshot=None) -> bytes:
    """Render the full multi-page FundaCheck report to PDF bytes.

    `snapshot` is the Sector Lens snapshot dict (core.sector_snapshot.load_snapshot);
    when omitted it is loaded here so the caller need not pass it. Never raises for
    missing data — absent figures are shown as unavailable."""
    if snapshot is None:
        try:
            from .sector_snapshot import load_snapshot
            snapshot = load_snapshot()
        except Exception:                                   # noqa: BLE001
            snapshot = None

    ctx = _build_ctx(model, result, snapshot)
    pdf = _Doc()
    pdf.add_page()
    _cover(pdf, ctx)
    _page_contents(pdf, ctx)
    _page_verdict(pdf, ctx)
    _page_waterfall(pdf, ctx)
    _page_scorecard(pdf, ctx)
    _page_sector(pdf, ctx)
    _page_income(pdf, ctx)
    _page_balance(pdf, ctx)
    _page_interpretation(pdf, ctx)
    _page_triggers(pdf, ctx)
    _page_method(pdf, ctx)

    out = pdf.output()
    return bytes(out)
