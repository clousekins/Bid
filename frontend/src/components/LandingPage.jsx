import { useEffect, useState } from "react";
import { HERO_SLIDES } from "../data/heroSlides.js";
import ThemeToggle from "./ThemeToggle.jsx";

const FEATURES = [
  {
    title: "Domain-aware discovery",
    body: "Filter fetches by AI, Data Engineering, Full Stack, Salesforce, and more — so your queue stays focused.",
  },
  {
    title: "Multi-persona tailoring",
    body: "Each identity gets its own prompt, CSS, and resume output. Switch profiles without leaving the workspace.",
  },
  {
    title: "Assisted apply",
    body: "Live browser control for forms, bot checks, and marking applied — with a full audit trail in one console.",
  },
  {
    title: "Pipeline you can trust",
    body: "Sort by domain, filter by status, deep-scan JDs, and track every step from listing to applied.",
  },
];

export default function LandingPage({ onEnter, theme, onToggleTheme }) {
  const [heroIndex, setHeroIndex] = useState(0);
  const hero = HERO_SLIDES[heroIndex] || HERO_SLIDES[0];

  useEffect(() => {
    const t = setInterval(() => {
      setHeroIndex((i) => (i + 1) % HERO_SLIDES.length);
    }, 6000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="landing-page">
      <header className="landing-header">
        <div className="landing-header-inner">
          <a href="#/" className="landing-brand" onClick={(e) => e.preventDefault()}>
            <span className="landing-logo">P</span>
            <span className="landing-brand-text">
              <strong>POONG</strong>
              <span className="muted">Remote US Apply</span>
            </span>
          </a>
          <nav className="landing-nav" aria-label="Landing">
            <a href="#features" className="landing-nav-link">
              Features
            </a>
            <a href="#showcase" className="landing-nav-link">
              Showcase
            </a>
            <ThemeToggle theme={theme} onToggle={onToggleTheme} className="landing-theme-toggle" />
            <button type="button" className="primary landing-cta-nav" onClick={onEnter}>
              Open workspace
            </button>
          </nav>
        </div>
      </header>

      <section className="landing-hero" aria-label="Introduction">
        <div className="landing-hero-bg">
          {HERO_SLIDES.map((s, i) => (
            <div key={s.id} className={`landing-hero-slide ${i === heroIndex ? "active" : ""}`}>
              <img src={s.image} alt="" className="landing-hero-img" loading={i === 0 ? "eager" : "lazy"} />
              <div className="landing-hero-scrim" />
            </div>
          ))}
        </div>
        <div className="landing-hero-inner">
          <div className="landing-hero-copy">
            <span className="landing-eyebrow">{hero.eyebrow}</span>
            <h1 className="landing-title">
              Your remote US job search,
              <em> orchestrated.</em>
            </h1>
            <p className="landing-lead">
              Discover roles, tailor resumes for every persona, and apply with assisted browser control —
              all in one production workspace built for serious candidates.
            </p>
            <div className="landing-hero-actions">
              <button type="button" className="primary landing-cta-main" onClick={onEnter}>
                Enter workspace
              </button>
              <a href="#features" className="landing-cta-secondary">
                See how it works
              </a>
            </div>
            <p className="landing-credit muted">{hero.credit}</p>
          </div>
          <div className="landing-hero-card">
            <span className="landing-card-label">{hero.eyebrow}</span>
            <h2 className="landing-card-title">{hero.title}</h2>
            <p className="landing-card-body">{hero.body}</p>
            <div className="landing-hero-dots" role="tablist" aria-label="Hero slides">
              {HERO_SLIDES.map((s, i) => (
                <button
                  key={s.id}
                  type="button"
                  role="tab"
                  aria-selected={i === heroIndex}
                  aria-label={`Slide ${i + 1}`}
                  className={i === heroIndex ? "landing-dot active" : "landing-dot"}
                  onClick={() => setHeroIndex(i)}
                />
              ))}
            </div>
          </div>
        </div>
      </section>

      <section id="features" className="landing-section">
        <div className="landing-section-head">
          <span className="landing-section-eyebrow">Why POONG</span>
          <h2>Built for real apply workflows</h2>
          <p className="muted">
            Not a toy dashboard — a full pipeline from ATS discovery through tailored resumes to assisted
            apply.
          </p>
        </div>
        <div className="landing-features">
          {FEATURES.map((f) => (
            <article key={f.title} className="landing-feature-card">
              <h3>{f.title}</h3>
              <p>{f.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="showcase" className="landing-section landing-showcase">
        <div className="landing-section-head">
          <span className="landing-section-eyebrow">Showcase</span>
          <h2>Real people. Real work. Real results.</h2>
          <p className="muted">Curated photography — bundled locally, no external image dependencies.</p>
        </div>
        <div className="landing-gallery">
          {HERO_SLIDES.map((s) => (
            <figure key={s.id} className="landing-gallery-item">
              <img src={s.image} alt="" loading="lazy" />
              <figcaption>
                <span className="landing-gallery-tag">{s.eyebrow}</span>
                <strong>{s.title}</strong>
              </figcaption>
            </figure>
          ))}
        </div>
      </section>

      <section className="landing-section landing-final-cta">
        <div className="landing-final-inner">
          <h2>Ready to run your pipeline?</h2>
          <p className="muted">Open the workspace, pick a persona, and fetch your first batch of roles.</p>
          <button type="button" className="primary landing-cta-main" onClick={onEnter}>
            Open workspace
          </button>
        </div>
      </section>

      <footer className="landing-footer">
        <span className="landing-footer-brand">POONG</span>
        <span className="muted">Remote US apply workspace</span>
      </footer>
    </div>
  );
}
