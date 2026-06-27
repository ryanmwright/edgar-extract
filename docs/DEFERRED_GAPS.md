# Deferred Data Gaps

Things we know are missing from the schema v0.4 output and chose not to
build yet. Listed in rough priority order for a frontend that wants
transaction import, bond/REIT support, and richer fund-level metadata.

---

## 1. Sector / industry classification — **RESOLVED** (schema v0.3)

Sector and industry data are now available, but the architecture diverged
from the original "embed `sic_code` + `sector` per holding" plan. Instead:

- Per-holding `issuer_cik` field added to fund snapshots (schema v0.3).
- A separate **securities registry** (`vizfolio/securities-extracts`)
  publishes per-issuer records keyed by CIK with `sic`, `sic_description`
  (≈ industry, ~1000 categories), `sector` (coarse GICS-style, 11
  buckets), country, exchanges, and tickers.
- Sector derivation: `config/sic_to_sector.json` (~430 SIC codes mapped)
  with per-CIK overrides in `config/sector_overrides.json` for SIC vs
  GICS mismatches (Alphabet/Meta/Disney → Communication Services;
  UnitedHealth → Health Care).
- Class-suffix recovery: tickers like `BRKA` / `CWENA` that N-PORT
  strips dashes from are resolved via dashed and bare-prefix variants.

**Coverage at last build:** 99.9% sector + SIC across 3,396 issuers;
98.2% of VTI holdings carry a resolved `issuer_cik`. The residual is
edgartools' bundled ticker→CIK parquet gaps for some smaller / newer
issuers (HOLX, CTRA, EHAB, AL, etc.) — would need a separate ticker
augmentation map or name-based EDGAR search to close further.

**Why not embed sector per-holding (the original plan)?** A separate
issuer-keyed registry doubles as the dataset for *standalone stock*
positions — not just fund holdings. It also future-proofs gaps #2 and
#3 below (share-class metadata and expense ratios are also per-issuer
slow-changing data, the same shape). See `pipeline/securities/` and
the README for build mechanics.

---

## 2. Share class detail (per-class metadata) — **RESOLVED** (schema v0.4)

`fund.share_classes[]` now ships with every snapshot, populated from
the N-PORT SGML header's `SERIES-AND-CLASSES-CONTRACTS-DATA` block.
Each entry carries `class_id`, `ticker`, `name`, and `expense_ratio`
(see gap #3 below for the expense source).

```json
"share_classes": [
  { "class_id": "C000007808", "ticker": "VTI",   "name": "ETF Shares",      "expense_ratio": 0.0003 },
  { "class_id": "C000007806", "ticker": "VTSAX", "name": "Admiral Shares",  "expense_ratio": 0.0004 },
  ...
]
```

Implementation: `pipeline/nport.parse_share_classes` regex-parses the
SGML header (filing data we were already reading to filter by series).
No extra HTTP call.

**Still deferred for this slot:** `inception_date` and
`minimum_investment`. edgartools exposes both via `Prospectus497K`
(`PerformanceReturn.inception_date`, `min_investments` dict) but
binding them back to `class_id` is messier than the expense ratio case
and wasn't required for the initial frontend cut.

---

## 3. Expense ratio — **RESOLVED** (schema v0.4)

`share_classes[].expense_ratio` is filled from the latest 497K summary
prospectus per series. Pipeline lives in `pipeline/expenses.py`.

**Resolution flow.** Multi-series registrants (Vanguard) file separate
497Ks per share-class subset on the same day rather than one
comprehensive prospectus, so a single-filing lookup misses most
classes. Instead `expenses.fetch_expense_ratios` walks 497K filings in
reverse-chronological order, aggregates `{class_id: expense_ratio}`,
and stops when every class for the series is covered. Capped at 540
days lookback / 30 filings to bound runtime on funds where some class
never had a 497K. Prefers `net_expenses` (post-waiver) and falls back
to `total_annual_expenses`.

**Coverage at last build (Vanguard VTI + BND):** 6/6 classes for both
funds, matching the rates Vanguard publishes on its public site. 497K
HTML parsing is handled entirely by edgartools'
`Prospectus497K.from_filing`.

`fund.fees_source_filings[]` lists every 497K that contributed an
expense ratio, with `{accession_no, filing_date, source_url}`.

**Cadence.** Expense ratios refresh whenever a new NPORT-P is filed
(quarterly) — same skip-if-exists logic as the snapshot. Mid-quarter
497K amendments aren't picked up until the next quarter's N-PORT.

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
