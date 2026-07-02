/**
 * JSSA — Email Usage Reporter
 * ---------------------------------------------------------------------------
 * Deploy this SAME script once inside EACH league Gmail account you want to
 * watch (jssagames@, jssaadmin@, cosentinoteam@). Running inside the account
 * is the only way to read that account's "Sent" mail, so each account needs
 * its own copy.
 *
 * What it reports, for the account it runs in, for TODAY:
 *   - messages_today    how many emails you sent (a BCC blast = 1)
 *   - recipients_today  how many people those reached (a BCC blast to 80 = 80)
 *   - remaining_today   DAILY_LIMIT minus recipients (your "sends left")
 *
 * HOW TO DEPLOY (do this in each of the three accounts, signed in as that
 * account):
 *   1. Go to  https://script.google.com  and click  New project.
 *   2. Delete the sample code, paste ALL of this file, and Save.
 *   3. Change the SECRET below to a long random phrase — use the SAME phrase
 *      in all three accounts.
 *   4. Click  Deploy > New deployment > (gear) Web app.
 *        Execute as:        Me
 *        Who has access:    Anyone
 *      Click Deploy, approve the permissions, and COPY the Web app URL
 *      (it ends in /exec).
 *   5. Add  ?key=YOUR_SECRET  to the end of that URL. Paste that full URL into
 *      the "Email Accounts" tab of the control sheet (see apps-script/README.md).
 * ---------------------------------------------------------------------------
 */

// Use a long random phrase. Put the SAME phrase in all three accounts, and in
// each URL you give the website (the ?key=... part).
var SECRET = 'PUT_A_LONG_RANDOM_PHRASE_HERE';

// Gmail's practical daily send limit for a free account, counted by the number
// of people you email (recipients), not the number of messages. Change this one
// number if you ever need to.
var DAILY_LIMIT = 100;


// Run this ONCE from the editor (pick it in the function dropdown, click Run)
// to grant the account's Gmail permission. Without this first run, the web app
// returns a "does not have permission" error. Harmless to leave in place.
function authorizeNow() {
  Logger.log(_usage());
}


function doGet(e) {
  var provided = (e && e.parameter && e.parameter.key) || '';
  // Add &format=csv to the URL to get a clean two-column table that a private
  // Google Sheet can pull in with =IMPORTDATA(). Default is JSON (used by the
  // website).
  var format = (e && e.parameter && e.parameter.format || '').toLowerCase();
  if (provided !== SECRET) {
    return _out({ ok: false, error: 'unauthorized' }, format);
  }
  try {
    return _out(_usage(), format);
  } catch (err) {
    return _out({ ok: false, error: String(err) }, format);
  }
}


function _out(obj, format) {
  return (format === 'csv') ? _csv(obj) : _json(obj);
}


function _usage() {
  var tz = Session.getScriptTimeZone();
  var me = (Session.getEffectiveUser().getEmail() || '').toLowerCase();

  // Midnight this morning, in the account's own time zone.
  var start = new Date();
  start.setHours(0, 0, 0, 0);

  // Only scan the last couple days of Sent mail, then keep just today's.
  var threads = GmailApp.search('in:sent newer_than:2d', 0, 300);
  var messages = 0;
  var recipients = 0;

  for (var i = 0; i < threads.length; i++) {
    var msgs = threads[i].getMessages();
    for (var j = 0; j < msgs.length; j++) {
      var m = msgs[j];
      if (m.getDate() < start) continue;             // not sent today
      var from = (m.getFrom() || '').toLowerCase();
      if (me && from.indexOf(me) === -1) continue;   // not sent BY this account
      messages += 1;
      recipients += _countAddrs(m.getTo()) +
                    _countAddrs(m.getCc()) +
                    _countAddrs(m.getBcc());
    }
  }

  var remaining = DAILY_LIMIT - recipients;
  if (remaining < 0) remaining = 0;

  return {
    ok: true,
    account: me,
    messages_today: messages,
    recipients_today: recipients,
    remaining_today: remaining,
    daily_limit: DAILY_LIMIT,
    updated: Utilities.formatDate(new Date(), tz, 'MMM d, h:mm a')
  };
}


// Count comma-separated addresses in a To/Cc/Bcc field ("" -> 0).
function _countAddrs(s) {
  if (!s) return 0;
  var parts = String(s).split(',');
  var n = 0;
  for (var i = 0; i < parts.length; i++) {
    if (parts[i].trim()) n += 1;
  }
  return n;
}


function _json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}


// A tidy two-column table for =IMPORTDATA() in a private sheet.
function _csv(u) {
  var rows;
  if (!u || u.ok === false) {
    rows = [['Metric', 'Value'], ['Error', (u && u.error) || 'unknown']];
  } else {
    rows = [
      ['Metric', 'Value'],
      ['Account', u.account],
      ['Emails sent today', u.messages_today],
      ['People reached', u.recipients_today],
      ['Sends left today', u.remaining_today],
      ['Daily limit', u.daily_limit],
      ['Updated', u.updated]
    ];
  }
  var out = rows.map(function (r) {
    return r.map(_csvCell).join(',');
  }).join('\n');
  return ContentService.createTextOutput(out).setMimeType(ContentService.MimeType.CSV);
}


function _csvCell(v) {
  v = String(v == null ? '' : v);
  return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v;
}


// ---------------------------------------------------------------------------
// OPTIONAL: auto-updating private log
// ---------------------------------------------------------------------------
// Writes today's numbers into a private Google Sheet every few minutes, so you
// get a live count (and a dated history) without the ~1-hour delay of
// =IMPORTDATA(). To turn it on:
//   1. Create/open a Google Sheet only you can see.
//   2. Copy its ID from the URL — the long code between /d/ and /edit — into
//      LOG_SHEET_ID below, and Save.
//   3. Pick 'startAutoUpdates' in the function dropdown and click Run once
//      (approve the extra permissions). It then updates on its own.
//   Use 'stopAutoUpdates' to turn it back off.
// ---------------------------------------------------------------------------
var LOG_SHEET_ID = '';          // <-- paste your private sheet's ID here
var LOG_TAB = 'Email Usage';    // the tab it writes into


function startAutoUpdates() {
  stopAutoUpdates();  // avoid piling up duplicate triggers
  ScriptApp.newTrigger('updateSheet').timeBased().everyMinutes(5).create();
  updateSheet();      // write once right now
}


function stopAutoUpdates() {
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === 'updateSheet') {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }
}


function updateSheet() {
  if (!LOG_SHEET_ID) {
    throw new Error('Set LOG_SHEET_ID to your private sheet ID first.');
  }
  var u = _usage();
  var tz = Session.getScriptTimeZone();
  var ss = SpreadsheetApp.openById(LOG_SHEET_ID);
  var sh = ss.getSheetByName(LOG_TAB);
  if (!sh) {
    sh = ss.insertSheet(LOG_TAB);
    sh.appendRow(['Date', 'Account', 'Emails sent', 'People reached',
                  'Sends left', 'Last updated']);
  }
  var today = Utilities.formatDate(new Date(), tz, 'yyyy-MM-dd');

  // One row per day: update today's row if it exists, otherwise add it.
  var values = sh.getDataRange().getValues();
  var rowIndex = -1;
  for (var i = 1; i < values.length; i++) {
    var cell = values[i][0];
    var cellStr = (cell instanceof Date)
      ? Utilities.formatDate(cell, tz, 'yyyy-MM-dd') : String(cell);
    if (cellStr === today) { rowIndex = i + 1; break; }
  }
  var row = [today, u.account, u.messages_today, u.recipients_today,
             u.remaining_today, u.updated];
  if (rowIndex > 0) {
    sh.getRange(rowIndex, 1, 1, row.length).setValues([row]);
  } else {
    sh.appendRow(row);
  }
}
