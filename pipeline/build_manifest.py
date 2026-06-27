"""Build data/funds.json — the runtime manifest of available funds.

For each fund in config/funds.json, scans data/snapshots/{series_id}/
for snapshots, picks the most recent, and emits an entry with:
- series_id, name, registrant_cik
- tickers[]: every share-class ticker for the series (from SEC's
  company_tickers_mf.json — covers VTI + VTSAX + VITSX all mapping
  to S000002848)
- latest_period: ISO date of the most recent snapshot
- latest_accession: accession_no of that snapshot

Consumers (the .NET app, ad-hoc CLI users) read funds.json at startup
and construct snapshot URLs as
  {series_id}/{latest_period}.json.gz
relative to the holdings root.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import nport

MF_TICKERS_URL = "https://www.sec.gov/files/company_tickers_mf.json"
SCHEMA_VERSION = "1"

log = logging.getLogger(__name__)


def _fetch_mf_tickers() -> dict[str, list[str]]:
    """Return {series_id: [ticker, ...]} from SEC's mutual fund index."""
    resp = requests.get(
        MF_TICKERS_URL,
        headers={"User-Agent": nport.user_agent()},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    fields = data["fields"]
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in data["data"]:
        rec = dict(zip(fields, row))
        sid = rec.get("seriesId")
        sym = rec.get("symbol")
        if sid and sym:
            grouped[sid].append(sym)
    return grouped


def _latest_snapshot(series_dir: Path) -> tuple[str, str | None] | None:
    """Find the newest snapshot file in a series dir. Returns (period, accession)."""
    if not series_dir.is_dir():
        return None
    snapshots = sorted(
        series_dir.glob("*.json.gz"), key=lambda p: p.name, reverse=True
    )
    if not snapshots:
        return None
    newest = snapshots[0]
    period = newest.name.removesuffix(".json.gz")
    accession: str | None = None
    try:
        with gzip.open(newest, "rt") as f:
            data = json.load(f)
        accession = data.get("fund", {}).get("source_filing")
    except (OSError, json.JSONDecodeError, KeyError):
        log.warning("could not read accession from %s", newest)
    return period, accession


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build the runtime funds manifest")
    p.add_argument("--seed", default="config/funds.json")
    p.add_argument("--snapshots", default="data/snapshots")
    p.add_argument("--out", default="data/funds.json")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    seed = json.loads(Path(args.seed).read_text())
    log.info("loaded %d tracked funds from %s", len(seed), args.seed)

    log.info("fetching SEC mutual-fund ticker index")
    by_series = _fetch_mf_tickers()
    log.info("%d series in SEC's MF index", len(by_series))

    snapshots_root = Path(args.snapshots)
    funds_out: list[dict] = []
    missing = 0
    for f in seed:
        sid = f["series_id"]
        latest = _latest_snapshot(snapshots_root / sid)
        if latest is None:
            log.warning(
                "no snapshots on disk for %s (%s) — skipping",
                f.get("ticker") or sid,
                sid,
            )
            missing += 1
            continue
        period, accession = latest

        tickers = set(by_series.get(sid, []))
        if f.get("ticker"):
            tickers.add(f["ticker"])

        funds_out.append(
            {
                "series_id": sid,
                "name": f.get("name"),
                "registrant_cik": f.get("cik"),
                "tickers": sorted(tickers),
                "latest_period": period,
                "latest_accession": accession,
            }
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "funds": sorted(funds_out, key=lambda x: x["series_id"]),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2))
    log.info(
        "wrote %s (%d funds, %d skipped — no snapshots)",
        out,
        len(funds_out),
        missing,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
