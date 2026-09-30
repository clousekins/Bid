// The ChatGPT panel: tick jobs, and have ChatGPT in this very tab write their
// resumes, one job at a time, each in a new chat.
//
// It opens instead of the clipper panel whenever the extension icon is
// clicked on chatgpt.com, and nowhere else. The work runs here, in the tab
// the person is looking at: for each job the backend hands over a prompt, the
// page steps (lib/gpt-page.js) paste it and press send, and when ChatGPT has
// finished, its answer goes back. The backend turns the answers into the same
// files an API-generated resume gets, and may ask for more than one prompt
// per job — the screening answers, an ATS retry — so the loop simply follows
// what it is given until the job says it is done.

import { ask, mountShell, say } from "./shell.js";

const TEMPLATE = `
  <div class="panel gpt" part="panel">
    <header class="bar">
      <span class="grip" title="Drag to move">⠿</span>
      <div class="titles">
        <strong>Poong · ChatGPT resumes</strong>
        <span id="status" class="muted">Loading jobs…</span>
      </div>
      <button type="button" id="btnClose" class="icon" title="Close (Esc)">✕</button>
    </header>

    <section class="card">
      <div class="row">
        <label>Write as<select id="persona"></select></label>
        <label>Show
          <select id="show">
            <option value="need">Needs a resume</option>
            <option value="all">Every job with a JD</option>
          </select>
        </label>
      </div>
      <input id="search" type="search" placeholder="Filter by company or title" autocomplete="off" />
    </section>

    <section class="card">
      <div class="card-head">
        <h2>Jobs</h2>
        <span class="muted" id="picked">None ticked</span>
      </div>
      <div class="tools">
        <button type="button" id="btnAll" class="ghost">Tick this page</button>
        <button type="button" id="btnPageNone" class="ghost">Untick this page</button>
        <button type="button" id="btnNone" class="ghost">Clear all ticks</button>
      </div>
      <ul id="jobs" class="jobs"></ul>
      <div class="pager">
        <button type="button" id="pgFirst" class="ghost" title="First page">«</button>
        <button type="button" id="pgPrev" class="ghost" title="Previous page">‹</button>
        <span class="pg">
          Page <input id="pgNum" type="number" min="1" value="1" /> of <b id="pgCount">1</b>
        </span>
        <button type="button" id="pgNext" class="ghost" title="Next page">›</button>
        <button type="button" id="pgLast" class="ghost" title="Last page">»</button>
        <select id="pgSize" title="Jobs per page">
          <option value="50">50 / page</option>
          <option value="100">100 / page</option>
          <option value="200">200 / page</option>
        </select>
      </div>
    </section>

    <div class="actions">
      <button type="button" id="btnStart" class="primary" disabled>Generate resumes</button>
      <button type="button" id="btnStop" hidden>Stop</button>
    </div>
    <p id="msg" class="message" hidden></p>
    <p class="hint">Keep this tab in front while it works. Each job opens a new chat here.</p>
  </div>
`;

const PERSONA_KEY = "gptPersona";
const PAGE_SIZE_KEY = "gptPageSize";
// The send button, once the person has shown it (see learnSend).
const SEND_KEY = "gptSendButton";
const TICK_MS = 1200;
// A reply is finished once ChatGPT has stopped and the page has not changed
// for this long.
const SETTLE_MS = 2500;

export async function mountPanel(hostId) {
  const { root, $, onClose } = await mountShell(hostId, TEMPLATE);
  const panel = new GptPanel(root, $);
  onClose(() => panel.stop("closed"));
  await panel.init();
}

class GptPanel {
  constructor(root, $) {
    this.root = root;
    this.$ = $;
    this.jobs = []; // the page on screen
    // Ticked jobs across every page, by id, in the order they were ticked —
    // which is the order they run in.
    this.ticked = new Map();
    this.page = 0;
    this.pageSize = 50;
    this.total = 0;
    this.running = false;
    this.stopped = false;
    this.session = null;
    this.rowState = new Map(); // job id -> { label, tone, title }
  }

  async init() {
    const $ = this.$;
    const stored = await chrome.storage.local.get({ [PERSONA_KEY]: "", [PAGE_SIZE_KEY]: 50 });
    this.persona = stored[PERSONA_KEY];
    this.pageSize = Number(stored[PAGE_SIZE_KEY]) || 50;
    $("pgSize").value = String(this.pageSize);

    // Filters change what the pages hold, so they start again at page one.
    const refilter = () => {
      this.page = 0;
      this.load();
    };
    $("persona").addEventListener("change", () => {
      this.persona = $("persona").value;
      chrome.storage.local.set({ [PERSONA_KEY]: this.persona });
      // A resume is written as one persona; ticks made for another would mislead.
      this.ticked.clear();
      refilter();
    });
    $("show").addEventListener("change", refilter);
    let typing;
    $("search").addEventListener("input", () => {
      clearTimeout(typing);
      typing = setTimeout(refilter, 300);
    });

    $("btnAll").addEventListener("click", () => {
      for (const job of this.jobs) this.ticked.set(job.id, job);
      this.render();
    });
    $("btnPageNone").addEventListener("click", () => {
      for (const job of this.jobs) this.ticked.delete(job.id);
      this.render();
    });
    $("btnNone").addEventListener("click", () => {
      this.ticked.clear();
      this.render();
    });
    $("jobs").addEventListener("change", (e) => {
      const box = e.target.closest("input[data-id]");
      if (!box) return;
      const job = this.jobs.find((j) => j.id === Number(box.dataset.id));
      if (!job) return;
      if (box.checked) this.ticked.set(job.id, job);
      else this.ticked.delete(job.id);
      this.renderCount();
    });

    $("pgFirst").addEventListener("click", () => this.goTo(0));
    $("pgPrev").addEventListener("click", () => this.goTo(this.page - 1));
    $("pgNext").addEventListener("click", () => this.goTo(this.page + 1));
    $("pgLast").addEventListener("click", () => this.goTo(this.pageCount() - 1));
    $("pgNum").addEventListener("change", () => this.goTo(Number($("pgNum").value) - 1));
    $("pgNum").addEventListener("keydown", (e) => {
      if (e.key === "Enter") this.goTo(Number($("pgNum").value) - 1);
    });
    $("pgSize").addEventListener("change", () => {
      // Keep the first job on screen in view at the new size.
      const first = this.page * this.pageSize;
      this.pageSize = Number($("pgSize").value) || 50;
      chrome.storage.local.set({ [PAGE_SIZE_KEY]: this.pageSize });
      this.goTo(Math.floor(first / this.pageSize));
    });
    $("btnStart").addEventListener("click", () => this.run());
    $("btnStop").addEventListener("click", () => this.stop("stopped"));

    await this.load();
  }

  // --- The list -------------------------------------------------------------

  pageCount() {
    return Math.max(1, Math.ceil(this.total / this.pageSize));
  }

  goTo(page) {
    const target = Math.min(Math.max(0, Number.isFinite(page) ? page : 0), this.pageCount() - 1);
    if (target === this.page && this.jobs.length) {
      this.renderPager(); // put a typed-in page number back if it was out of range
      return;
    }
    this.page = target;
    this.load();
  }

  async load() {
    const $ = this.$;
    // Answers can arrive out of order when pages are flipped quickly; only the
    // latest request may draw.
    const ticket = (this.loadTicket = (this.loadTicket || 0) + 1);
    $("status").textContent = "Loading jobs…";
    try {
      const data = await ask("gpt:jobs", {
        persona_id: this.persona || undefined,
        show: $("show").value,
        q: $("search").value.trim(),
        offset: this.page * this.pageSize,
        limit: this.pageSize,
      });
      if (ticket !== this.loadTicket) return;
      this.persona = data.persona_id;
      this.fillPersonas(data.personas, data.persona_id);
      this.total = data.total ?? (data.jobs || []).length;
      // A page past the end (the list shrank since it was drawn): go to the last.
      if (!(data.jobs || []).length && this.total && this.page > 0) {
        this.page = this.pageCount() - 1;
        this.load();
        return;
      }
      this.jobs = data.jobs || [];
      const what = $("show").value === "need" ? "need a resume" : "have a JD";
      $("status").textContent = `${this.total} job(s) ${what}`;
      this.render();
      $("jobs").scrollTop = 0;
    } catch (err) {
      if (ticket === this.loadTicket) $("status").textContent = err.message;
    }
  }

  renderPager() {
    const $ = this.$;
    const count = this.pageCount();
    $("pgNum").value = String(this.page + 1);
    $("pgNum").max = String(count);
    $("pgCount").textContent = String(count);
    $("pgFirst").disabled = $("pgPrev").disabled = this.page <= 0;
    $("pgNext").disabled = $("pgLast").disabled = this.page >= count - 1;
  }

  fillPersonas(personas, current) {
    const select = this.$("persona");
    if (select.options.length !== personas.length) {
      select.replaceChildren(
        ...personas.map((p) => {
          const option = document.createElement("option");
          option.value = p.id;
          option.textContent = p.label;
          return option;
        })
      );
    }
    select.value = current;
  }

  render() {
    const list = this.$("jobs");
    list.replaceChildren(
      ...this.jobs.map((job) => {
        const li = document.createElement("li");
        const label = document.createElement("label");
        label.className = "job";

        const box = document.createElement("input");
        box.type = "checkbox";
        box.dataset.id = String(job.id);
        box.checked = this.ticked.has(job.id);
        box.disabled = this.running;

        const seq = document.createElement("span");
        seq.className = "seq";
        seq.textContent = `#${job.seq_no ?? "?"}`;

        const what = document.createElement("span");
        what.className = "what";
        const company = document.createElement("b");
        company.textContent = job.company || "—";
        const title = document.createElement("span");
        title.textContent = job.title || "";
        what.append(company, title);

        const pill = document.createElement("span");
        const shown = this.rowState.get(job.id) || stateLabel(job);
        pill.className = `pill ${shown.tone || ""}`.trim();
        pill.textContent = shown.label;
        if (shown.title) pill.title = shown.title;
        pill.dataset.pill = String(job.id);

        label.append(box, seq, what, pill);
        li.append(label);
        return li;
      })
    );
    this.renderPager();
    this.renderCount();
  }

  renderCount() {
    const n = this.ticked.size;
    const here = this.jobs.filter((j) => this.ticked.has(j.id)).length;
    this.$("picked").textContent = !n
      ? "None ticked"
      : here === n
        ? `${n} ticked`
        : `${n} ticked (${here} on this page)`;
    const start = this.$("btnStart");
    start.disabled = this.running || !n;
    start.textContent =
      n === 0 ? "Generate resumes" : n === 1 ? "Generate 1 resume" : `Generate ${n} resumes`;
  }

  mark(jobId, label, tone = "", title = "") {
    this.rowState.set(jobId, { label, tone, title });
    const pill = this.root.querySelector(`[data-pill="${jobId}"]`);
    if (pill) {
      pill.className = `pill ${tone}`.trim();
      pill.textContent = label;
      pill.title = title;
    }
  }

  // --- The run --------------------------------------------------------------

  async run() {
    const $ = this.$;
    // Every ticked job, whichever page it was ticked on.
    const queue = [...this.ticked.values()];
    if (!queue.length) return;
    this.running = true;
    this.stopped = false;
    $("btnStop").hidden = false;
    this.render();
    for (const job of queue) this.mark(job.id, "queued");

    let ok = 0;
    let failed = 0;
    for (const [index, job] of queue.entries()) {
      if (this.stopped) {
        this.mark(job.id, "not run");
        continue;
      }
      say($("msg"), `Job ${index + 1} of ${queue.length}: ${job.company} — ${job.title}`);
      const outcome = await this.one(job);
      if (outcome.ok) {
        ok += 1;
        const ats = outcome.ats_score != null ? ` · ATS ${outcome.ats_score}` : "";
        this.mark(job.id, `done${ats}`, "ok", outcome.folder || "");
        this.ticked.delete(job.id);
      } else {
        failed += 1;
        this.mark(job.id, "failed", "bad", outcome.reason || "");
      }
    }

    this.running = false;
    this.session = null;
    $("btnStop").hidden = true;
    const summary = `${ok} resume(s) written${failed ? `, ${failed} failed (hover a pill for why)` : ""}.`;
    say($("msg"), this.stopped ? `Stopped. ${summary}` : summary, failed ? "warn" : "ok");
    this.render();
  }

  async stop(why) {
    if (!this.running) return;
    this.stopped = true;
    if (this.session) {
      try {
        await ask("gpt:cancel", { session: this.session });
      } catch {
        // The backend ends it at its own timeout.
      }
    }
    if (why === "stopped") say(this.$("msg"), "Stopping after this step…", "warn");
  }

  /** One job, start to finish. Resolves with the backend's outcome. */
  async one(job) {
    let step;
    try {
      this.mark(job.id, "starting");
      step = await ask("gpt:start", { job_id: job.id, persona_id: this.persona });
    } catch (err) {
      return { ok: false, reason: err.message };
    }
    this.session = step.session;
    while (step && !step.done) {
      if (step.pending) {
        this.mark(job.id, "saving");
        step = await ask("gpt:next", { session: step.session });
        continue;
      }
      try {
        const reply = await this.converse(job, step);
        this.mark(job.id, "saving");
        step = await ask("gpt:reply", { session: step.session, step: step.step, text: reply });
      } catch (err) {
        try {
          step = await ask("gpt:fail", { session: step.session, step: step.step, error: err.message });
        } catch (err2) {
          return { ok: false, reason: `${err.message} (${err2.message})` };
        }
      }
    }
    this.session = null;
    return step || { ok: false, reason: "no outcome" };
  }

  /** One prompt: into this tab's chat, and ChatGPT's answer back out. */
  async converse(job, step) {
    const label = step.step > 1 ? `step ${step.step}: ` : "";
    if (step.new_chat) {
      this.mark(job.id, "new chat");
      if (!(await page("newChat"))) {
        throw new Error("chatgpt_no_new_chat: could not find ChatGPT's “New chat” button");
      }
      await this.sleep(1500);
    }
    await this.waitForComposer();
    const before = await page("state");

    this.mark(job.id, `${label}pasting`);
    const pasted = await page("paste", [step.prompt]);
    if (!pasted || !pasted.ok) {
      throw new Error(`chatgpt_paste_failed: ${pasted ? pasted.reason : "no answer from the page"}`);
    }

    this.mark(job.id, `${label}sending`);
    // The page as it stood the moment the prompt went: its own message and
    // Copy button are already counted, so only ChatGPT's answer adds more.
    const sent = await this.send(before);

    this.mark(job.id, `${label}ChatGPT writing`);
    await this.waitForReply(sent, step.timeout || 360);

    this.mark(job.id, `${label}reading`);
    return this.readReply(step);
  }

  async waitForComposer() {
    const until = Date.now() + 30000;
    while (Date.now() < until) {
      this.checkStopped();
      const s = await page("state");
      if (s.loginRequired) {
        throw new Error("chatgpt_not_logged_in: sign in to chatgpt.com in this tab first");
      }
      if (s.composerIsEditor) return;
      await this.sleep(700);
    }
    throw new Error("chatgpt_page_not_ready: ChatGPT's message box did not appear");
  }

  /**
   * Whether the prompt left the box: ChatGPT is answering, or the prompt now
   * sits in the chat as a message with its own Copy button. Resolves with the
   * page at that moment, the baseline an answer is measured from.
   *
   * The page's text merely growing is not proof — that is what a prompt
   * pasted into the wrong place looks like. Nor is a new chat getting its
   * address: that happens before the message and its Copy button appear, and
   * a baseline taken then would count that button as ChatGPT's answer.
   */
  async sentSince(before, ms) {
    const until = Date.now() + ms;
    while (Date.now() < until) {
      const s = await page("state");
      if (s.streaming || s.copies > before.copies) {
        return s;
      }
      await this.sleep(600);
    }
    return null;
  }

  async send(before) {
    const learned = (await chrome.storage.local.get({ [SEND_KEY]: null }))[SEND_KEY];
    // A pasted-text attachment keeps the button disabled while it uploads.
    let info = null;
    const until = Date.now() + 30000;
    while (Date.now() < until) {
      this.checkStopped();
      info = await page("send", [learned, false]);
      if (info.found && info.enabled) break;
      await this.sleep(700);
    }
    if (info && info.found && info.enabled) {
      await page("send", [learned, true]);
      const sent = await this.sentSince(before, 10000);
      if (sent) {
        if (info.via !== "learned") {
          await chrome.storage.local.set({ [SEND_KEY]: { aria: info.aria, testid: info.testid } });
        }
        return sent;
      }
    }
    return this.learnSend(before);
  }

  /**
   * The send button could not be found or did not take. Ask the person to
   * press it once, and remember which button it was: their click both sends
   * this prompt and teaches the panel the button for every job after.
   */
  async learnSend(before) {
    say(
      this.$("msg"),
      "Click ChatGPT's send button (↑) once. Poong will remember it and press it itself from then on.",
      "warn"
    );
    let seen = null;
    const listen = (e) => {
      const button = e.target instanceof Element ? e.target.closest("button") : null;
      if (button && button.closest("form")) {
        seen = {
          aria: button.getAttribute("aria-label") || "",
          testid: button.getAttribute("data-testid") || "",
        };
      }
    };
    document.addEventListener("click", listen, true);
    let sent;
    try {
      sent = await this.sentSince(before, 180000);
      if (!sent) throw new Error("chatgpt_send_failed: the prompt was pasted but never sent");
    } finally {
      document.removeEventListener("click", listen, true);
    }
    if (seen && (seen.aria || seen.testid)) {
      await chrome.storage.local.set({ [SEND_KEY]: seen });
    }
    say(this.$("msg"), "Got it — sending on its own from now on.", "ok");
    return sent;
  }

  async waitForReply(before, timeoutSeconds) {
    const deadline = Date.now() + timeoutSeconds * 1000;
    let sawStreaming = !!before.streaming;
    let last = -1;
    let stableSince = Date.now();
    while (Date.now() < deadline) {
      this.checkStopped();
      await this.sleep(TICK_MS);
      const s = await page("state");
      if (s.streaming) {
        sawStreaming = true;
        last = s.textLength;
        stableSince = Date.now();
        continue;
      }
      if (s.textLength !== last) {
        last = s.textLength;
        stableSince = Date.now();
        continue;
      }
      if (Date.now() - stableSince < SETTLE_MS) continue;
      if (s.canContinue) {
        // A long reply cut off at the output limit.
        await page("clickContinue");
        stableSince = Date.now();
        continue;
      }
      if (sawStreaming || s.copies > before.copies) return;
      if (Date.now() - stableSince > 20000) {
        throw new Error("chatgpt_no_reply: ChatGPT did not start answering");
      }
    }
    throw new Error(`chatgpt_timeout: no complete answer within ${timeoutSeconds}s`);
  }

  /**
   * ChatGPT's answer, through the newest message's own Copy button. Checked
   * before it is trusted: never the prompt itself (the user message has a
   * Copy button too), and holding what this prompt asked for.
   */
  async readReply(step) {
    const promptHead = step.prompt.trim().slice(0, 160);
    // Only the newest message, or the one before it when ChatGPT's answer
    // carries no Copy button of its own. Further back is an earlier answer —
    // the resume, when the questions are being read — and would be wrong.
    for (let fromEnd = 0; fromEnd < 2; fromEnd += 1) {
      const copied = await page("copyAt", [fromEnd]);
      if (!copied || copied.text == null) break;
      const text = copied.text.trim();
      if (!text || text.includes(promptHead)) continue;
      if (step.expect === "html" && !/<\/html>/i.test(text)) continue;
      if (step.expect === "json" && !text.includes("{")) continue;
      return text;
    }
    throw new Error("chatgpt_read_failed: could not read ChatGPT's answer from the page");
  }

  checkStopped() {
    if (this.stopped) throw new Error("cancelled: stopped from the Poong panel");
  }

  sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

async function page(fn, args = []) {
  return ask("gpt:page", { fn, args });
}

function stateLabel(job) {
  if (job.state === "need_resume") return { label: "needs resume" };
  if (job.state === "failed") return { label: "failed before", tone: "warn", title: job.note || "" };
  return { label: job.state.replace(/_/g, " "), tone: "ok" };
}
