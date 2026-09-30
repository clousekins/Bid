// The Poong-Apply backend, as much of it as this extension needs.
//
// Two calls: ask whether a URL is already on file, and post a job. The backend
// deduplicates by URL on its own — process_listing answers "duplicate_url" and
// hands back the row it already had — but it says so with the same 200 and the
// same job body as a fresh insert. So "already there" is a question worth
// asking before posting rather than guessing at afterwards.

import { loadSettings } from "./settings.js";

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const { baseUrl, accessToken } = await loadSettings();
  const headers = { ...(options.headers || {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (options.body) headers["Content-Type"] = "application/json";

  let resp;
  try {
    resp = await fetch(`${baseUrl}${path}`, { ...options, headers });
  } catch (cause) {
    // A refused connection is the normal case here — the backend is a local
    // process the user starts by hand — so name it rather than surfacing
    // "Failed to fetch".
    throw new ApiError(`Cannot reach Poong-Apply at ${baseUrl}. Is the backend running?`, 0);
  }

  if (resp.status === 401 || resp.status === 403) {
    throw new ApiError("Rejected: check the access token in the options page.", resp.status);
  }

  let payload = null;
  try {
    payload = await resp.json();
  } catch {
    payload = null;
  }

  if (!resp.ok) {
    const detail = payload && payload.detail;
    const text = typeof detail === "string" ? detail : detail && detail.message;
    throw new ApiError(text || `HTTP ${resp.status}`, resp.status);
  }
  return payload;
}

/**
 * The backend answers, at all? Used to show a live status line.
 *
 * `maxSeq` is the highest sequence number on file. Pushing reads it first and
 * compares afterwards: POST /api/jobs answers a duplicate with the same 200
 * and the same job body as a fresh insert, but a row that already existed
 * keeps its old, lower number. That is how a push can report what it actually
 * added without asking about every job twice.
 */
export async function ping() {
  const stats = await request("/api/jobs/meta/stats");
  return {
    total: Number(stats?.total || 0),
    appliedToday: Number(stats?.applied_today || 0),
    maxSeq: Number(stats?.max_seq || 0),
  };
}

/**
 * The job already on file at this URL, or null.
 *
 * The list endpoint's `q` searches company and title, not url — so this asks
 * for the company's jobs and compares URLs here. A company has tens of
 * postings at most, which one page covers.
 */
export async function findByUrl({ company, url }) {
  const wanted = normalizeUrl(url);
  if (!wanted) return null;
  const params = new URLSearchParams({ page_size: "200", hide_own_employers: "false" });
  if (company) params.set("q", company);
  const page = await request(`/api/jobs?${params}`);
  // The list endpoint names its array `jobs`; the others are defensive.
  const rows = Array.isArray(page?.jobs)
    ? page.jobs
    : Array.isArray(page?.items)
      ? page.items
      : Array.isArray(page)
        ? page
        : [];
  return (
    rows.find(
      (row) => normalizeUrl(row.url) === wanted || normalizeUrl(row.apply_url) === wanted
    ) || null
  );
}

export async function createJob(job) {
  const { personaId } = await loadSettings();
  const body = { ...job };
  if (personaId) body.persona_id = personaId;
  return request("/api/jobs", { method: "POST", body: JSON.stringify(body) });
}

/**
 * Two URLs for the same posting rarely match character for character. Tracking
 * parameters, a trailing slash and the scheme all vary between where a link was
 * found and where it was saved from.
 */
/**
 * The ChatGPT panel's backend calls. A job is a session: start it and get the
 * first prompt; post each reply and get the next prompt, or the outcome.
 */
export const gpt = {
  jobs({ persona_id, show, q, offset, limit }) {
    const params = new URLSearchParams();
    if (persona_id) params.set("persona_id", persona_id);
    if (show) params.set("show", show);
    if (q) params.set("q", q);
    if (offset) params.set("offset", String(offset));
    if (limit) params.set("limit", String(limit));
    return request(`/api/chatgpt/jobs?${params}`);
  },
  start({ job_id, persona_id }) {
    return request("/api/chatgpt/sessions", {
      method: "POST",
      body: JSON.stringify({ job_id, persona_id }),
    });
  },
  reply({ session, step, text }) {
    return request(`/api/chatgpt/sessions/${encodeURIComponent(session)}/reply`, {
      method: "POST",
      body: JSON.stringify({ step, text }),
    });
  },
  fail({ session, step, error }) {
    return request(`/api/chatgpt/sessions/${encodeURIComponent(session)}/fail`, {
      method: "POST",
      body: JSON.stringify({ step, error }),
    });
  },
  next({ session }) {
    return request(`/api/chatgpt/sessions/${encodeURIComponent(session)}/next`);
  },
  cancel({ session }) {
    return request(`/api/chatgpt/sessions/${encodeURIComponent(session)}/cancel`, {
      method: "POST",
    });
  },
};

export function normalizeUrl(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  try {
    const parsed = new URL(text);
    parsed.hash = "";
    parsed.protocol = "https:";
    parsed.hostname = parsed.hostname.replace(/^www\./, "").toLowerCase();
    for (const key of [...parsed.searchParams.keys()]) {
      if (/^(utm_|ref$|referer|source$|gh_src$|trackingid$)/i.test(key)) {
        parsed.searchParams.delete(key);
      }
    }
    return parsed.toString().replace(/\/$/, "");
  } catch {
    return text.replace(/\/$/, "");
  }
}

export { ApiError };
