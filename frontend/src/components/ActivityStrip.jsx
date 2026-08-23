import { useEffect, useState } from "react";

function tone(message, busy) {
  if (!message || message === "\u00a0") return busy ? "running" : "idle";
  const m = String(message).toLowerCase();
  if (m.includes("fail") || m.includes("error") || m.includes("deleted")) return "warn";
  if (m.includes("ok") || m.includes("ready") || m.includes("marked applied") || m.includes("renumbered")) {
    return "success";
  }
  if (busy || m.includes("queued") || m.includes("running") || m.includes("generating")) return "running";
  return "info";
}

export default function ActivityStrip({ message, busy, onDismiss }) {
  const [visible, setVisible] = useState(false);
  const text = (message || "").trim();
  const kind = tone(text, busy);

  useEffect(() => {
    setVisible(Boolean(text) || busy);
  }, [text, busy]);

  if (!visible) return null;

  const label =
    kind === "running"
      ? busy
        ? "Working…"
        : "Activity"
      : kind === "success"
        ? "Done"
        : kind === "warn"
          ? "Notice"
          : "Status";

  return (
    <div className={`activity-strip ${kind}`} role="status" aria-live="polite">
      <div className="activity-strip-main">
        {kind === "running" ? <span className="activity-spinner" aria-hidden /> : null}
        <span className="activity-strip-label">{label}</span>
        <span className="activity-strip-msg">{text || (busy ? "Background job in progress…" : "")}</span>
      </div>
      {!busy && text ? (
        <button
          type="button"
          className="activity-strip-dismiss"
          aria-label="Dismiss"
          onClick={() => {
            setVisible(false);
            onDismiss?.();
          }}
        >
          ×
        </button>
      ) : null}
    </div>
  );
}
