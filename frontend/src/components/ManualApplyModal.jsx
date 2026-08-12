import { useEffect, useState } from "react";
import { client, logPoong } from "../api.js";
import { shortPersona } from "../utils/persona.js";

const emptyForm = {
  company: "",
  title: "",
  url: "",
  location: "",
  description: "",
};

export default function ManualApplyModal({
  open,
  workingPersonaId,
  onClose,
  onCreated,
  onAssistedApply,
}) {
  const [form, setForm] = useState(emptyForm);
  const [job, setJob] = useState(null);
  const [tailorResult, setTailorResult] = useState(null);
  const [questionsText, setQuestionsText] = useState("");
  const [answers, setAnswers] = useState([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [urlInferred, setUrlInferred] = useState(false);

  useEffect(() => {
    if (!open) return;
    setForm(emptyForm);
    setJob(null);
    setTailorResult(null);
    setQuestionsText("");
    setAnswers([]);
    setBusy("");
    setError("");
    setStatus("");
    setUrlInferred(false);
  }, [open, workingPersonaId]);

  if (!open) return null;

  const activePersonaId = workingPersonaId || "";
  const profileName = shortPersona(activePersonaId);
  const resumeFolder =
    tailorResult?.resume_folder ||
    tailorResult?.folder ||
    tailorResult?.package?.folder_path ||
    "";

  const setField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  const reset = () => {
    setForm(emptyForm);
    setJob(null);
    setTailorResult(null);
    setQuestionsText("");
    setAnswers([]);
    setBusy("");
    setError("");
    setStatus("");
    setUrlInferred(false);
  };

  const close = () => {
    reset();
    onClose?.();
  };

  const prepareJob = async () => {
    setError("");
    if (!activePersonaId) {
      setError("Choose a Working as profile first.");
      return null;
    }
    if ((form.description || "").trim().length < 50) {
      setError("Paste a full JD (at least ~50 characters). Company/title/URL are auto-filled from it.");
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
      setTailorResult(null);
      setAnswers([]);
      setUrlInferred(Boolean(res.url_inferred));
      setForm((prev) => ({
        ...prev,
        company: saved.company || prev.company,
        title: saved.title || prev.title,
        url: saved.url || prev.url,
        location: saved.location || prev.location || "Remote, US",
      }));
      setStatus(
        `Added #${saved.seq_no} ${saved.company} — ${saved.title}` +
          (res.url_inferred
            ? " (no URL found in JD — placeholder set; edit URL before assisted apply)"
            : "")
      );
      await onCreated?.(saved);
      return saved;
    } catch (e) {
      setError(String(e.message || e));
      logPoong("manual-prepare fail", e);
      return null;
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
    setBusy("tailor");
    try {
      const res = await client.manualTailor({
        job_id: current.id,
        persona_id: activePersonaId,
      });
      if (!res.ok) {
        throw new Error(res.reason || "tailor_failed");
      }
      setTailorResult(res);
      if (res.job) setJob(res.job);
      const folder = res.resume_folder || res.folder || "";
      setStatus(
        `Resume ready as ${res.persona_id || activePersonaId}` +
          (folder ? `\nSaved to: ${folder}` : "")
      );
      await onCreated?.(res.job || current);
    } catch (e) {
      setError(String(e.message || e));
      logPoong("manual-tailor fail", e);
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
    if (!tailorResult?.ok) {
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
      setStatus(
        `Generated ${(res.answers || []).length} answer(s)` +
          (res.resume_used ? " using the tailored resume" : " (JD + profile only)")
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
    if (!tailorResult?.ok) {
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
    <div className="modal-backdrop" onClick={close}>
      <div className="modal manual-apply-modal" onClick={(e) => e.stopPropagation()}>
        <div className="manual-apply-head">
          <h3 style={{ margin: 0 }}>Manual apply · {profileName}</h3>
          <button type="button" onClick={close}>
            Close
          </button>
        </div>
        <p className="muted">
          Uses the active Working as profile ({profileName}). Paste only the JD if you want —
          company, title, and URL are extracted from it. Resume files save under{" "}
          <code>data/applications/&lt;profile&gt;/&lt;seq&gt;-&lt;company&gt;/</code>.
        </p>

        {error ? <div className="error-banner">{error}</div> : null}
        {status ? (
          <div className="batch-progress" style={{ whiteSpace: "pre-wrap" }}>
            {status}
          </div>
        ) : null}

        <label>
          Job description (required)
          <textarea
            rows={9}
            value={form.description}
            disabled={Boolean(busy)}
            onChange={(e) => setField("description", e.target.value)}
            placeholder="Paste the full JD here. Company / title / URL will be filled from this text."
          />
        </label>

        <div className="manual-apply-grid">
          <label>
            Company (auto)
            <input
              value={form.company}
              disabled={Boolean(busy)}
              onChange={(e) => setField("company", e.target.value)}
              placeholder="Extracted from JD"
            />
          </label>
          <label>
            Title (auto)
            <input
              value={form.title}
              disabled={Boolean(busy)}
              onChange={(e) => setField("title", e.target.value)}
              placeholder="Extracted from JD"
            />
          </label>
        </div>

        <label>
          Job / apply URL (auto)
          <input
            value={form.url}
            disabled={Boolean(busy)}
            onChange={(e) => {
              setField("url", e.target.value);
              setUrlInferred(false);
            }}
            placeholder="Extracted from JD when present"
          />
        </label>
        {urlInferred ? (
          <p className="muted">No URL found in JD — placeholder set. Edit before Assisted apply.</p>
        ) : null}

        <label>
          Location (auto)
          <input
            value={form.location}
            disabled={Boolean(busy)}
            onChange={(e) => setField("location", e.target.value)}
            placeholder="Remote, US"
          />
        </label>

        <div className="modal-actions">
          <button type="button" disabled={Boolean(busy)} onClick={prepareJob}>
            {busy === "prepare" ? "Saving…" : "Add to job list"}
          </button>
          <button
            type="button"
            className="primary"
            disabled={Boolean(busy)}
            onClick={generateResume}
          >
            {busy === "tailor" ? "Generating resume…" : "Generate resume"}
          </button>
          <button type="button" disabled={Boolean(busy) || !job} onClick={openJobPage}>
            Open job page
          </button>
          <button
            type="button"
            className="primary"
            disabled={Boolean(busy) || !job || !tailorResult?.ok}
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
                Resume folder: <span className="detail-mono">{resumeFolder}</span>
              </>
            ) : (
              <>
                <br />
                Resume will save under{" "}
                <span className="detail-mono">
                  data/applications/{activePersonaId}/{job.seq_no}-…
                </span>
              </>
            )}
          </p>
        ) : null}

        <label>
          Application questions
          <textarea
            rows={7}
            value={questionsText}
            disabled={Boolean(busy)}
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
            disabled={Boolean(busy)}
            onClick={generateAnswers}
          >
            {busy === "answers" ? "Generating answers…" : "Generate answers"}
          </button>
          {answers.length ? (
            <button type="button" disabled={Boolean(busy)} onClick={copyAll}>
              Copy all Q&A
            </button>
          ) : null}
        </div>

        {answers.length ? (
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
        ) : null}
      </div>
    </div>
  );
}
