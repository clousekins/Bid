/** Desktop notification for work that finishes while you are looking elsewhere.
 *
 * A Jobright sweep runs for twenty minutes now. The tab that started it is
 * rarely the tab you are on when it lands, and the progress panel can only
 * tell you something finished if you happen to be watching it.
 *
 * Everything here degrades quietly: an old browser, a denied permission or a
 * blocked notification all end up as "no alert", never as an error the user
 * has to read.
 */

export function canNotify() {
  return typeof window !== "undefined" && "Notification" in window;
}

/** Ask once, from a click. Browsers reject this outside a user gesture. */
export async function askNotifyPermission() {
  if (!canNotify()) return "unsupported";
  if (Notification.permission !== "default") return Notification.permission;
  try {
    return await Notification.requestPermission();
  } catch {
    return "denied";
  }
}

export function notify(title, body, tag) {
  if (!canNotify() || Notification.permission !== "granted") return false;
  try {
    const note = new Notification(title, { body, tag, renotify: false });
    // Clicking it should bring you back to the run it is telling you about.
    note.onclick = () => {
      try {
        window.focus();
      } finally {
        note.close();
      }
    };
    return true;
  } catch {
    return false;
  }
}

/** What to say about a finished background job.
 *
 * `progress` arrives parsed, but the API hands back the raw string when the
 * JSON will not decode — spreading that would splat it one character per key.
 */
export function completionAlert(kind, status, progress) {
  const p = progress && typeof progress === "object" ? progress : {};
  const label = {
    discover: "Fetch",
    discover_fresh: "Fresh fetch",
    discover_site: p.site_label || p.site ? `${p.site_label || p.site} fetch` : "Fetch",
    discover_companies: "Company discovery",
    deep_scan: "Job description scan",
    tailor: "Resume generation",
  }[kind] || "Background job";

  if (status === "failed") {
    return { title: `${label} failed`, body: String(p.error || p.message || "").slice(0, 180) };
  }
  if (status === "cancelled") return { title: `${label} cancelled`, body: "" };

  const bits = [];
  if (p.inserted != null) bits.push(`${p.inserted} added`);
  if (p.after_filters != null) bits.push(`${p.after_filters} kept`);
  else if (p.done != null) bits.push(`${p.done} processed`);
  if (p.discovered_raw != null) bits.push(`${p.discovered_raw} found`);
  if (p.skipped) bits.push(`${p.skipped} skipped`);
  // The run stopped at its deadline rather than running out of pages.
  if (p.more_available) bits.push("more available");
  const lines = [bits.join(" · ")];
  if (p.csv_name) lines.push(`CSV: ${p.csv_name}`);
  if (p.source_error) lines.push(String(p.source_error).slice(0, 120));
  // A source that refused us did not "complete". Saying so in the body
  // while the title reads Complete is how a bot check gets mistaken for a
  // quiet day.
  const headline = p.source_error ? `${label} blocked` : `${label} complete`;
  return { title: headline, body: lines.filter(Boolean).join("\n") };
}
