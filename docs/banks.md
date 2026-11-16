# Banks and export checklist

No usernames, passwords, full IBANs or bank names beyond Revolut and Trade Republic here.
Your real accounts live in `accounts.csv` (not committed).

| Account | Owner | Type | Data | Parser |
|---|---|---|---|---|
| Joint checking | both | checking | transactions | your bank's CSV parser, or `enablebanking_json` |
| Savings A / B | A / B | savings | transactions | same as checking |
| Revolut | A | checking (EUR + USD) | transactions | `revolut_csv` or `enablebanking_json` |
| Trade Republic cash | A | savings | transactions | CSV transaction export (app, since April 2026) |
| Trade Republic portfolio | A | investment | balance | – |
| Company stock (US broker) | A | investment, USD | balance | – (needs fx.csv) |
| Broker A / B | A / B | investment | balance (+ IBAN of the cash account) | – |
| Home loan | both | loan | balance (negative) + IBAN | – |
| Home (optional) | both | property | estimated value, yearly | – |

Parsers for your own banks go into `pipeline/private_parsers.py` (gitignored), see the
comment at the end of `pipeline/parsers.py`.

## Per bank, note

- Login URL and menu path to the export
- Formats offered: CAMT.053 / CSV / PDF
- CSV details: delimiter, decimal separator, date format, encoding
- Counterparty IBAN on transfers: yes / no
- Max history per export
- Time for a manual export
