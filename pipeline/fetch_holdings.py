"""CLI entry: fetch one fund's latest N-PORT and emit a json1 holdings file."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import nport, transform


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a json1 holdings file for one fund.")
    parser.add_argument("--cik", required=True, help="SEC CIK (e.g. 0000036405)")
    parser.add_argument("--series-id", required=True, help="SEC series ID (e.g. S000002848)")
    parser.add_argument(
        "--out",
        default="data/holdings",
        help="Output directory (default: data/holdings)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("fetch_holdings")

    log.info("fetching latest NPORT-P for series=%s", args.series_id)
    parsed = nport.fetch_latest(args.cik, args.series_id)
    log.info(
        "filing %s (as_of %s), %d holdings",
        parsed["filing"]["accession_no"],
        parsed["fund"]["as_of"],
        len(parsed["holdings"]),
    )

    output = transform.to_json1(parsed)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.series_id}.json"
    out_path.write_text(json.dumps(output, indent=2))
    log.info("wrote %s (%d holdings)", out_path, len(output["holdings"]))

    return 0


if __name__ == "__main__":
    sys.exit(main())
