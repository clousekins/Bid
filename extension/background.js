// The privileged half of the extension.
//
// The panel lives in the page, and a content script may do neither of the two
// things this extension is for. Its fetches carry the page's origin, so a call
// to 127.0.0.1:8000 is a cross-origin request the backend's CORS decides on —
// host_permissions do not exempt it. And chrome.downloads simply is not there.
//
// So the panel asks, and this answers: every backend call and every file
// written goes through here.

import { createJob, findByUrl, gpt, personas, ping } from "./lib/api.js";
import { PAGE as GPT_PAGE, SEL as GPT_SEL } from "./lib/gpt-page.js";
import { PAGE as CLAUDE_PAGE, SEL as CLAUDE_SEL } from "./lib/claude-page.js";
import { BOARD_API } from "./lib/greenhouse.js";

// The on-page steps, by engine. Each site's resume panel asks for steps under
// its own engine id (see engine-panel.js), and the step runs against that
// engine's selectors. The backend session calls (gpt:*) are the same for both.
const PAGE_ENGINES = {
  gpt: { PAGE: GPT_PAGE, SEL: GPT_SEL, name: "ChatGPT" },
  claude: { PAGE: CLAUDE_PAGE, SEL: CLAUDE_SEL, name: "Claude" },
};
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
  "api:personas": () => personas(),
  "csv:download": (payload) => downloadCsv(payload),
  "csv:reveal": ({ id }) => chrome.downloads.show(id),
  "options:open": () => chrome.runtime.openOptionsPage(),
  "greenhouse:board": ({ slug }) => greenhouseBoard(slug),
  // The resume panel: the backend's side of each job (shared by both engines),
  // and steps on the page (run under the engine the payload names).
  "gpt:jobs": (payload) => gpt.jobs(payload),
  "gpt:start": (payload) => gpt.start(payload),
  "gpt:reply": (payload) => gpt.reply(payload),
  "gpt:fail": (payload) => gpt.fail(payload),
  "gpt:next": (payload) => gpt.next(payload),
  "gpt:cancel": (payload) => gpt.cancel(payload),
  "engine:page": (payload, sender) => onPage(payload, sender),
};

// A frozen page never answers an injected script; without a limit the panel
// would wait on it forever.
const PAGE_STEP_LIMIT_MS = 20000;

/**
 * Run one of the engine's page steps (lib/gpt-page.js or lib/claude-page.js) in
 * the tab that asked, in the isolated extension world. The steps only read and
 * edit the rendered DOM — which a content-script world shares with the page — so
 * they never need the page's own JavaScript, and never touch anything it can see.
 */
async function onPage({ engine = "gpt", fn, args = [] }, sender) {
  const picked = PAGE_ENGINES[engine];
  if (!picked) throw new Error(`Unknown engine: ${engine}`);
  const func = picked.PAGE[fn];
  if (!func) throw new Error(`Unknown page step: ${fn}`);
  const tabId = sender && sender.tab && sender.tab.id;
  if (!tabId) throw new Error("No tab to run the step in.");
  let timer;
  const limit = new Promise((_, reject) => {
    timer = setTimeout(
      () =>
        reject(
          new Error(
            `${engine}_page_frozen: the ${picked.name} page stopped responding ` +
              `(Chrome may be showing "Page Unresponsive")`
          )
        ),
      PAGE_STEP_LIMIT_MS
    );
  });
  try {
    const [res] = await Promise.race([
      chrome.scripting.executeScript({
        target: { tabId },
        func,
        args: [picked.SEL, ...args],
        world: "ISOLATED",
      }),
      limit,
    ]);
    return res ? res.result : undefined;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Every opening on one Greenhouse board.
 *
 * Asked from here rather than from the panel because boards-api.greenhouse.io
 * sends no access-control-allow-origin — a page on job-boards.greenhouse.io
 * calling it is still cross-origin, and would be blocked. The worker has a
 * host permission for it instead.
 */
async function greenhouseBoard(slug) {
  const name = String(slug || "").trim();
  if (!/^[A-Za-z0-9_-]+$/.test(name)) {
    throw new Error("That does not look like a Greenhouse board name.");
  }
  const resp = await fetch(`${BOARD_API}/${encodeURIComponent(name)}/jobs`, {
    headers: { Accept: "application/json" },
  });
  if (resp.status === 404) {
    throw new Error(`Greenhouse has no board called "${name}".`);
  }
  if (!resp.ok) {
    throw new Error(`Greenhouse answered ${resp.status}.`);
  }
  return resp.json();
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const handler = HANDLERS[message && message.type];
  if (!handler) return false;
  // Errors cross the message boundary as plain objects — an Error does not
  // survive structured cloning, and a panel told nothing would just hang.
  Promise.resolve(handler(message.payload || {}, sender))
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
