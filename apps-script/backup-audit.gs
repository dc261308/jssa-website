/**
 * JSSA — Backup Folder Auditor
 * ---------------------------------------------------------------------------
 * Reads a Google Drive backup folder and reports, for each backup set:
 *   - how many backups exist
 *   - the newest one (and how long ago)
 *   - how often they're running (measured from the file dates)
 *   - a ⚠️ flag for anything that looks stopped or can't be confirmed yet
 *
 * READ-ONLY: it only looks; it never changes, moves, or deletes anything.
 *
 * HOW TO RUN (signed in as the account that owns the backup folder):
 *   1. Go to https://script.google.com  →  New project.
 *   2. Delete the sample, paste this whole file, Save.
 *   3. Pick 'auditBackups' in the function dropdown  →  Run.
 *      Approve the Drive permission (Advanced → Go to … → Allow).
 *   4. Read the report in the Execution log at the bottom.
 * ---------------------------------------------------------------------------
 */

var BACKUP_FOLDER_ID = 'PASTE_FOLDER_ID_HERE';

// Backups are flagged "stale" if the newest one is older than this many days.
// Set to your intended schedule: 2 for daily, 8 for weekly, etc.
var EXPECTED_MAX_GAP_DAYS = 2;


function auditBackups() {
  var folder = DriveApp.getFolderById(BACKUP_FOLDER_ID);
  var files = [];
  _collectFiles(folder, folder.getName(), files, 0);

  if (!files.length) {
    Logger.log('No files found in "' + folder.getName() + '". Either the folder ' +
               'is empty or the backups are landing somewhere else.');
    return;
  }

  // Group files into backup "sets" by name, with trailing date/time stamps
  // stripped, so "Sheet 2026-07-02" and "Sheet 2026-07-01" group together.
  var groups = {};
  for (var i = 0; i < files.length; i++) {
    var key = _baseName(files[i].name);
    (groups[key] = groups[key] || []).push(files[i]);
  }

  var now = new Date();
  var out = [];
  out.push('BACKUP AUDIT — folder "' + folder.getName() + '"');
  out.push('Scanned ' + _fmt(now) + ' — ' + files.length + ' file(s) in ' +
           Object.keys(groups).length + ' backup set(s).');
  out.push('');

  var warnings = [];
  var names = Object.keys(groups).sort();
  for (var g = 0; g < names.length; g++) {
    var list = groups[names[g]].sort(function (a, b) { return a.created - b.created; });
    var oldest = list[0];
    var newest = list[list.length - 1];
    var latestActivity = newest.created;
    for (var k = 0; k < list.length; k++) {
      if (list[k].updated > latestActivity) latestActivity = list[k].updated;
    }
    var daysSince = (now - latestActivity) / 86400000;
    var freq = _frequency(list);

    out.push('• ' + names[g]);
    out.push('    ' + list.length + ' backup(s)  |  newest: ' + _fmt(latestActivity) +
             ' (' + _ago(daysSince) + ')  |  oldest: ' + _fmt(oldest.created));
    out.push('    frequency: ' + freq + '  |  size: ' + _size(newest.size));

    if (list.length < 2) {
      warnings.push(names[g] + ' — only one file so far; can\'t confirm a repeating ' +
                    'schedule yet (or the tool overwrites one file each run).');
    }
    if (daysSince > EXPECTED_MAX_GAP_DAYS) {
      warnings.push(names[g] + ' — newest backup is ' + _ago(daysSince) +
                    ' (older than your ' + EXPECTED_MAX_GAP_DAYS + '-day expectation — may have stopped).');
    }
  }

  out.push('');
  if (warnings.length) {
    out.push('⚠️ ATTENTION:');
    for (var w = 0; w < warnings.length; w++) out.push('  • ' + warnings[w]);
  } else {
    out.push('✅ Every backup set is current (newest within ' +
             EXPECTED_MAX_GAP_DAYS + ' day(s)).');
  }

  Logger.log(out.join('\n'));
}


// Walk the folder and its subfolders, collecting every file's name, date, size.
function _collectFiles(folder, path, out, depth) {
  var it = folder.getFiles();
  while (it.hasNext()) {
    var f = it.next();
    out.push({
      name: f.getName(), folder: path,
      created: f.getDateCreated(), updated: f.getLastUpdated(), size: f.getSize()
    });
  }
  if (depth < 5) {
    var subs = folder.getFolders();
    while (subs.hasNext()) {
      var s = subs.next();
      _collectFiles(s, path + '/' + s.getName(), out, depth + 1);
    }
  }
}


// Remove trailing date/time/backup tokens to find a file's base name.
function _baseName(name) {
  var n = name;
  n = n.replace(/\.(gsheet|xlsx|xls|csv|pdf|zip|json)$/i, '');
  n = n.replace(/\b\d{4}[-_.\/]\d{1,2}[-_.\/]\d{1,2}([ _T]\d{1,2}[:.]\d{2}([:.]\d{2})?\s*(am|pm)?)?\b/gi, '');
  n = n.replace(/\b\d{1,2}[-_.\/]\d{1,2}[-_.\/]\d{2,4}\b/g, '');
  n = n.replace(/\b(backup|copy of|copy|bak|snapshot|export|version)\b/gi, '');
  n = n.replace(/[\-_#()]+/g, ' ').replace(/\s+/g, ' ').trim();
  return n || name;
}


// Describe how often backups run, from the gaps between their dates.
function _frequency(list) {
  if (list.length < 2) return 'unknown (need 2+ backups)';
  var gaps = [];
  for (var i = 1; i < list.length; i++) {
    gaps.push((list[i].created - list[i - 1].created) / 86400000);  // days
  }
  gaps.sort(function (a, b) { return a - b; });
  var med = gaps[Math.floor(gaps.length / 2)];
  var label;
  if (med < 0.06) label = 'about hourly';
  else if (med < 0.35) label = 'several times a day';
  else if (med <= 1.5) label = 'daily';
  else if (med <= 10) label = 'weekly (~' + Math.round(med) + ' days)';
  else label = 'every ~' + Math.round(med) + ' days';
  return label + ' (median gap ' + (Math.round(med * 10) / 10) + ' days)';
}


function _ago(d) {
  if (d < 1) return 'today';
  if (d < 2) return 'yesterday';
  return Math.round(d) + ' days ago';
}

function _size(bytes) {
  if (!bytes) return 'n/a (native Google Sheet)';
  var kb = bytes / 1024;
  if (kb < 1024) return Math.round(kb) + ' KB';
  return (Math.round(kb / 1024 * 10) / 10) + ' MB';
}

function _fmt(d) {
  return Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy-MM-dd h:mm a');
}
