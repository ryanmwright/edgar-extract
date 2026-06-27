"""Fetch and parse 497K (summary prospectus) filings for expense ratios.

497K filings carry the "Fees and Expenses" table that N-PORT lacks.
edgartools' `Prospectus497K` already extracts per-class fee data from
the HTML; we just need to find the right filings and convert percentages
to fractions to match our existing schema convention.

Multi-series registrants commonly file 497Ks scoped to a subset of
share classes — Vanguard, for example, publishes one 497K per
class-group on the same day rather than a single comprehensive
prospectus per series. To get coverage across every class in a series
we walk filings in reverse-chronological order and merge results until
every known class has an expense ratio, capped by a freshness bound so
we don't trawl indefinitely on funds where some class never gets one.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import edgar

from .nport import _iso_date, user_agent

log = logging.getLogger(__name__)

# How far back to look for 497Ks and how many filings to inspect at most.
# Vanguard cycles prospectuses annually (April), so 18 months covers a
# full cycle with margin; the filing cap is a safety belt for pathological
# registrants.
_LOOKBACK_DAYS = 540
_MAX_FILINGS = 30


def _to_fraction(pct) -> float | None:
    """Convert a 497K percentage to a fraction.

    edgartools' `ShareClassFees` stores values in percent units
    (`Decimal('0.03')` for 3 bps); divide by 100 to match `weight`.
    """
    if pct is None:
        return None
    try:
        return float(Decimal(pct) / Decimal(100))
    except (TypeError, ValueError):
        return None


def _extract_from_filing(filing) -> dict[str, dict]:
    """Parse a single 497K → `{class_id: {expense_ratio, ticker}}`.

    Prefers `net_expenses` (post-waiver, what investors actually pay);
    falls back to `total_annual_expenses` when net is missing.
    """
    try:
        prospectus = filing.obj()
    except Exception as e:
        log.warning("497K %s: filing.obj() failed: %s", filing.accession_no, e)
        return {}

    if prospectus is None or not hasattr(prospectus, "share_classes"):
        return {}

    out: dict[str, dict] = {}
    for sc in prospectus.share_classes:
        if not sc.class_id:
            continue
        pct = sc.net_expenses if sc.net_expenses is not None else sc.total_annual_expenses
        ratio = _to_fraction(pct)
        if ratio is None:
            continue
        out[sc.class_id] = {"expense_ratio": ratio, "ticker": sc.ticker}
    return out


def fetch_expense_ratios(
    cik: str, series_id: str, class_ids: list[str] | None = None
) -> tuple[dict[str, dict], list[dict]]:
    """Walk 497K filings for `series_id` and aggregate per-class expense ratios.

    Stops early when every id in `class_ids` is covered. When `class_ids`
    is None, keeps walking until the lookback / filing-count caps hit
    (collects whatever it can find).

    Returns `(by_class_id, sources)` where `sources` is a list of
    `{accession_no, filing_date, source_url}` for every filing that
    contributed at least one expense ratio.
    """
    edgar.set_identity(user_agent())

    series = edgar.find(series_id)
    if series is None or getattr(series, "series_id", None) != series_id:
        log.warning("no FundSeries for series_id=%s — no expense ratios", series_id)
        return {}, []

    cutoff = date.today() - timedelta(days=_LOOKBACK_DAYS)
    needed: set[str] | None = set(class_ids) if class_ids else None
    target_tag = f"<SERIES-ID>{series_id}"

    by_class: dict[str, dict] = {}
    sources: list[dict] = []
    inspected = 0

    for f in series.get_filings(form="497K"):
        if inspected >= _MAX_FILINGS:
            break
        filing_date = getattr(f, "filing_date", None)
        if filing_date and isinstance(filing_date, date) and filing_date < cutoff:
            break
        if target_tag not in f.header.text:
            continue

        inspected += 1
        extracted = _extract_from_filing(f)
        added = {cid: data for cid, data in extracted.items() if cid not in by_class}
        if not added:
            continue
        by_class.update(added)
        sources.append(
            {
                "accession_no": f.accession_no,
                "filing_date": _iso_date(filing_date),
                "source_url": f.filing_url,
            }
        )
        if needed is not None and needed.issubset(by_class):
            break

    if needed:
        missing = sorted(needed - set(by_class))
        if missing:
            log.info(
                "series=%s: no 497K expense data for %d/%d classes: %s",
                series_id, len(missing), len(needed), missing,
            )

    return by_class, sources
