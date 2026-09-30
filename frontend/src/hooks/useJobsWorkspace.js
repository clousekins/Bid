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
  // Jobs at companies the working profile claims to work for. Hidden by
  // default, but the operator can look at them.
  const [hideOwnEmployers, setHideOwnEmployers] = useState(true);
  const [ownEmployerInfo, setOwnEmployerInfo] = useState({ hidden: 0, names: [] });
  const [searchText, setSearchText] = useState("");
  // Empty means every country. The options come from the server so only
  // countries that actually have listings can be chosen.
  const [countryFilter, setCountryFilter] = useState("");
  const [countryOptions, setCountryOptions] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [jobsTotal, setJobsTotal] = useState(0);
  const [jobsPages, setJobsPages] = useState(1);
  const [sort, setSort] = useState("seq");
  const [sortDir, setSortDir] = useState("asc");
  const [checkedIds, setCheckedIds] = useState(() => new Set());
  const [personas, setPersonas] = useState([]);
  const selectedIdRef = useRef(null);
  const selectReqRef = useRef(0);
  const lastCheckRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const params = { page, page_size: pageSize, sort, sort_dir: sortDir };
      if (statusFilter) params.status = statusFilter;
      if (workingPersonaId) params.profile = workingPersonaId;
      if (!hideOwnEmployers) params.hide_own_employers = false;
      if (workingPersonaId && profileStatusFilter) {
        params.profile_status = profileStatusFilter;
      }
      if (searchQuery) params.q = searchQuery;
      if (countryFilter) params.country = countryFilter;
      const [list, st, countries] = await Promise.all([
        client.listJobs(params),
        client.stats(),
        client.jobCountries().catch(() => null),
      ]);
      if (countries?.countries) setCountryOptions(countries.countries);
      const rows = list.jobs || [];
      setJobs(rows);
      setJobsTotal(Number(list.total ?? rows.length));
      setJobsPages(Math.max(1, Number(list.pages ?? 1)));
      setOwnEmployerInfo({
        hidden: Number(list.hidden_own_employers || 0),
        names: list.own_employer_names || [],
      });
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
  }, [statusFilter, workingPersonaId, profileStatusFilter, searchQuery, countryFilter, page, pageSize, sort, sortDir, hideOwnEmployers]);

  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(searchText.trim()), 250);
    return () => clearTimeout(timer);
  }, [searchText]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, workingPersonaId, profileStatusFilter, searchQuery, countryFilter, pageSize, sort, sortDir, hideOwnEmployers]);

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
      const full = await client.getJob(j.id, workingPersonaId);
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

  const setSortKey = (key) => {
    setPage(1);
    setSort((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
        return prev;
      }
      setSortDir(key === "seq" ? "asc" : "asc");
      return key;
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
    hideOwnEmployers,
    setHideOwnEmployers,
    ownEmployerInfo,
    setProfileStatusFilter,
    searchText,
    countryFilter,
    setCountryFilter,
    countryOptions,
    setSearchText,
    searchQuery,
    page,
    setPage,
    pageSize,
    setPageSize,
    jobsTotal,
    jobsPages,
    sort,
    sortDir,
    setSortKey,
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
