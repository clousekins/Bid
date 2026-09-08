// The privileged half of the extension.
//
// The panel lives in the page, and a content script may do neither of the two
// things this extension is for. Its fetches carry the page's origin, so a call
// to 127.0.0.1:8000 is a cross-origin request the backend's CORS decides on —
// host_permissions do not exempt it. And chrome.downloads simply is not there.
//
// So the panel asks, and this answers: every backend call and every file
// written goes through here.

import { createJob, findByUrl, ping } from "./lib/api.js";

// Clicking the icon toggles the panel. There is no popup any more, which is
// what makes this event fire at all.
chrome.action.onClicked.addListener(async (tab) => {
  if (!tab || !tab.id) return;
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  } catch (err) {
    // chrome://, the Web Store and a PDF viewer all refuse injection. There is
    // no panel to say so in, so the badge carries it.
    await chrome.action.setBadgeText({ tabId: tab.id, text: "—" });
    await chrome.action.setTitle({
      tabId: tab.id,
      title: `Poong Job Clipper cannot open here: ${err.message}`,
    });
  }
});

const HANDLERS = {
  "api:ping": () => ping(),
  "api:findByUrl": (payload) => findByUrl(payload),
  "api:createJob": (payload) => createJob(payload),
  "csv:download": (payload) => downloadCsv(payload),
  "csv:reveal": ({ id }) => chrome.downloads.show(id),
  "options:open": () => chrome.runtime.openOptionsPage(),
};

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  const handler = HANDLERS[message && message.type];
  if (!handler) return false;
  // Errors cross the message boundary as plain objects — an Error does not
  // survive structured cloning, and a panel told nothing would just hang.
  Promise.resolve(handler(message.payload || {}))
    .then((result) => respond({ ok: true, result }))
    .catch((err) => respond({ ok: false, error: err.message, status: err.status }));
  return true; // the reply is asynchronous
});

/**
 * Write the CSV and report where it landed.
 *
 * A data: URL rather than a blob: one — URL.createObjectURL does not exist in
 * an MV3 service worker, and a blob URL minted in the page's context is not
 * one this worker could read anyway.
 */
async function downloadCsv({ csv, filename }) {
  const url = `data:text/csv;charset=utf-8;base64,${base64(csv)}`;
  const id = await chrome.downloads.download({ url, filename, saveAs: false });
  return { id, path: await settledPath(id), bytes: new TextEncoder().encode(csv).length };
}

function base64(text) {
  const bytes = new TextEncoder().encode(text);
  // Chunked: String.fromCharCode(...bytes) blows the argument limit somewhere
  // around a hundred thousand jobs' worth of CSV.
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  }
  return btoa(binary);
}

/**
 * Where the file actually is.
 *
 * download() resolves with an id the moment the write starts, and the name is
 * only final once it finishes — Chrome appends " (1)" to avoid a collision, and
 * reporting the name we asked for would be reporting the wrong file.
 */
async function settledPath(id, attempts = 25) {
  for (let i = 0; i < attempts; i += 1) {
    const [item] = await chrome.downloads.search({ id });
    if (item && item.state === "complete" && item.filename) return item.filename;
    if (item && item.state === "interrupted") return "";
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  return "";
}
