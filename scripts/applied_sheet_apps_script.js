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
 * POONG will POST JSON: { title, company, url, location, persona_id, timestamp }
 * and append a row to the configured tab.
 */

// Fallbacks only. POONG sends sheet_id / sheet_tab / sheet_gid with every
// request, and this script now honours them — so changing APPLIED_SHEET_ID in
// .env redirects the log without editing this file. Set DEFAULT_SHEET_ID if
// you would rather pin one spreadsheet here.
var DEFAULT_SHEET_ID = ""; // "" means the spreadsheet this script is bound to
var SHEET_TAB = ""; // "" means resolve by gid, then first sheet
var SHEET_GID = 0;

function doPost(e) {
  try {
    var data = {};
    if (e.postData && e.postData.contents) {
      data = JSON.parse(e.postData.contents);
    }
    var title = data.title || data.job_title || "";
    var company = data.company || data.company_name || "";
    var url = data.url || data.link || data.job_link || "";
    var location = data.location || "";
    var persona = data.persona_id || "";
    var ts = data.timestamp || new Date().toISOString();

    // Open the spreadsheet POONG asked for. Without this the script always
    // wrote to whichever sheet it happened to be created in, so pointing
    // .env at a new sheet silently kept logging to the old one.
    var wantId = data.sheet_id || DEFAULT_SHEET_ID || "";
    var ss;
    if (wantId) {
      try {
        ss = SpreadsheetApp.openById(wantId);
      } catch (idErr) {
        ss = SpreadsheetApp.getActiveSpreadsheet();
      }
    } else {
      ss = SpreadsheetApp.getActiveSpreadsheet();
    }

    var wantTab = data.sheet_tab || SHEET_TAB || "";
    var wantGid = data.sheet_gid !== undefined && data.sheet_gid !== null && data.sheet_gid !== ""
      ? Number(data.sheet_gid)
      : SHEET_GID;

    var sh = wantTab ? ss.getSheetByName(wantTab) : null;
    if (!sh) {
      var sheets = ss.getSheets();
      for (var i = 0; i < sheets.length; i++) {
        if (sheets[i].getSheetId() === wantGid) {
          sh = sheets[i];
          break;
        }
      }
    }
    if (!sh) sh = ss.getSheets()[0];

    if (sh.getLastRow() === 0) {
      sh.appendRow(["Title", "Company", "Job Link", "Applied At", "Persona", "Location"]);
    }
    sh.appendRow([title, company, url, ts, persona, location]);
    // Report where the row actually landed. "ok" alone cannot distinguish a
    // correct write from one that quietly went to the previous spreadsheet.
    return ContentService.createTextOutput(
      JSON.stringify({
        ok: true,
        wrote_to_id: ss.getId(),
        wrote_to_name: ss.getName(),
        tab: sh.getName(),
        gid: sh.getSheetId(),
        row: sh.getLastRow()
      })
    ).setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService.createTextOutput(
      JSON.stringify({ ok: false, error: String(err) })
    ).setMimeType(ContentService.MimeType.JSON);
  }
}

function doGet(e) {
  // Diagnostic: says which spreadsheet this deployment is bound to, so you can
  // confirm the target without writing a row. Pass ?sheet_id=... to check that
  // this deployment can open a specific sheet.
  var bound = SpreadsheetApp.getActiveSpreadsheet();
  var out = {
    ok: true,
    service: "POONG applied-sheet webhook",
    bound_to_id: bound ? bound.getId() : null,
    bound_to_name: bound ? bound.getName() : null,
    honours_sheet_id: true
  };
  var wanted = e && e.parameter ? e.parameter.sheet_id : "";
  if (wanted) {
    try {
      var target = SpreadsheetApp.openById(wanted);
      out.can_open_requested = true;
      out.requested_name = target.getName();
    } catch (err) {
      out.can_open_requested = false;
      out.error = String(err);
    }
  }
  return ContentService.createTextOutput(JSON.stringify(out)).setMimeType(
    ContentService.MimeType.JSON
  );
}
