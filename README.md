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
identifying the requester. Export it as an environment variable before
running:

```bash
export EDGAR_USER_AGENT="Fund X-Ray your-email@example.com"

nix develop -c python -m pipeline.fetch_holdings \
  --cik 0000036405 \
  --series-id S000002848
```

Output: `data/holdings/S000002848.json` (VTI/VTSAX — same series).

### Module layout

- `pipeline/nport.py` — edgartools wrapper: fetch latest NPORT-P and produce
  the intermediate dict.
- `pipeline/mappings.py` — N-PORT enum codes → schema strings.
- `pipeline/transform.py` — intermediate dict → json1 output.
- `pipeline/fetch_holdings.py` — CLI orchestrator.

Edgartools resolves tickers from CUSIPs internally; CUSIPs are not propagated
past `nport.py` and are never written to disk.
