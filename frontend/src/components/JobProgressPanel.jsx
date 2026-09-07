import { useEffect, useState } from "react";
import { shortPersona } from "../utils/persona.js";
import { reasonLabel } from "../utils/outcomes.js";

function statusTone(status, error) {
  if (error || status === "failed") return "failed";
  if (status === "completed" || status === "ready") return "success";
  if (status === "cancelled") return "warn";
  if (status === "queued" || status === "running") return "running";
  return "info";
}

function postedLabel(value) {
  if (!value) return { short: "", full: "" };
  // Board timestamps are UTC and mostly arrive without a zone marker.
  // "2026-09-06 23:22:36" read as local time is seven hours wrong here, which
  // is the difference between a job posted today and one posted yesterday.
  const iso = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return { short: "", full: "" };
  const mins = Math.round((Date.now() - at.getTime()) / 60000);
  const short =
    mins < 1
      ? "just now"
      : mins < 60
        ? `${mins}m ago`
        : mins < 60 * 24
          ? `${Math.round(mins / 60)}h ago`
          : at.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  // Shown in the reader's own timezone, which is what "last 24h" means to them.
  return { short, full: at.toLocaleString() };
}

function ProgressBar({ pct, indeterminate, tone }) {
  return (
    <div className={`job-progress-bar ${tone} ${indeterminate ? "indeterminate" : ""}`} aria-hidden>
      <span
        className="job-progress-bar-fill"
        style={indeterminate || pct == null ? undefined : { width: `${pct}%` }}
      />
    </div>
  );
}

export default function JobProgressPanel({
  activeJob,
  busy,
  prepItems = [],
  workingPersonaId,
  onStop,
  onDismissJob,
  onOpenPrep,
  onDismissPrep,
  onOpenJob,
}) {
  const [collapsed, setCollapsed] = useState(false);
  const prepRunning = prepItems.filter((r) => r.status === "queued" || r.status === "running");
  const prepReady = prepItems.filter((r) => r.status === "ready");
  const prepFailed = prepItems.filter((r) => r.status === "failed");
  const hasPrep = prepItems.length > 0;
  const hasJob = Boolean(activeJob);
  const visible = hasJob || hasPrep;

  useEffect(() => {
    if (busy || prepRunning.length) setCollapsed(false);
  }, [busy, prepRunning.length]);

  if (!visible) return null;

  const tone = statusTone(activeJob?.status, activeJob?.error);
  const showStop = busy && (activeJob?.status === "queued" || activeJob?.status === "running");
  const canDismissJob =
    activeJob && !busy && ["completed", "failed", "cancelled"].includes(activeJob.status);

  return (
    <aside
      className={`job-progress-panel ${collapsed ? "collapsed" : ""} ${tone}`}
      aria-live="polite"
      aria-label="Background activity"
    >
      <div className="job-progress-head">
        <div className="job-progress-head-main">
          {busy || prepRunning.length ? (
            <span className="job-progress-pulse" aria-hidden />
          ) : (
            <span className={`job-progress-dot ${tone}`} aria-hidden />
          )}
          <div className="job-progress-titles">
            <strong className="job-progress-title">
              {busy || prepRunning.length
                ? "Working in the background"
                : tone === "success"
                  ? "Finished"
                  : tone === "failed"
                    ? "Needs attention"
                    : "Activity"}
            </strong>
            <span className="job-progress-sub muted">
              You can keep browsing.
              {activeJob?.jobId ? ` · #${activeJob.jobId}` : ""}
            </span>
          </div>
        </div>
        <div className="job-progress-head-actions">
          {showStop ? (
            <button type="button" className="ghost-btn danger-text" onClick={onStop}>
              Stop
            </button>
          ) : null}
          <button
            type="button"
            className="ghost-btn"
            onClick={() => setCollapsed((v) => !v)}
            aria-expanded={!collapsed}
          >
            {collapsed ? "Expand" : "Minimize"}
          </button>
          {canDismissJob && !hasPrep ? (
            <button type="button" className="ghost-btn" onClick={onDismissJob} aria-label="Dismiss">
              ×
            </button>
          ) : null}
        </div>
      </div>

      {!collapsed ? (
        <div className="job-progress-body">
          {activeJob ? (
            <section className={`job-progress-card ${tone}`}>
              <div className="job-progress-card-top">
                <div>
                  <h3>{activeJob.title}</h3>
                  <p className="muted job-progress-status-line">
                    {activeJob.status === "running"
                      ? "Running"
                      : activeJob.status === "queued"
                        ? "Queued"
                        : activeJob.status === "completed"
                          ? "Completed"
                          : activeJob.status === "cancelled"
                            ? "Cancelled"
                            : activeJob.status === "failed"
                              ? "Failed"
                              : activeJob.status}
                    {activeJob.total != null
                      ? ` · ${activeJob.done}/${activeJob.total}`
                      : activeJob.indeterminate
                        ? " · estimating…"
                        : ""}
                    {activeJob.pct != null && !activeJob.indeterminate ? ` · ${activeJob.pct}%` : ""}
                  </p>
                </div>
                {canDismissJob ? (
                  <button type="button" className="linkish" onClick={onDismissJob}>
                    Dismiss
                  </button>
                ) : null}
              </div>

              <ProgressBar
                pct={activeJob.pct}
                indeterminate={activeJob.indeterminate || activeJob.status === "queued"}
                tone={tone}
              />

              {activeJob.sourceError ? (
                <p className="job-source-error" title={activeJob.sourceError}>
                  {activeJob.sourceError}
                </p>
              ) : null}

              {activeJob.metrics?.length ? (
                <div className="job-progress-metrics">
                  {activeJob.metrics.map((m) => (
                    <span key={`${m.label}-${m.value}`} className="job-metric-chip">
                      <em>{m.label}</em> {m.value}
                    </span>
                  ))}
                </div>
              ) : null}

              {activeJob.detail ? (
                <p className={`job-progress-detail ${activeJob.error ? "error" : "muted"}`}>
                  {activeJob.detail}
                </p>
              ) : null}

              {activeJob.results?.length ? (
                <div className="fetch-results">
                  <div className="fetch-results-head">
                    <h4>Jobs found</h4>
                    <span className="muted">
                      {activeJob.results.length} found · {activeJob.resultsAdded} new
                    </span>
                  </div>
                  {activeJob.csvName ? (
                    <p className="muted fetch-results-csv" title={activeJob.csvFile}>
                      Saved {activeJob.csvRows ?? activeJob.results.length} rows to
                      data/fetches/{activeJob.csvName}
                    </p>
                  ) : null}
                  <ol className="fetch-results-list">
                    {activeJob.results.map((row) => {
                      const posted = postedLabel(row.postedAt);
                      return (
                        <li key={row.key} className={row.ok ? "ok" : "past"}>
                          <button
                            type="button"
                            className="fetch-result-row"
                            disabled={!row.jobId}
                            title={posted.full ? `Posted ${posted.full}` : undefined}
                            onClick={() => row.jobId && onOpenJob?.(row.jobId)}
                          >
                            <span className="fetch-result-main">
                              <strong>{row.title}</strong>
                              <em>{row.company}</em>
                            </span>
                            <span className="fetch-result-meta">
                              {posted.short ? (
                                <span className="fetch-result-posted">{posted.short}</span>
                              ) : null}
                              <span className="fetch-result-state">
                                {row.ok
                                  ? row.seqNo
                                    ? `#${row.seqNo}`
                                    : "added"
                                  : reasonLabel(row.reason)}
                              </span>
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ol>
                </div>
              ) : null}
            </section>
          ) : null}

          {hasPrep ? (
            <section className="job-progress-prep">
              <div className="job-progress-prep-head">
                <h3>Resume generation</h3>
                <div className="job-progress-prep-counts">
                  {prepRunning.length ? (
                    <span className="prep-count running">{prepRunning.length} generating</span>
                  ) : null}
                  {prepReady.length ? (
                    <span className="prep-count ready">{prepReady.length} ready</span>
                  ) : null}
                  {prepFailed.length ? (
                    <span className="prep-count failed">{prepFailed.length} failed</span>
                  ) : null}
                </div>
              </div>
              <div className="job-progress-prep-list">
                {prepItems.map((row) => {
                  const pct = row.pct || (row.status === "ready" ? 100 : 8);
                  const rowTone = statusTone(row.status, row.error);
                  return (
                    <article key={row.key} className={`job-prep-row ${rowTone}`}>
                      <button
                        type="button"
                        className="job-prep-row-main"
                        onClick={() => onOpenPrep?.(row)}
                      >
                        <div className="job-prep-row-top">
                          <strong>
                            #{row.seq} {row.company}
                          </strong>
                          <span className={`prep-state ${row.status}`}>
                            {row.status === "queued"
                              ? "Queued"
                              : row.status === "running"
                                ? "Generating"
                                : row.status === "ready"
                                  ? "Ready"
                                  : "Failed"}
                          </span>
                        </div>
                        <div className="job-prep-row-meta muted">
                          {row.title}
                          <span> · {shortPersona(row.personaId || workingPersonaId)}</span>
                        </div>
                        <ProgressBar
                          pct={pct}
                          indeterminate={row.status === "queued"}
                          tone={rowTone}
                        />
                        {row.error ? <div className="prep-error">{row.error}</div> : null}
                      </button>
                      {row.status === "ready" || row.status === "failed" ? (
                        <button
                          type="button"
                          className="prep-dismiss"
                          aria-label="Dismiss"
                          onClick={() => onDismissPrep?.(row.key)}
                        >
                          ×
                        </button>
                      ) : null}
                    </article>
                  );
                })}
              </div>
            </section>
          ) : null}
        </div>
      ) : (
        <div className="job-progress-collapsed-bar">
          {activeJob ? (
            <ProgressBar
              pct={activeJob.pct}
              indeterminate={activeJob.indeterminate || activeJob.status === "queued"}
              tone={tone}
            />
          ) : prepRunning.length ? (
            <ProgressBar pct={null} indeterminate tone="running" />
          ) : (
            <ProgressBar pct={100} indeterminate={false} tone={tone} />
          )}
          <span className="muted">
            {activeJob
              ? `${activeJob.title}${activeJob.pct != null ? ` · ${activeJob.pct}%` : ""}`
              : prepRunning.length
                ? `${prepRunning.length} resume(s) generating`
                : prepReady.length
                  ? `${prepReady.length} resume(s) ready`
                  : "Activity"}
          </span>
        </div>
      )}
    </aside>
  );
}
