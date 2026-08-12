from __future__ import annotations

from playwright.async_api import Page

from app.services.human_input import detect_ats_family, human_click, human_pause


async def promote_application(page: Page, *, emit=None) -> str:
    """Reach the application form across common ATS families. Returns detected family."""
    family = detect_ats_family(page.url or "")
    try:
        html = (await page.content())[:8000]
        family = detect_ats_family(page.url or "", html)
    except Exception:  # noqa: BLE001
        pass
    if emit:
        emit(f"ATS family hint: {family}")

    if family == "greenhouse":
        await _click_first(
            page,
            [
                "a:has-text('Apply')",
                "button:has-text('Apply')",
                "a:has-text('Apply for this job')",
                "#apply_button",
                "a[href*='job_app']",
                "a[href*='greenhouse']",
            ],
            emit=emit,
        )
        await _try_iframe_apply(page, "iframe[src*='greenhouse']", emit=emit)
    elif family == "lever":
        await _click_first(
            page,
            [
                "a:has-text('Apply for this job')",
                "a.postings-btn",
                "button:has-text('Apply')",
                "a:has-text('Apply')",
            ],
            emit=emit,
        )
    elif family == "ashby":
        await _click_first(
            page,
            [
                "button:has-text('Apply')",
                "a:has-text('Apply')",
                "[data-testid*='apply']",
            ],
            emit=emit,
        )
    elif family == "workday":
        await _click_first(
            page,
            [
                "a:has-text('Apply')",
                "button:has-text('Apply')",
                "button:has-text('Apply Now')",
                "[data-automation-id*='apply']",
            ],
            emit=emit,
        )
    else:
        await _click_first(
            page,
            [
                "a:has-text('Apply for this job')",
                "button:has-text('Apply Now')",
                "a:has-text('Apply Now')",
                "button:has-text('Apply')",
                "a:has-text('Apply')",
                "#apply_button",
            ],
            emit=emit,
        )
        await _try_iframe_apply(page, "iframe[src*='greenhouse'], iframe[src*='lever'], iframe[src*='ashby']", emit=emit)

    await human_pause(0.4, 1.0)
    return family


async def promote_greenhouse_application(page: Page) -> None:
    """Back-compat wrapper."""
    await promote_application(page)


async def _click_first(page: Page, selectors: list[str], *, emit=None) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            if await human_click(page, loc, timeout=3500):
                if emit:
                    emit(f"Clicked apply CTA: {sel}")
                await human_pause(0.5, 1.1)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def _try_iframe_apply(page: Page, iframe_sel: str, *, emit=None) -> None:
    try:
        frame = page.frame_locator(iframe_sel).first
        apply_btn = frame.locator(
            "a:has-text('Apply'), button:has-text('Apply'), a:has-text('Apply for this job')"
        ).first
        if await apply_btn.count():
            await apply_btn.click(timeout=3000)
            if emit:
                emit("Clicked Apply inside iframe")
            await human_pause(0.6, 1.2)
    except Exception:  # noqa: BLE001
        pass


async def detect_hard_block(page: Page) -> str | None:
    try:
        text = (await page.inner_text("body")).lower()
    except Exception:  # noqa: BLE001
        return None
    if "error 1015" in text or "you are being rate limited" in text:
        return "hard_waf_1015"
    if "access denied" in text and "cloudflare" in text:
        return "hard_cloudflare"
    return None
