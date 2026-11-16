"""Enable Banking: JSON parser, balance choice, JWT, balances.csv upsert. No network."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from fetch import enablebanking as eb
from pipeline.parsers import parse_enablebanking_json

TX = [
    {"entry_reference": "1", "status": "BOOK", "credit_debit_indicator": "DBIT",
     "transaction_amount": {"amount": "18.49", "currency": "EUR"}, "booking_date": "2026-10-03",
     "creditor": {"name": "BILLA  DANKT 1234"}, "creditor_account": {"iban": "AT11 2222 3333 4444 5555"},
     "remittance_information": ["POS 03.10.", "Wien"]},
    {"entry_reference": "2", "status": "BOOK", "credit_debit_indicator": "CRDT",
     "transaction_amount": {"amount": "3200.00", "currency": "EUR"}, "value_date": "2026-10-01",
     "debtor": {"name": "Employer GmbH"}, "debtor_account": {"iban": "AT99"},
     "bank_transaction_code": {"description": "Gehalt"}},
    {"entry_reference": "3", "status": "PDNG", "credit_debit_indicator": "DBIT",
     "transaction_amount": {"amount": "5.00", "currency": "EUR"}, "booking_date": "2026-10-04"},
]


def test_parser(tmp_path: Path):
    f = tmp_path / "2026-10-05_enablebanking.json"
    f.write_text(json.dumps({"transactions": TX, "balances": []}))
    rows = parse_enablebanking_json(f)
    assert [(str(r["date"]), r["amount"], r["payee"], r["counterparty_iban"], r["memo"]) for r in rows] == [
        ("2026-10-03", Decimal("-18.49"), "BILLA DANKT 1234", "AT112222333344445555", "POS 03.10. Wien"),
        ("2026-10-01", Decimal("3200.00"), "Employer GmbH", "AT99", "Gehalt"),
    ]


def test_parser_ignores_other_files(tmp_path: Path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    assert parse_enablebanking_json(f) == []


def test_pick_balance_prefers_booked():
    bs = [{"balance_type": "CLAV", "balance_amount": {"amount": "1"}},
          {"balance_type": "CLBD", "balance_amount": {"amount": "2"}},
          {"balance_type": "XYZ", "balance_amount": {"amount": "3"}}]
    assert eb.pick_balance(bs)["balance_amount"]["amount"] == "2"
    assert eb.pick_balance([]) is None


def test_jwt_claims():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    token = eb.make_jwt("app-123", pem, now=1_800_000_000)
    assert jwt.get_unverified_header(token)["kid"] == "app-123"
    claims = jwt.decode(token, key.public_key(), algorithms=["RS256"], audience="api.enablebanking.com",
                        options={"verify_exp": False, "verify_iat": False, "verify_nbf": False})
    assert claims["iss"] == "enablebanking.com" and claims["exp"] - claims["iat"] <= 86400


def test_append_balance_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    (tmp_path / "balances.csv").write_text("date,account,balance,currency\n")
    b = {"balance_amount": {"amount": "1234.50", "currency": "EUR"}, "reference_date": "2026-10-05"}
    assert eb.append_balance("homebank_joint", b) is True
    assert eb.append_balance("homebank_joint", b) is False
    assert (tmp_path / "balances.csv").read_text().splitlines()[1] == "2026-10-05,homebank_joint,1234.50,EUR"


def test_iban_mapping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FT_DATA", str(tmp_path))
    (tmp_path / "accounts.csv").write_text("account,name,owner,type,iban,format\n"
                                           "homebank_joint,Joint,both,checking,AT12 3456 7890 1234 5678,enablebanking_json\n"
                                           "home,Home,both,property,,\n")
    assert eb.our_accounts() == {"AT123456789012345678": "homebank_joint"}
