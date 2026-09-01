"""
sectors.py
----------
A bank of sector "expectations".

The whole point of this project is that a Debt/Equity of 2.0 is alarming for a
software company and completely normal for a bank or an infrastructure
developer. So instead of one universal rule book, every sector carries its own
thresholds and its own weightings.

Each sector profile holds:
  benchmarks : metric -> (weak_below, strong_above)  ... on the metric's own scale
  weights    : how much each analysis pillar matters for this sector
  notes      : plain-English context shown in the UI and given to the LLM
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The five pillars every company is scored on.
PILLARS = ("growth", "profitability", "returns", "leverage", "efficiency")


@dataclass
class SectorProfile:
    name: str
    benchmarks: dict[str, tuple[float, float]]
    weights: dict[str, float]
    notes: str
    peer_context: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Ratios stored as decimals in the workbook (0.18 = 18%) are kept as decimals
# here too, so the comparison is apples to apples.
_GENERIC = {
    "Sales Growth": (0.05, 0.15),
    "Net Profit Growth": (0.05, 0.18),
    "EBITDA Margin": (0.08, 0.18),
    "Net Profit Margin": (0.03, 0.10),
    "Return on Equity (ROE) %": (0.10, 0.18),
    "Return on Capital Employed (ROCE) %": (0.10, 0.18),
    "Return on Assets (ROA) %": (0.03, 0.08),
    "Debt to Equity Ratio": (1.20, 0.50),      # inverted: lower is better
    "Interest Coverage Ratio": (2.50, 6.00),
    "Cash Conversion Cycle": (90.0, 30.0),     # inverted: fewer days is better
    "CFO / PAT": (0.60, 1.00),
    "Fixed Asset Turnover": (1.00, 2.50),
}

_EQUAL_WEIGHTS = {p: 1.0 for p in PILLARS}


def _profile(name, overrides, weights, notes, peer="", aliases=()):
    benchmarks = dict(_GENERIC)
    benchmarks.update(overrides)
    full_weights = dict(_EQUAL_WEIGHTS)
    full_weights.update(weights)
    return SectorProfile(name, benchmarks, full_weights, notes, peer, aliases)


SECTORS: dict[str, SectorProfile] = {
    "generic": _profile(
        "Diversified / Other",
        {},
        {},
        "Balanced scoring with no sector tilt. Use this when the business does "
        "not fit a single bucket, or as a sanity check against a specific sector.",
        aliases=("other", "conglomerate", "diversified"),
    ),
    "it_services": _profile(
        "IT Services & Software",
        {
            "EBITDA Margin": (0.15, 0.25),
            "Net Profit Margin": (0.10, 0.18),
            "Return on Equity (ROE) %": (0.15, 0.25),
            "Return on Capital Employed (ROCE) %": (0.18, 0.30),
            "Debt to Equity Ratio": (0.30, 0.05),
            "Cash Conversion Cycle": (75.0, 45.0),
        },
        {"profitability": 1.3, "returns": 1.5, "leverage": 0.6, "growth": 1.2},
        "Asset-light and cash-generative. Investors expect near-zero debt, ROCE "
        "above 25% and high cash conversion. Debt of any size is a red flag, and "
        "margin compression matters more than a one-off slow growth year.",
        "Peers typically run 20-27% EBITDA margins and D/E under 0.1.",
        aliases=("software", "tech", "it", "saas", "technology"),
    ),
    "fmcg": _profile(
        "FMCG & Consumer Staples",
        {
            "EBITDA Margin": (0.12, 0.20),
            "Net Profit Margin": (0.07, 0.14),
            "Return on Equity (ROE) %": (0.15, 0.30),
            "Return on Capital Employed (ROCE) %": (0.18, 0.35),
            "Debt to Equity Ratio": (0.60, 0.20),
            "Cash Conversion Cycle": (60.0, 15.0),
            "Sales Growth": (0.04, 0.12),
        },
        {"profitability": 1.4, "returns": 1.4, "efficiency": 1.2, "growth": 0.9},
        "Slow, steady, hugely profitable. Volume growth in high single digits is "
        "healthy; the real quality signal is a very high ROCE on a small capital "
        "base plus a short (often negative) working capital cycle.",
        "Best-in-class names earn 30%+ ROCE and hold negative working capital.",
        aliases=("consumer", "staples", "food", "beverage"),
    ),
    "banking": _profile(
        "Banking & Financial Services",
        {
            "Return on Equity (ROE) %": (0.10, 0.16),
            "Return on Assets (ROA) %": (0.008, 0.015),
            "Debt to Equity Ratio": (12.0, 6.0),
            "Interest Coverage Ratio": (1.1, 1.6),
            "Net Profit Margin": (0.10, 0.22),
        },
        {"returns": 1.8, "leverage": 1.4, "profitability": 1.1, "efficiency": 0.4,
         "growth": 1.0},
        "Leverage IS the business model, so Debt/Equity of 6-10x is normal, not "
        "distress. Judge lenders on ROA (above 1.5% is excellent), ROE, and the "
        "consistency of both. Turnover and working-capital ratios are meaningless here.",
        "A healthy bank shows ROA above 1.2% and ROE in the mid-to-high teens.",
        aliases=("bank", "nbfc", "finance", "financial", "lending", "insurance"),
    ),
    "infrastructure": _profile(
        "Infrastructure, Power & Capital Goods",
        {
            "EBITDA Margin": (0.10, 0.22),
            "Net Profit Margin": (0.03, 0.08),
            "Return on Capital Employed (ROCE) %": (0.09, 0.15),
            "Return on Equity (ROE) %": (0.08, 0.15),
            "Debt to Equity Ratio": (2.00, 1.00),
            "Interest Coverage Ratio": (1.80, 3.50),
            "Cash Conversion Cycle": (120.0, 60.0),
            "CFO / PAT": (0.80, 1.50),
        },
        {"leverage": 1.6, "efficiency": 1.2, "returns": 1.3, "profitability": 0.9,
         "growth": 0.9},
        "Capital-hungry and long-gestation. Debt is expected, so the question is "
        "whether cash flows service it: interest coverage and CFO/PAT matter far "
        "more than headline margins. Long receivable cycles are normal.",
        "Comparable developers run D/E of 1-2x with interest cover above 2.5x.",
        aliases=("infra", "power", "energy", "utilities", "construction",
                 "engineering", "capital goods", "ports", "logistics"),
    ),
    "manufacturing": _profile(
        "Manufacturing & Industrials",
        {
            "EBITDA Margin": (0.09, 0.18),
            "Net Profit Margin": (0.04, 0.10),
            "Return on Capital Employed (ROCE) %": (0.12, 0.20),
            "Debt to Equity Ratio": (1.00, 0.40),
            "Fixed Asset Turnover": (1.50, 3.00),
            "Cash Conversion Cycle": (100.0, 45.0),
        },
        {"efficiency": 1.4, "profitability": 1.1, "leverage": 1.1, "returns": 1.2},
        "Cyclical and asset-heavy. Watch asset turnover and the working capital "
        "cycle: a factory that cannot sweat its assets destroys value even in a "
        "good margin year. Compare across a full cycle, not one year.",
        aliases=("auto", "industrial", "chemicals", "cement", "steel", "metals",
                 "pharma manufacturing"),
    ),
    "pharma": _profile(
        "Pharmaceuticals & Healthcare",
        {
            "EBITDA Margin": (0.15, 0.25),
            "Net Profit Margin": (0.08, 0.16),
            "Return on Capital Employed (ROCE) %": (0.14, 0.22),
            "Debt to Equity Ratio": (0.60, 0.25),
            "Cash Conversion Cycle": (120.0, 70.0),
        },
        {"profitability": 1.3, "returns": 1.2, "growth": 1.1, "leverage": 0.9},
        "R&D-led with long inventory cycles, so a 100+ day cash conversion cycle "
        "is structural rather than a warning. Gross margin durability and a low "
        "debt load are the quality markers.",
        aliases=("healthcare", "hospital", "life sciences", "biotech"),
    ),
    "retail": _profile(
        "Retail & E-commerce",
        {
            "EBITDA Margin": (0.05, 0.12),
            "Net Profit Margin": (0.02, 0.06),
            "Return on Capital Employed (ROCE) %": (0.12, 0.20),
            "Sales Growth": (0.08, 0.20),
            "Cash Conversion Cycle": (45.0, 0.0),
            "Fixed Asset Turnover": (2.00, 4.00),
        },
        {"growth": 1.5, "efficiency": 1.4, "profitability": 0.8, "returns": 1.1},
        "Thin margins by design. The model works on velocity: high store/asset "
        "turnover, fast inventory, and negative working capital funded by "
        "suppliers. Judge growth and throughput before margins.",
        aliases=("ecommerce", "consumer discretionary", "qsr", "apparel"),
    ),
    "realestate": _profile(
        "Real Estate",
        {
            "EBITDA Margin": (0.15, 0.28),
            "Debt to Equity Ratio": (1.50, 0.60),
            "Interest Coverage Ratio": (2.00, 4.00),
            "Cash Conversion Cycle": (200.0, 120.0),
            "CFO / PAT": (0.50, 1.20),
        },
        {"leverage": 1.7, "efficiency": 0.8, "profitability": 1.1, "growth": 1.0,
         "returns": 1.1},
        "Inventory sits on the balance sheet for years, so efficiency ratios look "
        "terrible by construction. Survival is a debt story: net debt, interest "
        "cover and collections discipline decide the verdict.",
        aliases=("property", "realty", "housing"),
    ),
}

# Which pillar each metric feeds into.
METRIC_PILLARS: dict[str, str] = {
    "Sales Growth": "growth",
    "Net Profit Growth": "growth",
    "EBITDA Margin": "profitability",
    "Net Profit Margin": "profitability",
    "Return on Equity (ROE) %": "returns",
    "Return on Capital Employed (ROCE) %": "returns",
    "Return on Assets (ROA) %": "returns",
    "Debt to Equity Ratio": "leverage",
    "Interest Coverage Ratio": "leverage",
    "Cash Conversion Cycle": "efficiency",
    "CFO / PAT": "efficiency",
    "Fixed Asset Turnover": "efficiency",
}

# Metrics where a *lower* number is the better number.
LOWER_IS_BETTER = {"Debt to Equity Ratio", "Cash Conversion Cycle"}

PERCENT_METRICS = {
    "Sales Growth", "Net Profit Growth", "EBITDA Margin", "Net Profit Margin",
    "Return on Equity (ROE) %", "Return on Capital Employed (ROCE) %",
    "Return on Assets (ROA) %",
}


def get_sector(key: str) -> SectorProfile:
    """Look up a sector by key, name or alias. Falls back to 'generic'."""
    if not key:
        return SECTORS["generic"]
    probe = key.strip().lower().replace(" ", "_")
    if probe in SECTORS:
        return SECTORS[probe]
    for profile in SECTORS.values():
        if probe == profile.name.lower() or probe in profile.aliases:
            return profile
        if any(alias in probe for alias in profile.aliases):
            return profile
    return SECTORS["generic"]


def sector_choices() -> list[tuple[str, str]]:
    """(key, display name) pairs for the sidebar dropdown."""
    return [(key, profile.name) for key, profile in SECTORS.items()]



# --------------------------------------------------------------------------
# sector detection
# --------------------------------------------------------------------------
# Words that reliably name a sector inside an Indian listed-company name.
NAME_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("banking", ("bank", "finserv", "financ", "nbfc", "capital first", "housing fin",
                 "insurance", "life ins", "general ins", "credit", "lending", "amc",
                 "mutual fund", "securities")),
    ("it_services", ("infosys", "tcs", "consultancy services", "wipro", "hcl", "tech mahindra",
                     "mindtree", "ltimindtree", "software", "systems", "infotech",
                     "technolog", "digital", "cyient", "persistent", "mphasis", "coforge")),
    ("pharma", ("pharma", "labs", "laboratories", "healthcare", "hospital", "drug",
                "biocon", "cipla", "lupin", "aurobindo", "divis", "torrent pharma",
                "life science", "medic")),
    ("fmcg", ("consumer", "hindustan unilever", "nestle", "britannia", "dabur", "marico",
              "godrej consumer", "colgate", "emami", "tata consumer", "foods", "beverage",
              "dairy", "amul")),
    ("infrastructure", ("infra", "power", "energy", "transmission", "grid", "ports",
                        "logistics", "construction", "engineer", "larsen", "ntpc",
                        "adani", "gail", "oil", "petroleum", "gas", "utilities",
                        "renewab", "solar", "wind")),
    ("realestate", ("realty", "estate", "properties", "developers", "housing dev",
                    "dlf", "oberoi realty", "prestige", "brigade", "sobha")),
    ("retail", ("retail", "trent", "avenue supermart", "dmart", "shoppers", "fashion",
                "apparel", "jubilant food", "westlife", "e-commerce", "mall")),
    ("manufacturing", ("steel", "cement", "motors", "auto", "chemical", "industries",
                       "industrial", "metals", "alumini", "copper", "fertil", "paints",
                       "tyre", "bearing", "forge", "engine", "manufact", "textile",
                       "polymer", "plastic", "glass", "paper")),
]


def _structure_guess(metrics: dict[str, float | None]) -> str:
    """
    Fall back to the shape of the balance sheet when the name says nothing.

    These are deliberately coarse: they only need to beat "always infrastructure",
    and the user can override the answer in one click.
    """
    debt_equity = metrics.get("Debt to Equity Ratio")
    interest_pct = metrics.get("Interest % Sales")
    ebitda_margin = metrics.get("EBITDA Margin")
    fixed_turnover = metrics.get("Fixed Asset Turnover")
    net_margin = metrics.get("Net Profit Margin")

    # Lenders borrow as their raw material: leverage and interest cost are both
    # extreme in a way no operating company matches.
    if debt_equity is not None and debt_equity > 4.5:
        if interest_pct is None or interest_pct > 0.2:
            return "banking"

    # Asset-light, high-margin, barely any debt reads as services.
    if (ebitda_margin is not None and ebitda_margin > 0.18
            and (debt_equity is None or debt_equity < 0.25)
            and (fixed_turnover is None or fixed_turnover > 2.0)):
        return "it_services"

    # Heavy balance sheet, thin margins, meaningful debt reads as capital-intensive.
    if (debt_equity is not None and debt_equity > 0.9
            and (net_margin is None or net_margin < 0.08)):
        return "infrastructure"

    if fixed_turnover is not None and fixed_turnover > 3.0 and (
            net_margin is None or net_margin < 0.06):
        return "retail"

    return "generic"


def detect_sector(company: str, metrics: dict[str, float | None] | None = None
                  ) -> tuple[str, str]:
    """
    Guess a company's sector from its name, falling back to its financials.

    Returns (sector key, how it was decided) so the UI can say why — a guess the
    user cannot see the reasoning for is worse than no guess at all.
    """
    name = (company or "").lower()
    for key, hints in NAME_HINTS:
        for hint in hints:
            if hint in name:
                return key, f"matched “{hint}” in the company name"

    if metrics:
        guess = _structure_guess(metrics)
        if guess != "generic":
            return guess, "inferred from the balance-sheet shape"

    return "generic", "no clear signal — pick the sector yourself"
