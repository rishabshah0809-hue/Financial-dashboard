"""
sector_universe.py
------------------
Maps FundaCheck's nine sectors to an authoritative NSE index constituent list
(published as CSV by NSE Indices, niftyindices.com), and declares which sector
metrics are financially meaningful.

The Sector lens uses this to know, per sector:
  * which NSE index defines the constituent universe (Company, Symbol, ISIN),
  * a human "reference index" label to show as the source, and
  * whether each headline ratio is applicable (e.g. ROCE is NOT meaningful for
    lenders, so Banking & Finance renders it as an em dash with a reason).

Nothing here touches the scoring benchmarks in core/sectors.py — those remain
the yardstick for the Dashboard and Ratio deep dive. This module only feeds the
Sector lens's live sector aggregates.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

# NSE Indices publishes each index's constituents as a stable CSV with columns
# "Company Name, Industry, Symbol, Series, ISIN Code".
NSE_CSV_BASE = "https://www.niftyindices.com/IndexConstituent/{file}"

# The five headline metrics the Sector lens reports.
METRICS = ("pe", "pb", "roe", "roce", "roa")


@dataclass(frozen=True)
class SectorUniverse:
    key: str                 # FundaCheck sector key (matches core.sectors)
    name: str                # FundaCheck sector display name
    index_label: str         # human label shown as the source index
    csv_file: str            # niftyindices constituent CSV filename
    is_financial: bool       # lenders: ROCE / EBIT-based metrics not meaningful

    @property
    def csv_url(self) -> str:
        return NSE_CSV_BASE.format(file=self.csv_file)


# One NSE index per FundaCheck sector. A few FundaCheck buckets are aggregates,
# so they map to the closest single NSE index; the Lens always shows the actual
# index label so the source is explicit. Filenames follow niftyindices' scheme
# and are validated on the first real pipeline run (a wrong name simply yields
# "constituent list unavailable" for that sector, never fabricated data).
UNIVERSE: dict[str, SectorUniverse] = {
    "generic":        SectorUniverse("generic", "Diversified / Other",
                                     "NIFTY 50", "ind_nifty50list.csv", False),
    "it_services":    SectorUniverse("it_services", "IT Services & Software",
                                     "NIFTY IT", "ind_niftyitlist.csv", False),
    "fmcg":           SectorUniverse("fmcg", "FMCG & Consumer Staples",
                                     "NIFTY FMCG", "ind_niftyfmcglist.csv", False),
    "banking":        SectorUniverse("banking", "Banking & Financial Services",
                                     "NIFTY BANK", "ind_niftybanklist.csv", True),
    "infrastructure": SectorUniverse("infrastructure", "Infrastructure, Power & Capital Goods",
                                     "NIFTY INFRASTRUCTURE", "ind_niftyinfralist.csv", False),
    "manufacturing":  SectorUniverse("manufacturing", "Manufacturing & Industrials",
                                     "NIFTY INDIA MANUFACTURING",
                                     "ind_niftyindiamanufacturinglist.csv", False),
    "pharma":         SectorUniverse("pharma", "Pharmaceuticals & Healthcare",
                                     "NIFTY PHARMA", "ind_niftypharmalist.csv", False),
    "retail":         SectorUniverse("retail", "Retail & E-commerce",
                                     "NIFTY INDIA CONSUMPTION",
                                     "ind_niftyindiaconsumptionlist.csv", False),
    "realestate":     SectorUniverse("realestate", "Real Estate",
                                     "NIFTY REALTY", "ind_niftyrealtylist.csv", False),
}

# Why a metric is withheld for a sector (shown next to the em dash).
NOT_APPLICABLE_REASON = {
    ("financial", "roce"): (
        "ROCE is not a meaningful metric for lenders — interest is a bank/NBFC's "
        "core input, not a financing cost on capital employed."
    ),
}


def metric_applicable(sector_key: str, metric: str) -> tuple[bool, str]:
    """Return (is_applicable, reason_if_not) for one sector/metric pair."""
    uni = UNIVERSE.get(sector_key)
    if uni and uni.is_financial and metric == "roce":
        return False, NOT_APPLICABLE_REASON[("financial", "roce")]
    return True, ""


@dataclass(frozen=True)
class Constituent:
    symbol: str
    isin: str
    company: str


def parse_constituents(csv_text: str) -> list[Constituent]:
    """Parse an NSE Indices constituent CSV into Constituent rows.

    Tolerant to column-name casing/spacing; requires a Symbol and an ISIN Code.
    """
    out: list[Constituent] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return out
    cols = {name.strip().lower(): name for name in reader.fieldnames}

    def col(*aliases):
        for a in aliases:
            if a in cols:
                return cols[a]
        return None

    c_sym = col("symbol")
    c_isin = col("isin code", "isin")
    c_name = col("company name", "company", "name")
    if not (c_sym and c_isin):
        return out
    for row in reader:
        sym = (row.get(c_sym) or "").strip()
        isin = (row.get(c_isin) or "").strip()
        name = (row.get(c_name) or "").strip() if c_name else sym
        if sym and isin:
            out.append(Constituent(symbol=sym, isin=isin, company=name))
    return out
