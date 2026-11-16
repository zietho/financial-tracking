"""Run bank fetchers.

Usage:
    uv run python -m fetch            # all banks in fetch/banks/
    uv run python -m fetch <bank> ... # only these

Each module in fetch/banks/ (not starting with "_") exposes
    ACCOUNTS: list[str]                      # target folders under raw/
    def run(ctx: BrowserContext) -> None     # log in, export, save downloads
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

from playwright.sync_api import sync_playwright

from . import banks
from .common import open_browser


def available() -> list[str]:
    return [m.name for m in pkgutil.iter_modules(banks.__path__) if not m.name.startswith("_")]


def main() -> None:
    names = sys.argv[1:] or available()
    if not names:
        raise SystemExit("No fetchers yet. Record one first: see docs/recording.md")
    with sync_playwright() as p:
        for name in names:
            module = importlib.import_module(f"{__package__}.banks.{name}")
            print(f"\n=== {name} ===")
            ctx = open_browser(p, name)
            try:
                module.run(ctx)
            finally:
                ctx.close()


if __name__ == "__main__":
    main()
