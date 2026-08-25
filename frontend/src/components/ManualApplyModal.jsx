import { useEffect, useRef, useState } from "react";
import { client, logPoong } from "../api.js";
import { shortPersona } from "../utils/persona.js";
import { formatGateList, gateMessage } from "../utils/gates.js";
import ResumePreview from "./ResumePreview.jsx";

const emptyForm = {
  company: "",
  title: "",
  url: "",
  location: "",
  description: "",
};

function resumeReadyFromJob(job, personaId) {
  const pkg = (job?.packages || []).find((p) => p.persona_id === personaId);
  return Boolean(pkg && ["resume_ready", "applying", "applied"].includes(pkg.status));
}

function tailorFromJob(job, personaId) {
  const pkg = (job?.packages || []).find((p) => p.persona_id === personaId);
  if (!resumeReadyFromJob(job, personaId)) return null;
  return {
    ok: true,
    resume_folder: pkg.folder_path,
    folder: pkg.folder_path,
    package: pkg,
    job,
  };
}

export default function ManualApplyModal({
  open,
  openKey = 0,
  workingPersonaId,
  resumeJob,
  prepItem,
  onClose,
  onCreated,
  onQueueGenerate,
  onAssistedApply,
}) {
  const [form, setForm] = useState(emptyForm);
  const [job, setJob] = useState(null);
  const [tailorResult, setTailorResult] = useState(null);
  const [questionsText, setQuestionsText] = useState("");
  const [answers, setAnswers] = useState([]);
  const [savedQaFolder, setSavedQaFolder] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [urlInferred, setUrlInferred] = useState(false);
  const [jobListed, setJobListed] = useState(false);
  const [warnings, setWarnings] = useState([]);
  const answersPanelRef = useRef(null);
  const handledReadyKey = useRef("");
  const hydrateKey = `${open}:${openKey}:${workingPersonaId || ""}`;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    const hydrate = async () => {
      handledReadyKey.current = "";
      setError("");
      setBusy("");
      setUrlInferred(false);
      if (!resumeJob?.id) {
        setForm(emptyForm);
        setJob(null);
        setTailorResult(null);
        setQuestionsText("");
        setAnswers([]);
        setSavedQaFolder("");
        setStatus("");
        setJobListed(false);
        setWarnings([]);
        return;
      }

      let full = resumeJob;
      try {
        full = await client.getJob(resumeJob.id);
      } catch {
        full = resumeJob;
      }
      if (cancelled) return;

      setJob(full);
      setJobListed(true);
      setForm({
        company: full.company || "",
        title: full.title || "",
        url: full.url || "",
        location: full.location || "",
        description: full.description || "",
      });
      setTailorResult(tailorFromJob(full, workingPersonaId));
      setStatus(
        resumeReadyFromJob(full, workingPersonaId)
          ? `Resume already on file for #${full.seq_no}. Paste questions to generate answers, or regenerate if needed.`
          : `Continuing #${full.seq_no} ${full.company} — ${full.title}. JD is saved; generate the resume when ready.`
      );

      try {
        const qa = await client.getJobQa(full.id, workingPersonaId);
        if (cancelled) return;
        if (qa.questions?.length) setQuestionsText(qa.questions.join("\n"));
        else setQuestionsText("");
        setAnswers(qa.answers || []);
        setSavedQaFolder(qa.folder || "");
      } catch (e) {
        if (!cancelled) logPoong("manual qa hydrate fail", e);
      }
    };

    hydrate();
    return () => {
      cancelled = true;
    };
  }, [hydrateKey]);

  useEffect(() => {
    if (!open || !job?.id || !prepItem) return;
    if (prepItem.jobId && prepItem.jobId !== job.id) return;

    if (prepItem.status === "queued" || prepItem.status === "running") {
      handledReadyKey.current = "";
      setBusy("tailor");
      setStatus(
        `Resume generating in the background (${prepItem.pct || 8}%). ` +
          "Close this window anytime and start another Manual apply — this job keeps going."
      );
      return;
    }

    if (prepItem.status === "failed") {
      setBusy("");
      setError(prepItem.error || "Resume generation failed");
      return;
    }

    if (prepItem.status === "ready" && handledReadyKey.current !== prepItem.key) {
      handledReadyKey.current = prepItem.key;
      (async () => {
        try {
          const full = await client.getJob(job.id);
          setJob(full);
          setForm((prev) => ({
            ...prev,
            company: full.company || prev.company,
            title: full.title || prev.title,
            url: full.url || prev.url,
            location: full.location || prev.location,
            description: full.description || prev.description,
          }));
          const next = tailorFromJob(full, workingPersonaId);
          setTailorResult(next || { ok: true, job: full, resume_folder: full.folder_path });
          setBusy("");
          setStatus(
            `Resume ready for #${full.seq_no} ${full.company}. ` +
              "Paste questions and generate answers — JD and resume are still here."
          );
          await onCreated?.(full);
        } catch (e) {
          setBusy("");
          setError(String(e.message || e));
        }
      })();
    }
  }, [open, job?.id, prepItem?.key, prepItem?.status, prepItem?.pct, prepItem?.error, prepItem?.jobId, workingPersonaId, onCreated]);

  useEffect(() => {
    if (!answers.length) return;
    answersPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [answers]);

  if (!open) return null;

  const activePersonaId = workingPersonaId || "";
  const profileName = shortPersona(activePersonaId);
  const generating = Boolean(
    busy === "tailor" ||
      (prepItem &&
        (prepItem.status === "queued" || prepItem.status === "running") &&
        (!prepItem.jobId || prepItem.jobId === job?.id))
  );
  const resumeReady = Boolean(tailorResult?.ok) || resumeReadyFromJob(job, activePersonaId);
  const resumeFolder =
    tailorResult?.resume_folder ||
    tailorResult?.folder ||
    tailorResult?.package?.folder_path ||
    savedQaFolder ||
    "";
  const listed = Boolean(job) || jobListed;
  const formBusy = Boolean(busy) && busy !== "tailor";
  const pct = generating ? prepItem?.pct || 12 : resumeReady ? 100 : 0;

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  const close = () => {
    onClose?.();
  };

  const prepareJob = async () => {
    setError("");
    if (!activePersonaId) {
      setError("Choose a Working as profile first.");
      return null;
    }
    if ((form.description || "").trim().length < 50) {
      setError(
        "Paste a full JD (at least ~50 characters). Company/title/URL are auto-filled from it."
      );
      return null;
    }
    setBusy("prepare");
    try {
      const res = await client.manualPrepare({
        persona_id: activePersonaId,
        description: form.description.trim(),
        url: form.url.trim() || undefined,
        company: form.company.trim() || undefined,
        title: form.title.trim() || undefined,
        location: form.location.trim() || undefined,
      });
      const saved = res.job;
      setJob(saved);
      setJobListed(true);
      setTailorResult(null);
      setAnswers([]);
      setSavedQaFolder("");
      setUrlInferred(Boolean(res.url_inferred));
      setForm((prev) => ({
        ...prev,
        company: saved.company || prev.company,
        title: saved.title || prev.title,
        url: saved.url || prev.url,
        location: saved.location || prev.location || "Remote, US",
      }));
      setStatus(
        `Job listed as #${saved.seq_no} ${saved.company} — ${saved.title}. ` +
          "No resume files yet — click Generate resume next." +
          (res.url_inferred
            ? " No URL found in JD — placeholder set; edit URL before assisted apply."
            : "")
      );
      await onCreated?.(saved);
      return saved;
    } catch (e) {
      const detail = String(e.message || e);
      if (detail.includes("company_already_applied")) {
        setError(gateMessage("company_already_applied", { company: form.company }));
      } else if (detail.includes("company_blocked")) {
        setError(gateMessage("company_blocked", { company: form.company }));
      } else {
        setError(detail);
      }
      logPoong("manual-prepare fail", e);
      return null;
    } finally {
      setBusy("");
    }
  };

  const fetchJd = async () => {
    setError("");
    const url = (form.url || job?.url || "").trim();
    if (!url || url.includes("manual.poong.local")) {
      setError("Paste a real job/apply URL, then Fetch JD.");
      return;
    }
    setBusy("fetch");
    setStatus("Fetching JD — HTTP first, then browser if the page is a login wall or too short.");
    try {
      const res = await client.manualFetch({
        persona_id: activePersonaId,
        url,
        job_id: job?.id || undefined,
      });
      const extra = { company: res.company, seq: res.job?.seq_no };
      if (res.description) {
        setForm((prev) => ({
          ...prev,
          description: res.description || prev.description,
          company: res.company || prev.company,
          title: res.title || prev.title,
          url: res.url || prev.url,
          location: res.location || prev.location || "Remote, US",
        }));
        setUrlInferred(false);
      }
      if (res.job) {
        setJob(res.job);
        setJobListed(true);
        await onCreated?.(res.job);
      }
      setWarnings(res.warnings || []);
      if (res.blockers?.length) {
        setError(formatGateList(res.blockers, extra));
        return;
      }
      if (res.ok === false) {
        setError(gateMessage(res.reason, extra) || res.reason || "Fetch failed");
        return;
      }
      setStatus(
        res.existing
          ? `Loaded existing #${res.job?.seq_no || ""} ${res.company || ""} — ${formatGateList(res.warnings || [], extra) || "JD on file."}`
          : `Fetched JD via ${res.fetch_via || "http"} (${(res.description || "").length} chars). Add to job list next, or Generate resume.`
      );
    } catch (e) {
      setError(String(e.message || e));
      logPoong("manual-fetch fail", e);
    } finally {
      setBusy("");
    }
  };

  const generateResume = async () => {
    setError("");
    let current = job;
    if (!current) {
      current = await prepareJob();
      if (!current) return;
    }
    if (generating) return;
    setBusy("tailor");
    setStatus(
      "Queued resume generation. You can wait here, or Close and keep browsing — progress stays in the bottom activity panel."
    );
    try {
      await onQueueGenerate?.(current);
      await onCreated?.(current);
    } catch (e) {
      setBusy("");
      setError(String(e.message || e));
      logPoong("manual-tailor queue fail", e);
    }
  };

  const saveJobDetails = async () => {
    if (!job?.id) {
      setError("Add the job to the list first, then you can edit and save details.");
      return;
    }
    setError("");
    setBusy("save");
    try {
      const updated = await client.patchJob(job.id, {
        company: form.company.trim(),
        title: form.title.trim(),
        url: form.url.trim(),
        location: form.location.trim(),
      });
      setJob(updated);
      setForm((prev) => ({
        ...prev,
        company: updated.company || prev.company,
        title: updated.title || prev.title,
        url: updated.url || prev.url,
        location: updated.location || prev.location,
      }));
      setUrlInferred(false);
      setStatus(
        `Updated job #${updated.seq_no}: ${updated.company} — ${updated.title}\nURL: ${updated.url}`
      );
      await onCreated?.(updated);
    } catch (e) {
      setError(String(e.message || e));
      logPoong("manual-save-fields fail", e);
    } finally {
      setBusy("");
    }
  };

  const generateAnswers = async () => {
    setError("");
    if (!questionsText.trim()) {
      setError("Paste at least one application question.");
      return;
    }
    let current = job;
    if (!current) {
      current = await prepareJob();
      if (!current) return;
    }
    if (!resumeReady) {
      setError("Generate the resume first so answers can use it.");
      return;
    }
    setBusy("answers");
    try {
      const res = await client.manualAnswers({
        persona_id: activePersonaId,
        job_id: current.id,
        questions_text: questionsText,
        description: form.description.trim(),
        company: form.company.trim() || current.company,
        title: form.title.trim() || current.title,
        url: form.url.trim() || current.url,
      });
      setAnswers(res.answers || []);
      if (res.saved_folder) setSavedQaFolder(res.saved_folder);
      setStatus(
        `Generated ${(res.answers || []).length} answer(s)` +
          (res.resume_used ? " using the tailored resume" : " (JD + profile only)") +
          (res.saved_folder
            ? `\nSaved Q&A to: ${res.saved_folder}\n(questions.json, answers.json, application_qa.txt)`
            : "")
      );
    } catch (e) {
      setError(String(e.message || e));
      logPoong("manual-answers fail", e);
    } finally {
      setBusy("");
    }
  };

  const openJobPage = () => {
    const url = (job?.url || form.url || "").trim();
    if (!url || url.includes("manual.poong.local")) {
      setError("No real job URL yet — paste/edit the apply URL first.");
      return;
    }
    window.open(url, "_blank");
  };

  const startAssisted = async () => {
    if (!job?.id) {
      setError("Save/generate the job first.");
      return;
    }
    if (!resumeReady) {
      setError("Generate the resume first, then Assisted apply can upload it.");
      return;
    }
    const url = (job.url || form.url || "").trim();
    if (!url || url.includes("manual.poong.local")) {
      setError("Set a real job/apply URL before Assisted apply.");
      return;
    }
    close();
    await onAssistedApply?.(job);
  };

  const copyAnswer = async (text) => {
    try {
      await navigator.clipboard.writeText(text || "");
      setStatus("Answer copied");
    } catch {
      setError("Could not copy to clipboard");
    }
  };

  const copyAll = async () => {
    const blob = answers
      .map((row) => `Q: ${row.question}\nA: ${row.answer}`)
      .join("\n\n");
    await copyAnswer(blob);
  };

  return (
    <div className="modal-backdrop">
      <div className="modal manual-apply-modal">
        <div className="manual-apply-head">
          <h3 style={{ margin: 0 }}>
            {resumeJob?.id ? "Continue apply" : "Manual apply"} · {profileName}
          </h3>
          <button type="button" onClick={close}>
            {generating ? "Close & keep generating" : "Close"}
          </button>
        </div>
        <p className="muted">
          Paste a job URL and <strong>Fetch JD</strong> (HTTP, then browser), or paste the JD.
          Duplicate URLs reopen the existing listing. Already-applied / blocked companies are
          stopped. Then <strong>Generate resume</strong> in the background.
        </p>

        <div className="manual-flow-status" aria-label="Manual apply progress">
          <div className={`manual-flow-step ${listed ? "done" : "idle"}`}>
            <span className="manual-flow-dot" />
            <div>
              <strong>1. Job list</strong>
              <span>
                {listed
                  ? `Saved #${job?.seq_no} ${job?.company || ""} — in the main jobs table`
                  : "Not added yet"}
              </span>
            </div>
          </div>
          <div
            className={`manual-flow-step ${
              resumeReady ? "done" : generating ? "next" : listed ? "next" : "idle"
            }`}
          >
            <span className="manual-flow-dot" />
            <div>
              <strong>2. Resume</strong>
              <span>
                {resumeReady
                  ? "Generated — files are in the job folder"
                  : generating
                    ? `Generating… ${pct}%`
                    : listed
                      ? "Job is listed; generate the resume next"
                      : "Waiting for job list"}
              </span>
            </div>
          </div>
          <div
            className={`manual-flow-step ${
              answers.length ? "done" : resumeReady ? "next" : "idle"
            }`}
          >
            <span className="manual-flow-dot" />
            <div>
              <strong>3. Answers</strong>
              <span>
                {answers.length
                  ? `${answers.length} answer(s) ready to copy`
                  : resumeReady
                    ? "Paste questions, then generate"
                    : "After the resume"}
              </span>
            </div>
          </div>
        </div>

        {generating || (listed && !resumeReady) ? (
          <div className={`manual-live-bar ${generating ? "active" : ""}`}>
            <div className="manual-live-bar-track" aria-hidden>
              <span style={{ width: `${generating ? pct : 0}%` }} />
            </div>
            <div className="manual-live-bar-copy">
              {generating
                ? `Working on #${job?.seq_no} ${job?.company || ""} — close this and apply to the next one.`
                : "Resume not generated yet."}
            </div>
          </div>
        ) : null}

        {error ? <div className="error-banner">{error}</div> : null}
        {warnings.length ? (
          <div className="warning-banner">
            {formatGateList(warnings, { company: form.company, seq: job?.seq_no })}
          </div>
        ) : null}
        {status ? (
          <div className="batch-progress" style={{ whiteSpace: "pre-wrap" }}>
            {status}
          </div>
        ) : null}

        <label>
          Job / apply URL
          <input
            value={form.url}
            disabled={formBusy}
            onChange={(e) => {
              setField("url", e.target.value);
              setUrlInferred(false);
            }}
            placeholder="https://… paste the apply URL, then Fetch JD"
          />
        </label>
        <div className="modal-actions">
          <button
            type="button"
            className="primary"
            disabled={formBusy || generating}
            onClick={fetchJd}
          >
            {busy === "fetch" ? "Fetching JD…" : "Fetch JD"}
          </button>
        </div>
        {urlInferred ? (
          <p className="muted">No URL found in JD — placeholder set. Edit before Assisted apply.</p>
        ) : null}

        <label>
          Job description (required)
          <textarea
            rows={9}
            value={form.description}
            disabled={formBusy}
            onChange={(e) => setField("description", e.target.value)}
            placeholder="Paste the full JD here. Click Add / Generate resume to auto-fill fields below."
          />
        </label>

        <div className="manual-apply-grid">
          <label>
            Company (auto after Add / Generate)
            <input
              value={form.company}
              disabled={formBusy}
              onChange={(e) => setField("company", e.target.value)}
              placeholder="Extracted from JD"
            />
          </label>
          <label>
            Title (auto after Add / Generate)
            <input
              value={form.title}
              disabled={formBusy}
              onChange={(e) => setField("title", e.target.value)}
              placeholder="Extracted from JD"
            />
          </label>
        </div>

        <label>
          Location (auto after Fetch / Add / Generate)
          <input
            value={form.location}
            disabled={formBusy}
            onChange={(e) => setField("location", e.target.value)}
            placeholder="Remote, US"
          />
        </label>

        <div className="modal-actions">
          <button type="button" disabled={formBusy || generating} onClick={prepareJob}>
            {busy === "prepare" ? "Saving…" : "Add to job list"}
          </button>
          <button type="button" disabled={formBusy || !job} onClick={saveJobDetails}>
            {busy === "save" ? "Saving details…" : "Save job details"}
          </button>
          <button
            type="button"
            className="primary"
            disabled={formBusy || generating}
            onClick={generateResume}
          >
            {generating
              ? `Generating… ${pct}%`
              : resumeReady
                ? "Regenerate resume"
                : "Generate resume"}
          </button>
          <button type="button" disabled={!job} onClick={openJobPage}>
            Open job page
          </button>
          <button
            type="button"
            className="primary"
            disabled={formBusy || !job || !resumeReady}
            onClick={startAssisted}
          >
            Assisted apply
          </button>
        </div>

        {job ? (
          <p className="muted">
            Job #{job.seq_no}: {job.company} — {job.title}
            {resumeFolder ? (
              <>
                <br />
                Folder: <span className="detail-mono">{resumeFolder}</span>
              </>
            ) : (
              <>
                <br />
                Folder will be{" "}
                <span className="detail-mono">
                  data/applications/{activePersonaId}/{job.seq_no}-…
                </span>
              </>
            )}
          </p>
        ) : null}

        {job && (resumeReady || generating) ? (
          <div className="modal-actions">
            <ResumePreview
              jobId={job.id}
              personaId={activePersonaId}
              packages={job.packages || []}
              generating={generating}
            />
          </div>
        ) : null}

        <label>
          Application questions
          <textarea
            rows={7}
            value={questionsText}
            disabled={formBusy}
            onChange={(e) => setQuestionsText(e.target.value)}
            placeholder={
              "Paste one question per line, e.g.\nYears of Python experience?\nAre you authorized to work in the US?\nWhy are you interested in this role?"
            }
          />
        </label>

        <div className="modal-actions">
          <button
            type="button"
            className="primary"
            disabled={formBusy || generating || !resumeReady}
            onClick={generateAnswers}
          >
            {busy === "answers" ? "Generating answers…" : "Generate answers"}
          </button>
          {answers.length ? (
            <button type="button" disabled={formBusy} onClick={copyAll}>
              Copy all Q&A
            </button>
          ) : null}
        </div>

        {answers.length ? (
          <div className="manual-answers-panel" ref={answersPanelRef}>
            <div className="manual-answers-panel-head">
              <strong>Generated answers</strong>
              <span className="muted">{answers.length} question(s)</span>
            </div>
            <div className="manual-answer-list">
              {answers.map((row) => (
                <div key={row.question} className="manual-answer-card">
                  <div className="manual-q">{row.question}</div>
                  <div className="manual-a">{row.answer || "—"}</div>
                  <button type="button" onClick={() => copyAnswer(row.answer)}>
                    Copy answer
                  </button>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
