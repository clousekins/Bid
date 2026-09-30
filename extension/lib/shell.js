// The frame both panels live in: a shadow root pinned to the page, draggable
// by its title bar, put back when the page rebuilds itself, closed by Esc.
//
// Everything lives in a shadow root. That is not tidiness — the panel opens on
// pages whose stylesheets set things like `div { position: static !important }`
// or a global `input { width: 100% }`, and without a shadow boundary the panel
// inherits all of it. The boundary also keeps our selectors off the page.
//
// The privileged work — reaching the backend, writing a file, reaching into
// the page's own world — is asked of the service worker. See background.js.

// The host element's own box is styled by the page, not by the shadow root: a
// shadow boundary contains what is inside it, and `:host` rules lose to the
// outer document at normal weight. A site with `div { position: static
// !important }` — and they exist — lays the panel out as a full-width block in
// the document flow. Inline `!important` is the one declaration nothing in the
// page outranks, so the handful of properties that decide where the panel sits
// are set here rather than in the stylesheet.
const PINNED = {
  position: "fixed",
  top: "16px",
  right: "16px",
  left: "auto",
  bottom: "auto",
  width: "400px",
  "max-width": "calc(100vw - 32px)",
  height: "auto",
  margin: "0",
  padding: "0",
  float: "none",
  transform: "none",
  "z-index": "2147483647",
  display: "block",
  visibility: "visible",
  opacity: "1",
  "pointer-events": "auto",
};

function pin(host, overrides = {}) {
  for (const [property, value] of Object.entries({ ...PINNED, ...overrides })) {
    host.style.setProperty(property, value, "important");
  }
}

/** Ask the service worker to do something a content script may not. */
export async function ask(type, payload = {}) {
  const reply = await chrome.runtime.sendMessage({ type, payload });
  if (!reply) throw new Error("The extension's background worker did not answer.");
  if (!reply.ok) {
    const error = new Error(reply.error || "Unknown failure");
    error.status = reply.status;
    throw error;
  }
  return reply.result;
}

/**
 * Put a panel on the page. `template` is its markup, whose title bar must have
 * class "bar" and a close button with id "btnClose". Returns the shadow root,
 * a getElementById shortcut, and close().
 */
export async function mountShell(hostId, template) {
  const host = document.createElement("div");
  host.id = hostId;
  pin(host);
  const root = host.attachShadow({ mode: "open" });

  // The stylesheet is fetched rather than inlined so it stays one file shared
  // with the options page.
  const css = await fetch(chrome.runtime.getURL("ui.css")).then((r) => r.text());
  const style = document.createElement("style");
  style.textContent = css;
  root.append(style);

  const wrap = document.createElement("div");
  wrap.innerHTML = template;
  root.append(wrap);
  document.documentElement.append(host);

  const $ = (id) => root.getElementById(id);

  // Pages bind their own keyboard shortcuts — j/k to move through results, "/"
  // to focus their search. Typing into the panel should not trigger them.
  for (const event of ["keydown", "keyup", "keypress"]) {
    wrap.addEventListener(event, (e) => e.stopPropagation());
  }

  // Some pages replace the whole document out from under the panel —
  // Greenhouse's board does it about a second after load, and a single-page
  // app can do it on any route change. So the panel puts itself back, for as
  // long as it is open.
  let closed = false;
  const reattach = () => {
    if (closed || host.isConnected) return;
    document.documentElement.append(host);
  };
  // The observer puts it back in the same task the page took it away, so there
  // is no frame where the panel is missing. The interval is the backstop for a
  // replacement the observer cannot see — it is watching a document that may
  // itself have been swapped.
  const watcher = new MutationObserver(reattach);
  watcher.observe(document, { childList: true, subtree: true });
  const keepAttached = setInterval(() => {
    if (closed) {
      clearInterval(keepAttached);
      watcher.disconnect();
      return;
    }
    reattach();
  }, 400);

  const onClose = [];
  const close = () => {
    closed = true;
    clearInterval(keepAttached);
    watcher.disconnect();
    host.remove();
    for (const fn of onClose) fn();
  };
  // content.js toggles the panel by removing the host. Without a way to say
  // "this one was deliberate", the watcher above would put it straight back.
  host.__poongClose = close;
  $("btnClose").addEventListener("click", close);
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });

  makeDraggable(host, root.querySelector(".bar"));
  return { root, $, host, close, onClose: (fn) => onClose.push(fn) };
}

// --- Dragging ---------------------------------------------------------------

function makeDraggable(host, handle) {
  let startX = 0;
  let startY = 0;
  let originLeft = 0;
  let originTop = 0;

  handle.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button, select, input")) return;
    const box = host.getBoundingClientRect();
    // Switch from the right-anchored default to explicit coordinates, once, so
    // the first drag does not jump. Still !important, for the same reason the
    // panel is pinned that way in the first place.
    pin(host, { left: `${box.left}px`, top: `${box.top}px`, right: "auto" });
    startX = event.clientX;
    startY = event.clientY;
    originLeft = box.left;
    originTop = box.top;
    handle.setPointerCapture(event.pointerId);

    const move = (e) => {
      const width = host.offsetWidth;
      // Kept on screen: a panel dragged off the edge cannot be dragged back.
      const left = clamp(originLeft + (e.clientX - startX), 8 - width + 60, window.innerWidth - 60);
      const top = clamp(originTop + (e.clientY - startY), 0, window.innerHeight - 40);
      host.style.setProperty("left", `${left}px`, "important");
      host.style.setProperty("top", `${top}px`, "important");
    };
    const drop = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", drop);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", drop);
  });
}

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);

// --- Small helpers both panels use ------------------------------------------

export function say(node, text, tone = "") {
  node.textContent = text;
  node.className = `message ${tone}`.trim();
  node.hidden = !text;
}

export async function whileBusy(button, label, work) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await work();
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}
