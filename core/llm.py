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


SYSTEM_PROMPT = """You are a buy-side equity analyst writing an internal note.

Rules you must follow:
- The numeric score and verdict were produced by a deterministic scoring engine.
  Do NOT contradict them or invent your own score. Explain them.
- Judge every ratio against the SECTOR the company operates in. A debt/equity of
  8x is normal for a bank and alarming for a software firm. Say so explicitly
  where it applies.
- Be specific: quote the actual numbers you are given. Never invent a figure,
  a competitor name, or a news event that is not in the data.
- If a metric is missing, say it is missing rather than guessing.
- Write in plain, confident English. No hype, no disclaimers about being an AI.

Return STRICT JSON with exactly these keys and no markdown fencing:
{
  "summary": "3-4 sentence verdict explaining WHY the company scores where it does, in sector terms",
  "sector_context": "2-3 sentences on what 'good' looks like in this sector and how this company compares",
  "strengths": ["3 to 4 specific bullet points, each quoting a number"],
  "risks": ["3 to 4 specific bullet points, each quoting a number"],
  "what_to_watch": ["2 to 3 forward-looking items an analyst should track next"],
  "confidence": "high | medium | low, based on how complete the data is"
}"""


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
        "profit covers the interest bill {val} over, so the debt is comfortably affordable",
        "profit covers the interest bill only {val}, so a dip in earnings could make debt payments hard to meet"),
    "Cash Conversion Cycle": ("Cash cycle",
        "cash comes back in {val} — it funds the business quickly without extra borrowing",
        "cash is tied up for {val} in stock and unpaid bills, straining day-to-day funding"),
    "Debtor Days": ("Collection speed",
        "customers pay in about {val}, so cash flows in fast",
        "customers take about {val} to pay, locking up cash the business could use"),
    "Inventory Days": ("Inventory speed",
        "stock sells through in about {val}, keeping little money idle on shelves",
        "stock sits for about {val}, tying up cash and risking write-downs"),
    "Return on Capital Employed (ROCE) %": ("ROCE",
        "every rupee of capital earns {val} back as profit — the business uses its money efficiently",
        "capital earns only {val} back as profit, so the business isn't using its money efficiently"),
    "Return on Equity (ROE) %": ("Return on equity",
        "shareholders earn {val} on their money — a strong return for owners",
        "shareholders earn only {val} on their money — a thin return for owners"),
    "Return on Assets (ROA) %": ("Return on assets",
        "the company squeezes {val} of profit from its assets",
        "the company squeezes only {val} of profit from its assets"),
    "Debt to Equity Ratio": ("Debt load",
        "debt is a modest {val} of owners' money, so the balance sheet is safe",
        "debt is a heavy {val} of owners' money, which raises risk if profits wobble"),
    "Gross Margin": ("Gross margin",
        "{val} of every sale is left after production cost — solid pricing power",
        "only {val} of every sale is left after production cost — thin pricing power"),
    "EBITDA Margin": ("Operating margin",
        "the core business keeps {val} of sales as operating profit",
        "the core business keeps only {val} of sales as operating profit"),
    "Net Profit Margin": ("Net margin",
        "{val} of every sale reaches the bottom line as profit",
        "only {val} of every sale reaches the bottom line as profit"),
    "Net Profit Growth": ("Profit growth",
        "bottom-line profit grew {val} year on year",
        "bottom-line profit moved {val} year on year — momentum is weak"),
    "Sales Growth": ("Sales growth",
        "revenue grew {val} year on year",
        "revenue moved {val} year on year — the top line is barely growing"),
    "Fixed Asset Turnover": ("Asset efficiency",
        "each rupee of plant and equipment generates {val} of sales",
        "each rupee of plant and equipment generates only {val} of sales — assets are under-used"),
    "CFO / PAT": ("Cash quality",
        "reported profit turns into real cash at {val} — earnings are high quality",
        "reported profit turns into cash at only {val} — profit isn't fully backed by cash"),
    "Interest % Sales": ("Interest burden",
        "interest eats just {val} of sales, leaving room to invest",
        "interest eats {val} of sales, leaving less for growth"),
}


def _friendly(metric: str) -> str:
    """A short, plain title when a ratio isn't in RATIO_MEANINGS."""
    return (metric.replace(" Ratio", "").replace(" (OPM)", "")
            .replace(" %", "").replace("%", "").strip())


def _reason(m, is_strength: bool) -> str:
    """One plain-language line: why the ratio is strong/weak and its business impact."""
    val = m.display(m.latest)
    info = RATIO_MEANINGS.get(m.metric)
    if info:
        name, good, bad = info
        clause = (good if is_strength else bad).format(val=val)
        return f"{name} — {clause}."
    name = _friendly(m.metric)
    if is_strength:
        return (f"{name} — at {val} it clears the sector's strong mark, "
                "a clear plus for the business.")
    return (f"{name} — at {val} it sits below the sector's safe level, "
            "a weak spot that drags on the business.")


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
                "You are an equity analyst answering a question about one company. "
                "Use only the data provided. Judge everything in sector context. "
                "If the data does not answer the question, say so. "
                "Answer in under 150 words, plain prose, no markdown headings."
            ),
        },
        {"role": "user", "content": f"{build_user_prompt(result)}\n\nANALYST QUESTION: {question}"},
    ]
    try:
        return post(config, messages).strip()
    except Exception as exc:                      # noqa: BLE001
        return f"The model could not be reached: {exc}"
