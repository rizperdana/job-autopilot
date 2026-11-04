"""Browser launch helpers — Playwright Chromium with a persistent user-data-dir.

Stdlib + playwright only. Reuses the Chromium already in ~/.cache/ms-playwright
(chromium-1243 verified); never downloads a browser.
"""
import os
from pathlib import Path

from . import config

def browser_profile_dir():
    return config.runtime_dir() / "browser-profile"

def _chromium_executable():
    # prefer the newest cached chromium; fall back to playwright's default
    cache = Path.home() / ".cache" / "ms-playwright"
    if cache.exists():
        cands = sorted(cache.glob("chromium-*/chrome-linux*/chrome"), reverse=True)
        if cands:
            return str(cands[0])
        cands = sorted(cache.glob("chromium-*/chrome"), reverse=True)
        if cands:
            return str(cands[0])
    return None

def launch(headless=True, timeout_ms=30000):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    exe = _chromium_executable()
    kwargs = {
        "user_data_dir": str(browser_profile_dir()),
        "headless": bool(headless),
        "args": ["--no-sandbox", "--disable-dev-shm-usage",
                 "--disable-blink-features=AutomationControlled"],
        "timeout": timeout_ms,
    }
    if exe:
        kwargs["executable_path"] = exe
    ctx = pw.chromium.launch_persistent_context(**kwargs)
    ctx.set_default_timeout(timeout_ms)
    ctx.set_default_navigation_timeout(timeout_ms)
    return pw, ctx

def new_page(ctx):
    page = ctx.new_page()
    return page

def close(pw, ctx):
    try:
        ctx.close()
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass