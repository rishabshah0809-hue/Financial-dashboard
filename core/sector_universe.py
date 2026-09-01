"""
sector_universe.py
------------------
Authoritative mapping from FundaCheck's 9 sectors to NSE index constituent lists,
including exact / approximate / proxy mapping labels and metric applicability.

Sector universes:
  - Banking & Finance       -> NIFTY FINANCIAL SERVICES (approximate)
  - IT Services & Software  -> NIFTY IT (exact)
  - FMCG & Consumer Staples -> NIFTY FMCG (exact)
  - Pharma & Healthcare     -> NIFTY PHARMA (exact)
  - Real Estate             -> NIFTY REALTY (exact)
  - Infrastructure          -> NIFTY INFRASTRUCTURE (approximate, capped 25)
  - Manufacturing           -> NIFTY INDIA MANUFACTURING (approximate, capped 25)
  - Retail & Consumer       -> NIFTY INDIA CONSUMPTION (proxy, capped 20)
  - Diversified / Other     -> NIFTY 100 (proxy, capped 20)
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

_CONSTITUENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "nse_constituents"
_COMPANY_MASTER = Path(__file__).resolve().parent.parent / "data" / "company_master.csv"

# The five headline metrics reported by Sector Lens
METRICS = ("pe", "pb", "roe", "roce", "roa")

LENDER_ROCE_REASON = "ROCE is not a meaningful metric for lenders."


@dataclass(frozen=True)
class SectorUniverse:
    key: str
    sector_name: str
    reference_index: str
    mapping_type: str            # "exact" | "approximate" | "proxy"
    is_financial: bool
    source_csv_url: str
    mapping_note: str = ""
    cap: int | None = None

    @property
    def name(self) -> str:
        return self.sector_name

    @property
    def index_label(self) -> str:
        return self.reference_index

    @property
    def csv_path(self) -> Path:
        return _CONSTITUENTS_DIR / f"{self.key}.csv"


UNIVERSES: dict[str, SectorUniverse] = {
    "banking": SectorUniverse(
        key="banking",
        sector_name="Banking & Finance",
        reference_index="NIFTY FINANCIAL SERVICES",
        mapping_type="approximate",
        is_financial=True,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyfinancelist.csv",
        mapping_note="Financial-services index — includes NBFCs and insurers alongside banks, broader than pure banking.",
    ),
    "it_services": SectorUniverse(
        key="it_services",
        sector_name="IT Services & Software",
        reference_index="NIFTY IT",
        mapping_type="exact",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyitlist.csv",
    ),
    "fmcg": SectorUniverse(
        key="fmcg",
        sector_name="FMCG & Consumer Staples",
        reference_index="NIFTY FMCG",
        mapping_type="exact",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyfmcglist.csv",
    ),
    "pharma": SectorUniverse(
        key="pharma",
        sector_name="Pharma & Healthcare",
        reference_index="NIFTY PHARMA",
        mapping_type="exact",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftypharmalist.csv",
    ),
    "realestate": SectorUniverse(
        key="realestate",
        sector_name="Real Estate",
        reference_index="NIFTY REALTY",
        mapping_type="exact",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyrealtylist.csv",
    ),
    "infrastructure": SectorUniverse(
        key="infrastructure",
        sector_name="Infrastructure",
        reference_index="NIFTY INFRASTRUCTURE",
        mapping_type="approximate",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyinfralist.csv",
        mapping_note="Broad infrastructure index — spans power, telecom and energy as well as construction.",
        cap=25,
    ),
    "manufacturing": SectorUniverse(
        key="manufacturing",
        sector_name="Manufacturing",
        reference_index="NIFTY INDIA MANUFACTURING",
        mapping_type="approximate",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyindiamanufacturing_list.csv",
        mapping_note="Broad manufacturing index — overlaps auto, metals and capital goods.",
        cap=25,
    ),
    "retail": SectorUniverse(
        key="retail",
        sector_name="Retail & Consumer",
        reference_index="NIFTY INDIA CONSUMPTION",
        mapping_type="proxy",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyconsumptionlist.csv",
        mapping_note="No dedicated NSE retail index — the consumption index is used as a proxy for consumer/retail names.",
        cap=20,
    ),
    "generic": SectorUniverse(
        key="generic",
        sector_name="Diversified / Other",
        reference_index="NIFTY 100",
        mapping_type="proxy",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_nifty100list.csv",
        mapping_note="No sector index maps to 'diversified' — a large-cap proxy, clearly labelled and capped.",
        cap=20,
    ),
}

ORDER = list(UNIVERSES.keys())
UNIVERSE = UNIVERSES  # alias for backward compatibility


def metric_applicable(sector_key: str, metric: str) -> tuple[bool, str]:
    """Return (is_applicable, reason_if_not) for one sector/metric pair."""
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
    """Parse an NSE Indices constituent CSV into Constituent rows.
    Tolerant of column name variations (Symbol, ISIN Code, Company Name).
    """
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
    """Load constituent records for a sector, capped if specified."""
    if not uni.csv_path.exists():
        return []
    try:
        text = uni.csv_path.read_text(encoding="utf-8-sig")
        items = parse_constituents(text)
    except Exception:
        return []

    seen = set()
    deduped = []
    for c in items:
        key = c.isin or c.symbol
        if key not in seen:
            seen.add(key)
            deduped.append(c)

    if uni.cap is not None:
        deduped = deduped[: uni.cap]
    return deduped


def all_unique_constituents() -> tuple[dict[str, list[Constituent]], list[Constituent]]:
    """Return (per_sector_dict, unique_union_list) deduplicated by ISIN/symbol."""
    per_sector: dict[str, list[Constituent]] = {}
    union: list[Constituent] = []
    seen: set[str] = set()

    for key in ORDER:
        uni = UNIVERSES[key]
        constits = load_constituents(uni)
        per_sector[key] = constits
        for c in constits:
            k = c.isin or c.symbol
            if k not in seen:
                seen.add(k)
                union.append(c)

    return per_sector, union


def all_unique_symbols() -> tuple[dict[str, list[str]], list[str]]:
    """Return (per_sector_symbols, unique_union_symbols)."""
    per_sec_c, union_c = all_unique_constituents()
    per_sec_s = {k: [c.symbol for c in v] for k, v in per_sec_c.items()}
    union_s = [c.symbol for c in union_c]
    return per_sec_s, union_s

