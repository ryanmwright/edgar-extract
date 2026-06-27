"""Run the pipeline for one shard of config/funds.json.

Used by the GitHub Actions matrix to parallelize across funds. Per-fund
failures are logged but don't fail the shard — partial results are
still committed by the downstream job.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import fetch_holdings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Process one shard of funds.json")
    p.add_argument("--funds-file", default="config/funds.json")
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--total-shards", type=int, required=True)
    p.add_argument("--out", default="data/snapshots")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("run_shard")

    funds = json.loads(Path(args.funds_file).read_text())
    my_funds = [
        f for i, f in enumerate(funds) if i % args.total_shards == args.shard
    ]
    log.info(
        "shard %d/%d: %d of %d funds to process",
        args.shard,
        args.total_shards,
        len(my_funds),
        len(funds),
    )

    failures: list[tuple[dict, str]] = []
    for f in my_funds:
        label = f.get("ticker") or f["series_id"]
        try:
            fetch_holdings.main(
                ["--cik", f["cik"], "--series-id", f["series_id"], "--out", args.out]
            )
        except Exception as e:
            log.exception("failed to process %s (%s)", label, f["series_id"])
            failures.append((f, str(e)))

    ok = len(my_funds) - len(failures)
    log.info("shard %d done: %d ok, %d failed", args.shard, ok, len(failures))
    for f, err in failures:
        log.warning("  failed: %s — %s", f.get("ticker") or f["series_id"], err)

    return 0


if __name__ == "__main__":
    sys.exit(main())
