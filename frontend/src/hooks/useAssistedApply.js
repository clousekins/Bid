import { useRef, useState } from "react";
import { client, logPoong } from "../api.js";

export function useAssistedApply({
  workingPersonaId,
  refresh,
  setError,
  selectedIdRef,
  setSelected,
}) {
  const [applySession, setApplySession] = useState(null);
  const applyPollRef = useRef(null);
  const applyQueueRef = useRef([]);

  const pollApply = (jobId) => {
    if (applyPollRef.current) clearInterval(applyPollRef.current);
    logPoong("apply poll start", jobId);
    applyPollRef.current = setInterval(async () => {
      try {
        const s = await client.applyStatus(jobId);
        setApplySession(s);
        const last = (s.logs || []).slice(-1)[0];
        if (last) logPoong("apply", s.status, last.message);
        if (["applied", "failed", "cancelled", "none"].includes(s.status)) {
          logPoong("apply terminal", s.status, s.error);
          clearInterval(applyPollRef.current);
          applyPollRef.current = null;
          await refresh();
          const next = applyQueueRef.current.shift();
          if (next) startAssistedOne(next.job, next.persona_id);
        }
      } catch (e) {
        logPoong("apply poll error", e);
      }
    }, 800);
  };

  const startAssistedOne = async (job, persona_id = workingPersonaId) => {
    const readyPkgs = (job.packages || []).filter(
      (p) =>
        (p.status === "resume_ready" || p.status === "applying") && p.folder_path
    );
    if (!readyPkgs.length) {
      setError(
        `No tailored resume package for #${job.seq_no} ${job.company} — generate a resume first.`
      );
      return;
    }
    if (!persona_id) {
      setError("Choose a Working as profile first.");
      return;
    }
    if (!readyPkgs.some((p) => p.persona_id === persona_id)) {
      setError(`No resume package for profile ${persona_id} on this job.`);
      return;
    }
    const applyPersona = persona_id;
    const url = job.apply_url || job.url;
    logPoong("assisted-apply begin", {
      id: job.id,
      seq: job.seq_no,
      company: job.company,
      url,
      persona_id: applyPersona,
    });
    const win = window.open(url, "_blank");
    logPoong("window.open result", { blocked: !win, url });
    if (!win) {
      setError("Popup blocked — allow popups for 127.0.0.1, or Octo will open the URL itself.");
    }
    try {
      logPoong("POST /api/apply/start", job.id, applyPersona);
      const s = await client.applyStart(job.id, {
        ui_opened_tab: true,
        persona_id: applyPersona,
      });
      logPoong("apply start response", s);
      setApplySession(s);
      selectedIdRef.current = job.id;
      setSelected(job);
      setError("");
      pollApply(job.id);
    } catch (e) {
      logPoong("apply start failed", e);
      setError(String(e.message || e));
    }
  };

  const assistedApplyRange = async (seqFrom, seqTo) => {
    try {
      if (!workingPersonaId) {
        setError("Choose a Working as profile first.");
        return;
      }
      logPoong("assisted-apply range", { seqFrom, seqTo, persona_id: workingPersonaId });
      const preview = await client.applyPreview({
        seq_from: seqFrom,
        seq_to: seqTo,
        persona_id: workingPersonaId,
      });
      const list = preview.jobs || [];
      logPoong("apply-preview", list.length, list.map((j) => j.seq_no));
      if (!list.length) {
        setError("No resume_ready jobs in range — generate resumes first");
        return;
      }
      applyQueueRef.current = list
        .slice(1)
        .map((job) => ({ job, persona_id: workingPersonaId }));
      await startAssistedOne(list[0], workingPersonaId);
    } catch (e) {
      setError(String(e.message || e));
      logPoong("assisted-apply range fail", e);
    }
  };

  const clearApplySession = async (cancel = true) => {
    if (cancel && applySession?.job_id) {
      await client.applyCancel(applySession.job_id).catch(() => {});
    }
    if (applyPollRef.current) clearInterval(applyPollRef.current);
    applyPollRef.current = null;
    setApplySession(null);
  };

  const stopApplyQueue = () => {
    applyQueueRef.current = [];
  };

  return {
    applySession,
    setApplySession,
    applyPollRef,
    startAssistedOne,
    assistedApplyRange,
    clearApplySession,
    stopApplyQueue,
  };
}
