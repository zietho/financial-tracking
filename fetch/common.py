"""Shared helpers for bank fetchers.

Security rules (see CONCEPT.md):
- Browser profiles live outside the repo and are readable only by this user.
- No Playwright tracing, video or screenshots anywhere in this package.
- Passwords come from the macOS Keychain at runtime and are never logged.
"""

from __future__ import annotations

import os
import shutil
from datetime import date
from pathlib import Path

from playwright.sync_api import BrowserContext, Download, Page, Playwright

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "raw"
PROFILE_ROOT = Path.home() / "Library" / "Application Support" / "financial-tracking" / "profiles"

KEYCHAIN_PREFIX = "finance-"


def profile_dir(bank: str) -> Path:
    path = PROFILE_ROOT / bank
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(PROFILE_ROOT, 0o700)
    os.chmod(path, 0o700)
    return path


def open_browser(p: Playwright, bank: str) -> BrowserContext:
    """Visible browser with a persistent per-bank profile.

    Uses the installed Google Chrome when available: banks are less likely to
    flag it than Playwright's bundled Chromium, and it keeps the trusted-device
    cookies between runs.
    """
    kwargs = dict(
        user_data_dir=str(profile_dir(bank)),
        headless=False,
        accept_downloads=True,
        no_viewport=True,
        args=["--start-maximized"],
    )
    if shutil.which("google-chrome") or Path("/Applications/Google Chrome.app").exists():
        kwargs["channel"] = "chrome"
    return p.chromium.launch_persistent_context(**kwargs)


def first_page(ctx: BrowserContext) -> Page:
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def get_password(bank: str, username: str) -> str:
    """Read the password from the macOS Keychain (service 'finance-<bank>').

    macOS asks for permission on each run unless you chose 'Always Allow'.
    Store it once with:
        security add-generic-password -s finance-<bank> -a <username> -w
    """
    import keyring

    pw = keyring.get_password(KEYCHAIN_PREFIX + bank, username)
    if not pw:
        raise SystemExit(
            f"No Keychain entry 'finance-{bank}' for this username. "
            f"Create it with: security add-generic-password -s finance-{bank} -a <username> -w"
        )
    return pw


def wait_for_login(page: Page, logged_in_selector: str, timeout_min: int = 5) -> None:
    """Block until an element that only exists after login appears.

    You approve 2FA in the bank app meanwhile.
    """
    print(f"Waiting up to {timeout_min} min for login and 2FA approval ...")
    page.wait_for_selector(logged_in_selector, timeout=timeout_min * 60_000)
    print("Logged in.")


def save_download(download: Download, account: str) -> Path:
    """Store a download as raw/<account>/<today>_<original name>."""
    target_dir = RAW / account
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{date.today():%Y-%m-%d}_{download.suggested_filename}"
    download.save_as(target)
    print(f"Saved {target.relative_to(REPO)}")
    return target
