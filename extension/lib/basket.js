// The rows collected so far, across pastes.
//
// This lives in chrome.storage.local rather than a variable because a popup is
// torn down the moment it loses focus — every paste happens in a fresh page
// with no memory of the last one. "Paste, paste, paste, then download" only
// works if each paste is written down somewhere that outlives the window.

const KEY = "clippedRows";

export async function readBasket() {
  const stored = await chrome.storage.local.get({ [KEY]: [] });
  return Array.isArray(stored[KEY]) ? stored[KEY] : [];
}

/**
 * Add rows, skipping the ones already held.
 *
 * Identity is the Jobright job id where there is one and the URL otherwise —
 * the same posting arrives again whenever two searches overlap, which they do
 * constantly once a few titles have been pasted.
 */
export async function addToBasket(rows) {
  const held = await readBasket();
  const seen = new Set(held.map(identity));
  const added = [];
  for (const row of rows) {
    const key = identity(row);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    added.push(row);
  }
  const next = held.concat(added);
  await chrome.storage.local.set({ [KEY]: next });
  return { added: added.length, duplicates: rows.length - added.length, total: next.length };
}

export async function clearBasket() {
  await chrome.storage.local.set({ [KEY]: [] });
}

/**
 * Record what the backend did with each row.
 *
 * `outcomes` maps a row's identity to the sequence number it now has on file,
 * so a second press of Push skips it instead of asking the backend again. The
 * backend would refuse the duplicate anyway; this is about not making a
 * hundred pointless requests to be told so.
 */
export async function markPushed(outcomes) {
  const rows = await readBasket();
  for (const row of rows) {
    const seq = outcomes[identity(row)];
    if (seq !== undefined) row.pushed_seq = seq;
  }
  await chrome.storage.local.set({ [KEY]: rows });
  return rows;
}

/** Rows that have not yet been accepted by the backend. */
export function unpushed(rows) {
  return rows.filter((row) => !row.pushed_seq);
}

function identity(row) {
  return String(row.external_id || row.url || "").trim();
}
