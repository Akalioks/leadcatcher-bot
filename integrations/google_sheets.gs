/**
 * LeadCatcher → Google Sheets. Every lead becomes a row; status changes update the same row.
 *
 * Setup (5 minutes, no Google Cloud account needed):
 *  1. Create a Google Sheet. Extensions → Apps Script. Replace the code with this file.
 *  2. Change SECRET below to any long random text.
 *  3. Deploy → New deployment → type "Web app". Execute as: Me. Who has access: Anyone. Deploy, allow access.
 *  4. Copy the Web app URL and add ?key=YOUR_SECRET to the end:
 *       https://script.google.com/macros/s/AKfy.../exec?key=YOUR_SECRET
 *  5. Put that into WEBHOOK_URLS in the bot's .env and restart the bot.
 */
const SECRET = "change-me-to-a-long-random-text";
const SHEET_NAME = "Leads";
const COLUMNS = ["id", "created_at", "temperature", "score", "status", "name", "phone", "email",
                 "telegram", "source", "channel", "manager", "answers"];

function doPost(e) {
  if (!e.parameter || e.parameter.key !== SECRET) return reply({ ok: false, error: "forbidden" });
  const lead = JSON.parse(e.postData.contents).lead;
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);  // two leads at the same second don't overwrite each other
  try {
    const book = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = book.getSheetByName(SHEET_NAME) || book.insertSheet(SHEET_NAME);
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(COLUMNS);
      sheet.setFrozenRows(1);
    }
    const row = COLUMNS.map(function (c) {
      if (c === "answers") {
        return Object.keys(lead.answers || {}).map(function (k) { return k + ": " + lead.answers[k]; }).join("\n");
      }
      return lead[c] === undefined || lead[c] === null ? "" : lead[c];
    });
    const last = sheet.getLastRow();
    const ids = last > 1 ? sheet.getRange(2, 1, last - 1, 1).getValues().map(function (r) { return r[0]; }) : [];
    const index = ids.indexOf(lead.id);
    if (index === -1) sheet.appendRow(row);
    else sheet.getRange(index + 2, 1, 1, row.length).setValues([row]);
  } finally {
    lock.releaseLock();
  }
  return reply({ ok: true });
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
