"""Review transactions in the terminal, no LLM involved. Run `make all` first.

    uv run python scripts/review.py              # unusual expenses (make review)
    uv run python scripts/review.py uncategorized   # largest uncategorized first (make categorize)

For each transaction type one of:
    Enter              skip
    r [note]           reviewed: fine as it is, hide it from "Anything unusual?"  -> reviewed.csv
    c <Category> [| note]  this transaction only gets <Category>                   -> overrides.csv
    a <Category>       always: every transaction from this payee gets <Category>    -> rules.csv
    q                  quit
Then run `make all` to see the result.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("FT_DATA") or REPO)


def append_csv(path: Path, header: list[str], row: list[str]) -> None:
    new = not path.exists() or not path.read_text().strip()
    with path.open("a", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(header)
        w.writerow(row)


def add_rule(payee: str, category: str) -> str:
    """Insert a payee rule near the top of rules.csv (first match wins, fallbacks stay last)."""
    words = re.sub(r"\s+\d[\d ]*$", "", payee).strip() or payee  # drop trailing terminal numbers
    pattern = "^" + re.escape(words).replace("\\ ", " ").replace(",", r"\x2c")
    path = DATA / "rules.csv"
    lines = path.read_text().splitlines()
    i = 1
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    lines[i:i] = [f"# added by review: {words}", f"{pattern},{category},"]
    path.write_text("\n".join(lines) + "\n")
    return pattern


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "unusual"
    con = duckdb.connect(str(DATA / "out" / "finance.duckdb"), read_only=True)
    categories = {c for (c,) in con.execute("SELECT category FROM categories").fetchall()}
    if mode == "uncategorized":
        rows = con.execute("""SELECT tx_id, date, account, payee, memo, amount, 'not categorized yet' FROM tx
                              WHERE category = 'Uncategorized' ORDER BY abs(amount) DESC""").fetchall()
    else:
        rows = con.execute("""SELECT u.tx_id, u.date, u.account_name, u.payee, t.memo, -u.spent, u.reason
                              FROM unusual u JOIN tx t USING (tx_id) ORDER BY u.spent DESC""").fetchall()
    con.close()
    if not rows:
        print("Nothing to review.")
        return
    print(__doc__.split("For each")[1].split("Then run")[0])
    done = 0
    for n, (tx_id, d, account, payee, memo, amount, reason) in enumerate(rows, 1):
        print(f"\n[{n}/{len(rows)}] {d}  {amount:>10,.2f} €  {payee}\n          {account} · {reason}\n          {(memo or '')[:110]}")
        while True:
            ans = input("> ").strip()
            if ans in ("", "s"):
                break
            if ans == "q":
                print(f"\n{done} saved. Run `make all` to update the report.")
                return
            cmd, _, rest = ans.partition(" ")
            if cmd == "r":
                append_csv(DATA / "reviewed.csv", ["tx_id", "note"], [tx_id, rest.strip()])
            elif cmd in ("c", "a"):
                category, _, note = rest.partition("|")
                category = category.strip()
                match = next((c for c in categories if c.lower() == category.lower()), None)
                if not match:
                    print("  unknown category. Known:", ", ".join(sorted(categories)))
                    continue
                if cmd == "c":
                    append_csv(DATA / "overrides.csv", ["tx_id", "category", "note"], [tx_id, match, note.strip()])
                else:
                    print("  rule added:", add_rule(payee or "", match))
            else:
                print("  ? Enter = skip, r = reviewed, c <Category> = this one, a <Category> = always, q = quit")
                continue
            done += 1
            break
    print(f"\nAll done, {done} saved. Run `make all` to update the report.")


if __name__ == "__main__":
    main()
