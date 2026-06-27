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

Output: `data/holdings/{series_id}/{period_of_report}.json.gz` — one
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

### Module layout

- `pipeline/nport.py` — edgartools wrapper: find + parse NPORT-P filings.
- `pipeline/mappings.py` — N-PORT enum codes → schema strings.
- `pipeline/transform.py` — intermediate dict → json output.
- `pipeline/fetch_holdings.py` — CLI for one fund.
- `pipeline/run_shard.py` — CLI for one shard of `funds.json` (used by CI).
- `pipeline/build_manifest.py` — emits `data/funds.json`.

Edgartools resolves tickers from CUSIPs internally; CUSIPs are not propagated
past `nport.py` and are never written to disk.
