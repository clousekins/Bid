import { personaInitials, shortPersona } from "../utils/persona.js";
import ThemeToggle from "./ThemeToggle.jsx";

const NAV = [
  {
    id: "top",
    label: "Overview",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1v-9.5Z" />
      </svg>
    ),
  },
  {
    id: "workflow",
    label: "Workflow",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8Z" />
      </svg>
    ),
  },
  {
    id: "jobs",
    label: "Jobs",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden>
        <path d="M4 6h16M4 12h16M4 18h10" />
      </svg>
    ),
  },
  {
    id: "detail",
    label: "Detail",
    icon: (
      <svg viewBox="0 0 24 24" aria-hidden>
        <rect x="5" y="3" width="14" height="18" rx="2" />
        <path d="M9 8h6M9 12h6M9 16h4" />
      </svg>
    ),
  },
];

export default function SideRail({
  workingPersonaId,
  workingName,
  onNavigate,
  onToggleHero,
  heroCollapsed,
  onGoHome,
  theme,
  onToggleTheme,
}) {
  return (
    <aside className="side-rail" aria-label="Primary navigation">
      <button type="button" className="side-rail-brand side-rail-home" onClick={onGoHome} title="Back to home">
        <span className="side-rail-logo">P</span>
        <span className="side-rail-name">POONG</span>
      </button>

      <nav className="side-rail-nav">
        {NAV.map((item) => (
          <button
            key={item.id}
            type="button"
            className="side-rail-link"
            title={item.label}
            onClick={() => onNavigate?.(item.id)}
          >
            <span className="side-rail-icon">{item.icon}</span>
            <span className="side-rail-label">{item.label}</span>
          </button>
        ))}
      </nav>

      <div className="side-rail-foot">
        {onToggleTheme ? (
          <ThemeToggle theme={theme} onToggle={onToggleTheme} className="side-rail-theme" />
        ) : null}
        <button
          type="button"
          className="side-rail-link subtle"
          onClick={onToggleHero}
          title={heroCollapsed ? "Show hero showcase" : "Hide hero showcase"}
        >
          <span className="side-rail-icon" aria-hidden>
            {heroCollapsed ? (
              <svg viewBox="0 0 24 24">
                <rect x="3" y="5" width="18" height="14" rx="2" />
                <path d="M8 12h8" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24">
                <rect x="3" y="5" width="18" height="14" rx="2" />
                <path d="M8 12h8M12 8v8" />
              </svg>
            )}
          </span>
          <span className="side-rail-label">{heroCollapsed ? "Showcase" : "Hide hero"}</span>
        </button>
        {workingPersonaId ? (
          <div className="side-rail-persona" title={workingPersonaId}>
            <span className="persona-pill-initials">{personaInitials(workingPersonaId)}</span>
            <span className="side-rail-persona-name">
              {workingName || shortPersona(workingPersonaId)}
            </span>
          </div>
        ) : null}
      </div>
    </aside>
  );
}
