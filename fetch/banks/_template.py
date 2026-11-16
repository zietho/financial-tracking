"""Template for a bank fetcher. Copy to fetch/banks/<bank>.py.

Filled in from a recording (recordings/<bank>.py): keep the clicks after login,
replace hard-coded dates with the date range below, wrap the download click in
page.expect_download().
"""

from __future__ import annotations

from datetime import date, timedelta

from playwright.sync_api import BrowserContext

from ..common import first_page, save_download, wait_for_login

BANK = "example"
LOGIN_URL = "https://example.com/login"
LOGGED_IN_SELECTOR = "text=Logout"  # something visible only after login
ACCOUNTS = ["example_checking"]

# Always the last ~2 months; overlapping exports are deduplicated later.
DATE_TO = date.today()
DATE_FROM = (DATE_TO.replace(day=1) - timedelta(days=1)).replace(day=1)


def run(ctx: BrowserContext) -> None:
    page = first_page(ctx)
    page.goto(LOGIN_URL)

    # Login stays manual for now (you type, you approve 2FA). Optional later:
    #   page.fill("#user", USERNAME); page.fill("#password", get_password(BANK, USERNAME))
    wait_for_login(page, LOGGED_IN_SELECTOR)

    for account in ACCOUNTS:
        # --- recorded export steps go here ---
        # page.get_by_role("link", name="Umsätze").click()
        # page.get_by_label("Von").fill(DATE_FROM.strftime("%d.%m.%Y"))
        # page.get_by_label("Bis").fill(DATE_TO.strftime("%d.%m.%Y"))
        with page.expect_download() as dl:
            page.get_by_role("button", name="Export").click()
        save_download(dl.value, account)
