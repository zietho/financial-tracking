# Financial tracking

Local household finance pipeline: bank exports → DuckDB → one offline HTML report.
Background and decisions: [CONCEPT.md](CONCEPT.md).

## Quick start (demo data)

```bash
make setup                 # Python deps (uv) + Playwright Chromium
make sample                # fake household in sample/
make all DATA=sample       # build sample/out/finance.duckdb + sample/out/report.html
make open DATA=sample
make test
```

## With your own data

```bash
make init                  # creates accounts.csv, balances.csv, fx.csv, rules.csv, categories.csv, overrides.csv, raw/
```

1. Fill in `accounts.csv` (one line per account: `account,bank,name,owner,type,iban,format`).
   - `bank`: optional, just for grouping (and the `raw/<bank>/<account>/` folder if you like)
   - `type`: checking, savings, credit_card, investment, pension, loan, property, cash, reserved
     (`reserved` = set aside for a purpose, e.g. equity for a home: in net worth, not counted as cash)
   - `format`: a parser from `pipeline/parsers.py` (`enablebanking_json`, `bank_a_csv`, `revolut_csv`, `card_csv`, `camt053`), or empty
     for balance-only accounts (brokers, loan, property)
   - `iban`: also for balance-only accounts that receive transfers (flattex cash account, loan account), so
     those transfers are recognized
2. Drop exports into `raw/<account>/`, or grouped by bank as `raw/<bank>/<account>/` (any file name;
   overlapping date ranges are fine).
3. Add month-end balances to `balances.csv` (`date,account,balance,currency`; currency defaults to EUR).
   - Loans as **negative** numbers. A property value can be entered once a year; balances carry forward.
   - Accounts holding several currencies (Revolut) get one line per currency.
4. For non-EUR amounts you need monthly rates in `fx.csv` (`2026-10,USD,0.92` means 1 USD = 0.92 EUR).
   `make prices` fills them from the ECB (month-end reference rates; only the currency code is sent).
5. Optional, for share plans: `holdings.csv` (`date,account,symbol,units,status,currency`) instead of a
   balance. `status` is `owned` (counts toward net worth) or `unvested` (shown, not counted). If the broker
   only shows a value, use the currency as symbol (`2026-11-22,stock_alex,USD,19500,owned,USD`); for real share
   counts add month-end prices to `prices.csv` (`month,symbol,price,currency`).
6. `make prices all && make open`
7. Go through what's open, without editing files by hand:
   - `make categorize`: uncategorized transactions, largest first. `c <Category>` sets one transaction
     (overrides.csv), `a <Category>` adds a rule for every transaction from that payee (rules.csv).
   - `make review`: the "Anything unusual?" list. `r` marks one as checked (reviewed.csv) so it disappears.
   Then `make all` again.
8. Check the **Data quality** section and add rules for uncategorized payees, then `make all` again.

None of these files are committed (see `.gitignore`).

## Mapping transactions to categories

`rules.csv`: one line per rule, a case-insensitive regex on payee + memo + counterparty IBAN, first match wins.

```csv
pattern,category,transfer_account
BILLA PLUS,Shopping,                  # specific rules first
BILLA|SPAR|HOFER|LIDL,Groceries,
VISA Abrechnung,Transfer,visa         # transfer without IBAN → names the other account
```

- Categories, their group (for the money-flow chart), kind (income / expense / transfer) and fixed vs.
  variable live in `categories.csv`.
- Transfers between your own accounts are detected by **IBAN** automatically (from `accounts.csv`) and
  counted once, even when both sides are exported.
- One-off fixes: put the `tx_id` (shown in the report) into `overrides.csv` with a category.
- The `matched_by` column in the `tx` table says what categorized each transaction (override, iban, rule N).

## Special cases

- **Loans:** the installment is a transfer to the loan account (by IBAN or rule). Interest is calculated each
  month as *installment − drop in remaining debt* and added as a `Loan interest` expense on the loan
  account; the principal counts as saving. Needs a loan balance every month.
- **Currency exchanges** inside one account (Revolut EUR → USD) are category `Exchange` and don't count as
  spending.
- **Revolut:** fees become separate rows (`Revolut fee`), pending and reverted rows are skipped, and the
  export has no counterparty IBAN, so top-ups need a text rule like `Payment from Alex,Transfer,homebank_joint`.
- **Company stock (US broker):** balance only, in USD. A sale wired to the bank is a transfer
  (`STOCK PLAN,Transfer,stock_alex`). Vesting shows up as growth in net worth, not as income.

## Adding a new bank format

The three parsers are **assumptions** until real exports arrive. For a new bank:

1. Write `parse_<name>(path) -> list[Row]` in `pipeline/parsers.py` and add it to `PARSERS`.
2. Set `format=<name>` for the account in `accounts.csv`.
3. Ideally add a tiny anonymized sample to `tests/`.

## Your own analyses

Everything lives in `out/finance.duckdb`. Main tables and views:

| Name | What |
|---|---|
| `tx`, `tx_enriched` | deduplicated, categorized transactions (+ group, kind, fixed, account info) |
| `transfers` | one row per transfer between accounts |
| `monthly`, `monthly_kpis` | income, expenses, fixed/variable, savings rate (+ rolling 12 months) |
| `category_monthly` | spending per category and month |
| `balances_monthly`, `net_worth` | month-end balances (carried forward, in EUR), net worth by bucket |
| `loan_monthly` | installment, principal and calculated interest per loan and month |
| `holdings_monthly`, `unvested_monthly` | shares or amounts from `holdings.csv`, valued per month |
| `investment_monthly` | per investment account: deposits, newly vested shares, market movement |
| `recurring` | payees seen in ≥ 4 of the last 6 months with stable amounts |

```bash
uv run python -c "import duckdb; print(duckdb.connect('out/finance.duckdb').sql('SELECT * FROM monthly_kpis'))"
```

Or open it from R (`duckdb` package), the DuckDB CLI, or any notebook. Add your own views at the end of
`pipeline/views.sql`, new charts as methods in `pipeline/report.py`.

## Fetching data

**Enable Banking (PSD2 API), preferred where the bank is supported:** see
[docs/enablebanking.md](docs/enablebanking.md). Approve access every 90–180 days, then
`make eb-fetch` downloads transactions and balances without logging in.

**Playwright, for the rest:** see [docs/recording.md](docs/recording.md): `make record BANK=<name> URL=<login-url>` records the export
clicks (not the login), which become `fetch/banks/<name>.py`; `make fetch` then runs them.

## Layout

```
pipeline/   parsers.py, build.py (load + views.sql), report.py
fetch/      Playwright fetchers, recording helper
scripts/    make_sample.py (demo data)
examples/   starter rules.csv and categories.csv
docs/       recording.md, banks.md
tests/
```
