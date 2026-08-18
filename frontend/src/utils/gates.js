export function gateMessage(code, extra = {}) {
  const company = extra.company || "this company";
  switch (code) {
    case "company_already_applied":
      return `Already applied to ${company} as this Working-as profile.`;
    case "company_blocked":
      return `${company} is blocked.`;
    case "duplicate_url":
      return extra.seq
        ? `This URL is already job #${extra.seq}. Using that listing.`
        : "This URL is already in the job list.";
    case "already_has_jd":
      return "JD is already on file for this URL.";
    case "url_required":
      return "Paste a real http(s) job URL first.";
    case "login_wall":
      return "Fetch hit a login wall. Paste the JD instead.";
    case "too_short":
    case "browser_too_short":
      return "Fetched page was too short to be a JD. Paste the description.";
    case "hybrid_or_onsite":
    case "not_remote":
      return "Location gate: not fully remote US.";
    case "not_us":
    case "non_us_only":
      return "Location gate: not US-remote.";
    case "staffing_firm_resolved":
      return extra.company
        ? `Resolved client company: ${company}.`
        : "Staffing-firm listing resolved to the client company.";
    case "playwright_missing":
      return "Browser fetch is unavailable. HTTP fetch failed.";
    default:
      return String(code || "").replace(/_/g, " ");
  }
}

export function formatGateList(codes, extra = {}) {
  return (codes || [])
    .map((code) => gateMessage(code, extra))
    .filter(Boolean)
    .join(" ");
}
