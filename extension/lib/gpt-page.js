// What the ChatGPT panel does to the chatgpt.com page: read its state, enter a
// prompt, press send, open a new chat, read an answer.
//
// Each function runs in the isolated extension world — the content-script world
// the service worker injects it into (world: "ISOLATED"; see background.js). It
// only reads and edits the rendered page from the outside: it never changes
// anything the page's own scripts can see, it enters the prompt as trusted text
// (document.execCommand("insertText")), and it reads the reply straight from the
// DOM instead of through the clipboard. Each is serialised on the way in, so it
// must not reach for anything outside its own body — the selectors arrive as the
// first argument.
//
// chatgpt.com renames and strips its markup without notice. As of this
// writing it has dropped almost every data-testid and every message-role
// attribute, and some builds carry no role, `.markdown` or <article> on a
// message at all. What holds across them: the message box is a ProseMirror
// editor, the button that stops a reply is labelled "Stop", and every message
// still carries its own "Copy message" button. readAt reads a reply's body
// where a build labels it, and otherwise anchors on that copy button and climbs
// to the message around it.

export const SEL = {
  // Only ever the message box at the bottom. A reply can hold editable blocks
  // of its own — a "Cover Letter" card is the same kind of ProseMirror editor
  // — and a selector that matched those pasted the next prompt into the
  // previous answer. The message box sits inside the chat's form; they do not.
  composer: [
    "#prompt-textarea[contenteditable='true']",
    "form div.ProseMirror[contenteditable='true']",
    "form [contenteditable='true'][role='textbox']",
    "#prompt-textarea",
    "form textarea[name='prompt-textarea']",
  ],
  stop: [
    "[data-testid='stop-button']",
    "form button[aria-label*='Stop' i]",
  ],
  send: [
    "[data-testid='send-button']",
    "#composer-submit-button",
    "form button[aria-label*='Send' i]",
    "form button[aria-label*='Submit' i]",
    "form button[type='submit']",
  ],
  // Buttons beside the message box that are never the send button, for when
  // none of the above matches and it has to be found by elimination.
  notSend: "add files|attach|upload|model|dictat|voice|microphone|stop|search|tools|more",
  // An assistant reply's rendered body. Role attributes come and go; the
  // `.markdown` container is ChatGPT's steadiest marker for an answer, and a
  // user message never has one. Read in order; the first that matches wins.
  answer: [
    "[data-message-author-role='assistant']",
    "[data-message-role='assistant']",
    "[data-testid='assistant-message']",
    ".markdown",
  ],
};

function state(sel) {
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const first = (list) => list.flatMap((s) => [...document.querySelectorAll(s)]).find(visible);
  const composer = first(sel.composer);
  // Messages already on the page: the baseline the panel measures a new reply
  // against. Prefer the rendered bodies; on builds without them, count the
  // message-copy buttons instead (one per message), never the code-block ones.
  let answers = 0;
  for (const s of sel.answer) {
    const nodes = [...document.querySelectorAll(s)].filter(visible);
    const outer = nodes.filter((el) => !nodes.some((o) => o !== el && o.contains(el)));
    if (outer.length) {
      answers = outer.length;
      break;
    }
  }
  if (!answers) {
    answers = [...document.querySelectorAll("button[aria-label]")].filter((b) =>
      /copy\s*(message|response|reply|answer)/i.test(b.getAttribute("aria-label") || "")
    ).length;
  }
  const thread = document.querySelector("main") || document.body;
  return {
    composer: !!composer,
    composerIsEditor: !!composer && composer.tagName !== "TEXTAREA",
    loginRequired: !composer && !!document.querySelector("[data-testid='login-button']"),
    streaming: !!first(sel.stop),
    answers,
    // Grows as a reply streams in; steady once it is done.
    textLength: (thread.innerText || "").length,
    path: location.pathname,
    canContinue: [...document.querySelectorAll("button")].some((b) =>
      /continue generating/i.test(b.innerText || "")
    ),
  };
}

// An insert, not typing: a persona prompt runs to hundreds of lines, and typing
// it made the editor rebuild itself on every one until the tab froze. An insert
// lands as one edit. From the isolated world the browser itself performs it:
// execCommand("insertText") fires trusted beforeinput/input events
// (isTrusted: true), so to ChatGPT it looks like ordinary text entry — no
// synthetic paste event, and no reaching into the page's own scripts. ChatGPT
// may turn a long entry into a "Pasted text" attachment, which it reads the
// same way.
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
  // earlier editable block — a reply's "Cover Letter" card is the same kind of
  // editor. Bind the caret to the end of the message box before inserting.
  const range = document.createRange();
  range.selectNodeContents(box);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  const ok = document.execCommand("insertText", false, text);
  return ok ? { ok: true } : { ok: false, reason: "the message box ignored the insert" };
}

// Finds the send button — the one the person showed us, else by name, else
// by elimination among the message box's buttons — and with `click`, presses
// it. ChatGPT keeps it disabled while a pasted-text attachment uploads.
function send(sel, learned, click) {
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const enabled = (b) => !b.disabled && b.getAttribute("aria-disabled") !== "true";
  const box = sel.composer.flatMap((s) => [...document.querySelectorAll(s)]).find(visible);
  const form = box ? box.closest("form") : null;
  const describe = (b) =>
    [b.getAttribute("aria-label"), b.getAttribute("data-testid"), b.id].filter(Boolean).join(" ");

  let button = null;
  let via = "";
  if (learned && (learned.aria || learned.testid)) {
    button = [...(form || document).querySelectorAll("button")].find(
      (b) =>
        visible(b) &&
        ((learned.aria && b.getAttribute("aria-label") === learned.aria) ||
          (learned.testid && b.getAttribute("data-testid") === learned.testid))
    );
    if (button) via = "learned";
  }
  if (!button) {
    button = sel.send.flatMap((s) => [...document.querySelectorAll(s)]).find(visible);
    if (button) via = "named";
  }
  if (!button && form) {
    const skip = new RegExp(sel.notSend, "i");
    const rest = [...form.querySelectorAll("button")].filter(
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
  const target = [...document.querySelectorAll("a, button")].find(
    (el) =>
      /^new chat$/i.test((el.innerText || "").trim()) ||
      /^new chat$/i.test(el.getAttribute("aria-label") || "")
  );
  if (!target) return false;
  target.click();
  return true;
}

function clickContinue(sel) {
  const button = [...document.querySelectorAll("button")].find((b) =>
    /continue generating/i.test(b.innerText || "")
  );
  if (button) button.click();
  return !!button;
}

// Reads one assistant reply straight from the rendered page, counted from the
// end: 0 is the newest. Built on the codex-sidepanel idea — read the rendered
// DOM from the isolated world, no clipboard, no Copy-button click — but shaped
// to how this ChatGPT build renders a reply: the resume comes back as a code
// block and the cover letter as an editable "Canvas" card, each its own widget.
//
// The key fact this leans on: the model's output lives in those widgets, while
// the prompt is plain user text and never a widget. So a message's reply is the
// code blocks and Canvas cards between its boundary and the previous one, read
// in order — which drops the prompt by construction. A code block reads as its
// code (the HTML or JSON the backend wants, without the ``` fence); a Canvas or
// anything else reads block by block. If a reply carries no widgets at all (a
// plain-text answer), it falls back to reading the message body before its
// boundary, bounded by the composer and the previous message.
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
  // Buttons, toolbars, icons and the screen-reader turn labels are never part of
  // the text; the composer is the one editor that is never a reply.
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
  // One widget's text. A code block (or a Canvas that holds only a code block)
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

  // Each assistant message carries one "Copy message" button; a user turn has
  // none. They mark where each message ends — position only, so visibility of
  // the button itself does not matter.
  const isMsgCopy = (b) => /copy\s*(message|response|reply|answer)/i.test(b.getAttribute("aria-label") || "");
  const ends = [...document.querySelectorAll("button[aria-label]")]
    .filter(isMsgCopy)
    .sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
  const idx = ends.length - 1 - fromEnd;
  if (idx < 0) return { text: null, count: ends.length };
  const end = ends[idx];
  const prevEnd = idx > 0 ? ends[idx - 1] : null;

  const follows = (ref, n) => ref.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING;
  const precedes = (ref, n) => end === n || ref.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_PRECEDING;

  // The reply's output widgets — code blocks and Canvas cards — between the
  // previous message's boundary and this one.
  let widgets = [
    ...document.querySelectorAll('pre, [contenteditable="true"], [contenteditable="plaintext-only"]'),
  ].filter(
    (w) => !isComposer(w) && visible(w) && (!prevEnd || follows(prevEnd, w)) && precedes(end, w)
  );
  widgets = widgets.filter((w) => !widgets.some((o) => o !== w && o.contains(w)));
  if (widgets.length) {
    const text = clean(widgets.map(widgetText).join("\n\n"));
    return { text: text || null, count: ends.length };
  }

  // No widgets: a plain-text reply. Read the message body before its boundary,
  // climbing to the largest ancestor that holds neither the composer nor the
  // previous message, then reading it up to the boundary button.
  let container = end;
  for (let node = end.parentElement; node && !node.matches("main, body, html"); node = node.parentElement) {
    if (isComposer(node) || node.querySelector(composerSel) || (prevEnd && node.contains(prevEnd))) break;
    container = node;
  }
  let ended = false;
  const readTo = (node) => {
    if (node === end || (node.contains && node.contains(end))) {
      ended = true;
      return "";
    }
    if (ended || node.nodeType === Node.COMMENT_NODE) return "";
    return read(node);
  };
  const text = clean([...container.childNodes].map(readTo).join(""));
  return { text: text || null, count: ends.length };
}

// A snapshot of how the page is built right now, for when a read fails: which
// of the answer selectors match, what the newest body holds, what the Copy
// buttons are called. It carries no personal text — only lengths, counts and a
// trimmed head/tail — and rides back in the failure so the log shows the shape
// of a DOM we cannot see from here.
function probe(sel) {
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const answerCounts = {};
  for (const s of sel.answer) answerCounts[s] = document.querySelectorAll(s).length;
  const copies = [...document.querySelectorAll("button[aria-label]")].filter((b) =>
    /^copy/i.test(b.getAttribute("aria-label") || "")
  );
  let body = null;
  let used = null;
  for (const s of sel.answer) {
    const nodes = [...document.querySelectorAll(s)].filter(visible);
    const outer = nodes.filter((el) => !nodes.some((o) => o !== el && o.contains(el)));
    if (outer.length) {
      body = outer[outer.length - 1];
      used = s;
      break;
    }
  }
  const isMsgCopy = (b) => /copy\s*(message|response|reply|answer)/i.test(b.getAttribute("aria-label") || "");
  const anchors = copies.filter(isMsgCopy);
  const txt = body ? body.innerText || "" : "";
  const tidy = (t) => t.replace(/\s+/g, " ").trim();
  // Does the DOM hold a complete HTML doc anywhere? Confirms the reply's text is
  // actually rendered (not windowed) even when the reader cannot bound it.
  const anyCloseHtml = [...document.querySelectorAll("pre")].some((p) => /<\/html>/i.test(p.innerText || ""));
  // The widgets the reader would collect for the newest message: output blocks
  // (code + Canvas) after the previous "Copy message" and before the last one.
  const composerSel = sel.composer.join(", ");
  const ends = anchors.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
  const end = ends[ends.length - 1];
  const prevEnd = ends.length > 1 ? ends[ends.length - 2] : null;
  let widgets = [];
  if (end) {
    widgets = [...document.querySelectorAll('pre, [contenteditable="true"], [contenteditable="plaintext-only"]')].filter(
      (w) =>
        !w.matches(composerSel) &&
        !w.closest(composerSel) &&
        visible(w) &&
        (!prevEnd || prevEnd.compareDocumentPosition(w) & Node.DOCUMENT_POSITION_FOLLOWING) &&
        end.compareDocumentPosition(w) & Node.DOCUMENT_POSITION_PRECEDING
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
    markdown: document.querySelectorAll(".markdown").length,
    article: document.querySelectorAll("article").length,
    roleAny: document.querySelectorAll("[data-message-author-role]").length,
    turnTestid: document.querySelectorAll("[data-testid^='conversation-turn']").length,
    preTotal: document.querySelectorAll("pre").length,
    editable: document.querySelectorAll("[contenteditable='true'], [contenteditable='plaintext-only']").length,
    msgCopies: anchors.length,
    copyVisible: copies.filter(visible).length,
    copyAll: copies.length,
    copyLabels: [...new Set(copies.map((b) => b.getAttribute("aria-label")))].slice(0, 6),
    semanticSelector: used,
    anyPreHasCloseHtml: anyCloseHtml,
    semanticBodyLen: txt.length,
    head: tidy(txt.slice(0, 160)),
    tail: tidy(txt.slice(-160)),
  };
}

// The steps the panel may ask for, by name.
export const PAGE = { state, paste, send, newChat, clickContinue, readAt, probe };
