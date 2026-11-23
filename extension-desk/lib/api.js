// The Poong-Apply backend, called straight from the side panel.
//
// The panel is an extension page with host_permissions for the backend, so
// Chrome skips CORS and preflight here. That matters: the backend's token
// middleware answers a token-less OPTIONS preflight with a bare 401, which
// would break the same fetch made from a content script.

import { loadSettings } from "./settings.js";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  const { baseUrl, accessToken } = await loadSettings();
  const headers = { ...(options.headers || {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  let body = options.body;
  if (body !== undefined && typeof body !== "string") {
    body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }

  let resp;
  try {
    resp = await fetch(`${baseUrl}${path}`, { ...options, body, headers });
  } catch {
    // The backend is a local process started by hand, so a refused connection
    // is the usual failure. Name it rather than surfacing "Failed to fetch".
    throw new ApiError(`Cannot reach Poong-Apply at ${baseUrl}. Is the backend running?`, 0);
  }

  if (resp.status === 401 || resp.status === 403) {
    throw new ApiError("Rejected: check the access token in settings (gear icon).", resp.status);
  }
  if (options.raw) {
    if (!resp.ok) throw new ApiError(`HTTP ${resp.status}`, resp.status);
    return resp;
  }

  let payload = null;
  try {
    payload = await resp.json();
  } catch {
    payload = null;
  }
  if (!resp.ok) {
    const detail = payload && payload.detail;
    const text = typeof detail === "string" ? detail : detail && (detail.message || detail.reason);
    throw new ApiError(text || `HTTP ${resp.status}`, resp.status);
  }
  return payload;
}

function qs(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const api = {
  stats: () => request("/api/jobs/meta/stats"),
  personas: () => request("/api/settings/personas"),

  // `seq` searches by the persona's own job number; the text search on the
  // backend only covers company, title and the two URLs.
  listJobs: ({ personaId, q, seq, page = 1, pageSize = 50 }) =>
    request(
      `/api/jobs${qs({
        persona_id: personaId,
        q,
        seq_from: seq,
        seq_to: seq,
        page,
        page_size: pageSize,
      })}`
    ),
  getJob: (id) => request(`/api/jobs/${id}`),
  patchJob: (id, fields) => request(`/api/jobs/${id}`, { method: "PATCH", body: fields }),
  setStatus: (id, status, personaId) =>
    request(`/api/jobs/${id}/status`, {
      method: "PATCH",
      body: { status, persona_id: personaId || undefined },
    }),
  setOutcome: (id, outcome, packageId) =>
    request(`/api/jobs/${id}/outcome`, {
      method: "PATCH",
      body: { outcome, package_id: packageId || undefined },
    }),
  post: (id, action, body) =>
    request(`/api/jobs/${id}/${action}`, { method: "POST", body: body || {} }),
  deleteJob: (id) => request(`/api/jobs/${id}`, { method: "DELETE" }),
  fetchJd: (job) =>
    request("/api/manual-apply/fetch", {
      method: "POST",
      body: { persona_id: job.persona_id, job_id: job.id, url: job.url },
    }),

  artifacts: (id, personaId) => request(`/api/jobs/${id}/artifacts${qs({ persona_id: personaId })}`),
  artifactResponse: (id, kind) => request(`/api/jobs/${id}/artifacts/${kind}`, { raw: true }),
};

/**
 * A plain URL for one generated file, with the token in the query string.
 *
 * Used where no header can be attached: a new tab, and the DownloadURL a drag
 * hands to the OS file manager. The backend accepts ?token= for exactly this.
 */
export async function artifactHref(id, kind, mtime) {
  const { baseUrl, accessToken } = await loadSettings();
  return `${baseUrl}/api/jobs/${id}/artifacts/${kind}${qs({ token: accessToken, v: mtime })}`;
}
