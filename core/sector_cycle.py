"""
sector_cycle.py
---------------
Structural cycle & seasonality profiles for each FundaCheck sector.

These describe the sector's *long-run* behaviour across market cycles — which is
what a decade of history reflects — as a qualitative, editorial characterization.
They are NOT a computed backtest of the last 10 years (the pipeline has no
historical price feed), and are labelled as such in the UI.

The "current tilt" is derived from real data — the snapshot's pooled trailing
earnings growth for that sector — so the phase read is grounded, not invented.
"""

from __future__ import annotations

# nature: one-word character shown as a badge.
# seasonal: whether intra-year seasonality is material.
# text: 5–8 line structural description.
SECTOR_CYCLE: dict[str, dict] = {
    "banking": {
        "nature": "Cyclical", "seasonal": True,
        "text": ("Banking & finance is a cyclical sector geared to the credit and "
                 "interest-rate cycle. Loan growth, net interest margins and credit "
                 "costs move with GDP and RBI policy: the sector expands as rates "
                 "ease and credit demand rises, while asset-quality stress and "
                 "provisioning tend to surface late in the cycle after tightening. "
                 "There is a clear intra-year rhythm — disbursements build through "
                 "H2 (Oct–Mar) around the festive season and fiscal year-end, and "
                 "Q4 (Jan–Mar) is usually the strongest quarter.")},
    "it_services": {
        "nature": "Cyclical (global)", "seasonal": True,
        "text": ("IT services is a structural-growth sector but cyclical to global "
                 "(mainly US/Europe) technology budgets and the USD/INR rate. Deal "
                 "signings and discretionary spend soften in global slowdowns and "
                 "re-accelerate in recoveries. Seasonality is mild but consistent: "
                 "the first half of the fiscal year (Apr–Sep) is seasonally strong "
                 "on budget flush and project ramp-ups, while Q3 (Oct–Dec) is "
                 "softer due to furloughs and holidays. A weaker rupee is a margin "
                 "tailwind.")},
    "fmcg": {
        "nature": "Defensive", "seasonal": True,
        "text": ("FMCG & consumer staples is a defensive, low-cyclicality sector — "
                 "demand for everyday essentials holds up across the economic cycle. "
                 "What moves it is seasonal and input-cost driven: rural demand "
                 "tracks the monsoon and harvest, the festive second half (Q3) lifts "
                 "volumes, and summer supports beverages. Margins swing more with "
                 "commodity cycles (palm oil, crude derivatives, grains) than with "
                 "demand, so the sector is a relative safe-haven in downturns.")},
    "pharma": {
        "nature": "Defensive", "seasonal": True,
        "text": ("Pharmaceuticals & healthcare is largely defensive — domestic drug "
                 "demand is inelastic to the economic cycle. The export/US-generics "
                 "business carries its own pricing cycle (price erosion vs new "
                 "launches) and is sensitive to USFDA regulatory events, which drive "
                 "company-specific swings more than macro. Seasonality is modest: "
                 "acute therapies such as anti-infectives and respiratory peak in the "
                 "monsoon and winter (roughly Q2–Q3).")},
    "infrastructure": {
        "nature": "Highly cyclical", "seasonal": True,
        "text": ("Infrastructure, power & capital goods is highly cyclical and "
                 "capex-led. Order inflows track the government and private capital-"
                 "expenditure cycle and are sensitive to interest rates because "
                 "projects are long-gestation and debt-funded. Execution has a strong "
                 "seasonal pattern: activity slows through the monsoon (Q2, Jul–Sep) "
                 "and accelerates into H2, peaking in the Jan–Mar fiscal year-end "
                 "push. Leverage makes the sector an early beneficiary of easing "
                 "cycles and an early casualty of tightening ones.")},
    "manufacturing": {
        "nature": "Cyclical", "seasonal": True,
        "text": ("Manufacturing & industrials is cyclical, geared to industrial "
                 "activity and commodity cycles. Volumes rise with the broader "
                 "economy and compress in slowdowns, while margins swing with input "
                 "(metals, energy) prices. Consumer-facing lines such as autos and "
                 "durables show festive seasonality — Q3 (Oct–Dec) is typically the "
                 "strongest quarter — and demand softens through the monsoon. Best "
                 "judged across a full cycle rather than a single year.")},
    "retail": {
        "nature": "Cyclical (discretionary)", "seasonal": True,
        "text": ("Retail & consumption is cyclical, tied to discretionary spending "
                 "and therefore to inflation, rates and consumer confidence. "
                 "Seasonality is pronounced: the festive Q3 (Oct–Dec, Diwali) is the "
                 "peak quarter, the wedding season supports Q4, and Q1/monsoon "
                 "months are softer. The model runs on velocity, so footfalls and "
                 "same-store growth lead the cycle; margins are thin and sensitive to "
                 "discounting during weak demand.")},
    "realestate": {
        "nature": "Highly cyclical", "seasonal": True,
        "text": ("Real estate is highly cyclical and rate-sensitive, moving in "
                 "multi-year up- and down-cycles driven by affordability, interest "
                 "rates and inventory overhang. Long cycles dominate, but there is "
                 "seasonality too: launches and bookings cluster around the festive "
                 "season (Q3), and registrations often rise near the fiscal year-end. "
                 "Falling rates and low unsold inventory mark the up-phase; rising "
                 "rates and rising inventory mark the down-phase.")},
    "generic": {
        "nature": "Blended", "seasonal": True,
        "text": ("A diversified basket broadly tracks the overall market and GDP "
                 "cycle, so its cyclicality depends on the underlying mix of "
                 "businesses. Indian equities in aggregate show the familiar "
                 "seasonal pattern — a stronger festive H2 and a fiscal year-end "
                 "(Jan–Mar) push — layered on top of the macro cycle. Treat this "
                 "profile as a market-level reference rather than a single-sector "
                 "read.")},
}


def phase_from_growth(growth: float | None) -> tuple[str, str]:
    """A grounded 'current tilt' from the snapshot's pooled earnings growth.

    Returns (phase_label, sentence). Never overstates: it is an inference from
    the latest trailing earnings growth, not a full cycle-dating model.
    """
    if growth is None:
        return ("—", "No earnings-growth reading is available in this snapshot, "
                      "so the current cycle phase is not inferred.")
    if growth <= 0:
        return ("Slowdown / late-cycle",
                f"Pooled earnings are contracting ({growth:.1f}%), consistent with a "
                "slowdown or late-cycle phase.")
    if growth < 8:
        return ("Mid-cycle",
                f"Pooled earnings are growing moderately ({growth:.1f}%), consistent "
                "with a mid-cycle phase.")
    return ("Expansion",
            f"Pooled earnings are growing strongly ({growth:.1f}%), consistent with "
            "an expansion phase.")


def profile(sector_key: str) -> dict | None:
    return SECTOR_CYCLE.get(sector_key)
