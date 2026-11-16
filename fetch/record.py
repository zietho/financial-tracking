"""Record a bank's export flow without recording the login.

Usage:
    uv run python -m fetch.record <bank> <login-url>

Opens Chrome with the persistent profile for <bank> plus the Playwright
Inspector. Recording is OFF at the start, so log in and approve 2FA first,
then press "Record" in the Inspector. See docs/recording.md.
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

from .common import first_page, open_browser

STEPS = """
Recording a bank export
-----------------------
1. Log in in the browser window and approve 2FA in the bank app.
   Recording is OFF, so nothing you type here is captured.
2. In the Playwright Inspector, set the target to "Python" (Library, sync).
3. Click "Record" and do the export: menu, account, date range, format, download.
   Prefer clicking over typing. Don't type passwords while recording.
4. Click "Record" again to stop. Copy the generated code into
   recordings/<bank>.py (git-ignored) and tell Claude.
5. Close the Inspector or press "Resume" to end.
"""


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    bank, url = sys.argv[1], sys.argv[2]
    print(STEPS)
    with sync_playwright() as p:
        ctx = open_browser(p, bank)
        page = first_page(ctx)
        page.goto(url)
        page.pause()  # opens the Inspector; recording only starts when you press Record
        ctx.close()


if __name__ == "__main__":
    main()
