// Jobright's search reply, turned into rows.
//
// The shape this expects is what /swan/recommend/search answers with:
//
//   { success, result: { jobList: [ { jobResult, companyResult }, … ] } }
//
// but it accepts the inner pieces too — a bare jobList, a bare array of
// entries, a single entry — because what gets copied out of a network tab
// depends on which node in the panel was right-clicked. Anything that turns out
// to hold a job is read; anything else is reported as a count, not swallowed.

const MAX_DESCRIPTION = 20000;

const clean = (value) =>
  String(value == null ? "" : value)
    .replace(/\s+/g, " ")
    .trim();

/** Every {jobResult, companyResult} entry inside whatever was pasted. */
function entriesOf(parsed) {
  if (!parsed) return [];
  if (Array.isArray(parsed)) return parsed.flatMap(entriesOf);
  if (typeof parsed !== "object") return [];
  if (parsed.jobResult) return [parsed];
  for (const key of ["jobList", "jobs", "items", "data"]) {
    if (Array.isArray(parsed[key])) return parsed[key].flatMap(entriesOf);
  }
  if (parsed.result) return entriesOf(parsed.result);
  return [];
}

/** The JD, assembled from the fields Jobright splits it across. */
function descriptionOf(job, company) {
  const pieces = [job.jobSummary, job.jdResponsibilitySummary];
  for (const key of ["coreResponsibilities", "skillSummaries", "benefitsSummaries"]) {
    if (Array.isArray(job[key])) pieces.push(job[key].join("\n"));
  }
  const musts = job.qualifications && job.qualifications.mustHave;
  if (Array.isArray(musts)) pieces.push(musts.join("\n"));
  if (company.companyDesc) pieces.push(String(company.companyDesc));
  if (job.salaryDesc) pieces.push(`Compensation: ${job.salaryDesc}`);
  return pieces
    .filter(Boolean)
    .map((piece) => String(piece).trim())
    .join("\n\n")
    .slice(0, MAX_DESCRIPTION);
}

function rowOf(entry) {
  const job = entry.jobResult || {};
  const company = entry.companyResult || {};
  const title = clean(job.jobTitle || job.jobNlpTitle);
  const employer = clean(company.companyName);
  const id = clean(job.jobId);
  // Without these three there is no row to write: the importer needs a
  // company, a title and a URL, and the URL is built from the id.
  if (!title || !employer || !id) return null;

  const workModel = clean(job.workModel);
  let where = clean(job.jobLocation);
  if (workModel && where && !where.toLowerCase().includes(workModel.toLowerCase())) {
    where = `${where} (${workModel})`;
  }

  // Jobright's own match score, which sits on the entry beside the job rather
  // than inside it. Roughly 40–90 in practice. Absent when a payload came from
  // somewhere that does not rank — kept as null so "unscored" stays tellable
  // apart from "scored zero".
  const score = Number(entry.displayScore);

  return {
    company: employer,
    title,
    url: `https://jobright.ai/jobs/info/${id}`,
    apply_url: clean(job.applyLink || job.originalUrl),
    location: where,
    posted_at: clean(job.publishTime),
    seniority: clean(job.jobSeniority),
    employment_type: clean(job.employmentType),
    remote: job.isRemote === true || workModel.toLowerCase() === "remote" ? "yes" : "",
    salary: clean(job.salaryDesc),
    score: Number.isFinite(score) ? Math.round(score * 10) / 10 : null,
    source: "Jobright",
    external_id: id,
    description: descriptionOf(job, company),
  };
}

/**
 * Rows from one pasted payload, plus what was skipped and why.
 *
 * Throws only on text that is not JSON at all — a payload that parses but
 * holds no jobs is a result to report, not an error to raise.
 */
export function parseJobsJson(text) {
  const trimmed = String(text || "").trim();
  if (!trimmed) throw new Error("Nothing pasted.");

  let parsed;
  try {
    parsed = JSON.parse(trimmed);
  } catch (cause) {
    throw new Error(`That is not JSON (${cause.message}).`);
  }

  if (parsed && parsed.success === false) {
    // Their own error envelope. 41001/41002 are the session codes.
    throw new Error(`Jobright returned an error: ${parsed.errorMsg || parsed.errorCode}`);
  }

  const entries = entriesOf(parsed);
  const rows = [];
  let unusable = 0;
  for (const entry of entries) {
    const row = rowOf(entry);
    if (row) rows.push(row);
    else unusable += 1;
  }
  return { rows, found: entries.length, unusable };
}

/**
 * True when applying means going to LinkedIn.
 *
 * Jobright lists plenty of postings whose apply link lands on LinkedIn, and
 * those are a dead end here: the backend has no LinkedIn adapter, the JD fetch
 * cannot read them, and assisted apply cannot fill them. Only the link is
 * judged. A JD that merely says "follow us on LinkedIn" is not a LinkedIn job.
 */
export function isLinkedInRow(row) {
  const links = `${(row && row.apply_url) || ""} ${(row && row.url) || ""}`;
  return links.toLowerCase().includes("linkedin.com");
}
