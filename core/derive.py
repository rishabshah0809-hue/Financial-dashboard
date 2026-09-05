"""
derive.py
---------
Compute the benchmark ratios from the raw statements when the workbook does not
supply them.

Why this exists: a "Ratio Analysis" sheet is usually a grid of formulas. If the
file was written by a tool that did not cache the results (or the sheet was
renamed, or the ratios are named differently), reading it yields nothing and the
whole analysis dies with "none of the benchmark ratios could be found" — even
though every input needed to compute them is sitting in the income statement and
balance sheet next door.

So: anything the workbook provides is trusted and used as-is. Anything missing
is derived here, from the statements, using the standard definition.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .parser import FinancialModel


def _row(model: FinancialModel, *names: str) -> pd.Series:
    """First matching row from the historical statements, as a clean Series."""
    for name in names:
        for frame in (model.historical, model.ratios):
            if not frame.empty and name in frame.index:
                series = pd.to_numeric(frame.loc[name], errors="coerce")
                series = series.replace([np.inf, -np.inf], np.nan).dropna()
                if not series.empty:
                    return series
    return pd.Series(dtype="float64")


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise divide on the shared years, with zero denominators dropped."""
    if numerator.empty or denominator.empty:
        return pd.Series(dtype="float64")
    frame = pd.DataFrame({"n": numerator, "d": denominator}).dropna()
    frame = frame[frame["d"] != 0]
    if frame.empty:
        return pd.Series(dtype="float64")
    return (frame["n"] / frame["d"]).replace([np.inf, -np.inf], np.nan).dropna()


def _growth(series: pd.Series) -> pd.Series:
    if series.empty or len(series) < 2:
        return pd.Series(dtype="float64")
    # abs() on the base so a swing out of a loss does not read as a fall
    return (series.diff() / series.shift(1).abs()).replace(
        [np.inf, -np.inf], np.nan
    ).dropna()


def derived_ratios(model: FinancialModel) -> dict[str, pd.Series]:
    """Every benchmark ratio this module knows how to build from the statements."""
    sales = _row(model, "Sales", "Revenue", "Net Sales")
    cogs = _row(model, "COGS", "Cost of Goods Sold", "Raw Material Cost")
    ebitda = _row(model, "EBITDA")
    ebit = _row(model, "EBIT (OPM)", "EBIT", "Operating Profit")
    profit = _row(model, "Net Profit", "Net profit", "PAT")
    interest = _row(model, "Interest", "Finance Cost")
    borrowings = _row(model, "Borrowings", "Total Debt")
    capital = _row(model, "Equity Share Capital", "Share Capital")
    reserves = _row(model, "Reserves", "Reserves and Surplus")
    assets = _row(model, "Total Asset", "Total Assets")
    net_block = _row(model, "Net Block", "Fixed Assets")
    receivables = _row(model, "Receivables", "Trade Receivables", "Debtors")
    inventory = _row(model, "Inventory", "Inventories")
    cfo = _row(model, "Cash from Operating Activity", "Cash from Operations")
    eps = _row(model, "Earnings per Share", "EPS")

    equity = capital.add(reserves, fill_value=0.0) if not reserves.empty else capital
    # Capital employed = what the business is funded with, debt included.
    capital_employed = equity.add(borrowings, fill_value=0.0) if not borrowings.empty else equity
    # Payables are not reported directly here, so back them out of the balance
    # sheet identity the same way the workbook's own sheet does.
    payables = _row(model, "Other Liabilities", "Trade Payables", "Payables")

    out: dict[str, pd.Series] = {
        "Sales Growth": _growth(sales),
        "Net Profit Growth": _growth(profit),
        "EBITDA Growth": _growth(ebitda),
        "EPS Growth": _growth(eps),
        "EBITDA Margin": _safe_div(ebitda, sales),
        "Net Profit Margin": _safe_div(profit, sales),
        "Return on Equity (ROE) %": _safe_div(profit, equity),
        "Return on Capital Employed (ROCE) %": _safe_div(ebit, capital_employed),
        "Return on Assets (ROA) %": _safe_div(profit, assets),
        "Debt to Equity Ratio": _safe_div(borrowings, equity),
        "Interest Coverage Ratio": _safe_div(ebit, interest),
        "Fixed Asset Turnover": _safe_div(sales, net_block),
        "CFO / Sales": _safe_div(cfo, sales),
        "CFO / PAT": _safe_div(cfo, profit),
        "Debtor Days": _safe_div(receivables, sales) * 365,
        "Inventory Days": _safe_div(inventory, cogs) * 365,
        "Payable Days": _safe_div(payables, cogs) * 365,
    }

    cycle = pd.DataFrame({
        "debtor": out["Debtor Days"],
        "inventory": out["Inventory Days"],
        "payable": out["Payable Days"],
    }).dropna(how="all")
    if not cycle.empty:
        out["Cash Conversion Cycle"] = (
            cycle["debtor"].fillna(0) + cycle["inventory"].fillna(0)
            - cycle["payable"].fillna(0)
        )

    price = model.meta.get("current_price")
    if price and not eps.empty:
        # Only the latest year has a meaningful P/E — today's price against each
        # historical EPS is the standard trailing view.
        out["PE Ratio"] = (price / eps.replace(0, np.nan)).dropna()

    return {name: series for name, series in out.items() if not series.empty}


# Common-size lines and the base each is expressed against. Income-statement
# lines are shown as a % of Sales; balance-sheet lines as a % of Total Assets —
# the standard common-size convention (and what the Excel sheet does).
_CS_PNL = ("Sales", "COGS", "Gross Profit", "Selling & General Expenses",
           "EBITDA", "Depreciation", "EBIT (OPM)", "Other Income ", "Other Income",
           "Interest", "Earnings Before Tax", "Tax", "Net Profit")
_CS_BS = ("Equity Share Capital", "Reserves", "Borrowings", "Other Liabilities",
          "Net Block", "Capital Work in Progress", "Investments", "Receivables",
          "Inventory", "Cash & Bank")


def fill_missing_common_size(model: FinancialModel) -> list[str]:
    """
    Build a common-size statement from the raw statements when the workbook did
    not supply one (its Common Size sheet is a grid of formulas that reads empty
    when the export cached no results).

    Every line is stored as a fraction (0.83 == 83%), which is what the UI
    expects — the statements table and the "₹100 of sales" card both multiply by
    100. Does nothing when the workbook already has a common-size sheet.
    """
    if not model.common_size.empty or model.historical.empty:
        return []
    h = model.historical
    if "Sales" not in h.index:
        return []

    sales = pd.to_numeric(h.loc["Sales"], errors="coerce").replace(0, np.nan)
    assets = (pd.to_numeric(h.loc["Total Asset"], errors="coerce").replace(0, np.nan)
              if "Total Asset" in h.index else None)

    rows: dict[str, pd.Series] = {}
    seen: set[str] = set()
    for name in _CS_PNL:
        if name in h.index and name not in seen:
            rows[name] = pd.to_numeric(h.loc[name], errors="coerce") / sales
            seen.add(name)
    if assets is not None:
        for name in _CS_BS:
            if name in h.index and name not in seen:
                rows[name] = pd.to_numeric(h.loc[name], errors="coerce") / assets
                seen.add(name)
    if not rows:
        return []

    cs = pd.DataFrame(rows).T.reindex(columns=list(h.columns))
    cs = cs.replace([np.inf, -np.inf], np.nan).dropna(axis=0, how="all")
    if cs.empty:
        return []

    model.common_size = cs
    for name in cs.index:
        model.sections.setdefault(name, "COMMON SIZE (DERIVED)")
    return list(cs.index)


def fill_missing_ratios(model: FinancialModel) -> list[str]:
    """
    Add any ratio the workbook did not provide into `model.ratios`.

    Returns the names that had to be derived, so the UI can be honest about
    which numbers came from the file and which the app worked out.
    """
    computed = derived_ratios(model)
    added: list[str] = []

    for name, series in computed.items():
        existing = model.series(name)
        if not existing.dropna().empty:
            continue        # the workbook's own number always wins
        if model.ratios.empty:
            model.ratios = pd.DataFrame(index=[], columns=model.years, dtype="float64")
        model.ratios.loc[name] = series.reindex(model.ratios.columns)
        model.sections.setdefault(name, "DERIVED")
        added.append(name)

    return added
