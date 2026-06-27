"""N-PORT enum code → json output schema string lookups.

Codes are per the SEC N-PORT XML technical spec. Verified against
live filings for VTI (equity) and BND (bond) and against the lookups
edgartools' own derivative-classification code uses.

Also hosts the SIC → coarse sector lookup used by the securities
registry. SIC mapping data lives in `config/sic_to_sector.json` so
it stays diffable / PR-reviewable rather than buried in code.
"""

import json
from pathlib import Path

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# assetCat — full N-PORT enumeration
# EC=equity-common, EP=equity-preferred,
# DBT=debt, SN=structured note, LON=loan,
# ABS-* = asset-backed (MBS, asset-backed CP, collateralized bond/debt obligation, other),
# STIV=short-term investment vehicle (money market, liquidity pool — really cash-like),
# RA=repurchase agreement,
# DE/DCR/DIR/DCO/DFE/DOT = derivative (equity/credit/rate/commodity/FX/other),
# COMD=commodity, RE=real estate, OTH=other.
_ASSET_CAT = {
    "EC": "equity",
    "EP": "equity",
    "DBT": "debt",
    "SN": "debt",
    "LON": "debt",
    "ABS-MBS": "debt",
    "ABS-APCP": "debt",
    "ABS-CBDO": "debt",
    "ABS-O": "debt",
    "DE": "derivative",
    "DCR": "derivative",
    "DIR": "derivative",
    "DCO": "derivative",
    "DFE": "derivative",
    "DOT": "derivative",
    "STIV": "other",
    "RA": "other",
    "COMD": "other",
    "RE": "other",
    "OTH": "other",
    "OTHER": "other",
}

# issuerCat — full N-PORT enumeration
# CORP=corporate, UST=US Treasury, USGA=US government agency,
# USGSE=US government sponsored entity, MUN=municipal,
# NUSS=non-US sovereign, PF=private fund, RF=registered fund, OTH=other.
_ISSUER_CAT = {
    "CORP": "corp",
    "UST": "sovereign",
    "USGA": "sovereign",
    "USGSE": "sovereign",
    "NUSS": "sovereign",
    "MUN": "muni",
    "PF": "other",
    "RF": "other",
    "OTH": "other",
    "OTHER": "other",
}


def asset_cat(code: str | None) -> str:
    if not code:
        return "other"
    return _ASSET_CAT.get(code.upper(), "other")


def issuer_cat(code: str | None) -> str:
    if not code:
        return "other"
    return _ISSUER_CAT.get(code.upper(), "other")


def _load_sic_to_sector() -> dict[str, str]:
    with open(_CONFIG_DIR / "sic_to_sector.json") as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


_SIC_TO_SECTOR = _load_sic_to_sector()


def sic_to_sector(sic: str | int | None) -> str | None:
    """Map an SEC SIC code to a coarse GICS-style sector. Returns None
    if the code is missing or not in the curated table."""
    if sic is None:
        return None
    key = str(sic).strip()
    if not key:
        return None
    # SIC codes are 4-digit; some sources strip leading zeros.
    key = key.zfill(4)
    return _SIC_TO_SECTOR.get(key)
