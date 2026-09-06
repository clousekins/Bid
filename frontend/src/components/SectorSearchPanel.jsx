import { useEffect, useState } from "react";
import { client } from "../api.js";

/**
 * Search one industry sector — "healthcare", "fintech" — across the public job
 * feeds and the ATS boards already in the catalog.
 *
 * Results are shown for review rather than imported automatically: a sector
 * search reaches wider than the curated boards, and the words that matched are
 * displayed so a wrong hit is obvious before anything enters the pipeline.
 */
export default function SectorSearchPanel({ onImport, busy }) {
  const [sector, setSector] = useState("");
  const [known, setKnown] = useState([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [picked, setPicked] = useState(() => new Set());
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    client
      .sectorList()
      .then((d) => setKnown(d.sectors || []))
      .catch(() => setKnown([]));
  }, []);

  const start = async () => {
    const term = sector.trim();
    if (!term) {
      setError("Type a sector first — for example healthcare.");
      return;
    }
    setRunning(true);
    setError("");
    setResult(null);
    setPicked(new Set());
    try {
      setResult(await client.sectorSearch({ sector: term, limit: 60 }));
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setRunning(false);
    }
  };

  const toggle = (url) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(url) ? next.delete(url) : next.add(url);
      return next;
    });

  const results = result?.results || [];
  const chosen = results.filter((r) => picked.has(r.url));

  const importPicked = async () => {
    if (!chosen.length || !onImport) return;
    setImporting(true);
    setError("");
    try {
      await onImport(chosen);
      setPicked(new Set());
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setImporting(false);
    }
  };

  return (
    <div className="sector-panel">
      <div className="sector-controls">
        <input
          className="sector-input"
          list="sector-known"
          placeholder="Your sector — healthcare, fintech, logistics…"
          value={sector}
          disabled={running || busy}
          onChange={(e) => setSector(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && start()}
        />
        <datalist id="sector-known">
          {known.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <button type="button" className="primary" disabled={running || busy} onClick={start}>
          {running ? "Searching…" : "Start"}
        </button>
        {chosen.length ? (
          <button type="button" disabled={importing} onClick={importPicked}>
            {importing ? "Adding…" : `Add ${chosen.length} to jobs`}
          </button>
        ) : null}
      </div>

      <p className="stage-hint muted">
        Searches Remotive, Jobicy, The Muse, Remote OK, Arbeitnow and Himalayas, plus the
        company ATS boards already in your catalog. Nothing is added until you pick it.
      </p>

      {error ? <p className="error-text">{error}</p> : null}

      {result ? (
        <div className="sector-results">
          <div className="sector-summary">
            <strong>{result.returned}</strong> in {result.sector}
            <span className="muted">
              {" "}
              · {result.counts?.fetched || 0} scanned · {result.counts?.off_sector || 0} wrong
              sector · {result.counts?.not_it || 0} not IT ·{" "}
              {result.counts?.not_remote_us || 0} not US-remote
            </span>
          </div>

          {results.length === 0 ? (
            <p className="muted">
              Nothing matched. The public feeds carry few roles in some sectors — try a
              broader word, or add company boards for that industry to the catalog.
            </p>
          ) : (
            <ul className="sector-list">
              {results.map((r) => (
                <li key={r.url} className={picked.has(r.url) ? "picked" : ""}>
                  <label>
                    <input
                      type="checkbox"
                      checked={picked.has(r.url)}
                      onChange={() => toggle(r.url)}
                    />
                    <span className="sector-company">{r.company}</span>
                    <span className="sector-title">{r.title}</span>
                  </label>
                  <div className="sector-meta">
                    <span className="sector-source">{r.source}</span>
                    {r.location ? <span className="muted">{r.location}</span> : null}
                    <a href={r.url} target="_blank" rel="noreferrer">
                      open
                    </a>
                  </div>
                  {r.matched_on?.length ? (
                    <div className="sector-matched">
                      {r.matched_on.map((m) => (
                        <span key={m} className="sector-term">
                          {m}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
