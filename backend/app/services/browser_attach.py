from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from app.config import get_settings
from app.services import octo

log = logging.getLogger("poong.browser_attach")
LogFn = Callable[[str, str], None]


@dataclass
class AttachResult:
    ok: bool
    browser: Any = None
    context: Any = None
    page: Any = None
    playwright: Any = None
    reason: str = ""
    created_new_page: bool = False


class BrowserAttach:
    """CDP attach helper.

    Prefer reuse Octo CDP without restarting. If UI window.open tab is not in
    Octo (common when POONG UI runs in normal Chrome), open the URL inside Octo.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def page(self):
        return self._page

    @property
    def browser(self):
        return self._browser

    async def attach(
        self,
        *,
        target_url: str | None = None,
        ui_opened_tab: bool = True,
        wait_seconds: float = 12.0,
        emit: LogFn | None = None,
    ) -> AttachResult:
        from playwright.async_api import async_playwright

        def _emit(msg: str, level: str = "info") -> None:
            log.info("%s", msg)
            if emit:
                emit(msg, level)

        settings = get_settings()
        cdp_url = (settings.browser_cdp_url or "").strip()
        _emit(
            f"Attach begin ui_opened_tab={ui_opened_tab} target={target_url} "
            f"octo_enabled={settings.octo_enabled} manual_cdp={bool(cdp_url)}"
        )

        if settings.octo_enabled and settings.octo_profile_uuid and not cdp_url:
            try:
                # Do NOT force-stop a healthy profile — that wipes tabs mid-apply.
                start = await octo.start_profile_with_debug(force_restart=False, emit=emit)
                endpoint = octo.extract_ws_endpoint(start) or ""
                _emit(f"Octo start/reuse payload endpoint={endpoint or '(none)'} reused={start.get('reused')}")
                if endpoint.startswith(("ws", "http")):
                    cdp_url = endpoint
            except Exception as exc:  # noqa: BLE001
                _emit(f"Octo CDP resolve failed: {exc}", "error")
                return AttachResult(ok=False, reason=f"octo_error:{exc}")

        if not cdp_url:
            if settings.apply_allow_launch_chromium:
                _emit("Falling back to launched Chromium (no cookies) — not recommended", "error")
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=False)
                self._context = await self._browser.new_context()
                self._page = await self._context.new_page()
                if target_url:
                    await self._page.goto(target_url, wait_until="domcontentloaded")
                return AttachResult(
                    ok=True,
                    browser=self._browser,
                    context=self._context,
                    page=self._page,
                    playwright=self._playwright,
                    created_new_page=True,
                    reason="launched_chromium",
                )
            return AttachResult(ok=False, reason="no_cdp_endpoint")

        _emit(f"Connecting Playwright over CDP: {cdp_url}")
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:  # noqa: BLE001
            _emit(f"CDP connect failed: {exc}", "error")
            # One recovery attempt: force Octo restart with debug_port
            if settings.octo_enabled and settings.octo_profile_uuid:
                _emit("Retrying with Octo force_restart=true", "error")
                start = await octo.start_profile_with_debug(force_restart=True, emit=emit)
                cdp_url = octo.extract_ws_endpoint(start) or ""
                if not cdp_url:
                    return AttachResult(ok=False, reason=f"cdp_connect_failed:{exc}", playwright=self._playwright)
                self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            else:
                return AttachResult(ok=False, reason=f"cdp_connect_failed:{exc}", playwright=self._playwright)

        contexts = self._browser.contexts
        _emit(f"CDP connected; contexts={len(contexts)}")
        if not contexts:
            return AttachResult(
                ok=False,
                reason="no_browser_contexts",
                playwright=self._playwright,
                browser=self._browser,
            )
        self._context = contexts[0]
        pages = list(self._context.pages)
        _emit(f"Open pages ({len(pages)}): {[getattr(p, 'url', '?') for p in pages]}")

        page = None
        if ui_opened_tab:
            _emit(f"Waiting up to {wait_seconds}s for UI-opened tab matching {target_url}")
            page = await self._wait_for_matching_page(target_url, wait_seconds, emit=_emit)

        if page is None and target_url:
            # UI often runs in normal Chrome while apply must use Octo cookies.
            _emit(
                "UI tab not found in Octo (window.open likely opened outside Octo). "
                "Opening apply URL inside Octo profile instead.",
                "error",
            )
            # Prefer reuse an existing blank/new tab; else create one inside Octo.
            blank = None
            for p in self._context.pages:
                try:
                    u = p.url or ""
                except Exception:  # noqa: BLE001
                    continue
                if u in {"about:blank", "chrome://newtab/", "chrome://new-tab-page/", ""}:
                    blank = p
                    break
            if blank is not None:
                page = blank
                await page.goto(target_url, wait_until="domcontentloaded")
                created = False
            else:
                page = await self._context.new_page()
                await page.goto(target_url, wait_until="domcontentloaded")
                created = True
            self._page = page
            _emit(f"Navigated Octo tab to {page.url} created_new_page={created}")
            return AttachResult(
                ok=True,
                browser=self._browser,
                context=self._context,
                page=page,
                playwright=self._playwright,
                created_new_page=created,
                reason="opened_in_octo",
            )

        if page is None:
            return AttachResult(
                ok=False,
                reason="ui_tab_not_found",
                browser=self._browser,
                context=self._context,
                playwright=self._playwright,
                created_new_page=False,
            )

        self._page = page
        _emit(f"Attached to existing tab: {page.url}")
        return AttachResult(
            ok=True,
            browser=self._browser,
            context=self._context,
            page=page,
            playwright=self._playwright,
            created_new_page=False,
            reason="attached_existing",
        )

    async def _wait_for_matching_page(self, target_url: str | None, wait_seconds: float, emit=None):
        deadline = asyncio.get_event_loop().time() + wait_seconds
        needle = _url_needle(target_url)
        last_urls: list[str] = []
        while asyncio.get_event_loop().time() < deadline:
            assert self._context is not None
            urls = []
            for p in self._context.pages:
                try:
                    u = p.url or ""
                except Exception:  # noqa: BLE001
                    continue
                urls.append(u)
                if u in {"about:blank", "chrome://newtab/", "chrome://new-tab-page/", ""}:
                    continue
                if not needle or needle in u or _host_in(target_url, u):
                    return p
            if urls != last_urls and emit:
                emit(f"Still waiting; Octo pages={urls}", "info")
                last_urls = urls
            await asyncio.sleep(0.5)
        return None

    async def disconnect(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:  # noqa: BLE001
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None


def _url_needle(url: str | None) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url)
        return (p.path or "")[-60:] or url[-60:]
    except Exception:  # noqa: BLE001
        return url[-60:]


def _host_in(target: str | None, page_url: str) -> bool:
    if not target:
        return False
    try:
        a = urlparse(target).netloc.lower()
        b = urlparse(page_url).netloc.lower()
        return bool(a) and (a == b or a in b or b in a)
    except Exception:  # noqa: BLE001
        return False
