"""
export_reference.py
-------------------
Publish FundaCheck's ingestion rule book as an Excel workbook:

  * every line-item synonym the parser understands,
  * every formula FundaCheck uses to derive a statement line or a ratio,
  * the Screener Data-Sheet mapping,
  * how a workbook's format is detected,
  * and what happens when an input is missing.

The workbook is GENERATED FROM THE CODE (core/synonyms.py, core/parser.py,
core/derive.py, core/sectors.py), so the documented formulas and the formulas
the app actually runs can never drift apart. Re-run it after changing any of
those modules.

    python scripts/export_reference.py [output.xlsx]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import derive, parser, sectors  # noqa: E402
from core import synonyms as SYN  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "docs" / "fundacheck_reference.xlsx"

# Concepts that must never be confused with one another, and why.
NEVER_CONFUSE = [
    ("payables", "other_liabilities",
     "Other Liabilities bundles provisions, customer advances and statutory "
     "dues. It is NOT trade credit, so it is never read as Trade Payables — "
     "without a real payables line, Creditor Turnover / Payable Days / Cash "
     "Conversion Cycle stay unavailable."),
    ("receivables", "other_assets",
     "Other Assets is a residual bucket, not amounts owed by customers."),
    ("cogs", "selling_general",
     "Other Expenses are period costs, not the cost of what was sold; they are "
     "grouped into Selling & General Expenses, never into COGS."),
    ("ebit", "ebitda",
     "EBIT is after depreciation, EBITDA before it — a workbook's "
     "'Operating Profit' is mapped to EBIT only when it is stated as such."),
]


def _readme() -> pd.DataFrame:
    return pd.DataFrame({
        "FundaCheck ingestion reference": [
            "This workbook is generated from the FundaCheck source code by "
            "scripts/export_reference.py.",
            "",
            "Sheet guide",
            "  Line item synonyms   — every wording the parser accepts for each "
            "financial concept.",
            "  Never confuse        — pairs of lines that look similar but are "
            "not economically the same.",
            "  Derived statement lines — totals FundaCheck builds when the "
            "workbook does not report them.",
            "  Ratio formulas       — the exact formula behind every ratio, plus "
            "the inputs it needs.",
            "  Data Sheet mapping   — Screener 'Data Sheet' row → FundaCheck "
            "statement line.",
            "  Workbook formats     — the four workbook shapes that are accepted.",
            "  Missing data rules   — what FundaCheck shows when an input is absent.",
            "",
            "Two rules govern everything here:",
            "  1. A value the workbook reports is always used as reported "
            "(SOURCE). FundaCheck only computes what is missing (DERIVED).",
            "  2. A ratio whose inputs are unavailable is shown as '—' with the "
            "reason. Nothing is ever proxied, estimated or filled in.",
        ]
    })


def _synonyms_sheet() -> pd.DataFrame:
    rows = []
    for concept, names in SYN.CANONICAL_CONCEPTS.items():
        rows.append({
            "Concept": concept,
            "FundaCheck label": SYN.PREFERRED_LABEL[concept],
            "Accepted wordings": " | ".join(names),
            "Count": len(names),
            "Never matched when the label contains":
                ", ".join(SYN.EXCLUSION_TOKENS.get(concept, ())) or "—",
        })
    return pd.DataFrame(rows)


def _never_confuse_sheet() -> pd.DataFrame:
    return pd.DataFrame(
        [{"Concept": a, "Must not be taken from": b, "Why": why}
         for a, b, why in NEVER_CONFUSE])


def _statement_lines_sheet() -> pd.DataFrame:
    rows = [{"Statement line": line, "Formula used when not reported": formula,
             "Source wins?": "yes — a reported figure is never overwritten"}
            for line, formula in derive.STATEMENT_FORMULAS.items()]
    rows += [{"Statement line": line,
              "Formula used when not reported": formula,
              "Source wins?": "rebuilt from the Screener Data Sheet"}
             for line, formula in parser.REBUILD_DERIVED_LINES.items()
             if line not in derive.STATEMENT_FORMULAS]
    return pd.DataFrame(rows)


def _ratio_sheet() -> pd.DataFrame:
    rows = []
    for name, formula in derive.RATIO_FORMULAS.items():
        unit = ("percent / fraction" if name in sectors.PERCENT_METRICS
                or "%" in name or "Margin" in name or "Growth" in name
                else "days" if "Days" in name or "Cycle" in name
                else "times (x)")
        rows.append({
            "Ratio": name,
            "Formula": formula,
            "Inputs required": ", ".join(derive.RATIO_INPUTS.get(name, ()))
                               or "the lines named in the formula",
            "Unit": unit,
            "Better when": ("lower" if name in sectors.LOWER_IS_BETTER
                            else "higher"),
            "Scoring pillar": sectors.METRIC_PILLARS.get(name, "not scored"),
            "If an input is missing": "shown as '—' with the reason; never estimated",
        })
    return pd.DataFrame(rows)


def _data_sheet_mapping() -> pd.DataFrame:
    rows = [{"Screener Data Sheet row (normalised)": key,
             "FundaCheck line": label,
             "How": "fixed Screener label"}
            for key, label in parser.DATA_SHEET_ALIASES.items()]
    rows += [{"Screener Data Sheet row (normalised)": f"(any wording for {concept})",
              "FundaCheck line": label,
              "How": "resolved through the synonym layer"}
             for concept, label in parser.DATA_SHEET_CONCEPTS.items()]
    rows.append({"Screener Data Sheet row (normalised)":
                 ", ".join(parser.OPERATING_COST_KEYS),
                 "FundaCheck line": "COGS / Selling & General Expenses",
                 "How": "operating-cost lines, grouped (Change in Inventory is "
                        "subtracted as a contra-expense)"})
    return pd.DataFrame(rows)


def _formats_sheet() -> pd.DataFrame:
    return pd.DataFrame([
        {"Format key": parser.FORMAT_HISTORICAL,
         "What it is": parser.FORMAT_LABELS[parser.FORMAT_HISTORICAL],
         "Sheet names accepted": ", ".join(parser.SHEET_ALIASES["historical"]),
         "Accepted when": f"the sheet holds a row of reporting dates and at "
                          f"least {parser.MIN_STATEMENT_CONCEPTS} recognised "
                          f"financial lines"},
        {"Format key": parser.FORMAT_STATEMENTS,
         "What it is": parser.FORMAT_LABELS[parser.FORMAT_STATEMENTS],
         "Sheet names accepted": ", ".join(parser.STATEMENT_SHEET_ALIASES),
         "Accepted when": "separate statement tabs parse and are stacked into "
                          "one annual frame"},
        {"Format key": parser.FORMAT_DATA_SHEET,
         "What it is": parser.FORMAT_LABELS[parser.FORMAT_DATA_SHEET],
         "Sheet names accepted": ", ".join(parser.SHEET_ALIASES["data"]),
         "Accepted when": "the statement tabs are formulas with no cached "
                          "values, so the statements are rebuilt from the raw "
                          "Data Sheet"},
        {"Format key": parser.FORMAT_UNSUPPORTED,
         "What it is": parser.FORMAT_LABELS[parser.FORMAT_UNSUPPORTED],
         "Sheet names accepted": "—",
         "Accepted when": "never — the upload is rejected with the sheets it "
                          "found and what it expected"},
    ])


def _missing_rules() -> pd.DataFrame:
    return pd.DataFrame([
        {"Situation": "No Trade Payables line",
         "Affected": "Creditor Turnover Ratio, Payable Days, Cash Conversion Cycle",
         "What FundaCheck shows": "— (required input unavailable: Trade Payables)"},
        {"Situation": "No EBIT (and no inputs to build it)",
         "Affected": "ROCE, ROIC, EBIT Margin, Interest Coverage",
         "What FundaCheck shows": "— (required input unavailable: EBIT)"},
        {"Situation": "No EPS and no share count",
         "Affected": "PE Ratio, EPS Growth",
         "What FundaCheck shows": "— (required input unavailable: EPS)"},
        {"Situation": "No borrowings line",
         "Affected": "Debt/Equity, Debt/Assets, CFO/Total Debt",
         "What FundaCheck shows": "— (required input unavailable)"},
        {"Situation": "Zero or missing denominator in any year",
         "Affected": "that year only",
         "What FundaCheck shows": "the year is dropped; no infinity, no zero "
                                  "stand-in"},
        {"Situation": "Quarterly block / TTM column / projected (FY27E) column",
         "Affected": "all annual ratios",
         "What FundaCheck shows": "excluded from the annual model entirely"},
    ])


def main(out: Path = DEFAULT_OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    sheets = {
        "Read me": _readme(),
        "Line item synonyms": _synonyms_sheet(),
        "Never confuse": _never_confuse_sheet(),
        "Derived statement lines": _statement_lines_sheet(),
        "Ratio formulas": _ratio_sheet(),
        "Data Sheet mapping": _data_sheet_mapping(),
        "Workbook formats": _formats_sheet(),
        "Missing data rules": _missing_rules(),
    }
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
            worksheet = writer.sheets[name[:31]]
            for column_cells in worksheet.columns:
                widest = max(len(str(c.value or "")) for c in column_cells)
                letter = column_cells[0].column_letter
                worksheet.column_dimensions[letter].width = min(max(widest + 2, 14), 90)
            wrap = Alignment(wrap_text=True, vertical="top")
            for row in worksheet.iter_rows():
                for cell in row:
                    cell.alignment = wrap
            worksheet.freeze_panes = "A2"
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    print(f"written: {main(target)}")
