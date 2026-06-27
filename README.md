# Fund X-Ray

Local-first portfolio X-ray tool. See `context/START.md` for the full design.

## Pipeline (v0)

Generates a json1-shaped holdings file for one fund by walking the latest
SEC N-PORT-P filing via [edgartools](https://github.com/dgunning/edgartools).

### Install

```bash
nix develop      # creates .venv with edgartools (uses uv under the hood)
```

### Run

SEC EDGAR requires every request to carry a descriptive `User-Agent`
identifying the requester. Copy the example env file once:

```bash
cp .env.example .env
# edit .env and set EDGAR_USER_AGENT to your contact info
```

`.env` is gitignored and the flake's `shellHook` auto-sources it on
`nix develop`, so subsequent runs need no extra steps:

```bash
nix develop -c python -m pipeline.fetch_holdings \
  --cik 0000036405 \
  --series-id S000002848
```

Output: `data/snapshots/{series_id}/{period_of_report}.json.gz` — one
gzipped JSON snapshot per fund per quarter, keyed by N-PORT
`period_of_report`. Re-runs are no-ops unless EDGAR has a newer filing
accession for the same period (e.g. NPORT-P/A amendments).

### Building the manifest

After (re)running the pipeline, build the runtime manifest that
consumers use to look up funds and find their latest snapshot:

```bash
nix develop -c python -m pipeline.build_manifest
```

Output: `data/funds.json` — schema-versioned manifest with one entry
per fund:

```json
{
  "series_id": "S000002848",
  "name": "Vanguard Total Stock Market Index Fund",
  "tickers": ["VTI", "VTSAX", "VITSX", ...],
  "latest_period": "2026-03-31",
  "latest_accession": "0000036405-26-000323"
}
```

Consumers read `funds.json` once at startup and construct snapshot URLs
as `{series_id}/{latest_period}.json.gz`.

### Building the securities registry

Fund snapshots reference each holding's issuer by `issuer_cik` rather
than embedding sector or industry data inline. Issuer reference data
(name, SIC, sector, tickers, exchanges, …) lives in a separate
registry that the fund pipeline points at via `--ticker-index`.

Build/refresh the registry after the fund pipeline runs:

```bash
nix develop -c python -m pipeline.securities.build_registry \
  --fund-snapshots data/snapshots \
  --seed-tickers config/seed_tickers.json \
  --out data-securities
```

Sources of CIKs to enrich:
1. Every `issuer_cik` that appears in any fund snapshot under
   `--fund-snapshots`.
2. Every ticker in `--seed-tickers` (resolved via `edgar.Company`) — use
   this to publish reference data for issuers no tracked fund holds.
3. Every CIK already in the registry. Records older than
   `--refresh-days` (default 90) are re-fetched; younger ones are kept.

Output layout under `--out`:

```
by_cik/
  0000320193.json     # one file per issuer
  0000726728.json
  …
by_ticker.json        # {TICKER: CIK} index, regenerated from by_cik/
```

Per-issuer record:

```json
{
  "cik": "0000320193",
  "name": "Apple Inc.",
  "sic": "3571",
  "sic_description": "Electronic Computers",
  "sector": "Information Technology",
  "country": "US",
  "tickers": ["AAPL"],
  "exchanges": ["Nasdaq"],
  "entity_type": "operating",
  "source": {"edgar_fetched_at": "2026-06-27T…"},
  "schema_version": "0.1"
}
```

Sector mapping is driven by `config/sic_to_sector.json` —
edit that file to refine sector buckets or extend coverage.

#### Wiring the registry back into the fund pipeline

Once `by_ticker.json` exists, pass it to `fetch_holdings` so cached
ticker → CIK lookups skip EDGAR:

```bash
nix develop -c python -m pipeline.fetch_holdings \
  --cik 0000036405 \
  --series-id S000002848 \
  --ticker-index data-securities/by_ticker.json
```

The path defaults to `data-securities/by_ticker.json` already, so the
CLI works without the flag once the registry is built. A missing
index is fine — every unknown ticker just hits EDGAR once.

### Module layout

- `pipeline/nport.py` — edgartools wrapper: find + parse NPORT-P filings.
- `pipeline/mappings.py` — N-PORT enum codes → schema strings; SIC → sector.
- `pipeline/transform.py` — intermediate dict → json output.
- `pipeline/fetch_holdings.py` — CLI for one fund.
- `pipeline/run_shard.py` — CLI for one shard of `funds.json` (used by CI).
- `pipeline/build_manifest.py` — emits `data/funds.json`.
- `pipeline/securities/registry.py` — single-CIK enrichment via `edgar.Company`.
- `pipeline/securities/build_registry.py` — CLI: build/refresh the registry.

All editable inputs live in `config/`:
- `config/funds.json` — seed list of tracked funds (cik + series_id).
- `config/sic_to_sector.json` — curated SIC → coarse sector map.
- `config/seed_tickers.json` — extra tickers to publish in the registry.

Edgartools resolves tickers from CUSIPs internally; CUSIPs are not propagated
past `nport.py` and are never written to disk.
