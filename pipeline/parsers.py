"""Bank export parsers: one function per format, all returning the same schema.

Add a new bank: write parse_<format>(path) -> list[Row], register it in PARSERS,
and set that format in accounts.csv.

Row fields: date, amount (+ in / - out, from the account's view), currency,
payee, counterparty_iban, memo, line_no.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, TypedDict


class Row(TypedDict):
    date: date
    amount: Decimal
    currency: str
    payee: str
    counterparty_iban: str | None
    memo: str
    line_no: int


# ---------------------------------------------------------------- helpers
def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode {path}")


def de_decimal(s: str) -> Decimal:
    """'1.234,56' / '-18,49' / '1234,5' -> Decimal."""
    s = s.strip().replace("\xa0", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return Decimal(s)


def norm_iban(s: str | None) -> str | None:
    s = re.sub(r"\s", "", s or "").upper()
    return s or None


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


# ---------------------------------------------------------------- formats
def parse_bank_a_csv(path: Path) -> list[Row]:
    """ASSUMED format: semicolon CSV, decimal comma, dd.mm.yyyy.
    Header: Buchungsdatum;Valutadatum;Partnername;Partner IBAN;Buchungstext;Verwendungszweck;Betrag;Währung
    """
    rows: list[Row] = []
    reader = csv.DictReader(io.StringIO(read_text(path)), delimiter=";")
    for i, r in enumerate(reader, start=2):
        rows.append(Row(
            date=datetime.strptime(r["Buchungsdatum"].strip(), "%d.%m.%Y").date(),
            amount=de_decimal(r["Betrag"]),
            currency=r.get("Währung") or "EUR",
            payee=clean(r["Partnername"]),
            counterparty_iban=norm_iban(r["Partner IBAN"]),
            memo=clean(f'{r["Buchungstext"]} {r["Verwendungszweck"]}'),
            line_no=i,
        ))
    return rows


def parse_card_csv(path: Path) -> list[Row]:
    """ASSUMED format: comma CSV, ISO dates, charges POSITIVE (flipped here).
    Header: Transaction Date,Posting Date,Description,Amount,Currency
    """
    rows: list[Row] = []
    reader = csv.DictReader(io.StringIO(read_text(path)))
    for i, r in enumerate(reader, start=2):
        rows.append(Row(
            date=date.fromisoformat(r["Transaction Date"].strip()),
            amount=-Decimal(r["Amount"].strip()),
            currency=r.get("Currency") or "EUR",
            payee=clean(r["Description"]),
            counterparty_iban=None,
            memo="",
            line_no=i,
        ))
    return rows


def parse_camt053(path: Path) -> list[Row]:
    """ISO 20022 camt.053 (any version): booked entries only."""
    root = ET.fromstring(path.read_bytes())
    for el in root.iter():  # drop namespaces so paths work for every camt version
        el.tag = el.tag.split("}", 1)[-1]

    def text(node, p):
        found = node.find(p) if node is not None else None
        return found.text.strip() if found is not None and found.text else ""

    rows: list[Row] = []
    for i, e in enumerate(root.iter("Ntry"), start=1):
        status = text(e, "Sts/Cd") or text(e, "Sts")
        if status and status != "BOOK":
            continue
        incoming = text(e, "CdtDbtInd") == "CRDT"
        amt = e.find("Amt")
        tx = e.find("NtryDtls/TxDtls")
        party = "Dbtr" if incoming else "Cdtr"
        payee = text(tx, f"RltdPties/{party}/Nm") or text(tx, f"RltdPties/{party}/Pty/Nm")
        iban = text(tx, f"RltdPties/{party}Acct/Id/IBAN")
        memo = " ".join(u.text.strip() for u in (tx.findall("RmtInf/Ustrd") if tx is not None else []) if u.text)
        memo = memo or text(e, "AddtlNtryInf")
        value = Decimal(amt.text.strip())
        rows.append(Row(
            date=date.fromisoformat(text(e, "BookgDt/Dt") or text(e, "BookgDt/DtTm")[:10]),
            amount=value if incoming else -value,
            currency=amt.get("Ccy", "EUR"),
            payee=clean(payee),
            counterparty_iban=norm_iban(iban),
            memo=clean(memo),
            line_no=i,
        ))
    return rows


def parse_revolut_csv(path: Path) -> list[Row]:
    """Revolut account statement CSV (layout as commonly exported; verify with a real file).
    Header: Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance

    - only COMPLETED rows (pending, reverted, declined are skipped)
    - only Product "Current" (savings vaults would be separate accounts)
    - Fee becomes its own row so it can be categorized as a fee
    - memo = Revolut's Type (CARD_PAYMENT, TOPUP, EXCHANGE, ...) so rules can match on it
    - no counterparty IBAN in this export: transfers need text rules
    """
    rows: list[Row] = []
    reader = csv.DictReader(io.StringIO(read_text(path)))
    for i, r in enumerate(reader, start=2):
        if (r.get("State") or "COMPLETED").strip().upper() != "COMPLETED":
            continue
        if (r.get("Product") or "Current").strip() not in ("Current", ""):
            continue
        ts = (r.get("Completed Date") or "").strip() or r["Started Date"].strip()
        d = date.fromisoformat(ts[:10])
        currency = r["Currency"].strip().upper()
        kind = (r.get("Type") or "").strip().upper()
        rows.append(Row(date=d, amount=Decimal(r["Amount"].strip()), currency=currency,
                        payee=clean(r["Description"]), counterparty_iban=None, memo=kind, line_no=i * 10))
        fee = Decimal((r.get("Fee") or "0").strip() or "0")
        if fee:
            rows.append(Row(date=d, amount=-abs(fee), currency=currency, payee="Revolut fee",
                            counterparty_iban=None, memo=f"FEE {kind} {clean(r['Description'])}", line_no=i * 10 + 1))
    return rows


def parse_enablebanking_json(path: Path) -> list[Row]:
    """JSON written by `make eb-fetch` (Enable Banking / Berlin Group fields).

    Only booked transactions. Amounts come unsigned; credit_debit_indicator
    (CRDT / DBIT) gives the direction. The other party is the debtor for
    incoming and the creditor for outgoing money.
    """
    import json

    if path.suffix != ".json":
        return []
    data = json.loads(read_text(path))
    rows: list[Row] = []
    for i, t in enumerate(data.get("transactions") or []):
        if (t.get("status") or "BOOK") != "BOOK":
            continue
        day = t.get("booking_date") or t.get("value_date") or t.get("transaction_date")
        if not day:
            continue
        amt = abs(Decimal(str(t["transaction_amount"]["amount"])))
        outgoing = t.get("credit_debit_indicator") == "DBIT"
        party = (t.get("creditor") if outgoing else t.get("debtor")) or {}
        party_acc = (t.get("creditor_account") if outgoing else t.get("debtor_account")) or {}
        memo = " ".join(t.get("remittance_information") or []) \
            or ((t.get("bank_transaction_code") or {}).get("description") or "")
        rows.append(Row(date=date.fromisoformat(day[:10]), amount=-amt if outgoing else amt,
                        currency=t["transaction_amount"].get("currency") or "EUR",
                        payee=clean(party.get("name")), counterparty_iban=norm_iban(party_acc.get("iban")),
                        memo=clean(memo), line_no=i))
    return rows


PARSERS: dict[str, Callable[[Path], list[Row]]] = {
    "bank_a_csv": parse_bank_a_csv,
    "card_csv": parse_card_csv,
    "camt053": parse_camt053,
    "revolut_csv": parse_revolut_csv,
    "enablebanking_json": parse_enablebanking_json,
}

# Parsers for your own banks can live in pipeline/private_parsers.py (gitignored, never
# pushed). It defines its own PARSERS dict, which is merged in here.
try:
    from .private_parsers import PARSERS as _PRIVATE
    PARSERS.update(_PRIVATE)
except ImportError:
    pass
