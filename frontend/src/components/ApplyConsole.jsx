export default function ApplyConsole({
  session,
  onResume,
  onConfirm,
  onClose,
}) {
  const idle = !session || session.status === "none";

  return (
    <div className="panel apply-panel work-panel">
      <div className="panel-head">
        <h2>Apply console{!idle ? ` — job ${session.job_id}` : ""}</h2>
        {!idle ? <span className={`pill ${session.status}`}>{session.status}</span> : null}
      </div>

      {idle ? (
        <p className="muted">No active apply session.</p>
      ) : (
        <>
          <div className="actions">
            <button type="button" className="primary" onClick={onResume}>
              Resume after bot check
            </button>
            <button type="button" onClick={onConfirm}>
              Confirm submit (retry)
            </button>
            <button type="button" className="danger" onClick={onClose}>
              Close session
            </button>
          </div>
          <p className="muted hint">
            Confirm only clicks Submit after fill. Use Mark applied after the ATS shows success.
          </p>
          <div className="console">
            {(session.logs || []).map((line, i) => (
              <div key={`${line.ts}-${i}`} className={`line ${line.level === "error" ? "error" : ""}`}>
                [{line.ts}] {line.message}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
