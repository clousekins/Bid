import { useEffect, useState } from "react";
import { client } from "../api.js";

/**
 * The minimum ATS score a generated resume has to reach.
 *
 * Below the floor the resume is regenerated with the missing keywords fed
 * back in. Each retry is a full LLM call, so the attempt cap is shown next to
 * the slider rather than buried — the cost of a high floor should be visible
 * at the moment you set it.
 */
export default function AtsFloorControl({ onChange }) {
  const [floor, setFloor] = useState(0);
  const [attempts, setAttempts] = useState(3);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    client
      .settings()
      .then((s) => {
        if (cancelled) return;
        setFloor(Number(s.min_ats_score || 0));
        setAttempts(Number(s.ats_max_attempts || 3));
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
    return () => {
      cancelled = true;
    };
  }, []);

  const persist = async (nextFloor, nextAttempts) => {
    setSaving(true);
    setError("");
    try {
      const saved = await client.setAtsFloor({
        min_ats_score: nextFloor,
        ats_max_attempts: nextAttempts,
      });
      setFloor(saved.min_ats_score);
      setAttempts(saved.ats_max_attempts);
      onChange?.(saved);
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setSaving(false);
    }
  };

  if (!loaded) return null;

  return (
    <div className="ats-floor">
      <div className="ats-floor-head">
        <label htmlFor="ats-floor-range" className="ats-floor-title">
          Minimum ATS score
        </label>
        <span className={`ats-floor-value ${floor ? "on" : "off"}`}>
          {floor ? floor : "Off"}
        </span>
      </div>

      <input
        id="ats-floor-range"
        className="ats-floor-range"
        type="range"
        min="0"
        max="100"
        step="1"
        value={floor}
        disabled={saving}
        onChange={(e) => setFloor(Number(e.target.value))}
        onMouseUp={(e) => persist(Number(e.target.value), attempts)}
        onKeyUp={(e) => persist(Number(e.target.value), attempts)}
        onTouchEnd={(e) => persist(Number(e.target.value), attempts)}
      />

      <div className="ats-floor-foot">
        <span className="muted">
          {floor === 0
            ? "Resumes are kept whatever they score."
            : `Resumes under ${floor} are regenerated with the missing keywords.`}
        </span>
        {floor > 0 ? (
          <label className="ats-floor-attempts">
            up to
            <select
              value={attempts}
              disabled={saving}
              onChange={(e) => persist(floor, Number(e.target.value))}
            >
              {[1, 2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
            tries
          </label>
        ) : null}
      </div>

      {floor >= 90 ? (
        <p className="ats-floor-warn">
          A floor this high is often unreachable — the resume can only use terms the
          profile can truthfully claim, so it will spend every retry and keep the best
          attempt.
        </p>
      ) : null}
      {error ? <p className="error-text">{error}</p> : null}
    </div>
  );
}
