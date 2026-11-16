"""Fill fx.csv with month-end ECB exchange rates.

Usage: uv run python -m pipeline.prices [data_dir]      (make prices)

- fx.csv: for every non-EUR currency in balances.csv, holdings.csv and fx.csv, the ECB
  reference rate of the last trading day of each month, as "1 unit = rate EUR".
  Source: ECB Data Portal (public, no key). Only currency codes are sent.
- prices.csv (share prices for holdings.csv) is not fetched: free price sources block
  automated requests. If you track share counts, enter month-end prices by hand; for a
  plain amount (symbol = currency, e.g. USD) no price is needed.

Fetched months replace existing rows for the same month and currency, so rerunning is
safe. Rows for other currencies stay as they are.
"""

from __future__ import annotations

import csv
import io
import os
import ssl
import sys
import urllib.request
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ECB = "https://data-api.ecb.europa.eu/service/data/EXR/D.{cur}.EUR.SP00.A?format=csvdata&startPeriod={start}"
UA = {"User-Agent": "household-finance-pipeline"}


def ssl_context() -> ssl.SSLContext:
    """uv's Python has no system CA store on macOS: use certifi's bundle."""
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        lines = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(lines)]


def fetch(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30, context=ssl_context()) as r:
        return r.read().decode("utf-8")


def month_end_fx(cur: str, start: str) -> dict[str, str]:
    """{'2026-10': '0.8612', ...}: EUR per 1 unit of `cur`, last ECB fixing of each month."""
    out: dict[str, tuple[str, float]] = {}
    for r in csv.DictReader(io.StringIO(fetch(ECB.format(cur=cur, start=start)))):
        day, val = r.get("TIME_PERIOD", ""), r.get("OBS_VALUE", "")
        if not day or not val:
            continue
        m = day[:7]
        if m not in out or day > out[m][0]:
            out[m] = (day, float(val))
    return {m: f"{1 / v:.6f}" for m, (_, v) in sorted(out.items())}


def write(path: Path, header: list[str], rows: list[dict]) -> None:
    rows.sort(key=lambda r: tuple(r[h] for h in header[:2]))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(data: Path) -> None:
    holdings = read_rows(data / "holdings.csv")
    balances = read_rows(data / "balances.csv")
    fx_rows = read_rows(data / "fx.csv")
    start_dates = [r["date"] for r in balances + holdings if r.get("date")]
    # balances can be calculated back to the first transaction, so cover two years by default
    two_years = f"{date.today().year - 2}-{date.today():%m}"
    start = min([d[:7] for d in start_dates] + [two_years])

    currencies = {(r.get("currency") or "EUR").upper() for r in balances + holdings} | {r["currency"].upper() for r in fx_rows}
    currencies.discard("EUR")
    for cur in sorted(currencies):
        rates = month_end_fx(cur, start)
        fx_rows = [r for r in fx_rows if not (r["currency"].upper() == cur and r["month"][:7] in rates)]
        fx_rows += [{"month": m, "currency": cur, "rate": v} for m, v in rates.items()]
        print(f"fx.csv: {cur} {len(rates)} months from the ECB ({min(rates)} – {max(rates)})")
    if currencies:
        write(data / "fx.csv", ["month", "currency", "rate"], fx_rows)

    missing = sorted({h["symbol"].upper() for h in holdings
                      if h["symbol"].upper() != (h.get("currency") or "EUR").upper()}
                     - {r["symbol"].upper() for r in read_rows(data / "prices.csv")})
    if missing:
        print("prices.csv: add month-end prices by hand for", ", ".join(missing))

if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FT_DATA", REPO)).resolve())
