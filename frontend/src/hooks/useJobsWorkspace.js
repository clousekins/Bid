import { useCallback, useEffect, useRef, useState } from "react";
import { client, logPoong } from "../api.js";
import { personaAccent } from "../utils/persona.js";

export function useJobsWorkspace() {
  const [jobs, setJobs] = useState([]);
  const [stats, setStats] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [workingPersonaId, setWorkingPersonaId] = useState("");
  const [profileStatusFilter, setProfileStatusFilter] = useState("");
  const [searchText, setSearchText] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [jobsTotal, setJobsTotal] = useState(0);
  const [jobsPages, setJobsPages] = useState(1);
  const [checkedIds, setCheckedIds] = useState(() => new Set());
  const [personas, setPersonas] = useState([]);
  const selectedIdRef = useRef(null);
  const selectReqRef = useRef(0);
  const lastCheckRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const params = { page, page_size: pageSize };
      if (statusFilter) params.status = statusFilter;
      if (workingPersonaId) params.profile = workingPersonaId;
      if (workingPersonaId && profileStatusFilter) {
        params.profile_status = profileStatusFilter;
      }
      if (searchQuery) params.q = searchQuery;
      const [list, st] = await Promise.all([client.listJobs(params), client.stats()]);
      const rows = list.jobs || [];
      setJobs(rows);
      setJobsTotal(Number(list.total ?? rows.length));
      setJobsPages(Math.max(1, Number(list.pages ?? 1)));
      if (list.page != null && Number(list.page) !== page && Number(list.total || 0) > 0) {
        const serverPage = Number(list.page);
        if (serverPage >= 1) setPage(serverPage);
      }
      setStats(st);
      const sid = selectedIdRef.current;
      if (sid != null) {
        const updated = rows.find((j) => j.id === sid);
        if (updated) {
          setSelected((prev) => {
            if (!prev || prev.id !== sid) return prev;
            return {
              ...prev,
              ...updated,
              description: updated.description ?? prev.description,
            };
          });
        }
      }
    } catch (e) {
      setError(String(e.message || e));
      logPoong("refresh error", e);
    }
  }, [statusFilter, workingPersonaId, profileStatusFilter, searchQuery, page, pageSize]);

  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(searchText.trim()), 250);
    return () => clearTimeout(timer);
  }, [searchText]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, workingPersonaId, profileStatusFilter, searchQuery, pageSize]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    client.health().then(() => logPoong("health ok")).catch((e) => logPoong("health fail", e));
    client
      .browserDiag()
      .then((d) => logPoong("browser-diag", d))
      .catch((e) => logPoong("browser-diag fail", e));
    client
      .personas()
      .then((d) => {
        const list = (d.personas || []).filter((p) => p.enabled !== false);
        setPersonas(list);
        const active = d.active || list.find((p) => p.active)?.id || "phong_v_cao";
        setWorkingPersonaId(active);
      })
      .catch((e) => logPoong("personas fail", e));
  }, []);

  useEffect(() => {
    if (!workingPersonaId) return;
    document.documentElement.style.setProperty(
      "--profile-accent",
      personaAccent(workingPersonaId)
    );
  }, [workingPersonaId]);

  const selectJob = async (j) => {
    const req = ++selectReqRef.current;
    selectedIdRef.current = j.id;
    setSelected(j);
    try {
      const full = await client.getJob(j.id);
      if (req !== selectReqRef.current) return;
      setSelected(full);
    } catch (e) {
      if (req !== selectReqRef.current) return;
      logPoong("getJob fail", e);
    }
  };

  const clearSelected = () => {
    selectedIdRef.current = null;
    setSelected(null);
  };

  const setWorkingAs = async (personaId) => {
    setWorkingPersonaId(personaId);
    setProfileStatusFilter("");
    setPage(1);
    try {
      await client.activatePersona(personaId);
    } catch (err) {
      setError(String(err.message || err));
    }
  };

  const toggleCheck = (id, checked, event) => {
    const shift = Boolean(event?.shiftKey);
    setCheckedIds((prev) => {
      const next = new Set(prev);
      const ids = jobs.map((j) => j.id);
      if (shift && lastCheckRef.current != null) {
        const a = ids.indexOf(lastCheckRef.current);
        const b = ids.indexOf(id);
        if (a >= 0 && b >= 0) {
          const [lo, hi] = a < b ? [a, b] : [b, a];
          // Windows Explorer style: Shift+click selects the inclusive range
          for (let i = lo; i <= hi; i += 1) next.add(ids[i]);
          return next;
        }
      }
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
    // Anchor only moves on a normal click (not Shift+click)
    if (!shift) lastCheckRef.current = id;
  };

  const toggleCheckAll = (checked) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      for (const j of jobs) {
        if (checked) next.add(j.id);
        else next.delete(j.id);
      }
      return next;
    });
    lastCheckRef.current = null;
  };

  const clearSelection = () => {
    setCheckedIds(new Set());
    lastCheckRef.current = null;
  };

  const removeChecked = (ids) => {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      for (const id of ids) next.delete(id);
      return next;
    });
  };

  return {
    jobs,
    stats,
    selected,
    setSelected,
    error,
    setError,
    statusFilter,
    setStatusFilter,
    workingPersonaId,
    setWorkingAs,
    profileStatusFilter,
    setProfileStatusFilter,
    searchText,
    setSearchText,
    page,
    setPage,
    pageSize,
    setPageSize,
    jobsTotal,
    jobsPages,
    checkedIds,
    personas,
    refresh,
    selectJob,
    clearSelected,
    selectedIdRef,
    toggleCheck,
    toggleCheckAll,
    clearSelection,
    removeChecked,
  };
}
