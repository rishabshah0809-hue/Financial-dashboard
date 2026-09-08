"""
synonyms.py
-----------
The single, reusable financial line-item normalization layer for FundaCheck.

A workbook may name the same financial concept many ways ("Revenue from
Operations", "Net Sales", "Turnover" all mean Sales). This module maps those to a
small set of CANONICAL_CONCEPTS so parser / statement renderer / derived / ratio /
common-size code can all resolve a concept without scattering synonym lists.

Design rules (financial accuracy over matching every spelling):
  1. exact normalized match (case/space/punctuation/`&`↔and folded away)
  2. registered-synonym match (curated per concept)
  3. a *bounded* fuzzy tier — only whole-token equality, gated by per-concept
     exclusions, never loose substring/edit-distance guessing
  4. otherwise leave UNMAPPED (never guess) — unmapped labels are logged so new
     workbook wording can be added here later.

Nothing here changes a financial VALUE; it only maps a label to a concept.
"""

from __future__ import annotations

import logging
import re

LOGGER = logging.getLogger("fundacheck.synonyms")

# --------------------------------------------------------------------------
# Canonical concepts → their accepted synonyms (first entry is the preferred
# label). Monetary concepts NEVER list a margin/percentage/growth variant.
# --------------------------------------------------------------------------
CANONICAL_CONCEPTS: dict[str, list[str]] = {
    # ---- income statement (monetary) ----
    "sales": [
        "Sales", "Revenue", "Total Revenue", "Net Sales", "Net Revenue",
        "Sales Revenue", "Revenue from Operations", "Revenue from Operations (Net)",
        "Revenue from Operations / Sales", "Operating Revenue", "Operating Revenues",
        "Turnover", "Net Turnover", "Total Sales", "Gross Sales",
        "Sales Revenue from Operations", "Income from Operations",
    ],
    "cogs": [
        "COGS", "Cost of Goods Sold", "Cost of Goods", "Cost of Sales",
        "Cost of Revenue", "Cost of Revenues", "Cost of Products Sold",
        "Cost of Products", "Cost of Materials", "Cost of Materials Consumed",
        "Consumption of Raw Materials", "Direct Cost", "Direct Costs",
        "Material Cost",
    ],
    "gross_profit": [
        "Gross Profit", "Gross Profit / (Loss)", "Gross Profit/(Loss)",
        "Gross Earnings", "Gross Income",
    ],
    "selling_general": [
        "Selling & General Expenses", "Selling and General Expenses",
        "Selling, General & Administrative Expenses", "SG&A", "SGA Expenses",
        "Selling & Administrative Expenses", "Selling and Distribution Expenses",
    ],
    "ebitda": [
        "EBITDA", "Operating EBITDA", "EBITDA Profit",
        "Earnings Before Interest Tax Depreciation and Amortization",
        "Earnings Before Interest, Tax, Depreciation & Amortization",
    ],
    "ebit": [
        "EBIT (OPM)", "EBIT", "Operating Profit", "Operating Profit / (Loss)",
        "Operating Income", "Operating Earnings", "PBIT",
        "Earnings Before Interest and Tax", "Earnings Before Interest & Tax",
        "Profit Before Interest and Tax", "EBIT (Operating Profit)",
    ],
    "depreciation": [
        "Depreciation", "Depreciation Expense", "Depreciation & Amortisation",
        "Depreciation and Amortization", "Depreciation & Amortization",
        "Depreciation / Amortisation", "D&A",
    ],
    "interest": [
        "Interest", "Interest Expense", "Interest Expenses", "Finance Cost",
        "Finance Costs", "Financial Charges", "Interest Charges",
        "Borrowing Costs", "Cost of Borrowings",
    ],
    "other_income": [
        "Other Income", "Other Operating Income", "Other Non-Operating Income",
    ],
    "profit_before_tax": [
        "Earnings Before Tax", "Profit Before Tax", "Profit Before Taxes", "PBT",
        "EBT", "Profit Before Income Tax", "Profit Before Taxation",
    ],
    "tax": [
        "Tax", "Tax Expense", "Income Tax", "Income Tax Expense",
        "Provision for Tax", "Provision for Income Tax", "Current Tax", "Taxation",
    ],
    "net_profit": [
        "Net Profit", "Net Profit / (Loss)", "Net Profit/(Loss)",
        "Profit After Tax", "PAT", "Net Income", "Net Earnings",
        "Profit for the Year", "Profit After Taxation",
        "Net Profit Attributable to Owners",
    ],
    # ---- balance sheet: liabilities & equity ----
    "equity_share_capital": [
        "Equity Share Capital", "Share Capital", "Equity Capital",
        "Paid-up Equity Share Capital", "Paid Up Capital", "Issued Share Capital",
    ],
    "reserves": [
        "Reserves", "Reserves & Surplus", "Reserves and Surplus", "Other Reserves",
        "Other Equity",
    ],
    "borrowings": [
        "Borrowings", "Total Borrowings", "Debt", "Total Debt", "Loans",
        "Loans & Borrowings", "Loans and Borrowings", "Financial Debt",
        "Interest Bearing Debt", "Interest-Bearing Borrowings",
    ],
    "other_liabilities": [
        "Other Liabilities", "Other Current Liabilities",
        "Other Non-Current Liabilities",
    ],
    "total_liabilities_equity": [
        "Total Liabilities & Equity", "Total Liabilities and Equity",
        "Total Equity & Liabilities", "Total Equity and Liabilities",
        "Total Liabilities",
    ],
    # ---- balance sheet: assets ----
    "net_block": [
        "Net Block", "Net Fixed Assets", "Fixed Assets",
        "Property Plant & Equipment", "Property, Plant and Equipment", "PPE",
        "Tangible Assets", "Net Property Plant & Equipment",
    ],
    "cwip": [
        "Capital Work in Progress", "Capital Work-in-Progress", "Capital WIP",
        "CWIP",
    ],
    "investments": [
        "Investments", "Total Investments", "Investment",
        "Non-current Investments", "Current Investments", "Marketable Securities",
    ],
    "other_assets": [
        "Other Assets", "Other Current Assets", "Other Non-Current Assets",
    ],
    "total_non_current_assets": [
        "Total Non Current Asset", "Total Non-Current Assets",
        "Total Non Current Assets",
    ],
    "receivables": [
        "Receivables", "Trade Receivables", "Accounts Receivable",
        "Sundry Debtors", "Debtors", "Trade Debtors", "Bills Receivable",
    ],
    "inventory": [
        "Inventory", "Inventories", "Stock", "Stocks",
        "Inventory and Work in Progress", "Inventories and Work in Progress",
    ],
    "cash": [
        "Cash & Bank", "Cash and Bank", "Cash & Cash Equivalents",
        "Cash and Cash Equivalents", "Cash & Bank Balances",
        "Cash and Bank Balances", "Cash & Equivalents", "Bank Balances",
    ],
    "total_current_assets": [
        "Total Current Asset", "Total Current Assets",
    ],
    "total_assets": [
        "Total Assets", "Total Asset",
    ],
}

# Per-concept DISqualifiers: if a candidate label normalizes to include any of
# these whole tokens, it is never mapped to that concept. This protects monetary
# concepts from margin/percentage/growth lines even in the bounded fuzzy tier.
EXCLUSION_TOKENS: dict[str, tuple[str, ...]] = {
    c: ("margin", "percent", "growth", "yoy", "per", "share", "ratio", "rate")
    for c in ("sales", "cogs", "gross_profit", "ebitda", "ebit", "depreciation",
              "interest", "other_income", "profit_before_tax", "tax", "net_profit",
              "selling_general")
}


def normalize(label) -> str:
    """Fold harmless formatting differences into a comparable key.

    Lower-cases, turns `&`→'and', treats `/ - _` and punctuation as spaces, drops
    a leading '+' (Screener sub-line marker) and collapses whitespace.
    'Net-Sales', 'NET SALES', ' net sales ' → 'net sales'.
    """
    s = str(label).strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[\/\-_]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)       # drop (), %, commas, etc.
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _tokens(norm: str) -> set[str]:
    return set(norm.split())


# Reverse index: normalized synonym -> concept. A normalized string that would
# map to two concepts is AMBIGUOUS and dropped (never guessed); it is logged once.
def _build_index() -> tuple[dict[str, str], set[str]]:
    idx: dict[str, str] = {}
    ambiguous: set[str] = set()
    for concept, syns in CANONICAL_CONCEPTS.items():
        for syn in syns:
            n = normalize(syn)
            if not n:
                continue
            if n in idx and idx[n] != concept:
                ambiguous.add(n)
            else:
                idx[n] = concept
    for n in ambiguous:
        idx.pop(n, None)
        LOGGER.warning("synonym '%s' is ambiguous across concepts — left unmapped", n)
    return idx, ambiguous


_INDEX, _AMBIGUOUS = _build_index()


def _excluded(concept: str, norm: str) -> bool:
    bad = EXCLUSION_TOKENS.get(concept)
    return bool(bad) and bool(_tokens(norm) & set(bad))


def resolve(label) -> str | None:
    """Return the canonical concept for a label, or None when not confident."""
    n = normalize(label)
    if not n:
        return None
    concept = _INDEX.get(n)
    if concept and not _excluded(concept, n):
        return concept
    return None


def find_label(available: list[str], concept: str) -> str | None:
    """Pick the actual workbook label for `concept` from `available` labels.

    Priority: (1) a label matching an earlier synonym wins; (2) exact normalized
    match; (3) a bounded fuzzy pass — the label's tokens exactly equal a synonym's
    tokens (order-free) and are not excluded. No loose/edit-distance matching.
    """
    syns = CANONICAL_CONCEPTS.get(concept)
    if not syns:
        return None
    norm_avail = [(lbl, normalize(lbl)) for lbl in available]

    # (1)+(2) exact normalized match, honouring synonym priority order.
    for syn in syns:
        ns = normalize(syn)
        for lbl, nl in norm_avail:
            if nl == ns and not _excluded(concept, nl):
                return lbl

    # (3) bounded fuzzy: identical token *sets* (handles reordered words only).
    syn_token_sets = [_tokens(normalize(s)) for s in syns]
    for lbl, nl in norm_avail:
        if _excluded(concept, nl):
            continue
        toks = _tokens(nl)
        if any(toks == st for st in syn_token_sets):
            return lbl
    return None


def find_all(available: list[str]) -> dict[str, str]:
    """Map every resolvable concept to its actual label in `available`."""
    return {c: lbl for c in CANONICAL_CONCEPTS
            if (lbl := find_label(available, c)) is not None}


def unmapped(available: list[str]) -> list[str]:
    """Labels that resolve to no concept — for logging so wording can be added."""
    return [lbl for lbl in available if resolve(lbl) is None]


def log_unmapped(available: list[str], context: str = "") -> None:
    miss = unmapped(available)
    if miss:
        LOGGER.debug("unmapped financial labels%s: %s",
                     f" ({context})" if context else "", miss)
