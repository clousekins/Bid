import { useRef } from "react";

export default function WorkflowBar({
  seqFrom,
  seqTo,
  setSeqFrom,
  setSeqTo,
  onDeepScan,
  onTailor,
  onAssistedApply,
  onStopBatch,
  onRefresh,
  onAddJob,
  onManualApply,
  onUpload,
  onDiscover,
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

  return (
    <div className="panel workflow-bar shell-panel">
      <div className="workflow-head">
        <h2>Workflow</h2>
        <div className="workflow-seq">
          <label>
            Seq
            <input
              type="number"
              value={seqFrom}
              onChange={(e) => setSeqFrom(Number(e.target.value))}
            />
          </label>
          <span className="muted">–</span>
          <label>
            <span className="sr-only">to</span>
            <input
              type="number"
              value={seqTo}
              onChange={(e) => setSeqTo(Number(e.target.value))}
            />
          </label>
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
