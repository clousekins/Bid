function getToken() {
  const params = new URLSearchParams(window.location.search);
  const q = params.get("token");
  if (q) {
    localStorage.setItem("poong_token", q);
    return q;
  }
  return localStorage.getItem("poong_token") || "";
}

async function api(path, options = {}) {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
    delete options.json;
  }
  const res = await fetch(path, { ...options, headers, credentials: "include" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || JSON.stringify(data);
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const client = {
  health: () => api("/api/health"),
  jobCountries: () => api("/api/jobs/countries"),
  listJobs: (params = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, v);
    });
    return api(`/api/jobs?${q}`);
  },
  stats: () => api("/api/jobs/meta/stats"),
  createJob: (body) => api("/api/jobs", { method: "POST", json: body }),
  importJobs: async (file) => {
    const token = getToken();
    const fd = new FormData();
    fd.append("file", file);
    const headers = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch("/api/jobs/import", {
      method: "POST",
      headers,
      body: fd,
      credentials: "include",
    });
    if (!res.ok) {
      let detail = await res.text();
      try {
        const j = JSON.parse(detail);
        detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j);
      } catch {
        /* keep text */
      }
      throw new Error(detail);
    }
    return res.json();
  },
  getJob: (id, profile) => {
    const q = new URLSearchParams();
    if (profile) q.set("profile", profile);
    return api(`/api/jobs/${id}?${q}`);
  },
  getJobQa: (id, persona_id) =>
    api(`/api/jobs/${id}/qa?${new URLSearchParams({ persona_id })}`),
  listArtifacts: (id, persona_id) => {
    const q = new URLSearchParams();
    if (persona_id) q.set("persona_id", persona_id);
    return api(`/api/jobs/${id}/artifacts?${q}`);
  },
  deleteJob: (id) => api(`/api/jobs/${id}`, { method: "DELETE" }),
  deleteJobsRange: (body) =>
    api("/api/jobs/delete-range", { method: "POST", json: body }),
  deleteJobsIds: (body) =>
    api("/api/jobs/delete-ids", { method: "POST", json: body }),
  renumberJobs: () => api("/api/jobs/renumber", { method: "POST" }),
  patchStatus: (id, status, extra = {}) =>
    api(`/api/jobs/${id}/status`, {
      method: "PATCH",
      json: { status, ...extra },
    }),
  patchJob: (id, fields) =>
    api(`/api/jobs/${id}`, {
      method: "PATCH",
      json: fields,
    }),
  setOutcome: (id, body) =>
    api(`/api/jobs/${id}/outcome`, { method: "PATCH", json: body }),
  outcomeStats: (persona_id) => {
    const q = new URLSearchParams();
    if (persona_id) q.set("persona_id", persona_id);
    return api(`/api/jobs/meta/outcomes?${q}`);
  },
  blockCompany: (id) => api(`/api/jobs/${id}/block-company`, { method: "POST" }),
  deepScanOne: (id) => api(`/api/jobs/${id}/deep-scan`, { method: "POST" }),
  manualFetch: (body) => api("/api/manual-apply/fetch", { method: "POST", json: body }),
  tailorOne: (id, body = {}) =>
    api(`/api/jobs/${id}/tailor`, { method: "POST", json: body }),
  deepScanRange: (body) => api("/api/sync/deep-scan", { method: "POST", json: body }),
  discoverJobs: (body = {}) => api("/api/sync/discover", { method: "POST", json: body }),
  discoverFreshJobs: (body = {}) =>
    api("/api/sync/discover-fresh", { method: "POST", json: body }),
  discoverCompanies: (body = {}) =>
    api("/api/sync/discover-companies", { method: "POST", json: body }),
  discoverSite: (body) => api("/api/sync/discover-site", { method: "POST", json: body }),
  fetchSites: () => api("/api/sync/fetch-sites"),
  resetTodayCounter: () =>
    api("/api/jobs/meta/stats/reset-today", { method: "POST" }),
  sectorSearch: (body) =>
    api("/api/sync/sector-search", { method: "POST", json: body }),
  sectorList: () => api("/api/sync/sector-search/sectors"),
  atsCatalog: () => api("/api/sync/ats-catalog"),
  listAtsBoards: (params = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, v);
    });
    return api(`/api/sync/ats-catalog/boards?${q}`);
  },
  tailorRange: (body) => api("/api/sync/tailor", { method: "POST", json: body }),  deepScanIds: (job_ids) =>
    api("/api/sync/deep-scan", { method: "POST", json: { job_ids } }),
  tailorIds: (job_ids, persona_id) =>
    api("/api/sync/tailor", {
      method: "POST",
      json: { job_ids, persona_id },
    }),
  applyPreview: (body) => api("/api/sync/apply-preview", { method: "POST", json: body }),
  getBgJob: (id) => api(`/api/jobs-bg/${id}`),
  cancelBgJob: (id) => api(`/api/jobs-bg/${id}/cancel`, { method: "POST" }),
  applyStart: (id, opts = {}) => {
    const body =
      typeof opts === "boolean"
        ? { ui_opened_tab: opts }
        : {
            ui_opened_tab: opts.ui_opened_tab !== false,
            persona_id: opts.persona_id || undefined,
            package_id: opts.package_id || undefined,
          };
    return api(`/api/apply/${id}/start`, { method: "POST", json: body });
  },
  applyStatus: (id) => api(`/api/apply/${id}/status`),
  applyResume: (id) => api(`/api/apply/${id}/resume`, { method: "POST" }),
  applyConfirm: (id) => api(`/api/apply/${id}/confirm`, { method: "POST" }),
  applyCancel: (id) => api(`/api/apply/${id}/cancel`, { method: "POST" }),
  manualPrepare: (body) => api("/api/manual-apply/prepare", { method: "POST", json: body }),
  manualTailor: (body) => api("/api/manual-apply/tailor", { method: "POST", json: body }),
  manualAnswers: (body) => api("/api/manual-apply/answers", { method: "POST", json: body }),
  settings: () => api("/api/settings"),
  browserDiag: () => api("/api/settings/browser-diag"),
  personas: () => api("/api/settings/personas"),
  setAtsFloor: (body) =>
    api("/api/settings/ats-floor", { method: "POST", json: body }),
  resumeAccent: () => api("/api/settings/resume-accent"),
  setResumeAccent: (accent) =>
    api("/api/settings/resume-accent", { method: "POST", json: { accent } }),
  activatePersona: (persona_id) =>
    api("/api/settings/personas/activate", { method: "POST", json: { persona_id } }),
  rejected: () => api("/api/rejected"),
};

export function getAuthToken() {
  return getToken();
}

export function artifactUrl(jobId, kind, personaId, extra = {}) {
  const q = new URLSearchParams();
  if (personaId) q.set("persona_id", personaId);
  const token = getToken();
  if (token) q.set("token", token);
  Object.entries(extra).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  });
  return `/api/jobs/${jobId}/artifacts/${kind}?${q}`;
}

export function logPoong(...args) {
  console.log("[POONG]", ...args);
}
