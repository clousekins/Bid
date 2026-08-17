import { useCallback, useEffect, useRef, useState } from "react";
import { client, logPoong } from "../api.js";

function itemKey(jobId, personaId) {
  return `${jobId}:${personaId || ""}`;
}

export function usePrepQueue(refresh) {
  const [items, setItems] = useState([]);
  const itemsRef = useRef([]);
  itemsRef.current = items;

  const upsert = useCallback((key, patch) => {
    setItems((prev) => {
      const idx = prev.findIndex((row) => row.key === key);
      const next =
        idx < 0
          ? [...prev, { key, ...patch }]
          : prev.map((row, i) => (i === idx ? { ...row, ...patch } : row));
      itemsRef.current = next;
      return next;
    });
  }, []);

  const enqueueTailor = useCallback(
    async (job, personaId) => {
      if (!job?.id || !personaId) {
        throw new Error("Job and Working-as profile are required");
      }
      const key = itemKey(job.id, personaId);
      const existing = itemsRef.current.find((row) => row.key === key);
      if (existing && (existing.status === "queued" || existing.status === "running")) {
        return existing.bgJobId;
      }
      upsert(key, {
        jobId: job.id,
        seq: job.seq_no,
        company: job.company,
        title: job.title,
        personaId,
        status: "queued",
        done: 0,
        total: 1,
        pct: 6,
        error: "",
        bgJobId: null,
      });
      logPoong("prep enqueue tailor", job.id, personaId);
      try {
        const res = await client.tailorRange({
          job_ids: [job.id],
          persona_id: personaId,
        });
        upsert(key, {
          bgJobId: res.job_id,
          status: "running",
          pct: 12,
        });
        return res.job_id;
      } catch (e) {
        upsert(key, {
          status: "failed",
          error: String(e.message || e),
          pct: 0,
        });
        throw e;
      }
    },
    [upsert]
  );

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const running = itemsRef.current.filter(
        (row) => row.bgJobId && (row.status === "queued" || row.status === "running")
      );
      if (!running.length) {
        if (!cancelled) setTimeout(tick, 1400);
        return;
      }
      for (const item of running) {
        try {
          const job = await client.getBgJob(item.bgJobId);
          const p = job.progress || {};
          const done = Number(p.done || 0);
          const total = Math.max(1, Number(p.total || 1));
          const terminal = ["completed", "failed", "cancelled"].includes(job.status);
          if (terminal) {
            const failed = job.status !== "completed";
            upsert(item.key, {
              status: failed ? "failed" : "ready",
              done: total,
              total,
              pct: 100,
              error: job.error || "",
            });
            await refresh();
          } else {
            const pct = Math.min(94, Math.max(12, Math.round((done / total) * 100) || 18));
            upsert(item.key, {
              status: "running",
              done,
              total,
              pct,
            });
          }
        } catch (e) {
          logPoong("prep poll error", e);
        }
      }
      if (!cancelled) setTimeout(tick, 1100);
    };
    tick();
    return () => {
      cancelled = true;
    };
  }, [refresh, upsert]);

  const dismiss = useCallback((key) => {
    setItems((prev) => {
      const next = prev.filter((row) => row.key !== key);
      itemsRef.current = next;
      return next;
    });
  }, []);

  const generatingIds = new Set(
    items.filter((row) => row.status === "queued" || row.status === "running").map((row) => row.jobId)
  );

  return {
    items,
    enqueueTailor,
    dismiss,
    generatingIds,
    itemFor: (jobId, personaId) => items.find((row) => row.key === itemKey(jobId, personaId)),
  };
}
