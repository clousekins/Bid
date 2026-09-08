// Where the backend is and what it will accept from us.
//
// chrome.storage.local rather than .sync deliberately: the access token is a
// credential, and sync would copy it to every browser signed into this Google
// account. Local keeps it on this machine, the way .env does.

const DEFAULTS = {
  baseUrl: "http://127.0.0.1:8000",
  accessToken: "",
  personaId: "",
};

export async function loadSettings() {
  const stored = await chrome.storage.local.get(DEFAULTS);
  return {
    ...DEFAULTS,
    ...stored,
    // A trailing slash turns /api/jobs into //api/jobs, which FastAPI answers
    // with a redirect the fetch then follows without the auth header.
    baseUrl: String(stored.baseUrl || DEFAULTS.baseUrl).replace(/\/+$/, ""),
  };
}

export async function saveSettings(values) {
  await chrome.storage.local.set({
    baseUrl: String(values.baseUrl || "").trim() || DEFAULTS.baseUrl,
    accessToken: String(values.accessToken || "").trim(),
    personaId: String(values.personaId || "").trim(),
  });
}
