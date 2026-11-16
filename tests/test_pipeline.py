"""End-to-end tests on tiny hand-written exports."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pytest

from pipeline.build import build
from pipeline.parsers import de_decimal, parse_card_csv

REPO = Path(__file__).resolve().parents[1]

BANK_HEADER = "Buchungsdatum;Valutadatum;Partnername;Partner IBAN;Buchungstext;Verwendungszweck;Betrag;Währung\n"


@pytest.fixture
def db(tmp_path: Path):
    (tmp_path / "accounts.csv").write_text(
        "account,name,owner,type,iban,format\n"
        "a,Account A,me,checking,AT00 0000 0000 0000 0001,bank_a_csv\n"
        "b,Account B,me,checking,AT000000000000000002,bank_a_csv\n"
        "card,Card,me,credit_card,,card_csv\n"
        "broker,Broker,me,investment,AT000000000000000009,\n")
    (tmp_path / "balances.csv").write_text("date,account,balance\n2026-10-31,a,100\n2026-10-31,broker,50\n")
    (tmp_path / "rules.csv").write_text(
        "pattern,category,transfer_account\n"
        "# comment lines are ignored\n"
        "CARD SETTLEMENT,Transfer,card\n"
        "PAYMENT RECEIVED,Transfer,a\n"
        "BILLA PLUS,Shopping,\n"   # specific rule above the generic one
        "BILLA|SPAR,Groceries,\n")
    shutil.copy(REPO / "examples" / "categories.csv", tmp_path / "categories.csv")
    (tmp_path / "overrides.csv").write_text("tx_id,category,note\n")

    for acc in ("a", "b", "card"):
        (tmp_path / "raw" / acc).mkdir(parents=True)
    # two overlapping exports of account a; the same coffee twice on one day is legit
    sep = ";;Kartenzahlung;"
    oct_rows = (f"01.10.2026;01.10.2026;BILLA DANKT{sep}POS 1;-3,20;EUR\n"
                f"01.10.2026;01.10.2026;BILLA DANKT{sep}POS 1;-3,20;EUR\n"
                f"02.10.2026;02.10.2026;BILLA PLUS 22{sep}POS 2;-1.234,50;EUR\n"
                "03.10.2026;03.10.2026;Me;AT000000000000000002;Überweisung;to b;-500,00;EUR\n"
                "04.10.2026;04.10.2026;Me;AT000000000000000009;Überweisung;to broker;-200,00;EUR\n"
                "06.10.2026;06.10.2026;Card Services;;Lastschrift;CARD SETTLEMENT 09;-80,00;EUR\n")
    (tmp_path / "raw/a/2026-10.csv").write_text(BANK_HEADER + oct_rows, encoding="cp1252")
    (tmp_path / "raw/a/2026-10b.csv").write_text(
        BANK_HEADER + oct_rows + f"05.10.2026;05.10.2026;SPAR{sep}POS 3;-10,00;EUR\n", encoding="cp1252")
    (tmp_path / "raw/b/2026-10.csv").write_text(
        BANK_HEADER + "04.10.2026;04.10.2026;Me;AT000000000000000001;Gutschrift;to b;500,00;EUR\n"
                      "07.10.2026;07.10.2026;Unknown Shop;;Kartenzahlung;xyz;-7,00;EUR\n", encoding="cp1252")
    (tmp_path / "raw/card/2026-10.csv").write_text(
        "Transaction Date,Posting Date,Description,Amount,Currency\n"
        "2026-10-07,2026-10-08,PAYMENT RECEIVED,-80.00,EUR\n"
        "2026-10-10,2026-10-11,SPAR 99,25.00,EUR\n")

    build(tmp_path)
    con = duckdb.connect(str(tmp_path / "out" / "finance.duckdb"), read_only=True)
    yield con
    con.close()


def q(con, sql):
    return con.execute(sql).fetchall()


def test_dedup_keeps_legit_duplicates(db):
    # overlapping files collapse, but the two identical coffees in one file stay two
    assert q(db, "SELECT count(*) FROM tx WHERE account='a' AND amount=-3.20") == [(2,)]
    assert q(db, "SELECT count(*) FROM tx WHERE account='a'") == [(7,)]


def test_rules_first_match_wins(db):
    assert q(db, "SELECT category FROM tx WHERE payee LIKE 'BILLA PLUS%'") == [("Shopping",)]
    assert q(db, "SELECT DISTINCT category FROM tx WHERE payee='BILLA DANKT'") == [("Groceries",)]
    assert q(db, "SELECT category FROM tx WHERE payee='Unknown Shop'") == [("Uncategorized",)]


def test_internal_transfers_by_iban_counted_once(db):
    rows = q(db, "SELECT from_account, to_account, amount FROM transfers ORDER BY to_account")
    # a->b appears in both accounts (1 day apart) but is counted once; a->broker has one leg only;
    # card settlement is matched by text rule on both sides
    assert rows == [("a", "b", 500), ("a", "broker", 200), ("a", "card", 80)]


def test_transfers_excluded_from_income_and_expenses(db):
    income, expenses = q(db, "SELECT income, expenses FROM monthly")[0]
    assert income == 0
    assert float(expenses) == pytest.approx(3.20 * 2 + 1234.50 + 10 + 7 + 25)


def test_german_decimal_format():
    assert de_decimal("1.234,50") == de_decimal("1234,5")


def test_card_sign_flip(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("Transaction Date,Posting Date,Description,Amount,Currency\n2026-01-01,2026-01-02,X,12.50,EUR\n")
    assert str(parse_card_csv(f)[0]["amount"]) == "-12.50"
