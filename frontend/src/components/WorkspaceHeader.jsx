import { useEffect, useRef, useState } from "react";
import { personaInitials, shortPersona } from "../utils/persona.js";

export default function WorkspaceHeader({
  personas,
  workingPersonaId,
  onSwitch,
  stats,
  onRefresh,
  onResetToday,
  busy,
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);

  const active = personas.find((p) => p.id === workingPersonaId);
  const displayName = active?.display_name || shortPersona(workingPersonaId) || "Profile";
  const profileCounts = stats?.by_profile?.[workingPersonaId] || {};
  const ready = Number(profileCounts.resume_ready || 0) + Number(profileCounts.applying || 0);
  const applied = Number(profileCounts.applied || 0);
  const total = Number(stats?.total || 0);
  // Today's count is the number that tells you whether to keep going.
  const todayAll = Number(stats?.applied_today || 0);
  const todayMine = Number(stats?.applied_today_by_profile?.[workingPersonaId] || 0);
  // When the counter started. Shown on the reset button so the number has a
  // reference point rather than being a bare figure you have to trust.
  const countingSince = (() => {
    const raw = stats?.today_started_at;
    if (!raw) return "the start of today";
    const when = new Date(raw);
    if (Number.isNaN(when.getTime())) return "the start of today";
    return stats?.today_reset_manually
      ? when.toLocaleString()
      : `midnight (${when.toLocaleDateString()})`;
  })();

  // Ctrl+R resets the counter instead of reloading the page. Worth knowing
  // that this takes a browser shortcut away from this tab; Ctrl+Shift+R and
  // F5 still reload, so there is a way out.
  useEffect(() => {
    if (!onResetToday) return undefined;
    const onKey = (e) => {
      if (!(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return;
      if (String(e.key).toLowerCase() !== "r") return;
      e.preventDefault();
      onResetToday();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onResetToday]);

  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => {
      if (!wrapRef.current?.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <header className="app-header">
      <div className="app-header-brand">
        <h1 className="app-logo">POONG</h1>
        <p className="app-tagline">Remote US apply console</p>
      </div>

      <div className="app-header-stats">
        <div className="stat-pill" title="Total job listings">
          <span className="stat-value">{total}</span>
          <span className="stat-label">Jobs</span>
        </div>
        <div className="stat-pill accent" title={`${displayName}: resume ready or applying`}>
          <span className="stat-value">{ready}</span>
          <span className="stat-label">Ready</span>
        </div>
        <div className="stat-pill success" title={`${displayName}: applied`}>
          <span className="stat-value">{applied}</span>
          <span className="stat-label">Applied</span>
        </div>
        <div
          className={`stat-pill today-stat ${todayMine ? "today-active" : ""}`}
          title={
            todayAll === todayMine
              ? `${displayName}: applications sent today`
              : `${displayName} sent ${todayMine} today — ${todayAll} across all profiles`
          }
        >
          <span className="stat-value">{todayMine}</span>
          <span className="stat-label">Today</span>
          {todayAll > todayMine ? (
            <span className="stat-sub">of {todayAll}</span>
          ) : null}
        </div>
        {stats?.ats ? (
          <div className="stat-pill muted-stat" title="Company ATS boards searched">
            <span className="stat-value">
              {stats.ats.checked}/{stats.ats.total}
            </span>
            <span className="stat-label">Companies</span>
          </div>
        ) : null}
        {onResetToday ? (
          <button
            type="button"
            className="stat-reset-btn"
            onClick={onResetToday}
            disabled={busy}
            title={`Start the Today count from now (Ctrl+R). Counting since ${countingSince}. Nothing is deleted.`}
          >
            Reset
          </button>
        ) : null}
      </div>

      <div className="app-header-actions">
        <button type="button" className="ghost-btn" disabled={busy} onClick={onRefresh} title="Refresh jobs">
          Refresh
        </button>
        <div className={`persona-switch ${open ? "open" : ""}`} ref={wrapRef}>
          <button
            type="button"
            className="persona-switch-trigger"
            aria-haspopup="listbox"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <span className="persona-pill-initials">{personaInitials(workingPersonaId)}</span>
            <span className="persona-switch-text">
              <span className="persona-switch-eyebrow">Working as</span>
              <strong>{displayName}</strong>
            </span>
            <span className="persona-switch-caret" aria-hidden>
              ▾
            </span>
          </button>
          {open ? (
            <div className="persona-switch-menu" role="listbox" aria-label="Choose profile">
              {personas.map((p) => {
                const selected = p.id === workingPersonaId;
                const label = p.display_name || shortPersona(p.id);
                return (
                  <button
                    key={p.id}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    className={`persona-switch-option ${selected ? "active" : ""}`}
                    onClick={() => {
                      onSwitch(p.id);
                      setOpen(false);
                    }}
                  >
                    <span className="persona-pill-initials">{personaInitials(p.id)}</span>
                    <span>
                      <span className="persona-option-name">{label}</span>
                      <span className="persona-option-id">{p.id}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}
