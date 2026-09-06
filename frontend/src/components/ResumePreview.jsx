import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { artifactUrl, client } from "../api.js";
import { personaLabel } from "../utils/persona.js";

function pickView(tab, files) {
  if (tab === "submission") {
    // Proof of what the form looked like when we submitted it.
    return files.submission_png ? { kind: "submission_png", mode: "image" } : null;
  }
  if (tab === "cover") {
    if (files.cover_pdf) return { kind: "cover_pdf", mode: "pdf" };
    if (files.cover_txt) return { kind: "cover_txt", mode: "text" };
    return null;
  }
  if (files.resume_pdf) return { kind: "resume_pdf", mode: "pdf" };
  if (files.resume_html) return { kind: "resume_html", mode: "html" };
  return null;
}

function Viewer({ jobId, personaId, packages, initialTab, onClose }) {
  const readyPackages = (packages || []).filter((p) => p.folder_path);
  const [activePersona, setActivePersona] = useState(personaId || readyPackages[0]?.persona_id || "");
  const [tab, setTab] = useState(initialTab || "resume");
  const [meta, setMeta] = useState(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");

  const files = meta?.files || {};
  const view = useMemo(() => pickView(tab, files), [tab, files]);
  const src =
    jobId && view && view.mode !== "text"
      ? artifactUrl(jobId, view.kind, activePersona, { v: files[view.kind]?.mtime })
      : "";
  const fileName = view ? files[view.kind]?.name : "";

  useEffect(() => {
    if (!jobId || !activePersona) return undefined;
    let cancelled = false;
    client
      .listArtifacts(jobId, activePersona)
      .then((data) => {
        if (!cancelled) setMeta(data);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e.message || e));
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, activePersona]);

  useEffect(() => {
    if (!jobId || !view || view.mode !== "text") {
      setText("");
      return undefined;
    }
    let cancelled = false;
    fetch(artifactUrl(jobId, view.kind, activePersona, { v: files[view.kind]?.mtime }))
      .then((res) => {
        if (!res.ok) throw new Error("Could not load cover letter");
        return res.text();
      })
      .then((body) => {
        if (!cancelled) setText(body);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e.message || e));
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, activePersona, view?.kind, view?.mode, files[view?.kind]?.mtime]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="doc-viewer-backdrop" onClick={onClose}>
      <div
        className="doc-viewer"
        role="dialog"
        aria-modal="true"
        aria-label={tab === "cover" ? "Cover letter preview" : "Resume preview"}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="doc-viewer-bar">
          <div className="doc-viewer-tabs">
            <button
              type="button"
              className={tab === "resume" ? "active" : ""}
              onClick={() => setTab("resume")}
            >
              Resume
            </button>
            <button
              type="button"
              className={tab === "cover" ? "active" : ""}
              onClick={() => setTab("cover")}
            >
              Cover letter
            </button>
            {files.submission_png ? (
              <button
                type="button"
                className={tab === "submission" ? "active" : ""}
                onClick={() => setTab("submission")}
              >
                Submission
              </button>
            ) : null}
          </div>
          <div className="doc-viewer-meta">
            {readyPackages.length > 1 ? (
              <select
                value={activePersona}
                onChange={(e) => setActivePersona(e.target.value)}
                aria-label="Preview profile"
              >
                {readyPackages.map((p) => (
                  <option key={p.persona_id} value={p.persona_id}>
                    {personaLabel(p.persona_id)}
                  </option>
                ))}
              </select>
            ) : (
              <span className="muted">{personaLabel(activePersona)}</span>
            )}
            {fileName ? <span className="doc-viewer-filename">{fileName}</span> : null}
          </div>
          <div className="doc-viewer-actions">
            {src ? (
              <a href={src} target="_blank" rel="noreferrer">
                Open
              </a>
            ) : null}
            {src ? (
              <a href={src} download={fileName || true}>
                Download
              </a>
            ) : null}
            <button type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </header>
        {error ? <div className="doc-viewer-error">{error}</div> : null}
        <div className="doc-viewer-stage">
          {!view && meta ? (
            <div className="doc-viewer-empty">
              {tab === "cover"
                ? "No cover letter file for this profile."
                : tab === "submission"
                  ? "No submission screenshot — captured on the next apply."
                  : "No resume file for this profile."}
            </div>
          ) : null}
          {!meta && !error ? <div className="doc-viewer-empty">Loading…</div> : null}
          {view?.mode === "text" ? <pre className="doc-viewer-text">{text || "—"}</pre> : null}
          {src && view?.mode === "image" ? (
            <img className="doc-viewer-image" src={src} alt="Submitted application" />
          ) : null}
          {src && view?.mode !== "image" ? (
            <iframe
              className="doc-viewer-frame"
              title={tab === "cover" ? "Cover letter" : "Resume"}
              src={src}
              sandbox={view?.mode === "html" ? "allow-same-origin" : undefined}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function ResumePreview({
  jobId,
  personaId,
  packages = [],
  generating = false,
  labels = { resume: "Preview resume", cover: "Preview cover letter" },
}) {
  const [openTab, setOpenTab] = useState(null);
  const ready = (packages || []).some(
    (p) =>
      (!personaId || p.persona_id === personaId) &&
      p.folder_path &&
      ["resume_ready", "applying", "applied"].includes(p.status)
  );
  const disabled = Boolean(generating) || !jobId || !ready;

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        title={disabled ? "Generate a resume first" : "Open the generated resume"}
        onClick={(e) => {
          e.stopPropagation();
          setOpenTab("resume");
        }}
      >
        {labels.resume}
      </button>
      <button
        type="button"
        disabled={disabled}
        title={disabled ? "Generate a resume first" : "Open the generated cover letter"}
        onClick={(e) => {
          e.stopPropagation();
          setOpenTab("cover");
        }}
      >
        {labels.cover}
      </button>
      {openTab && jobId
        ? createPortal(
            <Viewer
              jobId={jobId}
              personaId={personaId}
              packages={packages}
              initialTab={openTab}
              onClose={() => setOpenTab(null)}
            />,
            document.body
          )
        : null}
    </>
  );
}
