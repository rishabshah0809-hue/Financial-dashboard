"""
test_sectors.py
---------------
Sector classification correctness — the ITC-misclassified-as-IT bug and the
whole class it belongs to. Verifies the authoritative known-company layer, its
precedence over name keywords and balance-sheet shape, safe token matching, the
uncertain fallback, and that the SectorProfile actually used for scoring is the
right one (not just the label).
"""

from __future__ import annotations

import unittest

from core.sectors import (
    SECTORS,
    classify_sector,
    detect_sector,
    get_sector,
    _normalize_name,
)

# ITC's real financial shape — high EBITDA margin, ~zero debt. This is exactly
# what used to force the old _structure_guess into "it_services".
ITC_METRICS = {
    "Debt to Equity Ratio": 0.0, "Interest % Sales": 0.0,
    "EBITDA Margin": 0.346, "Net Profit Margin": 0.262, "Fixed Asset Turnover": 1.6,
}


class TestITCBug(unittest.TestCase):
    def test_itc_variants_are_fmcg(self):
        for name in ("ITC", "ITC Ltd", "ITC Limited", "ITC Ltd.", "Itc Ltd",
                     "ITC LIMITED", "ITC Limited India"):
            self.assertEqual(detect_sector(name, ITC_METRICS)[0], "fmcg",
                             f"{name!r} must classify as FMCG")

    def test_itc_identity_beats_financial_shape(self):
        # Even with the IT-looking balance sheet, identity wins (spec E).
        d = classify_sector("ITC Ltd", ITC_METRICS)
        self.assertEqual(d.sector, "fmcg")
        self.assertEqual(d.source, "known_company")
        self.assertEqual(d.confidence, "high")
        self.assertIn("ITC", d.reason)

    def test_scoring_profile_is_fmcg_not_it(self):
        # Task L: verify the actual SectorProfile used to score, not just a label.
        sector_key = classify_sector("ITC Ltd", ITC_METRICS).sector
        profile = get_sector(sector_key)
        self.assertEqual(profile.name, "FMCG & Consumer Staples")
        self.assertEqual(profile.benchmarks, SECTORS["fmcg"].benchmarks)
        self.assertNotEqual(profile.benchmarks, SECTORS["it_services"].benchmarks)
        # a handful of bands ITC would be judged against must be FMCG's, not IT's
        for metric in ("EBITDA Margin", "Net Profit Margin", "Debt to Equity Ratio",
                       "Cash Conversion Cycle", "Return on Equity (ROE) %",
                       "Return on Capital Employed (ROCE) %"):
            self.assertEqual(profile.benchmarks[metric], SECTORS["fmcg"].benchmarks[metric])


class TestKnownCompanies(unittest.TestCase):
    def test_known_it(self):
        for name in ("Infosys", "Infosys Ltd", "Tata Consultancy Services", "TCS",
                     "Wipro", "HCL Technologies", "Tech Mahindra"):
            self.assertEqual(detect_sector(name)[0], "it_services", name)

    def test_known_fmcg(self):
        for name in ("Hindustan Unilever", "Hindustan Unilever Ltd", "Nestle India",
                     "Britannia", "Britannia Industries", "Dabur", "Dabur India",
                     "Marico"):
            self.assertEqual(detect_sector(name)[0], "fmcg", name)

    def test_known_other_sectors(self):
        cases = {
            "HDFC Bank": "banking", "State Bank of India": "banking",
            "Sun Pharmaceutical": "pharma", "Cipla": "pharma",
            "Larsen & Toubro": "infrastructure", "Reliance Industries": "infrastructure",
            "Indian Oil": "infrastructure", "Tata Steel": "manufacturing",
            "Maruti Suzuki": "manufacturing", "DLF": "realestate",
            "Avenue Supermarts": "retail",
        }
        for name, exp in cases.items():
            self.assertEqual(detect_sector(name)[0], exp, name)

    def test_india_suffix_does_not_break_identity(self):
        # trailing "India" fallback must not corrupt genuine "... India" names
        self.assertEqual(detect_sector("Nestle India")[0], "fmcg")
        self.assertEqual(detect_sector("Abbott India")[0], "pharma")
        self.assertEqual(detect_sector("Indian Oil Corporation")[0], "infrastructure")


class TestSafeMatching(unittest.TestCase):
    def test_it_fragment_never_means_it_services(self):
        # "it" inside "itc" must never trigger IT; ITC is FMCG.
        self.assertNotEqual(detect_sector("ITC")[0], "it_services")
        self.assertEqual(_normalize_name("ITC Ltd."), "itc")
        # get_sector must not let the "it" alias hijack "itc"
        self.assertNotEqual(get_sector("itc").name, "IT Services & Software")

    def test_short_ticker_only_matches_whole_name(self):
        # "acc" (cement co) must not fire inside an unrelated longer name
        self.assertEqual(detect_sector("Accelya Solutions")[0], "generic")
        self.assertEqual(detect_sector("ACC")[0], "manufacturing")

    def test_empty_and_malformed(self):
        for bad in ("", "   ", None, "!!!", "123", "---"):
            key, reason = detect_sector(bad)  # type: ignore[arg-type]
            self.assertEqual(key, "generic")
            self.assertIsInstance(reason, str)

    def test_unknown_company_metrics_only(self):
        # no name signal -> structure inference; a lender shape -> banking
        d = classify_sector("Zzz Unknown Holdings", {
            "Debt to Equity Ratio": 8.0, "Interest % Sales": 0.5})
        self.assertEqual(d.sector, "banking")
        self.assertEqual(d.source, "financials")
        self.assertEqual(d.confidence, "low")

    def test_unknown_company_no_signal_is_generic(self):
        d = classify_sector("Zzz Unknown Holdings", None)
        self.assertEqual(d.sector, "generic")
        self.assertEqual(d.confidence, "none")
        self.assertEqual(d.source, "fallback")


if __name__ == "__main__":
    unittest.main()
