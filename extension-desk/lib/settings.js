// Where the backend is, the token it wants, and the persona last looked at.
//
// chrome.storage.local rather than .sync: the access token is a credential,
// and sync would copy it to every browser signed into this Google account.

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
  const clean = {};
  if ("baseUrl" in values) clean.baseUrl = String(values.baseUrl || "").trim() || DEFAULTS.baseUrl;
  if ("accessToken" in values) clean.accessToken = String(values.accessToken || "").trim();
  if ("personaId" in values) clean.personaId = String(values.personaId || "").trim();
  await chrome.storage.local.set(clean);
}
