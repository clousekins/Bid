from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class FetchResult:
    ok: bool
    text: str = ""
    reason: str = ""


def _clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    # Greenhouse content blocks
    content = (
        soup.select_one("#content")
        or soup.select_one(".content")
        or soup.select_one("[data-qa='job-description']")
        or soup.select_one(".job__description")
        or soup.body
    )
    text = content.get_text("\n", strip=True) if content else soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def fetch_jd_http(url: str, timeout: float = 30.0) -> FetchResult:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return FetchResult(ok=False, reason=f"http_{resp.status_code}")
            text = _clean_text(resp.text)
            if len(text) < 200:
                return FetchResult(ok=False, text=text, reason="too_short")
            # Login walls
            lower = text.lower()
            if "sign in" in lower and "greenhouse" in lower and len(text) < 800:
                return FetchResult(ok=False, text=text, reason="login_wall")
            return FetchResult(ok=True, text=text)
    except Exception as exc:  # noqa: BLE001
        return FetchResult(ok=False, reason=f"http_error:{exc}")
