/**
 * =========================================================
 * JSSA MAILER — the "Outbox" queue
 * =========================================================
 * PURPOSE: send ALL league email from ONE dedicated account
 * (jssagames@gmail.com) instead of from whichever account happens
 * to own a trigger. This keeps the core automation on the owner
 * account safe even if the mail account ever gets rate-limited.
 *
 * HOW IT WORKS (two halves that run as different accounts):
 *   1) queueEmail_(...)   — a drop-in replacement for
 *      MailApp.sendEmail / GmailApp.sendEmail. Instead of sending,
 *      it just writes the message to a hidden "Outbox" tab. This runs
 *      as whatever account owns the normal triggers (you/personal).
 *   2) processOutbox_()   — reads the Outbox and actually sends the
 *      mail. INSTALL THIS ON A TRIGGER OWNED BY jssagames@gmail.com,
 *      so every email goes out from the league address.
 *
 * SETUP: see MAILER-SETUP.md. Short version:
 *   - Paste this file into the project (as "Mailer.gs").
 *   - Replace MailApp.sendEmail( and GmailApp.sendEmail( with
 *     queueEmail_(  in the OTHER files (this file is safe — see note
 *     in processOutbox_).
 *   - Log in as jssagames@gmail.com and run installOutboxProcessor() once.
 * =========================================================
 */

var MAILER_CONFIG = {
  OUTBOX_TAB: 'Outbox',
  BATCH_SIZE: 50,                                   // max sends per run
  SEND_EVERY_MINUTES: 5,                            // how often the queue is flushed
  DEFAULT_SENDER_NAME: 'Jupiter Senior Softball Association',
  QUOTA_SAFETY_MARGIN: 3                            // stop before hitting the daily wall
};

/**
 * DROP-IN replacement for MailApp.sendEmail / GmailApp.sendEmail.
 * Appends the message to the Outbox instead of sending immediately.
 * Accepts the same shapes those functions do:
 *   queueEmail_(recipient, subject, body)
 *   queueEmail_(recipient, subject, body, options)
 *   queueEmail_({ to, subject, body, htmlBody, cc, bcc, name, replyTo })
 */
function queueEmail_(a, subject, body, options) {
  var msg;
  if (a && typeof a === 'object') {
    msg = a;                                        // message-object form
  } else {
    msg = { to: a, subject: subject, body: body };  // positional form
    if (options) {
      msg.htmlBody = options.htmlBody;
      msg.cc = options.cc;
      msg.bcc = options.bcc;
      msg.name = options.name;
      msg.replyTo = options.replyTo;
    }
  }

  var to = String(msg.to || msg.recipient || '').trim();
  if (!to) { Logger.log('queueEmail_: no recipient — skipped.'); return; }

  var opts = {
    cc: msg.cc || '',
    bcc: msg.bcc || '',
    name: msg.name || MAILER_CONFIG.DEFAULT_SENDER_NAME,
    replyTo: msg.replyTo || ''
  };

  var sheet = ensureOutbox_();
  sheet.appendRow([
    new Date(),                     // A Queued At
    to,                             // B To
    String(msg.subject || ''),      // C Subject
    String(msg.body || ''),         // D Body (plain)
    String(msg.htmlBody || ''),     // E HtmlBody
    JSON.stringify(opts),           // F Options
    'QUEUED',                       // G Status
    '',                             // H Sent At
    ''                              // I Error
  ]);
}

/**
 * Sends everything QUEUED in the Outbox, oldest first.
 * >>> INSTALL THIS ON A TIME TRIGGER OWNED BY jssagames@gmail.com <<<
 * (run installOutboxProcessor() once while logged in as that account).
 * Respects the daily send quota and leaves anything it can't send today
 * as QUEUED, so nothing is ever lost — it just goes out tomorrow.
 */
function processOutbox_() {
  var sheet = ensureOutbox_();
  var lastRow = sheet.getLastRow();
  if (lastRow < 2) return;

  var values = sheet.getRange(2, 1, lastRow - 1, 9).getValues();
  var quota = MailApp.getRemainingDailyQuota();
  var sent = 0;

  for (var i = 0; i < values.length; i++) {
    var status = String(values[i][6] || '').trim().toUpperCase();
    if (status && status !== 'QUEUED') continue;                 // skip SENT / ERROR
    if (sent >= MAILER_CONFIG.BATCH_SIZE) break;                 // batch cap
    if ((quota - sent) <= MAILER_CONFIG.QUOTA_SAFETY_MARGIN) break; // preserve quota

    var to = values[i][1];
    var subject = values[i][2];
    var bodyText = values[i][3];
    var htmlBody = values[i][4];
    var opts = {};
    try { opts = JSON.parse(values[i][5] || '{}'); } catch (e) { opts = {}; }

    var sendOpts = {};
    if (htmlBody) sendOpts.htmlBody = htmlBody;
    if (opts.cc) sendOpts.cc = opts.cc;
    if (opts.bcc) sendOpts.bcc = opts.bcc;
    if (opts.name) sendOpts.name = opts.name;
    if (opts.replyTo) sendOpts.replyTo = opts.replyTo;

    var rowNum = i + 2;
    try {
      // NOTE: bracket notation on purpose. A global find/replace of
      // "MailApp.sendEmail(" in the other files will NOT match this line,
      // so the real send here is never accidentally rewritten to queueEmail_.
      MailApp['sendEmail'](to, subject, bodyText, sendOpts);
      sheet.getRange(rowNum, 7).setValue('SENT');
      sheet.getRange(rowNum, 8).setValue(new Date());
      sent++;
    } catch (err) {
      sheet.getRange(rowNum, 7).setValue('ERROR');
      sheet.getRange(rowNum, 9).setValue(String(err));
    }
  }

  if (sent) Logger.log('processOutbox_: sent ' + sent + ' email(s).');
}

/** Creates (once) and returns the hidden Outbox tab. */
function ensureOutbox_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = ss.getSheetByName(MAILER_CONFIG.OUTBOX_TAB);
  if (!sheet) {
    sheet = ss.insertSheet(MAILER_CONFIG.OUTBOX_TAB);
    sheet.appendRow(['Queued At', 'To', 'Subject', 'Body', 'HtmlBody',
                     'Options', 'Status', 'Sent At', 'Error']);
    sheet.setFrozenRows(1);
    sheet.hideSheet();
  }
  return sheet;
}

/**
 * ONE-TIME SETUP — run this ONCE while logged in as jssagames@gmail.com.
 * Installs the recurring trigger that flushes the Outbox as that account.
 */
function installOutboxProcessor() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'processOutbox_') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('processOutbox_')
    .timeBased()
    .everyMinutes(MAILER_CONFIG.SEND_EVERY_MINUTES)
    .create();
  SpreadsheetApp.getActiveSpreadsheet().toast(
    'Outbox processor installed (runs every ' + MAILER_CONFIG.SEND_EVERY_MINUTES + ' minutes).'
  );
}

/** Handy check: queues a test email to yourself. Run, then wait for the flush. */
function sendMailerTest() {
  var me = Session.getEffectiveUser().getEmail();
  queueEmail_(me, 'JSSA Mailer test', 'If you received this, the Outbox is working.');
  SpreadsheetApp.getActiveSpreadsheet().toast('Test email queued to ' + me + '.');
}

/**
 * Public "send now" button. processOutbox_ is private (trailing underscore) so
 * it doesn't appear in the Run dropdown; this wrapper does. Run it once while
 * logged in as jssagames to grant the send-email permission and flush the queue
 * immediately, instead of waiting for the every-5-minute trigger.
 */
function runOutboxNow() {
  processOutbox_();
}
