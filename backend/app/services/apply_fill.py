from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from playwright.async_api import Page

from app.services.apply_answers import (
    dedupe_fields,
    looks_like_dropdown_field,
    normalize_phone_for_label,
    phone_digits,
    pick_option,
    should_skip_field,
)
from app.services.form_scan import collect_open_list_options
from app.services.human_input import (
    human_click,
    human_mouse_move,
    human_pause,
    human_type_into,
    set_input_logger,
    set_os_mouse,
)

LogFn = Callable[[str, str], None]


def _looks_boolean_question(label: str) -> bool:
    lab = (label or "").lower()
    return any(
        w in lab
        for w in (
            "are you",
            "do you",
            "have you",
            "will you",
            "can you",
            "located in",
            "living in",
            "authorized",
            "sponsor",
            "consent",
            "agree",
            "confirm",
            "eligible",
            "over 18",
            "18 years",
        )
    )


async def fill_all_answers(
    page: Page,
    fields: list[dict[str, Any]],
    answers: dict[str, str],
    *,
    emit: LogFn | None = None,
    resume_pdf: Path | None = None,
    cover_pdf: Path | None = None,
    only_labels: set[str] | None = None,
    skip_filled: bool = True,
) -> dict[str, Any]:
    """Fill mapped answers into the live form with human-like interaction."""
    filled = 0
    skipped = 0
    failed: list[str] = []

    def _emit(msg: str, level: str = "info") -> None:
        if emit:
            emit(msg, level)

    set_input_logger(_emit if emit else None)
    set_os_mouse(True)
    fields = dedupe_fields(fields)
    _emit(f"fill_all_answers start fields={len(fields)} only={list(only_labels or [])[:6]}")
    ordered = sorted(fields, key=lambda f: (0 if f.get("required") else 1, f.get("index") or 0))
    only_norm = {re.sub(r"\*+", "", x).strip().lower() for x in (only_labels or set()) if x}

    for f in ordered:
        if page.is_closed():
            _emit("PAGE/BROWSER CLOSED — aborting remaining fills", "error")
            break

        label = (f.get("label") or "").strip()
        kind = (f.get("kind") or "text").lower()
        ftype = (f.get("type") or "").lower()

        if should_skip_field(f):
            continue
        if ftype == "search" or label.lower() == "search":
            continue
        # Prefer click-dropdown over typing for Greenhouse-style selects
        if kind == "text" and looks_like_dropdown_field(f):
            kind = "combobox"
            _emit(f"promote text->combobox for {label[:50]!r}")
        # Choice tags / Yes-No pills scanned as radio with options
        if kind == "text" and (f.get("options") or []) and _looks_boolean_question(label):
            kind = "radio"
            _emit(f"promote text->radio/tags for {label[:50]!r}")
        if only_norm:
            lab_n = re.sub(r"\*+", "", label).strip().lower()
            if lab_n not in only_norm and not any(o in lab_n or lab_n in o for o in only_norm):
                continue

        if kind == "file" or ftype == "file":
            path = _pick_upload_path(label, resume_pdf=resume_pdf, cover_pdf=cover_pdf)
            if not path:
                if f.get("required"):
                    failed.append(label or "file")
                    _emit(f"No file for upload field: {label[:80]}", "error")
                continue

            already = False
            try:
                already = await _file_already_uploaded(page, f) or await _ui_shows_attached_resume(
                    page, label
                )
            except Exception:  # noqa: BLE001
                already = False
            ours = False
            try:
                ours = await _upload_looks_like_package(page, f, path)
            except Exception:  # noqa: BLE001
                ours = False

            # Never keep ATS/profile default CV — only skip if OUR package file is already there
            if skip_filled and already and ours:
                skipped += 1
                _emit(f"Skip upload (package already set): {label[:50]} <- {path.name}")
                continue
            if already and not ours:
                _emit(
                    f"Replace prefilled/default upload on [{label[:40]}] with package {path.name}",
                    "info",
                )
                await _clear_existing_upload(page, f, label, emit=_emit)
            elif not already:
                _emit(f"Upload package file for [{label[:40]}] <- {path.name}")

            ok = await _fill_file(page, f, label, path, emit=_emit)
            if ok:
                filled += 1
                _emit(f"Uploaded (package): {label[:60]} <- {path.name}")
                await human_pause(0.25, 0.6)
            else:
                failed.append(label or "file")
                _emit(f"Could not upload: {label[:80]}", "error")
            continue

        if not label:
            continue
        answer = _lookup_answer(label, answers)
        if answer is None or answer == "":
            continue

        # Phone +1 UI: don't retype country code into the national-number box
        if "phone" in label.lower():
            answer = normalize_phone_for_label(label, str(answer))

        if skip_filled:
            try:
                if await _field_already_ok(page, f, answer):
                    skipped += 1
                    continue
            except Exception:  # noqa: BLE001
                pass

        ok = False
        try:
            if page.is_closed():
                _emit("PAGE/BROWSER CLOSED — aborting remaining fills", "error")
                break
            _emit(f"fill kind={kind}/{ftype} label={label[:60]!r} answer={str(answer)[:40]!r}")
            if kind in {"select"}:
                opts = f.get("options") or []
                mapped = pick_option(str(answer), opts) if opts else str(answer)
                _emit(f"native select -> mapped={mapped!r} opts={opts[:8]}")
                ok = await _fill_native_select(page, f, mapped)
            elif kind in {"combobox"}:
                _emit("combobox path (click open + click option)")
                ok = await _fill_combobox(page, f, label, answer, emit=_emit)
            elif kind in {"checkbox"}:
                _emit("checkbox path (mouse click)")
                ok = await _fill_checkbox(page, f, answer)
            elif kind in {"radio"}:
                _emit("radio/tag path (mouse click choice)")
                ok = await _fill_radio(page, f, answer)
                if not ok and (f.get("options") or []):
                    ok = await _fill_choice_tags(page, f, answer, emit=_emit)
            else:
                ans_l = str(answer).strip().lower()
                if ans_l in {"yes", "no", "y", "n", "true", "false"} and _looks_boolean_question(
                    label
                ):
                    _emit("boolean text -> try Yes/No / tag click first")
                    ok = await _fill_radio(page, {**f, "label": label}, answer)
                    if not ok:
                        ok = await _fill_choice_tags(page, f, answer, emit=_emit)
                if not ok and (f.get("options") or []):
                    # Options on a non-select field -> try tag click, then native select
                    _emit("text field has options -> try tag/radio click")
                    ok = await _fill_choice_tags(page, f, answer, emit=_emit)
                    if not ok:
                        ok = await _fill_native_select(page, f, answer)
                if not ok:
                    _emit("text path (mouse + type)")
                    ok = await _fill_text(page, f, label, answer)
        except Exception as exc:  # noqa: BLE001
            _emit(f"Fill error [{label}]: {exc}", "error")
            ok = False
            if page.is_closed() or "closed" in str(exc).lower():
                _emit("PAGE/BROWSER CLOSED — aborting remaining fills", "error")
                break

        if ok:
            filled += 1
            _emit(f"Filled: {label[:80]} -> {str(answer)[:60]}")
            await human_pause(0.25, 0.7)
        else:
            failed.append(label)
            _emit(f"Could not fill: {label[:80]}", "error")

    return {"filled": filled, "failed": failed, "skipped": skipped}


def _css_attr_eq(attr: str, value: str) -> str:
    """Safe CSS attribute selector (handles ids that start with digits / special chars)."""
    v = (value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'[{attr}="{v}"]'


def _safe_locator(page: Page, selector: str):
    """Build locator; return None if selector is invalid CSS."""
    if not selector:
        return None
    try:
        loc = page.locator(selector).first
        # Force parse now — invalid CSS raises on count()
        if loc is None:
            return None
        return loc
    except Exception:  # noqa: BLE001
        return None


async def _safe_count(loc) -> int:
    if loc is None:
        return 0
    try:
        return await loc.count()
    except Exception:  # noqa: BLE001
        return 0


async def _resolve_control(page: Page, field: dict[str, Any]):
    hint = field.get("selector_hint")
    if hint:
        loc = _safe_locator(page, hint)
        if loc and await _safe_count(loc):
            return loc
    if field.get("id"):
        loc = _safe_locator(page, _css_attr_eq("id", field["id"]))
        if loc and await _safe_count(loc):
            return loc
        # Never use raw #id — UUIDs / leading digits break CSS
    if field.get("name"):
        kind = (field.get("kind") or "").lower()
        tag = "select" if kind == "select" else "*"
        loc = _safe_locator(page, f'{tag}{_css_attr_eq("name", field["name"])}')
        if loc and await _safe_count(loc):
            return loc
        loc = _safe_locator(page, _css_attr_eq("name", field["name"]))
        if loc and await _safe_count(loc):
            return loc
    return None


def _is_resume_upload_field(label: str) -> bool:
    lab = (label or "").lower()
    if any(k in lab for k in ("cover", "letter", "motivation")):
        return False
    return any(
        k in lab
        for k in (
            "resume",
            "cv",
            "curriculum",
            "attach",
            "upload",
            "document",
            "file",
        )
    ) or not lab


def _is_cover_upload_field(label: str) -> bool:
    lab = (label or "").lower()
    return any(k in lab for k in ("cover", "letter", "motivation"))


async def _clear_preattached_resume(page: Page, label: str, emit: LogFn | None = None) -> None:
    """Remove ATS-preloaded default resume/CV so our package file can replace it."""
    short = re.sub(r"\*+", "", (label or "")).strip()[:40].replace('"', '\\"')
    # Common Greenhouse / Lever / Ashby / Workday remove/replace controls
    selectors = [
        f'.application--question:has-text("{short}") >> button:has-text("Remove")',
        f'.application--question:has-text("{short}") >> a:has-text("Remove")',
        f'.application--question:has-text("{short}") >> button:has-text("Delete")',
        f'.application--question:has-text("{short}") >> button:has-text("Replace")',
        f'.application--question:has-text("{short}") >> button:has-text("Change")',
        f'[class*="field"]:has-text("{short}") >> button:has-text("Remove")',
        f'[class*="field"]:has-text("{short}") >> button:has-text("Replace")',
        f'[class*="field"]:has-text("{short}") >> button:has-text("Change")',
        'button:has-text("Remove")',
        'button:has-text("Delete file")',
        'button:has-text("Remove file")',
        'a:has-text("Remove")',
        '[aria-label*="Remove" i]',
        '[aria-label*="Delete" i]',
        '[data-testid*="remove" i]',
        '[class*="remove"]:has-text("Remove")',
    ]
    clicked = 0
    for sel in selectors:
        if clicked >= 2:
            break
        try:
            locs = page.locator(sel)
            n = min(await locs.count(), 3)
            for i in range(n):
                loc = locs.nth(i)
                try:
                    if not await loc.is_visible():
                        continue
                except Exception:  # noqa: BLE001
                    continue
                txt = ""
                try:
                    txt = ((await loc.inner_text()) or "")[:40]
                except Exception:  # noqa: BLE001
                    pass
                # Avoid random "Remove" elsewhere (filters, education rows)
                blob = f"{txt} {sel}".lower()
                if short and short.lower()[:12] not in sel.lower():
                    if not any(
                        k in blob
                        for k in ("resume", "cv", "file", "attach", "upload", "document", "pdf")
                    ):
                        # Generic Remove near resume question only when scoped
                        if "application--question" not in sel and "field" not in sel:
                            continue
                if emit:
                    emit(f"Clearing pre-attached file via {sel!r} ({txt!r})", "info")
                if await human_click(page, loc, timeout=2000):
                    clicked += 1
                    await human_pause(0.35, 0.7)
                    break
        except Exception:  # noqa: BLE001
            continue


async def _upload_looks_like_package(page: Page, field: dict[str, Any], path: Path) -> bool:
    """True only if the attached file name matches our package PDF (not a profile default)."""
    want = (path.name or "").strip().lower()
    if not want:
        return False
    loc = await _resolve_control(page, field)
    try:
        if loc and await _safe_count(loc):
            name = await loc.evaluate(
                """el => (el.files && el.files[0] && el.files[0].name) ? el.files[0].name : ''"""
            )
            if name and name.strip().lower() == want:
                return True
            if name and want in name.strip().lower():
                return True
    except Exception:  # noqa: BLE001
        pass
    # UI preview text (Greenhouse etc.)
    try:
        stem = Path(want).stem.lower()[:40]
        return bool(
            await page.evaluate(
                """([want, stem]) => {
                  const t = (document.body && document.body.innerText || '').toLowerCase();
                  return (want && t.includes(want)) || (stem && t.includes(stem) && t.includes('.pdf'));
                }""",
                [want, stem],
            )
        )
    except Exception:  # noqa: BLE001
        return False


async def _clear_existing_upload(
    page: Page,
    field: dict[str, Any],
    label: str,
    *,
    emit: LogFn | None = None,
) -> None:
    """Clear input.files and click Remove/Replace on pre-attached ATS resumes."""
    loc = await _resolve_control(page, field)
    if loc and await _safe_count(loc):
        try:
            await loc.evaluate(
                """el => {
                  try {
                    el.value = '';
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                  } catch (e) {}
                }"""
            )
        except Exception:  # noqa: BLE001
            pass
    await _clear_preattached_resume(page, label, emit=emit)


async def _file_already_uploaded(page: Page, field: dict[str, Any]) -> bool:
    loc = await _resolve_control(page, field)
    if not loc:
        # Fallback any visible file input with files
        try:
            n = await page.locator('input[type="file"]').evaluate_all(
                "els => els.some(e => e.files && e.files.length > 0)"
            )
            return bool(n)
        except Exception:  # noqa: BLE001
            return False
    try:
        return bool(
            await loc.evaluate("el => !!(el.files && el.files.length)")
        )
    except Exception:  # noqa: BLE001
        return False


async def _ui_shows_attached_resume(page: Page, label: str) -> bool:
    """Detect Greenhouse-style 'filename.pdf' preview when input.files is empty."""
    try:
        return bool(
            await page.evaluate(
                """(label) => {
                  const lab = (label || '').toLowerCase();
                  const blocks = Array.from(document.querySelectorAll(
                    '.application--question, [class*="field"], [class*="Field"], fieldset, form'
                  ));
                  const hit = blocks.filter(b => {
                    const t = (b.innerText || '').toLowerCase();
                    if (lab && lab.length > 2 && !t.includes(lab.slice(0, Math.min(lab.length, 24)))) {
                      // still allow global resume markers
                    }
                    return /\\.(pdf|docx?)\\b/i.test(t) || /resume (uploaded|attached|on file)/i.test(t)
                      || /uploaded|attached/.test(t) && /(resume|cv|curriculum)/i.test(t);
                  });
                  return hit.length > 0;
                }""",
                re.sub(r"\*+", "", (label or "")).strip()[:40],
            )
        )
    except Exception:  # noqa: BLE001
        return False


async def _field_already_ok(page: Page, field: dict[str, Any], answer: str) -> bool:
    loc = await _resolve_control(page, field)
    if not loc:
        return False
    try:
        kind = (field.get("kind") or "").lower()
        if kind == "select":
            selected = await loc.evaluate(
                """el => {
                  const o = el.options && el.options[el.selectedIndex];
                  return (o && o.text) ? o.text.trim() : (el.value || '');
                }"""
            )
            if not selected or selected.lower() in {"select...", "select", "", "—", "-"}:
                return False
            a = answer.lower().strip()
            s = selected.lower().strip()
            return a == s or a in s or s in a
        val = await loc.input_value(timeout=800)
        if not val:
            return False
        label = (field.get("label") or "").lower()
        if "phone" in label or (field.get("type") or "").lower() == "tel":
            vd = phone_digits(val)
            ad = phone_digits(answer)
            if not vd or not ad:
                return False
            # Match national 10 digits either way (+1 prefix optional)
            if len(vd) >= 10 and len(ad) >= 10:
                return vd[-10:] == ad[-10:]
            return vd == ad or vd in ad or ad in vd
        # Avoid "+1" prefix widgets matching full phone via substring
        if len(val.strip()) <= 3:
            return val.strip().lower() == str(answer).strip().lower()
        return val.strip().lower() == str(answer).strip().lower() or str(answer).strip().lower() in val.strip().lower()
    except Exception:  # noqa: BLE001
        return False


def _pick_upload_path(
    label: str,
    *,
    resume_pdf: Path | None,
    cover_pdf: Path | None,
) -> Path | None:
    lab = (label or "").lower()
    if any(k in lab for k in ("cover", "letter", "motivation")):
        if cover_pdf and cover_pdf.exists():
            return cover_pdf
        return resume_pdf if resume_pdf and resume_pdf.exists() else None
    # Default: resume / CV / attachment
    if resume_pdf and resume_pdf.exists():
        return resume_pdf
    if cover_pdf and cover_pdf.exists():
        return cover_pdf
    return None


def find_package_documents(folder: Path | None) -> tuple[Path | None, Path | None]:
    """Return (resume_pdf, cover_pdf) from a package folder."""
    if not folder or not folder.exists():
        return None, None
    resume = None
    cover = None
    for p in sorted(folder.glob("*.pdf")):
        name = p.name.lower()
        if "cover" in name:
            cover = cover or p
        else:
            resume = resume or p
    return resume, cover


def _lookup_answer(label: str, answers: dict[str, str]) -> str | None:
    if label in answers:
        return answers[label]
    lab = label.lower().strip()
    for k, v in answers.items():
        if k.lower().strip() == lab:
            return v
    for k, v in answers.items():
        if k.lower() in lab or lab in k.lower():
            return v
    return None


async def _resolve_text_locator(page: Page, field: dict, label: str):
    # Prefer name/id attribute selectors (Ashby UUIDs start with digits)
    if field.get("id"):
        loc = _safe_locator(page, _css_attr_eq("id", field["id"]))
        if loc and await _safe_count(loc):
            return loc
    if field.get("name"):
        loc = _safe_locator(
            page,
            f'input{_css_attr_eq("name", field["name"])}, textarea{_css_attr_eq("name", field["name"])}',
        )
        if loc and await _safe_count(loc):
            return loc
    hint = field.get("selector_hint")
    if hint:
        loc = _safe_locator(page, hint)
        if loc and await _safe_count(loc):
            return loc

    short = re.sub(r"\*+", "", label).strip()[:40]
    # Escape quotes in label for :has-text / attribute selectors
    short_css = short.replace("\\", "\\\\").replace('"', '\\"')
    ftype = (field.get("type") or "").lower()
    candidates = []
    if ftype == "tel" or "phone" in short.lower():
        candidates.extend(
            [
                'input[type="tel"]',
                f'input[type="tel"][placeholder*="phone" i]',
                f'input[placeholder*="415" i]',
            ]
        )
    candidates.extend(
        [
            f'input[aria-label*="{short_css}" i]',
            f'textarea[aria-label*="{short_css}" i]',
            f'label:has-text("{short_css}") >> input',
            f'label:has-text("{short_css}") >> textarea',
            f'[class*="field"]:has-text("{short_css}") >> input:not([type=hidden]):not([type=file])',
            f'[class*="Field"]:has-text("{short_css}") >> input:not([type=hidden]):not([type=file])',
            f'.ashby-application-form-field-entry:has-text("{short_css}") >> input',
            f'.application--question:has-text("{short_css}") >> input:not([type=hidden]):not([type=file]):not([type=search])',
            f'.application--question:has-text("{short_css}") >> textarea',
        ]
    )
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if await _safe_count(loc):
                return loc
        except Exception:  # noqa: BLE001
            continue
    return None


async def _fill_text(page: Page, field: dict, label: str, value: str) -> bool:
    loc = await _resolve_text_locator(page, field, label)
    if not loc:
        return False
    ok = await human_type_into(page, loc, str(value))
    if ok:
        return True
    # Phone fallback: digits only (some masks reject punctuation / +1)
    if "phone" in (label or "").lower() or (field.get("type") or "").lower() == "tel":
        digits = phone_digits(value)
        if digits.startswith("1") and len(digits) == 11:
            digits = digits[1:]
        if digits and digits != value:
            return await human_type_into(page, loc, digits)
    return False


async def _fill_file(
    page: Page,
    field: dict,
    label: str,
    path: Path,
    *,
    emit: LogFn | None = None,
) -> bool:
    loc = None
    if field.get("id"):
        loc = _safe_locator(page, _css_attr_eq("id", field["id"]))
    elif field.get("name"):
        loc = _safe_locator(
            page, f'input[type=file]{_css_attr_eq("name", field["name"])}'
        )
    if not loc or await _safe_count(loc) == 0:
        short = re.sub(r"\*+", "", label).strip()[:40].replace('"', '\\"')
        for sel in (
            f'label:has-text("{short}") >> input[type=file]',
            f'.application--question:has-text("{short}") >> input[type=file]',
            "input[type=file]",
        ):
            try:
                cand = page.locator(sel).first
                if await _safe_count(cand):
                    loc = cand
                    break
            except Exception:  # noqa: BLE001
                continue
    if not loc or await _safe_count(loc) == 0:
        return False
    try:
        await human_mouse_move(page, loc)
        # Force replace even if browser already has a default FileList
        try:
            await loc.evaluate(
                """el => {
                  try {
                    el.value = '';
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                  } catch (e) {}
                }"""
            )
        except Exception:  # noqa: BLE001
            pass
        await loc.set_input_files(str(path))
        await human_pause(0.35, 0.8)
        if emit:
            emit(f"set_input_files OK <- {path.name}", "info")
        return True
    except Exception as exc:  # noqa: BLE001
        if emit:
            emit(f"set_input_files failed: {exc}", "error")
        return False


async def _fill_native_select(page: Page, field: dict, value: str) -> bool:
    """Open native <select> with real mouse, then choose option (click when possible)."""
    loc = await _resolve_control(page, field)
    if not loc:
        if field.get("id"):
            loc = _safe_locator(page, f'select{_css_attr_eq("id", field["id"])}')
        elif field.get("name"):
            loc = _safe_locator(page, f'select{_css_attr_eq("name", field["name"])}')
        else:
            return False
    if not loc or await _safe_count(loc) == 0:
        return False
    try:
        options = [
            o.strip()
            for o in await loc.locator("option").all_text_contents()
            if o and o.strip()
        ]
        mapped = pick_option(str(value), options) if options else str(value)

        # Real mouse → click the select to open it
        if not await human_click(page, loc, timeout=3000):
            await human_mouse_move(page, loc)
            await loc.click(timeout=2500)
        await human_pause(0.15, 0.35)

        # Prefer clicking a visible option node (custom-styled / some browsers)
        vl = mapped.lower()
        for o in options:
            if not o or o.lower() in {"select...", "select", "—", "-"}:
                continue
            if not (vl == o.lower() or vl in o.lower() or o.lower() in vl):
                continue
            opt_loc = loc.locator("option", has_text=o).first
            try:
                # Native OS dropdowns often aren't DOM-clickable; try anyway
                if await opt_loc.count():
                    await human_mouse_move(page, loc)
            except Exception:  # noqa: BLE001
                pass
            try:
                await loc.select_option(label=o, timeout=2500)
                await human_pause(0.12, 0.3)
                return True
            except Exception:  # noqa: BLE001
                continue

        try:
            await loc.select_option(label=mapped, timeout=2500)
            await human_pause(0.12, 0.3)
            return True
        except Exception:  # noqa: BLE001
            pass
        try:
            await loc.select_option(value=mapped, timeout=2500)
            await human_pause(0.12, 0.3)
            return True
        except Exception:  # noqa: BLE001
            return False
    except Exception:  # noqa: BLE001
        return False


async def _click_dropdown_option(page: Page, choice: str) -> bool:
    """Move mouse to a visible list option and click it — no typing."""
    if not choice:
        return False
    safe = str(choice).replace('"', '\\"')[:80]
    selectors = [
        f'[role="option"]:has-text("{safe}")',
        f'[role="listbox"] [role="option"]:has-text("{safe}")',
        f'li[role="option"]:has-text("{safe}")',
        f'.select2-results__option:has-text("{safe}")',
        f'li.select2-results__option:has-text("{safe}")',
        f'.select__option:has-text("{safe}")',
        f'.dropdown-item:has-text("{safe}")',
        f'[class*="option"]:has-text("{safe}")',
        f'[class*="Option"]:has-text("{safe}")',
        f'ul[role="listbox"] >> text="{safe}"',
        f'text="{safe}"',
    ]
    # Also try partial / first-line match for long labels
    short = safe.split("(")[0].strip()[:40]
    if short and short != safe:
        selectors.insert(1, f'[role="option"]:has-text("{short}")')

    for sel in selectors:
        try:
            opts = page.locator(sel)
            n = await opts.count()
            if n == 0:
                continue
            # Prefer visible option
            for i in range(min(n, 8)):
                opt = opts.nth(i)
                try:
                    if not await opt.is_visible():
                        continue
                except Exception:  # noqa: BLE001
                    pass
                if await human_click(page, opt, timeout=2500):
                    await human_pause(0.15, 0.35)
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def _resolve_combobox_trigger(page: Page, field: dict, label: str, emit: LogFn | None = None):
    """Find the clickable dropdown trigger using field id/hint first, then label."""
    short = re.sub(r"\*+", "", label or "").strip()[:60]
    short_q = short.replace('"', '\\"')
    fid = (field.get("id") or "").strip()
    hint = (field.get("selector_hint") or "").strip()

    # Direct locators from scan (Greenhouse #gender, #country, etc.)
    priority: list[str] = []
    if hint:
        priority.append(hint)
    if fid:
        eq = _css_attr_eq("id", fid)
        priority.extend(
            [
                f"select{eq}",
                f"[role=combobox]{eq}",
                f"button{eq}",
                eq,
                f"label[for=\"{fid.replace(chr(34), '')}\"] + * [role=combobox]",
                f"label[for=\"{fid.replace(chr(34), '')}\"] ~ [role=combobox]",
                f"{eq} >> xpath=ancestor::*[.//*[@role='combobox' or contains(@class,'select2') or contains(@class,'select__')]][1]//*[@role='combobox' or contains(@class,'select2-selection') or contains(@class,'select__control')][1]",
                f"{eq} >> xpath=following-sibling::*[1]//*[@role='combobox' or contains(@class,'select2-selection')][1]",
                f"{eq} >> xpath=..//*[@role='combobox' or contains(@class,'select2-selection') or contains(@class,'select__control')][1]",
            ]
        )

    if short_q:
        priority.extend(
            [
                f'label:has-text("{short_q}") >> .. >> [role=combobox]',
                f'label:has-text("{short_q}") >> .. >> .select2-selection',
                f'label:has-text("{short_q}") >> .. >> .select__control',
                f'label:has-text("{short_q}") >> .. >> button[aria-haspopup]',
                f'label:has-text("{short_q}") >> .. >> select',
                f'label:has-text("{short_q}") + [role=combobox]',
                f'label:has-text("{short_q}") ~ [role=combobox]',
                f'.application--question:has-text("{short_q}") >> [role=combobox]',
                f'.application--question:has-text("{short_q}") >> .select2-selection',
                f'.application--question:has-text("{short_q}") >> button',
                f'[class*="field"]:has-text("{short_q}") >> [role=combobox]',
                f'[class*="field"]:has-text("{short_q}") >> button[aria-haspopup]',
                f'div:has(> label:has-text("{short_q}")) >> [role=combobox]',
                f'div:has(> label:has-text("{short_q}")) >> select',
            ]
        )
        if len(short_q) >= 8:
            priority.append(f'[aria-label*="{short_q[:40]}" i]')

    if emit:
        emit(f"combobox: resolve trigger id={fid!r} label={short_q!r}", "info")

    for sel in priority:
        try:
            loc = _safe_locator(page, sel)
            if not loc or await _safe_count(loc) == 0:
                continue
            try:
                if not await loc.is_visible():
                    # Hidden native <select> still OK for select_option
                    tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                    if tag != "select":
                        continue
            except Exception:  # noqa: BLE001
                pass
            tag = await loc.evaluate("el => el.tagName.toLowerCase()")
            role = await loc.get_attribute("role")
            if emit:
                emit(f"combobox trigger hit sel={sel[:90]!r} tag={tag} role={role}", "info")
            return loc, sel, tag
        except Exception as exc:  # noqa: BLE001
            if emit:
                emit(f"combobox resolve err: {exc}", "error")
            continue
    return None, None, None


async def _fill_combobox(
    page: Page,
    field: dict,
    label: str,
    value: str,
    emit: LogFn | None = None,
) -> bool:
    """Open dropdown with mouse click, then mouse-click an option (no type-as-answer)."""
    if page.is_closed():
        if emit:
            emit("combobox: page closed", "error")
        return False

    short = re.sub(r"\*+", "", label or "").strip()[:60]
    trigger, used_sel, tag = await _resolve_combobox_trigger(page, field, label, emit=emit)

    if trigger is None:
        if emit:
            emit("combobox: NO trigger found", "error")
        return False

    if tag == "select":
        return await _fill_native_select(page, field, value)

    if emit:
        emit(f"combobox: click open via {used_sel!r}", "info")
    if not await human_click(page, trigger, timeout=3000):
        if emit:
            emit("combobox: trigger click failed", "error")
        return False
    await human_pause(0.35, 0.7)

    options = await collect_open_list_options(page)
    if emit:
        emit(f"combobox open options ({len(options)}): {options[:15]}", "info")

    # Long lists (School/Country): type filter then click — never submit typed answer alone
    if options and len(options) > 12:
        needle = str(value).strip()[:40]
        try:
            await page.keyboard.type(needle, delay=35)
            await human_pause(0.25, 0.45)
            options = await collect_open_list_options(page) or options
            if emit:
                emit(f"combobox filtered ({len(options)}): {options[:10]}", "info")
        except Exception as exc:  # noqa: BLE001
            if emit:
                emit(f"combobox filter type skipped: {exc}", "info")

    choice = pick_option(str(value), options) if options else str(value)
    if emit:
        emit(f"combobox: will click option {choice!r}", "info")

    if await _click_dropdown_option(page, choice):
        if emit:
            emit("combobox: option clicked OK", "info")
        return True

    if options:
        for o in options:
            if pick_option(str(value), [o]) == o or str(value).lower() in o.lower():
                if emit:
                    emit(f"combobox: retry option {o!r}", "info")
                if await _click_dropdown_option(page, o):
                    if emit:
                        emit("combobox: option clicked OK (retry)", "info")
                    return True

    if emit:
        emit(f"Dropdown click failed for [{short}] — no option click matched", "error")
    try:
        await page.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass
    return False


def random_delay() -> int:
    import random

    return random.randint(45, 120)


async def _fill_checkbox(page: Page, field: dict, value: str) -> bool:
    """Move mouse to checkbox (or its label) and click — no set_checked shortcut."""
    want = str(value).lower() in {
        "yes",
        "true",
        "1",
        "checked",
        "consent",
        "i agree",
        "agree",
        "accept",
    }
    loc = None
    if field.get("id"):
        loc = _safe_locator(page, f'input[type=checkbox]{_css_attr_eq("id", field["id"])}')
        if not loc or await _safe_count(loc) == 0:
            loc = _safe_locator(page, _css_attr_eq("id", field["id"]))
    elif field.get("name"):
        loc = _safe_locator(
            page, f'input[type=checkbox]{_css_attr_eq("name", field["name"])}'
        )
    if not loc or await _safe_count(loc) == 0:
        # Try label text near the question
        lab = re.sub(r"\*+", "", (field.get("label") or "")).strip()[:50]
        if lab:
            for sel in (
                f'label:has-text("{lab.replace(chr(34), "")}") >> input[type=checkbox]',
                f'label:has-text("{lab.replace(chr(34), "")}")',
            ):
                try:
                    cand = page.locator(sel).first
                    if await _safe_count(cand):
                        loc = cand
                        break
                except Exception:  # noqa: BLE001
                    continue
    if not loc or await _safe_count(loc) == 0:
        return False

    try:
        # If it's an input, read checked state; for label, find nested input
        target = loc
        tag = await loc.evaluate("el => el.tagName.toLowerCase()")
        input_el = loc
        if tag != "input":
            nested = loc.locator("input[type=checkbox]").first
            if await _safe_count(nested):
                input_el = nested
        try:
            checked = await input_el.is_checked()
        except Exception:  # noqa: BLE001
            checked = False
        if want == checked:
            return True
        # Mouse to the visible control and click
        if not await human_click(page, target, timeout=3000):
            await human_mouse_move(page, target)
            await target.click(timeout=2500)
        await human_pause(0.1, 0.25)
        return True
    except Exception:  # noqa: BLE001
        return False


async def _fill_radio(page: Page, field: dict, value: str) -> bool:
    """Move mouse onto the matching radio / Yes-No / tag control and click it."""
    name = field.get("name")
    label = re.sub(r"\*+", "", (field.get("label") or "")).strip()
    val = str(value).strip()
    opts = field.get("options") or []
    if opts:
        val = pick_option(val, [str(o) for o in opts])
    safe_val = val.replace('"', '\\"')
    selectors: list[str] = []
    if name:
        selectors.extend(
            [
                f'input[type=radio]{_css_attr_eq("name", name)}{_css_attr_eq("value", val)}',
                f'label:has-text("{safe_val}") >> input[type=radio]{_css_attr_eq("name", name)}',
                f'input[type=radio]{_css_attr_eq("name", name)}',
            ]
        )
    # Yes / No button groups (Ashby / custom ATS)
    if val.lower() in {"yes", "no", "y", "n", "true", "false"}:
        yn = "Yes" if val.lower() in {"yes", "y", "true"} else "No"
        scope = label[:40].replace('"', '\\"') if label else ""
        if scope:
            selectors.extend(
                [
                    f':text-is("{scope}") >> .. >> button:has-text("{yn}")',
                    f'label:has-text("{scope}") >> .. >> button:has-text("{yn}")',
                    f'[class*="field"]:has-text("{scope}") >> button:has-text("{yn}")',
                    f'[class*="field"]:has-text("{scope}") >> label:has-text("{yn}")',
                    f'fieldset:has-text("{scope}") >> button:has-text("{yn}")',
                    f'fieldset:has-text("{scope}") >> [role="radio"]:has-text("{yn}")',
                ]
            )
        selectors.extend(
            [
                f'button:has-text("{yn}")',
                f'[role="radio"]:has-text("{yn}")',
                f'label:has-text("{yn}") >> input[type=radio]',
            ]
        )
    # Scoped option click for scanned tag options
    if label:
        scope = label[:50].replace('"', '\\"')
        selectors.extend(
            [
                f'[class*="field"]:has-text("{scope}") >> button:has-text("{safe_val}")',
                f'[class*="field"]:has-text("{scope}") >> [role="radio"]:has-text("{safe_val}")',
                f'[class*="field"]:has-text("{scope}") >> label:has-text("{safe_val}")',
                f'fieldset:has-text("{scope}") >> button:has-text("{safe_val}")',
                f'fieldset:has-text("{scope}") >> label:has-text("{safe_val}")',
                f'.application--question:has-text("{scope}") >> button:has-text("{safe_val}")',
            ]
        )
    selectors.append(f'label:has-text("{safe_val}")')
    selectors.append(f'button:has-text("{safe_val}")')
    selectors.append(f'[role="radio"]:has-text("{safe_val}")')

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await _safe_count(loc) == 0:
                continue
            if await human_click(page, loc, timeout=3000):
                await human_pause(0.15, 0.35)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def _fill_choice_tags(
    page: Page,
    field: dict,
    value: str,
    emit: LogFn | None = None,
) -> bool:
    """Click a visible tag/pill/button option near the question label."""
    label = re.sub(r"\*+", "", (field.get("label") or "")).strip()
    opts = [str(o) for o in (field.get("options") or []) if o]
    choice = pick_option(str(value), opts) if opts else str(value)
    if emit:
        emit(f"choice-tags: label={label[:40]!r} choice={choice!r} opts={opts[:6]}", "info")
    if not choice:
        return False

    # Prefer scoped search under the question
    ok = await _fill_radio(page, {**field, "options": opts or [choice]}, choice)
    if ok:
        return True

    safe = choice.replace('"', '\\"')[:80]
    for sel in (
        f'button:has-text("{safe}")',
        f'[role="radio"]:has-text("{safe}")',
        f'[class*="pill"]:has-text("{safe}")',
        f'[class*="chip"]:has-text("{safe}")',
        f'[class*="tag"]:has-text("{safe}")',
        f'label:has-text("{safe}")',
    ):
        try:
            loc = page.locator(sel).first
            if await _safe_count(loc) == 0:
                continue
            if await human_click(page, loc, timeout=3000):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False
