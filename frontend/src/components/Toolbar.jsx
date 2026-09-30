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
  countryFilter,
  onCountryFilterChange,
  countryOptions = [],
  stats,
  workingPersonaId,
  hideOwnEmployers = true,
  onHideOwnEmployersChange,
  ownEmployerInfo,
}) {
  const profileCounts = stats?.by_profile?.[workingPersonaId] || {};
  const allCount =
    Object.values(profileCounts).reduce((sum, n) => sum + Number(n || 0), 0) || stats?.total || 0;

  const activeFilters = [
    profileStatusFilter ? PACKAGE_STATUS_LABELS[profileStatusFilter] : null,
    statusFilter
      ? PIPELINE_OPTIONS.find((o) => o.value === statusFilter)?.label || statusFilter
      : null,
    searchText.trim() ? `"${searchText.trim()}"` : null,
    countryFilter
      ? countryOptions.find((c) => c.code === countryFilter)?.name || countryFilter
      : null,
  ].filter(Boolean);

  return (
    <div className="filter-bar">
      <label className="filter-search">
        <span className="sr-only">Search jobs</span>
        <input
          type="search"
          value={searchText}
          placeholder="Search company, title, profile, URL…"
          onChange={(e) => onSearchChange(e.target.value)}
        />
      </label>

      <div className="filter-chips" role="group" aria-label="My status">
        {PACKAGE_STATUS_FILTERS.map(({ value, label }) => {
          const count = value ? profileCounts[value] || 0 : allCount;
          const active = profileStatusFilter === value;
          return (
            <button
              key={value || "all"}
              type="button"
              className={`filter-chip ${value ? `status-${value}` : ""} ${active ? "active" : ""}`}
              onClick={() => onProfileStatusChange(value)}
            >
              {label}
              <span className="chip-count">{count}</span>
            </button>
          );
        })}
      </div>

      {/* Only countries that actually have listings are offered, so the filter
          cannot select an empty set. Empty value means every country, which is
          the default. */}
      <label className="filter-country">
        <span className="filter-pipeline-label">Country</span>
        <select
          value={countryFilter || ""}
          onChange={(e) => onCountryFilterChange(e.target.value)}
          title="Show listings from one country"
        >
          <option value="">All countries</option>
          {countryOptions.map((c) => (
            <option key={c.code} value={c.code}>
              {c.name} ({c.count})
            </option>
          ))}
        </select>
      </label>

      <label className="filter-pipeline">
        <span className="filter-pipeline-label">Pipeline</span>
        <select value={statusFilter} onChange={(e) => onStatusFilterChange(e.target.value)}>
          {PIPELINE_OPTIONS.map((o) => (
            <option key={o.value || "any"} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      {ownEmployerInfo?.hidden || !hideOwnEmployers ? (
        <label
          className={`own-employer-toggle ${hideOwnEmployers ? "on" : ""}`}
          title={
            ownEmployerInfo?.names?.length
              ? `Hidden because this profile's resume claims them:
${ownEmployerInfo.names
                  .slice(0, 20)
                  .join(", ")}`
              : "Jobs at companies this profile says it works for"
          }
        >
          <input
            type="checkbox"
            checked={hideOwnEmployers}
            onChange={(e) => onHideOwnEmployersChange?.(e.target.checked)}
          />
          <span>
            Hide own employers
            {ownEmployerInfo?.hidden ? (
              <strong> · {ownEmployerInfo.hidden}</strong>
            ) : null}
          </span>
        </label>
      ) : null}

      {activeFilters.length ? (
        <div className="filter-active muted" title="Active filters">
          {activeFilters.join(" · ")}
        </div>
      ) : null}
    </div>
  );
}
