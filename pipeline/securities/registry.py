"""Single-issuer enrichment via edgar.Company.

Produces one record per CIK for the securities registry. The registry's
schema is deliberately small: identifiers + classification. Per-class
metadata (expense ratios, share classes) and richer fields can grow
into the same record later (see DEFERRED_GAPS gaps #2 / #3).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import edgar

from .. import mappings
from ..nport import _normalize_cik, user_agent

log = logging.getLogger(__name__)

REGISTRY_SCHEMA_VERSION = "0.1"

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config"


def _load_sector_overrides() -> dict[str, str]:
    """Load per-CIK sector overrides (config/sector_overrides.json).

    SIC-derived sectors are wrong for some well-known issuers — modern
    internet/media (Alphabet, Meta, Disney) and managed-care HMOs
    (UnitedHealth). The override map fixes them by CIK.
    """
    path = _CONFIG_DIR / "sector_overrides.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not read sector overrides %s: %s", path, e)
        return {}
    overrides = raw.get("overrides", {})
    return {
        cik: entry["sector"]
        for cik, entry in overrides.items()
        if isinstance(entry, dict) and entry.get("sector")
    }


_SECTOR_OVERRIDES = _load_sector_overrides()


def _is_us_state(code: str | None) -> bool:
    if not code or len(code) != 2:
        return False
    return code.upper() in _US_STATE_CODES


# Two-letter US state + territory codes as used by SEC state_of_incorporation.
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC", "PR", "VI", "GU", "AS", "MP",
}


def _country(data) -> str | None:
    """Derive a country code. US state of incorporation → 'US'; otherwise
    use the SEC's country description (e.g., 'Japan', 'Cayman Islands').
    """
    soi = getattr(data, "state_of_incorporation", None)
    if _is_us_state(soi):
        return "US"
    desc = getattr(data, "state_of_incorporation_description", None)
    return desc or None


def derived_sector(cik: str | None, sic: str | None) -> str | None:
    """Derive sector from current overrides + SIC map. Pure local computation."""
    norm_cik = _normalize_cik(cik)
    if norm_cik and norm_cik in _SECTOR_OVERRIDES:
        return _SECTOR_OVERRIDES[norm_cik]
    return mappings.sic_to_sector(sic)


def _dedupe(seq) -> list:
    """Order-preserving dedupe."""
    return list(dict.fromkeys(seq or []))


def normalize(record: dict) -> bool:
    """Apply pure-local transforms to an existing record. Idempotent.

    Used by build_registry's in-place pass so config or logic changes
    propagate to existing records without re-fetching from EDGAR.
    Returns True if the record was mutated.
    """
    mutated = False
    new_sector = derived_sector(record.get("cik"), record.get("sic"))
    if new_sector != record.get("sector"):
        record["sector"] = new_sector
        mutated = True
    for field in ("tickers", "exchanges"):
        deduped = _dedupe(record.get(field) or [])
        if deduped != (record.get(field) or []):
            record[field] = deduped
            mutated = True
    return mutated


def enrich(cik: str | int) -> dict | None:
    """Fetch and enrich a single CIK. Returns None if the CIK can't be
    resolved at EDGAR (deleted entity, malformed input)."""
    norm_cik = _normalize_cik(cik)
    if not norm_cik:
        return None

    edgar.set_identity(user_agent())

    try:
        company = edgar.Company(norm_cik)
    except Exception as e:
        log.warning("CIK %s: edgar.Company failed: %s", norm_cik, e)
        return None

    data = company.data
    sic = data.sic or None
    sector = _SECTOR_OVERRIDES.get(norm_cik) or mappings.sic_to_sector(sic)
    return {
        "cik": norm_cik,
        "name": data.name,
        "sic": sic,
        "sic_description": data.sic_description or None,
        "sector": sector,
        "country": _country(data),
        "state_of_incorporation": data.state_of_incorporation or None,
        "tickers": _dedupe(data.tickers),
        "exchanges": _dedupe(data.exchanges),
        "entity_type": data.entity_type or None,
        "source": {
            "edgar_fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "schema_version": REGISTRY_SCHEMA_VERSION,
    }


def is_stale(record: dict, max_age_days: int) -> bool:
    """True if `record` is missing a fetch timestamp or older than max_age_days."""
    fetched = record.get("source", {}).get("edgar_fetched_at")
    if not fetched:
        return True
    try:
        ts = datetime.fromisoformat(fetched)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - ts
    return age.days >= max_age_days
