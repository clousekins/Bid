from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from playwright.async_api import Page


SCAN_SCRIPT = """
() => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 300);
  const escId = (id) => {
    try { return (window.CSS && CSS.escape) ? CSS.escape(id) : String(id).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&'); }
    catch { return String(id); }
  };
  const idSelector = (id) => {
    if (!id) return null;
    // Prefer [id="…"] — IDs starting with digits break raw #id CSS
    const safe = String(id).replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
    return `[id="${safe}"]`;
  };
  const nameSelector = (name) => {
    if (!name) return null;
    const safe = String(name).replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
    return `[name="${safe}"]`;
  };

  const isVisible = (el) => {
    if (!el || el.disabled) return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    const st = window.getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || Number(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1 && (el.type || '').toLowerCase() !== 'file') return false;
    return true;
  };

  const junkName = (el) => {
    const blob = `${el.name || ''} ${el.id || ''} ${el.className || ''}`.toLowerCase();
    return /recaptcha|g-recaptcha|honeypot|website_url|resumator-resumetext/.test(blob);
  };

  const labelFor = (el) => {
    if (el.id) {
      const lab = document.querySelector(`label[for="${escId(el.id)}"]`);
      if (lab) return clean(lab.innerText);
    }
    const wrap = el.closest('label');
    if (wrap) return clean(wrap.innerText);
    const field = el.closest(
      '.field, .form-field, [class*="field"], [data-field], .application--question, .question, .form-group'
    );
    if (field) {
      const lab = field.querySelector(
        'label, .label, legend, h3, h4, .application--question--label'
      );
      if (lab) return clean(lab.innerText);
    }
    // Prefer previous sibling label over first label in a huge parent (JazzHR)
    let sib = el.previousElementSibling;
    for (let i = 0; i < 4 && sib; i++) {
      if (sib.tagName === 'LABEL' || (sib.className || '').toLowerCase().includes('label')) {
        return clean(sib.innerText);
      }
      sib = sib.previousElementSibling;
    }
    return clean(el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || '');
  };

  const fields = [];
  const seen = new Set();
  const seenSoft = new Set();

  const push = (obj) => {
    const soft = `${obj.kind}|${(obj.label || '').toLowerCase().replace(/\\*+/g, '').trim()}`;
    const key = `${obj.kind}|${obj.name}|${obj.id}|${obj.label}`;
    if (seen.has(key)) return;
    // Keep candidates; Python dedupe_fields picks the best per soft key
    if (obj.label && seenSoft.has(soft) && !(obj.name || obj.id) && obj.kind !== 'select') return;
    seen.add(key);
    if (obj.label) seenSoft.add(soft);
    fields.push(obj);
  };

  // Native inputs / textareas / selects (include file for resume upload)
  document.querySelectorAll('input, textarea, select').forEach((el, idx) => {
    const type = (el.getAttribute('type') || el.tagName || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) return;
    if (junkName(el)) return;
    if (!isVisible(el) && type !== 'file') return;
    const label = labelFor(el);
    const required = !!(el.required || el.getAttribute('aria-required') === 'true' || (label && label.includes('*')));
    let options = [];
    if (el.tagName.toLowerCase() === 'select') {
      options = Array.from(el.options || []).map(o => clean(o.text)).filter(Boolean).slice(0, 80);
    }
    if (type === 'radio' || type === 'checkbox') {
      options = [el.value || el.getAttribute('aria-label') || 'checked'].filter(Boolean);
    }
    let kind = 'text';
    if (el.tagName.toLowerCase() === 'select') kind = 'select';
    else if (type === 'radio') kind = 'radio';
    else if (type === 'checkbox') kind = 'checkbox';
    else if (type === 'file') kind = 'file';
    else if (
      el.getAttribute('role') === 'combobox' ||
      el.getAttribute('aria-haspopup') === 'listbox' ||
      !!el.getAttribute('aria-autocomplete') ||
      /^(gender|country|race|veteran_status|disability_status|hispanic_ethnicity|pronouns)$/i.test(el.id || '') ||
      /^(school|degree|discipline)--/i.test(el.id || '')
    ) {
      kind = 'combobox';
    }

    // Collapse radio groups onto the question label (not "Yes"/"No" option labels)
    if (type === 'radio' && el.name) {
      const group = el.closest('fieldset, [role="radiogroup"], .field, .application--question, [class*="field"]');
      let groupLabel = label;
      if (group) {
        const gl = group.querySelector('legend, label.q, .application--question--label, h3, h4, [class*="label"]');
        if (gl) {
          const gt = clean(gl.innerText);
          // Prefer longer question text over short option text
          if (gt && gt.length > groupLabel.length) groupLabel = gt;
        }
      }
      if (/^(yes|no|y|n)$/i.test(groupLabel) || groupLabel.length < 4) {
        // Skip orphan option-only radios; group scanner or first sibling handles them
        const first = document.querySelector(`input[type="radio"][name="${CSS.escape ? CSS.escape(el.name) : el.name}"]`);
        if (first && first !== el) return;
      }
      const softRadio = `radio|${groupLabel.toLowerCase().replace(/\\*+/g, '').trim()}`;
      if (seenSoft.has(softRadio)) return;
      const radios = Array.from(document.querySelectorAll(`input[type="radio"][name="${el.name.replace(/"/g, '\\\\"')}"]`));
      options = radios.map(r => {
        const lab = r.closest('label') || (r.id ? document.querySelector(`label[for="${escId(r.id)}"]`) : null);
        return clean((lab && lab.innerText) || r.value || '');
      }).filter(Boolean);
      push({
        index: idx,
        kind: 'radio',
        tag: 'input',
        type: 'radio',
        name: el.name || '',
        id: el.id || '',
        label: groupLabel,
        required: !!(el.required || groupLabel.includes('*')),
        options,
        value: el.checked ? (el.value || '') : '',
        accept: '',
        selector_hint: nameSelector(el.name) || idSelector(el.id),
      });
      return;
    }

    push({
      index: idx,
      kind,
      tag: el.tagName.toLowerCase(),
      type,
      name: el.name || '',
      id: el.id || '',
      label,
      required,
      options,
      value: type === 'file' ? (el.files && el.files.length ? 'uploaded' : '') : (el.value || '').slice(0, 200),
      accept: el.getAttribute('accept') || '',
      selector_hint: idSelector(el.id) || nameSelector(el.name),
    });
  });

  // Greenhouse / custom listboxes (role=combobox / listbox buttons)
  document.querySelectorAll('[role="combobox"], [aria-haspopup="listbox"], button.select, .select__control, .select2-selection, [data-testid*="select"]').forEach((el, idx) => {
    // Prefer real question label — never use selected option innerText as the label
    let label = labelFor(el) || clean(el.getAttribute('aria-label') || '');
    const valueText = clean(el.getAttribute('aria-valuetext') || el.value || '');
    const inner = clean(el.innerText || '');
    // Skip junk / value-as-label widgets (+1, Select..., already-selected option text)
    const junk = /^(select(\\.\\.\\.)?|search|\\+?\\d{1,3})$/i;
    if (!label || junk.test(label) || (label === inner && junk.test(inner))) {
      if (!el.id) return;
      // Keep if we have an id and a real labelFor already failed — try aria-labelledby
      const lb = el.getAttribute('aria-labelledby');
      if (lb) {
        const labEl = document.getElementById(lb) || document.querySelector('#' + escId(lb.split(/\\s+/)[0]));
        if (labEl) label = clean(labEl.innerText);
      }
      if (!label || junk.test(label)) return;
    }
    // Don't re-add if same id already pushed as combobox/select
    if (el.id && fields.some(f => f.id === el.id && (f.kind === 'combobox' || f.kind === 'select'))) return;
    push({
      index: 10000 + idx,
      kind: 'combobox',
      tag: el.tagName.toLowerCase(),
      type: 'combobox',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      label,
      required: label.includes('*') || el.getAttribute('aria-required') === 'true',
      options: [],
      value: valueText || (junk.test(inner) ? '' : inner).slice(0, 200),
      selector_hint: idSelector(el.id),
    });
  });

  // Fieldsets / question blocks with select-looking widgets
  document.querySelectorAll('.application--question, [class*="Question"], fieldset').forEach((block, idx) => {
    const labEl = block.querySelector('label, legend, .application--question--label, h3, h4');
    const label = clean(labEl ? labEl.innerText : '');
    if (!label) return;
    const hasNative = block.querySelector('input, textarea, select');
    if (hasNative) return;
    const combo = block.querySelector(
      '[role="combobox"], .select, .select2-selection, .select__control, button.select, button[aria-haspopup="listbox"], [aria-haspopup="listbox"]'
    );
    if (!combo) return;
    push({
      index: 20000 + idx,
      kind: 'combobox',
      tag: 'div',
      type: 'combobox',
      name: '',
      id: combo.id || '',
      label,
      required: label.includes('*'),
      options: [],
      value: clean(combo.getAttribute('aria-valuetext') || ''),
      selector_hint: idSelector(combo.id),
    });
  });

  // Tag / pill / button choice groups (Ashby, Lever, custom ATS) — not dropdowns
  document.querySelectorAll(
    '[role="radiogroup"], fieldset, .application--question, [class*="Question"], [class*="field"], [data-testid*="question"]'
  ).forEach((block, idx) => {
    const labEl = block.querySelector('label, legend, .application--question--label, h3, h4, [class*="label"]');
    const label = clean(labEl ? labEl.innerText : block.getAttribute('aria-label') || '');
    if (!label || label.length < 2) return;
    if (block.querySelector('select, [role="combobox"], textarea, input[type="text"], input[type="email"], input[type="tel"]')) {
      // Still allow pure radio/checkbox groups that also have a hidden text — skip if real text input
      const textish = block.querySelector('textarea, input[type="text"], input[type="email"], input[type="tel"]');
      if (textish && isVisible(textish)) return;
    }
    const choices = Array.from(
      block.querySelectorAll(
        'input[type="radio"], input[type="checkbox"], [role="radio"], button[type="button"], button:not([type])'
      )
    ).filter((el) => {
      if (!isVisible(el)) return false;
      // Skip container nodes that merely wrap pills (e.g. class "pills")
      if (el.querySelector('button, input, [role="radio"]')) return false;
      return true;
    });
    // Need multiple short choice-looking controls
    const opts = [];
    const seenOpt = new Set();
    const labelNorm = label.toLowerCase().replace(/\\*+/g, '').trim();
    for (const el of choices) {
      let t = '';
      if (el.tagName === 'INPUT') {
        const lab = el.closest('label') || (el.id ? document.querySelector(`label[for="${escId(el.id)}"]`) : null);
        t = clean((lab && lab.innerText) || el.value || el.getAttribute('aria-label') || '');
      } else {
        t = clean(el.innerText || el.getAttribute('aria-label') || '');
      }
      if (!t || t.length > 60) continue;
      if (/^(submit|next|continue|apply|back|cancel|upload)$/i.test(t)) continue;
      const key = t.toLowerCase().replace(/\\*+/g, '').trim();
      if (!key || key === labelNorm || key.includes(labelNorm) || labelNorm.includes(key) && key.length > 20) continue;
      if (seenOpt.has(key)) continue;
      seenOpt.add(key);
      opts.push(t);
    }
    if (opts.length < 2 || opts.length > 12) return;
    // Prefer radio kind when inputs are radios; else choice (button tags)
    const hasRadio = !!block.querySelector('input[type="radio"], [role="radio"]');
    const hasCheck = !!block.querySelector('input[type="checkbox"]');
    let kind = 'radio';
    if (!hasRadio && hasCheck) kind = 'checkbox';
    if (!hasRadio && !hasCheck) kind = 'radio'; // button pills → click like radio
    const soft = `${kind}|${label.toLowerCase().replace(/\\*+/g, '').trim()}`;
    if (seenSoft.has(soft)) return;
    push({
      index: 30000 + idx,
      kind,
      tag: 'div',
      type: kind,
      name: '',
      id: '',
      label,
      required: label.includes('*') || block.getAttribute('aria-required') === 'true',
      options: opts.slice(0, 20),
      value: '',
      selector_hint: null,
    });
  });

  return fields;
}
"""


async def scan_form_fields(page: Page) -> list[dict[str, Any]]:
    try:
        fields = await page.evaluate(SCAN_SCRIPT)
        return fields if isinstance(fields, list) else []
    except Exception:  # noqa: BLE001
        return []


async def collect_open_list_options(page: Page) -> list[str]:
    script = """
    () => {
      const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
      const opts = [];
      const nodes = document.querySelectorAll(
        '[role="option"], [role="listbox"] li, .select__option, .dropdown-item, '
        + 'ul[role="listbox"] > li, .select2-results__option, li.select2-results__option, '
        + '[class*="select2-result"]'
      );
      nodes.forEach(n => {
        const t = clean(n.innerText);
        if (t) opts.push(t.slice(0, 200));
      });
      return [...new Set(opts)].slice(0, 120);
    }
    """
    try:
        return await page.evaluate(script)
    except Exception:  # noqa: BLE001
        return []


async def scan_required_empty(page: Page) -> list[str]:
    script = """
    () => {
      const msgs = [];
      const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
      const patterns = [
        /^this field is required\\.?$/i,
        /^required$/i,
        /is required/i,
        /required field/i,
        /missing (entry|field|value|input|selection)/i,
        /please (complete|fill|enter|select|provide|correct)/i,
        /cannot be blank/i,
        /must (be|select|enter|provide)/i,
        /field.+required/i,
        /needs? corrections?/i,
        /form needs/i,
        /upload (a |your )?resume/i,
        /resume is required/i,
        /invalid (phone|email|url|number)/i,
        /enter (a |your )?(valid )?phone/i,
      ];
      const looksLikeError = (el, t) => {
        if (!t || t.length > 280) return false;
        const cls = (el.className || '').toString().toLowerCase();
        const role = (el.getAttribute('role') || '').toLowerCase();
        const aria = (el.getAttribute('aria-live') || '').toLowerCase();
        const styleErr = cls.includes('error') || cls.includes('invalid') || cls.includes('danger')
          || cls.includes('alert') || cls.includes('warning')
          || role === 'alert' || aria === 'assertive' || aria === 'polite';
        const colorish = getComputedStyle(el).color || '';
        const bg = getComputedStyle(el).backgroundColor || '';
        const isRedish = /rgb\\(\\s*(2[0-2]\\d|1\\d\\d|[89]\\d)\\s*,\\s*\\d+\\s*,\\s*\\d+\\s*\\)/.test(colorish)
          || /rgb\\(\\s*(2[0-2]\\d|1\\d\\d|[89]\\d)\\s*,\\s*\\d+\\s*,\\s*\\d+\\s*\\)/.test(bg)
          || colorish.includes('red') || bg.includes('red');
        if (!patterns.some(p => p.test(t))) return false;
        return styleErr || isRedish || t.length < 120 || /missing|required|correction/i.test(t);
      };
      const pushMsg = (raw) => {
        const t = clean(raw);
        if (!t) return;
        msgs.push(t.slice(0, 200));
        // Pull explicit field names: "required field: Phone Number"
        const m = t.match(/required field\\s*:\\s*([^\\n.]+)/i)
          || t.match(/missing entry for required field\\s*:\\s*([^\\n.]+)/i)
          || t.match(/missing[:\\s]+([^\\n.]{2,60})/i);
        if (m && m[1]) msgs.push(clean(m[1]).slice(0, 120));
      };

      document.querySelectorAll(
        'span, div, p, li, label, small, em, strong, a, [role=alert], .error, .invalid-feedback, '
        + '[class*="error"], [class*="Error"], [class*="alert"], [class*="Alert"], '
        + '[class*="banner"], [class*="Banner"], [class*="toast"], [data-testid*="error"]'
      ).forEach(el => {
        const t = clean(el.innerText || el.textContent || '');
        if (!looksLikeError(el, t)) return;
        const block = el.closest(
          '.field, .form-field, .application--question, li, div, fieldset, '
          + '[class*="Question"], [class*="field"], [class*="Field"]'
        );
        const lab = block
          ? (block.querySelector('label, legend, .label, h3, h4, .application--question--label')?.innerText || '')
          : '';
        pushMsg(lab || t);
        pushMsg(t);
      });

      document.querySelectorAll('[aria-invalid="true"]').forEach(el => {
        const lab = (
          el.getAttribute('aria-label')
          || el.getAttribute('aria-labelledby')
          || el.name
          || el.id
          || 'invalid field'
        ).toString();
        // Resolve labelledby text if present
        let resolved = lab;
        if (el.getAttribute('aria-labelledby')) {
          const ids = el.getAttribute('aria-labelledby').split(/\\s+/);
          resolved = ids.map(id => document.getElementById(id)?.innerText || '').join(' ') || lab;
        }
        // Prefer associated <label for=>
        if (el.id) {
          const forLab = document.querySelector(`label[for="${CSS.escape ? CSS.escape(el.id) : el.id}"]`);
          if (forLab) resolved = forLab.innerText || resolved;
        }
        pushMsg(clean(resolved) || 'invalid field');
      });

      // Empty required inputs (native)
      document.querySelectorAll('input[required], textarea[required], select[required], [aria-required="true"]').forEach(el => {
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'file'].includes(type)) return;
        const val = (el.value || '').trim();
        if (val) return;
        if (el.offsetParent === null && type !== 'file') return;
        let lab = '';
        if (el.id) {
          const forLab = document.querySelector(`label[for="${CSS.escape ? CSS.escape(el.id) : el.id}"]`);
          if (forLab) lab = forLab.innerText || '';
        }
        lab = clean(lab || el.getAttribute('aria-label') || el.name || el.placeholder || 'required field');
        if (lab) pushMsg(lab);
      });

      return [...new Set(msgs)].slice(0, 50);
    }
    """
    try:
        return await page.evaluate(script)
    except Exception:  # noqa: BLE001
        return []


def write_form_fields(folder: Path, fields: list[dict[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "form_fields.json").write_text(
        json.dumps(fields, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_answers(folder: Path, answers: dict[str, Any]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "form_answers.json").write_text(
        json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8"
    )
