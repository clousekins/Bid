from __future__ import annotations

import asyncio

import pytest

from app.db.enums import ApplySessionStatus
from app.services.apply_session import ApplySessionManager
from app.services.browser_attach import BrowserAttach


@pytest.mark.asyncio
async def test_start_clears_prior_sessions():
    mgr = ApplySessionManager()

    async def noop(session):
        await asyncio.sleep(60)

    await mgr.start(1, apply_url="https://example.com/1", runner=noop)
    await mgr.start(2, apply_url="https://example.com/2", runner=noop)
    assert mgr.get(1) is None
    assert mgr.get(2) is not None
    await mgr.end_session(2, status=ApplySessionStatus.CANCELLED)


@pytest.mark.asyncio
async def test_mark_applied_ends_session():
    mgr = ApplySessionManager()

    async def noop(session):
        await asyncio.sleep(60)

    await mgr.start(9, apply_url="https://example.com/9", runner=noop)
    await mgr.end_session(9, status=ApplySessionStatus.APPLIED)
    assert mgr.get(9) is None


@pytest.mark.asyncio
async def test_attach_opens_in_octo_when_ui_tab_missing(monkeypatch):
    """When ui tab is missing but target_url set, open URL inside CDP browser."""

    class FakePage:
        url = "about:blank"

        async def goto(self, url, wait_until="domcontentloaded"):
            self.url = url

    class FakeContext:
        def __init__(self):
            self.pages = []

        async def new_page(self):
            p = FakePage()
            self.pages.append(p)
            return p

    class FakeBrowser:
        def __init__(self):
            self.contexts = [FakeContext()]

        async def close(self):
            return None

    class FakeChromium:
        @staticmethod
        async def connect_over_cdp(_url):
            return FakeBrowser()

    class FakePW:
        chromium = FakeChromium()

        async def stop(self):
            return None

    class FakeAsyncPlaywrightModule:
        async def start(self):
            return FakePW()

    def fake_async_playwright():
        return FakeAsyncPlaywrightModule()

    class Settings:
        browser_cdp_url = "http://127.0.0.1:9222"
        octo_enabled = False
        octo_profile_uuid = ""
        apply_allow_launch_chromium = False

    import sys
    from types import ModuleType

    import app.services.browser_attach as ba

    monkeypatch.setattr(ba, "get_settings", lambda: Settings())
    fake_mod = ModuleType("playwright.async_api")
    fake_mod.async_playwright = fake_async_playwright
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_mod)

    attach = BrowserAttach()
    result = await attach.attach(
        target_url="https://boards.greenhouse.io/acme/jobs/1",
        ui_opened_tab=True,
        wait_seconds=0.2,
    )
    assert result.ok is True
    assert result.reason == "opened_in_octo"
    assert "greenhouse" in result.page.url
