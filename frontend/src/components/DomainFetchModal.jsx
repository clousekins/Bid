import { useEffect, useMemo, useState } from "react";
import { client, logPoong } from "../api.js";

const FALLBACK_DOMAINS = [
  { id: "ai", code: "AI", label: "AI / ML" },
  { id: "de", code: "DE", label: "Data Engineering" },
  { id: "da", code: "DA", label: "Data Analyst" },
  { id: "ds", code: "DS", label: "Data Scientist" },
  { id: "fs", code: "FS", label: "Full Stack" },
  { id: "be", code: "BE", label: "Backend" },
  { id: "fe", code: "FE", label: "Frontend" },
  { id: "ce", code: "CE", label: "Cloud Engineer" },
  { id: "sq", code: "SQ", label: "SQL Developer" },
  { id: "sf", code: "SF", label: "Salesforce" },
  { id: "dv", code: "DV", label: "DevOps / SRE" },
  { id: "se", code: "SE", label: "Software Engineer" },
];

export default function DomainFetchModal({
  open,
  title = "Fetch domains",
  subtitle,
  busy,
  onClose,
  onConfirm,
}) {
  const [domains, setDomains] = useState(FALLBACK_DOMAINS);
  const [selected, setSelected] = useState(() => new Set(FALLBACK_DOMAINS.map((d) => d.id)));
  // Titles inside a domain. Empty means "the whole domain", which is what
  // selecting a row without picking any title should mean.
  const [roles, setRoles] = useState(() => new Set());
  const [openRows, setOpenRows] = useState(() => new Set());

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const res = await client.fetchSites();
        if (cancelled) return;
        if (Array.isArray(res?.domains) && res.domains.length) {
          setDomains(res.domains);
          setSelected(new Set(res.domains.map((d) => d.id)));
        }
      } catch (e) {
        logPoong("domain catalog fail", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape" && !busy) onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  const selectedList = useMemo(() => [...selected], [selected]);
  const roleList = useMemo(() => [...roles], [roles]);
  if (!open) return null;

  const toggle = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(domains.map((d) => d.id)));
  const clearAll = () => {
    setSelected(new Set());
    setRoles(new Set());
  };

  const toggleRole = (domainId, roleId) =>
    setRoles((prev) => {
      const next = new Set(prev);
      next.has(roleId) ? next.delete(roleId) : next.add(roleId);
      // Picking a title implies wanting its domain.
      if (next.has(roleId)) setSelected((s2) => new Set(s2).add(domainId));
      return next;
    });

  const toggleRow = (id) =>
    setOpenRows((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <div className="modal-backdrop" onClick={() => !busy && onClose?.()}>
      <div
        className="modal domain-fetch-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="domain-fetch-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="domain-fetch-head">
          <h2 id="domain-fetch-title">{title}</h2>
          <button type="button" disabled={busy} onClick={onClose}>
            Close
          </button>
        </div>
        <p className="muted domain-fetch-blurb">
          {subtitle ||
            "Pick tech domains to keep. Only matching jobs are fetched. The table Dom column shows 2-letter codes (AI, DE, FS, SF, …)."}
        </p>
        <div className="domain-fetch-actions">
          <button type="button" disabled={busy} onClick={selectAll}>
            Select all
          </button>
          <button type="button" disabled={busy} onClick={clearAll}>
            Clear
          </button>
          <span className="muted">{selectedList.length} selected</span>
        </div>
        <div className="domain-rows">
          {domains.map((d) => {
            const active = selected.has(d.id);
            const rows = d.roles || [];
            const picked = rows.filter((r) => roles.has(r.id));
            const expanded = openRows.has(d.id);
            return (
              <div key={d.id} className={`domain-row ${active ? "active" : ""}`}>
                <div className="domain-row-head">
                  <button
                    type="button"
                    className={active ? "seq-chip active" : "seq-chip"}
                    title={d.blurb || d.label}
                    disabled={busy}
                    onClick={() => toggle(d.id)}
                  >
                    <span className="domain-code">{d.code}</span>
                    <span className="domain-label">{d.label}</span>
                  </button>
                  {rows.length ? (
                    <button
                      type="button"
                      className="domain-row-toggle"
                      disabled={busy}
                      onClick={() => toggleRow(d.id)}
                      title="Choose specific job titles in this domain"
                    >
                      {picked.length
                        ? `${picked.length} of ${rows.length} titles`
                        : `all ${rows.length} titles`}
                      <span className="domain-row-caret">{expanded ? "▴" : "▾"}</span>
                    </button>
                  ) : null}
                </div>
                {expanded && rows.length ? (
                  <div className="domain-role-chips">
                    {rows.map((r) => (
                      <button
                        key={r.id}
                        type="button"
                        className={roles.has(r.id) ? "role-chip active" : "role-chip"}
                        disabled={busy}
                        onClick={() => toggleRole(d.id, r.id)}
                      >
                        {r.label}
                      </button>
                    ))}
                    {picked.length ? (
                      <button
                        type="button"
                        className="role-chip role-chip-clear"
                        disabled={busy}
                        onClick={() =>
                          setRoles((prev) => {
                            const next = new Set(prev);
                            rows.forEach((r) => next.delete(r.id));
                            return next;
                          })
                        }
                      >
                        clear titles
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
        <div className="modal-actions">
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            disabled={busy || selectedList.length === 0}
            onClick={() => onConfirm?.(selectedList, roleList)}
          >
            Fetch{" "}
            {roleList.length
              ? `(${roleList.length} titles)`
              : selectedList.length
                ? `(${selectedList.length} domains)`
                : ""}
          </button>
        </div>
      </div>
    </div>
  );
}
