// The resume panel: tick jobs, and have the AI in this very tab write their
// resumes, one job at a time, each in a new chat.
//
// One panel, two engines. It opens instead of the clipper panel whenever the
// extension icon is clicked on an AI site — chatgpt.com (engine "gpt") or
// claude.ai (engine "claude") — and nowhere else; which one is decided by the
// site, in content.js, because the panel drives the DOM of the very tab it runs
// in and cannot reach across to the other site. The work runs here, in the tab
// the person is looking at: for each job the backend hands over a prompt, the
// page steps (lib/gpt-page.js or lib/claude-page.js, by engine) paste it and
// press send, and when the AI has finished, its answer goes back. The backend
// turns the answers into the same files an API-generated resume gets, and the
// backend session API is the same for both engines — only the on-page steps
// differ. It may ask for more than one prompt per job (screening answers, an ATS
// retry), so the loop simply follows what it is given until the job says done.

import { ask, mountShell, say } from "./shell.js";

// Each engine's human name and storage namespace. `id` is both the storage
// prefix and the engine the page steps run under (see background.js onPage).
const ENGINES = {
  gpt: { id: "gpt", name: "ChatGPT", site: "chatgpt.com" },
  claude: { id: "claude", name: "Claude", site: "claude.ai" },
};

const template = (engine) => `
  <div class="panel ${engine.id}" part="panel">
    <header class="bar">
      <span class="grip" title="Drag to move">⠿</span>
      <div class="titles">
        <strong>Poong · ${engine.name} resumes</strong>
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
    <p class="hint">Keep this ${engine.name} tab in front while it works. Each job opens a new chat here.</p>
  </div>
`;

const TICK_MS = 1200;
// A reply is finished once the AI has stopped and the page has not changed for
// this long.
const SETTLE_MS = 2500;

export async function mountPanel(hostId, engineId = "gpt") {
  const engine = ENGINES[engineId] || ENGINES.gpt;
  const { root, $, onClose } = await mountShell(hostId, template(engine));
  const panel = new EnginePanel(root, $, engine);
  onClose(() => panel.stop("closed"));
  await panel.init();
}

class EnginePanel {
  constructor(root, $, engine) {
    this.root = root;
    this.$ = $;
    this.engine = engine;
    // Per-engine storage, so ChatGPT and Claude each remember their own last
    // persona, page size and taught send button without treading on each other.
    this.personaKey = `${engine.id}Persona`;
    this.pageSizeKey = `${engine.id}PageSize`;
    this.sendKey = `${engine.id}SendButton`;
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
    const stored = await chrome.storage.local.get({ [this.personaKey]: "", [this.pageSizeKey]: 50 });
    this.persona = stored[this.personaKey];
    this.pageSize = Number(stored[this.pageSizeKey]) || 50;
    $("pgSize").value = String(this.pageSize);

    // Filters change what the pages hold, so they start again at page one.
    const refilter = () => {
      this.page = 0;
      this.load();
    };
    $("persona").addEventListener("change", () => {
      this.persona = $("persona").value;
      chrome.storage.local.set({ [this.personaKey]: this.persona });
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
      chrome.storage.local.set({ [this.pageSizeKey]: this.pageSize });
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

  /** One prompt: into this tab's chat, and the AI's answer back out. */
  async converse(job, step) {
    const engine = this.engine;
    const label = step.step > 1 ? `step ${step.step}: ` : "";
    if (step.new_chat) {
      this.mark(job.id, "new chat");
      if (!(await this.pageStep("newChat"))) {
        throw new Error(`${engine.id}_no_new_chat: could not find ${engine.name}'s “New chat” button`);
      }
      await this.sleep(1500);
    }
    await this.waitForComposer();
    const before = await this.pageStep("state");

    this.mark(job.id, `${label}pasting`);
    const pasted = await this.pageStep("paste", [step.prompt]);
    if (!pasted || !pasted.ok) {
      throw new Error(`${engine.id}_paste_failed: ${pasted ? pasted.reason : "no answer from the page"}`);
    }

    this.mark(job.id, `${label}sending`);
    // The page as it stood before the prompt went: the answers already on it
    // are counted, so only the AI's new reply pushes the count past this.
    const sent = await this.send(before);

    this.mark(job.id, `${label}${engine.name} writing`);
    await this.waitForReply(sent, step.timeout || 360);

    this.mark(job.id, `${label}reading`);
    return this.readReply(step);
  }

  async waitForComposer() {
    const engine = this.engine;
    const until = Date.now() + 30000;
    while (Date.now() < until) {
      this.checkStopped();
      const s = await this.pageStep("state");
      if (s.loginRequired) {
        throw new Error(`${engine.id}_not_logged_in: sign in to ${engine.site} in this tab first`);
      }
      if (s.composerIsEditor) return;
      await this.sleep(700);
    }
    throw new Error(`${engine.id}_page_not_ready: ${engine.name}'s message box did not appear`);
  }

  /**
   * Whether the prompt left the box: the AI is answering, or a new reply has
   * appeared in the chat. Resolves with the page at that moment, the baseline an
   * answer is measured from.
   *
   * The page's text merely growing is not proof — that is what a prompt pasted
   * into the wrong place looks like. A new reply body appearing, or the Stop
   * button showing, is.
   */
  async sentSince(before, ms) {
    const until = Date.now() + ms;
    while (Date.now() < until) {
      const s = await this.pageStep("state");
      if (s.streaming || s.answers > before.answers) {
        return s;
      }
      await this.sleep(600);
    }
    return null;
  }

  async send(before) {
    const learned = (await chrome.storage.local.get({ [this.sendKey]: null }))[this.sendKey];
    // A pasted-text attachment can keep the button disabled while it uploads.
    let info = null;
    const until = Date.now() + 30000;
    while (Date.now() < until) {
      this.checkStopped();
      info = await this.pageStep("send", [learned, false]);
      if (info.found && info.enabled) break;
      await this.sleep(700);
    }
    if (info && info.found && info.enabled) {
      await this.pageStep("send", [learned, true]);
      const sent = await this.sentSince(before, 10000);
      if (sent) {
        if (info.via !== "learned") {
          await chrome.storage.local.set({ [this.sendKey]: { aria: info.aria, testid: info.testid } });
        }
        return sent;
      }
    }
    return this.learnSend(before);
  }

  /**
   * The send button could not be found or did not take. Ask the person to press
   * it once, and remember which button it was: their click both sends this
   * prompt and teaches the panel the button for every job after.
   */
  async learnSend(before) {
    const engine = this.engine;
    say(
      this.$("msg"),
      `Click ${engine.name}'s send button (↑) once. Poong will remember it and press it itself from then on.`,
      "warn"
    );
    let seen = null;
    const listen = (e) => {
      // Clicks inside the panel's shadow root arrive here retargeted to the host
      // element, whose closest("button") is null — so this only ever catches a
      // real page button.
      const button = e.target instanceof Element ? e.target.closest("button") : null;
      if (button) {
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
      if (!sent) throw new Error(`${engine.id}_send_failed: the prompt was pasted but never sent`);
    } finally {
      document.removeEventListener("click", listen, true);
    }
    if (seen && (seen.aria || seen.testid)) {
      await chrome.storage.local.set({ [this.sendKey]: seen });
    }
    say(this.$("msg"), "Got it — sending on its own from now on.", "ok");
    return sent;
  }

  async waitForReply(before, timeoutSeconds) {
    const engine = this.engine;
    const deadline = Date.now() + timeoutSeconds * 1000;
    let sawStreaming = !!before.streaming;
    let last = -1;
    let stableSince = Date.now();
    while (Date.now() < deadline) {
      this.checkStopped();
      await this.sleep(TICK_MS);
      const s = await this.pageStep("state");
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
        await this.pageStep("clickContinue");
        stableSince = Date.now();
        continue;
      }
      if (sawStreaming || s.answers > before.answers) return;
      if (Date.now() - stableSince > 20000) {
        throw new Error(`${engine.id}_no_reply: ${engine.name} did not start answering`);
      }
    }
    throw new Error(`${engine.id}_timeout: no complete answer within ${timeoutSeconds}s`);
  }

  /**
   * The AI's answer, read from the newest message on the page. Checked before it
   * is trusted: never the prompt itself (the user message is read the same way),
   * and holding what this prompt asked for.
   */
  async readReply(step) {
    const engine = this.engine;
    const promptHead = step.prompt.trim().slice(0, 160);
    // Only the newest message, or the one before it when the anchor lands on the
    // user's own message. Further back is an earlier answer — the resume, when
    // the questions are being read — and would be wrong.
    const why = [];
    for (let fromEnd = 0; fromEnd < 2; fromEnd += 1) {
      const got = await this.pageStep("readAt", [fromEnd]);
      if (!got || got.text == null) {
        why.push(`#${fromEnd}:no-body(bodies=${got ? got.count : "?"})`);
        break;
      }
      const text = got.text.trim();
      const sample = text.slice(0, 80).replace(/\s+/g, " ");
      if (!text) {
        why.push(`#${fromEnd}:empty`);
        continue;
      }
      if (text.includes(promptHead)) {
        why.push(`#${fromEnd}:is-prompt(len=${text.length}, head="${sample}")`);
        continue;
      }
      if (step.expect === "html" && !/<\/html>/i.test(text)) {
        why.push(`#${fromEnd}:no-</html>(len=${text.length}, head="${sample}")`);
        continue;
      }
      if (step.expect === "json" && !text.includes("{")) {
        why.push(`#${fromEnd}:no-json(len=${text.length})`);
        continue;
      }
      return text;
    }
    // Nothing usable. Carry back a snapshot of the page so the failure in the log
    // says why, when the DOM here is not one we can open.
    const probe = await this.pageStep("probe").catch(() => null);
    throw new Error(
      `${engine.id}_read_failed: could not read ${engine.name}'s answer from the page [${why.join(", ")}]` +
        (probe ? ` ${JSON.stringify(probe)}` : "")
    );
  }

  /**
   * Run one on-page step under this panel's engine. Named pageStep, not page,
   * because this.page is the current page index — a method called page would be
   * shadowed by it the moment the constructor sets this.page = 0.
   */
  pageStep(fn, args = []) {
    return ask("engine:page", { engine: this.engine.id, fn, args });
  }

  checkStopped() {
    if (this.stopped) throw new Error("cancelled: stopped from the Poong panel");
  }

  sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

function stateLabel(job) {
  if (job.state === "need_resume") return { label: "needs resume" };
  if (job.state === "failed") return { label: "failed before", tone: "warn", title: job.note || "" };
  return { label: job.state.replace(/_/g, " "), tone: "ok" };
}
