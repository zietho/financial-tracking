# Recording a bank export with Playwright

Goal: click through the export once, get Playwright code for it, and turn that
into `fetch/banks/<bank>.py`.

## Why not plain `playwright codegen`?

`codegen` starts recording immediately, so it would record your **username and
password** as `page.fill(...)` lines in the generated code. It also uses a
throwaway browser, so the bank sees a new device every time.

`make record` instead opens Chrome with a **persistent profile per bank** and the
Playwright Inspector with **recording off**. You log in first, then switch
recording on.

## Steps

```bash
make record BANK=<short-name> URL=<login-url>
# e.g. make record BANK=bank_a URL=https://login.bank-a.example
```

1. **Log in** in the Chrome window and approve 2FA in the bank app. Nothing is recorded yet.
2. In the **Playwright Inspector** window, choose target **Python → Library (sync)**.
3. Click **Record** and do the export once:
   account → transactions → date range → format (CAMT.053 if available, else CSV) → download.
   - Click rather than type where possible.
   - If you type a date, that's fine; it gets replaced by the computed range.
   - Don't type passwords or TANs while recording.
4. Click **Record** again to stop.
5. Copy the generated code into `recordings/<bank>.py` (git-ignored, because it can contain
   account numbers or names) and tell Claude. Claude turns it into `fetch/banks/<bank>.py`.
6. Close the Inspector or press **Resume** to end.

Also write down in `docs/banks.md`: login URL, menu path, available formats, whether the
counterparty IBAN appears for transfers.

## Afterwards

```bash
make fetch            # all banks
make fetch BANK=bank_a
```

You log in and approve 2FA; the script does the clicks and saves to `raw/<account>/`.

## Where things are stored

| What | Where | In git? |
|---|---|---|
| Browser profiles (live sessions!) | `~/Library/Application Support/financial-tracking/profiles/<bank>` (mode 700) | no |
| Recordings | `recordings/` | no |
| Downloads | `raw/<account>/` | no |
| Fetcher code | `fetch/banks/<bank>.py` | yes, without usernames or passwords |

To log out a bank's saved session completely, delete its profile folder.

## Alternatives for recording

- **Chrome DevTools → Recorder panel**: records a flow and exports JSON or Puppeteer
  scripts. Works, but you'd translate it to Python, and it also records typed values.
- **Playwright trace viewer**: not used here, because traces capture form inputs.
