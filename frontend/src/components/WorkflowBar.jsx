import { useRef } from "react";
import { commitSeqRange } from "../utils/seqRange.js";
import SiteFetchPanel from "./SiteFetchPanel.jsx";

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
  onRefresh,
  onAddJob,
  onManualApply,
  onUpload,
  onDiscover,
  onDiscoverFresh,
  onDiscoverCompanies,
  onOpenCompanyCatalog,
  onFetchSite,
  onDeleteRange,
  onRenumber,
  checkedCount,
  onClearSelection,
  onDeepScanSelected,
  onTailorSelected,
  onDeleteSelected,
  batchProgress,
  busy,
  workingLabel,
}) {
  const fileRef = useRef(null);
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
    <div className="panel workflow-bar shell-panel">
      <div className="workflow-head">
        <h2>Workflow</h2>
        <div className="workflow-seq">
          <label>
            Seq
            <input
              type="number"
              min={1}
              max={maxSeq || 1}
              step={1}
              inputMode="numeric"
              value={seqFrom}
              onChange={(e) => setSeqFrom(e.target.value === "" ? "" : Number(e.target.value))}
              onBlur={(e) => applyRange(e.target.value, seqTo)}
              onWheel={(e) => e.currentTarget.blur()}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  applyRange(e.currentTarget.value, seqTo);
                }
              }}
            />
          </label>
          <span className="muted">–</span>
          <label>
            <span className="sr-only">to</span>
            <input
              type="number"
              min={1}
              max={maxSeq || 1}
              step={1}
              inputMode="numeric"
              value={seqTo}
              onChange={(e) => setSeqTo(e.target.value === "" ? "" : Number(e.target.value))}
              onBlur={(e) => applyRange(seqFrom, e.target.value)}
              onWheel={(e) => e.currentTarget.blur()}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  applyRange(seqFrom, e.currentTarget.value);
                }
              }}
            />
          </label>
          <span className="seq-max muted">{maxSeq ? `of ${maxSeq}` : "no jobs"}</span>
          <button
            type="button"
            className="seq-chip"
            disabled={!maxSeq}
            onClick={() => applyRange(1, maxSeq)}
            title="Use the full job list"
          >
            All
          </button>
          <button
            type="button"
            className="seq-chip"
            disabled={!maxSeq}
            onClick={() => applyRange(Math.max(1, maxSeq - 49), maxSeq)}
            title="Last 50 jobs"
          >
            Last 50
          </button>
        </div>
      </div>

      <div className="workflow-groups">
        <div className="workflow-group">
          <span className="workflow-group-label">Ingest</span>
          <div className="workflow-group-actions">
            <button type="button" onClick={onAddJob}>
              Add job
            </button>
            <button type="button" className="primary" onClick={onManualApply}>
              Manual apply
            </button>
            <button type="button" className="primary" disabled={busy} onClick={onDiscover}>
              Fetch new jobs
            </button>
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={onDiscoverFresh}
              title="Only jobs posted within about 24 hours from feeds + company career pages"
            >
              Fetch fresh (24h)
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onDiscoverCompanies}
              title="Finds real US company ATS boards (OpenAI/search when available, else public Greenhouse/Lever/Ashby/Workable slug probing)"
            >
              Discover companies
            </button>
            {ats ? (
              <button
                type="button"
                className="ats-catalog-hint linkish"
                onClick={onOpenCompanyCatalog}
                title="Open the full list of company ATS boards we have searched or still plan to search"
              >
                Companies {ats.checked}/{ats.total} searched
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
        </div>

        <div className="workflow-group">
          <span className="workflow-group-label">Prep</span>
          <div className="workflow-group-actions">
            <button type="button" className="primary" disabled={busy} onClick={onDeepScan}>
              Fetch JDs (range)
            </button>
            <button
              type="button"
              className="primary"
              disabled={!hasSelection || busy}
              onClick={onDeepScanSelected}
            >
              Fetch selected
            </button>
          </div>
        </div>

        <div className="workflow-group">
          <span className="workflow-group-label">Generate · {workingLabel}</span>
          <div className="workflow-group-actions">
            <button type="button" className="primary" disabled={busy} onClick={onTailor}>
              Generate range
            </button>
            <button
              type="button"
              className="primary"
              disabled={!hasSelection || busy}
              onClick={onTailorSelected}
            >
              Generate selected
            </button>
          </div>
        </div>

        <div className="workflow-group">
          <span className="workflow-group-label">Apply · {workingLabel}</span>
          <div className="workflow-group-actions">
            <button type="button" disabled={busy} onClick={onAssistedApply}>
              Assisted apply range
            </button>
            <button type="button" onClick={onRefresh}>
              Refresh
            </button>
            <button type="button" className="danger" onClick={onStopBatch}>
              Stop batch
            </button>
          </div>
        </div>

        <div className="workflow-group danger-group">
          <span className="workflow-group-label">Danger</span>
          <div className="workflow-group-actions">
            <button type="button" className="danger" disabled={busy} onClick={onDeleteRange}>
              Delete range
            </button>
            <button
              type="button"
              className="danger"
              disabled={!hasSelection || busy}
              onClick={onDeleteSelected}
            >
              Delete selected
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={onRenumber}
              title="Force seq 1..N and rename resume folders"
            >
              Renumber
            </button>
          </div>
        </div>
      </div>

      <div className={`selection-bar ${hasSelection ? "active" : ""}`}>
        <span className="selection-count">
          {hasSelection
            ? `${checkedCount} selected`
            : "Select rows with checkboxes for bulk actions"}
        </span>
        <div className="selection-actions">
          <button type="button" disabled={!hasSelection || busy} onClick={onClearSelection}>
            Clear selection
          </button>
        </div>
      </div>

      <div className="progress">{batchProgress || "\u00a0"}</div>
    </div>
  );
}
