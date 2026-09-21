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

from . import synonyms as SYN
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


def _concept(model: FinancialModel, concept: str) -> pd.Series:
    """The statement row for a canonical concept, resolved via core.synonyms.

    Every alias lives in ONE place (core/synonyms.py); this just asks it which
    of the workbook's own labels means `concept`, and never guesses when the
    answer is not confident.
    """
    available: list[str] = []
    for frame in (model.historical, model.ratios):
        if not frame.empty:
            available.extend(str(i) for i in frame.index)
    label = SYN.find_label(available, concept)
    return _row(model, label) if label else pd.Series(dtype="float64")


# Every ratio FundaCheck derives, with the exact formula used and the statement
# inputs it needs. Nothing is computed off a definition not written down here;
# `scripts/export_reference.py` publishes this table so the formulas the app
# uses and the formulas on paper can never drift apart.
RATIO_FORMULAS: dict[str, str] = {
    "Sales Growth": "(Sales − prior Sales) ÷ |prior Sales|",
    "EBITDA Growth": "(EBITDA − prior EBITDA) ÷ |prior EBITDA|",
    "EBIT Growth": "(EBIT − prior EBIT) ÷ |prior EBIT|",
    "Net Profit Growth": "(Net Profit − prior Net Profit) ÷ |prior Net Profit|",
    "EPS Growth": "(EPS − prior EPS) ÷ |prior EPS|",
    "Gross Margin": "Gross Profit ÷ Sales",
    "EBITDA Margin": "EBITDA ÷ Sales",
    "EBIT Margin": "EBIT ÷ Sales",
    "EBT Margin": "Earnings Before Tax ÷ Sales",
    "Net Profit Margin": "Net Profit ÷ Sales",
    "Selling & General Expenses % Sales": "Selling & General Expenses ÷ Sales",
    "Depreciation % Sales": "Depreciation ÷ Sales",
    "Interest % Sales": "Interest ÷ Sales",
    "Tax Payout %": "Tax ÷ Earnings Before Tax",
    "Dividend Payout %": "Dividend Amount ÷ Net Profit",
    "Return on Equity (ROE) %": "Net Profit ÷ (Equity Share Capital + Reserves)",
    "Return on Capital Employed (ROCE) %":
        "EBIT ÷ (Equity Share Capital + Reserves + Borrowings)",
    "Return on Assets (ROA) %": "Net Profit ÷ Total Assets",
    "Return on Invested Capital (ROIC) %":
        "EBIT × (1 − effective tax rate) ÷ (Equity + Borrowings)",
    "Debtor Turnover Ratio": "Sales ÷ Receivables",
    "Creditor Turnover Ratio": "COGS ÷ Trade Payables",
    "Inventory Turnover": "COGS ÷ Inventory",
    "Fixed Asset Turnover": "Sales ÷ Net Block",
    "Capital Turnover Ratio": "Sales ÷ (Equity + Borrowings)",
    "Debtor Days": "Receivables ÷ Sales × 365",
    "Inventory Days": "Inventory ÷ COGS × 365",
    "Payable Days": "Trade Payables ÷ COGS × 365",
    "Cash Conversion Cycle": "Debtor Days + Inventory Days − Payable Days",
    "Debt to Equity Ratio": "Borrowings ÷ (Equity Share Capital + Reserves)",
    "Debt to Asset Ratio": "Borrowings ÷ Total Assets",
    "Interest Coverage Ratio": "EBIT ÷ Interest",
    "CFO / Sales": "Cash from Operating Activity ÷ Sales",
    "CFO / PAT": "Cash from Operating Activity ÷ Net Profit",
    "CFO / Total Assets": "Cash from Operating Activity ÷ Total Assets",
    "CFO / Total Debt": "Cash from Operating Activity ÷ Borrowings",
    "PE Ratio": "Share price ÷ EPS (per-year price where the workbook has one, "
                "else today's price)",
    "Price to Sales": "Market capitalisation ÷ Sales",
}

# The statement inputs each ratio needs, in plain English. When one is missing
# the ratio is NOT computed — it is reported as unavailable with this reason.
RATIO_INPUTS: dict[str, tuple[str, ...]] = {
    "Creditor Turnover Ratio": ("COGS", "Trade Payables"),
    "Payable Days": ("COGS", "Trade Payables"),
    "Cash Conversion Cycle": ("Receivables", "Inventory", "Trade Payables",
                              "Sales", "COGS"),
    "Return on Capital Employed (ROCE) %": ("EBIT", "Equity", "Borrowings"),
    "Return on Invested Capital (ROIC) %": ("EBIT", "Tax rate", "Equity"),
    "PE Ratio": ("Share price", "EPS"),
    "Price to Sales": ("Market capitalisation", "Sales"),
}


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
    """Every benchmark ratio this module knows how to build from the statements.

    Label→concept resolution goes through core/synonyms.py, so a workbook that
    spells a line differently still computes. A ratio whose inputs are missing
    is simply absent from the result — never estimated, never proxied.
    """
    sales = _concept(model, "sales")
    cogs = _concept(model, "cogs")
    ebitda = _concept(model, "ebitda")
    ebit = _concept(model, "ebit")
    profit = _concept(model, "net_profit")
    interest = _concept(model, "interest")
    borrowings = _concept(model, "borrowings")
    capital = _concept(model, "equity_share_capital")
    reserves = _concept(model, "reserves")
    assets = _concept(model, "total_assets")
    net_block = _concept(model, "net_block")
    receivables = _concept(model, "receivables")
    inventory = _concept(model, "inventory")
    cfo = _concept(model, "cfo")
    eps = _concept(model, "eps")
    gross = _row(model, "Gross Profit")
    sga = _concept(model, "selling_general")
    depreciation = _concept(model, "depreciation")
    ebt = _concept(model, "profit_before_tax")
    tax = _concept(model, "tax")
    dividend = _concept(model, "dividend_amount")
    price = _concept(model, "price")

    equity = capital.add(reserves, fill_value=0.0) if not reserves.empty else capital
    # Capital employed = what the business is funded with, debt included.
    capital_employed = equity.add(borrowings, fill_value=0.0) if not borrowings.empty else equity
    # TRADE PAYABLES ONLY. "Other Liabilities" bundles provisions, customer
    # advances and statutory dues alongside trade credit, so it is NOT a
    # payables proxy: when a workbook has no real payables line, Creditor
    # Turnover / Payable Days / Cash Conversion Cycle stay unavailable.
    payables = _concept(model, "payables")

    out: dict[str, pd.Series] = {
        "Sales Growth": _growth(sales),
        "Net Profit Growth": _growth(profit),
        "EBITDA Growth": _growth(ebitda),
        "EBIT Growth": _growth(ebit),
        "EPS Growth": _growth(eps),
        # Profitability & margins
        "Gross Margin": _safe_div(gross, sales),
        "EBITDA Margin": _safe_div(ebitda, sales),
        "EBIT Margin": _safe_div(ebit, sales),
        "EBT Margin": _safe_div(ebt, sales),
        "Net Profit Margin": _safe_div(profit, sales),
        # Cost & payout (% of sales)
        "Selling & General Expenses % Sales": _safe_div(sga, sales),
        "Depreciation % Sales": _safe_div(depreciation, sales),
        "Interest % Sales": _safe_div(interest, sales),
        "Tax Payout %": _safe_div(tax, ebt),
        "Dividend Payout %": _safe_div(dividend, profit),
        # Return ratios
        "Return on Equity (ROE) %": _safe_div(profit, equity),
        "Return on Capital Employed (ROCE) %": _safe_div(ebit, capital_employed),
        "Return on Assets (ROA) %": _safe_div(profit, assets),
        # Turnover & efficiency
        "Debtor Turnover Ratio": _safe_div(sales, receivables),
        "Creditor Turnover Ratio": _safe_div(cogs, payables),
        # Inventory turns against the COST of what was sold, not against sales —
        # the same definition the FundaCheck workbook uses, and the one that
        # keeps Inventory Days == 365 / Inventory Turnover.
        "Inventory Turnover": _safe_div(cogs, inventory),
        "Fixed Asset Turnover": _safe_div(sales, net_block),
        "Capital Turnover Ratio": _safe_div(sales, capital_employed),
        "Debtor Days": _safe_div(receivables, sales) * 365,
        "Inventory Days": _safe_div(inventory, cogs) * 365,
        "Payable Days": _safe_div(payables, cogs) * 365,
        # Solvency & leverage
        "Debt to Equity Ratio": _safe_div(borrowings, equity),
        "Debt to Asset Ratio": _safe_div(borrowings, assets),
        "Interest Coverage Ratio": _safe_div(ebit, interest),
        # Cash flow
        "CFO / Sales": _safe_div(cfo, sales),
        "CFO / PAT": _safe_div(cfo, profit),
        "CFO / Total Assets": _safe_div(cfo, assets),
        "CFO / Total Debt": _safe_div(cfo, borrowings),
    }

    # Return on invested capital: after-tax operating profit over the capital
    # actually invested in the business.
    tax_rate = _safe_div(tax, ebt)
    if not ebit.empty and not tax_rate.empty and not capital_employed.empty:
        nopat = (ebit * (1 - tax_rate)).dropna()
        out["Return on Invested Capital (ROIC) %"] = _safe_div(nopat, capital_employed)

    # Cash conversion cycle needs ALL THREE legs. Without a real payables line
    # the cycle is undefined — treating a missing payable leg as zero would
    # silently overstate the cycle, so it is left out instead.
    if not payables.empty:
        cycle = pd.DataFrame({
            "debtor": out["Debtor Days"],
            "inventory": out["Inventory Days"],
            "payable": out["Payable Days"],
        }).dropna(how="any")
        if not cycle.empty:
            out["Cash Conversion Cycle"] = (
                cycle["debtor"] + cycle["inventory"] - cycle["payable"]
            )

    if not eps.empty:
        clean_eps = eps.replace(0, np.nan)
        if not price.empty:
            # The workbook carries the year-end share price for each period —
            # that is the honest historical P/E (each year priced at its own
            # year-end), and it is what the FundaCheck template computes.
            out["PE Ratio"] = _safe_div(price, clean_eps)
        elif model.meta.get("current_price"):
            # No per-year price: fall back to today's price against each
            # historical EPS — the standard trailing view.
            out["PE Ratio"] = (
                model.meta["current_price"] / clean_eps
            ).dropna()

    shares = _concept(model, "adjusted_share_count")
    if shares.empty:
        shares = _concept(model, "share_count")
    if not sales.empty:
        if not price.empty and not shares.empty:
            # Year-end market capitalisation over that year's sales.
            out["Price to Sales"] = _safe_div(
                (price * shares).dropna(), sales)
        elif model.meta.get("market_cap"):
            out["Price to Sales"] = (
                model.meta["market_cap"] / sales.replace(0, np.nan)
            ).dropna()

    return {name: series for name, series in out.items() if not series.empty}


# Statement lines FundaCheck will build when the workbook does not report them,
# with the formula used. A line the workbook DOES report is always kept as-is.
STATEMENT_FORMULAS: dict[str, str] = {
    "Gross Profit": "Sales − COGS",
    "EBITDA": "Gross Profit − Selling & General Expenses",
    "EBIT (OPM)": "EBITDA − Depreciation",
    "Earnings Before Tax": "EBIT + Other Income − Interest",
    "Net Profit": "Earnings Before Tax − Tax",
    "Equity": "Equity Share Capital + Reserves",
    "Capital Employed": "Equity + Borrowings",
    "Total Asset": "Equity + Borrowings + Other Liabilities + Trade Payables",
}


def fill_missing_statement_lines(model: FinancialModel) -> list[str]:
    """Build the standard aggregate statement lines the workbook did not report.

    Only ever ADDS a line: when the workbook reports Gross Profit or EBITDA
    itself, that reported figure is kept — the source always wins. Each added
    line is tagged DERIVED with the formula above, so nothing computed here is
    ever presented as something the workbook supplied.
    """
    if model.historical.empty:
        return []
    added: list[str] = []

    def _has(label: str) -> bool:
        return (label in model.historical.index
                and not pd.to_numeric(model.historical.loc[label],
                                      errors="coerce").dropna().empty)

    def _add(label: str, series: pd.Series) -> None:
        series = series.replace([np.inf, -np.inf], np.nan).dropna()
        if series.empty:
            return
        model.historical.loc[label] = series.reindex(model.historical.columns)
        model.sections.setdefault(label, "DERIVED")
        model.mark_derived(label, STATEMENT_FORMULAS[label])
        added.append(label)

    sales = _concept(model, "sales")
    cogs = _concept(model, "cogs")
    sga = _concept(model, "selling_general")
    depreciation = _concept(model, "depreciation")
    other_income = _concept(model, "other_income")
    interest = _concept(model, "interest")
    tax = _concept(model, "tax")
    capital = _concept(model, "equity_share_capital")
    reserves = _concept(model, "reserves")
    borrowings = _concept(model, "borrowings")
    other_liabilities = _concept(model, "other_liabilities")
    payables = _concept(model, "payables")

    if not _has("Gross Profit") and not sales.empty and not cogs.empty:
        _add("Gross Profit", sales.sub(cogs, fill_value=0.0).dropna())

    gross = _row(model, "Gross Profit")
    if not _has("EBITDA") and not gross.empty:
        _add("EBITDA", gross.sub(sga, fill_value=0.0) if not sga.empty else gross)

    ebitda = _row(model, "EBITDA")
    if not _concept(model, "ebit").any() and not ebitda.empty and not depreciation.empty:
        _add("EBIT (OPM)", ebitda.sub(depreciation, fill_value=0.0).dropna())

    ebit = _concept(model, "ebit")
    if not _has("Earnings Before Tax") and not ebit.empty and not interest.empty:
        ebt = ebit.add(other_income, fill_value=0.0) if not other_income.empty else ebit
        _add("Earnings Before Tax", ebt.sub(interest, fill_value=0.0).dropna())

    ebt_row = _concept(model, "profit_before_tax")
    if not _has("Net Profit") and not ebt_row.empty and not tax.empty:
        _add("Net Profit", ebt_row.sub(tax, fill_value=0.0).dropna())

    equity = capital.add(reserves, fill_value=0.0) if not reserves.empty else capital
    if not _has("Equity") and not equity.empty:
        _add("Equity", equity)
    if not _has("Capital Employed") and not equity.empty and not borrowings.empty:
        _add("Capital Employed", equity.add(borrowings, fill_value=0.0))

    if not _concept(model, "total_assets").any() and not equity.empty:
        total = equity
        for part in (borrowings, other_liabilities, payables):
            if not part.empty:
                total = total.add(part, fill_value=0.0)
        if not total.equals(equity):      # only when the liability side is known
            _add("Total Asset", total)

    return added


def missing_ratio_reasons(model: FinancialModel) -> dict[str, str]:
    """Ratios FundaCheck could NOT compute, and the input that was missing.

    Part of the never-fabricate rule: the UI shows "—" plus this reason rather
    than a proxied or guessed number.
    """
    computed = derived_ratios(model)
    reasons: dict[str, str] = {}
    for name in RATIO_FORMULAS:
        if name in computed or not model.series(name).dropna().empty:
            continue
        inputs = RATIO_INPUTS.get(name)
        reasons[name] = (
            f"required input unavailable ({', '.join(inputs)})" if inputs
            else "required input unavailable"
        )
    return reasons


# Common-size lines and the base each is expressed against. Income-statement
# lines are shown as a % of Sales; balance-sheet lines as a % of Total Assets —
# the standard common-size convention (and what the Excel sheet does).
_CS_PNL = ("Sales", "COGS", "Gross Profit", "Selling & General Expenses",
           "EBITDA", "Depreciation", "EBIT (OPM)", "Other Income ", "Other Income",
           "Interest", "Earnings Before Tax", "Tax", "Net Profit")
_CS_BS = ("Equity Share Capital", "Reserves", "Borrowings", "Other Liabilities",
          "Trade Payables", "Net Block", "Capital Work in Progress",
          "Investments", "Receivables", "Inventory", "Cash & Bank")


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
    labels = [str(i) for i in h.index]
    sales_label = SYN.find_label(labels, "sales")
    if not sales_label:
        return []
    assets_label = SYN.find_label(labels, "total_assets")

    sales = pd.to_numeric(h.loc[sales_label], errors="coerce").replace(0, np.nan)
    assets = (pd.to_numeric(h.loc[assets_label], errors="coerce").replace(0, np.nan)
              if assets_label else None)

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
    pnl = set(_CS_PNL)
    for name in cs.index:
        model.sections.setdefault(name, "COMMON SIZE (DERIVED)")
        base = "Sales" if name in pnl else "Total Assets"
        model.provenance[f"Common Size · {name}"] = {
            "kind": "derived", "where": "FundaCheck",
            "formula": f"{name} ÷ {base}",
        }
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
        model.mark_derived(name, RATIO_FORMULAS.get(name, ""))
        added.append(name)

    return added
