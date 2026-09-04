"""
writing_rules.py
----------------
The single, authoritative style guide for EVERY piece of explanatory text the
app shows a user — the Funda Score explanation, the strength/risk bullets, the
AI analyst note and the free-text Q&A answer.

Why this file exists: the dashboard is read by people with NO finance
background. Text that just restates a ratio ("ROCE at 3% is below the sector
threshold") is useless to them. Every explanation must say, in plain words,
what the number *means for the business* and *why* that is good or bad.

These rules are MANDATORY. `WRITING_RULES` is injected into the LLM system
prompt (so the model must follow them before it writes), and the offline,
rule-based text generators are written to obey the same rules. The
human-readable version of this guide lives in docs/WRITING_STYLE.md, but THIS
module is the enforced source of truth — keep the two in sync.
"""

from __future__ import annotations

# The mandate block that is prepended to every LLM prompt. Written as direct
# instructions to the writer (human or model).
WRITING_RULES = """WRITING RULES — you MUST follow every one of these before writing a single word.

AUDIENCE
- Write for a smart reader who knows NOTHING about finance. No jargon. If a
  finance term is unavoidable, explain it in plain words in the same sentence.

EXPLAIN THE "WHY", NOT JUST THE NUMBER
- Never simply restate a ratio or say it is "above/below the threshold". Always
  say what the number MEANS for the actual business — its cash, its safety, its
  ability to grow, its pricing power, its debt — and WHY that is good or bad.
- Quote the real number once, in plain units (e.g. "3%", "6.7 times", "45 days").
  The number is evidence for your point, not the point itself.
- Judge every number against the SECTOR the company is in. A debt level that is
  normal for a bank is alarming for a software firm. Say so when it matters.

HONESTY
- Never invent a figure, a competitor, or an event that is not in the data.
- If a metric is missing, say it is missing — do not guess.
- Do not contradict the numeric score; explain how it was reached.

TONE
- Plain, confident English. Short words over long ones. No hype, no filler, no
  disclaimers about being an AI.

LENGTH & SHAPE (these are hard requirements)
- Funda Score explanation: EXACTLY 4 sentences that together explain HOW the
  score was reached — (1) what the score and its band mean in plain terms,
  (2) the company's strongest area and the plain reason it is strong,
  (3) the weakest area and the plain reason it is weak, (4) the overall
  takeaway for someone deciding whether this is a healthy business.
- Each strength and each risk: EXACTLY 2 sentences. Sentence 1 states the plain
  meaning of the metric with its number. Sentence 2 explains why that genuinely
  helps or hurts the business, in terms a non-finance reader feels.
"""


def system_preamble() -> str:
    """The rules block to prepend to any LLM system prompt."""
    return WRITING_RULES
