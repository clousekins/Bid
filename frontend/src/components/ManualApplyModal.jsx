import { useEffect, useRef, useState } from "react";
import { client, logPoong, artifactUrl, getAuthToken } from "../api.js";
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

// Offline interview-answer prompt. The selected job's JD and the generated
// resume text are spliced into the {JD field} / {Resume field} placeholders.
const INTERVIEW_PROMPT = `You are ChatGPT, an AI assistant helping me to handle the interview.

I’ll give you a specific Job Description (JD) and my resume. You’ll generate **natural, human-sounding answers** to interview questions, using my real project experience — but match it flexibly to the JD requirements.

Here’s how you should answer:

🔹 First, understand the **intent** of the question — is it technical, behavioral, or casual?
🔹 Then, respond with a **natural, human-like length** — not too short, not too long.
  • For casual/simple questions: 1–2 real-sounding sentences is enough (Specifically, be ready for the questions related to my location given in the resume. ).
  • For deep/behavioral ones: use short storytelling (3–6 sentences).
  • If the question is technical, then you have to give me the one sentence first that clearly shows what the key points are, and then give me 2 or 3 sentences more, explaining the first sentence and answering to the core of the question.
  • And if the question is a coding assessment, then you have to give me the whole code for that question or the requirements first. And then you have to give me the explanation of WHY? I mean why did you choose that way in total flow or outline, and then give me the explanation for each step and the functions that you gave me, but here, what you have to care about is, give me the aligned code parts for that specific step or the function that you are mentioning.
  • If there are any additional or follow-up questions for the coding assessment, then you have to give me the same code parts that you provided in the original answer and start the explanation line by line and step by step, showing the details of each part (remember you have to render the code itself there too.).

🔹 **If the interviewer asks a challenge-related question** (like “what was your biggest challenge in your past experience?” or “what was the challenge in that specific project?”), you have to dive deeper:
  • Identify the project (based on the interview context until then) — mention the client, their business need, and our purpose in the project.
  • Talk about what the main challenge was and why it was tough.
  • Briefly mention other methods or techniques we considered, and why we chose the final solution.
  • Then, vividly explain how we implemented it, step by step, like a short story.
  • Keep it natural and show how I contributed and solved the challenge from a senior-level perspective.

🔹 Use a **casual, emotional tone**. Talk like me in a real interview — informal but confident. Use fillers like “um,” “you know,” “just wanna say,” etc.
🔹 Use **simple English** (B1–B2 level), except for technical terms — those should be accurate and real.
🔹 Always give answers in **first person**, like I’m saying it out loud.
🔹 And if you think it’s needed, pick one of my past projects that best matches the JD and the question, and explain about the project.
🔹 If the tech in the JD is different from the project, **adjust it naturally** to fit.
🔹 Show what I did — what I solved, improved, or led in Senior level perspective. Be specific!
🔹 If the question is fun or silly, give a second **optional humorous answer**, but only if it clearly fits.

🔹 And one more important thing: in conversational English, sentences are made up of **small units of speech** called “intonation units” or “thought groups.”
  These are short phrases that carry one main idea — like how people speak in real life, naturally pausing to breathe.
  For example:
  “Well, you know, //
  it’s kind of hard to say, //
  but I think //
  maybe we should wait and see. //”
  (Here, each phrase is short — and it sounds like actual speech, not long paragraphs.)
  Please do this for every answer you give me.
  Each line should be **short** and end with “//.”
  Make sure you add real line breaks between these lines, so that when I copy-paste, it shows each clause on its own separate line.
  Keep the length of each clause **short**, like how real people talk in conversation.

🔹 When the interviewer asks: “Do you have any questions?”
  • I’ll give you a number at the end (like 111, 222, or 333).
  • For 111 (manager/director):
    - Show real appreciation for the chance to speak with someone driving the company’s vision.
    - Mention how I respect their role in turning ideas into real-world success.
    - Say I don’t want to miss this chance, so I’d love to ask about their business, mission, or project vision.
    - Show that I’ve done my research — share a quick, senior-level insight I got about their work.
    - Then ask a thoughtful question about the business itself (not too technical), to show I’m genuinely interested.
  • For 222 (technical interviewer):
    - Appreciate their technical leadership and effort for the interview.
    - Mention how I know they’re working on some tricky or tough parts of the project or product.
    - Show I’ve researched their tech vision and the challenges.
    - Then ask a deep technical question about a tricky implementation or decision in their system, showing my senior-level curiosity and understanding.
  • For 333 (non-technical recruiter):
    - Keep it light — just express appreciation for the interview.
    - Ask about their team culture, the company’s future plans, or something that shows I’m keen to fit in and grow with them.

📌Here, I’ll send:
– The JD:
	{JD field}
– The Resume:
	{Resume field}
– The Extra conversations:

Please reply only with the final answer, in my voice — like I’m saying it naturally in the interview.`;

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
  // Offline (no-API) path: show the exact prompt to run by hand, and paste the
  // chat's output back to build the resume + cover letter.
  const [promptText, setPromptText] = useState("");
  const [promptMeta, setPromptMeta] = useState(null);
  const [promptOpen, setPromptOpen] = useState(false);
  const [buildOpen, setBuildOpen] = useState(false);
  const [buildText, setBuildText] = useState("");
  // A generic copyable panel reused by the interview-prompt and JD+Resume buttons.
  const [copyPanel, setCopyPanel] = useState({ open: false, title: "", text: "" });
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
      setPromptOpen(false);
      setPromptText("");
      setPromptMeta(null);
      setBuildOpen(false);
      setBuildText("");
      setCopyPanel({ open: false, title: "", text: "" });
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

  const showPrompt = async () => {
    setError("");
    let current = job;
    if (!current) {
      current = await prepareJob();
      if (!current) return;
    }
    setBusy("prompt");
    try {
      const res = await client.manualPrompt({
        persona_id: activePersonaId,
        job_id: current.id,
      });
      setPromptText(res.combined || "");
      setPromptMeta({ mode: res.mode, model: res.model });
      setPromptOpen(true);
      setStatus(
        `Prompt ready (${res.mode}, ${(res.combined || "").length} chars). ` +
          'Copy it, run it in your chat, then paste the output back with "Paste AI output".'
      );
    } catch (e) {
      setError(String(e.message || e));
      logPoong("manual-prompt fail", e);
    } finally {
      setBusy("");
    }
  };

  const copyPromptText = async () => {
    try {
      await navigator.clipboard.writeText(promptText || "");
      setStatus("Prompt copied to clipboard.");
    } catch {
      setError("Could not copy to clipboard");
    }
  };

  // Read the generated resume as plain text by rendering its HTML artifact
  // off-screen and pulling the visible text (keeps line breaks and bullets).
  const fetchResumeText = async () => {
    const token = getAuthToken();
    const res = await fetch(artifactUrl(job.id, "resume_html", activePersonaId), {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      credentials: "include",
    });
    if (!res.ok) throw new Error(`resume artifact HTTP ${res.status}`);
    const html = await res.text();
    const clean = html
      .replace(/<!DOCTYPE[^>]*>/i, "")
      .replace(/<head[\s\S]*?<\/head>/i, "")
      .replace(/<(script|style)[\s\S]*?<\/\1>/gi, "");
    const div = document.createElement("div");
    div.style.cssText = "position:absolute;left:-99999px;top:0;width:800px;white-space:pre-wrap;";
    div.innerHTML = clean;
    document.body.appendChild(div);
    const text = (div.innerText || div.textContent || "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
    document.body.removeChild(div);
    if (!text) throw new Error("resume text is empty");
    return text;
  };

  const openCopy = (title, text) => setCopyPanel({ open: true, title, text });
  const closeCopy = () => setCopyPanel({ open: false, title: "", text: "" });
  const copyCopyPanel = async () => {
    try {
      await navigator.clipboard.writeText(copyPanel.text || "");
      setStatus("Copied to clipboard.");
    } catch {
      setError("Could not copy to clipboard");
    }
  };

  const makeInterviewPrompt = async () => {
    setError("");
    const jd = (form.description || job?.description || "").trim();
    if (!jd) {
      setError("No JD yet — fetch or paste the job description first.");
      return;
    }
    if (!resumeReady) {
      setError("Generate the resume first — the interview prompt needs the resume text.");
      return;
    }
    setBusy("interview");
    try {
      const resumeText = await fetchResumeText();
      const text = INTERVIEW_PROMPT.replace("{JD field}", () => jd).replace(
        "{Resume field}",
        () => resumeText
      );
      openCopy("Interview answer prompt (JD + resume inserted)", text);
      setStatus(`Interview prompt ready (${text.length} chars). Copy it into ChatGPT.`);
    } catch (e) {
      setError(`Could not read the resume text: ${String(e.message || e)}`);
      logPoong("interview-prompt fail", e);
    } finally {
      setBusy("");
    }
  };

  const makeJdResume = async () => {
    setError("");
    const jd = (form.description || job?.description || "").trim();
    if (!jd) {
      setError("No JD yet — fetch or paste the job description first.");
      return;
    }
    if (!resumeReady) {
      setError("Generate the resume first — JD + Resume needs the resume text.");
      return;
    }
    setBusy("jdresume");
    try {
      const resumeText = await fetchResumeText();
      const rule = "=".repeat(40);
      const text = `JOB DESCRIPTION\n${rule}\n${jd}\n\n\nRESUME\n${rule}\n${resumeText}`;
      openCopy("JD + Resume", text);
      setStatus(`JD + Resume ready (${text.length} chars).`);
    } catch (e) {
      setError(`Could not read the resume text: ${String(e.message || e)}`);
      logPoong("jd-resume fail", e);
    } finally {
      setBusy("");
    }
  };

  const openBuild = async () => {
    setError("");
    let current = job;
    if (!current) {
      current = await prepareJob();
      if (!current) return;
    }
    setBuildOpen(true);
  };

  const submitBuild = async () => {
    setError("");
    if (!buildText.trim()) {
      setError("Paste the AI output (HTML resume + cover letter) first.");
      return;
    }
    if (!job?.id) {
      setError("Add the job to the list first.");
      return;
    }
    setBusy("build");
    try {
      const res = await client.manualBuild({
        persona_id: activePersonaId,
        job_id: job.id,
        text: buildText,
      });
      const full = await client.getJob(job.id);
      setJob(full);
      setTailorResult(
        tailorFromJob(full, activePersonaId) || {
          ok: true,
          job: full,
          resume_folder: res.folder,
        }
      );
      setBuildOpen(false);
      setBuildText("");
      setStatus(
        `Built resume for #${full.seq_no} ${full.company} from pasted output` +
          (res.cover_included ? " (with cover letter)" : " (no cover letter in paste)") +
          `. ATS ${res.ats_score}. Files are in the job folder.`
      );
      await onCreated?.(full);
    } catch (e) {
      const detail = String(e.message || e);
      if (
        detail.includes("tailor_html_missing") ||
        detail.includes("tailor_html_incomplete")
      ) {
        setError(
          "That paste did not contain a complete HTML resume. Copy the whole HTML " +
            "block the chat produced, from <!DOCTYPE html> through </html>, then the cover letter."
        );
      } else {
        setError(detail);
      }
      logPoong("manual-build fail", e);
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

        <div className="modal-actions" style={{ marginTop: 2 }}>
          <span className="muted" style={{ alignSelf: "center", marginRight: 4 }}>
            Offline (no API):
          </span>
          <button type="button" disabled={formBusy || generating} onClick={showPrompt}>
            {busy === "prompt" ? "Building prompt…" : "Show prompt"}
          </button>
          <button type="button" disabled={formBusy || generating} onClick={openBuild}>
            Paste AI output → build
          </button>
          <button
            type="button"
            disabled={formBusy || generating || !resumeReady}
            title={resumeReady ? "" : "Generate the resume first"}
            onClick={makeInterviewPrompt}
          >
            {busy === "interview" ? "Building…" : "Interview prompt"}
          </button>
          <button
            type="button"
            disabled={formBusy || generating || !resumeReady}
            title={resumeReady ? "" : "Generate the resume first"}
            onClick={makeJdResume}
          >
            {busy === "jdresume" ? "Building…" : "JD + Resume"}
          </button>
        </div>

        {copyPanel.open ? (
          <div className="manual-answers-panel" style={{ marginTop: 8 }}>
            <div className="manual-answers-panel-head">
              <strong>{copyPanel.title}</strong>
              <span className="muted">{copyPanel.text.length} chars</span>
            </div>
            <textarea
              readOnly
              rows={14}
              value={copyPanel.text}
              onFocus={(e) => e.target.select()}
              style={{
                width: "100%",
                fontFamily: "monospace",
                fontSize: 11,
                whiteSpace: "pre-wrap",
              }}
            />
            <div className="modal-actions">
              <button type="button" className="primary" onClick={copyCopyPanel}>
                Copy
              </button>
              <button type="button" onClick={closeCopy}>
                Hide
              </button>
            </div>
          </div>
        ) : null}

        {promptOpen ? (
          <div className="manual-answers-panel" style={{ marginTop: 8 }}>
            <div className="manual-answers-panel-head">
              <strong>Completed prompt (sent to generate resume + cover letter)</strong>
              <span className="muted">
                {promptMeta
                  ? `${promptMeta.mode} · model ${promptMeta.model} · ${promptText.length} chars`
                  : `${promptText.length} chars`}
              </span>
            </div>
            <textarea
              readOnly
              rows={12}
              value={promptText}
              onFocus={(e) => e.target.select()}
              style={{
                width: "100%",
                fontFamily: "monospace",
                fontSize: 11,
                whiteSpace: "pre",
              }}
            />
            <div className="modal-actions">
              <button type="button" className="primary" onClick={copyPromptText}>
                Copy prompt
              </button>
              <button type="button" onClick={() => setPromptOpen(false)}>
                Hide
              </button>
            </div>
          </div>
        ) : null}

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

      {buildOpen ? (
        <div className="modal-backdrop" style={{ zIndex: 60 }}>
          <div className="modal">
            <div className="manual-apply-head">
              <h3 style={{ margin: 0 }}>Paste AI output → build resume &amp; cover letter</h3>
              <button
                type="button"
                onClick={() => setBuildOpen(false)}
                disabled={busy === "build"}
              >
                Close
              </button>
            </div>
            <p className="muted">
              Paste the chat&apos;s full output here — the complete HTML resume (
              <span className="detail-mono">{"<!DOCTYPE html> … </html>"}</span>) followed by the
              plain-text cover letter. Build writes the resume, cover letter, and PDF into the job
              folder and marks it resume-ready. No API key is used.
            </p>
            {error ? <div className="error-banner">{error}</div> : null}
            <textarea
              rows={16}
              value={buildText}
              disabled={busy === "build"}
              onChange={(e) => setBuildText(e.target.value)}
              placeholder="Paste the complete AI output here (HTML resume, then the cover letter)…"
              style={{ width: "100%", fontFamily: "monospace", fontSize: 12 }}
            />
            <div className="modal-actions">
              <button
                type="button"
                className="primary"
                disabled={busy === "build" || !buildText.trim()}
                onClick={submitBuild}
              >
                {busy === "build" ? "Building…" : "Build resume & cover letter"}
              </button>
              <button
                type="button"
                disabled={busy === "build"}
                onClick={() => setBuildOpen(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
