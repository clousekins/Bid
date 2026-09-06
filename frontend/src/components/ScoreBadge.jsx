/**
 * A score is only useful if you can see why it is what it is, so every badge
 * carries its matched/missing terms in the tooltip rather than a bare number.
 */
function band(score) {
  if (score == null) return "none";
  if (score >= 85) return "strong";
  if (score >= 70) return "good";
  if (score >= 50) return "fair";
  return "weak";
}

function tooltip(label, detail) {
  if (!detail) return `${label}: not scored yet`;
  const lines = [`${label}: ${detail.score}/100`];
  if (detail.notes?.length) lines.push(...detail.notes);
  if (detail.matched?.length) lines.push(`Matched: ${detail.matched.join(", ")}`);
  if (detail.missing?.length) lines.push(`Missing: ${detail.missing.join(", ")}`);
  return lines.join("\n");
}

export default function ScoreBadge({ label, score, detail, compact = false }) {
  const value = score ?? detail?.score ?? null;
  return (
    <span
      className={`score-badge score-${band(value)} ${compact ? "score-compact" : ""}`}
      title={tooltip(label, detail ?? (value == null ? null : { score: value }))}
    >
      {!compact && <span className="score-label">{label}</span>}
      <span className="score-value">{value == null ? "—" : value}</span>
    </span>
  );
}

/** The full breakdown, for the detail panel where there is room to explain. */
export function ScoreDetail({ label, detail }) {
  if (!detail) return null;
  return (
    <div className="score-detail">
      <div className="score-detail-head">
        <ScoreBadge label={label} detail={detail} />
        {detail.notes?.length ? (
          <span className="score-note">{detail.notes[0]}</span>
        ) : null}
      </div>
      {detail.missing?.length ? (
        <div className="score-terms">
          <span className="score-terms-label">Missing</span>
          {detail.missing.slice(0, 10).map((t) => (
            <span key={t} className="score-chip score-chip-missing">
              {t}
            </span>
          ))}
        </div>
      ) : null}
      {detail.matched?.length ? (
        <div className="score-terms">
          <span className="score-terms-label">Matched</span>
          {detail.matched.slice(0, 10).map((t) => (
            <span key={t} className="score-chip score-chip-matched">
              {t}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
