"""E2E assisted-apply fixture: text, dropdown, tags/pills, radio, checkbox + human mouse/scroll/type."""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from playwright.async_api import async_playwright

from app.services.apply_fill import fill_all_answers
from app.services.form_scan import scan_form_fields
from app.services.human_input import set_input_logger, set_os_mouse

HTML = """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>POONG E2E Apply</title>
<style>
  body { font-family: Georgia, serif; padding: 24px 40px 120px; max-width: 720px; margin: 0 auto; }
  h1 { margin-top: 0; }
  .field { margin: 28px 0; padding: 12px 0; border-bottom: 1px solid #ddd; }
  label.q { display:block; font-weight: 600; margin-bottom: 8px; }
  input[type=text], input[type=email], select, textarea {
    font-size: 16px; padding: 8px 12px; min-width: 280px; width: 90%;
  }
  .combo { border: 1px solid #888; background: #fff; cursor: pointer; padding: 8px 12px; width: 280px; }
  .menu { display:none; border:1px solid #888; width: 280px; background:#fff; max-height: 180px; overflow:auto; }
  .menu.open { display:block; }
  .menu div { padding: 8px 12px; cursor:pointer; }
  .menu div:hover { background:#eef; }
  .pills { display:flex; gap: 8px; flex-wrap: wrap; }
  .pills button {
    border: 1px solid #444; background: #fafafa; padding: 8px 14px; cursor: pointer; border-radius: 4px;
  }
  .pills button.selected { background: #222; color: #fff; }
  #spacer { height: 900px; background: linear-gradient(#f8f8f8, #e8e8e8); display:flex; align-items:center; justify-content:center; color:#888; }
  #log { margin-top: 24px; white-space: pre-wrap; background:#111; color:#0f0; padding:12px; font-family: monospace; font-size: 12px; }
</style>
</head><body>
  <h1>Assisted apply E2E fixture</h1>

  <div class="field" id="f-name">
    <label class="q" for="first_name">First Name*</label>
    <input id="first_name" type="text" />
  </div>

  <div class="field" id="f-email">
    <label class="q" for="email">Email*</label>
    <input id="email" type="email" />
  </div>

  <div id="spacer">↓ scroll zone (human wheel should crawl here) ↓</div>

  <div class="field" id="f-country">
    <label class="q" for="country">Country*</label>
    <select id="country">
      <option value="">Select...</option>
      <option value="US">United States</option>
      <option value="CA">Canada</option>
    </select>
  </div>

  <div class="field" id="f-gender">
    <label class="q" for="gender">Gender</label>
    <input id="gender" type="text" readonly placeholder="Select..." />
    <div id="gender-menu" class="menu" role="listbox">
      <div role="option">Male</div>
      <div role="option">Female</div>
      <div role="option">Decline to self-identify</div>
    </div>
  </div>

  <div class="field" id="f-auth">
    <label class="q">Are you authorized to work in the US?*</label>
    <div class="pills" role="radiogroup" aria-label="Are you authorized to work in the US?*">
      <button type="button" data-val="Yes">Yes</button>
      <button type="button" data-val="No">No</button>
    </div>
    <input type="hidden" id="authorized" value="" />
  </div>

  <div class="field" id="f-travel">
    <label class="q">Are you open to Travel?*</label>
    <div class="pills">
      <button type="button" class="pill" data-val="Yes">Yes</button>
      <button type="button" class="pill" data-val="No">No</button>
      <button type="button" class="pill" data-val="Sometimes">Sometimes</button>
    </div>
    <input type="hidden" id="travel" value="" />
  </div>

  <div class="field" id="f-radio">
    <label class="q">Do you require sponsorship?*</label>
    <label><input type="radio" name="sponsor" value="Yes"> Yes</label>
    <label><input type="radio" name="sponsor" value="No"> No</label>
  </div>

  <div class="field" id="f-consent">
    <label><input type="checkbox" id="consent" name="consent"> I agree to the privacy policy*</label>
  </div>

  <div id="log"></div>
  <script>
    const log = (m) => { document.getElementById('log').textContent += m + '\\n'; };
    const gender = document.getElementById('gender');
    const gmenu = document.getElementById('gender-menu');
    gender.addEventListener('click', () => gmenu.classList.add('open'));
    gmenu.querySelectorAll('[role=option]').forEach(o => {
      o.addEventListener('click', () => {
        gender.value = o.textContent;
        gmenu.classList.remove('open');
        log('gender=' + o.textContent);
      });
    });
    document.querySelectorAll('#f-auth button').forEach(b => {
      b.addEventListener('click', () => {
        document.querySelectorAll('#f-auth button').forEach(x => x.classList.remove('selected'));
        b.classList.add('selected');
        document.getElementById('authorized').value = b.dataset.val;
        log('authorized=' + b.dataset.val);
      });
    });
    document.querySelectorAll('#f-travel button').forEach(b => {
      b.addEventListener('click', () => {
        document.querySelectorAll('#f-travel button').forEach(x => x.classList.remove('selected'));
        b.classList.add('selected');
        document.getElementById('travel').value = b.dataset.val;
        log('travel=' + b.dataset.val);
      });
    });
    document.getElementById('country').addEventListener('change', e => log('country=' + e.target.value));
    document.getElementById('consent').addEventListener('change', e => log('consent=' + e.target.checked));
    document.querySelectorAll('input[name=sponsor]').forEach(r => {
      r.addEventListener('change', () => log('sponsor=' + r.value));
    });
  </script>
</body></html>
"""


async def main() -> int:
    logs: list[str] = []

    def emit(msg: str, level: str = "info") -> None:
        line = f"[{level}] {msg}"
        logs.append(line)
        print(line)

    set_input_logger(emit)
    set_os_mouse(True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=0)
        page = await browser.new_page(viewport={"width": 900, "height": 700})
        await page.set_content(HTML)
        await asyncio.sleep(0.4)

        fields = await scan_form_fields(page)
        emit(f"scanned fields={len(fields)}")
        for f in fields:
            emit(f"  kind={f.get('kind')} id={f.get('id')!r} label={str(f.get('label'))[:50]!r} opts={f.get('options')}")

        answers = {
            "First Name*": "Phong",
            "Email*": "pvc.pc.ai@gmail.com",
            "Country*": "United States",
            "Gender": "Male",
            "Are you authorized to work in the US?*": "Yes",
            "Are you open to Travel?*": "Yes",
            "Do you require sponsorship?*": "No",
            "I agree to the privacy policy*": "Yes",
        }

        t0 = time.perf_counter()
        stats = await fill_all_answers(page, fields, answers, emit=emit, skip_filled=False)
        elapsed = time.perf_counter() - t0
        emit(f"fill stats={stats} elapsed={elapsed:.1f}s")

        results = {
            "first_name": await page.locator("#first_name").input_value(),
            "email": await page.locator("#email").input_value(),
            "country": await page.locator("#country").input_value(),
            "gender": await page.locator("#gender").input_value(),
            "authorized": await page.locator("#authorized").input_value(),
            "travel": await page.locator("#travel").input_value(),
            "sponsor": await page.locator("input[name=sponsor]:checked").count(),
            "sponsor_val": await page.locator("input[name=sponsor]:checked").get_attribute("value")
            if await page.locator("input[name=sponsor]:checked").count()
            else None,
            "consent": await page.locator("#consent").is_checked(),
        }
        emit(f"DOM results={results}")

        ok = (
            results["first_name"] == "Phong"
            and results["email"] == "pvc.pc.ai@gmail.com"
            and results["country"] == "US"
            and results["gender"] == "Male"
            and results["authorized"] == "Yes"
            and results["travel"] == "Yes"
            and results["sponsor_val"] == "No"
            and results["consent"] is True
            and elapsed >= 3.0  # must not be instant — human pacing
        )
        print("OVERALL", "PASS" if ok else "FAIL", f"({elapsed:.1f}s)")
        out = ROOT / "data" / "e2e_apply_test_log.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        page_log = await page.locator("#log").inner_text()
        out.write_text("\n".join(logs) + "\n\nPAGE:\n" + page_log + f"\n\nRESULTS={results}\n", encoding="utf-8")
        await asyncio.sleep(0.8)
        await browser.close()
        return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
