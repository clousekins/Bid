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
        const fails = items.filter((i) => i && i.ok === false).length;
        const oks = items.filter((i) => i && i.ok === true).length;
        const lastFail = [...items].reverse().find((i) => i && i.ok === false);
        setBatchProgress(
          `${job.kind} ${job.status}: ${p.done || 0}/${p.total || "?"}` +
            (p.discovered_raw != null ? ` raw=${p.discovered_raw}` : "") +
            (p.after_filters != null ? ` filtered=${p.after_filters}` : "") +
            (p.inserted != null ? ` inserted=${p.inserted}` : ` ok=${oks}`) +
            (p.skipped != null ? ` skipped=${p.skipped}` : ` fail=${fails}`) +
            (p.rejected != null ? ` gated=${p.rejected}` : "") +
            (p.more_available ? " more=1" : "") +
            (p.ats_this_run != null ? ` companies=${p.ats_this_run}` : "") +
            (p.ats_pending != null ? ` waiting=${p.ats_pending}` : "") +
            (p.concurrency ? ` concurrency=${p.concurrency}` : "") +
            (p.generate_as ? ` as=${p.generate_as}` : "") +
            (job.error ? ` ${job.error}` : "") +
            (lastFail?.reason ? ` last_error=${lastFail.reason}` : "")
        );
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
