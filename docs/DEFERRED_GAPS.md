# Deferred Data Gaps

Things we know are missing from the schema v0.2 output and chose not to
build yet. Listed in rough priority order for a frontend that wants
sector breakdown, transaction import, and bond/REIT support.

---

## 1. Sector / industry classification (biggest gap)

**Problem.** N-PORT carries no sector or industry data. The richest
classification we expose today is `asset_cat` (equity/debt/derivative)
and `issuer_cat` (corp/sovereign/muni/other). For a per-stock industry
breakdown — including telling REITs apart from non-REIT equities — we
need an external concordance.

**Options:**

| Approach | Coverage | Cost | Maintenance |
|---|---|---|---|
| SEC SIC code via issuer CIK lookup | Most US equities | Free, pipeline-side enrichment | Per-issuer EDGAR call, cacheable |
| Curated `sic_to_sector.json` in repo | SIC → GICS/coarse sector | Free, one-time research | Manual; SIC is stable |
| OpenFIGI extended fields (`marketSector`) | Already partly used | Free (rate-limited) | None |
| Wikidata `P452` (industry) | Spotty | Free, slow | None |
| Commercial feed (Polygon, FMP) | Best | $$ | Vendor lock |

**Recommendation.** Combine 1+2: at pipeline time, look up each issuer's
CIK from edgartools (`Company(ticker).sic`), then map the 4-digit SIC
code through a small curated `sic_to_sector.json` to a coarse sector
(Financials, Energy, Real Estate, …). REITs fall out for free (SIC 6798,
6799). Industry granularity below sector can come later via OpenFIGI's
`securityType` or a third party.

**Where it fits in the schema.** Add per-holding fields:
- `sic_code: "6798"` (the raw 4-digit code; null when unresolvable)
- `sector: "Real Estate"` (mapped, coarse)

The mapping table lives at `config/sic_to_sector.json` and ships
with the code.

---

## 2. Share class detail (per-class metadata)

We currently emit fund-level data only. A consumer who holds VTSAX vs.
VTI vs. VITSX gets the same JSON. That's correct for *holdings* (same
underlying portfolio) but wrong for:
- expense ratio (differs per class)
- inception date (differs per class)
- minimum investment (differs per class)
- monthly_returns: we *do* emit per-class returns, but the consumer
  has no class-id → ticker map without the manifest above

**What to add to the per-fund JSON:**
```json
"share_classes": [
  { "class_id": "C000007808", "ticker": "VTI", "expense_ratio": 0.0003 },
  { "class_id": "C000007806", "ticker": "VTSAX", "expense_ratio": 0.0004 },
  ...
]
```

**Source.** Class IDs and tickers come from the SGML header
(`SERIES-AND-CLASSES-CONTRACTS-DATA`) plus `company_tickers_mf.json`.
Expense ratio is **not in N-PORT** — see gap #4.

---

## 3. Expense ratio (separate filings)

**Problem.** N-PORT carries no expense data. The fund's prospectus
(N-1A) and annual updates (497K) do.

**Approach.** Add a separate pipeline step:
1. For each (cik, series_id) tracked, fetch the latest 497K filing.
2. Parse the "Fees and Expenses" table — annual fund operating expenses
   row gives net expense ratio per class.
3. Write into `share_classes[].expense_ratio` in the fund JSON.

497K is a standard SEC form; edgartools should support pulling and
parsing it, though the fees table layout varies across families. Plan
for ~80% auto-parse coverage and a manual override table for the rest.

---

## 4. Cash & residual reconciliation

**What we have.** `dropped_holdings_count` + `dropped_weight` for things
we couldn't identify, and `cash_not_in_portfolio_usd` (off-book cash).

**What's missing.**
- An explicit `cash_in_portfolio_weight` for cash *positions* the fund
  reports as holdings (Vanguard Market Liquidity Fund, repos,
  short-term treasuries) but which we currently classify as `other` or
  `debt` with no special flag.
- A `categorized_weight` = `1 - cash_pct - dropped_weight - unclassified`
  so the frontend can put together an honest "X-ray covered N% of fund"
  badge.

**Approach.** Don't drop cash holdings — emit them with
`asset_cat: "cash"` and an `is_cash: true` flag. Detect via:
- name pattern (`*Market Liquidity Fund*`, `*Government Money*`)
- asset_cat code `STIV` from N-PORT
- repurchase agreements

---

## 5. Derivatives detail

**Current state.** We emit `asset_cat: "derivative"` and that's it.

**For an X-ray:** for most US equity/bond funds, derivative weight is
tiny (<1%). The big exception: currency-hedged share classes (where
forwards/swaps can be 5-15% of NAV) and inverse/leveraged funds.

**What to add when the user wants this:** the full `derivative` block
from json4.json — category, notional_usd, expiration_date, counterparty,
and category-specific sub-objects (option / swap / fx_forward).
Edgartools exposes `inv.derivative_info`, `report.forwards_data`,
`report.swaps_data`, etc.

Defer until you hit a fund where the X-ray looks materially wrong
because the derivative book is invisible.

---

## 6. Securities lending

N-PORT reports per-holding lending data (on-loan amount, cash collateral,
non-cash collateral). Useful for a "fund risk profile" view but not for
the X-ray itself. Edgartools exposes this via `inv.security_lending`.

Add `is_on_loan`, `on_loan_value_usd`, `cash_collateral_usd`,
`non_cash_collateral_usd` per holding when needed.

---

## 7. Convertible bond reference instruments

If a bond is convertible, its `convertible_ref_instruments` lists the
underlying equity. Letting the X-ray pierce through convertibles into
their equity exposure is a meaningful enhancement for funds that hold a
lot of converts (high-yield, convertible-arb funds).

Add to the `debt` block when present:
```json
"convertible_ref_instruments": [
  { "isin": "US0378331005", "name": "Apple Inc",
    "ticker": "AAPL", "lei": "..." }
]
```

---

## 8. Fund flows (creations / redemptions)

`fund_info.monthly_flow1/2/3` is already on the parsed object — three
months of (sales, reinvestment, redemption) per class. Useful for
"fund health" signals (large net outflows ≈ stress).

Add `monthly_flows` array under `fund` parallel to `monthly_returns`.
Small, easy lift — about 20 LOC.

---

## 9. Miscellaneous schema hygiene

- **`lei: "N/A"`** is currently passed through as the string `"N/A"`
  for holdings whose LEI is missing. Normalize to `null`.
- **Currency case.** N-PORT sometimes carries `"usd"` vs `"USD"`.
  Force ISO 4217 upper.
- **`coupon_kind` enum.** We lowercase the N-PORT string verbatim. SEC
  uses {Fixed, Variable, Floating, None} — fine — but worth pinning the
  enum in docs.
- **`fair_value_level`.** Stored as string ("1"/"2"/"3"). Could be int.
  Picked string because some filings include letter-suffixed codes.
- **`as_of` vs `period_of_report`.** We use `as_of` (matches design
  doc). EDGAR also exposes `period_of_report` on the filing object — same
  thing, less ambiguous name. Consider renaming on a future schema bump.

---

## 10. Pipeline-level (operational)

- **Schema docs** — `schema.json` (JSON Schema or just human-readable
  reference). Today only the implementation defines the shape.
- **Pipeline version stamp** — emit `pipeline_version` alongside
  `schema_version` so we can tell *what code* generated a given file.
- **Historical-snapshot garbage collection** — at ~1 GB/year for 500
  funds, the repo grows linearly. Eventually you'll want a separate
  workflow to prune snapshots older than N years (default off for now).
