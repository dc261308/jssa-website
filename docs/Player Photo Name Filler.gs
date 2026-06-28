/**
 * PLAYER PHOTO NAME FILLER
 * Container-bound Apps Script for the "JSSA Website Control" spreadsheet.
 *
 * Problem it solves: the photo-upload form stores each player's name in a
 * division-specific "Select your name" dropdown column (scattered out to the
 * right), so the "First Name" / "Last Name" columns at the front stay blank and
 * you can't tell at a glance who uploaded.
 *
 * What it does: on every photo upload it copies the picked name into the
 * First Name / Last Name columns of the "Player Photo Upload" tab, and makes the
 * new row match the formatting of the row above it (so new uploads don't look
 * "unformatted"). Run fillExistingPhotoNames() once to fill the uploads already
 * in the sheet.
 *
 * SETUP: open the JSSA Website Control sheet -> Extensions -> Apps Script,
 * paste this into a new file, Save, then run installPhotoNameFiller once and
 * approve the permission prompt.
 *
 * Safe for the website: the site matches photos to players by name and already
 * prefers First Name / Last Name, so filling them keeps every photo matched to
 * the same player.
 */

var PU_TAB = 'Player Photo Upload';

/** Installable "On form submit" handler. */
function onPhotoUploadSubmit(e) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = (e && e.range) ? e.range.getSheet() : ss.getSheetByName(PU_TAB);
    if (!sheet || sheet.getName() !== PU_TAB) return; // ignore other forms

    var row = (e && e.range) ? e.range.getRow() : sheet.getLastRow();

    pu_fill_name_for_row_(sheet, row);

    // Make the new upload row match the look of the row above it, so your
    // formatting stays consistent as uploads come in.
    if (row > 2) {
      try {
        sheet.getRange(row - 1, 1, 1, sheet.getLastColumn()).copyTo(
          sheet.getRange(row, 1, 1, sheet.getLastColumn()),
          SpreadsheetApp.CopyPasteType.PASTE_FORMAT, false);
      } catch (fmtErr) {
        Logger.log('format copy skipped: ' + fmtErr);
      }
    }
  } catch (err) {
    Logger.log('onPhotoUploadSubmit error: ' + err);
  }
}

/** One-time: fill First/Last Name for uploads already in the sheet. */
function fillExistingPhotoNames() {
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(PU_TAB);
  if (!sheet) {
    SpreadsheetApp.getActive().toast('"' + PU_TAB + '" tab not found.');
    return;
  }
  var last = sheet.getLastRow();
  var filled = 0;
  for (var r = 2; r <= last; r++) {
    if (pu_fill_name_for_row_(sheet, r)) filled++;
  }
  SpreadsheetApp.getActive().toast('Filled the name on ' + filled + ' row(s).');
}

/** Copy the picked dropdown name into First Name / Last Name for one row. */
function pu_fill_name_for_row_(sheet, row) {
  if (row < 2) return false;

  var width = sheet.getLastColumn();
  var headers = sheet.getRange(1, 1, 1, width).getValues()[0]
    .map(function (h) { return String(h || '').trim().toLowerCase(); });

  var fnCol = headers.indexOf('first name') + 1;
  var lnCol = headers.indexOf('last name') + 1;
  if (!fnCol || !lnCol) return false;

  var values = sheet.getRange(row, 1, 1, width).getValues()[0];

  // Don't overwrite a name that's already there.
  if (String(values[fnCol - 1] || '').trim() || String(values[lnCol - 1] || '').trim()) {
    return false;
  }

  // The picked name is the first non-blank "select your name" column.
  var picked = '';
  for (var i = 0; i < headers.length; i++) {
    if (headers[i].indexOf('select your name') > -1 || headers[i].indexOf('your name') > -1) {
      var v = String(values[i] || '').trim();
      if (v) { picked = v; break; }
    }
  }
  if (!picked) return false;

  var parts = picked.split(/\s+/);
  var first = parts.shift();
  var last = parts.join(' ');

  sheet.getRange(row, fnCol).setValue(first);
  sheet.getRange(row, lnCol).setValue(last);
  return true;
}

/** Run ONCE: install the On form submit trigger and backfill existing rows. */
function installPhotoNameFiller() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'onPhotoUploadSubmit') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('onPhotoUploadSubmit').forSpreadsheet(ss).onFormSubmit().create();
  fillExistingPhotoNames();
  SpreadsheetApp.getActive().toast('Photo name filler installed. New uploads will auto-fill First/Last Name.');
}
