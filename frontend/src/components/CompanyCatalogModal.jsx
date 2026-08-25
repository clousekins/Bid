import { useEffect, useState } from "react";
import { client, logPoong } from "../api.js";
import TableExportMenu from "./TableExportMenu.jsx";
import { exportCatalogTable } from "../utils/tableExports.js";

const STATUS_TABS = [
  { id: "all", label: "All" },
  { id: "searched", label: "Searched" },
  { id: "waiting", label: "Waiting" },
  { id: "ok", label: "With jobs" },
  { id: "pending", label: "Pending" },
  { id: "missing", label: "Missing" },
  { id: "failed", label: "Failed" },
  { id: "empty", label: "Empty" },
];

const SOURCE_OPTS = [
  { id: "all", label: "All ATS" },
  { id: "greenhouse", label: "Greenhouse" },
  { id: "lever", label: "Lever" },
  { id: "ashby", label: "Ashby" },
  { id: "workable", label: "Workable" },
];

function boardUrl(source, token) {
  if (!token) return null;
  if (source === "greenhouse") return `https://boards.greenhouse.io/${token}`;
  if (source === "lever") return `https://jobs.lever.co/${token}`;
  if (source === "ashby") return `https://jobs.ashbyhq.com/${token}`;
  if (source === "workable") return `https://apply.workable.com/${token}/`;
  return null;
}

function fmtWhen(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function CompanyCatalogModal({ open, onClose, summary }) {
  const [status, setStatus] = useState("all");
  const [source, setSource] = useState("all");
  const [q, setQ] = useState("");
  const [qDraft, setQDraft] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const limit = 100;

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    const load = async () => {
      setBusy(true);
      setError("");
      try {
        const res = await client.listAtsBoards({
          status,
          source,
          q,
          limit,
          offset,
        });
        if (!cancelled) setData(res);
      } catch (e) {
        logPoong("ats catalog load fail", e);
        if (!cancelled) setError(String(e.message || e));
      } finally {
        if (!cancelled) setBusy(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [open, status, source, q, offset]);

  useEffect(() => {
    if (!open) return;
    setOffset(0);
  }, [open, status, source, q]);

  if (!open) return null;

  const boards = data?.boards || [];
  const total = data?.total ?? summary?.total ?? 0;
  const byStatus = summary?.by_status || {};

  return (
    <div className="modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="modal company-catalog-modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Company catalog"
      >
        <div className="company-catalog-head">
          <div>
            <h2>Company catalog</h2>
            <p className="muted">
              ATS career boards we know about. Searched boards are skipped on the next fetch;
              waiting boards are tried first.
            </p>
          </div>
          <div className="company-catalog-head-actions">
            <TableExportMenu
              label="Download"
              disabled={!total}
              exporting={exporting}
              hint="Download filtered catalog as CSV or Excel"
              onExportCsv={async () => {
                try {
                  setExporting(true);
                  setError("");
                  await exportCatalogTable({
                    format: "csv",
                    filters: {
                      status: status === "all" ? undefined : status,
                      source: source === "all" ? undefined : source,
                      q: q || undefined,
                    },
                  });
                } catch (e) {
                  setError(String(e.message || e));
                } finally {
                  setExporting(false);
                }
              }}
              onExportExcel={async () => {
                try {
                  setExporting(true);
                  setError("");
                  await exportCatalogTable({
                    format: "excel",
                    filters: {
                      status: status === "all" ? undefined : status,
                      source: source === "all" ? undefined : source,
                      q: q || undefined,
                    },
                  });
                } catch (e) {
                  setError(String(e.message || e));
                } finally {
                  setExporting(false);
                }
              }}
            />
            <button type="button" className="ghost" onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        <div className="company-catalog-summary muted">
          <span>{summary?.total ?? total} total</span>
          <span>{summary?.checked ?? "?"} searched</span>
          <span>{summary?.pending ?? "?"} waiting</span>
          <span>{summary?.with_jobs ?? "?"} with jobs</span>
          {Object.keys(byStatus).length ? (
            <span>
              {Object.entries(byStatus)
                .map(([k, v]) => `${k}:${v}`)
                .join(" · ")}
            </span>
          ) : null}
        </div>

        <div className="company-catalog-filters">
          <div className="company-catalog-tabs">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={status === tab.id ? "seq-chip active" : "seq-chip"}
                onClick={() => setStatus(tab.id)}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="company-catalog-row">
            <select value={source} onChange={(e) => setSource(e.target.value)}>
              {SOURCE_OPTS.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </select>
            <form
              className="company-catalog-search"
              onSubmit={(e) => {
                e.preventDefault();
                setQ(qDraft.trim());
              }}
            >
              <input
                type="search"
                placeholder="Search company or token…"
                value={qDraft}
                onChange={(e) => setQDraft(e.target.value)}
              />
              <button type="submit">Search</button>
            </form>
          </div>
        </div>

        {error ? <div className="error-banner">{error}</div> : null}
        {busy && !boards.length ? <p className="muted">Loading…</p> : null}

        <div className="company-catalog-table-wrap">
          <table className="company-catalog-table">
            <thead>
              <tr>
                <th>Company</th>
                <th>ATS</th>
                <th>Token</th>
                <th>Status</th>
                <th>Jobs</th>
                <th>Last checked</th>
              </tr>
            </thead>
            <tbody>
              {boards.map((b) => {
                const href = boardUrl(b.source, b.token);
                const name = b.company_name || b.token;
                return (
                  <tr key={b.id}>
                    <td>
                      {href ? (
                        <a href={href} target="_blank" rel="noreferrer">
                          {name}
                        </a>
                      ) : (
                        name
                      )}
                    </td>
                    <td>{b.source}</td>
                    <td className="mono">{b.token}</td>
                    <td>
                      <span className={`ats-status ats-status-${b.status}`}>{b.status}</span>
                      {b.error ? (
                        <span className="muted ats-error" title={b.error}>
                          {" "}
                          err
                        </span>
                      ) : null}
                    </td>
                    <td>{b.jobs_found ?? 0}</td>
                    <td className="muted">{fmtWhen(b.last_checked_at)}</td>
                  </tr>
                );
              })}
              {!busy && !boards.length ? (
                <tr>
                  <td colSpan={6} className="muted">
                    No companies match these filters.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>

        <div className="company-catalog-pager modal-actions">
          <span className="muted">
            Showing {boards.length ? offset + 1 : 0}–{offset + boards.length} of {total}
          </span>
          <div className="company-catalog-pager-btns">
            <button
              type="button"
              disabled={offset <= 0 || busy}
              onClick={() => setOffset(Math.max(0, offset - limit))}
            >
              Prev
            </button>
            <button
              type="button"
              disabled={offset + boards.length >= total || busy}
              onClick={() => setOffset(offset + limit)}
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
