# Fetching through Enable Banking (PSD2 API)

Instead of clicking through online banking with Playwright, the bank sends the data
through the official PSD2 interface. [Enable Banking](https://enablebanking.com) is free
for private individuals in **restricted mode**: an app you register only sees the
accounts you linked to it yourself.

- Check `make eb-banks` for your bank. Revolut is very likely covered.
- You approve access in your bank app **once every 90–180 days**, depending on the bank.
  In between, `make eb-fetch` runs without logging in.
- What we get: booked transactions with counterparty name and IBAN, plus the current
  balance. The balance is added to `balances.csv` automatically.

## One-time setup (you, ~15 minutes)

1. Create an account at <https://enablebanking.com> and open the **Control Panel**.
2. **API applications → Register new application:**
   - Environment: **Production**
   - Name: e.g. `household finance`
   - Redirect URL: `https://localhost/enablebanking/callback`
     (it never has to load; our script catches the redirect in the browser)
   - Description / privacy / terms fields, if required: full `https://` URLs
3. Keep the **private key file (`.pem`)** you get, and copy the **Application ID** (a UUID).
4. Click **Activate by linking accounts** and link the accounts you want, at the bank's
   own login page. Until then the app is inactive, and it will only ever see these accounts.
5. In the repo:

   ```bash
   make eb-setup APP=<application-id> KEY=~/Downloads/<file>.pem
   ```

   This puts the key into the macOS Keychain (`finance-enablebanking`). Then delete the
   `.pem` file from Downloads.

## Per bank (every 90–180 days)

```bash
make eb-banks                 # exact bank names for Austria (COUNTRY=DE etc. for others)
make eb-link BANK='<bank name>'
```

A Chrome window opens the bank's consent page. Log in, approve in the bank app, done.
The script maps each account to `accounts.csv` **by IBAN**, so put the IBANs there first
and set `format` to `enablebanking_json` for those accounts:

```csv
account,name,owner,type,iban,format
homebank_joint,Joint checking,both,checking,AT..,enablebanking_json
```

Linking in the control panel (step 4) doesn't replace this step: the API needs its own consent.

## Monthly

```bash
make eb-fetch                 # last 90 days; DAYS=30 is enough monthly
make all && make open
```

Files land in `raw/<account>/<date>_enablebanking.json`. Overlapping fetches are deduplicated.
`make eb-status` shows which banks are linked and until when; `eb-fetch` warns two weeks
before a consent expires.

## Notes

- **Don't mix formats in one account folder.** If an account switches from CSV exports to
  the API, move the old CSVs out or the overlap is counted twice (different text, so the
  dedup can't match them).
- Banks only return about 90 days of history without a fresh approval. For older months,
  use a one-off CSV export in a separate account folder or keep the old files as they are
  and start the API from the first month that isn't covered.
- Where things are stored: the key in the Keychain; the session list in
  `~/Library/Application Support/financial-tracking/enablebanking.json` (mode 600);
  the data in `raw/` (not committed).
- Revoke access any time in the control panel or in your bank's online banking.
