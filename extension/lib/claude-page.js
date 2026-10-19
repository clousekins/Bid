// What the Claude panel does to the claude.ai page: read its state, enter a
// prompt, press send, open a new chat, read an answer.
//
// The twin of lib/gpt-page.js, for claude.ai instead of chatgpt.com. Same
// contract — the same step names (state, paste, send, newChat, clickContinue,
// readAt, probe), each running in the isolated extension world and only reading
// and editing the rendered DOM from the outside (world: "ISOLATED"; see
// background.js). The prompt goes in as trusted text with
// document.execCommand("insertText"); the reply is read straight from the DOM.
// Each step is serialised on its way into the page, so it must not reach for
// anything outside its own body — the selectors arrive as the first argument.
//
// claude.ai renders each assistant reply inside a `.font-claude-message`
// container and each of the person's turns inside `.font-user-message`. Those
// two classes are the steadiest markers it has, so the reader anchors on
// `.font-claude-message` rather than on per-message copy buttons the way the
// ChatGPT reader has to. The message box is a ProseMirror editor, the send
// button is labelled "Send", and the button that stops a reply is labelled
// "Stop"; a reply still streaming carries data-is-streaming.

export const SEL = {
  // Only ever the message box at the bottom. A reply can hold editable blocks
  // of its own, and a selector that matched those would paste the next prompt
  // into the previous answer — so the broad [contenteditable] catch-all comes
  // last, after the box's own markers.
  composer: [
    "div.ProseMirror[contenteditable='true']",
    "[contenteditable='true'][role='textbox']",
    "[aria-label*='prompt' i][contenteditable='true']",
    "fieldset [contenteditable='true']",
    "[contenteditable='true']",
  ],
  stop: [
    "[data-testid='stop-button']",
    "button[aria-label*='Stop' i]",
  ],
  send: [
    "[data-testid='send-button']",
    "button[aria-label*='Send' i]",
    "button[type='submit']",
  ],
  // Buttons around the message box that are never the send button, for when none
  // of the above matches and it has to be found by elimination.
  notSend:
    "attach|upload|file|add|model|tool|research|style|dictat|voice|microphone|stop|search|screenshot|more|menu|close|setting|project|chat history",
  // An assistant reply's rendered body. `.font-claude-message` wraps each one;
  // a person's turn never has it. Read in order; the first that matches wins.
  answer: [
    "div.font-claude-message",
    "[data-testid='assistant-message']",
    "div[data-is-streaming]",
  ],
};

function state(sel) {
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const first = (list) => list.flatMap((s) => [...document.querySelectorAll(s)]).find(visible);
  const composer = first(sel.composer);
  // Assistant messages already on the page: the baseline a new reply is measured
  // against. Outermost matches only, so a nested match never double-counts.
  let answers = 0;
  for (const s of sel.answer) {
    const nodes = [...document.querySelectorAll(s)].filter(visible);
    const outer = nodes.filter((el) => !nodes.some((o) => o !== el && o.contains(el)));
    if (outer.length) {
      answers = outer.length;
      break;
    }
  }
  const streaming = !!first(sel.stop) || !!document.querySelector('[data-is-streaming="true"]');
  const thread = document.querySelector("main") || document.body;
  // A nicety for a clearer error when the box never shows: only trusted when the
  // composer is absent and a prominent sign-in control is present.
  const loginRequired =
    !composer &&
    [...document.querySelectorAll("a, button")].some((el) =>
      /^(log ?in|sign ?in|continue with)/i.test((el.innerText || "").trim())
    );
  return {
    composer: !!composer,
    composerIsEditor: !!composer && composer.tagName !== "TEXTAREA",
    loginRequired,
    streaming,
    answers,
    // Grows as a reply streams in; steady once it is done.
    textLength: (thread.innerText || "").length,
    path: location.pathname,
    canContinue: [...document.querySelectorAll("button")].some((b) =>
      /^continue$/i.test((b.innerText || "").trim())
    ),
  };
}

// An insert, not typing: a persona prompt runs to hundreds of lines, and typing
// it would make the editor rebuild itself on every character. An insert lands as
// one edit. From the isolated world the browser itself performs it:
// execCommand("insertText") fires trusted beforeinput/input events
// (isTrusted: true), so to Claude it looks like ordinary text entry — no
// synthetic paste event, and no reaching into the page's own scripts.
function paste(sel, text) {
  const box = sel.composer
    .flatMap((s) => [...document.querySelectorAll(s)])
    .find((el) => el.getClientRects().length > 0);
  if (!box) return { ok: false, reason: "no message box" };
  box.focus({ preventScroll: true });
  if (box.tagName === "TEXTAREA") {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    setter.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
    return { ok: true };
  }
  if (document.activeElement !== box) {
    return { ok: false, reason: "the message box would not take focus" };
  }
  // execCommand works off the document selection, which may still sit in an
  // earlier editable block. Bind the caret to the end of the message box first.
  const range = document.createRange();
  range.selectNodeContents(box);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  const ok = document.execCommand("insertText", false, text);
  return ok ? { ok: true } : { ok: false, reason: "the message box ignored the insert" };
}

// Finds the send button — the one the person showed us, else by name, else by
// elimination among the buttons near the message box — and with `click`, presses
// it. Claude's composer is not always inside a <form>, so the elimination scope
// climbs to the nearest ancestor that actually holds buttons.
function send(sel, learned, click) {
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const enabled = (b) => !b.disabled && b.getAttribute("aria-disabled") !== "true";
  const box = sel.composer.flatMap((s) => [...document.querySelectorAll(s)]).find(visible);
  let scope = box ? box.closest("form") : null;
  if (!scope && box) {
    scope = box;
    for (let node = box.parentElement, i = 0; node && i < 6; node = node.parentElement, i += 1) {
      if (node.querySelectorAll("button").length) scope = node;
    }
  }
  scope = scope || document;
  const describe = (b) =>
    [b.getAttribute("aria-label"), b.getAttribute("data-testid"), b.id].filter(Boolean).join(" ");

  const byLearned = (within) =>
    [...within.querySelectorAll("button")].find(
      (b) =>
        visible(b) &&
        ((learned.aria && b.getAttribute("aria-label") === learned.aria) ||
          (learned.testid && b.getAttribute("data-testid") === learned.testid))
    );

  let button = null;
  let via = "";
  if (learned && (learned.aria || learned.testid)) {
    button = byLearned(scope) || byLearned(document);
    if (button) via = "learned";
  }
  if (!button) {
    button = sel.send.flatMap((s) => [...document.querySelectorAll(s)]).find(visible);
    if (button) via = "named";
  }
  if (!button) {
    const skip = new RegExp(sel.notSend, "i");
    const rest = [...scope.querySelectorAll("button")].filter(
      (b) => visible(b) && !skip.test(describe(b))
    );
    button = rest[rest.length - 1] || null; // the send arrow sits last
    if (button) via = "guessed";
  }
  const info = {
    found: !!button,
    enabled: !!button && enabled(button),
    via,
    aria: button ? button.getAttribute("aria-label") || "" : "",
    testid: button ? button.getAttribute("data-testid") || "" : "",
  };
  if (click && button && info.enabled) {
    button.click();
    info.clicked = true;
  }
  return info;
}

function newChat(sel) {
  const byText = [...document.querySelectorAll("a, button")].find(
    (el) =>
      /^new chat$/i.test((el.innerText || "").trim()) ||
      /^new chat$/i.test(el.getAttribute("aria-label") || "")
  );
  const target = byText || document.querySelector('a[href="/new"], a[href$="/new"]');
  if (!target) return false;
  target.click();
  return true;
}

function clickContinue(sel) {
  const button = [...document.querySelectorAll("button")].find((b) =>
    /^continue$/i.test((b.innerText || "").trim())
  );
  if (button) button.click();
  return !!button;
}

// Reads one assistant reply straight from the rendered page, counted from the
// end: 0 is the newest. Each reply is a `.font-claude-message`, so the reader
// picks that container by position and reads what is inside it — which drops the
// person's prompt by construction, since a prompt is a `.font-user-message` and
// never carries this class. A reply's output lives in widgets: a code block
// (the HTML or JSON the backend wants) reads as its code alone, without the ```
// fence; anything else reads block by block. A plain-text reply with no widgets
// reads as the message body's text.
function readAt(sel, fromEnd) {
  const normalize = (t) => String(t || "").replace(/\s+/g, " ").trim();
  const visible = (el) =>
    !!el &&
    el.getBoundingClientRect().width > 0 &&
    el.getBoundingClientRect().height > 0 &&
    getComputedStyle(el).visibility !== "hidden" &&
    !el.closest('[hidden], [inert], [aria-hidden="true"]') &&
    el.checkVisibility?.({ checkOpacity: true, checkVisibilityCSS: true }) !== false;

  const composerSel = sel.composer.join(", ");
  const isComposer = (el) => el.matches(composerSel) || !!el.closest(composerSel);
  // Buttons, toolbars, icons and screen-reader labels are never part of the
  // text; the composer is the one editor that is never a reply.
  const EXCLUDE =
    'nav, aside, header, footer, [role="dialog"], script, style, button, [role="button"],' +
    ' input, textarea, select, svg, [hidden], [aria-hidden="true"], .sr-only, [class*="sr-only"]';
  const read = (node) => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent;
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    if (node.matches(EXCLUDE) || isComposer(node)) return "";
    const style = getComputedStyle(node);
    if (style.display === "none" || style.visibility === "hidden") return "";
    if (node.tagName === "BR") return "\n";
    const inner = [...node.childNodes].map(read).join("");
    const block =
      /^(P|DIV|PRE|LI|H[1-6]|SECTION|ARTICLE|BLOCKQUOTE|TR|UL|OL)$/.test(node.tagName) ||
      /^(block|flex|grid|list-item|table-row)$/.test(style.display);
    return block ? `\n${inner}\n` : inner;
  };
  const clean = (t) => t.replace(/\n{3,}/g, "\n\n").trim();
  // One widget's text. A code block (or a card that holds only a code block)
  // reads as its code alone, so the block's language chip and controls stay out.
  const widgetText = (w) => {
    if (w.tagName === "PRE") return clean(read(w.querySelector("code") || w));
    const pres = [...w.querySelectorAll("pre")];
    if (pres.length === 1) {
      const rest = w.cloneNode(true);
      rest.querySelectorAll(`pre, ${EXCLUDE}`).forEach((el) => el.remove());
      if (!normalize(rest.textContent)) return clean(read(pres[0].querySelector("code") || pres[0]));
    }
    return clean(read(w));
  };

  // Assistant messages, outermost only, in document order.
  let msgs = sel.answer.flatMap((s) => [...document.querySelectorAll(s)]).filter(visible);
  msgs = msgs.filter((el, _i, arr) => !arr.some((o) => o !== el && o.contains(el)));
  msgs.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
  const idx = msgs.length - 1 - fromEnd;
  if (idx < 0) return { text: null, count: msgs.length };
  const msg = msgs[idx];

  // The reply's output widgets — code blocks and editable cards — inside it.
  let widgets = [
    ...msg.querySelectorAll('pre, [contenteditable="true"], [contenteditable="plaintext-only"]'),
  ].filter((w) => !isComposer(w) && visible(w));
  widgets = widgets.filter((w) => !widgets.some((o) => o !== w && o.contains(w)));
  if (widgets.length) {
    const text = clean(widgets.map(widgetText).join("\n\n"));
    return { text: text || null, count: msgs.length };
  }

  // No widgets: a plain-text reply. Read the message body itself.
  const text = clean(read(msg));
  return { text: text || null, count: msgs.length };
}

// A snapshot of how the page is built right now, for when a read fails: how many
// assistant messages match, what the newest one holds, what widgets it carries.
// It carries no personal text — only lengths, counts and a trimmed head/tail —
// and rides back in the failure so the log shows the shape of a DOM we cannot
// see from here.
function probe(sel) {
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const answerCounts = {};
  for (const s of sel.answer) answerCounts[s] = document.querySelectorAll(s).length;
  let msgs = sel.answer.flatMap((s) => [...document.querySelectorAll(s)]).filter(visible);
  msgs = msgs.filter((el, _i, arr) => !arr.some((o) => o !== el && o.contains(el)));
  msgs.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
  const newest = msgs[msgs.length - 1] || null;
  const tidy = (t) => t.replace(/\s+/g, " ").trim();
  const txt = newest ? newest.innerText || "" : "";
  const anyCloseHtml = [...document.querySelectorAll("pre")].some((p) => /<\/html>/i.test(p.innerText || ""));
  let widgets = [];
  if (newest) {
    widgets = [...newest.querySelectorAll('pre, [contenteditable="true"], [contenteditable="plaintext-only"]')].filter(
      visible
    );
    widgets = widgets.filter((w) => !widgets.some((o) => o !== w && o.contains(w)));
  }
  const widgetInfo = widgets.map((w) => ({
    tag: w.tagName,
    editable: w.getAttribute("contenteditable") || null,
    pre: w.querySelectorAll("pre").length,
    len: (w.innerText || "").length,
    closeHtml: /<\/html>/i.test(w.innerText || ""),
  }));
  return {
    widgetCount: widgets.length,
    widgets: widgetInfo,
    path: location.pathname,
    answerCounts,
    claudeMessages: document.querySelectorAll("div.font-claude-message").length,
    userMessages: document.querySelectorAll("div.font-user-message").length,
    streamingAttr: document.querySelectorAll("[data-is-streaming]").length,
    preTotal: document.querySelectorAll("pre").length,
    editable: document.querySelectorAll("[contenteditable='true'], [contenteditable='plaintext-only']").length,
    anyPreHasCloseHtml: anyCloseHtml,
    newestBodyLen: txt.length,
    head: tidy(txt.slice(0, 160)),
    tail: tidy(txt.slice(-160)),
  };
}

// The steps the panel may ask for, by name.
export const PAGE = { state, paste, send, newChat, clickContinue, readAt, probe };
