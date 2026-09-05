"""Hybrid Screener pipeline tests — deterministic, offline (no scraping)."""
import unittest

from core import screener as SC
from core import sector_snapshot as SNAP


def _c(sym, mc, np_ttm=None, eq=None, op=None, dep=None, bor=None, roce=None):
    return SC.ScreenerCompany(
        symbol=sym, name=sym, cmp=None, market_cap=mc, pe_display=None,
        book_value=None, roe_reported=None, roce_reported=roce,
        net_profit_ttm=np_ttm, equity_reported=eq, operating_profit_ttm=op,
        depreciation_ttm=dep, borrowings=bor)


class TestNumberParsing(unittest.TestCase):
    def test_num(self):
        self.assertEqual(SC._num("₹17,62,680Cr."), 1762680.0)
        self.assertEqual(SC._num("8.91%"), 8.91)
        self.assertEqual(SC._num("-1,234"), -1234.0)
        self.assertIsNone(SC._num(""))       # missing -> None, never 0
        self.assertIsNone(SC._num("—"))


class TestPooledMetrics(unittest.TestCase):
    def setUp(self):
        # BIG: PE 20 ; LOSS: negative earnings ; MISS: missing ; TINY: PE 50
        self.comps = [_c("BIG", 100000, 5000, 40000, op=9000, dep=1000, bor=10000),
                      _c("LOSS", 2000, -500, 1000),
                      _c("MISS", 3000, None, None),
                      _c("TINY", 50, 1, 20)]
        self.m = SC.pooled_metrics(self.comps)

    def test_pe_is_aggregate_not_average(self):
        # aggregate = Σmc/Σnp over profitable = (100000+50)/(5000+1) = 20.0
        self.assertAlmostEqual(self.m["pe"], (100050) / (5001), places=1)
        self.assertNotAlmostEqual(self.m["pe"], (20 + 50) / 2, places=1)  # not simple avg

    def test_negative_and_missing_excluded_from_pe(self):
        excl = self.m["methodology"]["pe"]["exclusions"]
        self.assertIn("LOSS", excl)
        self.assertIn("MISS", excl)

    def test_missing_not_zeroed(self):
        # MISS has no equity -> excluded from P/B, not counted as equity 0
        self.assertEqual(self.m["methodology"]["pb"]["included"], 3)

    def test_financial_roce_none(self):
        mf = SC.pooled_metrics(self.comps, is_financial=True)
        self.assertIsNone(mf["roce"])


class TestMerge(unittest.TestCase):
    def test_screener_wins_and_indianapi_fills(self):
        scr = {"market_snapshot_date": "2026-09-04", "sectors": [
            {"key": "x", "sector_name": "X", "is_financial": False,
             "included_count": 1, "constituent_count": 1,
             "metrics": {"pe": 10, "pb": 2, "roe": 20, "roce": 15, "methodology": {}},
             "constituents": [{"nse_symbol": "A", "cmp": 100, "market_cap": 500,
                               "pe": 10, "pb": 2, "roe": 20, "roce": 15}]}]}
        fund = {"as_of_date": "2026-09-03", "sectors": [
            {"key": "x", "sector_name": "X", "is_financial": False,
             "metrics": {"pe": 99, "pb": 99, "roe": 99, "roce": 99,
                         "peg": 1.5, "piotroski": 6, "roa": 8},
             "constituents": [{"nse_symbol": "A", "roa": 8, "opm": 25,
                               "debt_to_equity": 0.3}]}]}
        merged, meta = SNAP.merge_sector(scr, fund, "x")
        # Screener wins headline valuation/returns
        self.assertEqual(merged["metrics"]["pe"], 10)
        self.assertEqual(merged["metrics"]["roce"], 15)
        # IndianAPI still fills the sector-level depth tiles
        self.assertEqual(merged["metrics"]["peg"], 1.5)
        self.assertEqual(merged["metrics"]["piotroski"], 6)
        # constituents are Screener-ONLY now: the Screener row is used as-is and
        # IndianAPI constituent extras (opm/roa/de) never leak into the table.
        row = merged["constituents"][0]
        self.assertEqual(row["cmp"], 100)
        self.assertNotIn("opm", row)
        # dates never conflated
        self.assertEqual(meta["market_snapshot_date"], "2026-09-04")
        self.assertEqual(meta["fundamentals_date"], "2026-09-03")

    def test_screener_missing_falls_back_to_indianapi(self):
        fund = {"as_of_date": "2026-09-03", "sectors": [
            {"key": "x", "metrics": {"pe": 99}, "constituents": []}]}
        merged, meta = SNAP.merge_sector(None, fund, "x")
        self.assertEqual(merged["metrics"]["pe"], 99)
        self.assertIsNone(meta["market_source"])


if __name__ == "__main__":
    unittest.main()
