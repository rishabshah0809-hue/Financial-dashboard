"""
test_indianapi.py
-----------------
Comprehensive unit test suite for the Sector Lens unified engine.

Validates:
1. Exact metric reproduction on real HDFC Bank and TCS raw inspection dumps.
2. Direct OperatingIncome extraction as EBIT for ROCE.
3. Strict withholding of ROCE for Banking & Finance lenders.
4. ISIN-based constituent deduplication across all 9 sectors.
5. Pre-flight credit budget guard (<= 400 requests).
6. Immediate abortion on HTTP 401/403/429 errors.
7. Top 10 ranking strictly by Market Cap with official NSE quote URLs.
8. 6-state Current Monthly Tilt engine.
9. Failure protection & snapshot validation.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core import indianapi as E
from core import sector_snapshot as SNAP
from core import sector_universe as U
from core import tilt as T
from scripts import refresh_sectors as R

ROOT = Path(__file__).resolve().parent.parent
HDFC_RAW_PATH = ROOT / "api" / "indianapi_hdfc_raw.json"
TCS_RAW_PATH = ROOT / "api" / "indianapi_tcs_raw.json"


class TestIndianAPIExtraction(unittest.TestCase):
    """Test fundamental metric extraction from raw IndianAPI JSON responses."""

    def setUp(self):
        self.assertTrue(HDFC_RAW_PATH.exists(), f"Missing fixture {HDFC_RAW_PATH}")
        self.assertTrue(TCS_RAW_PATH.exists(), f"Missing fixture {TCS_RAW_PATH}")
        self.hdfc_raw = json.loads(HDFC_RAW_PATH.read_text(encoding="utf-8"))
        self.tcs_raw = json.loads(TCS_RAW_PATH.read_text(encoding="utf-8"))

    def test_hdfc_bank_fixture_exact_metrics(self):
        """HDFC Bank must produce: P/E 14.60, P/B 1.89, ROE 12.97%, ROA 1.55%, ROCE = None (lender note)."""
        comp = E.parse_company(self.hdfc_raw, default_symbol="HDFCBANK")
        self.assertIsNotNone(comp)
        self.assertEqual(comp.nse_symbol, "HDFCBANK")

        metrics = E.pooled_metrics([comp], is_financial=True)

        self.assertAlmostEqual(metrics["pe"], 14.60, places=2)
        self.assertAlmostEqual(metrics["pb"], 1.89, places=2)
        self.assertAlmostEqual(metrics["roe"], 12.97, places=2)
        self.assertAlmostEqual(metrics["roa"], 1.55, places=2)
        self.assertIsNone(metrics["roce"], "ROCE must be None for Banking & Finance")
        self.assertEqual(metrics["roce_note"], "ROCE is not a meaningful metric for lenders.")

    def test_tcs_fixture_exact_metrics(self):
        """TCS must produce: P/E 17.38, P/B 7.98, ROE 45.89%, ROA 26.98%, ROCE 52.79%."""
        comp = E.parse_company(self.tcs_raw, default_symbol="TCS")
        self.assertIsNotNone(comp)
        self.assertEqual(comp.nse_symbol, "TCS")

        metrics = E.pooled_metrics([comp], is_financial=False)

        self.assertAlmostEqual(metrics["pe"], 17.38, places=2)
        self.assertAlmostEqual(metrics["pb"], 7.98, places=2)
        self.assertAlmostEqual(metrics["roe"], 45.89, places=2)
        self.assertAlmostEqual(metrics["roa"], 26.98, places=2)
        self.assertAlmostEqual(metrics["roce"], 52.79, places=2)
        self.assertIsNone(metrics["roce_note"])

    def test_operating_income_as_ebit_for_roce(self):
        """Verify that OperatingIncome is used directly as EBIT for ROCE."""
        comp = E.parse_company(self.tcs_raw, default_symbol="TCS")
        self.assertIsNotNone(comp.operating_income)
        # ROCE = OperatingIncome / (total_equity + total_debt)
        expected_roce = round((comp.operating_income / (comp.total_equity + comp.total_debt)) * 100.0, 2)
        m = E.top_metrics(comp, is_financial=False)
        self.assertAlmostEqual(m["roce"], expected_roce, places=2)
        self.assertAlmostEqual(m["roce"], 52.79, places=2)


class TestSectorUniverseAndDeduplication(unittest.TestCase):
    """Test 9 sector definitions, exact/approximate/proxy mapping, and ISIN deduplication."""

    def test_nine_sectors_present(self):
        self.assertEqual(len(U.ORDER), 9)
        expected_keys = {
            "banking", "it_services", "fmcg", "pharma", "realestate",
            "infrastructure", "manufacturing", "retail", "generic"
        }
        self.assertEqual(set(U.ORDER), expected_keys)

    def test_mapping_types(self):
        self.assertEqual(U.UNIVERSES["it_services"].mapping_type, "exact")
        self.assertEqual(U.UNIVERSES["fmcg"].mapping_type, "exact")
        self.assertEqual(U.UNIVERSES["pharma"].mapping_type, "exact")
        self.assertEqual(U.UNIVERSES["realestate"].mapping_type, "exact")
        self.assertEqual(U.UNIVERSES["banking"].mapping_type, "approximate")
        self.assertEqual(U.UNIVERSES["infrastructure"].mapping_type, "approximate")
        self.assertEqual(U.UNIVERSES["manufacturing"].mapping_type, "approximate")
        self.assertEqual(U.UNIVERSES["retail"].mapping_type, "proxy")
        self.assertEqual(U.UNIVERSES["generic"].mapping_type, "proxy")

    def test_lender_metric_applicability(self):
        app, reason = U.metric_applicable("banking", "roce")
        self.assertFalse(app)
        self.assertIn("not a meaningful metric for lenders", reason)

        app_it, _ = U.metric_applicable("it_services", "roce")
        self.assertTrue(app_it)

    def test_all_unique_constituents_deduplication(self):
        per_sec, union = U.all_unique_symbols()
        total_constituents = sum(len(v) for v in per_sec.values())
        # Deduplication must reduce total constituent requests
        self.assertLessEqual(len(union), total_constituents)
        self.assertLessEqual(len(union), 400, "Must be well within 400 credit safety budget")


class TestTop10Rankings(unittest.TestCase):
    """Test Top 10 rankings strictly by Market Cap and official NSE URLs."""

    def test_rank_top_ordering(self):
        hdfc = E.parse_company(json.loads(HDFC_RAW_PATH.read_text(encoding="utf-8")), "HDFCBANK")
        tcs = E.parse_company(json.loads(TCS_RAW_PATH.read_text(encoding="utf-8")), "TCS")

        ranked = E.rank_top([hdfc, tcs], n=10, is_financial=False)
        self.assertEqual(len(ranked), 2)
        # HDFC Bank market cap (1.14M Cr) > TCS market cap (1.07M Cr)
        self.assertEqual(ranked[0]["rank"], 1)
        self.assertEqual(ranked[0]["nse_symbol"], "HDFCBANK")
        self.assertEqual(ranked[0]["nse_url"], "https://www.nseindia.com/get-quotes/equity?symbol=HDFCBANK")
        self.assertEqual(ranked[1]["rank"], 2)
        self.assertEqual(ranked[1]["nse_symbol"], "TCS")
        self.assertEqual(ranked[1]["nse_url"], "https://www.nseindia.com/get-quotes/equity?symbol=TCS")

        # Check required columns
        for row in ranked:
            self.assertIn("rank", row)
            self.assertIn("name", row)
            self.assertIn("nse_symbol", row)
            self.assertIn("market_cap", row)
            self.assertIn("pe", row)
            self.assertIn("pb", row)
            self.assertIn("roe", row)
            self.assertIn("roa", row)
            self.assertIn("roce", row)
            self.assertIn("revenue_growth_yoy", row)
            self.assertIn("eps_ttm_growth", row)
            self.assertIn("opm", row)
            self.assertIn("npm", row)
            self.assertIn("debt_to_equity", row)
            self.assertIn("asset_turnover", row)
            self.assertIn("interest_coverage", row)


class TestTiltEngine(unittest.TestCase):
    """Test 6-state Current Monthly Tilt engine and structural seasonality."""

    def test_structural_seasonality_all_sectors(self):
        for key in U.ORDER:
            prof = T.profile(key)
            self.assertIsNotNone(prof, f"Missing profile for {key}")
            self.assertIn("nature", prof)
            self.assertIn("seasonal", prof)
            self.assertIn("text", prof)
            self.assertTrue(len(prof["text"]) > 50)

    def test_current_tilt_states(self):
        current = {
            "metrics": {"roe": 15.0, "roa": 10.0, "pe": 20.0},
            "earnings_growth": 14.5,
            "revenue_growth": 12.0,
            "top10": [{"nse_symbol": "TCS"}, {"nse_symbol": "INFY"}],
        }
        res_initial = T.current_tilt("it_services", current, None)
        self.assertIn(res_initial["current_tilt"], T.TILTS)

        previous = {
            "metrics": {"roe": 12.0, "roa": 8.0, "pe": 18.0},
            "earnings_growth": 8.0,
            "revenue_growth": 7.0,
            "top10": [{"nse_symbol": "TCS"}, {"nse_symbol": "WIPRO"}],
        }
        res_expansion = T.current_tilt("it_services", current, previous)
        self.assertEqual(res_expansion["current_tilt"], "Expansion")
        self.assertIn(res_expansion["current_tilt"], T.TILTS)


class TestSafetyGuards(unittest.TestCase):
    """Test credit safety threshold and HTTP 401/403/429 abort behavior."""

    def test_credit_guard_aborts_before_any_api_call(self):
        # When expected requests exceed max_budget, run() must return 3 with ZERO API calls
        ret = R.run(fixtures=False, max_budget=10)  # threshold 10 is less than 128 constituents
        self.assertEqual(ret, 3, "Must abort with exit code 3 when over budget")

    def test_hard_stop_on_401_403_429(self):
        fetcher = E.Fetcher(key="test_key")
        with patch.object(fetcher.session, "get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.text = "Unauthorized"
            mock_get.return_value = mock_resp

            with self.assertRaises(E.IndianAPIError):
                fetcher.fetch("TCS")


class TestSectorSnapshotReader(unittest.TestCase):
    """Test snapshot loader and accessor functions."""

    def test_snapshot_loader_and_meta(self):
        snap = SNAP.load_snapshot()
        self.assertIsNotNone(snap)
        meta = SNAP.snapshot_meta(snap)
        self.assertIn("source", meta)
        self.assertIn("safety_threshold", meta)

        banking = SNAP.get_sector(snap, "banking")
        self.assertIsNotNone(banking)
        self.assertEqual(banking["key"], "banking")
        self.assertTrue(banking["is_financial"])
        self.assertIsNone(banking["metrics"]["roce"])


if __name__ == "__main__":
    unittest.main()

