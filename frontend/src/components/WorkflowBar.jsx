import { useRef, useState } from "react";
import { commitSeqRange } from "../utils/seqRange.js";
import DomainFetchModal from "./DomainFetchModal.jsx";
import SiteFetchPanel from "./SiteFetchPanel.jsx";
import AtsFloorControl from "./AtsFloorControl.jsx";
import AccentControl from "./AccentControl.jsx";
import SectorSearchPanel from "./SectorSearchPanel.jsx";

const STAGES = [
  { id: "ingest", label: "Ingest", hint: "Add & fetch listings" },
  { id: "prep", label: "Prep", hint: "Fetch job descriptions" },
  { id: "generate", label: "Generate", hint: "Tailor resumes" },
  { id: "apply", label: "Apply", hint: "Assisted apply" },
  { id: "sector", label: "Special field", hint: "Search one industry sector" },
];

export default function WorkflowBar({
  seqFrom,
  seqTo,
  seqMax = 0,
  setSeqFrom,
  setSeqTo,
  ats,
  onDeepScan,
  onTailor,
  onAssistedApply,
  onStopBatch,
  onAddJob,
  onManualApply,
  onUpload,
  onDiscover,
  onDiscoverFresh,
  onDiscoverCompanies,
  onOpenCompanyCatalog,
  onFetchSite,
  onSectorImport,
  onDeleteRange,
  onRenumber,
  checkedCount,
  onClearSelection,
  onDeepScanSelected,
  onTailorSelected,
  onRegenerateSelected,
  onDeleteSelected,
  busy,
  workingLabel,
}) {
  const fileRef = useRef(null);
  const [stage, setStage] = useState("ingest");
  const [showAdmin, setShowAdmin] = useState(false);
  const [domainFetch, setDomainFetch] = useState(null);
  const hasSelection = checkedCount > 0;
  const maxSeq = Math.max(0, Number(seqMax) || 0);

  const applyRange = (fromRaw, toRaw) => {
    if (!maxSeq) {
      setSeqFrom(1);
      setSeqTo(1);
      return;
    }
    const next = commitSeqRange(fromRaw, toRaw, maxSeq);
    setSeqFrom(next.from);
    setSeqTo(next.to);
  };

  return (
    <section className="workflow-shell">
      <div className="workflow-top">
        <nav className="workflow-tabs" aria-label="Workflow stages">
          {STAGES.map((s) => (
            <button
              key={s.id}
              type="button"
              className={stage === s.id ? "workflow-tab active" : "workflow-tab"}
              title={s.hint}
              onClick={() => setStage(s.id)}
            >
              {s.label}
            </button>
          ))}
        </nav>

        <div className="workflow-range">
          <span className="workflow-range-label">Seq range</span>
          <input
            type="number"
            min={1}
            max={maxSeq || 1}
            value={seqFrom}
            aria-label="Seq from"
            onChange={(e) => setSeqFrom(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={(e) => applyRange(e.target.value, seqTo)}
          />
          <span className="muted">–</span>
          <input
            type="number"
            min={1}
            max={maxSeq || 1}
            value={seqTo}
            aria-label="Seq to"
            onChange={(e) => setSeqTo(e.target.value === "" ? "" : Number(e.target.value))}
            onBlur={(e) => applyRange(seqFrom, e.target.value)}
          />
          <span className="muted seq-cap">{maxSeq ? `of ${maxSeq}` : "—"}</span>
          <button type="button" className="chip-btn" disabled={!maxSeq} onClick={() => applyRange(1, maxSeq)}>
            All
          </button>
          <button
            type="button"
            className="chip-btn"
            disabled={!maxSeq}
            onClick={() => applyRange(Math.max(1, maxSeq - 49), maxSeq)}
          >
            Last 50
          </button>
        </div>

        <div className="workflow-top-right">
          {hasSelection ? (
            <span className="selection-badge">
              {checkedCount} selected
              <button type="button" className="linkish" onClick={onClearSelection}>
                Clear
              </button>
            </span>
          ) : null}
          <button
            type="button"
            className="ghost-btn danger-text"
            disabled={!busy}
            onClick={onStopBatch}
            title={busy ? "Stop the running background job" : "No batch running"}
          >
            Stop batch
          </button>
          <button
            type="button"
            className={`ghost-btn ${showAdmin ? "active" : ""}`}
            onClick={() => setShowAdmin((v) => !v)}
          >
            Admin
          </button>
        </div>
      </div>

      {showAdmin ? (
        <div className="workflow-admin">
          <button type="button" className="danger" disabled={busy} onClick={onDeleteRange}>
            Delete seq range
          </button>
          <button type="button" className="danger" disabled={!hasSelection || busy} onClick={onDeleteSelected}>
            Delete selected
          </button>
          <button type="button" disabled={busy} onClick={onRenumber}>
            Renumber folders
          </button>
        </div>
      ) : null}

      <div className="workflow-stage">
        {stage === "ingest" ? (
          <>
            <div className="action-grid">
              <button type="button" className="primary" onClick={onAddJob}>
                Add job
              </button>
              <button type="button" onClick={onManualApply}>
                Manual apply
              </button>
              <button
                type="button"
                className="primary"
                disabled={busy}
                onClick={() =>
                  setDomainFetch({
                    kind: "discover",
                    title: "Fetch new jobs — domains",
                    subtitle: "Choose domains (AI, DE, FS, SF, …). Only matching titles are inserted.",
                  })
                }
              >
                Fetch new jobs
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  setDomainFetch({
                    kind: "discover_fresh",
                    title: "Fetch fresh (24h) — domains",
                    subtitle: "Recent postings only, filtered by the domains you pick.",
                  })
                }
              >
                Fetch fresh (24h)
              </button>
              <button type="button" disabled={busy} onClick={onDiscoverCompanies}>
                Discover companies
              </button>
              {ats ? (
                <button type="button" className="linkish" onClick={onOpenCompanyCatalog}>
                  Catalog {ats.checked}/{ats.total}
                  {ats.pending ? ` · ${ats.pending} waiting` : ""}
                </button>
              ) : null}
              <button type="button" onClick={() => fileRef.current?.click()}>
                Upload JSON/CSV
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".json,.csv,application/json,text/csv"
                hidden
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) onUpload(f);
                  e.target.value = "";
                }}
              />
            </div>
            <SiteFetchPanel busy={busy} onFetchSite={onFetchSite} />
          </>
        ) : null}

        {stage === "prep" ? (
          <div className="action-grid">
            <button type="button" className="primary" disabled={busy} onClick={onDeepScan}>
              Fetch JDs (seq range)
            </button>
            <button type="button" className="primary" disabled={!hasSelection || busy} onClick={onDeepScanSelected}>
              Fetch JDs (selected)
            </button>
            <p className="stage-hint muted">
              Deep-scan pulls full job descriptions for listings still marked new or scan failed.
            </p>
          </div>
        ) : null}

        {stage === "generate" ? (
          <div className="action-grid">
            <button type="button" className="primary" disabled={busy} onClick={onTailor}>
              Generate resumes (seq range)
            </button>
            <button type="button" className="primary" disabled={!hasSelection || busy} onClick={onTailorSelected}>
              Generate (selected)
            </button>
            <button
              type="button"
              disabled={!hasSelection || busy}
              onClick={onRegenerateSelected}
              title="Tailor every selected job again, overwriting resumes already generated"
            >
              Regenerate (selected)
            </button>
            <p className="stage-hint muted">
              Tailors HTML resume bundles for <strong>{workingLabel}</strong>. Watch the bottom activity panel while it runs.
            </p>
            <AtsFloorControl />
            <AccentControl />
          </div>
        ) : null}

        {stage === "sector" ? (
          <div className="action-grid">
            <SectorSearchPanel onImport={onSectorImport} busy={busy} />
          </div>
        ) : null}

        {stage === "apply" ? (
          <div className="action-grid">
            <button type="button" className="primary" disabled={busy} onClick={onAssistedApply}>
              Assisted apply (seq range)
            </button>
            <button type="button" disabled={busy} onClick={onManualApply}>
              Manual apply wizard
            </button>
            <p className="stage-hint muted">
              Ctrl+M marks the selected job applied for the working persona. Use Apply console for live browser
              fill logs.
            </p>
          </div>
        ) : null}
      </div>

      <DomainFetchModal
        open={Boolean(domainFetch)}
        title={domainFetch?.title || "Fetch domains"}
        subtitle={domainFetch?.subtitle}
        busy={busy}
        onClose={() => setDomainFetch(null)}
        onConfirm={(domains) => {
          const kind = domainFetch?.kind;
          setDomainFetch(null);
          if (kind === "discover_fresh") onDiscoverFresh?.(domains);
          else if (kind === "discover") onDiscover?.(domains);
        }}
      />
    </section>
  );
}
