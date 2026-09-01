"""
Locks the IndianAPI field mapping and the pooled-metric engine against the two
real responses that were inspected (HDFC Bank = a lender, TCS = a non-financial).
These fixtures are genuine IndianAPI dumps, so the expected numbers here are the
real ones — if a field name or a formula drifts, these tests fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.indianapi import parse_company, pooled_metrics, top_metrics

ROOT = Path(__file__).resolve().parent.parent
HDFC = ROOT / "api" / "indianapi_hdfc_raw.json"
TCS = ROOT / "api" / "indianapi_tcs_raw.json"

pytestmark = pytest.mark.skipif(
    not (HDFC.exists() and TCS.exists()),
    reason="raw IndianAPI fixtures not present")


def _load(p):
    return parse_company(json.loads(p.read_text(encoding="utf-8")))


def test_bank_fields():
    c = _load(HDFC)
    assert c.name == "HDFC Bank"
    assert c.nse_symbol == "HDFCBANK"
    assert c.fiscal_year == "2026"
    assert c.market_cap == pytest.approx(1109600.71)
    assert c.net_income == pytest.approx(76025.97)
    assert c.total_equity == pytest.approx(586059.47)
    assert c.total_assets == pytest.approx(4908040.84)
    assert c.total_debt == pytest.approx(603058.42)
    # a bank has no standard OperatingIncome/Revenue line
    assert c.operating_income is None
    assert c.revenue is None


def test_nonfinancial_fields():
    c = _load(TCS)
    assert c.nse_symbol == "TCS"
    assert c.operating_income == pytest.approx(62632.0)   # EBIT, direct
    assert c.depreciation == pytest.approx(5560.0)
    assert c.revenue == pytest.approx(267021.0)
    assert c.prev_revenue == pytest.approx(255324.0)
    assert c.eps == pytest.approx(136.01)
    assert c.prev_eps == pytest.approx(134.20)


def test_pooled_banking():
    m = pooled_metrics([_load(HDFC)], is_financial=True)
    assert m["pe"] == pytest.approx(14.60, abs=0.05)
    assert m["pb"] == pytest.approx(1.89, abs=0.05)
    assert m["roe"] == pytest.approx(12.97, abs=0.05)
    assert m["roa"] == pytest.approx(1.55, abs=0.05)
    assert m["roce"] is None                    # lenders → "—"
    assert m["roce_note"]


def test_pooled_it():
    m = pooled_metrics([_load(TCS)], is_financial=False)
    assert m["pe"] == pytest.approx(17.38, abs=0.05)
    assert m["pb"] == pytest.approx(7.98, abs=0.05)
    assert m["roe"] == pytest.approx(45.89, abs=0.05)
    assert m["roa"] == pytest.approx(26.98, abs=0.05)
    assert m["roce"] == pytest.approx(52.79, abs=0.1)   # OperatingIncome/(Eq+Debt)


def test_top_metrics_unavailable_is_none():
    bank = top_metrics(_load(HDFC), is_financial=True)
    assert bank["roce"] is None                 # not computed for lenders
    assert bank["asset_turnover"] is None       # genuinely None in source
    assert bank["nse_url"].endswith("symbol=HDFCBANK")
    it = top_metrics(_load(TCS), is_financial=False)
    assert it["revenue_growth_yoy"] == pytest.approx(4.58, abs=0.05)
    assert it["interest_coverage"] is None      # debt-light → source None
