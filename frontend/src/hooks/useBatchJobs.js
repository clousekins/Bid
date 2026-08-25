import { useEffect, useState } from "react";
import { client, logPoong } from "../api.js";

const KIND_LABELS = {
  discover: "Fetching jobs",
  discover_fresh: "Fetching fresh jobs",
  discover_site: "Fetching by site",
  discover_companies: "Discovering companies",
  deep_scan: "Fetching job descriptions",
  tailor: "Generating resumes",
};

function buildProgress(job) {
  const p = job.progress || {};
  const done = Number(p.done || 0);
  const total = Number(p.total || 0);
  const hasTotal = total > 0;
  const pct = hasTotal
    ? Math.min(99, Math.max(job.status === "queued" ? 2 : 6, Math.round((done / total) * 100)))
    : null;

  const metrics = [];
  const push = (label, value) => {
    if (value == null || value === "") return;
    metrics.push({ label, value: String(value) });
  };

  push("Phase", p.phase);
  push("Site", p.site);
  push("Window", p.window);
  if (p.boards_done != null && p.boards_total != null) {
    push("Boards", `${p.boards_done}/${p.boards_total}`);
  }
  push("Inserted", p.inserted);
  push("JD ready", p.jd_ready);
  push("Needs scan", p.needs_scan);
  push("Gated", p.gated ?? p.rejected);
  push("Skipped", p.skipped);
  push("Raw", p.discovered_raw);
  push("Filtered", p.after_filters);
  push("Clients", p.employer_resolved);
  push("Added", p.added);
  push("Probed", p.probed);
  push("Seen", p.seen);
  push("Provider", p.provider);
  push("As", p.generate_as);
  push("Companies", p.ats_this_run);
  push("Waiting", p.ats_pending);
  if (p.concurrency) push("Workers", p.concurrency);

  const items = p.items || [];
  const oks = items.filter((i) => i && i.ok === true).length;
  const fails = items.filter((i) => i && i.ok === false).length;
  if (items.length && p.inserted == null) {
    push("OK", oks);
    push("Failed", fails);
  }

  const lastFail = [...items].reverse().find((i) => i && i.ok === false);
  const detail =
    p.message ||
    job.error ||
    lastFail?.reason ||
    (job.status === "queued" ? "Queued — waiting to start…" : "");

  return {
    jobId: job.id ?? job.job_id,
    kind: job.kind,
    title: KIND_LABELS[job.kind] || String(job.kind || "Background job"),
    status: job.status,
    done,
    total: hasTotal ? total : null,
    pct,
    indeterminate: !hasTotal && ["queued", "running"].includes(job.status),
    metrics: metrics.slice(0, 8),
    detail: String(detail || "").slice(0, 280),
    error: job.error || "",
    rawLine: null,
  };
}

function formatFallbackLine(job, progress) {
  const parts = [`${progress.title}`, progress.status];
  if (progress.total != null) parts.push(`${progress.done}/${progress.total}`);
  if (progress.detail) parts.push(progress.detail);
  return parts.filter(Boolean).join(" · ");
}

export function useBatchJobs(refresh) {
  const [batchProgress, setBatchProgress] = useState("");
  const [busy, setBusy] = useState(false);
  const [bgJobId, setBgJobId] = useState(null);
  const [activeJob, setActiveJob] = useState(null);

  useEffect(() => {
    if (!bgJobId) return undefined;
    let cancelled = false;
    const tick = async () => {
      try {
        const job = await client.getBgJob(bgJobId);
        if (cancelled) return;
        const progress = buildProgress(job);
        const line = formatFallbackLine(job, progress);
        setActiveJob(progress);
        setBatchProgress(line);
        if (["completed", "failed", "cancelled"].includes(job.status)) {
          setBusy(false);
          setBgJobId(null);
          await refresh();
          return;
        }
      } catch (e) {
        const msg = String(e.message || e);
        setBatchProgress(msg);
        setActiveJob((prev) =>
          prev
            ? { ...prev, status: "failed", error: msg, detail: msg, indeterminate: false }
            : {
                jobId: bgJobId,
                kind: "unknown",
                title: "Background job",
                status: "failed",
                done: 0,
                total: null,
                pct: null,
                indeterminate: false,
                metrics: [],
                detail: msg,
                error: msg,
              }
        );
        logPoong("bg-job poll error", e);
      }
      setTimeout(tick, 1100);
    };
    tick();
    return () => {
      cancelled = true;
    };
  }, [bgJobId, refresh]);

  const startBg = async (kind, body) => {
    setBusy(true);
    setActiveJob({
      jobId: null,
      kind,
      title: KIND_LABELS[kind] || kind,
      status: "queued",
      done: 0,
      total: null,
      pct: 4,
      indeterminate: true,
      metrics: [],
      detail: "Starting…",
      error: "",
    });
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
      setBatchProgress(`${KIND_LABELS[kind] || kind} queued #${res.job_id}`);
      setActiveJob((prev) => ({
        ...(prev || {}),
        jobId: res.job_id,
        kind,
        title: KIND_LABELS[kind] || kind,
        status: "queued",
        detail: `Queued #${res.job_id}`,
        indeterminate: true,
        pct: 6,
      }));
    } catch (e) {
      setBusy(false);
      setActiveJob(null);
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
    setActiveJob((prev) =>
      prev
        ? { ...prev, status: "cancelled", detail: "Stop requested", indeterminate: false }
        : null
    );
  };

  const dismissActiveJob = () => {
    if (busy) return;
    setActiveJob(null);
    setBatchProgress("");
  };

  return {
    batchProgress,
    setBatchProgress,
    busy,
    setBusy,
    bgJobId,
    activeJob,
    dismissActiveJob,
    startBg,
    stopBatch,
  };
}
