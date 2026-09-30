import { useState } from "react";
import {
  personaInitials,
  shortPersona,
} from "../utils/persona.js";
import {
  PIPELINE_INLINE,
  packageStatusLabel,
  pipelineLabel,
} from "../utils/statusLabels.js";
import ResumePreview from "./ResumePreview.jsx";
import JobsEmptyState from "./JobsEmptyState.jsx";
import ScoreBadge from "./ScoreBadge.jsx";
import TableExportMenu from "./TableExportMenu.jsx";
import { exportJobsTable } from "../utils/tableExports.js";

const DOMAIN_SHOW_MAX = 2;

function parseDomainCodes(domains) {
  return (domains || "")
    .split(/[,;]+/)
    .map((c) => c.trim())
    .filter(Boolean);
}

function DomainCell({ domains }) {
  const codes = parseDomainCodes(domains);
  if (!codes.length) {
    return (
      <td className="cell-domain">
        <span className="muted">—</span>
      </td>
    );
  }

  const visible = codes.slice(0, DOMAIN_SHOW_MAX);
  const extra = codes.length - visible.length;
  const label = codes.join(", ");

  return (
    <td className="cell-domain" title={label} aria-label={`Domains: ${label}`}>
      <span className="domain-pills">
        {visible.map((code) => (
          <span key={code} className="domain-pill">
            {code}
          </span>
        ))}
        {extra > 0 ? (
          <span className="domain-pill domain-pill-more" title={codes.slice(DOMAIN_SHOW_MAX).join(", ")}>
            +{extra}
          </span>
        ) : null}
      </span>
    </td>
  );
}

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
  sort = "seq",
  sortDir = "asc",
  onSort,
  onPageChange,
  onPageSizeChange,
  generatingIds,
  onContinue,
  onFetchJd,
  onEmptyFetch,
  listParams = {},
  onExportError,
}) {
  const [exporting, setExporting] = useState(false);
  const visibleIds = jobs.map((j) => j.id);
  const checkedVisible = visibleIds.filter((id) => checkedIds.has(id));
  const allVisibleChecked =
    visibleIds.length > 0 && checkedVisible.length === visibleIds.length;
  const someVisibleChecked =
    checkedVisible.length > 0 && checkedVisible.length < visibleIds.length;
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  const sortMark = (key) => {
    if (sort !== key) return "";
    return sortDir === "desc" ? " ↓" : " ↑";
  };

  const SortTh = ({ col, children, title, className }) => (
    <th className={className} title={title}>
      {onSort ? (
        <button
          type="button"
          className={`th-sort ${sort === col ? "active" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            onSort(col);
          }}
        >
          {children}
          {sortMark(col)}
        </button>
      ) : (
        children
      )}
    </th>
  );

  const runExport = async (format, scope) => {
    try {
      setExporting(true);
      await exportJobsTable({
        format,
        listParams,
        selectedIds: checkedIds,
        scope,
      });
    } catch (e) {
      onExportError?.(String(e.message || e));
    } finally {
      setExporting(false);
    }
  };

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
        <div className="panel-head-actions">
          <TableExportMenu
            label="Download"
            disabled={!total}
            exporting={exporting}
            hint="Download filtered jobs as CSV or Excel"
            onExportCsv={() => runExport("csv", "filtered")}
            onExportExcel={() => runExport("excel", "filtered")}
          />
          {checkedIds.size ? (
            <TableExportMenu
              label="Selected"
              exporting={exporting}
              hint="Download only the selected jobs"
              onExportCsv={() => runExport("csv", "selected")}
              onExportExcel={() => runExport("excel", "selected")}
            />
          ) : null}
        </div>
      </div>
      {!jobs.length && total === 0 ? (
        <JobsEmptyState onFetch={onEmptyFetch} />
      ) : (
      <>
      <div className="jobs-scroll">
        <table className="jobs-table">
          <colgroup>
            <col className="col-check" />
            <col className="col-seq" />
            <col className="col-company" />
            <col className="col-title" />
            <col className="col-country" />
            <col className="col-domain" />
            <col className="col-score" />
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
              <SortTh col="seq" title="Sort by seq">
                #
              </SortTh>
              <SortTh col="company" title="Sort by company">
                Company
              </SortTh>
              <SortTh col="title" title="Sort by title">
                Title
              </SortTh>
              <SortTh col="country" title="Sort by country">
                Country
              </SortTh>
              <SortTh col="domains" title="Sort by domain codes (AI, DE, FS, SF, …)">
                Dom
              </SortTh>
              <SortTh col="feasibility" title="Fit for this profile / the resume's ATS keyword score / whether the resume reads as true">
                Fit / ATS / True
              </SortTh>
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
              const generating = generatingIds?.has(j.id);
              const needsJd =
                j.status === "new" ||
                j.status === "scan_failed" ||
                j.jd_fetched === false;

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
                  <td
                    className="cell-country"
                    title={
                      j.location
                        ? `${j.country_name || j.country || "United States"} — ${j.location}`
                        : j.country_name || j.country || "United States"
                    }
                  >
                    {j.country || "US"}
                  </td>
                  <DomainCell domains={j.domains} />
                  <td className="cell-score">
                    <div className="cell-score-wrap">
                      <ScoreBadge label="Fit" detail={j.feasibility} compact />
                      <span className="score-sep">/</span>
                      <ScoreBadge label="Resume ATS" score={j.ats_score} compact />
                      <span className="score-sep">/</span>
                      <ScoreBadge label="Credibility" score={j.credibility_score} compact />
                    </div>
                  </td>
                  <td className="cell-status">
                    <div className="cell-status-wrap">
                      <span className={`pill package-status status-strong ${myStatus}`}>
                        {packageStatusLabel(myStatus)}
                      </span>
                      {generating ? (
                        <span className="pill generating" title="Resume generating in the background">
                          Generating
                        </span>
                      ) : null}
                      {showPipeline ? (
                        <span className={`pill pipeline-inline ${j.status}`} title="Listing pipeline">
                          {pipelineLabel(j.status)}
                        </span>
                      ) : null}
                      {needsJd && onFetchJd ? (
                        <button
                          type="button"
                          className="table-continue"
                          title="Fetch job description from the apply URL"
                          onClick={(e) => {
                            e.stopPropagation();
                            onFetchJd(j);
                          }}
                        >
                          Fetch
                        </button>
                      ) : null}
                      {["resume_ready", "applying", "applied"].includes(myStatus) ? (
                        <span onClick={(e) => e.stopPropagation()}>
                          <ResumePreview
                            jobId={j.id}
                            personaId={workingPersonaId}
                            packages={pkgs}
                            generating={generating}
                            labels={{ resume: "Resume", cover: "Cover" }}
                          />
                        </span>
                      ) : null}
                      {onContinue ? (
                        <button
                          type="button"
                          className="table-continue"
                          title="Open Manual apply for this job"
                          onClick={(e) => {
                            e.stopPropagation();
                            onContinue(j);
                          }}
                        >
                          Apply
                        </button>
                      ) : null}
                    </div>
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
                <td colSpan={7} className="empty-row">
                  No jobs match your filters. Try Ingest → Fetch new jobs.
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
      </>
      )}
    </div>
  );
}
