"""Month-end balances calculated back and forward from one known balance (the anchor)."""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb

from pipeline.build import build

REPO = Path(__file__).resolve().parents[1]
HDR = "Buchungsdatum;Valutadatum;Partnername;Partner IBAN;Buchungstext;Verwendungszweck;Betrag;Währung\n"


def test_balances_from_anchor(tmp_path: Path):
    (tmp_path / "accounts.csv").write_text("account,name,owner,type,iban,format\ngiro,Giro,me,checking,,bank_a_csv\n")
    # anchor in the middle: 1000 on 15 Feb
    (tmp_path / "balances.csv").write_text("date,account,balance,currency\n2026-02-15,giro,1000,EUR\n")
    (tmp_path / "rules.csv").write_text("pattern,category,transfer_account\n")
    shutil.copy(REPO / "examples" / "categories.csv", tmp_path / "categories.csv")
    (tmp_path / "raw/giro").mkdir(parents=True)
    (tmp_path / "raw/giro/x.csv").write_text(HDR +
        "10.01.2026;10.01.2026;A;;x;y;-100,00;EUR\n"
        "10.02.2026;10.02.2026;B;;x;y;-200,00;EUR\n"
        "20.02.2026;20.02.2026;C;;x;y;50,00;EUR\n"
        "05.03.2026;05.03.2026;D;;x;y;-30,00;EUR\n", encoding="cp1252")
    build(tmp_path)
    con = duckdb.connect(str(tmp_path / "out" / "finance.duckdb"), read_only=True)
    rows = con.execute("SELECT strftime(month, '%m'), balance FROM balances_monthly WHERE account = 'giro' ORDER BY month").fetchall()
    # end of Jan: 1000 + 200 (Feb 10 undone) = 1200; end of Feb: 1000 + 50 = 1050; end of Mar: 1050 - 30 = 1020
    assert [(m, float(b)) for m, b in rows] == [("01", 1200.0), ("02", 1050.0), ("03", 1020.0)]
