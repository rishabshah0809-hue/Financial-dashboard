# Writing style guide (mandatory)

Every explanation the app shows — the **Funda Score** explainer, the
**strength / risk** bullets, the **AI analyst note**, and the **Ask-the-analyst**
answers — must follow these rules. They exist because the dashboard is read by
people with **no finance background**: text that only restates a ratio is
useless to them.

> The enforced source of truth is [`core/writing_rules.py`](../core/writing_rules.py).
> Its `WRITING_RULES` string is injected into the LLM system prompt (so the model
> must obey it before writing) and the offline, rule-based generators are written
> to the same rules. If you change one, change the other.

## The rules

**Audience.** Write for a smart reader who knows nothing about finance. No
jargon. If a finance term is unavoidable, explain it in plain words in the same
sentence.

**Explain the "why", not just the number.**
- Never simply restate a ratio or say it is "above / below the threshold".
- Always say what the number *means for the business* — its cash, safety,
  ability to grow, pricing power, debt — and *why* that is good or bad.
- Quote the real number once, in plain units ("3%", "6.7 times", "45 days").
  The number is evidence for the point, not the point itself.
- Judge every number against the **sector**. What is normal for a bank is
  alarming for a software firm; say so when it matters.

**Honesty.** Never invent a figure, competitor, or event. If a metric is
missing, say so. Never contradict the numeric score — explain how it was reached.

**Tone.** Plain, confident English. Short words over long. No hype, no filler,
no "as an AI" disclaimers.

**Length & shape (hard requirements).**
- **Funda Score explanation:** exactly **4 sentences** that explain *how the
  score was reached* — (1) what the score and band mean in plain terms, (2) the
  strongest area and the plain reason it is strong, (3) the weakest area and the
  plain reason it is weak, (4) the overall takeaway.
- **Each strength and each risk:** exactly **2 sentences**. Sentence 1 = the
  plain meaning of the metric with its number. Sentence 2 = why that genuinely
  helps or hurts the business, in terms a non-finance reader feels.
