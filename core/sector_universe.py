"""
sector_universe.py
------------------
Single source of truth for the Sector Lens niche-industry peer universes.

Each entry is an OFFICIAL NSE Nifty sectoral index (constituent CSV published on
niftyindices.com). The Sector Lens classifies the analysed company by its NSE
symbol's index membership and compares it against ALL constituents of the most
specific matching niche index — never a broad thematic bucket.

Classification rule (see classify_symbol):
  1. specific niche index wins over a broad-parent index
     (e.g. Bank/NBFC/Insurance/Housing Finance beat Financial Services;
      Pharma/Hospitals beat Healthcare);
  2. if a company is in no niche index, it falls back to a broad index
     (Commodities / Infrastructure) clearly labelled "Broad / Proxy";
  3. if it is in nothing, it is Unclassified.

This module does NOT touch core/sectors.py, which owns the scoring benchmarks.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

_CONSTITUENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "nse_constituents"

METRICS = ("pe", "pb", "roe", "roce", "roa")
LENDER_ROCE_REASON = "ROCE is not a meaningful metric for lenders."

# tier: 0 = specific niche, 1 = broad-parent, 2 = broad fallback/proxy
TIER_SPECIFIC, TIER_BROAD_PARENT, TIER_FALLBACK = 0, 1, 2


@dataclass(frozen=True)
class SectorUniverse:
    key: str
    sector_name: str
    reference_index: str
    csv_file: str
    is_financial: bool = False
    tier: int = TIER_SPECIFIC
    mapping_type: str = "exact"        # "exact" | "broad_proxy"
    mapping_note: str = ""

    @property
    def name(self) -> str:
        return self.sector_name

    @property
    def index_label(self) -> str:
        return self.reference_index

    @property
    def is_fallback(self) -> bool:
        return self.tier == TIER_FALLBACK

    @property
    def source_csv_url(self) -> str:
        return f"https://niftyindices.com/IndexConstituent/{self.csv_file}"

    @property
    def csv_path(self) -> Path:
        return _CONSTITUENTS_DIR / f"{self.key}.csv"


# key, Sector name, NSE index label, csv filename, is_financial, tier
_DEFS = [
    ("bank",                 "Bank",                          "NIFTY BANK",                          "ind_niftybanklist.csv",                       True,  TIER_SPECIFIC),
    ("nbfc",                 "NBFC",                          "NIFTY NBFC",                          "ind_niftyNBFC_list.csv",                      True,  TIER_SPECIFIC),
    ("housing_finance",      "Housing Finance",               "NIFTY HOUSING FINANCE",               "ind_niftyHousingFinance_list.csv",            True,  TIER_SPECIFIC),
    ("insurance",            "Insurance",                     "NIFTY INSURANCE",                     "ind_niftyInsurance_list.csv",                 True,  TIER_SPECIFIC),
    ("financial_services",   "Financial Services",            "NIFTY FINANCIAL SERVICES",            "ind_niftyfinancelist.csv",                    True,  TIER_BROAD_PARENT),
    ("it",                   "IT",                            "NIFTY IT",                            "ind_niftyitlist.csv",                         False, TIER_SPECIFIC),
    ("telecom",              "Telecommunications",            "NIFTY TELECOMMUNICATIONS",            "ind_niftyTelecommunications_list.csv",        False, TIER_SPECIFIC),
    ("media",                "Media",                         "NIFTY MEDIA",                         "ind_niftymedialist.csv",                      False, TIER_SPECIFIC),
    ("pharma",               "Pharma",                        "NIFTY PHARMA",                        "ind_niftypharmalist.csv",                     False, TIER_SPECIFIC),
    ("hospitals",            "Hospitals",                     "NIFTY HOSPITALS",                     "ind_niftyHospitals_list.csv",                 False, TIER_SPECIFIC),
    ("healthcare",           "Healthcare",                    "NIFTY HEALTHCARE",                    "ind_niftyhealthcarelist.csv",                 False, TIER_BROAD_PARENT),
    ("auto",                 "Auto",                          "NIFTY AUTO",                          "ind_niftyautolist.csv",                       False, TIER_SPECIFIC),
    ("fmcg",                 "FMCG",                          "NIFTY FMCG",                          "ind_niftyfmcglist.csv",                       False, TIER_SPECIFIC),
    ("consumer_durables",    "Consumer Durables",             "NIFTY CONSUMER DURABLES",             "ind_niftyconsumerdurableslist.csv",           False, TIER_SPECIFIC),
    ("consumer_services",    "Consumer Services",             "NIFTY CONSUMER SERVICES",             "ind_niftyConsumerServices_list.csv",          False, TIER_SPECIFIC),
    ("retail",               "Retail",                        "NIFTY RETAIL",                        "ind_niftyRetail_list.csv",                    False, TIER_SPECIFIC),
    ("realty",               "Realty",                        "NIFTY REALTY",                        "ind_niftyrealtylist.csv",                     False, TIER_SPECIFIC),
    ("metal",                "Metal",                         "NIFTY METAL",                         "ind_niftymetallist.csv",                      False, TIER_SPECIFIC),
    ("cement",               "Cement",                        "NIFTY CEMENT",                        "ind_NiftyCement_list.csv",                    False, TIER_SPECIFIC),
    ("chemicals",            "Chemicals",                     "NIFTY CHEMICALS",                     "ind_niftyChemicals_list.csv",                 False, TIER_SPECIFIC),
    ("oil_gas",              "Oil & Gas",                     "NIFTY OIL & GAS",                     "ind_niftyoilgaslist.csv",                     False, TIER_SPECIFIC),
    ("power",                "Power",                         "NIFTY POWER",                         "ind_niftyPower_list.csv",                     False, TIER_SPECIFIC),
    ("capital_goods",        "Capital Goods",                 "NIFTY CAPITAL GOODS",                 "ind_niftyCapitalGoods_list.csv",              False, TIER_SPECIFIC),
    ("construction",         "Construction",                  "NIFTY CONSTRUCTION",                  "ind_niftyConstruction_list.csv",              False, TIER_SPECIFIC),
    ("commercial_transport", "Commercial & Transport Services","NIFTY COMMERCIAL & TRANSPORT SERVICES","ind_niftyCommercialTransportServices_list.csv",False, TIER_SPECIFIC),
    # Broad fallback indices — used only when a company is in no niche index.
    ("commodities",          "Commodities (broad)",           "NIFTY COMMODITIES",                   "ind_niftycommoditieslist.csv",                False, TIER_FALLBACK),
    ("infrastructure",       "Infrastructure (broad)",        "NIFTY INFRASTRUCTURE",                "ind_niftyinfralist.csv",                      False, TIER_FALLBACK),
]

UNIVERSES: dict[str, SectorUniverse] = {
    key: SectorUniverse(
        key=key, sector_name=name, reference_index=idx, csv_file=csvf,
        is_financial=fin, tier=tier,
        mapping_type=("broad_proxy" if tier == TIER_FALLBACK else "exact"),
        mapping_note=("Broad fallback index — used only for companies that map to no niche sector."
                      if tier == TIER_FALLBACK else ""),
    )
    for (key, name, idx, csvf, fin, tier) in _DEFS
}

ORDER = list(UNIVERSES.keys())
UNIVERSE = UNIVERSES  # backward-compat alias

# Niche keys (exclude broad fallbacks) — the selectable niche sectors.
NICHE_KEYS = [k for k in ORDER if not UNIVERSES[k].is_fallback]


def metric_applicable(sector_key: str, metric: str) -> tuple[bool, str]:
    uni = UNIVERSES.get(sector_key)
    if uni and uni.is_financial and metric == "roce":
        return False, LENDER_ROCE_REASON
    return True, ""


@dataclass(frozen=True)
class Constituent:
    symbol: str
    isin: str
    company: str


def parse_constituents(csv_text: str) -> list[Constituent]:
    out: list[Constituent] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return out
    cols = {name.strip().lower(): name for name in reader.fieldnames if name}

    def col(*aliases):
        for a in aliases:
            if a in cols:
                return cols[a]
        return None

    c_sym = col("symbol")
    c_isin = col("isin code", "isin", "isincode")
    c_name = col("company name", "company", "name")
    if not c_sym:
        return out
    for row in reader:
        sym = (row.get(c_sym) or "").strip().upper()
        isin = (row.get(c_isin) or "").strip().upper() if c_isin else ""
        name = (row.get(c_name) or "").strip() if c_name else sym
        if sym:
            out.append(Constituent(symbol=sym, isin=isin, company=name))
    return out


def load_constituents(uni: SectorUniverse) -> list[Constituent]:
    """Load ALL constituent records for a sector (no cap / no truncation)."""
    if not uni.csv_path.exists():
        return []
    try:
        items = parse_constituents(uni.csv_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return []
    seen, deduped = set(), []
    for c in items:
        k = c.isin or c.symbol
        if k not in seen:
            seen.add(k)
            deduped.append(c)
    return deduped


def all_unique_constituents() -> tuple[dict[str, list[Constituent]], list[Constituent]]:
    per_sector: dict[str, list[Constituent]] = {}
    union: list[Constituent] = []
    seen: set[str] = set()
    for key in ORDER:
        constits = load_constituents(UNIVERSES[key])
        per_sector[key] = constits
        for c in constits:
            k = c.isin or c.symbol
            if k not in seen:
                seen.add(k)
                union.append(c)
    return per_sector, union


def all_unique_symbols() -> tuple[dict[str, list[str]], list[str]]:
    per_sec_c, union_c = all_unique_constituents()
    per_sec_s = {k: [c.symbol for c in v] for k, v in per_sec_c.items()}
    union_s = [c.symbol for c in union_c]
    return per_sec_s, union_s


# --- Company -> primary niche sector classification -------------------------

_SYMBOL_INDEX: dict[str, list[str]] | None = None


def _symbol_index() -> dict[str, list[str]]:
    """Map NSE symbol -> list of sector keys it is a constituent of (cached)."""
    global _SYMBOL_INDEX
    if _SYMBOL_INDEX is None:
        idx: dict[str, list[str]] = {}
        per, _ = all_unique_constituents()
        for key in ORDER:
            for c in per.get(key, []):
                idx.setdefault(c.symbol.upper(), []).append(key)
        _SYMBOL_INDEX = idx
    return _SYMBOL_INDEX


def classify_symbol(symbol: str) -> tuple[str | None, str]:
    """Return (sector_key, label) for an NSE symbol.

    label is one of: "exact" (specific niche), "broad_proxy" (fallback index),
    or "unclassified" (in no index). Most specific niche wins; ties break by the
    declared ORDER so the narrower sector listed first is preferred.
    """
    if not symbol:
        return None, "unclassified"
    memberships = _symbol_index().get(symbol.strip().upper(), [])
    if not memberships:
        return None, "unclassified"
    # rank by tier (specific first), then ORDER position
    memberships.sort(key=lambda k: (UNIVERSES[k].tier, ORDER.index(k)))
    best = memberships[0]
    return best, ("broad_proxy" if UNIVERSES[best].is_fallback else "exact")
