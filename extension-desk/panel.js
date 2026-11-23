// Poong Job Desk: the side panel.
//
// Top: a small job list with search. Each row with a generated resume carries
// two file chips, Resume and Cover, that can be dragged out like real files.
// Below: the selected job's detail with the same actions as Poong's job
// detail, plus JD + Resume and the interview prompt.

import { api, artifactHref } from "./lib/api.js";
import { loadSettings, saveSettings } from "./lib/settings.js";
import { INTERVIEW_PROMPT } from "./lib/prompts.js";
import { toBase64 } from "./lib/b64.js";

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const state = {
  personaId: "",
  personas: [],
  jobs: [],
  total: 0,
  page: 1,
  pages: 1,
  query: "",
  selectedId: null,
  job: null,
  editing: false,
  busy: "",
  output: null, // { title, text }
  listToken: 0,
};

// ---------------------------------------------------------------- status line

let statusTimer = null;
function say(text, tone = "") {
  const el = $("status");
  el.textContent = text || "";
  el.className = `status ${tone}`;
  clearTimeout(statusTimer);
  if (text && tone !== "bad") statusTimer = setTimeout(() => say(""), 6000);
}
const fail = (err) => say(String(err?.message || err), "bad");

// ---------------------------------------------------------------- files
//
// A drag has to be set up synchronously inside dragstart, so the file must
// already be in memory when the mouse goes down. Rows prefetch on hover and
// the selected job prefetches when it opens.
//
// Two things go on the drag:
//  - the File itself, which an upload box on a web page receives as a drop;
//  - a DownloadURL, which Explorer or the desktop saves as a file.

const PICK = {
  resume: ["resume_pdf", "resume_html"],
  cover: ["cover_pdf", "cover_txt"],
};
const files = new Map(); // `${jobId}:${which}` -> { kind, name, type, href, file } | { missing: true }
const loading = new Map(); // jobId -> Promise

function fileKey(jobId, which) {
  return `${jobId}:${which}`;
}

function loadFiles(jobId, { force = false } = {}) {
  if (!force && loading.has(jobId)) return loading.get(jobId);
  const job = state.jobs.find((j) => j.id === jobId) || (state.job?.id === jobId ? state.job : null);
  const run = (async () => {
    const listing = await api.artifacts(jobId, job?.persona_id);
    const available = listing?.files || {};
    await Promise.all(
      Object.entries(PICK).map(async ([which, kinds]) => {
        const kind = kinds.find((k) => available[k]);
        if (!kind) {
          files.set(fileKey(jobId, which), { missing: true });
          return;
        }
        const meta = available[kind];
        const resp = await api.artifactResponse(jobId, kind);
        const blob = await resp.blob();
        const type = blob.type || resp.headers.get("content-type") || "application/octet-stream";
        files.set(fileKey(jobId, which), {
          kind,
          name: meta.name,
          type: type.split(";")[0],
          href: await artifactHref(jobId, kind, meta.mtime),
          file: new File([blob], meta.name, { type: type.split(";")[0], lastModified: Math.round((meta.mtime || 0) * 1000) || Date.now() }),
        });
      })
    );
    paintChips(jobId);
  })();
  loading.set(jobId, run);
  run.catch(() => loading.delete(jobId));
  return run;
}

function forgetFiles(jobId) {
  loading.delete(jobId);
  files.delete(fileKey(jobId, "resume"));
  files.delete(fileKey(jobId, "cover"));
}

function chip(jobId, which, big = false) {
  const entry = files.get(fileKey(jobId, which));
  const label = which === "resume" ? "Resume" : "Cover";
  const missing = entry?.missing;
  const title = missing
    ? `No ${label.toLowerCase()} file for this job`
    : entry
      ? `Drag ${entry.name} onto an upload box or the desktop. Click to open.`
      : "Drag onto an upload box or the desktop. Click to open.";
  return `<span class="file${big ? " big" : ""}${missing ? " missing" : ""}" draggable="${missing ? "false" : "true"}"
    data-job="${jobId}" data-which="${which}" title="${esc(title)}">&#x1F4C4; ${label}</span>`;
}

function paintChips(jobId) {
  for (const el of document.querySelectorAll(`.file[data-job="${jobId}"]`)) {
    const big = el.classList.contains("big");
    el.outerHTML = chip(jobId, el.dataset.which, big);
  }
}

document.addEventListener("dragstart", (event) => {
  const el = event.target.closest?.(".file");
  if (!el) return;
  const jobId = Number(el.dataset.job);
  const entry = files.get(fileKey(jobId, el.dataset.which));
  if (!entry || entry.missing) {
    event.preventDefault();
    if (!entry) {
      say("Loading the file, try the drag again in a moment.");
      loadFiles(jobId).catch(fail);
    }
    return;
  }
  const dt = event.dataTransfer;
  dt.effectAllowed = "copy";
  try {
    dt.items.add(entry.file);
  } catch (err) {
    console.warn("[poong-desk] could not attach the file to the drag", err);
  }
  // What drop.js in the page reads to fetch the file again on its side.
  dt.setData(
    "application/x-poong-file",
    JSON.stringify({ jobId, kind: entry.kind, name: entry.name, type: entry.type })
  );
  dt.setData("DownloadURL", `${entry.type}:${entry.name}:${entry.href}`);
  dt.setData("text/uri-list", entry.href);
});

// drop.js asks for the bytes of the file it just received a drop for. The
// panel holds that file already, so it answers here and the background worker
// never has to wake: Chrome puts it to sleep when idle, and the first message
// that wakes it can lose its reply ("message port closed"). When the panel
// does not hold the file it stays silent and the worker answers instead.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type !== "desk:file") return false;
  const entry = ["resume", "cover"]
    .map((which) => files.get(fileKey(msg.jobId, which)))
    .find((candidate) => candidate && !candidate.missing && candidate.kind === msg.kind);
  if (!entry) return false;
  entry.file
    .arrayBuffer()
    .then((buffer) => sendResponse({ ok: true, name: entry.name, type: entry.type, b64: toBase64(buffer) }))
    .catch((err) => sendResponse({ ok: false, error: String(err?.message || err) }));
  return true;
});

document.addEventListener(
  "mouseover",
  (event) => {
    const row = event.target.closest?.("li[data-id]");
    if (!row || row.dataset.resume !== "1") return;
    const jobId = Number(row.dataset.id);
    if (!loading.has(jobId)) loadFiles(jobId).catch(() => {});
  },
  { passive: true }
);

async function openFile(jobId, which) {
  try {
    await loadFiles(jobId);
    const entry = files.get(fileKey(jobId, which));
    if (!entry || entry.missing) return say(`No ${which === "resume" ? "resume" : "cover letter"} file for this job.`, "bad");
    window.open(entry.href, "_blank");
  } catch (err) {
    fail(err);
  }
}

// ---------------------------------------------------------------- list

function statusOf(job) {
  return job.my_status || job.status || "";
}

function pillTone(status) {
  if (["applied", "interview", "resume_ready"].includes(status)) return "ok";
  if (["applying", "jd_ready", "needs_manual"].includes(status)) return "warn";
  if (["rejected", "rejected_onsite", "blocked", "scan_failed", "tailor_failed", "expired"].includes(status)) return "bad";
  return "";
}

function rowHtml(job) {
  const status = statusOf(job);
  const off = job.disabled || ["rejected", "blocked", "skipped"].includes(status);
  return `<li data-id="${job.id}" data-resume="${job.has_resume ? 1 : 0}" class="${job.id === state.selectedId ? "sel" : ""}${off ? " off" : ""}">
    <span class="seq">#${esc(job.seq_no)}</span>
    <span class="what" title="${esc(`${job.company} | ${job.title}`)}"><b>${esc(job.company)}</b> ${esc(job.title)}</span>
    <span class="pill ${pillTone(status)}">${esc(status.replace(/_/g, " "))}</span>
    ${job.has_resume ? chip(job.id, "resume") + chip(job.id, "cover") : ""}
  </li>`;
}

function renderList() {
  const ul = $("jobs");
  if (!state.jobs.length) {
    ul.innerHTML = `<li class="note">${state.query ? "No job matches." : "No jobs."}</li>`;
  } else {
    ul.innerHTML = state.jobs.map(rowHtml).join("");
  }
  const first = (state.page - 1) * PAGE_SIZE + 1;
  $("count").textContent = state.total ? `${first}-${first + state.jobs.length - 1} of ${state.total}` : "";
  renderPager();
}

function renderPager() {
  $("pager").hidden = state.pages <= 1;
  $("pageInput").value = state.page;
  $("pageInput").max = state.pages;
  $("pageCount").textContent = state.pages;
  $("btnFirst").disabled = $("btnPrev").disabled = state.page <= 1;
  $("btnNext").disabled = $("btnLast").disabled = state.page >= state.pages;
}

function parseQuery(text) {
  const trimmed = text.trim();
  const num = trimmed.match(/^#?\s*(\d+)$/);
  return num ? { seq: Number(num[1]) } : { q: trimmed };
}

const PAGE_SIZE = 50;

async function loadJobs({ page = 1 } = {}) {
  const token = ++state.listToken;
  try {
    let result = await api.listJobs({ personaId: state.personaId, page, pageSize: PAGE_SIZE, ...parseQuery(state.query) });
    // A delete can leave the last page empty; fall back to the new last page.
    if (!(result.jobs || []).length && page > 1 && (result.pages || 1) < page) {
      page = result.pages || 1;
      result = await api.listJobs({ personaId: state.personaId, page, pageSize: PAGE_SIZE, ...parseQuery(state.query) });
    }
    if (token !== state.listToken) return;
    state.jobs = result.jobs || [];
    state.total = result.total || 0;
    state.page = result.page || page;
    state.pages = Math.max(1, result.pages || 1);
    renderList();
    $("jobs").scrollTop = 0;
    setDot(true);
  } catch (err) {
    if (token !== state.listToken) return;
    setDot(false);
    fail(err);
  }
}

function replaceJob(job) {
  if (!job || !job.id) return;
  const index = state.jobs.findIndex((j) => j.id === job.id);
  if (index >= 0) state.jobs[index] = { ...state.jobs[index], ...job };
  if (state.job?.id === job.id) state.job = { ...state.job, ...job };
  renderList();
  renderDetail();
}

let searchTimer = null;
$("search").addEventListener("input", (event) => {
  state.query = event.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadJobs(), 250);
});
$("search").addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  clearTimeout(searchTimer);
  loadJobs().then(() => {
    if (state.jobs.length === 1) selectJob(state.jobs[0].id);
  });
});

function goToPage(page) {
  const target = Math.min(Math.max(1, Math.round(Number(page) || 1)), state.pages);
  if (target === state.page) return renderPager();
  loadJobs({ page: target });
}
$("btnFirst").addEventListener("click", () => goToPage(1));
$("btnPrev").addEventListener("click", () => goToPage(state.page - 1));
$("btnNext").addEventListener("click", () => goToPage(state.page + 1));
$("btnLast").addEventListener("click", () => goToPage(state.pages));
$("pageInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") goToPage(event.target.value);
});
$("pageInput").addEventListener("change", (event) => goToPage(event.target.value));

$("btnRefresh").addEventListener("click", () => {
  loadJobs({ page: state.page });
  if (state.selectedId) selectJob(state.selectedId, { refresh: true });
});

$("jobs").addEventListener("click", (event) => {
  const fileEl = event.target.closest(".file");
  if (fileEl) {
    event.stopPropagation();
    if (!fileEl.classList.contains("missing")) openFile(Number(fileEl.dataset.job), fileEl.dataset.which);
    return;
  }
  const row = event.target.closest("li[data-id]");
  if (row) selectJob(Number(row.dataset.id));
});

// ---------------------------------------------------------------- detail

async function selectJob(id, { refresh = false } = {}) {
  if (state.selectedId !== id) {
    state.output = null;
    state.editing = false;
  }
  state.selectedId = id;
  state.job = state.jobs.find((j) => j.id === id) || state.job;
  renderList();
  renderDetail();
  try {
    const job = await api.getJob(id);
    if (state.selectedId !== id) return;
    state.job = job;
    renderDetail();
    if (job.has_resume) {
      if (refresh) forgetFiles(id);
      loadFiles(id).catch(fail);
    }
  } catch (err) {
    fail(err);
  }
}

function pkgOf(job) {
  return (job.packages || []).find((p) => p.persona_id === job.persona_id) || (job.packages || [])[0] || null;
}

const FIELDS = [
  ["company", "Company"],
  ["title", "Title"],
  ["location", "Location"],
  ["salary", "Salary"],
  ["url", "URL"],
  ["apply_url", "Apply URL"],
];

const OUTCOMES = ["pending", "acknowledged", "screen", "interview", "offer", "rejected", "ghosted"];

function fieldsHtml(job) {
  return FIELDS.map(([key, label]) => {
    const value = job[key] || "";
    let shown;
    if (state.editing) shown = `<input data-field="${key}" value="${esc(value)}" />`;
    else if (/url$/.test(key) && value) shown = `<a href="${esc(value)}" target="_blank" rel="noreferrer">${esc(value)}</a>`;
    else shown = value ? esc(value) : `<span class="muted">-</span>`;
    return `<dt>${label}</dt><dd>${shown}</dd>`;
  }).join("");
}

function btn(action, label, { cls = "", disabled = false, title = "" } = {}) {
  const busy = state.busy === action;
  return `<button data-act="${action}" class="${cls}" ${disabled || state.busy ? "disabled" : ""} ${title ? `title="${esc(title)}"` : ""}>${busy ? "Working..." : label}</button>`;
}

function renderDetail() {
  const el = $("detail");
  const job = state.job;
  if (!job) {
    el.innerHTML = `<p class="empty">Pick a job above.</p>`;
    return;
  }
  const status = statusOf(job);
  const pkg = pkgOf(job);
  const hasJd = (job.description || "").trim().length >= 50;
  const persona = state.personas.find((p) => p.id === job.persona_id);
  const personaName = persona ? persona.label.split(" ")[0] : job.persona_id;

  const pills = [
    `<span class="pill ${pillTone(status)}">${esc(status.replace(/_/g, " "))}</span>`,
    job.disabled ? `<span class="pill bad" title="${esc(job.disabled_reason || "")}">disabled</span>` : "",
    job.federal ? `<span class="pill bad">federal</span>` : "",
    job.ats_score != null ? `<span class="pill">ATS ${esc(job.ats_score)}</span>` : "",
    job.country_name ? `<span class="pill">${esc(job.country_name)}</span>` : "",
  ].join("");

  const outcome =
    status === "applied" && pkg
      ? `<div class="group"><div class="label">Outcome</div>
          <select id="outcome">${OUTCOMES.map((o) => `<option ${o === (pkg.outcome || "pending") ? "selected" : ""}>${o}</option>`).join("")}</select></div>`
      : "";

  const output = state.output
    ? `<div class="out"><div class="head"><b>${esc(state.output.title)}</b>
        <span class="muted">${state.output.text.length} chars</span>
        <button data-act="copyOut">Copy</button><button data-act="closeOut" class="icon" title="Close">&times;</button></div>
        <textarea readonly id="outText">${esc(state.output.text)}</textarea></div>`
    : "";

  el.innerHTML = `
    <h2>#${esc(job.seq_no)} ${esc(job.company)}</h2>
    <div class="sub">${esc(job.title)}</div>
    <div class="pills">${pills}</div>

    <div class="group"><div class="label">Files</div>
      <div class="buttons">
        ${job.has_resume ? chip(job.id, "resume", true) + chip(job.id, "cover", true) : `<span class="muted">No resume yet.</span>`}
      </div>
    </div>

    <div class="group"><div class="label">Copy for AI</div>
      <div class="buttons">
        ${btn("jdResume", "JD + Resume", { cls: "primary", disabled: !hasJd || !job.has_resume, title: "Needs a JD and a generated resume" })}
        ${btn("interview", "Interview prompt", { cls: "primary", disabled: !hasJd || !job.has_resume, title: "Needs a JD and a generated resume" })}
        ${btn("copyJd", "Copy JD", { disabled: !hasJd })}
      </div>
    </div>
    ${output}

    <div class="group"><div class="label">Job</div>
      <dl class="fields">${fieldsHtml(job)}</dl>
      <div class="buttons" style="margin-top:4px">
        ${state.editing ? btn("saveFields", "Save", { cls: "primary" }) + btn("cancelEdit", "Cancel") : btn("edit", "Edit fields")}
        ${btn("openJd", "Open JD", { disabled: !job.url })}
        ${btn("openApply", "Open apply URL", { disabled: !job.apply_url })}
      </div>
    </div>
    ${outcome}

    <div class="group"><div class="label">Pipeline</div>
      <div class="buttons">
        ${btn("generate", `Generate for ${esc(personaName)}`, { disabled: status === "rejected" || !hasJd })}
        ${["new", "scan_failed"].includes(job.status) || !hasJd ? btn("fetchJd", "Fetch JD", { disabled: !job.url }) : ""}
        ${btn("applied", "Mark applied")}
        ${btn("skip", "Skip")}
        ${status === "interview" ? btn("unInterview", "Un-interview") : btn("interviewStatus", "Make interview")}
        ${status === "rejected" ? btn("unReject", "Un-reject") : btn("reject", "Make rejected")}
        ${job.federal ? btn("unfederal", "Un-federal") : btn("federal", "Make federal")}
        ${job.disabled ? btn("enable", "Enable") : btn("disable", "Disable")}
        ${btn("openFolder", "Open folder", { disabled: !job.folder_path && !pkg?.folder_path })}
        ${btn("block", "Block company", { cls: "danger" })}
        ${btn("delete", "Delete job", { cls: "danger" })}
      </div>
    </div>

    ${hasJd ? `<details class="jd"><summary>Job description</summary><pre>${esc(job.description)}</pre></details>` : ""}
  `;
}

// Resume text for the prompts: render the resume HTML off-screen and take the
// visible text, the same way Poong's Continue-apply window does.
async function resumeText(job) {
  const resp = await api.artifactResponse(job.id, "resume_html");
  const html = await resp.text();
  const clean = html
    .replace(/<!DOCTYPE[^>]*>/i, "")
    .replace(/<head[\s\S]*?<\/head>/i, "")
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, "");
  const div = document.createElement("div");
  div.style.cssText = "position:absolute;left:-99999px;top:0;width:800px;white-space:pre-wrap;";
  div.innerHTML = clean;
  document.body.appendChild(div);
  const text = (div.innerText || div.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
  div.remove();
  if (!text) throw new Error("The resume text is empty.");
  return text;
}

async function showAndCopy(title, text) {
  state.output = { title, text };
  renderDetail();
  try {
    await navigator.clipboard.writeText(text);
    say(`${title} copied (${text.length} chars).`, "ok");
  } catch {
    say(`${title} is ready. Press Copy.`);
  }
}

// The job routes answer with the updated job; anything else, re-read it.
const reload = (job) => (job && "seq_no" in job ? replaceJob(job) : selectJob(state.selectedId, { refresh: true }));
const unStatus = (job) => ((job.description || "").trim().length >= 50 ? "jd_ready" : "new");

const ACTIONS = {
  async jdResume(job) {
    const rule = "=".repeat(40);
    const text = `JOB DESCRIPTION\n${rule}\n${job.description.trim()}\n\n\nRESUME\n${rule}\n${await resumeText(job)}`;
    await showAndCopy("JD + Resume", text);
  },
  async interview(job) {
    const resume = await resumeText(job);
    const text = INTERVIEW_PROMPT.replace("{JD field}", () => job.description.trim()).replace("{Resume field}", () => resume);
    await showAndCopy("Interview prompt", text);
  },
  async copyJd(job) {
    await navigator.clipboard.writeText(job.description.trim());
    say("JD copied.", "ok");
  },
  async copyOut() {
    await navigator.clipboard.writeText(state.output?.text || "");
    say("Copied.", "ok");
  },
  closeOut() {
    state.output = null;
    renderDetail();
  },
  edit() {
    state.editing = true;
    renderDetail();
  },
  cancelEdit() {
    state.editing = false;
    renderDetail();
  },
  async saveFields(job) {
    const patch = {};
    for (const input of document.querySelectorAll("[data-field]")) {
      if ((input.value || "") !== (job[input.dataset.field] || "")) patch[input.dataset.field] = input.value.trim();
    }
    state.editing = false;
    if (!Object.keys(patch).length) return renderDetail();
    replaceJob(await api.patchJob(job.id, patch));
    say("Saved.", "ok");
  },
  openJd: (job) => window.open(job.url, "_blank"),
  openApply: (job) => window.open(job.apply_url, "_blank"),

  async generate(job) {
    say("Generating the resume. This can take a few minutes.");
    const result = await api.post(job.id, "tailor", { persona_id: job.persona_id });
    if (result && result.ok === false) throw new Error(result.reason || result.error || "Generation failed.");
    forgetFiles(job.id);
    await selectJob(job.id, { refresh: true });
    say("Resume generated.", "ok");
  },
  async fetchJd(job) {
    const result = await api.fetchJd(job);
    if (result && result.ok === false) throw new Error(result.reason || "Could not fetch the JD.");
    await selectJob(job.id, { refresh: true });
    say("JD fetched.", "ok");
  },
  async applied(job) {
    reload(await api.setStatus(job.id, "applied", job.persona_id));
    say("Marked applied.", "ok");
  },
  async skip(job) {
    reload(await api.setStatus(job.id, "skipped", job.persona_id));
  },
  async interviewStatus(job) {
    reload(await api.setStatus(job.id, "interview", job.persona_id));
  },
  async unInterview(job) {
    reload(await api.setStatus(job.id, unStatus(job), job.persona_id));
  },
  async reject(job) {
    reload(await api.setStatus(job.id, "rejected", job.persona_id));
  },
  async unReject(job) {
    reload(await api.setStatus(job.id, unStatus(job), job.persona_id));
  },
  async federal(job) {
    reload(await api.post(job.id, "federal"));
  },
  async unfederal(job) {
    reload(await api.post(job.id, "unfederal"));
  },
  async disable(job) {
    reload(await api.post(job.id, "disable", { persona_id: job.persona_id }));
  },
  async enable(job) {
    reload(await api.post(job.id, "enable", { persona_id: job.persona_id }));
  },
  async openFolder(job) {
    await api.post(job.id, "open-folder");
    say("Opened the folder on the backend machine.", "ok");
  },
  async block(job) {
    if (!confirm(`Block every job from ${job.company}?`)) return;
    reload(await api.post(job.id, "block-company"));
  },
  async delete(job) {
    if (!confirm(`Delete #${job.seq_no} ${job.company}? This removes its folder too.`)) return;
    await api.deleteJob(job.id);
    forgetFiles(job.id);
    state.selectedId = null;
    state.job = null;
    state.output = null;
    renderDetail();
    await loadJobs({ page: state.page });
    say("Deleted.", "ok");
  },
};

$("detail").addEventListener("click", async (event) => {
  const fileEl = event.target.closest(".file");
  if (fileEl) {
    if (!fileEl.classList.contains("missing")) openFile(Number(fileEl.dataset.job), fileEl.dataset.which);
    return;
  }
  const button = event.target.closest("button[data-act]");
  if (!button || !state.job) return;
  const action = button.dataset.act;
  const run = ACTIONS[action];
  if (!run) return;
  const quick = ["copyOut", "closeOut", "edit", "cancelEdit", "openJd", "openApply", "copyJd"].includes(action);
  if (!quick) {
    state.busy = action;
    renderDetail();
  }
  try {
    await run(state.job);
  } catch (err) {
    fail(err);
  } finally {
    if (!quick) {
      state.busy = "";
      renderDetail();
    }
  }
});

$("detail").addEventListener("change", async (event) => {
  if (event.target.id !== "outcome" || !state.job) return;
  try {
    await api.setOutcome(state.job.id, event.target.value, pkgOf(state.job)?.id);
    await selectJob(state.job.id);
    say(`Outcome: ${event.target.value}.`, "ok");
  } catch (err) {
    fail(err);
  }
});

// ---------------------------------------------------------------- persona & settings

function setDot(ok) {
  $("dot").className = `dot ${ok ? "ok" : "bad"}`;
}

async function loadPersonas() {
  const result = await api.personas();
  state.personas = (result.personas || []).filter((p) => p.enabled !== false);
  const settings = await loadSettings();
  const known = state.personas.some((p) => p.id === settings.personaId);
  state.personaId = known ? settings.personaId : result.active || state.personas[0]?.id || "";
  $("persona").innerHTML = state.personas
    .map((p) => `<option value="${esc(p.id)}" ${p.id === state.personaId ? "selected" : ""}>${esc(p.label)}</option>`)
    .join("");
}

$("persona").addEventListener("change", async (event) => {
  state.personaId = event.target.value;
  await saveSettings({ personaId: state.personaId });
  state.selectedId = null;
  state.job = null;
  state.output = null;
  renderDetail();
  loadJobs();
});

$("btnGear").addEventListener("click", async () => {
  const form = $("settings");
  form.hidden = !form.hidden;
  if (!form.hidden) {
    const settings = await loadSettings();
    $("baseUrl").value = settings.baseUrl;
    $("accessToken").value = settings.accessToken;
  }
});

$("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  await saveSettings({ baseUrl: $("baseUrl").value, accessToken: $("accessToken").value });
  files.clear();
  loading.clear();
  $("settingsMsg").textContent = "Testing...";
  try {
    const stats = await api.stats();
    $("settingsMsg").textContent = `Connected: ${stats.total} jobs on file.`;
    setDot(true);
    await start();
  } catch (err) {
    $("settingsMsg").textContent = String(err.message || err);
    setDot(false);
  }
});

async function start() {
  try {
    await loadPersonas();
    setDot(true);
  } catch (err) {
    setDot(false);
    fail(err);
    if (err.status === 401 || err.status === 403 || err.status === 0) $("btnGear").click();
    return;
  }
  await loadJobs();
}

start();
