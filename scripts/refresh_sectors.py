#!/usr/bin/env python
"""
refresh_sectors.py
------------------
Offline monthly runner that fetches IndianAPI fundamentals for unique NSE constituents,
computes pooled sector metrics, Top 10 rankings, month-over-month differences, and current tilt,
then writes data/sector_snapshot.json.

Safety & Reliability Rules:
1. Credit guard: Exact unique company count calculated before making any API calls.
   If expected requests > 400 (safety threshold), ABORTS BEFORE ANY API REQUEST.
2. Hard stop on 401, 403, 429: Does not burn credits if authentication or rate limit errors occur.
3. Zero extra API calls for Top 10: Reuses constituent responses already fetched.
4. Official NSE links: Links directly to official NSE quote pages using actual constituent symbols.
5. Preserves previous snapshot: Validates before atomic write; never overwrites valid data with a broken snapshot.
6. Offline testable: --fixtures runs validation offline using saved JSON dumps.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import indianapi as E  # noqa: E402
from core import sector_universe as U  # noqa: E402
from core import tilt as T  # noqa: E402

LOGGER = logging.getLogger("fundacheck.refresh")

SNAPSHOT_PATH = ROOT / "data" / "sector_snapshot.json"
COMPANY_MASTER_PATH = ROOT / "data" / "company_master.csv"
MAX_REQUESTS = 460          # unique companies across all 27 niche universes ~= 410
REFRESH_FREQUENCY = "monthly"
SOURCE = "IndianAPI (company fundamentals) + NSE Indices (constituents)"


def _load_previous(path: Path = SNAPSHOT_PATH) -> dict | None:
    """Load existing snapshot for MoM comparison and fallback."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("sectors") else None
    except Exception:
        return None


def _load_name_map(path: Path = COMPANY_MASTER_PATH) -> dict[str, str]:
    """Map NSE symbol -> company name from data/company_master.csv."""
    if not path.exists():
        return {}
    import csv
    m = {}
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                sym = (row.get("nse_symbol") or row.get("symbol") or "").strip().upper()
                name = (row.get("name") or row.get("company") or "").strip()
                if sym and name:
                    m[sym] = name
    except Exception as e:
        LOGGER.warning("Could not read company master: %s", e)
    return m


def _prev_sector(previous: dict | None, sector_key: str) -> dict | None:
    if not previous:
        return None
    sectors = previous.get("sectors")
    if isinstance(sectors, list):
        for s in sectors:
            if isinstance(s, dict) and s.get("key") == sector_key:
                return s
    elif isinstance(sectors, dict):
        return sectors.get(sector_key)
    return None


def _mom_changes(current: dict, previous: dict | None) -> dict:
    """Compute month-over-month differences for metrics and Top 10."""
    if not previous:
        return {}
    cm, pm = current.get("metrics", {}), previous.get("metrics", {})
    changes = {}
    for k in ("pe", "pb", "roe", "roa", "roce"):
        cv, pv = cm.get(k), pm.get(k)
        if isinstance(cv, (int, float)) and isinstance(pv, (int, float)):
            changes[k] = {
                "from": pv,
                "to": cv,
                "change": round(cv - pv, 2),
            }

    cur_top = [r.get("nse_symbol") for r in current.get("constituents", []) if r.get("nse_symbol")]
    prev_top = [r.get("nse_symbol") for r in previous.get("constituents", []) if r.get("nse_symbol")]
    if cur_top and prev_top:
        new_names = [s for s in cur_top if s not in set(prev_top)]
        dropped_names = [s for s in prev_top if s not in set(cur_top)]
        changes["constituents"] = {
            "changed": len(new_names),
            "new_entrants": new_names,
            "dropped": dropped_names,
        }
    return changes


def _pooled_growth(companies: list[E.Company], cur_attr: str, prev_attr: str) -> float | None:
    """Pooled YoY growth = (Σ current / Σ prior - 1) * 100.

    Only companies with both a current and a *positive* prior value are pooled,
    so a negative or zero prior base can't invert the sign of the result.
    """
    cur = sum(getattr(c, cur_attr) for c in companies
              if getattr(c, cur_attr) is not None
              and getattr(c, prev_attr) is not None and getattr(c, prev_attr) > 0)
    prev = sum(getattr(c, prev_attr) for c in companies
               if getattr(c, cur_attr) is not None
               and getattr(c, prev_attr) is not None and getattr(c, prev_attr) > 0)
    return round((cur / prev - 1) * 100.0, 2) if prev > 0 else None


def _sector_revenue_growth(companies: list[E.Company]) -> float | None:
    """Pooled trailing revenue (top-line) growth for a sector."""
    return _pooled_growth(companies, "revenue", "prev_revenue")


def _sector_earnings_growth(companies: list[E.Company]) -> float | None:
    """Pooled trailing earnings (net-income) growth for a sector."""
    return _pooled_growth(companies, "net_income", "prev_net_income")


def build_snapshot(
    fetch_fn,
    per_sector: dict[str, list[str]],
    union: list[str],
    name_map: dict[str, str],
    previous: dict | None,
    request_stats: dict,
) -> dict:
    """Fetch unique companies once, then compute all 9 sector profiles."""
    parsed: dict[str, E.Company] = {}
    skipped_fetch: dict[str, str] = {}

    for sym in union:
        # IndianAPI resolves NSE ticker symbols reliably, whereas suffixed
        # company names ("Infosys Ltd.") frequently return "Stock not found".
        # Query by symbol; the display name still comes from the API response.
        try:
            raw = fetch_fn(sym, expected_symbol=sym)
        except E.IndianAPIError as err:
            LOGGER.error("IndianAPI error fetching %s: %s — aborting further API calls", sym, err)
            raise

        request_stats["actual"] += 1
        if raw is None:
            request_stats["failed"] += 1
            skipped_fetch[sym] = "fetch_failed"
            continue

        comp = E.parse_company(raw, default_symbol=sym)
        if comp is None:
            request_stats["failed"] += 1
            skipped_fetch[sym] = "unparseable"
            continue

        request_stats["successful"] += 1
        parsed[sym] = comp

    sectors_out = []
    for key in U.ORDER:
        uni = U.UNIVERSES[key]
        syms = per_sector.get(key, [])
        companies, skipped = [], []

        for s in syms:
            if s in parsed:
                companies.append(parsed[s])
            else:
                skipped.append({"symbol": s, "reason": skipped_fetch.get(s, "not_fetched")})

        metrics = E.pooled_metrics(companies, is_financial=uni.is_financial)
        # ALL constituents ranked by market cap (no Top-10 truncation).
        constituents = E.rank_top(companies, None, is_financial=uni.is_financial)

        years = [c.fiscal_year for c in companies if c.fiscal_year]
        period = max(set(years), key=years.count) if years else None

        earnings_growth = _sector_earnings_growth(companies)
        revenue_growth = _sector_revenue_growth(companies)

        # Sector PEG = sector P/E / trailing pooled earnings-growth %. Defined
        # only when earnings growth is positive (a negative/zero base makes PEG
        # meaningless), matching how PEG is read for single stocks.
        pe_v = metrics.get("pe")
        if isinstance(pe_v, (int, float)) and isinstance(earnings_growth, (int, float)) and earnings_growth > 0:
            metrics["peg"] = round(pe_v / earnings_growth, 2)
        else:
            metrics["peg"] = None

        # Sector Piotroski F-score: simple average of constituents' scores.
        # Suppressed for financial sectors — the F-score is not meaningful for
        # lenders, same convention as ROCE — so a handful of finance-adjacent
        # names can't skew the sector figure.
        if uni.is_financial:
            metrics["piotroski"] = None
        else:
            fscores = [c.piotroski for c in companies if c.piotroski is not None]
            metrics["piotroski"] = round(sum(fscores) / len(fscores), 1) if fscores else None

        # Sector EPS growth: market-cap-weighted mean of per-company EPS growth.
        eps_pairs = [(c.eps_growth, c.market_cap) for c in companies
                     if c.eps_growth is not None and c.market_cap is not None and c.market_cap > 0]
        wsum = sum(w for _, w in eps_pairs)
        metrics["eps_growth"] = round(sum(g * w for g, w in eps_pairs) / wsum, 2) if wsum > 0 else None

        sector = {
            "key": key,
            "sector_name": uni.sector_name,
            "reference_index": uni.reference_index,
            "mapping_type": uni.mapping_type,
            "mapping_note": uni.mapping_note,
            "is_financial": uni.is_financial,
            "constituent_count": len(syms),
            "included_count": len(companies),
            "skipped_count": len(skipped),
            "skipped": skipped,
            "financial_period": f"FY{period}" if period else None,
            "metrics": metrics,
            "earnings_growth": earnings_growth,
            "revenue_growth": revenue_growth,
            "is_fallback": uni.is_fallback,
            "constituents": constituents,
        }

        prev_sec = _prev_sector(previous, key)
        sector["monthly_changes"] = _mom_changes(sector, prev_sec)
        sector.update(T.current_tilt(key, sector, prev_sec))
        sectors_out.append(sector)

    now = dt.datetime.now(dt.timezone.utc)
    prev_date = (previous or {}).get("as_of_date")
    snapshot = {
        "generated_at": now.isoformat(),
        "as_of_date": now.date().isoformat(),
        "previous_snapshot_date": prev_date,
        "refresh_frequency": REFRESH_FREQUENCY,
        "source": SOURCE,
        "expected_api_requests": request_stats["expected"],
        "actual_api_requests": request_stats["actual"],
        "successful_requests": request_stats["successful"],
        "failed_requests": request_stats["failed"],
        "skipped_requests": request_stats["expected"] - request_stats["actual"],
        "safety_threshold": MAX_REQUESTS,
        "methodology": E.METHODOLOGY,
        "sectors": sectors_out,
    }
    return snapshot


def validate(snapshot: dict) -> list[str]:
    """Validate snapshot integrity before writing. Empty list means valid."""
    problems = []
    sectors = snapshot.get("sectors", [])
    if len(sectors) != len(U.ORDER):
        problems.append(f"Expected {len(U.ORDER)} sectors, got {len(sectors)}")

    usable = [s for s in sectors if s.get("included_count", 0) > 0]
    if not usable:
        problems.append("No sector has any included company")

    for s in sectors:
        if s.get("is_financial") and s.get("metrics", {}).get("roce") is not None:
            problems.append(f"{s['key']}: financial sector must have ROCE = null")

    return problems


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def run(
    *,
    fixtures: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    target_sectors: list[str] | None = None,
    max_budget: int = MAX_REQUESTS,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    previous = _load_previous()
    name_map = _load_name_map()

    if fixtures:
        LOGGER.info("Running in offline fixture mode")
        fx_files = {
            "HDFCBANK": ROOT / "api" / "indianapi_hdfc_raw.json",
            "TCS": ROOT / "api" / "indianapi_tcs_raw.json",
            "ADANIENT": ROOT / "api" / "indianapi_adani_enterprises_raw.json",
        }
        fx_map = {k: json.loads(p.read_text(encoding="utf-8")) for k, p in fx_files.items() if p.exists()}

        def fetch_fn(name: str, expected_symbol: str = ""):
            return fx_map.get(expected_symbol)

        per_sector = {k: [] for k in U.ORDER}
        per_sector["banking"] = ["HDFCBANK"] if "HDFCBANK" in fx_map else []
        per_sector["it_services"] = ["TCS"] if "TCS" in fx_map else []
        per_sector["infrastructure"] = ["ADANIENT"] if "ADANIENT" in fx_map else []
        union = list(fx_map.keys())
        name_map = name_map or {
            "HDFCBANK": "HDFC Bank",
            "TCS": "Tata Consultancy Services",
            "ADANIENT": "Adani Enterprises",
        }
    else:
        per_sector, union = U.all_unique_symbols()
        if target_sectors:
            per_sector = {k: (v if k in target_sectors else []) for k, v in per_sector.items()}
            union = [s for k in target_sectors for s in per_sector.get(k, [])]
            union = list(dict.fromkeys(union))

        if limit and limit > 0:
            union = union[:limit]
            per_sector = {k: [s for s in v if s in set(union)] for k, v in per_sector.items()}

        if dry_run:
            print(f"[DRY-RUN] Total unique companies to fetch: {len(union)}")
            print(f"[DRY-RUN] Safety threshold: {max_budget}")
            for k in U.ORDER:
                print(f"  {k}: {len(per_sector.get(k, []))} constituents")
            return 0

        expected = len(union)
        LOGGER.info("Unique companies to fetch: %d (threshold %d)", expected, max_budget)
        if expected > max_budget:
            LOGGER.error(
                "Expected %d requests exceeds safety threshold %d — ABORTING with ZERO API calls. Previous snapshot preserved.",
                expected,
                max_budget,
            )
            return 3

        if not E.has_key():
            LOGGER.error("INDIANAPI_KEY is not set — aborting refresh, keeping previous snapshot.")
            return 2

        if not union:
            LOGGER.error("No NSE constituents found in data/nse_constituents/ — aborting.")
            return 2

        fetcher = E.Fetcher()
        fetch_fn = fetcher.fetch

    stats = {"expected": expected, "actual": 0, "successful": 0, "failed": 0}
    try:
        snapshot = build_snapshot(fetch_fn, per_sector, union, name_map, previous, stats)
    except Exception as exc:
        LOGGER.error("Failed building snapshot: %s — previous snapshot preserved.", exc)
        return 4

    problems = validate(snapshot)
    if problems:
        LOGGER.error("Validation failed, previous snapshot kept:\n  - %s", "\n  - ".join(problems))
        return 5

    _atomic_write(SNAPSHOT_PATH, snapshot)
    LOGGER.info(
        "Successfully wrote %s: %d/%d requests succeeded across %d sectors.",
        SNAPSHOT_PATH,
        stats["successful"],
        stats["actual"],
        len(snapshot["sectors"]),
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Monthly Sector Lens refresh pipeline.")
    ap.add_argument("--fixtures", action="store_true", help="Offline validation run from saved JSON fixtures.")
    ap.add_argument("--dry-run", action="store_true", help="Calculate unique companies and exit without API calls.")
    ap.add_argument("--limit", type=int, default=None, help="Cap number of companies to fetch (testing).")
    ap.add_argument("--sector", type=str, default=None, help="Comma-separated sector keys to refresh.")
    ap.add_argument("--max-budget", type=int, default=MAX_REQUESTS, help="Custom credit safety limit (default 400).")

    args = ap.parse_args()
    sectors = [s.strip() for s in args.sector.split(",")] if args.sector else None
    return run(
        fixtures=args.fixtures,
        dry_run=args.dry_run,
        limit=args.limit,
        target_sectors=sectors,
        max_budget=args.max_budget,
    )


if __name__ == "__main__":
    raise SystemExit(main())

