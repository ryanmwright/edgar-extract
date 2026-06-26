"""OpenFIGI CUSIP → {ticker, isin} batch mapping (fallback for edgartools).

Per-request job limit: 10 unauthenticated, 100 with an API key set via
OPENFIGI_API_KEY. Request rate: ~25/min unauth, ~250/min keyed. Batch
size and throttle adapt to whether a key is present.

Network failures are caught and logged — fallback is best-effort and
should not break the pipeline if OpenFIGI is unreachable.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Iterable

import requests

log = logging.getLogger(__name__)

_ENDPOINT = "https://api.openfigi.com/v3/mapping"
_BATCH_UNAUTH = 10
_BATCH_KEYED = 100
_THROTTLE_UNAUTH = 2.5
_THROTTLE_KEYED = 0.3


def _auth() -> tuple[dict, int, float]:
    headers = {"Content-Type": "application/json"}
    key = os.environ.get("OPENFIGI_API_KEY")
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
        return headers, _BATCH_KEYED, _THROTTLE_KEYED
    return headers, _BATCH_UNAUTH, _THROTTLE_UNAUTH


def _pick(results: list[dict]) -> dict | None:
    if not results:
        return None
    equity = [r for r in results if r.get("marketSector") == "Equity" and r.get("ticker")]
    pool = equity or [r for r in results if r.get("ticker")]
    if not pool:
        return None
    chosen = pool[0]
    return {"ticker": chosen.get("ticker"), "isin": chosen.get("isin")}


def map_cusips(cusips: Iterable[str]) -> dict[str, dict]:
    unique = sorted({c for c in cusips if c and c.strip()})
    out: dict[str, dict] = {}
    if not unique:
        return out

    headers, batch_size, throttle = _auth()
    log.info("OpenFIGI fallback: %d CUSIPs, batch=%d", len(unique), batch_size)

    for i in range(0, len(unique), batch_size):
        batch = unique[i : i + batch_size]
        payload = [{"idType": "ID_CUSIP", "idValue": c} for c in batch]
        if i > 0:
            time.sleep(throttle)
        try:
            resp = requests.post(_ENDPOINT, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.warning("OpenFIGI batch failed (continuing): %s", e)
            continue
        for cusip, item in zip(batch, resp.json()):
            picked = _pick(item.get("data", []))
            if picked:
                out[cusip] = picked

    return out
