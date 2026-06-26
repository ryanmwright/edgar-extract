"""N-PORT enum code → json output schema string lookups.

Codes are per the SEC N-PORT XML technical spec. Verified against
live filings for VTI (equity) and BND (bond) and against the lookups
edgartools' own derivative-classification code uses.
"""

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
