from __future__ import annotations

import asyncio
import random
import sys
from collections.abc import Callable

from playwright.async_api import Locator, Page

LogFn = Callable[[str, str], None]

_EMIT: LogFn | None = None
_LAST_XY: tuple[float, float] | None = None
_OS_MOUSE = sys.platform == "win32"  # move real Windows cursor during apply


def set_input_logger(emit: LogFn | None) -> None:
    global _EMIT
    _EMIT = emit


def set_os_mouse(enabled: bool) -> None:
    global _OS_MOUSE
    _OS_MOUSE = bool(enabled) and sys.platform == "win32"


def _log(msg: str, level: str = "info") -> None:
    if _EMIT:
        try:
            _EMIT(msg, level)
        except Exception:  # noqa: BLE001
            pass


def _page_closed(page: Page) -> bool:
    try:
        return page.is_closed()
    except Exception:  # noqa: BLE001
        return True


async def human_pause(lo: float = 0.08, hi: float = 0.28) -> None:
    await asyncio.sleep(random.uniform(lo, hi))


def _os_set_cursor(screen_x: float, screen_y: float) -> None:
    if not _OS_MOUSE:
        return
    try:
        import ctypes

        ctypes.windll.user32.SetCursorPos(int(round(screen_x)), int(round(screen_y)))
    except Exception:  # noqa: BLE001
        pass


async def _viewport_to_screen(page: Page, x: float, y: float) -> tuple[float, float] | None:
    """Map page viewport coords → physical screen coords for OS cursor."""
    try:
        info = await page.evaluate(
            """([vx, vy]) => {
              const dpr = window.devicePixelRatio || 1;
              const chromeX = Math.max(0, (window.outerWidth - window.innerWidth) / 2);
              const chromeY = Math.max(0, window.outerHeight - window.innerHeight);
              return {
                sx: (window.screenX + chromeX + vx) * dpr,
                sy: (window.screenY + chromeY + vy) * dpr,
              };
            }""",
            [x, y],
        )
        if isinstance(info, dict) and "sx" in info:
            return float(info["sx"]), float(info["sy"])
    except Exception:  # noqa: BLE001
        pass
    return None


async def _move_point(page: Page, x: float, y: float, *, steps: int = 1) -> None:
    global _LAST_XY
    if _page_closed(page):
        return
    await page.mouse.move(x, y, steps=max(1, steps))
    screen = await _viewport_to_screen(page, x, y)
    if screen:
        _os_set_cursor(screen[0], screen[1])
    _LAST_XY = (x, y)


async def human_scroll_into_view(page: Page, locator: Locator) -> None:
    """Scroll with mouse wheel in human-sized chunks (not instant jump)."""
    if _page_closed(page):
        return
    vp = page.viewport_size or {"width": 1200, "height": 800}
    # Park cursor mid-viewport so wheel feels natural
    try:
        await _move_point(
            page,
            vp["width"] * random.uniform(0.35, 0.65),
            vp["height"] * random.uniform(0.4, 0.6),
            steps=random.randint(4, 10),
        )
    except Exception:  # noqa: BLE001
        pass

    for _ in range(18):
        if _page_closed(page):
            return
        try:
            box = await locator.bounding_box()
        except Exception:  # noqa: BLE001
            return
        if not box:
            try:
                await locator.scroll_into_view_if_needed(timeout=1500)
            except Exception:  # noqa: BLE001
                pass
            return

        mid_y = box["y"] + box["height"] / 2
        top_ok = vp["height"] * 0.18
        bot_ok = vp["height"] * 0.78
        if top_ok <= mid_y <= bot_ok and box["height"] > 0:
            await human_pause(0.05, 0.14)
            return

        # Distance to bring mid into center band
        target = vp["height"] * random.uniform(0.38, 0.55)
        delta = mid_y - target
        # Wheel: positive Y scrolls content up (element moves up) in Chromium
        chunks = random.randint(2, 5)
        per = delta / chunks
        for _c in range(chunks):
            if _page_closed(page):
                return
            wobble = random.uniform(0.75, 1.25)
            await page.mouse.wheel(random.uniform(-4, 4), per * wobble)
            await asyncio.sleep(random.uniform(0.05, 0.14))
        await human_pause(0.06, 0.16)

    # Fallback if still off-screen
    try:
        await locator.scroll_into_view_if_needed(timeout=1500)
        await human_pause(0.08, 0.18)
    except Exception:  # noqa: BLE001
        pass


async def human_mouse_move(page: Page, locator: Locator | None = None) -> None:
    """Curved, human-speed CDP mouse move (+ OS cursor on Windows)."""
    global _LAST_XY
    if _page_closed(page):
        _log("mouse_move skipped — page closed", "error")
        return
    try:
        vp = page.viewport_size or {"width": 1200, "height": 800}
        end_x = vp["width"] * random.uniform(0.3, 0.7)
        end_y = vp["height"] * random.uniform(0.3, 0.7)

        if locator is not None:
            await human_scroll_into_view(page, locator)
            if await locator.count():
                box = await locator.bounding_box()
                if box and box["width"] > 0 and box["height"] > 0:
                    end_x = box["x"] + box["width"] * random.uniform(0.28, 0.72)
                    end_y = box["y"] + box["height"] * random.uniform(0.32, 0.68)

        if _LAST_XY:
            start_x, start_y = _LAST_XY
            # Nudge start if identical
            if abs(start_x - end_x) < 2 and abs(start_y - end_y) < 2:
                start_x += random.uniform(-40, 40)
                start_y += random.uniform(-30, 30)
        else:
            start_x = max(2, min(vp["width"] - 2, end_x + random.uniform(-180, 180)))
            start_y = max(2, min(vp["height"] - 2, end_y + random.uniform(-120, 120)))

        start_x = max(2, min(vp["width"] - 2, start_x))
        start_y = max(2, min(vp["height"] - 2, start_y))

        dist = ((end_x - start_x) ** 2 + (end_y - start_y) ** 2) ** 0.5
        # Human: ~400–900 px/s with variance → duration ~0.25–1.1s
        speed = random.uniform(380, 900)
        duration = max(0.22, min(1.25, dist / speed + random.uniform(0.05, 0.2)))
        steps = max(18, min(55, int(duration / random.uniform(0.014, 0.022))))

        cx1 = start_x + (end_x - start_x) * random.uniform(0.15, 0.4) + random.uniform(-45, 45)
        cy1 = start_y + (end_y - start_y) * random.uniform(0.05, 0.35) + random.uniform(-50, 50)
        cx2 = start_x + (end_x - start_x) * random.uniform(0.55, 0.9) + random.uniform(-45, 45)
        cy2 = start_y + (end_y - start_y) * random.uniform(0.5, 0.95) + random.uniform(-45, 45)

        _log(f"mouse_move -> ({end_x:.0f},{end_y:.0f}) steps={steps} ~{duration*1000:.0f}ms")
        for i in range(1, steps + 1):
            if _page_closed(page):
                return
            t = i / steps
            # Ease-in-out + slight overshoot mid-path
            te = t * t * (3 - 2 * t)
            u = 1 - te
            x = u**3 * start_x + 3 * u * u * te * cx1 + 3 * u * te * te * cx2 + te**3 * end_x
            y = u**3 * start_y + 3 * u * u * te * cy1 + 3 * u * te * te * cy2 + te**3 * end_y
            # Micro-jitter
            x += random.uniform(-0.6, 0.6)
            y += random.uniform(-0.6, 0.6)
            await _move_point(page, x, y, steps=1)
            # Variable inter-step delay (slower near ends)
            edge = 1.0 + 0.7 * (1 - abs(2 * t - 1))
            await asyncio.sleep((duration / steps) * edge * random.uniform(0.75, 1.3))

        await _move_point(page, end_x, end_y, steps=2)
        await human_pause(0.06, 0.16)
    except Exception as exc:  # noqa: BLE001
        if "closed" in str(exc).lower():
            _log("mouse_move aborted — page/browser closed", "error")
        else:
            _log(f"mouse_move error: {exc}", "error")


async def human_click(page: Page, locator: Locator, *, timeout: float = 3000) -> bool:
    """Scroll → human mouse move → press/release click."""
    if _page_closed(page):
        _log("human_click skipped — page closed", "error")
        return False
    try:
        await human_scroll_into_view(page, locator)
        await locator.wait_for(state="visible", timeout=timeout)
        await human_mouse_move(page, locator)
        if _page_closed(page):
            return False
        box = await locator.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            await locator.click(timeout=timeout)
            await human_pause(0.1, 0.25)
            return True
        x = box["x"] + box["width"] * random.uniform(0.32, 0.68)
        y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        await _move_point(page, x, y, steps=random.randint(2, 5))
        try:
            await locator.hover(timeout=1200, force=True)
        except Exception:  # noqa: BLE001
            pass
        await page.mouse.down()
        await human_pause(0.05, 0.14)
        await page.mouse.up()
        await human_pause(0.12, 0.32)
        _log(f"click OK ({x:.0f},{y:.0f})")
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"human_click retry after: {exc}", "error")
        try:
            if _page_closed(page):
                return False
            await locator.click(timeout=timeout, force=True)
            await human_pause(0.1, 0.22)
            return True
        except Exception as exc2:  # noqa: BLE001
            _log(f"human_click FAILED: {exc2}", "error")
            return False


def _word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w])


async def human_type_into(
    page: Page,
    locator: Locator,
    text: str,
    *,
    paste_over_words: int = 200,
) -> bool:
    if _page_closed(page):
        return False
    value = str(text or "")
    try:
        await human_mouse_move(page, locator)
        box = await locator.bounding_box()
        if box:
            await page.mouse.click(
                box["x"] + box["width"] * random.uniform(0.3, 0.7),
                box["y"] + box["height"] * random.uniform(0.35, 0.65),
            )
            screen = await _viewport_to_screen(
                page,
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
            )
            if screen:
                _os_set_cursor(screen[0], screen[1])
        else:
            await locator.click(timeout=2500)
        await human_pause(0.1, 0.25)
        try:
            await locator.fill("", timeout=1500)
        except Exception:  # noqa: BLE001
            await page.keyboard.press("Control+A")
            await human_pause(0.04, 0.1)
            await page.keyboard.press("Backspace")

        if _word_count(value) > paste_over_words or len(value) > 1400:
            await locator.fill(value, timeout=5000)
            return True

        for i, ch in enumerate(value):
            # ~45–110 WPM equivalent with bursts and hesitations
            delay = random.randint(40, 110)
            if ch in ".!?,;:\n":
                delay = random.randint(90, 220)
            elif ch == " ":
                delay = random.randint(50, 140)
            await locator.type(ch, delay=delay)
            if i > 0 and i % random.randint(28, 55) == 0:
                await human_pause(0.15, 0.45)
            elif random.random() < 0.04:
                await human_pause(0.08, 0.22)
        await human_pause(0.08, 0.2)
        return True
    except Exception as exc:  # noqa: BLE001
        _log(f"type failed: {exc}", "error")
        try:
            await locator.fill(value, timeout=3000)
            return True
        except Exception:  # noqa: BLE001
            return False


async def human_type(page: Page, selector: str, text: str) -> None:
    await human_type_into(page, page.locator(selector).first, text)


async def soft_cloudflare_wait(page: Page, timeout_s: float = 8.0) -> bool:
    try:
        content = (await page.content()).lower()
    except Exception:  # noqa: BLE001
        return False
    markers = ("cf-browser-verification", "challenge-platform", "just a moment", "checking your browser")
    if any(m in content for m in markers):
        await asyncio.sleep(min(timeout_s, 5.0))
        try:
            content2 = (await page.content()).lower()
        except Exception:  # noqa: BLE001
            return True
        return any(m in content2 for m in markers)
    return False


def detect_ats_family(url: str, html_sample: str = "") -> str:
    u = (url or "").lower()
    h = (html_sample or "").lower()
    if "greenhouse" in u or "greenhouse.io" in u or "boards.greenhouse" in u or "greenhouse" in h:
        return "greenhouse"
    if "lever.co" in u or "jobs.lever" in u or "lever-application" in h:
        return "lever"
    if "ashbyhq.com" in u or "ashby" in h:
        return "ashby"
    if "myworkdayjobs.com" in u or "workday" in u or "workday" in h:
        return "workday"
    if "icims.com" in u or "icims" in h:
        return "icims"
    if "smartrecruiters.com" in u:
        return "smartrecruiters"
    if "jobvite.com" in u:
        return "jobvite"
    if "applytojob.com" in u or "jazzhr" in u or "resumator" in h or "applytojob" in h:
        return "jazzhr"
    return "generic"
