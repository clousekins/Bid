"""Self-test: CDP mouse + native select + custom dropdown click + Greenhouse-style id combobox."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from playwright.async_api import async_playwright

from app.services.apply_fill import _fill_checkbox, _fill_combobox, _fill_native_select
from app.services.human_input import human_mouse_move, set_input_logger

HTML = """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>POONG mouse test</title>
<style>
  body { font-family: sans-serif; padding: 40px; }
  label { display:block; margin: 16px 0 6px; }
  select, .combo, button, input { font-size: 16px; padding: 8px 12px; min-width: 220px; }
  .combo { border: 1px solid #888; background: #fff; cursor: pointer; width: 240px; }
  .menu { display:none; border:1px solid #888; width: 240px; background:#fff; }
  .menu.open { display:block; }
  .menu div { padding: 8px 12px; cursor:pointer; }
  .menu div:hover { background:#eef; }
  #log { margin-top: 24px; white-space: pre-wrap; background:#111; color:#0f0; padding:12px; }
</style>
</head><body>
  <h1>POONG mouse / dropdown test</h1>
  <label for="country">Country *</label>
  <select id="country" name="country">
    <option value="">Select...</option>
    <option value="US">United States</option>
    <option value="CA">Canada</option>
    <option value="TX">Texas</option>
  </select>

  <label>Immigration status *</label>
  <div id="immig" class="combo" role="combobox" tabindex="0">Select...</div>
  <div id="immig-menu" class="menu" role="listbox">
    <div role="option">US Citizen</div>
    <div role="option">Green Card</div>
    <div role="option">H1B</div>
  </div>

  <label for="gender">Gender</label>
  <input id="gender" type="text" readonly value="" placeholder="Select..." />
  <div id="gender-menu" class="menu" role="listbox">
    <div role="option">Male</div>
    <div role="option">Female</div>
    <div role="option">Decline to self-identify</div>
  </div>

  <label><input type="checkbox" id="consent" name="consent"> I agree to SMS *</label>

  <div id="log"></div>
  <script>
    const log = (m) => { document.getElementById('log').textContent += m + '\\n'; };
    const combo = document.getElementById('immig');
    const menu = document.getElementById('immig-menu');
    combo.addEventListener('click', () => menu.classList.toggle('open'));
    menu.querySelectorAll('[role=option]').forEach(o => {
      o.addEventListener('click', () => {
        combo.textContent = o.textContent;
        menu.classList.remove('open');
        log('combobox selected: ' + o.textContent);
      });
    });
    const gender = document.getElementById('gender');
    const gmenu = document.getElementById('gender-menu');
    gender.addEventListener('click', () => gmenu.classList.add('open'));
    gmenu.querySelectorAll('[role=option]').forEach(o => {
      o.addEventListener('click', () => {
        gender.value = o.textContent;
        gmenu.classList.remove('open');
        log('gender selected: ' + o.textContent);
      });
    });
    document.getElementById('country').addEventListener('change', (e) => {
      log('select changed: ' + e.target.value + ' / ' + e.target.selectedOptions[0].text);
    });
    document.getElementById('consent').addEventListener('change', (e) => {
      log('checkbox: ' + e.target.checked);
    });
  </script>
</body></html>
"""


async def main() -> None:
    logs: list[str] = []

    def emit(msg: str, level: str = "info") -> None:
        line = f"[{level}] {msg}"
        logs.append(line)
        print(line)

    set_input_logger(emit)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=40)
        page = await browser.new_page(viewport={"width": 900, "height": 700})
        await page.set_content(HTML)
        await asyncio.sleep(0.3)

        print("--- mouse wander ---")
        await human_mouse_move(page)

        print("--- native select ---")
        ok_sel = await _fill_native_select(
            page,
            {"id": "country", "name": "country", "kind": "select"},
            "United States",
        )
        val = await page.locator("#country").input_value()
        print("select ok=", ok_sel, "value=", val)

        print("--- custom combobox (label) ---")
        ok_cb = await _fill_combobox(
            page,
            {"id": "immig", "kind": "combobox", "selector_hint": "[id=\"immig\"]"},
            "Immigration status *",
            "US Citizen",
            emit=emit,
        )
        combo_txt = await page.locator("#immig").inner_text()
        print("combobox ok=", ok_cb, "text=", combo_txt)

        print("--- greenhouse-style #gender click ---")
        ok_g = await _fill_combobox(
            page,
            {"id": "gender", "kind": "combobox", "selector_hint": "[id=\"gender\"]"},
            "Gender",
            "Male",
            emit=emit,
        )
        gval = await page.locator("#gender").input_value()
        print("gender ok=", ok_g, "value=", gval)

        print("--- checkbox ---")
        ok_chk = await _fill_checkbox(
            page,
            {"id": "consent", "name": "consent", "kind": "checkbox", "label": "I agree to SMS *"},
            "Yes",
        )
        checked = await page.locator("#consent").is_checked()
        print("checkbox ok=", ok_chk, "checked=", checked)

        page_log = await page.locator("#log").inner_text()
        print("--- page log ---\n", page_log)

        ok = (
            ok_sel
            and val == "US"
            and ok_cb
            and "US Citizen" in combo_txt
            and ok_g
            and gval == "Male"
            and ok_chk
            and checked
        )
        print("OVERALL", "PASS" if ok else "FAIL")
        out = ROOT / "data" / "mouse_dropdown_test_log.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(logs) + "\n\nPAGE:\n" + page_log, encoding="utf-8")
        await browser.close()
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
