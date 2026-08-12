/**
 * Bound Apps Script for the "Ken bid" spreadsheet.
 *
 * Setup (once):
 * 1. Open the sheet → Extensions → Apps Script
 * 2. Paste this file, save
 * 3. Deploy → New deployment → Type: Web app
 *    - Execute as: Me
 *    - Who has access: Anyone (or Anyone with Google account)
 * 4. Copy the Web App URL into .env as:
 *    APPLIED_SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/.../exec
 *
 * POONG will POST JSON: { title, company, url, persona_id, timestamp }
 * and append a row to the configured tab.
 */

var SHEET_TAB = "Sheet8USTamoorSF"; // change if your tab name differs
var SHEET_GID = 1934565105; // fallback match by gid

function doPost(e) {
  try {
    var data = {};
    if (e.postData && e.postData.contents) {
      data = JSON.parse(e.postData.contents);
    }
    var title = data.title || data.job_title || "";
    var company = data.company || data.company_name || "";
    var url = data.url || data.link || data.job_link || "";
    var persona = data.persona_id || "";
    var ts = data.timestamp || new Date().toISOString();

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sh = ss.getSheetByName(SHEET_TAB);
    if (!sh) {
      var sheets = ss.getSheets();
      for (var i = 0; i < sheets.length; i++) {
        if (sheets[i].getSheetId() === SHEET_GID) {
          sh = sheets[i];
          break;
        }
      }
    }
    if (!sh) sh = ss.getSheets()[0];

    if (sh.getLastRow() === 0) {
      sh.appendRow(["Title", "Company", "Job Link", "Applied At", "Persona"]);
    }
    sh.appendRow([title, company, url, ts, persona]);
    return ContentService.createTextOutput(
      JSON.stringify({ ok: true })
    ).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(
      JSON.stringify({ ok: false, error: String(err) })
    ).setMimeType(ContentService.MimeType.JSON);
  }
}

function doGet() {
  return ContentService.createTextOutput(
    "POONG applied-sheet webhook OK"
  );
}
