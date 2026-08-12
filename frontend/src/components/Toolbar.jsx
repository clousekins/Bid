import {
  PACKAGE_STATUS_FILTERS,
  PACKAGE_STATUS_LABELS,
  PIPELINE_OPTIONS,
} from "../utils/statusLabels.js";

export default function Toolbar({
  searchText,
  onSearchChange,
  profileStatusFilter,
  onProfileStatusChange,
  statusFilter,
  onStatusFilterChange,
  stats,
  workingPersonaId,
}) {
  const profileCounts = stats?.by_profile?.[workingPersonaId] || {};
  const allCount =
    Object.values(profileCounts).reduce((sum, n) => sum + Number(n || 0), 0) ||
    stats?.total ||
    0;

  return (
    <div className="toolbar">
      <label className="toolbar-search">
        <span className="sr-only">Search</span>
        <input
          type="search"
          value={searchText}
          placeholder="Search title, company, or profile…"
          onChange={(e) => onSearchChange(e.target.value)}
        />
      </label>

      <div className="status-chips" role="group" aria-label="My status">
        {PACKAGE_STATUS_FILTERS.map(({ value, label }) => {
          const count = value ? profileCounts[value] || 0 : allCount;
          const active = profileStatusFilter === value;
          return (
            <button
              key={value || "all"}
              type="button"
              className={`filter-chip ${value ? `pill package-status ${value}` : ""} ${
                active ? "active" : ""
              }`}
              onClick={() => onProfileStatusChange(value)}
            >
              {label}
              <span className="chip-count">{count}</span>
            </button>
          );
        })}
      </div>

      <label className="toolbar-pipeline">
        Pipeline
        <select
          value={statusFilter}
          onChange={(e) => onStatusFilterChange(e.target.value)}
        >
          {PIPELINE_OPTIONS.map((o) => (
            <option key={o.value || "any"} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      {profileStatusFilter ? (
        <span className="toolbar-active-filter muted">
          Showing {PACKAGE_STATUS_LABELS[profileStatusFilter] || profileStatusFilter}
        </span>
      ) : null}
    </div>
  );
}
