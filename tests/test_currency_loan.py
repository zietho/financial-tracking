"""Revolut parsing, currency conversion, loan interest split, balance carry-forward."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from pipeline.build import build
from pipeline.parsers import parse_revolut_csv

REPO = Path(__file__).resolve().parents[1]
REVOLUT_HEADER = "Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\n"


def test_revolut_parser(tmp_path: Path):
    f = tmp_path / "r.csv"
    f.write_text(REVOLUT_HEADER +
                 "CARD_PAYMENT,Current,2026-04-12 10:00:00,2026-04-12 10:00:05,Hotel NYC,-412.00,0.00,USD,COMPLETED,600\n"
                 "ATM,Current,2026-04-15 10:00:00,2026-04-15 10:00:05,Chase ATM,-200.00,4.00,USD,COMPLETED,396\n"
                 "CARD_PAYMENT,Current,2026-04-16 10:00:00,,Pending coffee,-5.00,0.00,USD,PENDING,\n"
                 "CARD_PAYMENT,Current,2026-04-16 10:00:00,2026-04-16 11:00:00,Tickets,-120.00,0.00,USD,REVERTED,\n"
                 "TRANSFER,Savings,2026-04-17 10:00:00,2026-04-17 10:00:00,To vault,-50.00,0.00,EUR,COMPLETED,10\n")
    rows = parse_revolut_csv(f)
    assert [(r["payee"], str(r["amount"]), r["currency"]) for r in rows] == [
        ("Hotel NYC", "-412.00", "USD"),
        ("Chase ATM", "-200.00", "USD"),
        ("Revolut fee", "-4.00", "USD"),
    ]
    assert rows[0]["memo"] == "CARD_PAYMENT"


@pytest.fixture
def db(tmp_path: Path):
    (tmp_path / "accounts.csv").write_text(
        "account,name,owner,type,iban,format\n"
        "joint,Joint,both,checking,AT000000000000000001,bank_a_csv\n"
        "rev,Revolut,me,checking,,revolut_csv\n"
        "loan,Loan,both,loan,AT000000000000000009,\n"
        "home,Home,both,property,,\n"
        "stock,Company stock,me,investment,,\n")
    (tmp_path / "fx.csv").write_text("month,currency,rate\n2026-01,USD,0.90\n2026-03,USD,0.95\n")
    (tmp_path / "balances.csv").write_text(
        "date,account,balance,currency\n"
        "2026-01-31,loan,-100000,EUR\n2026-02-28,loan,-99500,EUR\n2026-03-31,loan,-99000,EUR\n"
        "2026-01-31,home,300000,EUR\n"          # entered once, carried forward
        "2026-01-31,stock,1000,USD\n2026-03-31,stock,1000,USD\n"
        "2026-01-31,joint,0\n2026-02-28,joint,0\n2026-03-31,joint,0\n")  # currency column optional
    (tmp_path / "rules.csv").write_text("pattern,category,transfer_account\nExchanged,Exchange,\nHotel,Travel,\n")
    shutil.copy(REPO / "examples" / "categories.csv", tmp_path / "categories.csv")
    (tmp_path / "raw/joint").mkdir(parents=True)
    (tmp_path / "raw/rev").mkdir(parents=True)
    hdr = "Buchungsdatum;Valutadatum;Partnername;Partner IBAN;Buchungstext;Verwendungszweck;Betrag;Währung\n"
    (tmp_path / "raw/joint/x.csv").write_text(
        hdr + "".join(f"01.{m:02d}.2026;01.{m:02d}.2026;Bank;AT000000000000000009;Dauerauftrag;Kreditrate;-800,00;EUR\n"
                      for m in (1, 2, 3)), encoding="cp1252")
    (tmp_path / "raw/rev/x.csv").write_text(
        REVOLUT_HEADER +
        "EXCHANGE,Current,2026-02-10 10:00:00,2026-02-10 10:00:00,Exchanged to USD,-100.00,0.00,EUR,COMPLETED,0\n"
        "EXCHANGE,Current,2026-02-10 10:00:00,2026-02-10 10:00:00,Exchanged from EUR,110.00,0.00,USD,COMPLETED,110\n"
        "CARD_PAYMENT,Current,2026-02-12 10:00:00,2026-02-12 10:00:00,Hotel,-100.00,0.00,USD,COMPLETED,10\n")
    build(tmp_path)
    con = duckdb.connect(str(tmp_path / "out" / "finance.duckdb"), read_only=True)
    yield con
    con.close()


def q(con, sql):
    return con.execute(sql).fetchall()


def test_foreign_currency_converted_with_latest_rate(db):
    # Feb uses the Jan rate (latest at or before the date)
    assert q(db, "SELECT amount_orig, amount, currency FROM tx WHERE payee = 'Hotel'") == [
        (pytest.approx(-100), pytest.approx(-90), "USD")]


def test_exchange_not_counted_as_spending(db):
    feb = q(db, "SELECT expenses, income FROM monthly WHERE month = '2026-02-01'")[0]
    assert float(feb[0]) == pytest.approx(90.0 + 300.0)  # hotel + calculated loan interest, no exchange legs
    assert float(feb[1]) == 0


def test_loan_interest_split(db):
    rows = q(db, "SELECT month, principal, interest FROM loan_monthly ORDER BY month")
    # installment 800, debt drops 500 -> 300 interest, 500 principal
    assert [(str(m), float(p), float(i)) for m, p, i in rows] == [
        ("2026-02-01", 500.0, 300.0), ("2026-03-01", 500.0, 300.0)]
    assert q(db, "SELECT count(*), sum(amount) FROM tx WHERE category = 'Loan interest'")[0] == (2, -600)
    # the installment itself is a transfer, not an expense
    assert q(db, "SELECT count(*) FROM transfers WHERE to_account = 'loan'") == [(3,)]


def test_balances_carried_forward_and_converted(db):
    rows = q(db, "SELECT month, property, investments FROM net_worth ORDER BY month")
    assert [(str(m), float(p), float(i)) for m, p, i in rows] == [
        ("2026-01-01", 300000.0, 900.0), ("2026-02-01", 300000.0, 900.0), ("2026-03-01", 300000.0, 950.0)]
