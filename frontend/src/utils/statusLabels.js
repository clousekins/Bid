export const PACKAGE_STATUS_LABELS = {
  need_resume: "Need resume",
  resume_ready: "Ready",
  applying: "Applying",
  applied: "Applied",
  failed: "Failed",
};

export const PACKAGE_STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "need_resume", label: "Need resume" },
  { value: "resume_ready", label: "Ready" },
  { value: "applying", label: "Applying" },
  { value: "applied", label: "Applied" },
  { value: "failed", label: "Failed" },
];

export const PIPELINE_OPTIONS = [
  { value: "", label: "Any pipeline" },
  { value: "new", label: "New" },
  { value: "jd_ready", label: "JD ready" },
  { value: "scan_failed", label: "Scan failed" },
  { value: "needs_manual", label: "Needs manual" },
  { value: "interview", label: "Interview" },
  { value: "skipped", label: "Skipped" },
  { value: "rejected", label: "Rejected" },
  { value: "blocked", label: "Blocked" },
  { value: "expired", label: "Expired" },
];

export const PIPELINE_LABELS = Object.fromEntries(
  PIPELINE_OPTIONS.filter((o) => o.value).map((o) => [o.value, o.label])
);

/** Pipeline gates worth showing inline next to Status in the table. */
export const PIPELINE_INLINE = new Set([
  "scan_failed",
  "needs_manual",
  "interview",
  "skipped",
  "rejected",
  "blocked",
  "expired",
]);

export function packageStatusLabel(status) {
  if (!status) return PACKAGE_STATUS_LABELS.need_resume;
  return PACKAGE_STATUS_LABELS[status] || status.replace(/_/g, " ");
}

export function pipelineLabel(status) {
  if (!status) return "—";
  return PIPELINE_LABELS[status] || status.replace(/_/g, " ");
}
