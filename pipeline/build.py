"""Build out/finance.duckdb from a data folder.

Usage: uv run python -m pipeline.build [data_dir]     (default: $FT_DATA or the repo root)

Data folder layout:
    accounts.csv    account,name,owner,type,iban,format
    balances.csv    date,account,balance[,currency] (month-end, entered by hand)
    holdings.csv    date,account,symbol,units,status,currency (optional: shares instead of a balance)
    prices.csv      month,symbol,price,currency    (optional: filled by `make prices`)
    fx.csv          month,currency,rate            (1 unit = rate EUR; `make prices` fills it from the ECB)
    trips.csv       name,start,end                 (optional: vacations, to see what a trip cost)
    rules.csv       pattern,category,transfer_account
    categories.csv  category,group,kind,fixed
    overrides.csv   tx_id,category,note            (optional, one-off fixes)
    raw/<account>/  bank exports, untouched
    out/            generated: finance.duckdb, report.html

Everything is rebuilt from scratch on every run.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import duckdb

from .parsers import PARSERS

try:  # balance anchors and payslips, from pipeline/private_parsers.py (gitignored)
    from .private_parsers import ANCHORS
except ImportError:
    ANCHORS = {}
try:
    from .private_parsers import PAYEE, PAYROLL, parse_payslip_pdf
except ImportError:
    PAYROLL = {}

REPO = Path(__file__).resolve().parents[1]
VIEWS_SQL = Path(__file__).with_name("views.sql")


def data_dir(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1]).resolve()
    return Path(os.environ.get("FT_DATA", REPO)).resolve()


def read_config(path: Path, required: bool = True) -> list[dict]:
    """CSV with '#' comment lines allowed."""
    if not path.exists():
        if required:
            raise SystemExit(f"Missing {path}. See examples/ or run `make init`.")
        return []
    with path.open(encoding="utf-8-sig") as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    rows = []
    for r in csv.DictReader(lines):
        extra = r.pop(None, None)  # more values than header columns
        row = {k: (v or "").strip() for k, v in r.items()}
        if extra:
            row["__extra__"] = ",".join(extra)
        rows.append(row)
    return rows


def insert(con: duckdb.DuckDBPyConnection, table: str, rows: list[tuple]) -> None:
    if rows:
        con.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * len(rows[0]))})", rows)


def load_config(con: duckdb.DuckDBPyConnection, data: Path) -> list[dict]:
    accounts = read_config(data / "accounts.csv")
    for a in accounts:
        a["iban"] = a["iban"].replace(" ", "").upper() or None
    con.execute("""CREATE TABLE accounts (account TEXT PRIMARY KEY, name TEXT, owner TEXT,
                   type TEXT, iban TEXT, format TEXT)""")
    insert(con, "accounts", [(a["account"], a["name"], a["owner"], a["type"], a["iban"], a["format"] or None) for a in accounts])

    con.execute("CREATE TABLE categories (category TEXT PRIMARY KEY, \"group\" TEXT, kind TEXT, fixed TEXT)")
    insert(con, "categories", [(c["category"], c["group"], c["kind"], c["fixed"] or None)
                     for c in read_config(data / "categories.csv")])

    rules = read_config(data / "rules.csv")
    con.execute("CREATE TABLE rules (rule_no INT, pattern TEXT, category TEXT, transfer_account TEXT)")
    insert(con, "rules", [(i, r["pattern"], r["category"], r["transfer_account"] or None) for i, r in enumerate(rules, 1)])

    con.execute("CREATE TABLE overrides (tx_id TEXT PRIMARY KEY, category TEXT, note TEXT)")
    insert(con, "overrides", [(o["tx_id"], o["category"], o.get("note")) for o in read_config(data / "overrides.csv", False)])

    # balances.csv: date,account,balance[,currency]  (currency defaults to EUR)
    con.execute("CREATE TABLE balances (date DATE, account TEXT, balance DECIMAL(14,2), currency TEXT)")
    ids = {a["account"] for a in accounts}
    balances = read_config(data / "balances.csv", False)
    for n, b in enumerate(balances, start=2):
        problem = ("too many columns" if "__extra__" in b else
                   f"unknown account '{b['account']}' (not in accounts.csv)" if b["account"] not in ids else
                   f"balance '{b['balance']}' is not a number" if not b["balance"].replace(".", "", 1).lstrip("-").isdigit() else "")
        if problem:
            raise SystemExit(f"balances.csv line {n}: {problem}. Format: date,account,balance,currency "
                             f"(e.g. 2026-11-30,homebank_savings_alex,1500.00,EUR)")
    insert(con, "balances", [(b["date"], b["account"], b["balance"], (b.get("currency") or "EUR").upper())
                             for b in balances])

    # holdings.csv: date,account,symbol,units,status,currency  (units held from that date on;
    # status owned|unvested, default owned; only owned counts toward net worth)
    con.execute("CREATE TABLE holdings (date DATE, account TEXT, symbol TEXT, units DECIMAL(18,6), status TEXT, currency TEXT)")
    insert(con, "holdings", [(h["date"], h["account"], h["symbol"].upper(), h["units"],
                              (h.get("status") or "owned").lower(), (h.get("currency") or "EUR").upper())
                             for h in read_config(data / "holdings.csv", False)])

    # prices.csv: month,symbol,price,currency  (month-end price; filled by `make prices`)
    con.execute("CREATE TABLE prices (month DATE, symbol TEXT, price DECIMAL(18,6), currency TEXT)")
    insert(con, "prices", [(f["month"] if len(f["month"]) > 7 else f["month"] + "-01", f["symbol"].upper(), f["price"],
                            (f.get("currency") or "EUR").upper()) for f in read_config(data / "prices.csv", False)])

    # reviewed.csv: tx_id,note  (optional; expenses you checked, hidden from "Anything unusual?")
    con.execute("CREATE TABLE reviewed (tx_id TEXT, note TEXT)")
    insert(con, "reviewed", [(r["tx_id"], r.get("note")) for r in read_config(data / "reviewed.csv", False)])

    # trips.csv: name,start,end  (optional; spending in that period is tagged with the trip)
    con.execute("CREATE TABLE trips (name TEXT, start_date DATE, end_date DATE)")
    insert(con, "trips", [(t["name"], t["start"], t["end"]) for t in read_config(data / "trips.csv", False)])

    # fx.csv: month,currency,rate   (1 unit of currency = rate EUR; latest month <= date is used)
    con.execute("CREATE TABLE fx (month DATE, currency TEXT, rate DECIMAL(18,8))")
    insert(con, "fx", [(f["month"] if len(f["month"]) > 7 else f["month"] + "-01", f["currency"].upper(), f["rate"])
                       for f in read_config(data / "fx.csv", False)])
    return accounts


def load_raw(con: duckdb.DuckDBPyConnection, data: Path, accounts: list[dict]) -> int:
    con.execute("""CREATE TABLE tx_raw (account TEXT, date DATE, amount DECIMAL(14,2), currency TEXT,
                   payee TEXT, counterparty_iban TEXT, memo TEXT, source_file TEXT, line_no INT)""")
    n = 0
    for a in accounts:
        # raw/<account>/ or grouped in subfolders: raw/<bank>/<account>/, raw/<bank>/<product>/<account>/
        folder = data / "raw" / a["account"]
        if not folder.exists():
            folder = next((d for d in sorted((data / "raw").rglob(a["account"])) if d.is_dir()), folder)
        if not a["format"]:
            continue  # balance-only account
        if a["format"] not in PARSERS:
            raise SystemExit(f"Unknown format '{a['format']}' for account {a['account']}")
        if not folder.exists():
            print(f"  ! no exports for {a['account']} ({folder.relative_to(data)})")
            continue
        parse = PARSERS[a["format"]]
        anchor = ANCHORS.get(a["format"])
        for f in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")):
            if anchor:  # files that state a balance become balance lines (see derived balances)
                insert(con, "balances", [(d, a["account"], bal, cur) for d, bal, cur in anchor(f)])
            rows = parse(f)
            insert(con, "tx_raw", [
                (a["account"], r["date"], r["amount"], r["currency"], r["payee"], r["counterparty_iban"],
                 r["memo"], str(f.relative_to(data)), r["line_no"]) for r in rows])
            n += len(rows)
    n += load_payroll(con, data, {a["account"] for a in accounts})
    return n


def load_payroll(con: duckdb.DuckDBPyConnection, data: Path, account_ids: set[str]) -> int:
    """Payslips in raw/payroll/<folder>/ (any depth): money paid straight from gross salary into
    pension or company stock becomes income on that account, so the savings rate sees it."""
    n = 0
    for folder, targets in PAYROLL.items():
        root = data / "raw" / "payroll" / folder
        if not root.exists():
            continue
        for f in sorted(root.rglob("*.pdf")):
            rows = []
            for i, (d, kind, amount, memo) in enumerate(parse_payslip_pdf(f)):
                account = targets.get(kind)
                if account not in account_ids:
                    print(f"  ! payroll: account '{account}' for {kind} is not in accounts.csv, skipped")
                    continue
                rows.append((account, d, amount, "EUR", PAYEE[kind], None, memo, str(f.relative_to(data)), i))
            insert(con, "tx_raw", rows)
            n += len(rows)
    return n


def build(data: Path) -> Path:
    out = data / "out"
    out.mkdir(exist_ok=True)
    db_path = out / "finance.duckdb"
    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    accounts = load_config(con, data)
    n_raw = load_raw(con, data, accounts)
    con.execute(VIEWS_SQL.read_text())

    n_tx, n_unc, n_fx = con.execute("""
        SELECT count(*), count(*) FILTER (category = 'Uncategorized'), count(*) FILTER (amount IS NULL)
        FROM tx WHERE source_file IS NOT NULL""").fetchone()
    n_interest = con.execute("SELECT count(*) FROM tx WHERE matched_by = 'loan calc'").fetchone()[0]
    unknown = con.execute("""SELECT DISTINCT r.category FROM rules r
                             LEFT JOIN categories c USING (category) WHERE c.category IS NULL""").fetchall()
    print(f"Built {db_path.relative_to(data)}: {n_raw} raw rows -> {n_tx} transactions "
          f"({n_raw - n_tx} duplicates removed), {n_unc} uncategorized"
          + (f", {n_interest} calculated loan-interest rows" if n_interest else ""))
    if n_fx:
        print(f"  ! {n_fx} transactions in a foreign currency have no rate in fx.csv")
    bal_fx = con.execute("SELECT count(*) FROM balances_monthly WHERE balance_eur IS NULL").fetchone()[0]
    if bal_fx:
        print(f"  ! {bal_fx} balances in a foreign currency have no rate in fx.csv")
    if unknown:
        print("  ! rules use categories missing from categories.csv:", ", ".join(u[0] for u in unknown))
    con.close()
    return db_path


if __name__ == "__main__":
    build(data_dir(sys.argv))
