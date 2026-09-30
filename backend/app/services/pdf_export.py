from __future__ import annotations

import re
from pathlib import Path


def markdown_to_html(md: str, title: str = "Document") -> str:
    lines = (md or "").splitlines()
    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{_esc(title)}</title>",
        "<style>",
        "body{font-family:Georgia,serif;font-size:11pt;line-height:1.35;margin:28px;color:#111}",
        "h1{font-size:18pt;margin:0 0 6px} h2{font-size:12pt;margin:16px 0 6px;",
        "border-bottom:1px solid #333;padding-bottom:2px;text-transform:uppercase}",
        "h3{font-size:11pt;margin:10px 0 2px} p{margin:4px 0} ul{margin:4px 0 8px 18px}",
        "li{margin:2px 0} .meta{color:#333;margin-bottom:10px}",
        "</style></head><body>",
    ]
    in_list = False
    for line in lines:
        s = line.rstrip()
        if not s.strip():
            if in_list:
                parts.append("</ul>")
                in_list = False
            continue
        if s.startswith("# "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<h1>{_inline(s[2:])}</h1>")
        elif s.startswith("## "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<h2>{_inline(s[3:])}</h2>")
        elif s.startswith("### "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<h3>{_inline(s[4:])}</h3>")
        elif s.lstrip().startswith(("- ", "* ")):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            item = s.lstrip()[2:]
            parts.append(f"<li>{_inline(item)}</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<p>{_inline(s)}</p>")
    if in_list:
        parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _inline(s: str) -> str:
    s = _esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    # Restore HTML bold if the model wrote <strong>/<b> (escaped by _esc).
    s = re.sub(r"&lt;(/?)(strong|b)&gt;", r"<\1\2>", s, flags=re.I)
    return s


def html_to_pdf_xhtml2pdf(html: str, out_path: Path) -> bool:
    try:
        from xhtml2pdf import pisa
    except ImportError:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as fh:
        status = pisa.CreatePDF(html, dest=fh)
    return not status.err


async def html_to_pdf_playwright(html: str, out_path: Path) -> bool:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html, wait_until="load")
        await page.pdf(
            path=str(out_path),
            format="Letter",
            margin={"top": "0.6in", "bottom": "0.6in", "left": "0.6in", "right": "0.6in"},
            # Persona stylesheets colour headings, rules and bullet markers.
            # Without this Chromium drops every background and prints the page
            # as if it had none.
            print_background=True,
        )
        await browser.close()
    return out_path.exists()


async def export_pdf(html: str, out_path: Path) -> Path:
    """Render to PDF, preferring the engine that understands the stylesheet.

    Playwright goes first. It is real Chromium, so the PDF matches what the
    .html next to it shows in a browser.

    xhtml2pdf is the fallback rather than the default because it is a ReportLab
    renderer, not a browser: it honours colour and borders but silently ignores
    flexbox and text-transform. A persona sheet that lays the company against
    its dates, or the job title against its location, collapses into one run-on
    line under it — "Data Engineer IIIRemote" — and uppercase headings come out
    in title case. The HTML looked right while the PDF, which is the file that
    actually gets submitted, did not.

    It is kept because it needs no browser binary: on a machine where Chromium
    was never installed, a slightly wrong PDF beats no PDF at all.
    """
    if await html_to_pdf_playwright(html, out_path):
        return out_path
    if html_to_pdf_xhtml2pdf(html, out_path):
        return out_path
    raise RuntimeError("PDF export failed (Playwright and xhtml2pdf)")
