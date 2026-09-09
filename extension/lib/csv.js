// Rows to a CSV the rest of Poong-Apply already knows how to read.
//
// The column names are the ones manual_ingest.FIELD_ALIASES recognises, so a
// file written here can be handed straight back to POST /api/jobs/import. That
// is the whole reason for spelling them this way rather than prettily.

export const COLUMNS = [
  "company",
  "title",
  "url",
  "apply_url",
  "location",
  "posted_at",
  "seniority",
  "employment_type",
  "remote",
  "salary",
  // Jobright's match score. The importer ignores columns it does not know, so
  // carrying it costs nothing and it is the number the filter acted on.
  "score",
  "source",
  "external_id",
  "description",
];

function cell(value) {
  const text = String(value == null ? "" : value);
  // A field is quoted when it holds a delimiter, a quote or a newline, and an
  // inner quote is doubled. Descriptions carry all three.
  if (/[",\r\n]/.test(text)) return `"${text.replace(/"/g, '""')}"`;
  return text;
}

export function toCsv(rows, columns = COLUMNS) {
  const lines = [columns.join(",")];
  for (const row of rows) {
    lines.push(columns.map((column) => cell(row[column])).join(","));
  }
  // CRLF and a BOM: Excel opens a UTF-8 CSV as mojibake without the mark, and
  // the accented company names in this data are exactly what it mangles.
  return `﻿${lines.join("\r\n")}\r\n`;
}

export function csvFilename(now = new Date()) {
  const pad = (n) => String(n).padStart(2, "0");
  const stamp =
    `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
    `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `jobright-clipped-${stamp}.csv`;
}
