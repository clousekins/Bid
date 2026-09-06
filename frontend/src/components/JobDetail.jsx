import { useEffect, useState } from "react";
import { personaLabel } from "../utils/persona.js";
import { packageStatusLabel, pipelineLabel } from "../utils/statusLabels.js";
import ResumePreview from "./ResumePreview.jsx";

const OUTCOMES = [
  { value: "pending", label: "Waiting" },
  { value: "acknowledged", label: "Auto-reply only" },
  { value: "screen", label: "Recruiter screen" },
  { value: "interview", label: "Interview" },
  { value: "offer", label: "Offer" },
  { value: "rejected", label: "Rejected" },
  { value: "ghosted", label: "Ghosted" },
];

function OutcomeControl({ pkg, onSetOutcome }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const current = pkg?.outcome || "pending";

  const change = async (value) => {
    if (value === current) return;
    setBusy(true);
    setError("");
    try {
      await onSetOutcome({ outcome: value, package_id: pkg.id });
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="detail-row">
      <span className="detail-label">Outcome</span>
      <div className="detail-edit-wrap">
        <select
          className={`outcome-select outcome-${current}`}
          value={current}
          disabled={busy}
          onChange={(e) => change(e.target.value)}
        >
          {OUTCOMES.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        {pkg.applied_at ? (
          <span className="muted outcome-sent">
            sent {new Date(pkg.applied_at).toLocaleDateString()}
          </span>
        ) : null}
        {error ? <span className="error-text">{error}</span> : null}
      </div>
    </div>
  );
}

function EditableField({
  label,
  value,
  field,
  multiline = false,
  href = false,
  extra,
  onSave,
  busy,
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value || "");

  useEffect(() => {
    if (!editing) setDraft(value || "");
  }, [value, editing]);

  const save = async () => {
    const next = draft.trim();
    if (next === (value || "").trim()) {
      setEditing(false);
      return;
    }
    await onSave(field, next);
    setEditing(false);
  };

  return (
    <div className="detail-row">
      <span className="detail-label">{label}</span>
      <div className="detail-edit-wrap">
        {editing ? (
          <>
            {multiline ? (
              <textarea
                className="detail-edit-input"
                rows={2}
                value={draft}
                autoFocus
                disabled={busy}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setEditing(false);
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
                }}
              />
            ) : (
              <input
                className="detail-edit-input"
                value={draft}
                autoFocus
                disabled={busy}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") save();
                  if (e.key === "Escape") setEditing(false);
                }}
              />
            )}
            <button type="button" className="detail-edit-save" disabled={busy} onClick={save}>
              Save
            </button>
            <button type="button" disabled={busy} onClick={() => setEditing(false)}>
              Cancel
            </button>
          </>
        ) : (
          <>
            {href && value ? (
              <a
                className="detail-link cell-ellipsis"
                href={value}
                target="_blank"
                rel="noreferrer"
                title={value}
              >
                {value}
              </a>
            ) : (
              <span className="detail-value">
                {value || "—"}
                {extra}
              </span>
            )}
            <button
              type="button"
              className="detail-pencil"
              title={`Edit ${label.toLowerCase()}`}
              aria-label={`Edit ${label}`}
              disabled={busy}
              onClick={() => {
                setDraft(value || "");
                setEditing(true);
              }}
            >
              ✎
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export default function JobDetail({
  job,
  workingPersonaId,
  onGenerate,
  onAssistedApply,
  onOpenJd,
  onDeepScan,
  onSkip,
  onMarkApplied,
  onBlock,
  onDelete,
  onUpdateFields,
  onContinueManual,
  onSetOutcome,
  generating = false,
}) {
  const [saving, setSaving] = useState(false);

  if (!job) {
    return (
      <div className="panel detail detail-empty work-panel">
        <div className="panel-head">
          <h2>Detail</h2>
        </div>
        <p className="muted">Select a job from the list.</p>
      </div>
    );
  }

  const packages = job.packages || [];
  const workingPackage = packages.find((pkg) => pkg.persona_id === workingPersonaId);
  const workingLabel = personaLabel(workingPersonaId);
  const myStatus = workingPackage?.status || "need_resume";

  const saveField = async (field, next) => {
    if (!onUpdateFields) return;
    setSaving(true);
    try {
      await onUpdateFields({ [field]: next });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="panel detail work-panel">
      <div className="panel-head">
        <h2 className="detail-title">
          <span className="detail-seq">#{job.seq_no}</span> {job.company}
        </h2>
        <span className={`pill package-status status-strong ${myStatus}`}>
          {packageStatusLabel(myStatus)}
        </span>
        {generating ? <span className="pill generating">Generating</span> : null}
      </div>

      <div className="detail-body">
        <div className="detail-row">
          <span className="detail-label">Working as</span>
          <span className="detail-value">{workingLabel}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Pipeline</span>
          <span className={`pill ${job.status}`}>{pipelineLabel(job.status)}</span>
        </div>
        {workingPackage?.status === "applied" && onSetOutcome ? (
          <OutcomeControl pkg={workingPackage} onSetOutcome={onSetOutcome} />
        ) : null}
        <EditableField
          label="Company"
          field="company"
          value={job.company}
          extra={
            job.staffing_firm ? (
              <span className="muted"> (via {job.staffing_firm})</span>
            ) : null
          }
          onSave={saveField}
          busy={saving}
        />
        <EditableField
          label="Title"
          field="title"
          value={job.title}
          onSave={saveField}
          busy={saving}
        />
        <EditableField
          label="Location"
          field="location"
          value={job.location || ""}
          onSave={saveField}
          busy={saving}
        />
        <EditableField
          label="URL"
          field="url"
          value={job.url}
          href
          onSave={saveField}
          busy={saving}
        />
        <div className="detail-row">
          <span className="detail-label">Folder</span>
          <span className="detail-mono cell-ellipsis" title={job.folder_path || ""}>
            {job.folder_path || "—"}
          </span>
        </div>
        {packages.length ? (
          <div className="detail-packages">
            <span className="detail-label">Resumes</span>
            <ul className="package-list">
              {packages.map((pkg) => (
                <li key={pkg.id || pkg.persona_id} className="package-chip">
                  <span className="package-name">{personaLabel(pkg.persona_id)}</span>
                  <span className={`pill package-status ${pkg.status}`}>
                    {packageStatusLabel(pkg.status)}
                  </span>
                  <span
                    className="package-path cell-ellipsis"
                    title={pkg.notes || pkg.folder_path || ""}
                  >
                    {pkg.notes || pkg.folder_path || "—"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {job.notes ? (
          <div className="detail-row">
            <span className="detail-label">Notes</span>
            <span className="detail-notes">{job.notes}</span>
          </div>
        ) : null}
        <div className="detail-jd">
          <span className="detail-label">JD preview</span>
          <pre className="jd-preview">
            {(job.description || "").slice(0, 1200) || "No JD yet — use Fetch JD in Prep stage."}
          </pre>
        </div>
      </div>

      <div className="detail-actions">
        <div className="action-group primary-group">
          {onContinueManual ? (
            <button type="button" className="primary" onClick={onContinueManual}>
              Continue apply
            </button>
          ) : null}
          {myStatus === "need_resume" || job.status === "jd_ready" ? (
            <button type="button" className="primary" onClick={onGenerate} disabled={generating}>
              Generate for {workingLabel}
            </button>
          ) : null}
          {myStatus === "resume_ready" || myStatus === "applying" ? (
            <>
              <button type="button" className="primary" onClick={onAssistedApply}>
                Assisted apply
              </button>
              <ResumePreview
                jobId={job.id}
                personaId={workingPersonaId}
                packages={packages}
                generating={generating}
              />
            </>
          ) : null}
          {(job.status === "new" || job.status === "scan_failed") && onDeepScan ? (
            <button type="button" className="primary" onClick={onDeepScan}>
              Fetch JD
            </button>
          ) : null}
        </div>
        <div className="action-group secondary-group">
          <button type="button" onClick={onOpenJd}>
            Open JD
          </button>
          <button type="button" onClick={onMarkApplied}>
            Mark applied
          </button>
          <button type="button" onClick={onSkip}>
            Skip
          </button>
        </div>
        <div className="action-group danger-group-inline">
          <button type="button" className="danger" onClick={onBlock}>
            Block company
          </button>
          <button type="button" className="danger" onClick={onDelete}>
            Delete job
          </button>
        </div>
      </div>
    </div>
  );
}
