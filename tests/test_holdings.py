"""holdings.csv (shares or plain amounts, owned vs. unvested) and the deposits-vs-market split."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from pipeline.build import build

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def db(tmp_path: Path):
    (tmp_path / "accounts.csv").write_text(
        "account,bank,name,owner,type,iban,format\n"
        "giro,b,Giro,me,checking,AT000000000000000001,bank_a_csv\n"
        "depot,b,Depot,me,investment,AT000000000000000002,\n"
        "stock,s,Stock plan,me,investment,,\n")
    (tmp_path / "balances.csv").write_text(
        "date,account,balance,currency\n2026-01-31,depot,1000,EUR\n2026-02-28,depot,1600,EUR\n"
        "2026-03-31,depot,1650,EUR\n2026-01-31,giro,0\n")
    (tmp_path / "holdings.csv").write_text(
        "date,account,symbol,units,status,currency\n"
        "2026-01-15,stock,ACME,10,owned,USD\n"
        "2026-03-15,stock,ACME,12,owned,USD\n"       # vest of 2 shares in March
        "2026-01-15,stock,ACME,30,unvested,USD\n"
        "2026-02-01,stock,USD,500,owned,USD\n")      # plain amount: price 1
    (tmp_path / "prices.csv").write_text("month,symbol,price,currency\n2026-01,ACME,100,USD\n2026-03,ACME,110,USD\n")
    (tmp_path / "fx.csv").write_text("month,currency,rate\n2026-01,USD,0.5\n")
    (tmp_path / "rules.csv").write_text("pattern,category,transfer_account\n")
    shutil.copy(REPO / "examples" / "categories.csv", tmp_path / "categories.csv")
    (tmp_path / "raw/giro").mkdir(parents=True)
    (tmp_path / "raw/giro/x.csv").write_text(
        "Buchungsdatum;Valutadatum;Partnername;Partner IBAN;Buchungstext;Verwendungszweck;Betrag;Währung\n"
        "05.02.2026;05.02.2026;Depot;AT000000000000000002;Überweisung;Sparplan;-500,00;EUR\n", encoding="cp1252")
    build(tmp_path)
    con = duckdb.connect(str(tmp_path / "out" / "finance.duckdb"), read_only=True)
    yield con
    con.close()


def q(con, sql):
    return [tuple(float(v) if v is not None and not isinstance(v, str) else v for v in r)
            for r in con.execute(sql).fetchall()]


def test_owned_shares_and_amounts_become_balances(db):
    # Jan: 10 x 100 = 1000 USD; Feb: + 500 USD plain amount; Mar: 12 x 110 + 500 = 1820 USD; rate 0.5
    assert q(db, "SELECT strftime(month, '%m'), balance, balance_eur FROM balances_monthly "
                 "WHERE account = 'stock' ORDER BY month") == [("01", 1000, 500), ("02", 1500, 750), ("03", 1820, 910)]


def test_unvested_not_in_net_worth(db):
    assert q(db, "SELECT value_eur FROM unvested_monthly WHERE month = '2026-01-01'") == [(1500,)]
    assert q(db, "SELECT investments FROM net_worth WHERE month = '2026-01-01'") == [(1500,)]  # depot 1000 + stock 500


def test_deposits_vs_market(db):
    rows = q(db, "SELECT account, strftime(month, '%m'), deposits, added, market FROM investment_monthly "
                 "WHERE change IS NOT NULL ORDER BY account, month")
    assert ("depot", "02", 500, 0, 100) in rows       # +600: 500 deposited, 100 market
    assert ("stock", "03", 0, 110, 50) in rows        # +160 EUR: 2 new shares (110), price 100 -> 110 on 10 shares (50)
