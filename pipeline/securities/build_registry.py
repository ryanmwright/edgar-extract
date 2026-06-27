"""CLI: build/refresh the issuer-keyed securities registry.

Sources of CIKs:
  1. Every `issuer_cik` that appears in any fund snapshot under
     `--fund-snapshots` (scans `*.json.gz` recursively).
  2. Every ticker in `--seed-tickers`, resolved via edgar.Company(ticker)
     into a CIK so the registry covers holdings no tracked fund owns.
  3. Every CIK already present in the on-disk registry — refreshed if
     older than --refresh-days, kept as-is otherwise.

Output layout (at `--out`):
    by_cik/{CIK}.json     one file per issuer
    by_ticker.json        {TICKER: CIK} index, regenerated from all by_cik files

Failures (unresolvable tickers, EDGAR errors) are logged and skipped; the
registry still publishes whatever did succeed.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from pathlib import Path

import edgar

from ..nport import _normalize_cik, lookup_company_cik, user_agent
from . import registry

log = logging.getLogger("build_registry")


def _read_gz(path: Path) -> dict | None:
    try:
        with gzip.open(path, "rt") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("could not read %s: %s", path, e)
        return None


def _scan_snapshots(snapshots_dir: Path) -> tuple[set[str], dict[str, set[str]]]:
    """Walk fund snapshots. Returns (cik_set, ticker_to_ciks).

    `ticker_to_ciks` lets us notice when the same ticker maps to multiple
    CIKs across funds (shouldn't happen, but log if it does).
    """
    ciks: set[str] = set()
    by_ticker: dict[str, set[str]] = {}

    if not snapshots_dir.exists():
        log.warning("snapshots dir %s does not exist — nothing to scan", snapshots_dir)
        return ciks, by_ticker

    files = list(snapshots_dir.rglob("*.json.gz"))
    log.info("scanning %d snapshot files under %s", len(files), snapshots_dir)
    for path in files:
        data = _read_gz(path)
        if not data:
            continue
        for h in data.get("holdings", []) or []:
            cik = _normalize_cik(h.get("issuer_cik"))
            ticker = (h.get("ticker") or "").upper().strip() or None
            if cik:
                ciks.add(cik)
                if ticker:
                    by_ticker.setdefault(ticker, set()).add(cik)
    return ciks, by_ticker


def _load_seed_tickers(path: Path) -> list[str]:
    if not path.exists():
        log.info("no seed-tickers file at %s", path)
        return []
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("seed-tickers file %s is invalid: %s", path, e)
        return []
    tickers = raw.get("tickers") if isinstance(raw, dict) else raw
    if not isinstance(tickers, list):
        return []
    return [t.upper().strip() for t in tickers if isinstance(t, str) and t.strip()]


def _resolve_seed_tickers(tickers: list[str]) -> set[str]:
    out: set[str] = set()
    for ticker in tickers:
        cik = lookup_company_cik(ticker)
        if cik:
            out.add(cik)
        else:
            log.warning("seed ticker %s: unresolved", ticker)
    return out


def _load_existing_records(by_cik_dir: Path) -> dict[str, dict]:
    if not by_cik_dir.exists():
        return {}
    out: dict[str, dict] = {}
    for path in by_cik_dir.glob("*.json"):
        try:
            rec = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.warning("could not read existing record %s: %s", path, e)
            continue
        cik = _normalize_cik(rec.get("cik")) or path.stem
        out[cik] = rec
    return out


def _write_record(by_cik_dir: Path, record: dict) -> None:
    by_cik_dir.mkdir(parents=True, exist_ok=True)
    path = by_cik_dir / f"{record['cik']}.json"
    with open(path, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")


def _build_ticker_index(records: dict[str, dict]) -> dict[str, str]:
    """Build {ticker: cik}. When the same ticker shows on multiple records,
    prefer the one with a US listing.
    """
    out: dict[str, str] = {}
    collisions: list[tuple[str, str, str]] = []
    for cik, rec in records.items():
        for ticker in rec.get("tickers") or []:
            t = ticker.upper().strip()
            if not t:
                continue
            if t in out and out[t] != cik:
                # Prefer the record with a US country tag.
                prev_cik = out[t]
                prev = records.get(prev_cik, {})
                if rec.get("country") == "US" and prev.get("country") != "US":
                    out[t] = cik
                collisions.append((t, prev_cik, cik))
                continue
            out[t] = cik
    if collisions:
        log.warning("ticker collisions (%d): %s", len(collisions), collisions[:5])
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build the securities registry.")
    p.add_argument("--fund-snapshots", default="data-funds/snapshots",
                   help="Directory containing fund snapshot .json.gz files")
    p.add_argument("--seed-tickers", default="config/seed_tickers.json",
                   help="Optional list of extra tickers to enrich")
    p.add_argument("--out", default="data-securities",
                   help="Securities-repo root (writes by_cik/ and by_ticker.json)")
    p.add_argument("--refresh-days", type=int, default=90,
                   help="Re-fetch issuer records older than N days")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    out_root = Path(args.out)
    by_cik_dir = out_root / "by_cik"
    snapshots_dir = Path(args.fund_snapshots)
    seed_path = Path(args.seed_tickers)

    edgar.set_identity(user_agent())

    snap_ciks, _ = _scan_snapshots(snapshots_dir)
    log.info("found %d unique CIKs across fund snapshots", len(snap_ciks))

    seed_tickers = _load_seed_tickers(seed_path)
    log.info("loaded %d seed tickers", len(seed_tickers))
    seed_ciks = _resolve_seed_tickers(seed_tickers)
    log.info("resolved %d seed tickers → CIKs", len(seed_ciks))

    existing = _load_existing_records(by_cik_dir)
    log.info("loaded %d existing registry records", len(existing))

    # Universe: every CIK we've ever seen + freshly discovered.
    universe = set(existing.keys()) | snap_ciks | seed_ciks
    log.info("registry universe: %d CIKs", len(universe))

    enriched, refreshed, kept, normalized, failed = 0, 0, 0, 0, 0
    for cik in sorted(universe):
        prev = existing.get(cik)
        if prev and not registry.is_stale(prev, args.refresh_days):
            # Apply pure-local transforms (sector re-derivation, dedupe)
            # so config/logic changes propagate without re-fetching EDGAR.
            if registry.normalize(prev):
                _write_record(by_cik_dir, prev)
                normalized += 1
            kept += 1
            continue
        record = registry.enrich(cik)
        if not record:
            failed += 1
            # Keep stale record on disk rather than dropping it.
            continue
        _write_record(by_cik_dir, record)
        existing[cik] = record
        if prev:
            refreshed += 1
        else:
            enriched += 1

    log.info(
        "enrichment: %d new, %d refreshed, %d kept (%d normalized), %d failed",
        enriched, refreshed, kept, normalized, failed,
    )

    ticker_index = _build_ticker_index(existing)
    index_path = out_root / "by_ticker.json"
    out_root.mkdir(parents=True, exist_ok=True)
    with open(index_path, "w") as f:
        json.dump(ticker_index, f, indent=2, sort_keys=True)
        f.write("\n")
    log.info("wrote ticker index: %s (%d entries)", index_path, len(ticker_index))

    return 0


if __name__ == "__main__":
    sys.exit(main())
