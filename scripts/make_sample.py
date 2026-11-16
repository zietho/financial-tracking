"""Generate a fake household data set in sample/ to develop and demo the pipeline.

Usage: uv run python scripts/make_sample.py [out_dir]

A typical household: a joint checking account and two savings accounts at a home bank,
Revolut, Trade Republic, company stock at a US broker, two broker accounts and a home
loan. Everything is invented: names, IBANs, amounts, and who pays what.

Export formats are ASSUMPTIONS until we have real samples:
- home bank       -> bank_a_csv   semicolon CSV, decimal comma, dd.mm.yyyy, cp1252 (placeholder)
- Revolut         -> revolut_csv  Revolut statement CSV (commonly seen layout)
- Trade Republic  -> camt053      placeholder until we know what the app exports
"""

from __future__ import annotations

import csv
import random
import shutil
import sys
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parents[1]
rng = random.Random(42)

START = date(2025, 11, 1)
MONTHS = 12  # Nov 2025 .. Oct 2026

ACCOUNTS = [
    # account, name, owner, type, iban, format
    ("homebank_joint", "Joint checking", "both", "checking", "AT001100000000000001", "bank_a_csv"),
    ("homebank_savings_alex", "Savings Alex", "alex", "savings", "AT001100000000000002", "bank_a_csv"),
    ("homebank_savings_anna", "Savings Anna", "anna", "savings", "AT001100000000000003", "bank_a_csv"),
    ("revolut_alex", "Revolut", "alex", "checking", "LT000000000000000004", "revolut_csv"),
    ("traderepublic_cash", "Trade Republic cash", "alex", "savings", "DE000000000000000005", "camt053"),
    ("traderepublic_portfolio", "Trade Republic portfolio", "alex", "investment", "", ""),
    ("stock_alex", "Company stock (US broker)", "alex", "investment", "", ""),
    ("broker_alex", "Broker Alex", "alex", "investment", "DE000000000000000008", ""),
    ("broker_anna", "Broker Anna", "anna", "investment", "DE000000000000000009", ""),
    ("homebank_loan", "Home loan", "both", "homebank_loan", "AT002200000000000010", ""),
    ("apartment", "Apartment (estimated value)", "both", "property", "", ""),
]
IBAN = {a[0]: a[4] for a in ACCOUNTS}
HOLDER = {"homebank_joint": "Alex u. Anna Muster", "homebank_savings_alex": "Alex Muster", "homebank_savings_anna": "Anna Muster",
          "revolut_alex": "Alex Muster", "traderepublic_cash": "Alex Muster", "broker_alex": "Beispiel Broker AG",
          "broker_anna": "Beispiel Broker AG", "homebank_loan": "Beispiel Kreditbank AG"}
START_BALANCE = {"homebank_joint": 4200, "homebank_savings_alex": 9000, "homebank_savings_anna": 11000, "revolut_alex": 350,
                 "traderepublic_cash": 6000, "traderepublic_portfolio": 14000, "broker_alex": 21000, "broker_anna": 12500,
                 "homebank_loan": -186000, "apartment": 310000}
STOCK_START_USD = Decimal(42000)
LOAN_RATE = Decimal("0.034")
INSTALLMENT = Decimal("1100.00")

# Transfer rules the sample needs (no IBAN in those exports). Prepended to examples/rules.csv.
SAMPLE_RULES = """\
# --- sample: transfers without IBAN
Payment from Alex Muster,Transfer,homebank_joint
Sparplan,Transfer,traderepublic_portfolio
STOCK PLAN,Transfer,stock_alex
"""


@dataclass
class Tx:
    account: str
    date: date
    amount: Decimal  # + in, - out (from the account's point of view), in `currency`
    payee: str
    iban: str = ""
    memo: str = ""
    kind: str = ""  # bank_a "Buchungstext" or Revolut "Type"
    currency: str = "EUR"
    fee: Decimal = Decimal(0)
    state: str = "COMPLETED"


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def month_starts():
    d = START
    for _ in range(MONTHS):
        yield d
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def month_end(m: date) -> date:
    return (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def day(m: date, d: int) -> date:
    return m.replace(day=min(d, month_end(m).day))


def rand_day(m):
    return day(m, rng.randint(1, 28))


txs: list[Tx] = []


def add(account, d, amount, payee, iban="", memo="", kind="", **kw):
    txs.append(Tx(account, d, money(amount), payee, iban, memo, kind, **kw))


def transfer(src, dst, d, amount, memo):
    """Both legs of an internal transfer (only legs of exported accounts end up in files)."""
    add(src, d, -amount, HOLDER.get(dst, dst), IBAN[dst], memo, "Überweisung")
    add(dst, d, amount, HOLDER.get(src, src), IBAN[src], memo, "Gutschrift")


def pos(account, d, low, high, payees):
    t = f"{rng.randint(8, 20):02d}:{rng.randint(0, 59):02d}"
    add(account, d, -rng.uniform(low, high), rng.choice(payees),
        memo=f"POS {rng.randint(1000, 9999)} K1 {d:%d.%m.} {t}", kind="Kartenzahlung")


def card(d, amount, payee, currency="EUR", fee=0, kind="CARD_PAYMENT"):
    add("revolut_alex", d, -amount, payee, kind=kind, currency=currency, fee=money(fee))


GROCERIES = ["BILLA DANKT 1234", "SPAR DANKT 5678", "HOFER DANKT 0815", "LIDL DANKT 4711", "PENNY 3300", "BILLA PLUS 2211"]
DRUGSTORE = ["DM DROGERIE 0458", "BIPA 0921"]
FUEL = ["OMV 1203", "BP 4410", "SHELL 7781"]
RESTAURANTS = ["Gasthaus zur Linde", "Pizzeria Bella", "Cafe Kranz", "Wirtshaus am Eck", "Sushi Miya"]
CLOTHING = ["H&M 0412", "ZARA 2210", "PEEK & CLOPPENBURG"]

for m in month_starts():
    mm = f"{m:%m/%Y}"
    extra = {6: "Urlaubsgeld", 11: "Weihnachtsgeld"}.get(m.month)

    # --- income into the joint account
    add("homebank_joint", day(m, 28), 3400, "Muster Technik GmbH", "AT009900000000009001", f"Gehalt {mm}", "Gutschrift")
    add("homebank_joint", day(m, 25), 3050, "Beispiel Handels AG", "AT009900000000009002", f"Lohn {mm}", "Gutschrift")
    if extra:
        add("homebank_joint", day(m, 28), 3400, "Muster Technik GmbH", "AT009900000000009001", extra, "Gutschrift")
        add("homebank_joint", day(m, 25), 3050, "Beispiel Handels AG", "AT009900000000009002", extra, "Gutschrift")

    # --- standing orders from the joint account
    transfer("homebank_joint", "homebank_loan", day(m, 1), INSTALLMENT, "Kreditrate")
    transfer("homebank_joint", "homebank_savings_alex", day(m, 2), 300, "Sparen")
    transfer("homebank_joint", "homebank_savings_anna", day(m, 2), 300, "Sparen")
    transfer("homebank_joint", "traderepublic_cash", day(m, 3), 400, "Trade Republic")
    transfer("homebank_joint", "broker_alex", day(m, 5), 300, "ETF Sparplan")
    transfer("homebank_joint", "broker_anna", day(m, 5), 200, "ETF Sparplan")
    # Revolut top-up: checking side has Revolut's IBAN; Revolut's export has no IBAN -> text rule
    add("homebank_joint", day(m, 2), -600, "Revolut Bank UAB", IBAN["revolut_alex"], "Top-up", "Überweisung")
    add("revolut_alex", day(m, 3), 600, "Payment from Alex Muster", kind="TOPUP")
    if m.month == 8:
        transfer("homebank_savings_alex", "homebank_joint", day(m, 4), 2000, "Urlaub")
        add("homebank_joint", day(m, 5), -1500, "Revolut Bank UAB", IBAN["revolut_alex"], "Top-up Urlaub", "Überweisung")
        add("revolut_alex", day(m, 6), 1500, "Payment from Alex Muster", kind="TOPUP")
    if m.month == 6:  # sold company shares, wired to the checking account (no IBAN in the statement)
        add("homebank_joint", day(m, 18), 8000, "Stock Plan Broker LLC", "", "WIRE TRANSFER STOCK PLAN", "Gutschrift")

    # --- joint: fixed costs
    add("homebank_joint", day(m, 1), -280, "Hausverwaltung Beispiel GmbH", "AT009900000000009100", "Betriebskosten Top 7", "Dauerauftrag")
    add("homebank_joint", day(m, 10), -rng.uniform(95, 140), "Wien Energie GmbH", "AT009900000000009101", "Teilbetrag Strom Gas", "Lastschrift")
    add("homebank_joint", day(m, 15), -64.50, "Generali Versicherung AG", "AT009900000000009102", "Haushaltsversicherung", "Lastschrift")
    add("homebank_joint", day(m, 20), -45.00, "Magenta Telekom", "AT009900000000009103", "Internet", "Lastschrift")
    add("homebank_joint", day(m, 12), -15.30, "ORF-Beitrag Service GmbH", "AT009900000000009104", "ORF Beitrag", "Lastschrift")
    add("homebank_joint", day(m, 4), -9.90, "HoT Telekom", "AT009900000000009300", "Mobilfunk", "Lastschrift")
    add("homebank_joint", day(m, 1), -79.00, "John Harris Fitness", "AT009900000000009301", "Mitgliedsbeitrag", "Lastschrift")
    if m.month == 1:
        add("homebank_joint", day(m, 8), -365, "Wiener Linien", "AT009900000000009105", "Jahreskarte", "Lastschrift")

    # --- joint: variable
    for _ in range(rng.randint(14, 20)):
        pos("homebank_joint", rand_day(m), 9, 115, GROCERIES)
    for _ in range(rng.randint(3, 5)):
        pos("homebank_joint", rand_day(m), 6, 45, DRUGSTORE)
    for _ in range(rng.randint(1, 3)):
        pos("homebank_joint", rand_day(m), 45, 78, FUEL)
    for _ in range(rng.randint(1, 3)):
        pos("homebank_joint", rand_day(m), 25, 120, CLOTHING)
    for _ in range(rng.randint(0, 2)):
        d = rand_day(m)
        add("homebank_joint", d, -100, "BANKOMAT", memo=f"Bankomat {d:%d.%m.} AT", kind="Behebung")
    for _ in range(rng.randint(0, 2)):  # deliberately uncategorized
        add("homebank_joint", rand_day(m), -rng.uniform(9, 60), "PayPal Europe S.a.r.l.", "LU009900000000009302",
            f"PP.{rng.randint(1000, 9999)}.PP . Ihr Einkauf bei {rng.choice(['Kleinanzeigen', 'Etsy Seller', 'Pte Ltd'])}", "Lastschrift")

    # --- savings interest (quarterly on the savings accounts, monthly on Trade Republic)
    if m.month in (3, 6, 9, 12):
        add("homebank_savings_alex", day(m, 28), rng.uniform(40, 60), "Beispiel Bank AG", memo="Habenzinsen", kind="Gutschrift")
        add("homebank_savings_anna", day(m, 28), rng.uniform(50, 70), "Beispiel Bank AG", memo="Habenzinsen", kind="Gutschrift")
    add("traderepublic_cash", day(m, 1), rng.uniform(10, 16), "Trade Republic Bank GmbH", memo="Zinsen", kind="Gutschrift")
    add("traderepublic_cash", day(m, 2), -300, "Trade Republic Bank GmbH", memo="Sparplan Ausfuehrung ETF", kind="Lastschrift")

    # --- Revolut: Alex's personal card
    card(day(m, 3), 10.99, "Spotify")
    card(day(m, 2), 29.90, "FITINN Fitnesscenter")
    card(day(m, 9), 13.99, "Netflix")
    card(day(m, 18), 25.00, "A1 Telekom Austria")
    for _ in range(rng.randint(2, 5)):
        card(rand_day(m), rng.uniform(15, 80), rng.choice(RESTAURANTS))
    for _ in range(rng.randint(1, 3)):
        card(rand_day(m), rng.uniform(15, 120), "Amazon")
    if rng.random() < 0.4:
        card(rand_day(m), rng.uniform(40, 260), rng.choice(["Bikeshop Beispiel", "Gigasport"]))
    if m.month == 8:
        card(day(m, 7), 1240.00, "Booking.com")
        card(day(m, 7), 642.40, "Austrian Airlines")
    if m.month == 3:
        card(day(m, 6), 318.00, "Ski amade")
    if m.month == 12:
        for _ in range(4):
            card(rand_day(m), rng.uniform(30, 90), "Thalia")  # gifts
    if m.month == 4:  # US trip: exchange EUR -> USD, pay in USD
        add("revolut_alex", day(m, 10), -1000, "Exchanged to USD", kind="EXCHANGE")
        add("revolut_alex", day(m, 10), 1082, "Exchanged from EUR", kind="EXCHANGE", currency="USD")
        card(day(m, 12), 412.00, "Hotel Midtown NYC", "USD")
        card(day(m, 13), 86.40, "Whole Foods Market", "USD")
        card(day(m, 14), 54.20, "Uber", "USD")
        card(day(m, 15), 200.00, "Cash withdrawal at Chase ATM", "USD", fee=4.00, kind="ATM")
        add("revolut_alex", day(m, 16), -120, "Broadway Tickets", kind="CARD_PAYMENT", currency="USD", state="REVERTED")
        add("revolut_alex", day(m, 16), -35, "Pending coffee", kind="CARD_PAYMENT", currency="USD", state="PENDING")

# A legit duplicate: two identical purchases on the same day (must NOT be deduplicated away).
dup = Tx("homebank_joint", date(2026, 9, 12), money(-3.20), "BILLA DANKT 1234", "", "POS 1111 K1 12.09. 08:01", "Kartenzahlung")
txs.extend([dup, replace(dup)])
txs.sort(key=lambda t: (t.date, t.account))


# ---------------------------------------------------------------- writers
def de_amount(x: Decimal) -> str:
    return f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def write_bank_a(path: Path, rows: list[Tx]):
    with path.open("w", encoding="cp1252", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Buchungsdatum", "Valutadatum", "Partnername", "Partner IBAN", "Buchungstext",
                    "Verwendungszweck", "Betrag", "Währung"])
        for t in rows:
            w.writerow([f"{t.date:%d.%m.%Y}", f"{t.date:%d.%m.%Y}", t.payee, t.iban, t.kind, t.memo,
                        de_amount(t.amount), t.currency])


def write_revolut(path: Path, rows: list[Tx], opening: dict[str, Decimal]):
    bal = dict(opening)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Type", "Product", "Started Date", "Completed Date", "Description", "Amount", "Fee",
                    "Currency", "State", "Balance"])
        for t in rows:
            done = t.state == "COMPLETED"
            if done:
                bal[t.currency] = bal.get(t.currency, Decimal(0)) + t.amount - t.fee
            ts = f"{t.date} {rng.randint(8, 21):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}"
            w.writerow([t.kind, "Current", ts, ts if done else "", t.payee, f"{t.amount:.2f}", f"{t.fee:.2f}",
                        t.currency, t.state, f"{bal.get(t.currency, 0):.2f}" if done else ""])


def write_camt(path: Path, account: str, rows: list[Tx]):
    entries = []
    for t in rows:
        incoming = t.amount > 0
        party, acct = ("Dbtr", "DbtrAcct") if incoming else ("Cdtr", "CdtrAcct")
        iban_xml = f"<{acct}><Id><IBAN>{t.iban}</IBAN></Id></{acct}>" if t.iban else ""
        entries.append(f"""
      <Ntry>
        <Amt Ccy="{t.currency}">{abs(t.amount):.2f}</Amt>
        <CdtDbtInd>{"CRDT" if incoming else "DBIT"}</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><Dt>{t.date.isoformat()}</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <RltdPties><{party}><Nm>{escape(t.payee)}</Nm></{party}>{iban_xml}</RltdPties>
          <RmtInf><Ustrd>{escape(t.memo)}</Ustrd></RmtInf>
        </TxDtls></NtryDtls>
      </Ntry>""")
    path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.08">
  <BkToCstmrStmt>
    <GrpHdr><MsgId>SAMPLE-{account}-{path.stem}</MsgId><CreDtTm>2026-11-15T10:00:00</CreDtTm></GrpHdr>
    <Stmt>
      <Id>{path.stem}</Id>
      <Acct><Id><IBAN>{IBAN[account]}</IBAN></Id><Ccy>EUR</Ccy></Acct>{''.join(entries)}
    </Stmt>
  </BkToCstmrStmt>
</Document>
""", encoding="utf-8")


def main(out: Path):
    if out.exists():
        shutil.rmtree(out)
    (out / "raw").mkdir(parents=True)
    months = list(month_starts())

    with (out / "accounts.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["account", "name", "owner", "type", "iban", "format"])
        w.writerows(ACCOUNTS)

    # FX: 1 USD = rate EUR, one rate per month
    usd = {m: Decimal(str(round(0.925 + rng.uniform(-0.03, 0.03), 4))) for m in months}
    with (out / "fx.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month", "currency", "rate"])
        for m in months:
            w.writerow([f"{m:%Y-%m}", "USD", usd[m]])

    # Exports: overlapping 2-month windows, like a real monthly "last 2 months" routine.
    for account, *_, fmt in ACCOUNTS:
        if not fmt:
            continue
        folder = out / "raw" / account
        folder.mkdir()
        opening = {"EUR": Decimal(START_BALANCE.get(account, 0))}
        for i in range(len(months)):
            a = months[max(0, i - 1)]
            b = month_end(months[i]) + timedelta(days=1)
            rows = [t for t in txs if t.account == account and a <= t.date < b]
            if fmt == "bank_a_csv":
                write_bank_a(folder / f"{months[i]:%Y-%m}_export.csv", rows)
            elif fmt == "revolut_csv":
                before = [t for t in txs if t.account == account and t.date < a and t.state == "COMPLETED"]
                op = dict(opening)
                for t in before:
                    op[t.currency] = op.get(t.currency, Decimal(0)) + t.amount - t.fee
                write_revolut(folder / f"account-statement_{months[i]:%Y-%m}.csv", rows, op)
            else:
                write_camt(folder / f"{months[i]:%Y-%m}_statement.xml", account, rows)

    # Month-end balances, as you'd read them from the apps.
    bal = {k: Decimal(v) for k, v in START_BALANCE.items()}
    revolut_usd = Decimal(0)
    stock_usd = STOCK_START_USD
    with (out / "balances.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "account", "balance", "currency"])
        for m in months:
            end = month_end(m)
            for t in txs:
                if m <= t.date <= end and t.state == "COMPLETED":
                    delta = t.amount - t.fee
                    if t.account == "revolut_alex" and t.currency == "USD":
                        revolut_usd += delta
                    elif t.account == "homebank_loan":
                        pass  # handled below
                    else:
                        bal[t.account] = bal.get(t.account, Decimal(0)) + delta
            # broker accounts: contributions (already added via transfers) + market moves
            for acc in ("traderepublic_portfolio", "broker_alex", "broker_anna"):
                if acc == "traderepublic_portfolio":
                    bal[acc] += 300  # TR savings plan (TR cash leg is in the export)
                bal[acc] = (bal[acc] * Decimal(str(1 + rng.gauss(0.006, 0.03)))).quantize(Decimal("0.01"))
            # company stock in USD: quarterly vesting, a sale in June, market moves
            if m.month in (2, 5, 8, 11):
                stock_usd += 4500
            if m.month == 6:
                stock_usd -= (Decimal(8000) / usd[m]).quantize(Decimal("0.01"))
            stock_usd = (stock_usd * Decimal(str(1 + rng.gauss(0.008, 0.05)))).quantize(Decimal("0.01"))
            # loan: interest accrues, installment pays it plus principal
            interest = (-bal["homebank_loan"] * LOAN_RATE / 12).quantize(Decimal("0.01"))
            bal["homebank_loan"] += INSTALLMENT - interest

            if m.month == 1:
                bal["apartment"] = Decimal(318000)  # yearly re-estimate
            for account, *_ in ACCOUNTS:
                if account == "stock_alex":
                    w.writerow([end.isoformat(), account, f"{stock_usd:.2f}", "USD"])
                else:
                    w.writerow([end.isoformat(), account, f"{bal[account]:.2f}", "EUR"])
            w.writerow([end.isoformat(), "revolut_alex", f"{revolut_usd:.2f}", "USD"])

    rules = (REPO / "examples" / "rules.csv").read_text().splitlines(keepends=True)
    (out / "rules.csv").write_text(rules[0] + SAMPLE_RULES + "".join(rules[1:]))
    shutil.copy(REPO / "examples" / "categories.csv", out / "categories.csv")
    (out / "overrides.csv").write_text("tx_id,category,note\n")
    print(f"Sample data written to {out.name}/: {len(txs)} transactions, {len(ACCOUNTS)} accounts, {len(months)} months")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO / "sample")
