export default function ApplyConsole({ session, job, onResume, onConfirm, onClose }) {
  const idle = !session || session.status === "none";
  const logs = session?.logs || [];

  if (idle) return null;

  const title = job?.company
    ? `#${job.seq_no} ${job.company}`
    : session?.job_id
      ? `Job ${session.job_id}`
      : "Apply session";

  return (
    <section className="apply-console">
      <div className="apply-console-head">
        <div>
          <h2>Apply session</h2>
          <p className="apply-console-sub">{title}</p>
        </div>
        <span className={`pill session-${session.status}`}>{session.status}</span>
      </div>

      <div className="apply-console-actions">
        <button type="button" className="primary" onClick={onResume}>
          Resume after bot check
        </button>
        <button type="button" onClick={onConfirm}>
          Confirm submit
        </button>
        <button type="button" className="ghost-btn danger-text" onClick={onClose}>
          Close
        </button>
      </div>

      <p className="apply-console-hint muted">
        Confirm only clicks Submit after fill. Mark applied once the ATS shows success.
      </p>

      <div className="log-panel" aria-label="Apply log">
        {logs.length ? (
          logs.map((line, i) => (
            <div
              key={`${line.ts}-${i}`}
              className={`log-line ${line.level === "error" ? "log-error" : ""}`}
            >
              <span className="log-ts">{line.ts}</span>
              <span className="log-msg">{line.message}</span>
            </div>
          ))
        ) : (
          <div className="log-line muted">Waiting for browser events…</div>
        )}
      </div>
    </section>
  );
}
