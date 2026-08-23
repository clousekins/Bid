import { useEffect, useState } from "react";
import { HERO_SLIDES } from "../data/heroSlides.js";

const STORAGE_KEY = "poong.hero.collapsed";
const INTERVAL_MS = 7000;

export default function HeroShowcase({ stats, workingName, collapsed, onToggleCollapsed }) {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const slide = HERO_SLIDES[index] || HERO_SLIDES[0];

  useEffect(() => {
    if (collapsed || paused) return undefined;
    const t = setInterval(() => {
      setIndex((i) => (i + 1) % HERO_SLIDES.length);
    }, INTERVAL_MS);
    return () => clearInterval(t);
  }, [collapsed, paused]);

  if (collapsed) {
    return (
      <div className="hero-collapsed-bar">
        <button type="button" className="hero-expand-btn" onClick={onToggleCollapsed}>
          Show showcase
        </button>
        <span className="hero-collapsed-tagline">
          POONG · Remote US apply workspace
          {workingName ? ` · ${workingName}` : ""}
        </span>
        {stats ? (
          <span className="hero-collapsed-stat muted">{stats.total ?? 0} jobs in pipeline</span>
        ) : null}
      </div>
    );
  }

  return (
    <section
      className="hero-showcase"
      aria-label="Product showcase"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="hero-slides">
        {HERO_SLIDES.map((s, i) => (
          <div
            key={s.id}
            className={`hero-slide ${i === index ? "active" : ""}`}
            aria-hidden={i !== index}
          >
            <img src={s.image} alt="" className="hero-slide-img" loading={i === 0 ? "eager" : "lazy"} />
            <div className="hero-slide-scrim" />
          </div>
        ))}
      </div>

      <div className="hero-content">
        <div className="hero-copy">
          <span className="hero-eyebrow">{slide.eyebrow}</span>
          <h2 className="hero-title">{slide.title}</h2>
          <p className="hero-body">{slide.body}</p>
          <p className="hero-credit muted">{slide.credit}</p>
        </div>

        <div className="hero-metrics">
          <div className="hero-metric">
            <span className="hero-metric-value">{stats?.total ?? "—"}</span>
            <span className="hero-metric-label">Jobs</span>
          </div>
          <div className="hero-metric">
            <span className="hero-metric-value">{stats?.ats?.checked ?? "—"}</span>
            <span className="hero-metric-label">Companies searched</span>
          </div>
          <div className="hero-metric">
            <span className="hero-metric-value">{stats?.ats?.with_jobs ?? "—"}</span>
            <span className="hero-metric-label">Boards with roles</span>
          </div>
        </div>
      </div>

      <div className="hero-controls">
        <div className="hero-dots" role="tablist" aria-label="Showcase slides">
          {HERO_SLIDES.map((s, i) => (
            <button
              key={s.id}
              type="button"
              role="tab"
              aria-selected={i === index}
              aria-label={`Slide ${i + 1}: ${s.title}`}
              className={i === index ? "hero-dot active" : "hero-dot"}
              onClick={() => setIndex(i)}
            />
          ))}
        </div>
        <button type="button" className="hero-collapse-btn" onClick={onToggleCollapsed}>
          Hide showcase
        </button>
      </div>
    </section>
  );
}

export function loadHeroCollapsed() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function saveHeroCollapsed(collapsed) {
  try {
    localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    /* ignore */
  }
}
