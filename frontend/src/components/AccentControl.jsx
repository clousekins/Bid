import { useEffect, useState } from "react";
import { client } from "../api.js";

/**
 * The colour a generated resume is printed in.
 *
 * The swatches are served by the backend rather than hard-coded here, so the
 * palette has one definition and the picker cannot drift from what the
 * stylesheet substitution will accept.
 *
 * The choice applies at generation time, so it does not alter resumes already
 * on disk — the hint under the swatches says so, because picking a colour and
 * seeing an old preview unchanged otherwise reads as a bug.
 */
export default function AccentControl({ onChange }) {
  const [palette, setPalette] = useState([]);
  const [accent, setAccent] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    client
      .resumeAccent()
      .then((s) => {
        if (cancelled) return;
        setPalette(s.palette || []);
        setAccent(s.accent || null);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, []);

  const choose = async (value) => {
    // Clicking the colour it already is clears the choice, so there is a way
    // back to the stylesheet's own accent without a separate reset button.
    const next = accent && value.toUpperCase() === accent.toUpperCase() ? "" : value;
    setSaving(true);
    setError("");
    try {
      const saved = await client.setResumeAccent(next);
      setAccent(saved.accent || null);
      onChange?.(saved);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  if (!loaded || !palette.length) return null;

  const current = palette.find(
    (s) => accent && s.value.toUpperCase() === accent.toUpperCase()
  );

  return (
    <div className="accent-picker">
      <div className="accent-head">
        <span className="accent-title">Resume colour</span>
        <span className="accent-value muted">
          {current ? current.name : accent ? accent : "Stylesheet default"}
        </span>
      </div>

      <div className="accent-swatches" role="radiogroup" aria-label="Resume colour">
        {palette.map((swatch) => {
          const on = accent && swatch.value.toUpperCase() === accent.toUpperCase();
          return (
            <button
              key={swatch.value}
              type="button"
              role="radio"
              aria-checked={Boolean(on)}
              aria-label={swatch.name}
              title={`${swatch.name} · ${swatch.value}`}
              className={`accent-swatch${on ? " on" : ""}`}
              style={{ background: swatch.value }}
              disabled={saving}
              onClick={() => choose(swatch.value)}
            />
          );
        })}
      </div>

      <p className="accent-hint muted">
        Applied when a resume is generated. Resumes already on disk keep the colour
        they were made with.
      </p>
      {error ? <p className="error-text">{error}</p> : null}
    </div>
  );
}
