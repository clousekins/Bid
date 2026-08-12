import { useState } from "react";
import { client, logPoong } from "./api.js";
import { useJobsWorkspace } from "./hooks/useJobsWorkspace.js";
import { useBatchJobs } from "./hooks/useBatchJobs.js";
import { useAssistedApply } from "./hooks/useAssistedApply.js";
import { shortPersona } from "./utils/persona.js";
import WorkspaceHeader from "./components/WorkspaceHeader.jsx";
import Toolbar from "./components/Toolbar.jsx";
import WorkflowBar from "./components/WorkflowBar.jsx";
import JobsTable from "./components/JobsTable.jsx";
import JobDetail from "./components/JobDetail.jsx";
import ApplyConsole from "./components/ApplyConsole.jsx";
import ManualApplyModal from "./components/ManualApplyModal.jsx";

export default function App() {
  const ws = useJobsWorkspace();
  const batch = useBatchJobs(ws.refresh);
  const apply = useAssistedApply({
    workingPersonaId: ws.workingPersonaId,
    refresh: ws.refresh,
    setError: ws.setError,
    selectedIdRef: ws.selectedIdRef,
    setSelected: ws.setSelected,
  });

  const [seqFrom, setSeqFrom] = useState(1);
  const [seqTo, setSeqTo] = useState(20);
  const [showAdd, setShowAdd] = useState(false);
  const [showManualApply, setShowManualApply] = useState(false);
  const [addForm, setAddForm] = useState({
    company: "",
    title: "",
    url: "",
    location: "Remote, US",
    description: "",
  });

  const workingLabel = shortPersona(ws.workingPersonaId);

  const runOrError = async (fn) => {
    try {
      ws.setError("");
      await fn();
    } catch (e) {
      ws.setError(String(e.message || e));
    }
  };

  const onUpload = async (file) => {
    try {
      const res = await client.importJobs(file);
      logPoong("import", res);
      const skippedN = (res.skipped || []).length;
      const rejectedN = (res.rejected || []).length;
      batch.setBatchProgress(
        res.message ||
          `Import: ${res.inserted} inserted` +
            (res.jd_ready != null ? `, ${res.jd_ready} jd_ready` : "") +
            (rejectedN ? `, ${rejectedN} rejected` : "") +
            (skippedN ? `, ${skippedN} skipped` : "")
      );
      if (!res.inserted) {
        ws.setError(
          skippedN ? `Import mapped rows but all were skipped.` : "Import returned 0 jobs."
        );
      } else {
        ws.setError("");
      }
      await ws.refresh();
    } catch (e) {
      ws.setError(String(e.message || e));
      batch.setBatchProgress("");
    }
  };

  const submitAdd = async () => {
    try {
      await client.createJob(addForm);
      setShowAdd(false);
      setAddForm({
        company: "",
        title: "",
        url: "",
        location: "Remote, US",
        description: "",
      });
      await ws.refresh();
    } catch (e) {
      ws.setError(String(e.message || e));
    }
  };

  return (
    <div className="app">
      <WorkspaceHeader
        personas={ws.personas}
        workingPersonaId={ws.workingPersonaId}
        onSwitch={ws.setWorkingAs}
      />

      {ws.error && <div className="error-banner">{ws.error}</div>}

      <Toolbar
        searchText={ws.searchText}
        onSearchChange={ws.setSearchText}
        profileStatusFilter={ws.profileStatusFilter}
        onProfileStatusChange={ws.setProfileStatusFilter}
        statusFilter={ws.statusFilter}
        onStatusFilterChange={ws.setStatusFilter}
        stats={ws.stats}
        workingPersonaId={ws.workingPersonaId}
      />

      <WorkflowBar
        seqFrom={seqFrom}
        seqTo={seqTo}
        setSeqFrom={setSeqFrom}
        setSeqTo={setSeqTo}
        workingLabel={workingLabel}
        onDeepScan={() =>
          runOrError(() => batch.startBg("deep_scan", { seq_from: seqFrom, seq_to: seqTo }))
        }
        onTailor={() =>
          runOrError(() =>
            batch.startBg("tailor", {
              seq_from: seqFrom,
              seq_to: seqTo,
              persona_id: ws.workingPersonaId,
            })
          )
        }
        onAssistedApply={() => apply.assistedApplyRange(seqFrom, seqTo)}
        onStopBatch={async () => {
          apply.stopApplyQueue();
          await batch.stopBatch();
        }}
        onRefresh={ws.refresh}
        onAddJob={() => setShowAdd(true)}
        onManualApply={() => setShowManualApply(true)}
        onUpload={onUpload}
        onDeleteRange={async () => {
          const msg =
            `Permanently delete all jobs with seq ${seqFrom}–${seqTo}?\n` +
            "Removes DB rows and all persona resume folders, then renumbers remaining jobs to 1…N.";
          if (!window.confirm(msg)) return;
          try {
            batch.setBusy(true);
            const res = await client.deleteJobsRange({
              seq_from: seqFrom,
              seq_to: seqTo,
            });
            logPoong("delete-range", res);
            const rn = res.renumber || {};
            batch.setBatchProgress(
              `Deleted ${res.deleted} job(s), removed ${res.folders_removed ?? 0} folder(s); ` +
                `renumbered to 1–${rn.seq_max ?? "?"} (${rn.folders_renamed ?? 0} folders renamed)`
            );
            if (rn.seq_max != null) setSeqTo(rn.seq_max || 1);
            ws.clearSelection();
            if (
              ws.selected &&
              ws.selected.seq_no >= seqFrom &&
              ws.selected.seq_no <= seqTo
            ) {
              ws.clearSelected();
            }
            ws.setError("");
            await ws.refresh();
          } catch (e) {
            ws.setError(String(e.message || e));
          } finally {
            batch.setBusy(false);
          }
        }}
        onRenumber={async () => {
          const msg =
            "Renumber all jobs to contiguous seq 1…N and rename resume folders for every persona to match?";
          if (!window.confirm(msg)) return;
          try {
            batch.setBusy(true);
            const res = await client.renumberJobs();
            logPoong("renumber", res);
            batch.setBatchProgress(
              res.changed
                ? `Renumbered ${res.renumbered} job(s) → 1–${res.seq_max}; renamed ${res.folders_renamed} folder(s)`
                : `Sequences already contiguous (1–${res.seq_max})`
            );
            if (res.seq_max != null) setSeqTo(res.seq_max || 1);
            setSeqFrom(1);
            ws.setError("");
            await ws.refresh();
          } catch (e) {
            ws.setError(String(e.message || e));
          } finally {
            batch.setBusy(false);
          }
        }}
        checkedCount={ws.checkedIds.size}
        onClearSelection={ws.clearSelection}
        onDeepScanSelected={() => {
          if (!ws.checkedIds.size) return;
          runOrError(() => batch.startBg("deep_scan", { job_ids: [...ws.checkedIds] }));
        }}
        onTailorSelected={() => {
          if (!ws.checkedIds.size) return;
          runOrError(() =>
            batch.startBg("tailor", {
              job_ids: [...ws.checkedIds],
              persona_id: ws.workingPersonaId,
            })
          );
        }}
        onDeleteSelected={async () => {
          const ids = [...ws.checkedIds];
          if (!ids.length) return;
          const msg =
            `Permanently delete ${ids.length} selected job(s)?\n` +
            "Removes DB rows and all persona resume folders, then renumbers remaining jobs to 1…N.";
          if (!window.confirm(msg)) return;
          try {
            batch.setBusy(true);
            const res = await client.deleteJobsIds({ ids });
            logPoong("delete-ids", res);
            const rn = res.renumber || {};
            batch.setBatchProgress(
              `Deleted ${res.deleted} selected job(s), removed ${res.folders_removed ?? 0} folder(s); ` +
                `renumbered to 1–${rn.seq_max ?? "?"} (${rn.folders_renamed ?? 0} folders renamed)`
            );
            if (rn.seq_max != null) setSeqTo(rn.seq_max || 1);
            if (ws.selected && ids.includes(ws.selected.id)) {
              ws.clearSelected();
            }
            ws.clearSelection();
            ws.setError("");
            await ws.refresh();
          } catch (e) {
            ws.setError(String(e.message || e));
          } finally {
            batch.setBusy(false);
          }
        }}
        batchProgress={batch.batchProgress}
        busy={batch.busy}
      />

      <div className="layout">
        <JobsTable
          jobs={ws.jobs}
          selectedId={ws.selected?.id}
          checkedIds={ws.checkedIds}
          statusFilter={ws.statusFilter}
          profileStatusFilter={ws.profileStatusFilter}
          workingPersonaId={ws.workingPersonaId}
          page={ws.page}
          pages={ws.jobsPages}
          pageSize={ws.pageSize}
          total={ws.jobsTotal}
          onPageChange={ws.setPage}
          onPageSizeChange={ws.setPageSize}
          onSelect={ws.selectJob}
          onToggleCheck={ws.toggleCheck}
          onToggleCheckAll={ws.toggleCheckAll}
        />
        <div className="side-column">
          <JobDetail
            job={ws.selected}
            workingPersonaId={ws.workingPersonaId}
            onGenerate={async () => {
              if (!ws.selected || !ws.workingPersonaId) return;
              try {
                batch.setBusy(true);
                await client.tailorOne(ws.selected.id, {
                  persona_id: ws.workingPersonaId,
                });
                batch.setBatchProgress(
                  `Generated resume as ${ws.workingPersonaId} for #${ws.selected.seq_no}`
                );
                await ws.refresh();
              } catch (e) {
                ws.setError(String(e.message || e));
              } finally {
                batch.setBusy(false);
              }
            }}
            onAssistedApply={() =>
              ws.selected && apply.startAssistedOne(ws.selected, ws.workingPersonaId)
            }
            onOpenJd={() => ws.selected && window.open(ws.selected.url, "_blank")}
            onDeepScan={async () => {
              if (!ws.selected) return;
              try {
                await client.deepScanOne(ws.selected.id);
                await ws.refresh();
              } catch (e) {
                ws.setError(String(e.message || e));
              }
            }}
            onSkip={async () => {
              if (!ws.selected) return;
              try {
                if (apply.applySession?.job_id === ws.selected.id) {
                  await apply.clearApplySession(true);
                }
                await client.patchStatus(ws.selected.id, "skipped");
                await ws.refresh();
              } catch (e) {
                ws.setError(String(e.message || e));
              }
            }}
            onMarkApplied={() => {
              if (!ws.selected || !ws.workingPersonaId) return;
              (async () => {
                try {
                  if (apply.applySession?.job_id === ws.selected.id) {
                    await apply.clearApplySession(true);
                  } else {
                    if (apply.applyPollRef.current) clearInterval(apply.applyPollRef.current);
                    apply.setApplySession(null);
                  }
                  await client.patchStatus(ws.selected.id, "applied", {
                    persona_id: ws.workingPersonaId,
                  });
                  batch.setBatchProgress(
                    `Marked applied as ${ws.workingPersonaId} for #${ws.selected.seq_no}`
                  );
                  await ws.refresh();
                } catch (e) {
                  ws.setError(String(e.message || e));
                }
              })();
            }}
            onBlock={async () => {
              if (!ws.selected) return;
              await client.blockCompany(ws.selected.id);
              await ws.refresh();
            }}
            onDelete={async () => {
              if (!ws.selected) return;
              const label = `#${ws.selected.seq_no} ${ws.selected.company}`;
              if (
                !window.confirm(
                  `Permanently delete ${label}?\nRemoves the job row and all persona resume folders, then renumbers remaining jobs to 1…N.`
                )
              ) {
                return;
              }
              try {
                const id = ws.selected.id;
                const res = await client.deleteJob(id);
                logPoong("delete-job", id, res);
                ws.clearSelected();
                ws.removeChecked([id]);
                const rn = res?.renumber || {};
                batch.setBatchProgress(
                  `Deleted job ${label}` +
                    (res?.folders_removed ? ` (folder removed)` : "") +
                    (rn.seq_max != null
                      ? `; renumbered remaining to 1–${rn.seq_max}`
                      : "")
                );
                if (rn.seq_max != null) setSeqTo(rn.seq_max || 1);
                await ws.refresh();
              } catch (e) {
                ws.setError(String(e.message || e));
              }
            }}
          />
          <ApplyConsole
            session={apply.applySession}
            onResume={async () => {
              if (!apply.applySession?.job_id) return;
              const s = await client.applyResume(apply.applySession.job_id);
              apply.setApplySession(s);
            }}
            onConfirm={async () => {
              if (!apply.applySession?.job_id) return;
              const s = await client.applyConfirm(apply.applySession.job_id);
              apply.setApplySession(s);
            }}
            onClose={async () => {
              await apply.clearApplySession(true);
            }}
          />
        </div>
      </div>

      {showAdd && (
        <div className="modal-backdrop" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>Add job</h3>
            {["company", "title", "url", "location"].map((k) => (
              <label key={k}>
                {k}
                <input
                  value={addForm[k]}
                  onChange={(e) => setAddForm({ ...addForm, [k]: e.target.value })}
                />
              </label>
            ))}
            <label>
              description (optional)
              <textarea
                rows={5}
                value={addForm.description}
                onChange={(e) => setAddForm({ ...addForm, description: e.target.value })}
              />
            </label>
            <div className="modal-actions">
              <button type="button" onClick={() => setShowAdd(false)}>
                Cancel
              </button>
              <button type="button" className="primary" onClick={submitAdd}>
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      <ManualApplyModal
        open={showManualApply}
        workingPersonaId={ws.workingPersonaId}
        onClose={() => setShowManualApply(false)}
        onCreated={async () => {
          await ws.refresh();
        }}
        onAssistedApply={async (job) => {
          let full = job;
          try {
            full = await client.getJob(job.id);
          } catch {
            /* use provided job */
          }
          await apply.startAssistedOne(full, ws.workingPersonaId);
        }}
      />
    </div>
  );
}
