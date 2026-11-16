# Data folder: the repo root by default (your real data), or DATA=sample for the demo set.
DATA ?= .
export FT_DATA := $(abspath $(DATA))

.PHONY: help setup init sample build report all open test record fetch prices review categorize eb-setup eb-banks eb-link eb-fetch eb-status

help:
	@echo "make setup            install Python deps + Chromium"
	@echo "make init             copy starter rules/categories + empty config into the repo root"
	@echo "make sample           generate fake data in sample/"
	@echo "make prices           month-end USD/other rates from the ECB into fx.csv"
	@echo "make all [DATA=...]   build database + report"
	@echo "make open [DATA=...]  open the report"
	@echo "make review           go through unusual expenses (mark reviewed / categorize)"
	@echo "make categorize       go through uncategorized transactions, largest first"
	@echo "make test             run tests"
	@echo "make record BANK=x URL=https://...   record a bank export (see docs/recording.md)"
	@echo "make fetch [BANK=x]   run Playwright fetchers"
	@echo "make eb-setup APP=<id> KEY=<file.pem>   Enable Banking: store key (docs/enablebanking.md)"
	@echo "make eb-banks [COUNTRY=AT]              list banks available via Enable Banking"
	@echo "make eb-link BANK='<name>'              approve access to a bank (every ~90-180 days)"
	@echo "make eb-fetch [DAYS=90]                 download transactions + balances"

setup:
	uv sync
	uv run playwright install chromium

init:
	@for f in rules.csv categories.csv; do [ -f $$f ] || cp examples/$$f $$f; done
	@[ -f accounts.csv ] || echo "account,bank,name,owner,type,iban,format" > accounts.csv
	@[ -f balances.csv ] || echo "date,account,balance,currency" > balances.csv
	@[ -f overrides.csv ] || echo "tx_id,category,note" > overrides.csv
	@[ -f fx.csv ] || echo "month,currency,rate" > fx.csv
	@[ -f holdings.csv ] || echo "date,account,symbol,units,status,currency" > holdings.csv
	@mkdir -p raw
	@echo "Now fill in accounts.csv and drop exports into raw/<account>/"

sample:
	uv run python scripts/make_sample.py sample

prices:
	uv run python -m pipeline.prices

build:
	uv run python -m pipeline.build

report:
	uv run python -m pipeline.report

all: build report

open:
	open "$(FT_DATA)/out/report.html"

review:
	uv run python scripts/review.py

categorize:
	uv run python scripts/review.py uncategorized

test:
	uv run pytest -q

record:
	@test -n "$(BANK)" -a -n "$(URL)" || (echo "usage: make record BANK=<name> URL=<login-url>"; exit 1)
	@mkdir -p recordings
	uv run python -m fetch.record $(BANK) "$(URL)"

fetch:
	uv run python -m fetch $(BANK)

COUNTRY ?= AT
DAYS ?= 90
eb = uv run python -m fetch.enablebanking

eb-setup:
	@test -n "$(APP)" -a -n "$(KEY)" || (echo "usage: make eb-setup APP=<application-id> KEY=<path/to/private.pem>"; exit 1)
	$(eb) setup $(APP) "$(KEY)" $(REDIRECT)

eb-banks:
	$(eb) banks $(COUNTRY)

eb-link:
	@test -n "$(BANK)" || (echo "usage: make eb-link BANK='<bank name from make eb-banks>' [COUNTRY=AT]"; exit 1)
	$(eb) link "$(BANK)" $(COUNTRY)

eb-fetch:
	$(eb) fetch --days $(DAYS)

eb-status:
	$(eb) status
