// The pipeline's own words for why a job stopped. They are precise, and they
// are not English — "duplicate_url" shown to someone reading a list of
// results looks like an error when it means "you already have this one".
const REASON_LABELS = {
  duplicate_url: "already have",
  duplicate_external_id: "already have",
  already_seen: "already have",
  company_already_applied: "applied there",
  company_blocked: "blocked",
  hybrid_or_onsite: "not remote",
  not_remote: "not remote",
  not_us: "not US",
  non_us_only: "not US",
  not_it_domain: "off domain",
  junior_title: "too junior",
  role_mismatch: "other title",
  missing_fields: "incomplete",
  skipped: "skipped",
};

export function reasonLabel(reason) {
  if (!reason) return "skipped";
  return REASON_LABELS[reason] || String(reason).replace(/_/g, " ");
}
