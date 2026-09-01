"""
sector_universe.py
------------------
Maps each of the nine FundaCheck sectors to an official NSE constituent
universe, and loads those constituents from committed CSVs.

Design rules
============
* **NSE membership is the backbone.** A company belongs to a sector because it
  is a constituent of that sector's NSE index — never because of IndianAPI's
  free-text ``industry`` field (that is metadata only).
* **Honest labelling.** Every mapping carries a ``mapping_type`` of ``exact``,
  ``approximate`` or ``proxy``. Four NSE sector indices line up cleanly with a
  FundaCheck bucket (IT, FMCG, Pharma, Real Estate); the rest are broader NSE
  universes used as the closest available stand-in and are marked as such. The
  Sector Lens shows this label so an approximate universe is never presented as
  an exact sector definition.
* **No fabricated membership.** Constituents come from a CSV the monthly job
  refreshes from NSE. If a sector's CSV is missing, that sector is reported as
  ``skipped`` with a reason — its metrics are never invented.

Constituent CSVs live in ``data/nse_constituents/<key>.csv`` with at least a
``Symbol`` column (the format of NSE's published ``ind_nifty*list.csv`` files).
``source_csv_url`` records where each list is published so the monthly job can
refresh it; the download itself happens in the pipeline, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Repo-root/data/nse_constituents
_CONSTITUENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "nse_constituents"


@dataclass(frozen=True)
class SectorUniverse:
    key: str                    # FundaCheck sector key (matches core.sectors)
    sector_name: str            # display name
    reference_index: str        # NSE index/universe name
    mapping_type: str           # 'exact' | 'approximate' | 'proxy'
    is_financial: bool          # lenders → ROCE '—'
    source_csv_url: str         # where NSE publishes this constituent list
    mapping_note: str = ""      # why, when not exact
    cap: int | None = None      # keep at most this many (by index weight) — proxies

    @property
    def csv_path(self) -> Path:
        return _CONSTITUENTS_DIR / f"{self.key}.csv"


# The nine FundaCheck sectors, in the order the Lens lists them.
# Keys match core.sectors.SECTORS. mapping_type is stated honestly.
UNIVERSES: dict[str, SectorUniverse] = {
    "banking": SectorUniverse(
        key="banking", sector_name="Banking & Finance",
        reference_index="NIFTY FINANCIAL SERVICES", mapping_type="approximate",
        is_financial=True,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyfinancelist.csv",
        mapping_note="Financial-services index — includes NBFCs and insurers "
                     "alongside banks, broader than pure banking.",
    ),
    "it_services": SectorUniverse(
        key="it_services", sector_name="IT Services & Software",
        reference_index="NIFTY IT", mapping_type="exact", is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyitlist.csv",
    ),
    "fmcg": SectorUniverse(
        key="fmcg", sector_name="FMCG & Consumer Staples",
        reference_index="NIFTY FMCG", mapping_type="exact", is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyfmcglist.csv",
    ),
    "pharma": SectorUniverse(
        key="pharma", sector_name="Pharma & Healthcare",
        reference_index="NIFTY PHARMA", mapping_type="exact", is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftypharmalist.csv",
    ),
    "realestate": SectorUniverse(
        key="realestate", sector_name="Real Estate",
        reference_index="NIFTY REALTY", mapping_type="exact", is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyrealtylist.csv",
    ),
    "infrastructure": SectorUniverse(
        key="infrastructure", sector_name="Infrastructure",
        reference_index="NIFTY INFRASTRUCTURE", mapping_type="approximate",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyinfralist.csv",
        mapping_note="Broad infrastructure index — spans power, telecom and "
                     "energy as well as construction.",
        cap=25,
    ),
    "manufacturing": SectorUniverse(
        key="manufacturing", sector_name="Manufacturing",
        reference_index="NIFTY INDIA MANUFACTURING", mapping_type="approximate",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyindiamanufacturinglist.csv",
        mapping_note="Broad manufacturing index — overlaps auto, metals and "
                     "capital goods.",
        cap=25,
    ),
    "retail": SectorUniverse(
        key="retail", sector_name="Retail & Consumer",
        reference_index="NIFTY INDIA CONSUMPTION", mapping_type="proxy",
        is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_niftyindiaconsumptionlist.csv",
        mapping_note="No dedicated NSE retail index — the consumption index is "
                     "used as a proxy for consumer/retail names.",
        cap=20,
    ),
    "generic": SectorUniverse(
        key="generic", sector_name="Diversified / Other",
        reference_index="NIFTY 100", mapping_type="proxy", is_financial=False,
        source_csv_url="https://niftyindices.com/IndexConstituent/ind_nifty100list.csv",
        mapping_note="No sector index maps to 'diversified' — a large-cap "
                     "proxy, clearly labelled and capped.",
        cap=20,
    ),
}

ORDER = list(UNIVERSES.keys())


def _read_symbols(path: Path) -> list[str]:
    """Read the ``Symbol`` column from an NSE constituent CSV. Tolerant of the
    exact header casing NSE uses; returns [] if the file is absent/empty."""
    if not path.exists():
        return []
    import csv
    symbols: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        # find the symbol column case-insensitively
        field = None
        for name in (reader.fieldnames or []):
            if name and name.strip().lower() == "symbol":
                field = name
                break
        if field is None:
            return []
        for row in reader:
            sym = (row.get(field) or "").strip().upper()
            if sym:
                symbols.append(sym)
    return symbols


def load_constituents(uni: SectorUniverse) -> list[str]:
    """The NSE symbols for a sector, de-duplicated and capped (for proxies).
    Empty when the constituent CSV has not been provided yet."""
    seen: set[str] = set()
    ordered: list[str] = []
    for sym in _read_symbols(uni.csv_path):
        if sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    if uni.cap is not None:
        ordered = ordered[: uni.cap]
    return ordered


def all_unique_symbols() -> tuple[dict[str, list[str]], list[str]]:
    """(per-sector symbol lists, deduped union across all sectors).

    The union size is what the monthly job checks against the 400-request safety
    threshold — a company shared by two sectors is fetched once and reused."""
    per_sector: dict[str, list[str]] = {}
    union: list[str] = []
    seen: set[str] = set()
    for key in ORDER:
        syms = load_constituents(UNIVERSES[key])
        per_sector[key] = syms
        for s in syms:
            if s not in seen:
                seen.add(s)
                union.append(s)
    return per_sector, union
