// A whole Greenhouse board, from Greenhouse's own board API.
//
// The board page renders its list client-side and embeds nothing useful, so
// scraping it would mean reading whatever markup their frontend happens to
// produce today. They publish the same list properly:
//
//   https://boards-api.greenhouse.io/v1/boards/<slug>/jobs
//
// One request, every opening, and fields that are theirs rather than ours —
// including company_name, which is the employer's real name ("Motive") where
// the URL only carries the board slug ("gomotive").
//
// That host sends no access-control-allow-origin, so a page cannot call it.
// The service worker can, with a host permission. See background.js.

export const BOARD_API = "https://boards-api.greenhouse.io/v1/boards";

const clean = (value) =>
  String(value == null ? "" : value)
    .replace(/\s+/g, " ")
    .trim();

/**
 * The board slug in a Greenhouse URL, from any of the shapes they use.
 *
 *   job-boards.greenhouse.io/gomotive               → gomotive
 *   job-boards.greenhouse.io/gomotive/jobs/8783131  → gomotive
 *   boards.greenhouse.io/gomotive/jobs/8783131      → gomotive
 *   .../embed/job_app?for=gomotive&token=8783131    → gomotive
 */
export function boardSlugFromUrl(href) {
  let url;
  try {
    url = new URL(href);
  } catch {
    return "";
  }
  if (!/(^|\.)greenhouse\.io$/i.test(url.hostname)) return "";

  const embedded = url.searchParams.get("for");
  if (embedded) return clean(embedded);

  const first = url.pathname.split("/").filter(Boolean)[0] || "";
  // "embed" is their form host, not a board; the slug is in the query there.
  if (!first || first.toLowerCase() === "embed") return "";
  return clean(first);
}

/** Is this posting remote, as far as its location line admits? */
function remoteFlag(location) {
  return /\bremote\b/i.test(location) ? "yes" : "";
}

/**
 * Board jobs as basket rows.
 *
 * List fields only — no description. The backend already fetches JDs from a
 * URL when it deep-scans, and asking this board for 142 descriptions is 142
 * requests for text that would be fetched again anyway.
 *
 * `titles` narrows by substring, case-insensitively. A board is everything the
 * company is hiring for, and most of it is not the job you are looking for:
 * this one carries 142 openings, of which the majority are sales.
 */
export function rowsFromBoard(payload, { titles = [] } = {}) {
  const jobs = Array.isArray(payload && payload.jobs) ? payload.jobs : [];
  const wanted = titles.map((t) => t.trim().toLowerCase()).filter(Boolean);

  const rows = [];
  let filtered = 0;
  let unusable = 0;

  for (const job of jobs) {
    const title = clean(job && job.title);
    const url = clean(job && job.absolute_url);
    const company = clean(job && job.company_name);
    if (!title || !url || !company) {
      unusable += 1;
      continue;
    }
    if (wanted.length && !wanted.some((t) => title.toLowerCase().includes(t))) {
      filtered += 1;
      continue;
    }

    const where = clean(job.location && job.location.name);
    rows.push({
      company,
      title,
      url,
      apply_url: url, // On Greenhouse the posting is the application form.
      location: where,
      posted_at: clean(job.first_published || job.updated_at),
      seniority: "",
      employment_type: "",
      remote: remoteFlag(where),
      salary: "",
      score: null,
      source: "Greenhouse",
      external_id: clean(job.id),
      description: "",
    });
  }

  return { rows, found: jobs.length, filtered, unusable };
}
