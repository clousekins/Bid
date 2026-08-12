import { useEffect, useRef, useState } from "react";
import { personaInitials, shortPersona } from "../utils/persona.js";

export default function WorkspaceHeader({ personas, workingPersonaId, onSwitch }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const closeTimer = useRef(null);

  const active = personas.find((p) => p.id === workingPersonaId);
  const displayName = active?.display_name || shortPersona(workingPersonaId) || "Profile";

  const clearClose = () => {
    if (closeTimer.current) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  };

  const scheduleClose = () => {
    clearClose();
    closeTimer.current = setTimeout(() => setOpen(false), 140);
  };

  useEffect(() => () => clearClose(), []);

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
    <header className="workspace-header">
      <div className="workspace-brand">
        <div className="brand-mark-wrap" aria-label="POONG">
          <span className="brand-heart brand-heart-a" aria-hidden>
            ♥
          </span>
          <span className="brand-heart brand-heart-b" aria-hidden>
            ♥
          </span>
          <span className="brand-heart brand-heart-c" aria-hidden>
            ♥
          </span>
          <h1 className="brand-mark">
            {"POONG".split("").map((ch, i) => (
              <span key={`${ch}-${i}`} className="brand-letter" style={{ "--i": i }}>
                {ch}
              </span>
            ))}
          </h1>
          <span className="brand-bloom" aria-hidden />
        </div>
        <p className="brand-tag">I will always be with you.</p>
      </div>

      <div
        className={`working-as-compact ${open ? "open" : ""}`}
        ref={wrapRef}
        onMouseEnter={() => {
          clearClose();
          setOpen(true);
        }}
        onMouseLeave={scheduleClose}
      >
        <button
          type="button"
          className="working-as-trigger"
          aria-haspopup="listbox"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="working-as-eyebrow">Working as</span>
          <strong className="working-as-name">{displayName}</strong>
          <span className="working-as-caret" aria-hidden>
            ▾
          </span>
        </button>

        {open ? (
          <div className="working-as-menu" role="listbox" aria-label="Choose profile">
            <p className="working-as-menu-hint">
              Generating, applying, and filters use this profile
            </p>
            {personas.map((p) => {
              const selected = p.id === workingPersonaId;
              const label = p.display_name || shortPersona(p.id);
              return (
                <button
                  key={p.id}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  className={`working-as-option ${selected ? "active" : ""}`}
                  onClick={() => {
                    onSwitch(p.id);
                    setOpen(false);
                  }}
                >
                  <span className="persona-pill-initials">{personaInitials(p.id)}</span>
                  <span className="working-as-option-text">
                    <span className="working-as-option-name">{label}</span>
                    <span className="working-as-option-id">{p.id}</span>
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
    </header>
  );
}
