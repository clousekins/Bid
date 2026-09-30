import { useEffect, useState } from "react";
import { client, logPoong } from "./api.js";
import { commitSeqRange } from "./utils/seqRange.js";
import { useJobsWorkspace } from "./hooks/useJobsWorkspace.js";
import { useBatchJobs } from "./hooks/useBatchJobs.js";
import { useAssistedApply } from "./hooks/useAssistedApply.js";
import { usePrepQueue } from "./hooks/usePrepQueue.js";
import { shortPersona, personaLabel } from "./utils/persona.js";
import { formatGateList } from "./utils/gates.js";
import WorkspaceHeader from "./components/WorkspaceHeader.jsx";
import HeroShowcase, { loadHeroCollapsed, saveHeroCollapsed } from "./components/HeroShowcase.jsx";
import SideRail from "./components/SideRail.jsx";
import Toolbar from "./components/Toolbar.jsx";
import WorkflowBar from "./components/WorkflowBar.jsx";
import JobsTable from "./components/JobsTable.jsx";
import JobDetail from "./components/JobDetail.jsx";
import ApplyConsole from "./components/ApplyConsole.jsx";
import ManualApplyModal from "./components/ManualApplyModal.jsx";
import CompanyCatalogModal from "./components/CompanyCatalogModal.jsx";
import JobProgressPanel from "./components/JobProgressPanel.jsx";

export default function WorkspaceApp({ onGoHome, theme, onToggleTheme }) {
  const ws = useJobsWorkspace();
  const batch = useBatchJobs(ws.refresh);
  const prep = usePrepQueue(ws.refresh);
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
  const [showCompanyCatalog, setShowCompanyCatalog] = useState(false);
  const [manualJob, setManualJob] = useState(null);
  const [manualOpenKey, setManualOpenKey] = useState(0);
  const [addForm, setAddForm] = useState({
    company: "",
    title: "",
    url: "",
    location: "Remote, US",
    description: "",
  });
  const [addBusy, setAddBusy] = useState(false);
  const [addError, setAddError] = useState("");
  const [heroCollapsed, setHeroCollapsed] = useState(() => loadHeroCollapsed());

  const workingLabel = shortPersona(ws.workingPersonaId);
  const workingDisplayName = personaLabel(ws.workingPersonaId);
  const seqMax = Number(ws.stats?.max_seq || ws.stats?.total || 0);

  const scrollToSection = (id) => {
    const map = {
      top: ".app-main-top",
      workflow: "#workflow-section",
      jobs: "#jobs-section",
      detail: "#detail-section",
    };
    document.querySelector(map[id] || map.top)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const toggleHero = () => {
    setHeroCollapsed((v) => {
      const next = !v;
      saveHeroCollapsed(next);
      return next;
    });
  };

  useEffect(() => {
    if (!seqMax) return;
    setSeqFrom((from) => {
      if (from === "") return from;
      const n = Number(from);
      if (!Number.isFinite(n) || n < 1) return 1;
      return Math.min(Math.trunc(n), seqMax);
    });
    setSeqTo((to) => {
      if (to === "") return to;
      const n = Number(to);
      if (!Number.isFinite(n) || n < 1) return seqMax;
      return Math.min(Math.trunc(n), seqMax);
    });
  }, [seqMax]);

  const committedRange = () => {
    const next = commitSeqRange(seqFrom, seqTo, seqMax || 1);
    setSeqFrom(next.from);
    setSeqTo(next.to);
    return next;
  };

  const openManual = (job = null) => {
    setManualJob(job);
    setManualOpenKey((n) => n + 1);
    setShowManualApply(true);
  };

  const closeManual = () => {
    setShowManualApply(false);
    setManualJob(null);
  };

  const runOrError = async (fn) => {
    try {
      ws.setError("");
      await fn();
    } catch (e) {
      ws.setError(String(e.message || e));
    }
  };

  const markSelectedApplied = async () => {
    if (!ws.selected || !ws.workingPersonaId) {
      ws.setError("Select a job and working persona first (Ctrl+M marks applied).");
      return;
    }
    try {
      ws.setError("");
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
  };

  useEffect(() => {
    const onKey = (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key !== "m" && e.key !== "M") return;
      const tag = (e.target?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || e.target?.isContentEditable) {
        return;
      }
      e.preventDefault();
      markSelectedApplied();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ws.selected?.id, ws.workingPersonaId]);

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
    setAddError("");
    try {
      setAddBusy(true);
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
      const detail = String(e.message || e);
      setAddError(formatGateList([detail.replace(/^\d+:\s*/, "")], { company: addForm.company }) || detail);
    } finally {
      setAddBusy(false);
    }
  };

  const fetchListedJd = async (job) => {
    if (!job?.id || !ws.workingPersonaId) return;
    try {
      ws.setError("");
      batch.setBusy(true);
      const res = await client.manualFetch({
        persona_id: ws.workingPersonaId,
        job_id: job.id,
        url: job.url,
      });
      const extra = { company: res.company || job.company, seq: job.seq_no };
      if (res.blockers?.length) {
        ws.setError(formatGateList(res.blockers, extra));
      } else {
        batch.setBatchProgress(
          `Fetched JD for #${job.seq_no} ${res.company || job.company} (${(res.description || "").length} chars)`
        );
      }
      await ws.refresh();
    } catch (e) {
      ws.setError(String(e.message || e));
    } finally {
      batch.setBusy(false);
    }
  };

  const fetchAddJd = async () => {
    setAddError("");
    const url = (addForm.url || "").trim();
    if (!url) {
      setAddError("Paste a job URL first.");
      return;
    }
    try {
      setAddBusy(true);
      const res = await client.manualFetch({
        persona_id: ws.workingPersonaId,
        url,
      });
      const extra = { company: res.company, seq: res.job?.seq_no };
      if (res.description || res.company) {
        setAddForm((prev) => ({
          ...prev,
          company: res.company || prev.company,
          title: res.title || prev.title,
          url: res.url || prev.url,
          location: res.location || prev.location,
          description: res.description || prev.description,
        }));
      }
      if (res.blockers?.length) {
        setAddError(formatGateList(res.blockers, extra));
        return;
      }
      if (res.warnings?.length) {
        setAddError(formatGateList(res.warnings, extra));
      }
    } catch (e) {
      setAddError(String(e.message || e));
    } finally {
      setAddBusy(false);
    }
  };

  // Resetting only moves the mark the count runs from - every application
  // stays on record - so this needs no confirmation.
  const handleResetToday = async () => {
    try {
      await client.resetTodayCounter();
      await ws.refresh();
    } catch (e) {
      ws.setError(String(e.message || e));
    }
  };

  return (
    <div className="app-shell">
      <SideRail
        workingPersonaId={ws.workingPersonaId}
        workingName={workingDisplayName}
        heroCollapsed={heroCollapsed}
        onToggleHero={toggleHero}
        onNavigate={scrollToSection}
        onGoHome={onGoHome}
        theme={theme}
        onToggleTheme={onToggleTheme}
      />

      <div className="app-main">
        <HeroShowcase
          stats={ws.stats}
          workingName={workingDisplayName}
          collapsed={heroCollapsed}
          onToggleCollapsed={toggleHero}
        />

        <div className="app-workspace glass-panel">
          <div className="app-main-top">
            <WorkspaceHeader
              personas={ws.personas}
              workingPersonaId={ws.workingPersonaId}
              onSwitch={ws.setWorkingAs}
              stats={ws.stats}
              onRefresh={ws.refresh}
              onResetToday={handleResetToday}
              busy={false}
            />
          </div>

      {ws.error ? (
        <div className="alert-banner error" role="alert">
          <span>{ws.error}</span>
          <button type="button" className="alert-dismiss" onClick={() => ws.setError("")} aria-label="Dismiss">
            ×
          </button>
        </div>
      ) : null}

      <Toolbar
        searchText={ws.searchText}
        onSearchChange={ws.setSearchText}
        profileStatusFilter={ws.profileStatusFilter}
        hideOwnEmployers={ws.hideOwnEmployers}
        onHideOwnEmployersChange={ws.setHideOwnEmployers}
        ownEmployerInfo={ws.ownEmployerInfo}
        onProfileStatusChange={ws.setProfileStatusFilter}
        statusFilter={ws.statusFilter}
        onStatusFilterChange={ws.setStatusFilter}
        countryFilter={ws.countryFilter}
        onCountryFilterChange={ws.setCountryFilter}
        countryOptions={ws.countryOptions}
        stats={ws.stats}
        workingPersonaId={ws.workingPersonaId}
      />

      <div id="workflow-section">
      <WorkflowBar
        seqFrom={seqFrom}
        seqTo={seqTo}
        seqMax={seqMax}
        ats={ws.stats?.ats}
        setSeqFrom={setSeqFrom}
        setSeqTo={setSeqTo}
        workingLabel={workingLabel}
        onDeepScan={() => {
          const { from, to } = committedRange();
          return runOrError(() => batch.startBg("deep_scan", { seq_from: from, seq_to: to }));
        }}
        onTailor={() => {
          const { from, to } = committedRange();
          return runOrError(() =>
            batch.startBg("tailor", {
              seq_from: from,
              seq_to: to,
              persona_id: ws.workingPersonaId,
            })
          );
        }}
        onAssistedApply={() => {
          const { from, to } = committedRange();
          return apply.assistedApplyRange(from, to);
        }}
        onStopBatch={async () => {
          apply.stopApplyQueue();
          await batch.stopBatch();
        }}
        onAddJob={() => setShowAdd(true)}
        onManualApply={() => openManual(null)}
        onDiscover={(domains) =>
          runOrError(() =>
            batch.startBg("discover", {
              persona_id: ws.workingPersonaId,
              domains,
            })
          )
        }
        onDiscoverFresh={(domains) =>
          runOrError(() =>
            batch.startBg("discover_fresh", {
              persona_id: ws.workingPersonaId,
              domains,
            })
          )
        }
        onDiscoverCompanies={() =>
          runOrError(() =>
            batch.startBg("discover_companies", { persona_id: ws.workingPersonaId })
          )
        }
        onOpenCompanyCatalog={() => setShowCompanyCatalog(true)}
        onFetchSite={({ site, window, max_jobs, domains, roles }) =>
          runOrError(() =>
            batch.startBg("discover_site", {
              persona_id: ws.workingPersonaId,
              site,
              window,
              max_jobs,
              domains,
              roles,
            })
          )
        }
        onSectorImport={async (rows) => {
          // Reuse the normal ingest path so the gates, dedupe and folder
          // creation behave exactly as they do for any other listing.
          let added = 0;
          const skipped = [];
          for (const row of rows) {
            try {
              await client.createJob({
                company: row.company,
                title: row.title,
                url: row.url,
                location: row.location || "Remote, US",
                description: row.description || undefined,
                source: `sector:${row.source}`,
                persona_id: ws.workingPersonaId || undefined,
              });
              added += 1;
            } catch (e) {
              skipped.push(`${row.company}: ${String(e.message || e).replace(/^\d+:\s*/, "")}`);
            }
          }
          batch.setBatchProgress(
            `Sector import: ${added} added` + (skipped.length ? `, ${skipped.length} skipped` : "")
          );
          if (skipped.length) ws.setError(formatGateList(skipped.slice(0, 4)));
          await ws.refresh();
        }}
        onUpload={onUpload}
        onDeleteRange={async () => {
          const { from, to } = committedRange();
          const msg =
            `Permanently delete all jobs with seq ${from}–${to}?\n` +
            "Removes DB rows and all persona resume folders, then renumbers remaining jobs to 1…N.";
          if (!window.confirm(msg)) return;
          try {
            batch.setBusy(true);
            const res = await client.deleteJobsRange({
              seq_from: from,
              seq_to: to,
            });
            logPoong("delete-range", res);
            const rn = res.renumber || {};
            batch.setBatchProgress(
              `Deleted ${res.deleted} job(s), removed ${res.folders_removed ?? 0} folder(s); ` +
                `renumbered to 1–${rn.seq_max ?? "?"} (${rn.folders_renamed ?? 0} folders renamed)`
            );
            if (rn.seq_max != null) {
              setSeqFrom(1);
              setSeqTo(rn.seq_max || 1);
            }
            ws.clearSelection();
            if (
              ws.selected &&
              ws.selected.seq_no >= from &&
              ws.selected.seq_no <= to
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
        onRegenerateSelected={() => {
          const n = ws.checkedIds.size;
          if (!n) return;
          // Overwrites resumes that are already done, so it asks first.
          const msg =
            `Regenerate ${n} selected job(s) as ${ws.workingPersonaId}?\n` +
            "Resumes already generated for this persona are tailored again and overwritten.";
          if (!window.confirm(msg)) return;
          runOrError(() =>
            batch.startBg("tailor", {
              job_ids: [...ws.checkedIds],
              persona_id: ws.workingPersonaId,
              force: true,
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
        busy={batch.busy}
      />
      </div>

      <div className="layout">
        <div id="jobs-section">
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
          sort={ws.sort}
          sortDir={ws.sortDir}
          onSort={ws.setSortKey}
          onPageChange={ws.setPage}
          onPageSizeChange={ws.setPageSize}
          onSelect={ws.selectJob}
          onToggleCheck={ws.toggleCheck}
          onToggleCheckAll={ws.toggleCheckAll}
          generatingIds={prep.generatingIds}
          onContinue={(job) => openManual(job)}
          onFetchJd={fetchListedJd}
          onEmptyFetch={() => scrollToSection("workflow")}
          listParams={{
            status: ws.statusFilter || undefined,
            profile: ws.workingPersonaId || undefined,
            profile_status:
              ws.workingPersonaId && ws.profileStatusFilter
                ? ws.profileStatusFilter
                : undefined,
            q: ws.searchQuery || undefined,
            sort: ws.sort,
            sort_dir: ws.sortDir,
          }}
          onExportError={(msg) => ws.setError(msg)}
        />
        </div>
        <div className="side-column" id="detail-section">
          <JobDetail
            job={ws.selected}
            workingPersonaId={ws.workingPersonaId}
            generating={prep.generatingIds.has(ws.selected?.id)}
            onContinueManual={() => ws.selected && openManual(ws.selected)}
            onGenerate={async () => {
              if (!ws.selected || !ws.workingPersonaId) return;
              const job = ws.selected;
              try {
                batch.setBusy(true);
                const res = await client.tailorOne(job.id, {
                  persona_id: ws.workingPersonaId,
                });
                // The endpoint answers a failed tailor with 200 and ok:false;
                // without this the bar said "Generated" over a failure.
                if (res && res.ok === false) {
                  throw new Error(res.reason || "Resume generation failed");
                }
                batch.setBatchProgress(
                  `Generated resume as ${ws.workingPersonaId} for #${job.seq_no}`
                );
                await ws.refresh();
              } catch (e) {
                ws.setError(String(e.message || e));
              } finally {
                batch.setBusy(false);
              }
            }}
            onUpdateFields={async (fields) => {
              if (!ws.selected) return;
              const updated = await client.patchJob(ws.selected.id, fields);
              ws.setSelected(updated);
              await ws.refresh();
            }}
            onSetOutcome={async (body) => {
              if (!ws.selected) return;
              await client.setOutcome(ws.selected.id, body);
              const updated = await client.getJob(ws.selected.id, ws.workingPersonaId);
              ws.setSelected(updated);
              await ws.refresh();
            }}
            onAssistedApply={() =>
              ws.selected && apply.startAssistedOne(ws.selected, ws.workingPersonaId)
            }
            onOpenJd={() => ws.selected && window.open(ws.selected.url, "_blank")}
            onDeepScan={() => fetchListedJd(ws.selected)}
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
            onReject={async () => {
              if (!ws.selected) return;
              try {
                // Same stop as Skip: an apply already running on this job has
                // to end with it, or a rejected job could still be submitted.
                if (apply.applySession?.job_id === ws.selected.id) {
                  await apply.clearApplySession(true);
                }
                await client.patchStatus(ws.selected.id, "rejected");
                await ws.refresh();
              } catch (e) {
                ws.setError(String(e.message || e));
              }
            }}
            onUnreject={async () => {
              if (!ws.selected) return;
              try {
                // Back to where the pipeline can pick it up: with a usable JD
                // that is jd_ready, without one it needs fetching again.
                const back =
                  (ws.selected.description || "").length >= 50 ? "jd_ready" : "new";
                await client.patchStatus(ws.selected.id, back);
                await ws.refresh();
              } catch (e) {
                ws.setError(String(e.message || e));
              }
            }}
            onMakeInterview={async () => {
              if (!ws.selected) return;
              try {
                await client.patchStatus(ws.selected.id, "interview");
                await ws.refresh();
              } catch (e) {
                ws.setError(String(e.message || e));
              }
            }}
            onUnmakeInterview={async () => {
              if (!ws.selected) return;
              try {
                // Same landing as Un-reject. Going back to "applied" would run
                // the mark-applied path, which needs a package this job may
                // never have had — an interview can be recorded by hand for an
                // application made outside the tool.
                const back =
                  (ws.selected.description || "").length >= 50 ? "jd_ready" : "new";
                await client.patchStatus(ws.selected.id, back);
                await ws.refresh();
              } catch (e) {
                ws.setError(String(e.message || e));
              }
            }}
            onMarkApplied={markSelectedApplied}
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
            job={ws.selected}
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

      <JobProgressPanel
        activeJob={batch.activeJob}
        busy={batch.busy}
        prepItems={prep.items}
        workingPersonaId={ws.workingPersonaId}
        onOpenJob={(id) => ws.selectJob({ id })}
        onStop={async () => {
          apply.stopApplyQueue();
          await batch.stopBatch();
        }}
        onDismissJob={batch.dismissActiveJob}
        onOpenPrep={async (row) => {
          try {
            const full = await client.getJob(row.jobId, ws.workingPersonaId);
            openManual(full);
          } catch {
            openManual({
              id: row.jobId,
              seq_no: row.seq,
              company: row.company,
              title: row.title,
            });
          }
        }}
        onDismissPrep={prep.dismiss}
      />
        </div>
      </div>

      {showAdd && (
        <div className="modal-backdrop" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0 }}>Add job</h3>
            {addError ? <div className="error-banner">{addError}</div> : null}
            {["company", "title", "url", "location"].map((k) => (
              <label key={k}>
                {k}
                <input
                  value={addForm[k]}
                  disabled={addBusy}
                  onChange={(e) => setAddForm({ ...addForm, [k]: e.target.value })}
                />
              </label>
            ))}
            <div className="modal-actions">
              <button type="button" className="primary" disabled={addBusy} onClick={fetchAddJd}>
                {addBusy ? "Fetching…" : "Fetch JD"}
              </button>
            </div>
            <label>
              description (optional)
              <textarea
                rows={5}
                value={addForm.description}
                disabled={addBusy}
                onChange={(e) => setAddForm({ ...addForm, description: e.target.value })}
              />
            </label>
            <div className="modal-actions">
              <button type="button" onClick={() => setShowAdd(false)}>
                Cancel
              </button>
              <button type="button" className="primary" disabled={addBusy} onClick={submitAdd}>
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      <ManualApplyModal
        open={showManualApply}
        openKey={manualOpenKey}
        workingPersonaId={ws.workingPersonaId}
        resumeJob={manualJob}
        prepItem={
          manualJob?.id
            ? prep.itemFor(manualJob.id, ws.workingPersonaId)
            : null
        }
        onClose={closeManual}
        onQueueGenerate={(job) => prep.enqueueTailor(job, ws.workingPersonaId)}
        onCreated={async (job) => {
          if (job?.id) setManualJob(job);
          await ws.refresh();
        }}
        onAssistedApply={async (job) => {
          let full = job;
          try {
            full = await client.getJob(job.id, ws.workingPersonaId);
          } catch {
            /* use provided job */
          }
          await apply.startAssistedOne(full, ws.workingPersonaId);
        }}
      />

      <CompanyCatalogModal
        open={showCompanyCatalog}
        onClose={() => setShowCompanyCatalog(false)}
        summary={ws.stats?.ats}
      />
    </div>
  );
}
