// The panel, as it sits on the page.
//
// Everything lives in a shadow root. That is not tidiness — the panel opens on
// pages whose stylesheets set things like `div { position: static !important }`
// or a global `input { width: 100% }`, and without a shadow boundary the panel
// inherits all of it. The boundary also keeps our selectors off the page.
//
// The privileged work — reaching the backend, writing a file — is asked of the
// service worker. See background.js for why a content script cannot do either.

import { addToBasket, clearBasket, markPushed, readBasket, unpushed } from "./basket.js";
import { csvFilename, toCsv } from "./csv.js";
import { extractJobFromPage } from "./extract.js";
import { parseJobsJson } from "./jobright.js";

const TEMPLATE = `
  <div class="panel" part="panel">
    <header class="bar">
      <span class="grip" title="Drag to move">⠿</span>
      <div class="titles">
        <strong>Poong Job Clipper</strong>
        <span id="status" class="muted">Checking the backend…</span>
      </div>
      <button type="button" id="btnClose" class="icon" title="Close (Esc)">✕</button>
    </header>

    <section class="card">
      <div class="card-head">
        <h2>This page</h2>
        <span class="muted" id="readAs"></span>
      </div>
      <div class="fields">
        <label>Company<input id="fCompany" type="text" autocomplete="off" /></label>
        <label>Title<input id="fTitle" type="text" autocomplete="off" /></label>
        <label>URL<input id="fUrl" type="url" autocomplete="off" /></label>
        <div class="row">
          <label>Location<input id="fLocation" type="text" autocomplete="off" /></label>
          <label>Source<input id="fSource" type="text" autocomplete="off" /></label>
        </div>
        <label>Description<textarea id="fDescription" rows="3" spellcheck="false"></textarea></label>
      </div>
      <div class="actions">
        <button type="button" id="btnRead">Read this page</button>
        <button type="button" id="btnSave" class="primary">Add to Poong</button>
      </div>
      <p id="clipMsg" class="message" hidden></p>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>Jobright JSON</h2>
        <span class="muted" id="basketCount">0 jobs collected</span>
      </div>
      <textarea id="jsonInput" rows="4" spellcheck="false"
        placeholder='Paste a /swan/recommend/search reply, then Add. Paste as many as you like — they accumulate.'></textarea>

      <label class="slider">
        <span>Keep matches scoring at least <b id="minScoreOut">1</b></span>
        <input id="minScore" type="range" min="1" max="100" step="1" value="1" />
      </label>

      <div class="actions">
        <button type="button" id="btnAddJson">Add to list</button>
        <button type="button" id="btnPush" class="primary">Push jobs</button>
      </div>
      <div class="actions">
        <button type="button" id="btnCsv">Download CSV</button>
        <button type="button" id="btnClear" class="ghost">Clear</button>
      </div>
      <p id="jsonMsg" class="message" hidden></p>
      <p id="downloadLine" class="download" hidden></p>
    </section>

    <footer class="foot"><a href="#" id="openOptions">Backend settings</a></footer>
  </div>
`;

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
async function ask(type, payload = {}) {
  const reply = await chrome.runtime.sendMessage({ type, payload });
  if (!reply) throw new Error("The extension's background worker did not answer.");
  if (!reply.ok) {
    const error = new Error(reply.error || "Unknown failure");
    error.status = reply.status;
    throw error;
  }
  return reply.result;
}

export async function mountPanel(hostId) {
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
  wrap.innerHTML = TEMPLATE;
  root.append(wrap);
  document.documentElement.append(host);

  const $ = (id) => root.getElementById(id);

  // Pages bind their own keyboard shortcuts — j/k to move through results, "/"
  // to focus their search. Typing a job title should not trigger them.
  for (const event of ["keydown", "keyup", "keypress"]) {
    wrap.addEventListener(event, (e) => e.stopPropagation());
  }

  const close = () => host.remove();
  $("btnClose").addEventListener("click", close);
  wrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });

  makeDraggable(host, root.querySelector(".bar"));
  wireClip(root, $, host);
  wireJson(root, $);

  refreshStatus($);
  refreshBasket($);
}

// --- Dragging ---------------------------------------------------------------

function makeDraggable(host, handle) {
  let startX = 0;
  let startY = 0;
  let originLeft = 0;
  let originTop = 0;

  handle.addEventListener("pointerdown", (event) => {
    if (event.target.closest("button")) return;
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

// --- Shared bits ------------------------------------------------------------

function say(node, text, tone = "") {
  node.textContent = text;
  node.className = `message ${tone}`.trim();
  node.hidden = !text;
}

async function whileBusy(button, label, work) {
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

async function refreshStatus($) {
  try {
    const { total, appliedToday } = await ask("api:ping");
    $("status").textContent = `${total} jobs on file · ${appliedToday} applied today`;
  } catch (err) {
    $("status").textContent = err.message;
  }
}

// --- The page in front of you -----------------------------------------------

function wireClip(root, $, host) {
  const FIELDS = {
    company: $("fCompany"),
    title: $("fTitle"),
    url: $("fUrl"),
    location: $("fLocation"),
    source: $("fSource"),
    description: $("fDescription"),
  };
  let readState = { external_id: "" };

  $("btnRead").addEventListener("click", async () => {
    say($("clipMsg"), "");
    await whileBusy($("btnRead"), "Reading…", async () => {
      // The panel is part of the page now, so the fallback reading — which is
      // just the visible text — would otherwise scrape the panel's own labels
      // into the description.
      host.style.setProperty("display", "none", "important");
      let found;
      try {
        found = extractJobFromPage();
      } catch (err) {
        say($("clipMsg"), `Could not read the page: ${err.message}`, "bad");
        return;
      } finally {
        host.style.setProperty("display", "block", "important");
      }
      for (const [key, node] of Object.entries(FIELDS)) node.value = found[key] || "";
      readState = { external_id: found.external_id || "" };
      $("readAs").textContent = found.readAs || "";
      say($("clipMsg"), "Read. Check the fields, then add.", "ok");
    });
  });

  $("btnSave").addEventListener("click", async () => {
    say($("clipMsg"), "");
    const job = {
      company: FIELDS.company.value.trim(),
      title: FIELDS.title.value.trim(),
      url: FIELDS.url.value.trim(),
      location: FIELDS.location.value.trim(),
      source: FIELDS.source.value.trim() || "clipper",
      description: FIELDS.description.value.trim(),
      external_id: readState.external_id,
    };
    if (!job.company || !job.title || !job.url) {
      say($("clipMsg"), "Company, title and URL are all required.", "warn");
      return;
    }
    await whileBusy($("btnSave"), "Saving…", async () => {
      try {
        // Asked before posting: the backend deduplicates by URL itself, but
        // answers a duplicate with the same 200 and job body as a new insert,
        // so posting blind could never report which of the two happened.
        const existing = await ask("api:findByUrl", { company: job.company, url: job.url });
        if (existing) {
          say(
            $("clipMsg"),
            `Already on file as #${existing.seq_no ?? existing.id} (${existing.status || "saved"}).`,
            "warn"
          );
          return;
        }
        const saved = await ask("api:createJob", job);
        say($("clipMsg"), `Added as #${saved.seq_no ?? saved.id}.`, "ok");
        await refreshStatus($);
      } catch (err) {
        if (err.status === 409) {
          say($("clipMsg"), `Rejected: ${err.message.replace(/_/g, " ")}.`, "warn");
          return;
        }
        say($("clipMsg"), err.message, "bad");
      }
    });
  });

  // openOptionsPage is not callable from a content script, so the worker opens
  // it — that way it reuses an already-open settings tab instead of stacking.
  $("openOptions").addEventListener("click", (event) => {
    event.preventDefault();
    ask("options:open").catch(() => {});
  });
}

// --- Pasted JSON ------------------------------------------------------------

const MIN_SCORE_KEY = "minScore";

async function refreshBasket($) {
  const rows = await readBasket();
  const waiting = unpushed(rows).length;
  const pushed = rows.length - waiting;
  $("basketCount").textContent =
    `${rows.length} collected` + (pushed ? `, ${pushed} pushed` : "");
  $("btnCsv").disabled = rows.length === 0;
  $("btnClear").disabled = rows.length === 0;
  $("btnPush").disabled = waiting === 0;
  return rows;
}

function wireJson(root, $) {
  // The slider, and its remembered position.
  const out = $("minScoreOut");
  const slider = $("minScore");
  chrome.storage.local.get({ [MIN_SCORE_KEY]: 1 }).then((stored) => {
    slider.value = String(stored[MIN_SCORE_KEY] || 1);
    out.textContent = slider.value;
  });
  slider.addEventListener("input", () => {
    out.textContent = slider.value;
  });
  slider.addEventListener("change", () =>
    chrome.storage.local.set({ [MIN_SCORE_KEY]: Number(slider.value) })
  );

  $("btnAddJson").addEventListener("click", async () => {
    say($("jsonMsg"), "");
    await whileBusy($("btnAddJson"), "Adding…", async () => {
      let parsed;
      try {
        parsed = parseJobsJson($("jsonInput").value);
      } catch (err) {
        say($("jsonMsg"), err.message, "bad");
        return;
      }
      if (!parsed.rows.length) {
        say($("jsonMsg"), `No jobs in that payload (${parsed.found} entries seen).`, "warn");
        return;
      }

      // The score gate. A job with no score at all is kept and counted
      // separately — silently dropping it would be judging it by a number it
      // never had.
      const floor = Number(slider.value);
      const keep = [];
      let belowScore = 0;
      let unscored = 0;
      for (const row of parsed.rows) {
        if (row.score === null || row.score === undefined) {
          unscored += 1;
          keep.push(row);
        } else if (row.score < floor) {
          belowScore += 1;
        } else {
          keep.push(row);
        }
      }

      if (!keep.length) {
        say($("jsonMsg"), `All ${belowScore} scored below ${floor}.`, "warn");
        return;
      }

      const { added, duplicates, total } = await addToBasket(keep);
      const notes = [`${added} added`];
      if (belowScore) notes.push(`${belowScore} below ${floor}`);
      if (unscored) notes.push(`${unscored} unscored, kept`);
      if (duplicates) notes.push(`${duplicates} already collected`);
      if (parsed.unusable) notes.push(`${parsed.unusable} unusable`);
      say($("jsonMsg"), `${notes.join(", ")} — ${total} in total.`, added ? "ok" : "warn");
      $("jsonInput").value = "";
      await refreshBasket($);
    });
  });

  $("btnPush").addEventListener("click", async () => {
    say($("jsonMsg"), "");
    await whileBusy($("btnPush"), "Pushing…", async () => {
      const waiting = unpushed(await readBasket());
      if (!waiting.length) {
        say($("jsonMsg"), "Every collected job has been pushed already.", "warn");
        return;
      }

      // Read the high-water mark first. A job the backend already had comes
      // back with its original, lower sequence number, which is how "added"
      // and "already on file" are told apart without a second request each.
      let baseline = 0;
      try {
        baseline = (await ask("api:ping")).maxSeq;
      } catch (err) {
        say($("jsonMsg"), err.message, "bad");
        return;
      }

      const outcomes = {};
      let added = 0;
      let already = 0;
      const refused = [];

      for (const [index, row] of waiting.entries()) {
        try {
          const saved = await ask("api:createJob", {
            company: row.company,
            title: row.title,
            url: row.url,
            location: row.location,
            source: row.source || "Jobright",
            description: row.description,
            external_id: row.external_id,
          });
          const seq = Number(saved.seq_no ?? saved.id ?? 0);
          outcomes[row.external_id || row.url] = seq || -1;
          if (seq > baseline) added += 1;
          else already += 1;
        } catch (err) {
          // A gate turned it away — an employer already applied to, a blocked
          // company. Recorded so it is not offered again, and named so the
          // count is not a silent loss.
          outcomes[row.external_id || row.url] = -1;
          refused.push(`${row.company}: ${String(err.message).replace(/_/g, " ")}`);
        }
        if (index % 5 === 0 || index === waiting.length - 1) {
          say($("jsonMsg"), `Pushing ${index + 1}/${waiting.length} — ${added} new…`);
        }
      }

      await markPushed(outcomes);
      await refreshStatus($);

      const notes = [`${added} added`];
      if (already) notes.push(`${already} already on file`);
      if (refused.length) notes.push(`${refused.length} refused`);
      say(
        $("jsonMsg"),
        `${notes.join(", ")}.` + (refused.length ? ` First: ${refused[0]}` : ""),
        added ? "ok" : "warn"
      );
    });
    // After whileBusy, not inside it: its finally re-enables the button it was
    // given, which would undo the disabling this does when nothing is left to
    // push.
    await refreshBasket($);
  });

  $("btnCsv").addEventListener("click", async () => {
    say($("jsonMsg"), "");
    $("downloadLine").hidden = true;
    await whileBusy($("btnCsv"), "Writing…", async () => {
      const rows = await readBasket();
      if (!rows.length) {
        say($("jsonMsg"), "Nothing collected yet.", "warn");
        return;
      }
      try {
        const { id, path, bytes } = await ask("csv:download", {
          csv: toCsv(rows),
          filename: csvFilename(),
        });
        showDownload($, path, id);
        say($("jsonMsg"), `Wrote ${rows.length} rows (${Math.round(bytes / 1024)} KB).`, "ok");
      } catch (err) {
        say($("jsonMsg"), `Download failed: ${err.message}`, "bad");
      }
    });
  });

  $("btnClear").addEventListener("click", async () => {
    await clearBasket();
    $("downloadLine").hidden = true;
    say($("jsonMsg"), "Collected jobs cleared.", "ok");
    await refreshBasket($);
  });
}

function showDownload($, path, id) {
  const line = $("downloadLine");
  line.textContent = "";
  const saved = document.createElement("div");
  saved.textContent = path || "(saved — the browser did not report a path)";
  const reveal = document.createElement("button");
  reveal.type = "button";
  reveal.textContent = "Show in folder";
  reveal.addEventListener("click", () => ask("csv:reveal", { id }).catch(() => {}));
  line.append(saved, reveal);
  line.hidden = false;
}
