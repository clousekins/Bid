import { useEffect, useState } from "react";
import { client, logPoong } from "../api.js";

export function useBatchJobs(refresh) {
  const [batchProgress, setBatchProgress] = useState("");
  const [busy, setBusy] = useState(false);
  const [bgJobId, setBgJobId] = useState(null);

  useEffect(() => {
    if (!bgJobId) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const job = await client.getBgJob(bgJobId);
        if (cancelled) return;
        const p = job.progress || {};
        const items = p.items || [];
        const lastFail = [...items].reverse().find((i) => i && i.ok === false);
        let line = `${job.kind} ${job.status}: ${p.done || 0}/${p.total || "?"}`;
        if (p.discovered_raw != null) line += ` raw=${p.discovered_raw}`;
        if (p.after_filters != null) line += ` filtered=${p.after_filters}`;
        if (job.kind === "discover" || job.kind === "discover_fresh" || job.kind === "discover_site") {
          if (p.phase) line += ` phase=${p.phase}`;
          if (p.site) line += ` site=${p.site}`;
          if (p.window) line += ` window=${p.window}`;
          if (p.mode) line += ` mode=${p.mode}`;
          if (p.posted_within_days != null) line += ` days=${p.posted_within_days}`;
          if (p.boards_done != null && p.boards_total != null) {
            line += ` boards=${p.boards_done}/${p.boards_total}`;
          }
          if (p.inserted != null) line += ` inserted=${p.inserted}`;
          if (p.jd_ready != null) line += ` jd_ready=${p.jd_ready}`;
          if (p.needs_scan != null) line += ` needs_scan=${p.needs_scan}`;
          if (p.gated != null) line += ` gated=${p.gated}`;
          if (p.skipped != null) line += ` skipped=${p.skipped}`;
          if (p.employer_resolved != null) line += ` clients=${p.employer_resolved}`;
          if (p.auto_scan_queued) line += ` auto_scan=${p.auto_scan_queued}`;
          const drops = p.prefilter_drops;
          if (drops && typeof drops === "object") {
            const top = Object.entries(drops)
              .sort((a, b) => Number(b[1]) - Number(a[1]))
              .slice(0, 3)
              .map(([k, v]) => `${k}:${v}`)
              .join(",");
            if (top) line += ` drops=${top}`;
          }
        } else if (job.kind === "discover_companies") {
          if (p.provider) line += ` via=${p.provider}`;
          if (p.fallback_from) line += ` fallback_from=${p.fallback_from}`;
          if (p.probed != null) line += ` probed=${p.probed}`;
          if (p.added != null) line += ` added=${p.added}`;
          if (p.seen != null) line += ` seen=${p.seen}`;
          if (p.message) line += ` ${p.message}`;
        } else {
          const oks = items.filter((i) => i && i.ok === true).length;
          const fails = items.filter((i) => i && i.ok === false).length;
          if (p.inserted != null) line += ` inserted=${p.inserted}`;
          else line += ` ok=${oks} fail=${fails}`;
          if (p.skipped != null) line += ` skipped=${p.skipped}`;
          if (p.rejected != null) line += ` gated=${p.rejected}`;
        }
        if (p.more_available) line += " more=1";
        if (p.ats_this_run != null) line += ` companies=${p.ats_this_run}`;
        if (p.ats_pending != null) line += ` waiting=${p.ats_pending}`;
        if (p.concurrency) line += ` concurrency=${p.concurrency}`;
        if (p.generate_as) line += ` as=${p.generate_as}`;
        if (job.error) line += ` ${job.error}`;
        if (lastFail?.reason) line += ` last=${lastFail.reason}`;
        setBatchProgress(line);
        if (["completed", "failed", "cancelled"].includes(job.status)) {
          setBusy(false);
          setBgJobId(null);
          await refresh();
          return;
        }
      } catch (e) {
        setBatchProgress(String(e.message || e));
        logPoong("bg-job poll error", e);
      }
      setTimeout(tick, 1200);
    };
    tick();
    return () => {
      cancelled = true;
    };
  }, [bgJobId, refresh]);

  const startBg = async (kind, body) => {
    setBusy(true);
    try {
      logPoong("enqueue", kind, body);
      const res =
        kind === "deep_scan"
          ? await client.deepScanRange(body)
          : kind === "discover"
            ? await client.discoverJobs(body)
            : kind === "discover_fresh"
              ? await client.discoverFreshJobs(body)
              : kind === "discover_companies"
                ? await client.discoverCompanies(body)
                : kind === "discover_site"
                  ? await client.discoverSite(body)
                  : await client.tailorRange(body);
      logPoong("enqueued", kind, res);
      setBgJobId(res.job_id);
      setBatchProgress(`${kind} queued #${res.job_id}`);
    } catch (e) {
      setBusy(false);
      logPoong("enqueue fail", e);
      throw e;
    }
  };

  const stopBatch = async () => {
    logPoong("stop batch", { bgJobId });
    if (bgJobId) {
      try {
        await client.cancelBgJob(bgJobId);
      } catch {
        /* ignore */
      }
    }
    setBusy(false);
    setBatchProgress("Stop requested");
  };

  return {
    batchProgress,
    setBatchProgress,
    busy,
    setBusy,
    bgJobId,
    startBg,
    stopBatch,
  };
}
