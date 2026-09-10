"""
scoring.py
----------
Turns a parsed workbook into a numeric fundamental score and a verdict of
STRONG / NEUTRAL / WEAK.

The method, in plain English:

1. For each key ratio we take the latest value AND a 3-year average, because a
   single good year can be luck while a 3-year trend is character.
2. Each ratio is compared against the *sector's* weak/strong thresholds and
   converted to a 0-100 sub-score. Anything between the two thresholds is
   scaled linearly, so a company just short of "strong" is not treated the
   same as one in genuine trouble.
3. Sub-scores roll up into five pillars (growth, profitability, returns,
   leverage, efficiency), weighted by what matters in that sector.
4. A small consistency bonus/penalty is applied for trend direction and
   earnings-quality (does reported profit actually turn into cash?).

Everything is transparent: each metric keeps its own record so the UI can show
exactly why the verdict came out the way it did. The LLM layer sits on top of
this and explains it in words — it never replaces the arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .parser import FinancialModel
from .sectors import (
    LOWER_IS_BETTER,
    METRIC_PILLARS,
    PERCENT_METRICS,
    PILLARS,
    SectorProfile,
)

STRONG_CUTOFF = 66.0
WEAK_CUTOFF = 40.0

VERDICT_STYLES = {
    "STRONG": ("#16a34a", "Fundamentally Strong"),
    "NEUTRAL": ("#d97706", "Fundamentally Neutral"),
    "WEAK": ("#dc2626", "Fundamentally Weak"),
}


@dataclass
class MetricScore:
    metric: str
    pillar: str
    latest: float | None
    average_3y: float | None
    weak_at: float
    strong_at: float
    score: float           # 0-100
    trend: float           # slope over available years, normalised
    verdict: str           # Strong / Adequate / Weak
    lower_is_better: bool

    def display(self, value: float | None) -> str:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return "n/a"
        if self.metric in PERCENT_METRICS:
            return f"{value * 100:.1f}%"
        if "Days" in self.metric or "Cycle" in self.metric:
            return f"{value:.0f} days"
        return f"{value:.2f}x" if "Ratio" in self.metric or "Turnover" in self.metric else f"{value:.2f}"


@dataclass
class Assessment:
    company: str
    sector: SectorProfile
    total_score: float
    verdict: str
    pillar_scores: dict[str, float]
    metrics: list[MetricScore]
    strengths: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    earnings_quality: float | None = None

    @property
    def headline(self) -> str:
        return VERDICT_STYLES[self.verdict][1]

    @property
    def colour(self) -> str:
        return VERDICT_STYLES[self.verdict][0]

    def metric(self, name: str) -> MetricScore | None:
        return next((m for m in self.metrics if m.metric == name), None)

    def as_prompt_table(self) -> str:
        """A compact text table handed to the LLM."""
        lines = [
            f"{'Metric':<38}{'Latest':>12}{'3Y avg':>12}{'Score':>8}  Sector band"
        ]
        for m in self.metrics:
            band = f"weak<{m.display(m.weak_at)} / strong>{m.display(m.strong_at)}"
            if m.lower_is_better:
                band = f"weak>{m.display(m.weak_at)} / strong<{m.display(m.strong_at)}"
            lines.append(
                f"{m.metric:<38}{m.display(m.latest):>12}"
                f"{m.display(m.average_3y):>12}{m.score:>8.0f}  {band}"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------
# metric plumbing
# --------------------------------------------------------------------------
# Some workbooks name the same idea differently. First match wins.
METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "Sales Growth": ("Sales Growth", "Revenue Growth"),
    "Net Profit Growth": ("Net Profit Growth", "PAT Growth", "Net Profit Growth %"),
    "EBITDA Margin": ("EBITDA Margin", "EBITDA Margins"),
    "Net Profit Margin": ("Net Profit Margin", "Net Margins", "Net Margin"),
    "Return on Equity (ROE) %": ("Return on Equity (ROE) %", "ROE", "Return on Equity"),
    "Return on Capital Employed (ROCE) %": (
        "Return on Capital Employed (ROCE) %", "ROCE", "Return on Capital Employed"),
    "Return on Assets (ROA) %": ("Return on Assets (ROA) %", "ROA", "Return on Assets"),
    "Debt to Equity Ratio": ("Debt to Equity Ratio", "Debt to Equity", "D/E"),
    "Interest Coverage Ratio": ("Interest Coverage Ratio", "Interest Coverage"),
    "Cash Conversion Cycle": ("Cash Conversion Cycle", "Cash Conversion Cycle (Days)"),
    "CFO / PAT": ("CFO / PAT", "CFO/PAT", "Cash from Operations / PAT"),
    "Fixed Asset Turnover": ("Fixed Asset Turnover", "Fixed Asset Turnover Ratio"),
}


def _resolve(model: FinancialModel, metric: str) -> pd.Series:
    for candidate in METRIC_ALIASES.get(metric, (metric,)):
        series = model.series(candidate)
        if not series.empty:
            return series
    return pd.Series(dtype="float64")


def _clean(series: pd.Series) -> pd.Series:
    """Drop infinities and absurd outliers that come from divide-by-near-zero."""
    s = series.replace([np.inf, -np.inf], np.nan).dropna()
    return s[s.abs() < 1e6]


def _sub_score(value: float, weak_at: float, strong_at: float, lower_better: bool) -> float:
    """Map a raw ratio onto 1-100 using the sector's two thresholds.

    Universal score-band boundaries (all ratios share these):
      1-39 = Weak, 40-65 = Neutral, 66-100 = Strong.

    The *financial* thresholds differ per ratio/sector (``weak_at`` /
    ``strong_at`` from the sector profile). Anchors:
      higher-is-better: value == weak -> 40, value == strong -> 66.
      lower-is-better:  value == weak -> 40, value == strong -> 66 (reversed).
    Between the anchors the score interpolates linearly (continuous, not
    bucketed). Beyond an anchor the score extends linearly for one further
    ``span`` (strong-weak distance) to 100 on the good side and to 1 on the
    bad side, then clamps. Never invents data: NaN input yields NaN.
    """
    import math
    try:
        if value is None or (isinstance(value, float) and (math.isnan(value))):
            return float("nan")
    except TypeError:
        pass
    if not lower_better:
        span = strong_at - weak_at
        if not span or span <= 0:
            span = max(abs(strong_at), abs(weak_at), 1e-6)
            # Degenerate band: fall back to either side of the midpoint.
            mid = (weak_at + strong_at) / 2.0
            return 66.0 if value >= mid else 40.0
        if value <= weak_at:
            droop = (weak_at - value) / span
            return max(1.0, 40.0 - min(droop, 1.0) * 39.0)
        if value >= strong_at:
            excess = (value - strong_at) / span
            return min(100.0, 66.0 + min(excess, 1.0) * 34.0)
        return 40.0 + (value - weak_at) / span * 26.0
    # lower-is-better: weak_at is the high (bad) number, strong_at the low one.
    span = weak_at - strong_at
    if not span or span <= 0:
        span = max(abs(strong_at), abs(weak_at), 1e-6)
        mid = (weak_at + strong_at) / 2.0
        return 66.0 if value <= mid else 40.0
    if value >= weak_at:
        overshoot = (value - weak_at) / span
        return max(1.0, 40.0 - min(overshoot, 1.0) * 39.0)
    if value <= strong_at:
        margin = (strong_at - value) / span
        return min(100.0, 66.0 + min(margin, 1.0) * 34.0)
    return 66.0 - (value - strong_at) / span * 26.0


def score_ratio(value: float | None, weak_at: float, strong_at: float,
                lower_better: bool) -> tuple[float | None, str | None]:
    """Reusable single-point scorer: raw value -> (1-100 score, band word).

    Returns (None, None) when the value is missing so callers can show
    "unavailable" instead of inventing a score. Band words follow the
    universal boundaries: 1-39 Weak, 40-65 Neutral, 66-100 Strong.
    """
    import math
    if value is None:
        return None, None
    try:
        if isinstance(value, float) and math.isnan(value):
            return None, None
    except TypeError:
        return None, None
    s = _sub_score(float(value), weak_at, strong_at, lower_better)
    if isinstance(s, float) and math.isnan(s):
        return None, None
    return float(s), classify(float(s))


def classify(score: float) -> str:
    """Universal score-band word: 1-39 Weak, 40-65 Neutral, 66-100 Strong."""
    if score >= 66:
        return "Strong"
    if score >= 40:
        return "Neutral"
    return "Weak"


def _trend(series: pd.Series) -> float:
    """
    Normalised slope of the last 5 observations.
    Positive means improving, negative means deteriorating.
    """
    s = _clean(series).tail(5)
    if len(s) < 3:
        return 0.0
    x = np.arange(len(s), dtype=float)
    slope = np.polyfit(x, s.to_numpy(dtype=float), 1)[0]
    scale = max(abs(float(s.mean())), 1e-6)
    return float(np.clip(slope / scale, -1.0, 1.0))


def _label(score: float) -> str:
    # Universal score bands: 1-39 Weak, 40-65 Neutral, 66-100 Strong.
    return classify(score)


# --------------------------------------------------------------------------
# main entry point
# --------------------------------------------------------------------------
def assess(model: FinancialModel, sector: SectorProfile) -> Assessment:
    """Score a company against its sector's expectations."""
    metric_scores: list[MetricScore] = []
    gaps: list[str] = []

    for metric, (weak_at, strong_at) in sector.benchmarks.items():
        series = _clean(_resolve(model, metric))
        if series.empty:
            gaps.append(metric)
            continue

        latest = float(series.iloc[-1])
        avg_3y = float(series.tail(3).mean())
        lower_better = metric in LOWER_IS_BETTER

        # 60% weight on the latest year, 40% on the 3-year average: recent
        # performance matters most, but not to the exclusion of history.
        score = (
            0.6 * _sub_score(latest, weak_at, strong_at, lower_better)
            + 0.4 * _sub_score(avg_3y, weak_at, strong_at, lower_better)
        )

        trend = _trend(series)
        if lower_better:
            trend = -trend
        score = float(np.clip(score + trend * 6.0, 1.0, 100.0))

        metric_scores.append(
            MetricScore(
                metric=metric,
                pillar=METRIC_PILLARS.get(metric, "profitability"),
                latest=latest,
                average_3y=avg_3y,
                weak_at=weak_at,
                strong_at=strong_at,
                score=score,
                trend=trend,
                verdict=_label(score),
                lower_is_better=lower_better,
            )
        )

    if not metric_scores:
        raise ValueError(
            "None of the benchmark ratios could be found in this workbook. "
            "Check that the 'Ratio Analysis' sheet uses standard ratio names."
        )

    # Roll metric scores up into pillars.
    pillar_scores: dict[str, float] = {}
    for pillar in PILLARS:
        members = [m.score for m in metric_scores if m.pillar == pillar]
        if members:
            pillar_scores[pillar] = float(np.mean(members))

    weights = {p: sector.weights.get(p, 1.0) for p in pillar_scores}
    weight_sum = sum(weights.values()) or 1.0
    total = sum(pillar_scores[p] * weights[p] for p in pillar_scores) / weight_sum

    # Earnings quality: does accounting profit convert into operating cash?
    earnings_quality = None
    cfo = _clean(_resolve(model, "CFO / PAT"))
    if not cfo.empty:
        earnings_quality = float(cfo.tail(3).mean())
        if earnings_quality < 0.5:
            total -= 5.0     # profits that never become cash are a real warning
        elif earnings_quality > 1.0:
            total += 2.5

    total = float(np.clip(total, 1.0, 100.0))
    verdict = "STRONG" if total >= STRONG_CUTOFF else "WEAK" if total < WEAK_CUTOFF else "NEUTRAL"

    ranked = sorted(metric_scores, key=lambda m: m.score, reverse=True)
    strengths = [
        f"{m.metric} at {m.display(m.latest)} — {'ahead of' if not m.lower_is_better else 'better than'} "
        f"the {sector.name} strong threshold of {m.display(m.strong_at)}."
        for m in ranked[:3] if m.score >= 66
    ]
    concerns = [
        f"{m.metric} at {m.display(m.latest)} sits in the weak band for "
        f"{sector.name} (weak at {m.display(m.weak_at)})."
        for m in reversed(ranked[-3:]) if m.score < 40
    ]

    return Assessment(
        company=model.company,
        sector=sector,
        total_score=round(total, 1),
        verdict=verdict,
        pillar_scores={p: round(v, 1) for p, v in pillar_scores.items()},
        metrics=metric_scores,
        strengths=strengths,
        concerns=concerns,
        data_gaps=gaps,
        earnings_quality=earnings_quality,
    )


# Growth benchmarks are calibrated to ANNUAL rates, so a single
# quarter-on-quarter figure is not scored against them; it is used only as a
# direction nudge in quarterly mode.
GROWTH_METRICS = {"Sales Growth", "Net Profit Growth", "EBITDA Growth", "EPS Growth"}


def assess_quarterly(model: FinancialModel, sector: SectorProfile) -> Assessment:
    """
    Score a company from a TWO-QUARTER income statement.

    This is the quarterly counterpart to :func:`assess`. It deliberately does
    NOT reuse the annual rule (0.6·latest + 0.4·3-year-average + 5-year trend):
    with two quarters there is no multi-year history and the growth thresholds
    are annual. Instead it scores the *current quarter's* level-based metrics
    (margins, interest coverage, and any balance-sheet ratio that happens to be
    present) against the same sector thresholds, applies a small
    quarter-on-quarter direction nudge, and reports everything it could not
    compute as a data gap. Missing data is never turned into a zero, and no
    figure is invented.
    """
    if model.meta.get("periodicity") != "quarterly":
        raise ValueError("assess_quarterly expects a quarterly model.")

    metric_scores: list[MetricScore] = []
    gaps: list[str] = []

    # Skip ratios that are not meaningful for this business type (e.g. ROCE /
    # inventory turnover for a bank) rather than scoring them as weak.
    from .quarterly_semantics import metric_applicable
    business_type = model.meta.get("business_type")

    for metric, (weak_at, strong_at) in sector.benchmarks.items():
        if not metric_applicable(metric, business_type):
            continue
        series = _clean(_resolve(model, metric))
        if series.empty:
            gaps.append(metric)                    # genuinely unavailable
            continue
        if metric in GROWTH_METRICS:
            continue                               # has data, but not scored here

        current = float(series.iloc[-1])
        lower_better = metric in LOWER_IS_BETTER
        score = _sub_score(current, weak_at, strong_at, lower_better)

        # QoQ direction nudge from the two points, if both are present.
        trend = 0.0
        if len(series) >= 2:
            prev = float(series.iloc[-2])
            scale = max(abs(prev), 1e-6)
            trend = float(np.clip((current - prev) / scale, -1.0, 1.0))
        adj = -trend if lower_better else trend
        score = float(np.clip(score + adj * 4.0, 1.0, 100.0))

        metric_scores.append(
            MetricScore(
                metric=metric,
                pillar=METRIC_PILLARS.get(metric, "profitability"),
                latest=current,
                average_3y=None,                   # no multi-period history
                weak_at=weak_at,
                strong_at=strong_at,
                score=score,
                trend=trend,
                verdict=_label(score),
                lower_is_better=lower_better,
            )
        )

    if not metric_scores:
        raise ValueError(
            "This quarterly filing did not expose any scorable metric "
            "(margins or interest coverage). Only the raw statements are shown.")

    pillar_scores: dict[str, float] = {}
    for pillar in PILLARS:
        members = [m.score for m in metric_scores if m.pillar == pillar]
        if members:
            pillar_scores[pillar] = float(np.mean(members))

    weights = {p: sector.weights.get(p, 1.0) for p in pillar_scores}
    weight_sum = sum(weights.values()) or 1.0
    total = sum(pillar_scores[p] * weights[p] for p in pillar_scores) / weight_sum
    total = float(np.clip(total, 1.0, 100.0))
    verdict = "STRONG" if total >= STRONG_CUTOFF else "WEAK" if total < WEAK_CUTOFF else "NEUTRAL"

    ranked = sorted(metric_scores, key=lambda m: m.score, reverse=True)
    strengths = [
        f"{m.metric} at {m.display(m.latest)} — ahead of the {sector.name} "
        f"strong threshold of {m.display(m.strong_at)} this quarter."
        for m in ranked[:3] if m.score >= 66
    ]
    concerns = [
        f"{m.metric} at {m.display(m.latest)} sits in the weak band for "
        f"{sector.name} this quarter (weak at {m.display(m.weak_at)})."
        for m in reversed(ranked[-3:]) if m.score < 40
    ]

    return Assessment(
        company=model.company,
        sector=sector,
        total_score=round(total, 1),
        verdict=verdict,
        pillar_scores={p: round(v, 1) for p, v in pillar_scores.items()},
        metrics=metric_scores,
        strengths=strengths,
        concerns=concerns,
        data_gaps=gaps,
        earnings_quality=None,                     # no cash-flow data in the filing
    )


def compare_sectors(model: FinancialModel, profiles: list[SectorProfile]) -> pd.DataFrame:
    """
    Score the same company under several sector rule books.

    This is the clearest demonstration of the project's core idea: the numbers
    never change, only the yardstick does, and the verdict moves with it.
    """
    rows = []
    for profile in profiles:
        try:
            result = assess(model, profile)
        except ValueError:
            continue
        rows.append({
            "Sector lens": profile.name,
            "Score": result.total_score,
            "Verdict": result.verdict,
        })
    return pd.DataFrame(rows)
