"""
indianapi_client.py
-------------------
The only place that talks to IndianAPI over the network. Kept deliberately thin
and separate from the pure engine in ``core.indianapi`` so the metric logic can
be tested offline against saved fixtures.

Contract
========
* One HTTP GET per company. The monthly job calls this at most ~150 times, once
  per de-duplicated NSE symbol, and reuses the response for both sector
  aggregates and the Top 10 — there are no extra calls.
* **Never returns the wrong company.** IndianAPI's ``/stock`` endpoint is queried
  by company name (resolved from ``data/company_master.csv``); the response's
  ``exchangeCodeNse`` is checked against the requested NSE symbol, and a mismatch
  is treated as a miss rather than silently accepted.
* **Never fabricates.** Any non-200, network error, or unparseable body returns
  ``None`` with the reason recorded by the caller; the pipeline counts it as a
  failed/skipped request and moves on.

Endpoint and auth are configurable by environment so nothing is hard-coded to a
single deployment:
  INDIANAPI_KEY        (required)  — sent as the ``X-Api-Key`` header
  INDIANAPI_BASE_URL   default ``https://stock.indianapi.in``
  INDIANAPI_STOCK_PATH default ``/stock``
  INDIANAPI_NAME_PARAM default ``name``
"""

from __future__ import annotations

import logging
import os
import time

import requests

LOGGER = logging.getLogger("fundacheck.indianapi")

_TIMEOUT = 20
_RETRIES = 2
_BACKOFF = 1.5


def _cfg() -> dict:
    return {
        "base": os.environ.get("INDIANAPI_BASE_URL", "https://stock.indianapi.in").rstrip("/"),
        "path": os.environ.get("INDIANAPI_STOCK_PATH", "/stock"),
        "param": os.environ.get("INDIANAPI_NAME_PARAM", "name"),
        "key": os.environ.get("INDIANAPI_KEY", ""),
    }


def has_key() -> bool:
    return bool(_cfg()["key"])


class Fetcher:
    """Callable symbol/name → raw JSON dict (or None). Tracks per-request outcome
    so the pipeline can report successful / failed counts without the key ever
    being logged."""

    def __init__(self) -> None:
        cfg = _cfg()
        if not cfg["key"]:
            raise RuntimeError("INDIANAPI_KEY is not set; refusing to run a live "
                               "refresh without a key (no fabricated data).")
        self._cfg = cfg
        self._session = requests.Session()
        self.outcomes: list[dict] = []

    def fetch(self, name: str, expected_symbol: str) -> dict | None:
        cfg = self._cfg
        url = f"{cfg['base']}{cfg['path']}"
        headers = {"X-Api-Key": cfg["key"], "Accept": "application/json"}
        params = {cfg["param"]: name}
        raw = self._get(url, headers, params, expected_symbol)
        return raw

    def _get(self, url, headers, params, expected_symbol) -> dict | None:
        last = "unknown"
        for attempt in range(_RETRIES + 1):
            try:
                resp = self._session.get(url, headers=headers, params=params,
                                         timeout=_TIMEOUT)
            except requests.RequestException as exc:
                last = f"network:{type(exc).__name__}"
                time.sleep(_BACKOFF * (attempt + 1))
                continue
            if resp.status_code == 429:
                last = "rate_limited"
                time.sleep(_BACKOFF * (attempt + 2))
                continue
            if resp.status_code >= 400:
                last = f"http_{resp.status_code}"
                break
            try:
                data = resp.json()
            except ValueError:
                last = "bad_json"
                break
            got = ((data.get("companyProfile") or {}).get("exchangeCodeNse") or "").strip().upper()
            if expected_symbol and got and got != expected_symbol.upper():
                # Right endpoint, wrong company — never accept it.
                self.outcomes.append({"symbol": expected_symbol, "status": "failed",
                                      "reason": f"symbol_mismatch:{got}"})
                return None
            self.outcomes.append({"symbol": expected_symbol, "status": "success",
                                  "reason": None})
            return data
        self.outcomes.append({"symbol": expected_symbol, "status": "failed",
                              "reason": last})
        return None
