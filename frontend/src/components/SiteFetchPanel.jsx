import { useEffect, useState } from "react";
import DomainFetchModal from "./DomainFetchModal.jsx";
import { client, logPoong } from "../api.js";

const FALLBACK_SITES = [
  { id: "greenhouse", label: "Greenhouse", kind: "ats" },
  { id: "ashby", label: "Ashby", kind: "ats" },
  { id: "lever", label: "Lever", kind: "ats" },
  { id: "workable", label: "Workable", kind: "ats" },
  { id: "companies", label: "All company ATS", kind: "ats_all" },
  { id: "remoteok", label: "Remote OK", kind: "feed" },
  { id: "remotive", label: "Remotive", kind: "feed" },
  { id: "hn_whoishiring", label: "HN Who is Hiring", kind: "feed" },
];

const FALLBACK_WINDOWS = [
  { id: "24h", label: "24h", max_jobs: 1000 },
  { id: "7d", label: "7 days", max_jobs: 500 },
  { id: "all", label: "All time", max_jobs: 100 },
];

export default function SiteFetchPanel({ busy, onFetchSite }) {
  const [sites, setSites] = useState(FALLBACK_SITES);
  const [windows, setWindows] = useState(FALLBACK_WINDOWS);
  const [notes, setNotes] = useState("");
  const [selected, setSelected] = useState("greenhouse");
  const [pendingWindow, setPendingWindow] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await client.fetchSites();
        if (cancelled) return;
        if (Array.isArray(res?.sites) && res.sites.length) setSites(res.sites);
        if (Array.isArray(res?.windows) && res.windows.length) setWindows(res.windows);
        if (res?.notes) setNotes(res.notes);
      } catch (e) {
        logPoong("fetch-sites catalog fail", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const site = sites.find((s) => s.id === selected) || sites[0];
  const windowMeta = windows.find((w) => w.id === pendingWindow);
  // Each window says how many it may return. A day of listings is bounded by
  // its own date filter, so asking for "today" takes all of today.
  const capFor = (w) => w?.max_jobs || 100;

  return (
    <div className="site-fetch-panel">
      <div className="site-fetch-head">
        <span className="workflow-group-label">Fetch by site</span>
        <span className="muted site-fetch-cap">the window sets the limit</span>
      </div>
      <p className="muted site-fetch-blurb">
        Pick a board/feed, then a time window. Next you choose domains (AI, DE, FS, SF, …) so
        only matching titles are kept.
      </p>
      <div className="site-fetch-sites">
        {sites.map((s) => (
          <button
            key={s.id}
            type="button"
            className={selected === s.id ? "seq-chip active" : "seq-chip"}
            title={s.blurb || s.label}
            disabled={busy}
            onClick={() => setSelected(s.id)}
          >
            {s.label}
          </button>
        ))}
      </div>
      {site?.blurb ? <p className="muted site-fetch-selected">{site.blurb}</p> : null}
      <div className="site-fetch-windows">
        {windows.map((w) => (
          <button
            key={w.id}
            type="button"
            className="primary"
            disabled={busy || !selected}
            onClick={() => setPendingWindow(w.id)}
            title={`Choose domains, then fetch up to ${capFor(w)} from ${site?.label || selected} (${w.label})`}
          >
            {site?.label || selected} · {w.label}
          </button>
        ))}
      </div>
      {notes ? <p className="muted site-fetch-notes">{notes}</p> : null}
      <DomainFetchModal
        open={Boolean(pendingWindow)}
        title="Fetch by site — domains"
        subtitle={`Fetching ${site?.label || selected} · ${windowMeta?.label || pendingWindow || ""}. Only matching domains are kept.`}
        busy={busy}
        onClose={() => setPendingWindow(null)}
        onConfirm={(domains, roles) => {
          const win = pendingWindow;
          setPendingWindow(null);
          if (!win) return;
          onFetchSite?.({
            site: selected,
            window: win,
            max_jobs: capFor(windows.find((w) => w.id === win)),
            domains,
            roles,
          });
        }}
      />
    </div>
  );
}
