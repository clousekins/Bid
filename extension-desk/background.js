// Two jobs: make the toolbar icon open the side panel, and hand drop.js the
// bytes of a file dropped from it.
//
// The panel is registered once in the manifest and never per tab, so Chrome
// keeps the same panel open while the user moves between tabs. A per-tab
// chrome.sidePanel.setOptions({tabId}) would close it on every switch.

import { api } from "./lib/api.js";
import { toBase64 } from "./lib/b64.js";

function openOnClick() {
  chrome.sidePanel
    .setPanelBehavior({ openPanelOnActionClick: true })
    .catch((err) => console.warn("[poong-desk] setPanelBehavior", err));
}

chrome.runtime.onInstalled.addListener(openOnClick);
chrome.runtime.onStartup.addListener(openOnClick);

// The bytes of one generated file, for drop.js. A content script cannot fetch
// the backend itself (the page's origin hits CORS), so it asks. The side panel
// answers first from the file it already holds; this is the fallback when the
// panel does not have it.
function fileName(resp, fallback) {
  const header = resp.headers.get("content-disposition") || "";
  const star = header.match(/filename\*=(?:UTF-8|utf-8)''([^;]+)/);
  if (star) return decodeURIComponent(star[1]);
  const plain = header.match(/filename="?([^";]+)"?/);
  return plain ? plain[1] : fallback;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "desk:file") return false;
  (async () => {
    const resp = await api.artifactResponse(msg.jobId, msg.kind);
    const type = (resp.headers.get("content-type") || msg.mime || "application/octet-stream").split(";")[0];
    const name = msg.name || fileName(resp, `${msg.kind}.${type.split("/")[1] || "bin"}`);
    sendResponse({ ok: true, name, type, b64: toBase64(await resp.arrayBuffer()) });
  })().catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }));
  return true;
});
