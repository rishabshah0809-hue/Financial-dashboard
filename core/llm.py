"""
llm.py
------
The "AI analyst" layer.

The numbers are already decided by scoring.py. The language model's only job is
to read those numbers *in sector context* and write the paragraph a human
analyst would write. That ordering matters: the model can never quietly change
a ratio, so the output stays auditable.

Two free providers are supported out of the box, both with generous free tiers:

  Groq        - https://console.groq.com/keys      (fast, free)
  OpenRouter  - https://openrouter.ai/keys         (free ':free' models)

If no key is configured the terminal still works — it falls back to a written
summary generated from the scoring engine itself, so the app never hard-fails
on a missing API key.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

import requests

from .scoring import Assessment
from .writing_rules import WRITING_RULES

TIMEOUT_SECONDS = 60

# Diagnostics only. Emits provider name, success/failure, HTTP status, error
# category and a timestamp. It NEVER logs API keys, secret values, prompts,
# document text or any user data.
LOGGER = logging.getLogger("fundacheck.llm")


def _classify(err: str) -> tuple[str, str]:
    """(http_status, category) from a provider error string — no sensitive data."""
    m = re.search(r"\b([1-5]\d{2})\b", err)
    http = m.group(1) if m else "none"
    low = err.lower()
    if "429" in err or "rate" in low or "quota" in low or "tpm" in low or "too many" in low:
        cat = "rate_limit"
    elif "413" in err or "too large" in low or "context length" in low:
        cat = "too_large"
    elif http in ("401", "403") or "unauthor" in low or "permission" in low or "api key" in low:
        cat = "auth"
    # HTTP 5xx is a provider outage regardless of any "model" wording in the body.
    elif http in ("500", "502", "503", "504") or "unavailable" in low or "overload" in low or "high demand" in low:
        cat = "unavailable"
    elif "timeout" in low or "timed out" in low:
        cat = "timeout"
    elif http == "404" or "not found" in low or "no longer available" in low or "model" in low:
        cat = "model"
    else:
        cat = "other"
    return http, cat

# The analyst always runs in reasoning mode. The verdict is a judgement across a
# dozen interacting ratios where the sector decides what "good" means, so a model
# that reasons before answering is not a nice-to-have. Any model configured here
# must be one of these; anything else is replaced with the default rather than
# silently downgrading the analysis to a non-reasoning model.
REASONING_MODELS = {
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "deepseek-r1-distill-llama-70b",
}
DEFAULT_REASONING_MODEL = "openai/gpt-oss-120b"


@dataclass
class LLMConfig:
    """
    Connection settings for the analyst.

    `api_keys` is a pool rather than a single key: free tiers rate-limit per
    key, so a second key lets a busy session keep working instead of silently
    dropping to the offline note.
    """

    provider: str = "groq"          # "groq" | "openrouter" | "gemini" | "offline"
    api_keys: list[str] = field(default_factory=list)
    model: str = ""
    temperature: float = 0.2
    reasoning_effort: str = "high"
    # Providers to try, in order, if this one fails/rate-limits (e.g. Groq → Gemini).
    fallbacks: list = field(default_factory=list)

    def __post_init__(self) -> None:
        # Enforce reasoning mode at construction, so no call site can opt out.
        if self.provider == "groq" and self.model not in REASONING_MODELS:
            self.model = DEFAULT_REASONING_MODEL

    @property
    def api_key(self) -> str:
        return self.api_keys[0] if self.api_keys else ""

    @property
    def is_live(self) -> bool:
        return self.provider != "offline" and bool(self.api_keys)


PROVIDERS = {
    "groq": {
        "label": "Groq",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        # Every entry here must be a reasoning model — see REASONING_MODELS.
        # Reasoning-capable models only: the verdict is a judgement call across
        # a dozen interacting ratios, which is exactly where a model that thinks
        # before answering beats one that does not.
        "models": [
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "qwen/qwen3-32b",
        ],
        "key_env": "GROQ_API_KEY",
        "signup": "https://console.groq.com/keys",
    },
    "openrouter": {
        "label": "OpenRouter (free models)",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "google/gemma-2-9b-it:free",
            "mistralai/mistral-7b-instruct:free",
        ],
        "key_env": "OPENROUTER_API_KEY",
        "signup": "https://openrouter.ai/keys",
    },
    "gemini": {
        "label": "Gemini",
        # Google Generative Language REST API (free tier). Different request/
        # response shape from the OpenAI-style providers above — handled in _post.
        "url": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        "models": ["gemini-flash-latest", "gemini-2.0-flash"],
        "key_env": "GEMINI_API_KEY",
        "signup": "https://aistudio.google.com/apikey",
    },
}


def _keys_from(source: dict | None, env_name: str) -> list[str]:
    """
    Collect API keys from Streamlit secrets or the environment — tolerantly.

    Streamlit secret names are case-sensitive and users name them in many ways
    (GROQ_API_KEY, groq_api_key, GROQ_KEY, a [groq] section with api_keys, …).
    Rather than demand one exact spelling, match the provider prefix
    case-insensitively across flat entries and sections. Keys are never stored
    here — they come from the deployment's secret store.
    """
    prefix = env_name.split("_")[0].lower()     # groq | gemini | google | openrouter
    keys: list[str] = []

    def _add(v) -> None:
        if isinstance(v, (list, tuple)):
            keys.extend(str(x) for x in v)
        elif isinstance(v, str):
            keys.extend(v.split(","))

    if source:
        for raw_k, v in source.items():
            k = str(raw_k).lower().replace("-", "_")
            if isinstance(v, dict):                      # e.g. [groq] section
                if k == prefix or k.startswith(prefix):
                    _add(v.get("api_keys") or v.get("keys")
                         or v.get("api_key") or v.get("key"))
            elif prefix in k and any(t in k for t in ("key", "token", "api", prefix)):
                _add(v)                                  # e.g. GROQ_API_KEY / groq_key

    for name in (f"{env_name}S", env_name):
        raw = os.getenv(name, "")
        if raw:
            keys.extend(raw.split(","))

    seen, unique = set(), []
    for key in (k.strip() for k in keys):
        if key and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


def config_from_env(provider: str = "groq", model: str = "",
                    secrets: dict | None = None) -> LLMConfig:
    spec = PROVIDERS.get(provider)
    if not spec:
        return LLMConfig(provider="offline")
    keys = _keys_from(secrets, spec["key_env"])
    if provider == "gemini":                    # Google keys are often named GOOGLE_*
        for k in _keys_from(secrets, "GOOGLE_API_KEY"):
            if k not in keys:
                keys.append(k)
    return LLMConfig(
        provider=provider,
        api_keys=keys,
        model=model or spec["models"][0],
    )


SYSTEM_PROMPT = (
    WRITING_RULES
    + """

You are a buy-side equity analyst writing an internal note for a reader with no
finance background. The numeric score and verdict were produced by a
deterministic scoring engine — do NOT contradict them or invent your own score;
explain how they were reached.

Return STRICT JSON with exactly these keys and no markdown fencing:
{
  "summary": "EXACTLY 4 sentences explaining HOW the Funda Score was reached: (1) what the score and its band mean in plain terms, (2) the strongest area and the plain reason it is strong, (3) the weakest area and the plain reason it is weak, (4) the overall takeaway for someone deciding if this is a healthy business",
  "sector_context": "2-3 sentences on what 'good' looks like in this sector and how this company compares",
  "strengths": ["3 to 4 items. Each is EXACTLY 2 sentences: sentence 1 states the metric's plain meaning with its number; sentence 2 explains why that genuinely helps the business, for a non-finance reader"],
  "risks": ["3 to 4 items. Each is EXACTLY 2 sentences: sentence 1 states the metric's plain meaning with its number; sentence 2 explains why that genuinely hurts the business, for a non-finance reader"],
  "what_to_watch": ["2 to 3 forward-looking items an analyst should track next"],
  "confidence": "high | medium | low, based on how complete the data is"
}""")


def build_user_prompt(result: Assessment) -> str:
    gaps = ", ".join(result.data_gaps) if result.data_gaps else "none"
    quality = (
        f"{result.earnings_quality:.2f}x" if result.earnings_quality is not None else "not available"
    )
    pillars = ", ".join(f"{name}: {score:.0f}/100" for name, score in result.pillar_scores.items())

    return f"""COMPANY: {result.company}
SECTOR APPLIED: {result.sector.name}
SECTOR CHARACTERISTICS: {result.sector.notes}
SECTOR PEER CONTEXT: {result.sector.peer_context or "not supplied"}

ENGINE OUTPUT
Composite score: {result.total_score}/100
Verdict: {result.verdict}
Pillar scores: {pillars}
Earnings quality (3Y average CFO/PAT): {quality}
Metrics that could not be found in the workbook: {gaps}

RATIO DETAIL (latest year, 3-year average, 0-100 sub-score, and the sector's
weak/strong bands — note the bands are sector-specific, not universal):
{result.as_prompt_table()}

Write the analyst note as JSON."""


# Which provider actually answered the most recent successful call — for the UI.
_LAST_USED = ""


def last_provider() -> str:
    return _LAST_USED


def post(config: LLMConfig, messages: list[dict], json_mode: bool = False) -> str:
    """Fallback-aware call: try `config`, then each of its `.fallbacks` in turn
    (e.g. Groq → Gemini). Records which provider answered in `last_provider()`.

    `json_mode` asks the provider to return a strict JSON object (used for the
    analyst note), which stops a reasoning model from replying in prose."""
    global _LAST_USED
    chain = [config, *(config.fallbacks or [])]
    errors = []
    for cfg in chain:
        label = PROVIDERS.get(cfg.provider, {}).get("label", cfg.provider)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not cfg.is_live:
            errors.append(f"{cfg.provider}: no key configured")
            LOGGER.warning("llm provider=%s status=failed http=none category=no_key ts=%s",
                           label, ts)
            continue
        try:
            text = _post(cfg, messages, json_mode=json_mode)
            _LAST_USED = label
            LOGGER.info("llm provider=%s status=success http=200 ts=%s", label, ts)
            return text
        except Exception as exc:                # noqa: BLE001 - try the next provider
            http, category = _classify(str(exc))
            errors.append(f"{cfg.provider}: {exc}")
            LOGGER.warning("llm provider=%s status=failed http=%s category=%s ts=%s",
                           label, http, category, ts)
            continue
    raise RuntimeError("all LLM providers failed — " + " | ".join(errors))


def _post_gemini(config: LLMConfig, messages: list[dict], json_mode: bool = False) -> str:
    """Google Generative Language API (different shape from the OpenAI providers)."""
    spec = PROVIDERS["gemini"]
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": m["content"]}]}
        for m in messages if m["role"] != "system"
    ]
    gen_config = {"temperature": config.temperature, "maxOutputTokens": 4096}
    if json_mode:
        gen_config["responseMimeType"] = "application/json"
    payload = {
        "contents": contents,
        "generationConfig": gen_config,
    }
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    url = spec["url"].format(model=config.model)
    last_error = "no API key configured"
    for key in config.api_keys:
        try:
            response = requests.post(
                url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=payload, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = str(exc)
            continue
        if response.status_code == 200:
            cand = response.json().get("candidates") or []
            if not cand:
                last_error = "empty response"
                continue
            parts = cand[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        if response.status_code in (401, 403, 429):
            last_error = f"{response.status_code} on one key"
            continue
        raise RuntimeError(f"Gemini returned {response.status_code}: "
                           f"{response.text[:200]}")
    raise RuntimeError(f"every Gemini key failed — last error: {last_error}")


def _post(config: LLMConfig, messages: list[dict], json_mode: bool = False) -> str:
    """
    Call the provider, trying each key in the pool.

    A rate-limited or rejected key moves to the next one rather than failing the
    request; only when every key is exhausted does the caller fall back.
    """
    if config.provider == "gemini":
        return _post_gemini(config, messages, json_mode=json_mode)
    spec = PROVIDERS[config.provider]
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        # Reasoning models spend part of the completion budget on hidden
        # thinking, so a small cap can leave the visible answer empty (and the
        # note then has no JSON to parse). Give the answer room to land.
        "max_tokens": 4096,
    }
    if config.model in REASONING_MODELS:
        payload["reasoning_effort"] = config.reasoning_effort
    # Ask OpenAI-style providers (Groq, OpenRouter) for a strict JSON object so a
    # reasoning model returns the note as JSON instead of prose.
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_error = "no API key configured"
    for key in config.api_keys:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if config.provider == "openrouter":
            headers["HTTP-Referer"] = "https://github.com/fundacheck"
            headers["X-Title"] = "FundaCheck"
        try:
            response = requests.post(spec["url"], headers=headers, json=payload,
                                     timeout=TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = str(exc)
            continue

        if response.status_code == 200:
            message = response.json()["choices"][0]["message"]
            # Reasoning models may return their thinking separately; the answer
            # is always in content.
            return message.get("content") or ""
        if response.status_code in (401, 402, 403, 429):
            last_error = f"{response.status_code} on one key"
            continue        # try the next key in the pool
        raise RuntimeError(f"{spec['label']} returned {response.status_code}: "
                           f"{response.text[:200]}")

    raise RuntimeError(f"every key failed — last error: {last_error}")


def _extract_json(text: str) -> dict:
    """LLMs sometimes wrap JSON in prose, code fences or reasoning tags. Dig it out."""
    text = (text or "").strip()
    # Reasoning models occasionally leak a <think>...</think> block before the
    # answer; drop it so it can't swallow the JSON braces.
    text = re.sub(r"(?is)<think>.*?</think>", "", text).strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in the model response.")
    return json.loads(text[start:end + 1])


# Plain-language rationale for each ratio: (friendly name, why-good, why-bad).
# {val} is filled with the latest reading. Written for a non-finance reader and
# focused on what it means for the business, not just the band it lands in.
RATIO_MEANINGS = {
    "Interest Coverage Ratio": ("Interest cover",
        "Operating profit covers the company's interest bill about {val} over. That is a comfortable safety margin, so even a bad year would not stop it paying lenders.",
        "Operating profit covers the company's interest bill only about {val}. That leaves almost no room for error, so one weak year could make its loan payments hard to meet."),
    "Cash Conversion Cycle": ("Cash cycle",
        "Cash spent on stock and unpaid bills flows back to the business in about {val}. Money is freed up quickly, so it can fund day-to-day running without leaning on loans.",
        "Cash stays locked up in stock and unpaid bills for about {val} before it returns. That ties up money the business could use and usually means extra borrowing to bridge the gap."),
    "Debtor Days": ("Collection speed",
        "Customers settle their bills in about {val} on average. Cash arrives soon after each sale, keeping the business well stocked with working money.",
        "Customers take about {val} on average to pay. The company is effectively lending to its own customers for that long, starving it of cash it has already earned."),
    "Inventory Days": ("Inventory speed",
        "Goods sit in stock only about {val} before they sell. Little cash is left idle on the shelf, and there is less risk of stock ageing or needing markdowns.",
        "Goods sit in stock about {val} before they sell. That parks a lot of cash on the shelf and raises the risk of discounting unsold or ageing stock."),
    "Return on Capital Employed (ROCE) %": ("ROCE",
        "For every ₹100 put into the business it earns about {val} of that back as operating profit each year. That is an efficient use of money and a sign the core business is genuinely productive.",
        "For every ₹100 put into the business it earns only about {val} back as operating profit each year. The company is not making its money work hard, which limits how much value it can build over time."),
    "Return on Equity (ROE) %": ("Return on equity",
        "The profit earned for shareholders works out to about {val} of the money they have invested. That is a strong return, meaning owners' money is being multiplied well.",
        "The profit earned for shareholders is only about {val} of the money they have invested. That is a thin return, so owners' money is barely growing inside the business."),
    "Return on Assets (ROA) %": ("Return on assets",
        "The company turns everything it owns into profit at a rate of about {val}. It gets good earnings out of its assets, a sign of an efficient operation.",
        "The company turns everything it owns into profit at only about {val}. A lot is tied up in assets that are not producing much, which weighs on returns."),
    "Debt to Equity Ratio": ("Debt load",
        "The company's debt is only about {val} of the owners' money in the business. The balance sheet is safe, so a rough patch is unlikely to threaten its survival.",
        "The company's debt is about {val} of the owners' money in the business. That is a heavy load, so if profits wobble the interest and repayments could become hard to carry."),
    "Gross Margin": ("Gross margin",
        "After the direct cost of what it sells, the company keeps about {val} of each sale. That healthy cushion shows real pricing power and room to cover its other costs.",
        "After the direct cost of what it sells, only about {val} of each sale is left. That thin cushion leaves little to cover salaries and overheads, and signals weak pricing power."),
    "EBITDA Margin": ("Operating margin",
        "The core business keeps about {val} of its sales as operating profit, before financing and accounting items. That shows the everyday operation itself makes money reliably.",
        "The core business keeps only about {val} of its sales as operating profit. The everyday operation barely earns its keep, so there is little buffer if costs rise or sales slip."),
    "Net Profit Margin": ("Net margin",
        "About {val} of every rupee of sales survives all costs and taxes to become final profit. A healthy slice reaches the bottom line, which funds growth and rewards to owners.",
        "Only about {val} of every rupee of sales survives all costs and taxes as final profit. Almost everything the company earns is eaten by costs, leaving little to reinvest or return to owners."),
    "Net Profit Growth": ("Profit growth",
        "The company's final profit grew about {val} versus the year before. Rising profit means the business is getting stronger, not just bigger.",
        "The company's final profit moved about {val} versus the year before. Weak or falling profit suggests it is struggling to turn effort into extra earnings."),
    "Sales Growth": ("Sales growth",
        "Revenue grew about {val} over the previous year. The company is winning more business, which is the raw fuel for future profit.",
        "Revenue moved about {val} over the previous year. The top line is barely growing, so there is little fresh fuel to drive future profit."),
    "Fixed Asset Turnover": ("Asset efficiency",
        "Every ₹1 tied up in plant and equipment generates about {val} of sales. The company sweats its assets hard, getting plenty of business from what it owns.",
        "Every ₹1 tied up in plant and equipment generates only about {val} of sales. Expensive assets are being under-used, which drags on returns."),
    "CFO / PAT": ("Cash quality",
        "For every ₹1 of reported profit, the business actually collected about {val} as real cash. Profit that turns into cash is high quality and hard to fake.",
        "For every ₹1 of reported profit, only about {val} showed up as real cash. Profit that does not convert into cash can flatter the accounts and hint at collection or accounting problems."),
    "Interest % Sales": ("Interest burden",
        "Interest on debt eats up only about {val} of sales. That leaves plenty of each sale free to reinvest in growth.",
        "Interest on debt eats up about {val} of sales. That is a real drag, taking money straight to lenders that could otherwise fund growth."),
}


def _friendly(metric: str) -> str:
    """A short, plain title when a ratio isn't in RATIO_MEANINGS."""
    return (metric.replace(" Ratio", "").replace(" (OPM)", "")
            .replace(" %", "").replace("%", "").strip())


def _reason(m, is_strength: bool) -> str:
    """Two plain-language sentences: what the ratio means and why it helps/hurts
    the business. Format is "Title — sentence one. Sentence two." so the UI can
    split the title from the explanation."""
    val = m.display(m.latest)
    info = RATIO_MEANINGS.get(m.metric)
    if info:
        name, good, bad = info
        clause = (good if is_strength else bad).format(val=val)
        return f"{name} — {clause}"
    name = _friendly(m.metric)
    if is_strength:
        return (f"{name} — at {val} it comfortably clears the mark this sector "
                "expects. That is a genuine advantage and one of the things "
                "holding the business up.")
    return (f"{name} — at {val} it sits below the level this sector needs. "
            "That is a weak spot that quietly drags on the company's overall health.")


def offline_note(result: Assessment) -> dict:
    """
    Deterministic fallback so the terminal is fully usable with no API key.
    Built from the same scoring output the LLM would have received.
    """
    best = sorted(result.metrics, key=lambda m: m.score, reverse=True)
    worst = sorted(result.metrics, key=lambda m: m.score)
    strong_pillars = [p for p, s in result.pillar_scores.items() if s >= 60]
    weak_pillars = [p for p, s in result.pillar_scores.items() if s < 45]

    summary = (
        f"{result.company} scores {result.total_score}/100 against "
        f"{result.sector.name} expectations, which places it in the "
        f"{result.verdict.lower()} band. "
        + (f"It is carried by {', '.join(strong_pillars)}. " if strong_pillars else "")
        + (f"It is held back by {', '.join(weak_pillars)}. " if weak_pillars else "")
        + "This is the rule-based reading — the AI analyst could not be reached "
          "for its narrative view."
    )

    return {
        "summary": summary,
        "sector_context": result.sector.notes,
        "strengths": [
            _reason(m, True) for m in best[:4] if m.score >= 55
        ] or ["No metric currently clears its sector's strong threshold."],
        "risks": [
            _reason(m, False) for m in worst[:4] if m.score < 60
        ] or ["No metric falls into the sector's weak band."],
        "what_to_watch": [
            f"Direction of {m.metric} — currently "
            f"{'improving' if m.trend > 0.05 else 'deteriorating' if m.trend < -0.05 else 'flat'}."
            for m in worst[:3]
        ],
        "confidence": "low" if result.data_gaps else "medium",
        "_offline": True,
    }


def analyse(result: Assessment, config: LLMConfig) -> dict:
    """
    Ask the language model for a sector-aware analyst note.
    Falls back to the offline note on any failure, with the error attached
    so the UI can tell the user what went wrong.
    """
    if not config.is_live and not any(c.is_live for c in config.fallbacks):
        return offline_note(result)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(result)},
    ]
    try:
        note = _extract_json(post(config, messages, json_mode=True))
    except Exception as exc:                      # noqa: BLE001 - surfaced in the UI
        fallback = offline_note(result)
        fallback["_error"] = str(exc)
        return fallback

    note["_offline"] = False
    note["_model"] = config.model
    return note


def answer_question(result: Assessment, question: str, config: LLMConfig) -> str:
    """Free-text Q&A about the loaded company (the 'ask the analyst' box)."""
    if not config.is_live and not any(c.is_live for c in config.fallbacks):
        return (
            "The analyst is not connected. Add Groq API keys to the app's secrets "
            "(see the README) and ask again."
        )
    messages = [
        {
            "role": "system",
            "content": (
                WRITING_RULES
                + "\n\nYou are an equity analyst answering one question about one "
                "company for a reader with no finance background. Use only the data "
                "provided. Judge everything in sector context. If the data does not "
                "answer the question, say so. Answer in under 150 words, plain prose, "
                "no markdown headings. (The 4-sentence and 2-sentence shape rules "
                "above apply to notes, not to this free-text answer — but every other "
                "rule, especially explaining the 'why' in plain words, still applies.)"
            ),
        },
        {"role": "user", "content": f"{build_user_prompt(result)}\n\nANALYST QUESTION: {question}"},
    ]
    try:
        return post(config, messages).strip()
    except Exception as exc:                      # noqa: BLE001
        return f"The model could not be reached: {exc}"
