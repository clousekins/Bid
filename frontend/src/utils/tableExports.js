import { packageStatusLabel, pipelineLabel } from "./statusLabels.js";
import { shortPersona } from "./persona.js";
import { downloadCsv, downloadExcel, fetchAllPages } from "./tableExport.js";
import { client } from "../api.js";

export const JOB_EXPORT_COLUMNS = [
  { header: "Seq", key: "seq_no" },
  { header: "Company", key: "company" },
  { header: "Title", key: "title" },
  { header: "Domains", key: "domains" },
  {
    header: "My status",
    value: (j) => packageStatusLabel(j.my_status || "need_resume"),
  },
  {
    header: "Pipeline",
    value: (j) => pipelineLabel(j.status) || j.status || "",
  },
  { header: "Location", key: "location" },
  { header: "Source", key: "source" },
  { header: "URL", key: "url" },
  { header: "Apply URL", key: "apply_url" },
  {
    header: "JD fetched",
    value: (j) => (j.jd_fetched ? "yes" : "no"),
  },
  {
    header: "Other profiles",
    value: (j) =>
      (j.packages || [])
        .filter((p) => p.persona_id)
        .map((p) => `${shortPersona(p.persona_id)}:${packageStatusLabel(p.status)}`)
        .join("; "),
  },
  { header: "Created", key: "created_at" },
  { header: "Updated", key: "updated_at" },
];

export const CATALOG_EXPORT_COLUMNS = [
  { header: "Company", value: (b) => b.company_name || b.token || "" },
  { header: "ATS", key: "source" },
  { header: "Token", key: "token" },
  { header: "Status", key: "status" },
  { header: "Jobs found", key: "jobs_found" },
  { header: "Last checked", key: "last_checked_at" },
  { header: "Error", key: "error" },
  { header: "Created", key: "created_at" },
];

export async function exportJobsTable({
  format,
  listParams,
  selectedIds,
  scope = "filtered",
}) {
  let rows;
  if (scope === "selected") {
    const ids = [...(selectedIds || [])];
    if (!ids.length) throw new Error("Select at least one job to export.");
    const idSet = new Set(ids);
    const all = await fetchAllPages(async (page, pageSize) => {
      const res = await client.listJobs({ ...listParams, page, page_size: pageSize });
      return { rows: res.jobs || [], total: res.total, pages: res.pages };
    });
    rows = all.filter((j) => idSet.has(j.id));
  } else {
    rows = await fetchAllPages(async (page, pageSize) => {
      const res = await client.listJobs({ ...listParams, page, page_size: pageSize });
      return { rows: res.jobs || [], total: res.total, pages: res.pages };
    });
  }

  if (!rows.length) throw new Error("No jobs to export.");

  const base = scope === "selected" ? "poong-jobs-selected" : "poong-jobs";
  if (format === "excel") {
    downloadExcel(base, JOB_EXPORT_COLUMNS, rows, "Jobs");
  } else {
    downloadCsv(base, JOB_EXPORT_COLUMNS, rows);
  }
  return rows.length;
}

export async function exportCatalogTable({ format, filters }) {
  const rows = await fetchAllPages(async (page, pageSize) => {
    const offset = (page - 1) * pageSize;
    const res = await client.listAtsBoards({
      ...filters,
      limit: pageSize,
      offset,
    });
    const chunk = res.boards || [];
    const total = Number(res.total ?? chunk.length);
    return {
      rows: chunk,
      total,
      pages: Math.max(1, Math.ceil(total / pageSize) || 1),
    };
  });

  if (!rows.length) throw new Error("No companies to export.");

  if (format === "excel") {
    downloadExcel("poong-company-catalog", CATALOG_EXPORT_COLUMNS, rows, "Catalog");
  } else {
    downloadCsv("poong-company-catalog", CATALOG_EXPORT_COLUMNS, rows);
  }
  return rows.length;
}
