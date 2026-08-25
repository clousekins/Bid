/** Client-side table export — CSV + Excel-compatible SpreadsheetML (.xls). */

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`;
}

export function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

function csvEscape(value) {
  if (value == null) return "";
  const s = String(value);
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

export function rowsToCsv(columns, rows) {
  const header = columns.map((c) => csvEscape(c.header)).join(",");
  const lines = rows.map((row) =>
    columns.map((c) => csvEscape(typeof c.value === "function" ? c.value(row) : row[c.key])).join(",")
  );
  return `\uFEFF${[header, ...lines].join("\r\n")}`;
}

function xmlEscape(value) {
  if (value == null) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Excel opens this SpreadsheetML as a real workbook (no npm dependency). */
export function rowsToExcelXml(columns, rows, sheetName = "Sheet1") {
  const safeSheet = String(sheetName || "Sheet1").replace(/[\\/*?:\[\]]/g, "_").slice(0, 31);
  const headerCells = columns
    .map((c) => `<Cell><Data ss:Type="String">${xmlEscape(c.header)}</Data></Cell>`)
    .join("");
  const body = rows
    .map((row) => {
      const cells = columns
        .map((c) => {
          const raw = typeof c.value === "function" ? c.value(row) : row[c.key];
          if (raw == null || raw === "") {
            return `<Cell><Data ss:Type="String"></Data></Cell>`;
          }
          if (typeof raw === "number" && Number.isFinite(raw)) {
            return `<Cell><Data ss:Type="Number">${raw}</Data></Cell>`;
          }
          return `<Cell><Data ss:Type="String">${xmlEscape(raw)}</Data></Cell>`;
        })
        .join("");
      return `<Row>${cells}</Row>`;
    })
    .join("");

  return `<?xml version="1.0"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:html="http://www.w3.org/TR/REC-html40">
 <Styles>
  <Style ss:ID="Header">
   <Font ss:Bold="1"/>
   <Interior ss:Color="#E8EEF6" ss:Pattern="Solid"/>
  </Style>
 </Styles>
 <Worksheet ss:Name="${xmlEscape(safeSheet)}">
  <Table>
   <Row ss:StyleID="Header">${headerCells}</Row>
   ${body}
  </Table>
 </Worksheet>
</Workbook>`;
}

export function downloadCsv(filenameBase, columns, rows) {
  const csv = rowsToCsv(columns, rows);
  downloadBlob(
    `${filenameBase}-${stamp()}.csv`,
    new Blob([csv], { type: "text/csv;charset=utf-8" })
  );
}

export function downloadExcel(filenameBase, columns, rows, sheetName) {
  const xml = rowsToExcelXml(columns, rows, sheetName);
  downloadBlob(
    `${filenameBase}-${stamp()}.xls`,
    new Blob([xml], { type: "application/vnd.ms-excel;charset=utf-8" })
  );
}

export async function fetchAllPages(fetchPage, { pageSize = 200, maxPages = 500 } = {}) {
  const all = [];
  let page = 1;
  for (let i = 0; i < maxPages; i += 1) {
    const res = await fetchPage(page, pageSize);
    const chunk = res.rows || [];
    all.push(...chunk);
    const total = Number(res.total ?? all.length);
    const pages = Number(res.pages ?? Math.max(1, Math.ceil(total / pageSize)));
    if (!chunk.length || page >= pages || all.length >= total) break;
    page += 1;
  }
  return all;
}
