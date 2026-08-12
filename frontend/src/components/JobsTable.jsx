import {
  personaInitials,
  shortPersona,
} from "../utils/persona.js";
import {
  PIPELINE_INLINE,
  packageStatusLabel,
  pipelineLabel,
} from "../utils/statusLabels.js";

export default function JobsTable({
  jobs,
  selectedId,
  checkedIds,
  onSelect,
  onToggleCheck,
  onToggleCheckAll,
  statusFilter,
  profileStatusFilter,
  workingPersonaId,
  page = 1,
  pages = 1,
  pageSize = 50,
  total = 0,
  onPageChange,
  onPageSizeChange,
}) {
  const visibleIds = jobs.map((j) => j.id);
  const checkedVisible = visibleIds.filter((id) => checkedIds.has(id));
  const allVisibleChecked =
    visibleIds.length > 0 && checkedVisible.length === visibleIds.length;
  const someVisibleChecked =
    checkedVisible.length > 0 && checkedVisible.length < visibleIds.length;
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <div className="panel jobs-panel work-panel">
      <div className="panel-head">
        <h2>Jobs</h2>
        <span className="panel-meta">
          {total ? (
            <>
              {from}–{to} of <strong>{total}</strong>
            </>
          ) : (
            <>0 jobs</>
          )}
          {checkedIds.size ? (
            <>
              {" "}
              · <strong>{checkedIds.size}</strong> selected
            </>
          ) : null}
          {workingPersonaId ? (
            <>
              {" "}
              · <span className="pill package-status">{shortPersona(workingPersonaId)}</span>
            </>
          ) : null}
          {profileStatusFilter ? (
            <>
              {" "}
              ·{" "}
              <span className={`pill package-status ${profileStatusFilter}`}>
                {packageStatusLabel(profileStatusFilter)}
              </span>
            </>
          ) : null}
          {statusFilter ? (
            <>
              {" "}
              · <span className={`pill ${statusFilter}`}>{pipelineLabel(statusFilter)}</span>
            </>
          ) : null}
        </span>
      </div>
      <div className="jobs-scroll">
        <table className="jobs-table">
          <colgroup>
            <col className="col-check" />
            <col className="col-seq" />
            <col className="col-company" />
            <col className="col-title" />
            <col className="col-my-status" />
            <col className="col-others" />
          </colgroup>
          <thead>
            <tr>
              <th className="cell-check">
                <input
                  type="checkbox"
                  aria-label="Select all visible jobs"
                  checked={allVisibleChecked}
                  ref={(el) => {
                    if (el) el.indeterminate = someVisibleChecked;
                  }}
                  onChange={(e) => onToggleCheckAll(e.target.checked)}
                  onClick={(e) => e.stopPropagation()}
                />
              </th>
              <th>#</th>
              <th>Company</th>
              <th>Title</th>
              <th>Status</th>
              <th>Others</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => {
              const pkgs = j.packages || [];
              const myStatus = j.my_status || "need_resume";
              const others = pkgs.filter((p) => p.persona_id !== workingPersonaId);
              const othersTitle = others.length
                ? others
                    .map(
                      (p) =>
                        `${shortPersona(p.persona_id)}: ${packageStatusLabel(p.status)}`
                    )
                    .join(", ")
                : "";
              const showPipeline = PIPELINE_INLINE.has(j.status);

              return (
                <tr
                  key={j.id}
                  className={[
                    j.id === selectedId ? "active" : "",
                    checkedIds.has(j.id) ? "checked" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  onClick={() => onSelect(j)}
                >
                  <td className="cell-check" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      aria-label={`Select job ${j.seq_no}`}
                      checked={checkedIds.has(j.id)}
                      onClick={(e) => {
                        if (!e.shiftKey) return;
                        // Keep Explorer behavior: Shift selects a range; don't let the
                        // browser flip only this box first.
                        e.preventDefault();
                        onToggleCheck(j.id, true, e);
                      }}
                      onChange={(e) => {
                        if (e.nativeEvent?.shiftKey || e.shiftKey) return;
                        onToggleCheck(j.id, e.target.checked, e);
                      }}
                    />
                  </td>
                  <td className="cell-seq">{j.seq_no ?? "—"}</td>
                  <td className="cell-ellipsis" title={j.company}>
                    {j.company}
                  </td>
                  <td className="cell-ellipsis" title={j.title}>
                    {j.title}
                  </td>
                  <td className="cell-status">
                    <span className={`pill package-status status-strong ${myStatus}`}>
                      {packageStatusLabel(myStatus)}
                    </span>
                    {showPipeline ? (
                      <span className={`pill pipeline-inline ${j.status}`} title="Listing pipeline">
                        {pipelineLabel(j.status)}
                      </span>
                    ) : null}
                  </td>
                  <td className="cell-others">
                    {others.length ? (
                      <div className="others-dots" title={othersTitle}>
                        {others.map((p) => (
                          <span
                            key={p.id || p.persona_id}
                            className={`other-dot ${p.status}`}
                            title={`${shortPersona(p.persona_id)}: ${packageStatusLabel(p.status)}`}
                          >
                            {personaInitials(p.persona_id)}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {!jobs.length && (
              <tr>
                <td colSpan={6} className="empty-row">
                  No jobs yet. Upload JSON/CSV or add a job.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="jobs-pagination">
        <label>
          Per page
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange?.(Number(e.target.value))}
          >
            <option value={25}>25</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={200}>200</option>
          </select>
        </label>
        <span className="muted">
          Page {page} / {pages}
        </span>
        <div className="jobs-pagination-actions">
          <button type="button" disabled={page <= 1} onClick={() => onPageChange?.(1)}>
            First
          </button>
          <button
            type="button"
            disabled={page <= 1}
            onClick={() => onPageChange?.(page - 1)}
          >
            Prev
          </button>
          <button
            type="button"
            disabled={page >= pages}
            onClick={() => onPageChange?.(page + 1)}
          >
            Next
          </button>
          <button
            type="button"
            disabled={page >= pages}
            onClick={() => onPageChange?.(pages)}
          >
            Last
          </button>
        </div>
      </div>
    </div>
  );
}
