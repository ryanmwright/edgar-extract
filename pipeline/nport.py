"""Fetch and parse the latest N-PORT-P for a fund via edgartools.

Returns the intermediate dict consumed by `transform.to_json1`, plus
filing-provenance metadata. Resolution flow for ticker/ISIN:

1. Edgartools resolves tickers from its bundled CUSIP→ticker parquet
   and surfaces them on `inv.ticker`.
2. For any holding that ends up with no ticker AND no ISIN, fall back
   to OpenFIGI's CUSIP→ticker/ISIN lookup.
3. CUSIPs are read in-memory only and are dropped before the returned
   dict crosses out of this module.
"""

from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from pathlib import Path

import edgar

from . import openfigi

log = logging.getLogger(__name__)

_PLACEHOLDER_CUSIPS = {"000000000", "000000", "N/A", "", "0"}


def user_agent() -> str:
    ua = os.environ.get("EDGAR_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError(
            "EDGAR_USER_AGENT is not set. SEC EDGAR requires a descriptive "
            "User-Agent identifying the requester (e.g. "
            "'Fund X-Ray your-email@example.com'). Export it before running."
        )
    return ua


def _f(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _iso_date(val) -> str | None:
    if val is None:
        return None
    s = str(val)
    return s.split(" ")[0] if " " in s else s


def _period_bucket(p) -> dict | None:
    if p is None:
        return None
    return {
        "3mo": _f(getattr(p, "period3Mon", None)),
        "1yr": _f(getattr(p, "period1Yr", None)),
        "5yr": _f(getattr(p, "period5Yr", None)),
        "10yr": _f(getattr(p, "period10Yr", None)),
        "30yr": _f(getattr(p, "period30Yr", None)),
    }


def _debt_block(ds) -> dict | None:
    if ds is None:
        return None
    coupon = getattr(ds, "coupon_kind", None)
    return {
        "maturity_date": _iso_date(getattr(ds, "maturity_date", None)),
        "coupon_kind": coupon.lower() if coupon else None,
        "coupon_rate": _f(getattr(ds, "annualized_rate", None)),
        "is_default": bool(getattr(ds, "is_default", False)),
        "interest_payments_in_arrears": bool(
            getattr(ds, "are_instrument_payents_in_arrears", False)
        ),
        "is_mandatory_convertible": bool(getattr(ds, "is_mandatory_convertible", False)),
        "is_contingent_convertible": bool(getattr(ds, "is_continuing_convertible", False)),
    }


def _interest_rate_risk(current_metrics: dict | None) -> list[dict]:
    if not current_metrics:
        return []
    out = []
    for cur, metric in current_metrics.items():
        dv01 = _period_bucket(getattr(metric, "intrstRtRiskdv01", None))
        dv100 = _period_bucket(getattr(metric, "intrstRtRiskdv100", None))
        if dv01 or dv100:
            out.append({"currency": cur, "dv01": dv01, "dv100": dv100})
    return out


def _credit_spread_risk(fi) -> dict | None:
    ig = _period_bucket(getattr(fi, "credit_spread_risk_investment_grade", None))
    nig = _period_bucket(getattr(fi, "credit_spread_risk_non_investment_grade", None))
    if not ig and not nig:
        return None
    return {"investment_grade": ig, "non_investment_grade": nig}


def _monthly_returns(return_info, as_of: str | None) -> list[dict]:
    """Convert N-PORT's 3-month-return triplets to (month, class_id, return_pct)."""
    if return_info is None or not as_of:
        return []
    # as_of is the quarter-end; return1/2/3 are the three months ending there.
    from datetime import date

    try:
        y, m, _ = as_of.split("-")
        end = date(int(y), int(m), 1)
    except (ValueError, AttributeError):
        return []

    def _prev(d: date, n: int) -> str:
        month = d.month - n
        year = d.year
        while month < 1:
            month += 12
            year -= 1
        return f"{year:04d}-{month:02d}"

    months = [_prev(end, 2), _prev(end, 1), _prev(end, 0)]
    out = []
    for cr in getattr(return_info, "monthly_total_returns", []) or []:
        cid = getattr(cr, "class_id", None)
        for i, attr in enumerate(("return1", "return2", "return3")):
            r = _f(getattr(cr, attr, None))
            if r is not None:
                out.append({"month": months[i], "class_id": cid, "return_pct": r})
    return out


def _normalize_cik(cik) -> str | None:
    """Format any CIK representation to SEC-canonical zero-padded 10 digits."""
    if cik is None:
        return None
    s = str(cik).strip()
    if not s:
        return None
    try:
        return f"{int(s):010d}"
    except ValueError:
        return None


def _load_ticker_index(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        log.info("ticker index not found at %s — every ticker will hit EDGAR", p)
        return {}
    try:
        with open(p) as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not read ticker index %s: %s", p, e)
        return {}
    # Normalize: tickers upper-cased, CIKs zero-padded.
    out: dict[str, str] = {}
    for ticker, cik in raw.items():
        if not isinstance(ticker, str):
            continue
        norm = _normalize_cik(cik)
        if norm:
            out[ticker.upper()] = norm
    return out


def _ticker_variants(ticker: str) -> list[str]:
    """Plausible lookup variants for a ticker.

    N-PORT sometimes strips the dash from class-suffix tickers (BRK-A → BRKA).
    Edgartools' ticker parquet sometimes carries only the parent (CWEN), not
    the class form (CWEN-A / CWENA). We try, in order:
      1. The raw ticker as given (catches the common case).
      2. The dashed form, when the trailing letter looks like a share class.
      3. The bare prefix without the suffix letter — same issuer, since
         share-class tickers all roll up to one CIK.
    The fallbacks only fire when the raw lookup fails, so well-formed
    tickers (AAPL, META, TSLA) take the fast path.
    """
    out = [ticker]
    if len(ticker) >= 3 and ticker.isalpha() and ticker[-1] in "ABCD":
        out.append(f"{ticker[:-1]}-{ticker[-1]}")
        out.append(ticker[:-1])
    return out


def lookup_company_cik(ticker: str) -> str | None:
    """Resolve a ticker → CIK via edgartools, trying class-suffix variants
    on failure. Returns the normalized CIK or None when no variant resolves.
    """
    for candidate in _ticker_variants(ticker.upper()):
        try:
            company = edgar.Company(candidate)
            return _normalize_cik(company.cik)
        except Exception as e:  # CompanyNotFoundError + transient network
            log.debug("CIK lookup failed for %s: %s", candidate, e)
            continue
    return None


def _resolve_issuer_ciks(holdings: list[dict], ticker_index: dict[str, str]) -> None:
    """Populate h['issuer_cik'] in-place for every holding that has a ticker.

    Resolution order per ticker: cached index (free) -> edgar.Company (one
    EDGAR call, with class-suffix retry). In-memory dedupe within a run;
    misses are cached so a ticker that doesn't resolve is only attempted
    once.
    """
    runtime_cache: dict[str, str | None] = {}
    hits, misses, network = 0, 0, 0

    for h in holdings:
        ticker = h.get("ticker")
        if not ticker:
            h["issuer_cik"] = None
            continue
        key = ticker.upper()
        if key in runtime_cache:
            h["issuer_cik"] = runtime_cache[key]
            continue
        cik = ticker_index.get(key)
        if cik:
            runtime_cache[key] = cik
            h["issuer_cik"] = cik
            hits += 1
            continue
        cik = lookup_company_cik(key)
        if cik:
            network += 1
        else:
            misses += 1
        runtime_cache[key] = cik
        h["issuer_cik"] = cik

    log.info(
        "CIK resolution: %d index hits, %d EDGAR fetches, %d unresolved",
        hits, network, misses,
    )


_SERIES_BLOCK_RE = re.compile(r"<SERIES>(.*?)</SERIES>", re.DOTALL)
_CLASS_BLOCK_RE = re.compile(r"<CLASS-CONTRACT>(.*?)</CLASS-CONTRACT>", re.DOTALL)
_SERIES_ID_RE = re.compile(r"<SERIES-ID>\s*(\S+)")
_CLASS_ID_RE = re.compile(r"<CLASS-CONTRACT-ID>\s*(\S+)")
_CLASS_NAME_RE = re.compile(r"<CLASS-CONTRACT-NAME>\s*([^\n<]+)")
_CLASS_TICKER_RE = re.compile(r"<CLASS-CONTRACT-TICKER-SYMBOL>\s*(\S+)")


def parse_share_classes(filing, series_id: str) -> list[dict]:
    """Extract {class_id, ticker, name} for `series_id` from the SGML header.

    Multi-series registrants (e.g., Vanguard) carry several `<SERIES>` blocks
    in the same SGML header; we only return classes for the requested
    series. Returns an empty list when the header is absent or doesn't
    mention `series_id`.
    """
    header = getattr(filing, "header", None)
    text = getattr(header, "text", None) if header else None
    if not text:
        return []

    for series_block in _SERIES_BLOCK_RE.findall(text):
        sid_match = _SERIES_ID_RE.search(series_block)
        if not sid_match or sid_match.group(1).strip() != series_id:
            continue
        classes = []
        for class_block in _CLASS_BLOCK_RE.findall(series_block):
            cid = _CLASS_ID_RE.search(class_block)
            name = _CLASS_NAME_RE.search(class_block)
            ticker = _CLASS_TICKER_RE.search(class_block)
            if not cid:
                continue
            classes.append(
                {
                    "class_id": cid.group(1).strip(),
                    "ticker": ticker.group(1).strip() if ticker else None,
                    "name": name.group(1).strip() if name else None,
                }
            )
        return classes
    return []


def find_latest(cik: str, series_id: str):
    """Locate the latest NPORT-P filing for a series without downloading XML.

    Returns the edgartools EntityFiling object plus a small metadata dict
    (accession_no, period_of_report, source_url). The caller can use this
    metadata to decide whether to skip the heavy parse step.
    """
    edgar.set_identity(user_agent())

    series = edgar.find(series_id)
    if series is None or getattr(series, "series_id", None) != series_id:
        raise LookupError(f"No FundSeries found for series_id={series_id}")

    # FundSeries.get_filings returns all of the registrant CIK's NPORT-P
    # filings, not just this series'. Scan headers (cheap, no XML download)
    # for the SGML SERIES-ID tag to find the latest matching filing.
    filings = series.get_filings(form="NPORT-P")
    target_tag = f"<SERIES-ID>{series_id}"
    filing = None
    for f in filings:
        if target_tag in f.header.text:
            filing = f
            break
    if filing is None:
        raise LookupError(f"No NPORT-P filings for series_id={series_id}")

    meta = {
        "accession_no": filing.accession_no,
        "period_of_report": _iso_date(filing.period_of_report),
        "source_url": filing.filing_url,
    }
    return filing, meta


def parse(filing, ticker_index_path: str | Path | None = None) -> dict:
    """Download and parse a located NPORT-P filing. Returns the intermediate
    dict consumed by `transform.to_json1`.

    `ticker_index_path` optionally points at a `by_ticker.json` from the
    securities registry (`<SECURITIES_REPO>/by_ticker.json`). When present,
    it lets us resolve ticker → issuer CIK without an EDGAR call for
    issuers we've already seen.
    """
    report = filing.obj()

    gi = report.general_info
    fi = report.fund_info

    as_of = _iso_date(gi.rep_period_date)
    fund = {
        "name": gi.series_name,
        "series_id": gi.series_id,
        "series_lei": gi.series_lei,
        "registrant_cik": str(gi.cik) if gi.cik else None,
        "registrant_name": gi.name,
        "registrant_lei": getattr(gi, "reg_lei", None),
        "as_of": as_of,
        "total_assets_usd": _f(fi.total_assets),
        "total_liabs_usd": _f(fi.total_liabilities),
        "net_assets_usd": _f(fi.net_assets),
        "cash_not_in_portfolio_usd": _f(fi.cash_not_report_in_cor_d),
        "is_final_filing": bool(gi.is_final_filing),
        "interest_rate_risk": _interest_rate_risk(fi.current_metrics),
        "credit_spread_risk": _credit_spread_risk(fi),
        "monthly_returns": _monthly_returns(fi.return_info, as_of),
        "share_classes": parse_share_classes(filing, gi.series_id),
    }

    # First pass: build holdings carrying CUSIP in-memory for the fallback step.
    holdings = []
    for inv in report.investments:
        isin = inv.isin or (
            inv.identifiers.get("isin") if isinstance(inv.identifiers, dict) else None
        )
        payoff = (inv.payoff_profile or "").lower() or None
        holdings.append(
            {
                "name": inv.name,
                "lei": inv.lei,
                "ticker": inv.ticker,
                "isin": isin,
                "_cusip": inv.cusip,
                "pct_val": _f(inv.pct_value),
                "val_usd": _f(inv.value_usd),
                "balance": _f(inv.balance),
                "units": inv.units,
                "asset_cat_code": inv.asset_category,
                "issuer_cat_code": inv.issuer_category,
                "inv_country": inv.investment_country,
                "cur_cd": inv.currency_code,
                "payoff_profile": payoff,
                "is_restricted": bool(inv.is_restricted_security),
                "is_fair_valued": str(inv.fair_value_level) == "3"
                if inv.fair_value_level is not None
                else False,
                "fair_value_level": str(inv.fair_value_level)
                if inv.fair_value_level is not None
                else None,
                "debt": _debt_block(inv.debt_security),
            }
        )

    # Fallback: OpenFIGI for any holding edgartools couldn't resolve and
    # which lacks an ISIN natively. Skip the placeholder CUSIPs edgartools
    # also treats as non-identifiers (foreign / cash / internal positions).
    unresolved = [
        h["_cusip"] for h in holdings
        if not h["ticker"] and not h["isin"]
        and h["_cusip"] and h["_cusip"].strip().upper() not in _PLACEHOLDER_CUSIPS
    ]
    if unresolved:
        log.info("falling back to OpenFIGI for %d unresolved CUSIPs", len(unresolved))
        figi_map = openfigi.map_cusips(unresolved)
        recovered = 0
        for h in holdings:
            if h["ticker"] or h["isin"]:
                continue
            mapped = figi_map.get(h["_cusip"]) if h["_cusip"] else None
            if mapped:
                h["ticker"] = mapped.get("ticker")
                h["isin"] = mapped.get("isin")
                recovered += 1
        log.info("OpenFIGI recovered %d/%d holdings", recovered, len(unresolved))

    # Drop CUSIPs before returning — they never cross out of this module.
    for h in holdings:
        h.pop("_cusip", None)

    # Resolve each holding's ticker to its issuer CIK so the snapshot can
    # reference the securities registry. Holdings without a ticker stay
    # null — the registry pipeline can try harder via LEI/name later.
    ticker_index = _load_ticker_index(ticker_index_path)
    _resolve_issuer_ciks(holdings, ticker_index)

    return {"fund": fund, "holdings": holdings}


def fetch_latest(cik: str, series_id: str, ticker_index_path: str | Path | None = None) -> dict:
    """Convenience wrapper: find + parse + attach filing metadata."""
    filing, meta = find_latest(cik, series_id)
    parsed = parse(filing, ticker_index_path=ticker_index_path)
    parsed["filing"] = {
        "accession_no": meta["accession_no"],
        "source_url": meta["source_url"],
    }
    return parsed
