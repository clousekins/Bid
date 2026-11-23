// Runs in every page and frame. Receives a Resume / Cover chip dropped from
// the side panel.
//
// Chrome does not carry a File from an extension page into a web page: the
// side panel can put the file on its drag, but the page only receives the
// string parts. An upload box sees no file and refuses the drop. So this
// script takes over while a Poong chip is dragged: it allows the drop, catches
// it, gets the file's bytes from the extension's background, and hands the
// file to the upload box. It sets the nearby <input type=file> the way a real
// file pick would, or, where the box has no input, replays the drop with the
// file on it.
(() => {
  if (window.__poongDeskDrop) return;
  window.__poongDeskDrop = true;

  const TYPE = "application/x-poong-file";
  const ARTIFACT_RE = /\/api\/jobs\/(\d+)\/artifacts\/([a-z_]+)/;
  const ZONE_STYLE = "2px dashed #2f6bdd";

  let dragFromThisPage = false;
  let zone = null;
  let zoneOutline = "";

  addEventListener("dragstart", () => (dragFromThisPage = true), true);
  addEventListener("dragend", () => {
    dragFromThisPage = false;
    unmark();
  }, true);

  // While dragging only the types are readable, not the data. The custom type
  // is the reliable sign; a link dragged in from outside this page is the
  // fallback in case Chrome ever drops the custom type on the way.
  function looksOurs(event) {
    const types = Array.from(event.dataTransfer?.types || []);
    if (types.includes(TYPE)) return true;
    return !dragFromThisPage && types.includes("text/uri-list") && !types.includes("Files");
  }

  function readInfo(dt) {
    try {
      const raw = dt.getData(TYPE);
      if (raw) return JSON.parse(raw);
    } catch {
      /* fall through to the link */
    }
    const match = String(dt.getData("text/uri-list") || "").match(ARTIFACT_RE);
    return match ? { jobId: Number(match[1]), kind: match[2] } : null;
  }

  function fileInputs(root) {
    return Array.from(root.querySelectorAll?.('input[type="file"]') || []);
  }

  function visibleBox(el) {
    for (let node = el; node && node !== document.body; node = node.parentElement) {
      const rect = node.getBoundingClientRect();
      if (rect.width > 4 && rect.height > 4) return rect;
    }
    return null;
  }

  // The file input belonging to the box under the pointer: the closest
  // ancestor that holds one. Where an ancestor holds several (a form with a
  // resume box and a cover letter box), the one drawn nearest the pointer.
  function inputFor(target, x, y) {
    for (let node = target; node && node !== document.documentElement; node = node.parentElement) {
      if (node.matches?.('input[type="file"]')) return node;
      const found = fileInputs(node);
      if (found.length === 1) return found[0];
      if (found.length > 1) {
        let best = null;
        let bestDistance = Infinity;
        for (const input of found) {
          const rect = visibleBox(input);
          if (!rect) continue;
          const dx = Math.max(rect.left - x, 0, x - rect.right);
          const dy = Math.max(rect.top - y, 0, y - rect.bottom);
          const distance = Math.hypot(dx, dy);
          if (distance < bestDistance) {
            bestDistance = distance;
            best = input;
          }
        }
        return best || found[0];
      }
    }
    return null;
  }

  function zoneFor(target, x, y) {
    const input = inputFor(target, x, y);
    if (!input) return target;
    // Outline the box around the input rather than the hidden input itself.
    for (let node = input.parentElement; node; node = node.parentElement) {
      const rect = node.getBoundingClientRect();
      if (rect.width > 40 && rect.height > 20) return node;
    }
    return target;
  }

  function mark(el) {
    if (zone === el) return;
    unmark();
    zone = el;
    zoneOutline = el.style.outline;
    el.style.outline = ZONE_STYLE;
  }

  function unmark() {
    if (zone) zone.style.outline = zoneOutline;
    zone = null;
  }

  function onOver(event) {
    if (!looksOurs(event)) return;
    event.preventDefault();
    // Only a drag that is certainly a chip is kept from the page; an ordinary
    // link dragged in still reaches the page's own handlers.
    if (event.dataTransfer.types.includes(TYPE)) event.stopImmediatePropagation();
    event.dataTransfer.dropEffect = "copy";
    mark(zoneFor(event.target, event.clientX, event.clientY));
  }
  addEventListener("dragenter", onOver, true);
  addEventListener("dragover", onOver, true);
  addEventListener("dragleave", (event) => {
    if (!event.relatedTarget) unmark();
  }, true);

  addEventListener("drop", (event) => {
    // The replayed drop below is ours; let the page have it.
    if (!event.isTrusted || !looksOurs(event)) return;
    const info = readInfo(event.dataTransfer);
    if (!info) return unmark();
    event.preventDefault();
    event.stopImmediatePropagation();
    unmark();
    const { target, clientX, clientY } = event;
    requestFile(info)
      .then((reply) => {
        const bytes = Uint8Array.from(atob(reply.b64), (c) => c.charCodeAt(0));
        deliver(new File([bytes], reply.name, { type: reply.type }), target, clientX, clientY);
      })
      .catch((err) => toast(`Poong: could not get the file. ${err.message || err}`, true));
  }, true);

  // A sleeping background worker can lose the reply to the message that wakes
  // it. That is transient: a short wait and a resend reach the awake worker.
  const WAKEUP_RE =
    /message channel closed|could not establish connection|receiving end does not exist|message port closed/i;

  async function requestFile(info) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      let reply;
      try {
        // `type` is the message's; the file's own MIME type travels as `mime`.
        reply = await chrome.runtime.sendMessage({
          type: "desk:file",
          jobId: info.jobId,
          kind: info.kind,
          name: info.name,
          mime: info.type,
        });
      } catch (err) {
        if (attempt < 2 && WAKEUP_RE.test(String(err?.message || err))) {
          await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
          continue;
        }
        if (/context invalidated/i.test(String(err?.message || err))) {
          throw new Error("The extension was reloaded. Reload this page and drop again.");
        }
        throw err;
      }
      if (reply?.ok) return reply;
      if (reply) throw new Error(reply.error || "Unknown failure");
      await new Promise((resolve) => setTimeout(resolve, 250 * (attempt + 1)));
    }
    throw new Error("The extension did not answer. Reload this page and try again.");
  }

  function deliver(file, target, x, y) {
    const dt = new DataTransfer();
    dt.items.add(file);
    const input = inputFor(target, x, y);
    if (input) {
      input.files = dt.files;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    } else {
      for (const type of ["dragenter", "dragover", "drop"]) {
        target.dispatchEvent(
          new DragEvent(type, { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, dataTransfer: dt })
        );
      }
    }
    toast(`Poong: attached ${file.name}`);
  }

  function toast(text, bad = false) {
    const el = document.createElement("div");
    el.textContent = text;
    el.style.cssText = `position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:360px;padding:8px 12px;
      border-radius:8px;font:13px/1.4 system-ui,sans-serif;color:#fff;background:${bad ? "#b3261e" : "#1b7f4b"};
      box-shadow:0 6px 20px rgb(0 0 0 / 25%)`;
    document.documentElement.appendChild(el);
    setTimeout(() => el.remove(), bad ? 6000 : 2500);
  }
})();
