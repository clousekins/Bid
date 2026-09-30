"""The accent colour a persona's resume is printed in.

A persona stylesheet is written around one accent: the name, the section rules,
the role titles, the bullet markers. Swapping it is the cheapest way to make the
same resume look different for a different employer, so the choice belongs in
the UI rather than in a file edit.

The chosen value is substituted into the stylesheet at generation time. The
stylesheet on disk keeps its own accent as the literal default, which means a
persona with no choice recorded, or with the picker never touched, generates
exactly as it did before this existed.

Substitution rather than a CSS variable on purpose: var() is fine in Chromium,
but the PDF fallback (xhtml2pdf, used when no browser binary exists) does not
resolve custom properties and would print the accent as black.
"""
from __future__ import annotations

import re

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")

# Offered in the picker, in this order.
PALETTE: tuple[dict[str, str], ...] = (
    {"name": "Teal", "value": "#1D6E93"},
    {"name": "Rust", "value": "#8B2E13"},
    {"name": "Indigo", "value": "#2C3181"},
    {"name": "Plum", "value": "#5F3A4D"},
    {"name": "Forest", "value": "#4F6151"},
    # Near-black with a blue cast. Sits a shade off the #1A1A1A body text, so
    # it reads as a mono resume rather than an accented one.
    {"name": "Ink", "value": "#11171E"},
    # Muted blue-grey. Low saturation, so it reads as a restrained accent
    # rather than a colour, while still sitting clear of the body text.
    {"name": "Slate", "value": "#4A5459"},
)

DEFAULT = PALETTE[0]["value"]


def is_valid(value: str | None) -> bool:
    return bool(value) and bool(HEX.match(str(value).strip()))


def normalise(value: str | None) -> str | None:
    """An acceptable accent, or None for 'use the stylesheet's own'."""
    text = str(value or "").strip()
    if not text:
        return None
    if not HEX.match(text):
        raise ValueError(f"{value!r} is not a #rrggbb colour")
    return text.upper()


def apply_to_css(css: str, base: str | None, chosen: str | None) -> str:
    """Repaint a stylesheet's accent.

    `base` is the accent the file is written in, declared as tailor.accent_color
    in the persona's yml. Without it nothing is substituted — there would be no
    way to tell an accent apart from the body colour, and a blind replace would
    repaint the text as well.
    """
    if not css or not base or not chosen:
        return css
    if not is_valid(base) or not is_valid(chosen):
        return css
    if base.upper() == chosen.upper():
        return css
    return re.sub(re.escape(base), chosen, css, flags=re.IGNORECASE)
