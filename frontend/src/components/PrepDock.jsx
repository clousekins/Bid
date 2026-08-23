import { shortPersona } from "../utils/persona.js";

export default function PrepDock({ items, workingPersonaId, onOpen, onDismiss }) {
  if (!items?.length) return null;

  const running = items.filter((row) => row.status === "queued" || row.status === "running");
  const ready = items.filter((row) => row.status === "ready");
  const failed = items.filter((row) => row.status === "failed");

  return (
    <section className="prep-dock" aria-label="Background resume generation">
      <div className="prep-dock-head">
        <div>
          <h2>In flight</h2>
          <p className="prep-dock-copy muted">
            Background resume generation — click a card to continue in Manual apply.
          </p>
        </div>
        <div className="prep-dock-counts">
          {running.length ? <span className="prep-count running">{running.length} generating</span> : null}
          {ready.length ? <span className="prep-count ready">{ready.length} ready</span> : null}
          {failed.length ? <span className="prep-count failed">{failed.length} failed</span> : null}
        </div>
      </div>
      <div className="prep-dock-list">
        {items.map((row) => {
          const pct = row.pct || (row.status === "ready" ? 100 : 8);
          return (
            <article key={row.key} className={`prep-card ${row.status}`}>
              <button
                type="button"
                className="prep-card-main"
                onClick={() => onOpen(row)}
              >
                <div className="prep-card-top">
                  <strong>
                    #{row.seq} {row.company}
                  </strong>
                  <span className={`prep-state ${row.status}`}>
                    {row.status === "queued"
                      ? "Queued"
                      : row.status === "running"
                        ? "Generating"
                        : row.status === "ready"
                          ? "Resume ready"
                          : "Failed"}
                  </span>
                </div>
                <div className="prep-card-meta">
                  {row.title}
                  <span> · {shortPersona(row.personaId || workingPersonaId)}</span>
                </div>
                <div className="prep-bar" aria-hidden>
                  <span style={{ width: `${pct}%` }} />
                </div>
                {row.error ? <div className="prep-error">{row.error}</div> : null}
              </button>
              {row.status === "ready" || row.status === "failed" ? (
                <button
                  type="button"
                  className="prep-dismiss"
                  aria-label="Dismiss"
                  onClick={() => onDismiss(row.key)}
                >
                  ×
                </button>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
