from __future__ import annotations

from app.services.jd_fetch import FetchResult


async def fetch_jd_browser(url: str, timeout_ms: int = 45000) -> FetchResult:
    """Optional Playwright Chromium fallback for public JD pages."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return FetchResult(ok=False, reason="playwright_missing")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1500)
            text = await page.inner_text("body")
            await browser.close()
            text = (text or "").strip()
            if len(text) < 200:
                return FetchResult(ok=False, text=text, reason="browser_too_short")
            return FetchResult(ok=True, text=text)
    except Exception as exc:  # noqa: BLE001
        return FetchResult(ok=False, reason=f"browser_error:{exc}")
