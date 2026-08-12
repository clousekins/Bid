import { personaLabel } from "../utils/persona.js";
import { packageStatusLabel, pipelineLabel } from "../utils/statusLabels.js";

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
}) {
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

  return (
    <div className="panel detail work-panel">
      <div className="panel-head">
        <h2 className="detail-title">
          <span className="detail-seq">#{job.seq_no}</span> {job.company}
        </h2>
        <span className={`pill package-status status-strong ${myStatus}`}>
          {packageStatusLabel(myStatus)}
        </span>
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
        <div className="detail-row">
          <span className="detail-label">Company</span>
          <span className="detail-value">
            {job.company}
            {job.staffing_firm ? (
              <span className="muted"> (via {job.staffing_firm})</span>
            ) : null}
          </span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Title</span>
          <span className="detail-value">{job.title}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">Location</span>
          <span className="detail-value">{job.location || "—"}</span>
        </div>
        <div className="detail-row">
          <span className="detail-label">URL</span>
          <a
            className="detail-link cell-ellipsis"
            href={job.url}
            target="_blank"
            rel="noreferrer"
            title={job.url}
          >
            {job.url}
          </a>
        </div>
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
            {(job.description || "").slice(0, 800) || "No JD yet"}
          </pre>
        </div>
      </div>

      <div className="actions">
        <button type="button" className="primary" onClick={onGenerate}>
          Generate for {workingLabel}
        </button>
        <button type="button" onClick={onAssistedApply}>
          Apply as {workingLabel}
        </button>
        <button type="button" onClick={onOpenJd}>
          Open JD
        </button>
        <button type="button" onClick={onDeepScan}>
          Deep-scan one
        </button>
        <button type="button" onClick={onSkip}>
          Skip
        </button>
        <button type="button" onClick={onMarkApplied}>
          Mark {workingLabel} applied
        </button>
        <button type="button" className="danger" onClick={onBlock}>
          Block
        </button>
        <button type="button" className="danger" onClick={onDelete}>
          Delete
        </button>
      </div>
    </div>
  );
}
