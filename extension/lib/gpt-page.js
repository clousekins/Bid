// What the ChatGPT panel does to the chatgpt.com page: read its state, paste a
// prompt, press send, open a new chat, copy an answer.
//
// Each function runs in the page's own world (the service worker injects it
// there; see background.js), because that is where ChatGPT's copy button calls
// the clipboard. Each is serialised on the way in, so it must not reach for
// anything outside its own body — the selectors arrive as the first argument.
//
// chatgpt.com renames and strips its markup without notice. As of this
// writing it has dropped almost every data-testid and every message-role
// attribute. What holds: the message box is a ProseMirror editor, the button
// that stops a reply is labelled "Stop", and every message has a button
// labelled "Copy …" whose clipboard text is the message's Markdown.

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
};

function state(sel) {
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const first = (list) => list.flatMap((s) => [...document.querySelectorAll(s)]).find(visible);
  const composer = first(sel.composer);
  const copies = [...document.querySelectorAll("button[aria-label]")].filter(
    (b) => /^copy/i.test(b.getAttribute("aria-label")) && visible(b)
  );
  const thread = document.querySelector("main") || document.body;
  return {
    composer: !!composer,
    composerIsEditor: !!composer && composer.tagName !== "TEXTAREA",
    loginRequired: !composer && !!document.querySelector("[data-testid='login-button']"),
    streaming: !!first(sel.stop),
    copies: copies.length,
    // Grows as a reply streams in; steady once it is done.
    textLength: (thread.innerText || "").length,
    path: location.pathname,
    canContinue: [...document.querySelectorAll("button")].some((b) =>
      /continue generating/i.test(b.innerText || "")
    ),
  };
}

// A paste, not typing: a persona prompt runs to hundreds of lines, and typing
// it made the editor rebuild itself on every one until the tab froze. A paste
// lands as one edit. ChatGPT may turn a long paste into a "Pasted text"
// attachment, which it reads the same way.
function paste(sel, text) {
  const box = sel.composer
    .flatMap((s) => [...document.querySelectorAll(s)])
    .find((el) => el.getClientRects().length > 0);
  if (!box) return { ok: false, reason: "no message box" };
  box.focus();
  if (box.tagName === "TEXTAREA") {
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
    setter.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
    return { ok: true };
  }
  const range = document.createRange();
  range.selectNodeContents(box);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  const data = new DataTransfer();
  data.setData("text/plain", text);
  const event = new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true });
  box.dispatchEvent(event);
  // The editor takes a paste by cancelling the browser's own handling.
  return event.defaultPrevented
    ? { ok: true }
    : { ok: false, reason: "the message box ignored the paste" };
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

// Copies one message through its own "Copy" button, counted from the end of
// the page: 0 is the newest. The clipboard is intercepted for that one click,
// so the person's own clipboard is never touched, and what comes back is the
// message's Markdown — code fences and all, which is what the backend parses.
async function copyAt(sel, fromEnd) {
  const buttons = [...document.querySelectorAll("button[aria-label]")].filter(
    (b) => /^copy/i.test(b.getAttribute("aria-label")) && b.getClientRects().length > 0
  );
  const button = buttons[buttons.length - 1 - fromEnd];
  if (!button) return { text: null, count: buttons.length };

  let captured = null;
  const clip = navigator.clipboard;
  if (clip) {
    clip.writeText = async (value) => {
      captured = String(value);
    };
    clip.write = async (items) => {
      for (const item of items) {
        if (item.types.includes("text/plain")) {
          captured = await (await item.getType("text/plain")).text();
          return;
        }
      }
    };
  }
  try {
    button.click();
    for (let i = 0; i < 30 && captured === null; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  } finally {
    if (clip) {
      delete clip.writeText;
      delete clip.write;
    }
  }
  return { text: captured, label: button.getAttribute("aria-label"), count: buttons.length };
}

// The steps the panel may ask for, by name.
export const PAGE = { state, paste, send, newChat, clickContinue, copyAt };
