"""Fetch transactions and balances through the Enable Banking API (PSD2).

Free for private individuals in "restricted mode": only accounts you linked to
your own application are returned. See docs/enablebanking.md for the one-time
setup in the Enable Banking control panel.

Usage (or the make targets eb-setup / eb-banks / eb-link / eb-fetch):
    uv run python -m fetch.enablebanking setup <app-id> <path/to/private.pem> [redirect-url]
    uv run python -m fetch.enablebanking banks [country]
    uv run python -m fetch.enablebanking link "<bank name>" [country]
    uv run python -m fetch.enablebanking fetch [--days N]
    uv run python -m fetch.enablebanking status

Security:
- The private key lives in the macOS Keychain (service 'finance-enablebanking'),
  not on disk. `setup` imports it; delete the downloaded .pem afterwards.
- Sessions (which, together with the key, grant read access) are stored in
  ~/Library/Application Support/financial-tracking/enablebanking.json, mode 600.
- Nothing here logs amounts, names or IBANs beyond the last 4 IBAN digits.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API = "https://api.enablebanking.com"
KEYCHAIN_SERVICE = "finance-enablebanking"
STATE_DIR = Path.home() / "Library" / "Application Support" / "financial-tracking"
STATE_FILE = STATE_DIR / "enablebanking.json"
# Must be registered as redirect URL of the app. It never has to load: the
# browser opened by `link` intercepts it and reads the code from the URL.
DEFAULT_REDIRECT = "https://localhost/enablebanking/callback"
# Preferred balance types, best first (ISO 20022 codes).
BALANCE_PREFERENCE = ["CLBD", "ITBD", "XPCD", "CLAV", "ITAV", "ITBA", "OPBD"]

REPO = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    return Path(os.environ.get("FT_DATA") or REPO)


# ------------------------------------------------------------------ state
def load_state() -> dict:
    if not STATE_FILE.exists():
        raise SystemExit("Not set up yet. Run: make eb-setup APP=<app-id> KEY=<path/to/private.pem>")
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE_FILE)


def private_key(app_id: str) -> str:
    import keyring

    pem = keyring.get_password(KEYCHAIN_SERVICE, app_id)
    if not pem:
        raise SystemExit(f"No private key in Keychain ({KEYCHAIN_SERVICE}). Run make eb-setup again.")
    return pem


# ------------------------------------------------------------------ API
def make_jwt(app_id: str, pem: str, now: int | None = None) -> str:
    import jwt

    now = int(now if now is not None else time.time())
    claims = {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": now, "exp": now + 3600}
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": app_id})


class Client:
    def __init__(self, app_id: str, pem: str):
        self.token = make_jwt(app_id, pem)

    def call(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
        url = API + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        try:
            import ssl

            import certifi

            ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise SystemExit(f"Enable Banking {method} {path} failed: HTTP {e.code} {detail}") from None


def client() -> tuple[Client, dict]:
    state = load_state()
    return Client(state["app_id"], private_key(state["app_id"])), state


# ------------------------------------------------------------------ commands
def cmd_setup(app_id: str, pem_path: str, redirect: str = DEFAULT_REDIRECT) -> None:
    import keyring

    pem = Path(pem_path).expanduser().read_text()
    if "PRIVATE KEY" not in pem:
        raise SystemExit(f"{pem_path} doesn't look like a PEM private key")
    make_jwt(app_id, pem)  # fails early on a broken key
    keyring.set_password(KEYCHAIN_SERVICE, app_id, pem)
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {"sessions": []}
    state.update(app_id=app_id, redirect_url=redirect)
    save_state(state)
    print(f"Key stored in Keychain ({KEYCHAIN_SERVICE}), config in {STATE_FILE}")
    print(f"You can now delete {pem_path}.")


def cmd_banks(country: str = "AT") -> None:
    c, _ = client()
    for a in c.call("GET", "/aspsps", query={"country": country.upper(), "psu_type": "personal"})["aspsps"]:
        days = (a.get("maximum_consent_validity") or 0) // 86400
        print(f"{a['name']:<45} consent max {days:>3} days{'  (beta)' if a.get('beta') else ''}")


def wait_for_code(auth_url: str, redirect: str) -> str:
    """Open the bank's consent page and return the `code` from the redirect.

    Uses a persistent Playwright profile; the redirect is intercepted so the
    callback URL never has to exist. Falls back to pasting the URL by hand.
    """
    try:
        from playwright.sync_api import sync_playwright

        from .common import open_browser, first_page
    except ImportError:
        sync_playwright = None
    if sync_playwright is not None:
        captured: dict[str, str] = {}
        with sync_playwright() as p:
            ctx = open_browser(p, "enablebanking")
            try:
                page = first_page(ctx)

                def handle(route):
                    captured["url"] = route.request.url
                    route.fulfill(status=200, content_type="text/html",
                                  body="<h2>Done. You can close this window.</h2>")

                ctx.route(redirect.split("?")[0] + "*", handle)
                page.goto(auth_url)
                print("Approve access in the browser (and in your bank app). Waiting up to 10 min ...")
                deadline = time.time() + 600
                while "url" not in captured and time.time() < deadline:
                    page.wait_for_timeout(500)
            finally:
                ctx.close()
        url = captured.get("url")
        if not url:
            raise SystemExit("Timed out waiting for the bank to redirect back.")
    else:
        print(f"Open this URL, approve access, then paste the URL you land on:\n{auth_url}")
        url = input("Redirect URL: ").strip()
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if "error" in params:
        raise SystemExit(f"Bank returned an error: {params['error'][0]} {params.get('error_description', [''])[0]}")
    if "code" not in params:
        raise SystemExit("No code in the redirect URL.")
    return params["code"][0]


def our_accounts() -> dict[str, str]:
    """IBAN -> account id from accounts.csv."""
    path = data_dir() / "accounts.csv"
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        rows = [r for r in csv.DictReader(line for line in f if not line.startswith("#"))]
    return {r["iban"].replace(" ", "").upper(): r["account"] for r in rows if r.get("iban")}


def cmd_link(bank: str, country: str = "AT") -> None:
    c, state = client()
    aspsps = c.call("GET", "/aspsps", query={"country": country.upper(), "psu_type": "personal"})["aspsps"]
    match = [a for a in aspsps if a["name"].lower() == bank.lower()] or \
            [a for a in aspsps if bank.lower() in a["name"].lower()]
    if len(match) != 1:
        names = ", ".join(a["name"] for a in match) or "none"
        raise SystemExit(f"'{bank}' matches {names}. Check `make eb-banks` for the exact name.")
    aspsp = match[0]
    max_days = (aspsp.get("maximum_consent_validity") or 90 * 86400) // 86400
    valid_until = datetime.now(timezone.utc) + timedelta(days=min(max_days, 180))
    nonce = str(uuid.uuid4())
    auth = c.call("POST", "/auth", body={
        "access": {"valid_until": valid_until.isoformat()},
        "aspsp": {"name": aspsp["name"], "country": aspsp["country"]},
        "state": nonce,
        "redirect_url": state["redirect_url"],
        "psu_type": "personal",
    })
    code = wait_for_code(auth["url"], state["redirect_url"])
    session = c.call("POST", "/sessions", body={"code": code})

    ibans = our_accounts()
    accounts = []
    for a in session.get("accounts", []):
        iban = ((a.get("account_id") or {}).get("iban") or "").replace(" ", "").upper()
        accounts.append({"uid": a["uid"], "iban_last4": iban[-4:], "currency": a.get("currency"),
                         "name": a.get("name") or a.get("product"), "account": ibans.get(iban)})
    state["sessions"] = [s for s in state["sessions"] if s["aspsp"]["name"] != aspsp["name"]]
    state["sessions"].append({
        "session_id": session["session_id"],
        "aspsp": {"name": aspsp["name"], "country": aspsp["country"]},
        "valid_until": session.get("access", {}).get("valid_until", valid_until.isoformat()),
        "accounts": accounts,
    })
    save_state(state)
    print(f"Linked {aspsp['name']} until {valid_until:%Y-%m-%d}:")
    for a in accounts:
        target = a["account"] or "NOT MAPPED: add its IBAN to accounts.csv, then run eb-link again"
        print(f"  ...{a['iban_last4']} {a['currency']} {a['name'] or ''} -> {target}")


def pick_balance(balances: list[dict]) -> dict | None:
    if not balances:
        return None
    rank = {t: i for i, t in enumerate(BALANCE_PREFERENCE)}
    return min(balances, key=lambda b: rank.get(b.get("balance_type"), len(rank)))


def append_balance(account: str, b: dict) -> bool:
    """Add a balance line to balances.csv unless that date/account/currency is already there."""
    path = data_dir() / "balances.csv"
    amount = b["balance_amount"]
    day = b.get("reference_date") or date.today().isoformat()
    new = (day, account, amount["amount"], amount["currency"])
    if path.exists():
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                if (r["date"], r["account"], (r.get("currency") or "EUR")) == (day, account, new[3]):
                    return False
    else:
        path.write_text("date,account,balance,currency\n")
    with path.open("a", newline="") as f:
        csv.writer(f).writerow(new)
    return True


def fetch_transactions(c: Client, uid: str, date_from: date) -> list[dict]:
    out, key = [], None
    while True:
        page = c.call("GET", f"/accounts/{uid}/transactions",
                      query={"date_from": date_from.isoformat(), "continuation_key": key})
        out.extend(page.get("transactions") or [])
        key = page.get("continuation_key")
        if not key:
            return out


def cmd_fetch(days: int = 90) -> None:
    c, state = client()
    if not state.get("sessions"):
        raise SystemExit("No linked banks yet. Run: make eb-link BANK='<bank name>'")
    today = date.today()
    for s in state["sessions"]:
        left = (datetime.fromisoformat(s["valid_until"]) - datetime.now(timezone.utc)).days
        print(f"\n=== {s['aspsp']['name']} (consent: {left} days left) ===")
        if left < 0:
            print("  consent expired, run eb-link again")
            continue
        for a in s["accounts"]:
            if not a["account"]:
                print(f"  ...{a['iban_last4']}: not mapped to an account, skipped")
                continue
            txs = fetch_transactions(c, a["uid"], today - timedelta(days=days))
            balances = c.call("GET", f"/accounts/{a['uid']}/balances").get("balances") or []
            folder = data_dir() / "raw" / a["account"]
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{today:%Y-%m-%d}_enablebanking.json"
            target.write_text(json.dumps({"fetched": today.isoformat(), "aspsp": s["aspsp"]["name"],
                                          "transactions": txs, "balances": balances}, indent=1))
            b = pick_balance(balances)
            added = b is not None and append_balance(a["account"], b)
            print(f"  {a['account']}: {len(txs)} transactions -> {target.relative_to(data_dir())}"
                  f"{', balance added to balances.csv' if added else ''}")
        if 0 <= left <= 14:
            print(f"  ! consent expires in {left} days: run make eb-link BANK='{s['aspsp']['name']}'")


def cmd_status() -> None:
    state = load_state()
    print(f"App {state['app_id']}, redirect {state['redirect_url']}")
    for s in state.get("sessions", []):
        print(f"  {s['aspsp']['name']}: valid until {s['valid_until'][:10]}, "
              f"accounts: {', '.join(a['account'] or '?' for a in s['accounts'])}")


def main(argv: list[str]) -> None:
    if not argv:
        raise SystemExit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "fetch":
        days = int(args[args.index("--days") + 1]) if "--days" in args else 90
        return cmd_fetch(days)
    commands = {"setup": cmd_setup, "banks": cmd_banks, "link": cmd_link, "status": cmd_status}
    if cmd not in commands:
        raise SystemExit(__doc__)
    commands[cmd](*args)


if __name__ == "__main__":
    main(sys.argv[1:])
