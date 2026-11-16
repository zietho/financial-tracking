# Financial Tracking – Concept & Agreements

_Status: pipeline and report built and tested on synthetic data mirroring the real accounts; bank formats are still assumptions. Last updated: 2026-11-16._

**Accounts (2026-11-16):** a joint checking account and one savings account each at our home bank, Revolut, Trade Republic (cash + portfolio), company stock at a US broker (USD), one broker account each, a home loan. Details and status: [docs/banks.md](docs/banks.md). Added for this: currency conversion (fx.csv), Revolut parser, automatic loan interest split, property as an optional asset.

## Goal

A continuous, **local** overview of our household finances, with self-built reports instead of feeding data to an LLM or a third-party app. The setup should keep the monthly effort small so it becomes a habit, and be easy to extend step by step (with Claude Code as a coding helper).

## Principles

- **Local only.** Data stays on this Mac (FileVault). No cloud services or hosted finance apps. One deliberate exception (2026-11-17): Enable Banking as a PSD2 pipe for banks that support it. It passes the data through but stores nothing for us; see [docs/enablebanking.md](docs/enablebanking.md).
- **Raw data is immutable.** Exports land in `raw/` and are never edited. Everything else is rebuilt from scratch on each run.
- **Small and boring.** Few dependencies, plain SQL where possible, one language (Python).
- **Rules and overrides, not manual edits.** Categorization is driven by `rules.csv`, with one-off fixes in `overrides.csv`.
- **Being roughly right is fine.** Small differences get an adjustment entry, then we move on.
- **Habit first, features second.** Start minimal, automate only what turns out to be annoying.

## What we track

| Metric | Source | Frequency |
|---|---|---|
| Net worth (assets − debts), by account type | `balances.csv` (manual) | Monthly |
| Income vs. expenses | transactions | Monthly |
| Savings rate = (income − expenses) / income | transactions | Monthly, plus a rolling 12-month view |
| Expenses by category (~10–15 categories) | transactions | Monthly vs. 12-month average |
| Fixed vs. variable costs, recurring payments | transactions | Quarterly |
| Emergency fund in months (liquid cash ÷ monthly expenses) | balances + transactions | Quarterly |
| **Flows between our accounts** (Sankey) | transactions + IBAN matching | Monthly / yearly |

Scope: **the whole household** (joint and personal accounts).

## Two kinds of input

1. **Balances: every account, monthly, entered by hand** into `balances.csv`
   (`date, account, balance`). This covers checking, savings, broker, pension, loans and cash. It's the source of truth for net worth, so investments don't need unit or price modeling.
2. **Transactions: only accounts where spending or transfers happen.** That means checking (joint and personal) and credit cards. Savings, broker and pension usually need balances only.

History: 3–6 months is enough to start. **The first data set is October 2026.**

## Stack (agreed)

| Layer | Tool | Why |
|---|---|---|
| Fetch | **Python + Playwright** (persistent browser profile per bank) | Good download handling, auto-waits, `playwright codegen` to record click paths |
| Secrets | **macOS Keychain via `keyring`** (or 1Password CLI `op read` with Touch ID) | No passwords in code; one approval per run |
| Transform | **Python + DuckDB (SQL)** | Queries CSVs directly, fast, local, easy to extend |
| Report | **Python → one self-contained HTML file**, Plotly charts (incl. Sankey) | Works offline, no Quarto install needed (changed from Quarto on 2026-11-16) |
| Run | `Makefile`: `make fetch`, `make build`, `make report` | One command per step |
| Environment | `uv` (installed from PyPI into `~/.local/share/uv-venv`, linked in `~/.local/bin`) | Fast Python dependency management, one lock file. Homebrew had no prebuilt uv for this Mac |

Rejected: Grafana/Kibana (built for metrics and logs, overkill), Excel/Numbers (fine for net worth only, breaks with multi-account transaction data), Beancount/Firefly/Actual (we prefer our own pipeline), Selenium/Puppeteer (more boilerplate or a second language), AI browser agents (non-deterministic, send banking pages to a model).

## Planned folder layout

```
financial-tracking/
├── CONCEPT.md          # this file
├── Makefile
├── pyproject.toml      # uv
├── accounts.csv        # iban, name, owner, type   (not in git: contains IBANs)
├── balances.csv        # date, account, balance    (not in git)
├── rules.csv           # pattern → category (first match wins)
├── overrides.csv       # tx_hash → category (one-off fixes)
├── raw/<account>/      # bank exports, untouched   (not in git)
├── data/               # generated parquet/duckdb  (not in git)
├── fetch/              # Playwright fetchers, one per bank
├── pipeline/           # parsers (one per bank format) + SQL views
├── report.qmd          # Quarto report
└── docs/banks.md       # export checklist: bank, menu path, format
```

## Pipeline design

**Common transaction schema:**
`date, account, amount (+ in / − out), payee, counterparty_iban, memo, category, is_transfer, tx_hash`

- **Parsers:** one small function per bank format → common schema. They handle decimal commas (`1.234,56`), `;` separators and date formats.
- **Preferred export format:** **CAMT.053 (ISO 20022 XML)** where available, since it's standardized (one parser for all banks) and has reliable counterparty IBANs. CSV is the fallback. Avoid PDF.
- **Dedup:** `tx_hash = hash(account, date, amount, payee)`. Overlapping exports are harmless, so we always export the **last 2 months**.
- **Categorization:** `rules.csv` (text or regex on payee/memo), first match wins, then `overrides.csv`.
- **Internal transfers:**
  - The counterparty IBAN is in `accounts.csv` → internal transfer (edge from A to B), excluded from income and expenses.
  - Pair both sides (opposite amount, dates within ±3 days) so each transfer is counted once. One side alone is still enough to draw the flow.
  - Text fallbacks for cases without an IBAN: credit card settlement ("VISA Abrechnung") → card account, cash withdrawals → "Cash" account or an expense category, broker deposits.
- **Net worth** always comes from `balances.csv`, never from summing transactions.

## Report contents (`report.qmd`)

1. **Data quality first:** last transaction date per account (to spot forgotten exports), uncategorized transactions, balance gaps
2. Net worth over time, stacked by account type
3. Income / expenses / savings rate per month, plus a rolling 12-month average
4. Expenses by category: this month vs. 12-month average
5. **Sankey of money flows:** income sources → accounts → other accounts → expense categories
6. Transfer matrix (from × to per month), net flow per account
7. Fixed vs. variable costs, recurring payments
8. Emergency fund in months

## Automated fetching (Playwright)

- `make fetch` opens a **visible** browser per bank with a **persistent profile** (stored outside git).
- Password from **Keychain** (`keyring.get_password(...)`), approved once per run with "Allow", never "Always Allow". Alternative: 1Password CLI with Touch ID.
- Store the password once by hand: `security add-generic-password -s "finance-<bank>" -a "<username>" -w`
- **2FA/SCA approval stays manual** in the bank app (legally required).
- The script navigates to export → sets date range (last 2 months) → chooses CAMT/CSV → saves to `raw/<account>/`.
- **Security rules:** no Playwright traces, videos or screenshots on login pages; never log field values; nothing secret in the repo; runs only on this Mac.
- Caveats: bank terms of service may forbid automation; selectors break when bank websites change (quick fix with Claude).
- Some banks offer scheduled statement delivery (online mailbox or email), which may be easier than UI automation. Check per bank.

## Monthly routine (target ~20 min, first weekend of the month)

1. `make fetch`, or export manually into `raw/<account>/` (always the last 2 months)
2. Enter month-end balances in `balances.csv`
3. `make build report`
4. Check the data-quality section, add rules for new uncategorized payees, then re-render
5. Read: net worth, savings rate, top 3 categories, anything unusual. Write one sentence of notes.

Yearly in January: review categories, goals and fixed costs.

## Privacy & backup

- `raw/`, `data/`, `accounts.csv`, `balances.csv` and browser profiles are listed in `.gitignore`.
- Git without a remote, or code only.
- Encrypted backup (Time Machine / restic).

## Next steps

- [ ] **Tonight:** export **October 2026** manually from every bank/account. Per bank, note:
  - how long it took
  - available formats (CAMT.053? CSV? which encoding/delimiter?)
  - whether the **counterparty IBAN** appears for transfers
  - the click path (menu → export → format → date range), to go into `docs/banks.md`
- [ ] List all accounts (IBAN, name, owner, type) → `accounts.csv`
- [ ] Send Claude one **anonymized** sample per bank (header and layout unchanged, names and amounts altered)
- [ ] Decide: Keychain or 1Password
- [ ] Claude builds: project skeleton (uv, Makefile) → parsers → DuckDB views → Quarto report incl. Sankey
- [ ] Run manually for 1–2 months
- [ ] Then: Playwright fetchers for the banks that are annoying to export by hand
