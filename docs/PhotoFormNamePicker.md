# Photo Form — auto-updating name picker (Apps Script)

Goal: instead of typing their name on the photo-upload Google Form, each player
**picks their division, then selects their name** from a dropdown of just that
division's players. The list rebuilds itself **every night** from the master
**JSSA players** list, and Tom can refresh it on demand from a **JSSA Tools**
menu. No website code changes — the site already matches photos to players by
name, so picking the exact name makes the match reliable.

## How the form ends up looking
1. **Page 1** — "Division" (RED / WHITE / BLUE). Picking one jumps to that
   division's page.
2. **Page 2** — "Select your name" (a dropdown of only that division's players).
3. **Page 3** — the existing photo (file-upload) question, then Submit.

The script **replaces** the form's old typed name/team questions with this
two-step picker. It **never touches the photo question** (Apps Script can't
create file-upload questions, so we keep the one that's already there).

## One-time setup (~5 minutes)
1. Open your **JSSA players** spreadsheet — the one with the
   `First Name` / `Last Name` / `Division` columns.
2. Top menu: **Extensions → Apps Script**. A code editor opens in a new tab.
3. Delete anything in the editor and **paste the whole script below**.
4. Near the top, replace the `FORM_URL` value: open your photo **Form** in edit
   mode, copy the address-bar URL (it ends in `/edit`), and paste it between the
   quotes.
5. Click **Save** (disk icon).
6. In the function dropdown at the top choose **installTriggers**, click **Run**,
   and approve the permission prompt the first time (Advanced → Go to project →
   Allow — it's your own script).
7. A popup confirms it filled the form. Reload the spreadsheet tab and you'll see
   a new **JSSA Tools** menu.

After that: it refreshes nightly. When you add players, click
**JSSA Tools → Update photo-form name list** to refresh immediately.

## Requirements
- The Form must have **one file-upload question** (the photo). Its title should
  include the word **photo**.
- The master list tab needs headers **First Name**, **Last Name**, **Division**
  (an optional **Active** column is respected — non-active players are skipped).

## The script

```javascript
/**
 * JSSA Photo Form — fill the "Select your name" dropdowns from the JSSA players
 * list, grouped by division, and keep it up to date automatically.
 * Bind this to the spreadsheet that holds the JSSA players list
 * (Extensions -> Apps Script).
 */

// ▼▼▼ PASTE YOUR PHOTO FORM'S EDIT URL BETWEEN THE QUOTES ▼▼▼
var FORM_URL = 'https://docs.google.com/forms/d/REPLACE_WITH_YOUR_FORM_ID/edit';
// ▲▲▲

var DIVISIONS = ['RED', 'WHITE', 'BLUE'];
var Q_DIVISION = 'Division';
var Q_NAME = 'Select your name';

/** Adds the "JSSA Tools" menu when the spreadsheet opens. */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('JSSA Tools')
    .addItem('Update photo-form name list', 'menuUpdate')
    .addItem('Set up / repair (run once)', 'installTriggers')
    .addToUi();
}

/** Menu handler: update now and report what happened. */
function menuUpdate() {
  try {
    var c = updatePhotoForm();
    SpreadsheetApp.getUi().alert(
      'Done! The photo form now lists  RED ' + c.RED +
      ' · WHITE ' + c.WHITE + ' · BLUE ' + c.BLUE + '  players.');
  } catch (e) {
    SpreadsheetApp.getUi().alert(
      'Could not update the form:\n\n' + e.message +
      '\n\nCheck that FORM_URL (top of the script) is your photo form\'s edit ' +
      'link, and that the form has one photo (file-upload) question.');
  }
}

/** Run once: refresh now and schedule a nightly refresh. */
function installTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'updatePhotoForm') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('updatePhotoForm').timeBased().everyDays(1).atHour(4).create();
  menuUpdate();
}

/** Read the players list (first tab whose header has First/Last/Division). */
function readRoster() {
  var byDiv = { RED: [], WHITE: [], BLUE: [] };
  var sheets = SpreadsheetApp.getActiveSpreadsheet().getSheets();
  for (var s = 0; s < sheets.length; s++) {
    var values = sheets[s].getDataRange().getValues();
    var hdr = -1, col = {};
    for (var i = 0; i < values.length; i++) {
      var low = values[i].map(function (c) { return String(c || '').trim().toLowerCase(); });
      if (low.indexOf('first name') > -1 && low.indexOf('last name') > -1 &&
          low.indexOf('division') > -1) {
        hdr = i;
        low.forEach(function (h, ci) { col[h] = ci; });
        break;
      }
    }
    if (hdr < 0) continue;
    var fi = col['first name'], li = col['last name'], di = col['division'];
    var ai = (col['active'] !== undefined) ? col['active'] : -1;
    var seen = {};
    for (var r = hdr + 1; r < values.length; r++) {
      var row = values[r];
      var first = String(row[fi] || '').trim();
      var last = String(row[li] || '').trim();
      var d0 = String(row[di] || '').trim().toUpperCase().charAt(0);
      var div = d0 === 'R' ? 'RED' : d0 === 'W' ? 'WHITE' : d0 === 'B' ? 'BLUE' : '';
      var name = (first + ' ' + last).trim();
      if (!name || !div) continue;
      if (ai > -1) {
        var act = String(row[ai] || '').trim().toLowerCase();
        if (['no', 'n', 'false', '0', 'inactive'].indexOf(act) > -1) continue;
      }
      var key = div + '|' + name.toLowerCase();
      if (seen[key]) continue;
      seen[key] = true;
      byDiv[div].push(name);
    }
    break; // first matching tab wins
  }
  DIVISIONS.forEach(function (d) {
    byDiv[d].sort(function (a, b) { return a.toLowerCase() < b.toLowerCase() ? -1 : 1; });
  });
  return byDiv;
}

/** Rebuild the form's name picker. Returns counts per division. */
function updatePhotoForm() {
  var form = FormApp.openByUrl(FORM_URL);
  var roster = readRoster();

  // Keep the photo (file-upload) question; abort if it's missing so we never
  // wipe the form by mistake.
  var photoIds = {};
  form.getItems().forEach(function (it) {
    if (it.getType() === FormApp.ItemType.FILE_UPLOAD) photoIds[it.getId()] = true;
  });
  if (!Object.keys(photoIds).length) {
    throw new Error('No file-upload (photo) question found in the form.');
  }

  // Delete everything except the photo question — we rebuild the rest.
  form.getItems().slice().reverse().forEach(function (it) {
    if (!photoIds[it.getId()]) form.deleteItem(it);
  });

  // Build the three division sections + name dropdowns, then a photo section.
  var redBreak = form.addPageBreakItem().setTitle('RED Division');
  var redName = form.addListItem().setTitle(Q_NAME).setRequired(true);
  var whiteBreak = form.addPageBreakItem().setTitle('WHITE Division');
  var whiteName = form.addListItem().setTitle(Q_NAME).setRequired(true);
  var blueBreak = form.addPageBreakItem().setTitle('BLUE Division');
  var blueName = form.addListItem().setTitle(Q_NAME).setRequired(true);
  var photoBreak = form.addPageBreakItem().setTitle('Upload your photo');

  setNames(redName, roster.RED);
  setNames(whiteName, roster.WHITE);
  setNames(blueName, roster.BLUE);

  // After any division's name, jump straight to the photo section.
  redBreak.setGoToPage(photoBreak);
  whiteBreak.setGoToPage(photoBreak);
  blueBreak.setGoToPage(photoBreak);

  // Division question routes to each section.
  var divQ = form.addMultipleChoiceItem()
    .setTitle(Q_DIVISION)
    .setHelpText('Pick your division, then choose your name on the next page.')
    .setRequired(true);
  divQ.setChoices([
    divQ.createChoice('RED', redBreak),
    divQ.createChoice('WHITE', whiteBreak),
    divQ.createChoice('BLUE', blueBreak)
  ]);

  // Order: division first, photo last.
  form.moveItem(divQ.getIndex(), 0);
  form.getItems().forEach(function (it) {
    if (photoIds[it.getId()]) form.moveItem(it.getIndex(), form.getItems().length - 1);
  });

  return { RED: roster.RED.length, WHITE: roster.WHITE.length, BLUE: roster.BLUE.length };
}

/** Set a dropdown's choices, with a friendly placeholder if a division is empty. */
function setNames(listItem, names) {
  listItem.setChoiceValues(names && names.length ? names : ['(no players listed yet)']);
}
```

## Notes
- Dropdown options are the player's plain **First Last** name (sorted), which is
  exactly what the website matches on — so a player's card photo lines up every
  time.
- Re-running is safe (it rebuilds the same structure). Past responses already in
  the response sheet are not deleted.
- To undo entirely: delete the time trigger (Apps Script → Triggers → trash it)
  and rebuild the form's questions by hand. Nothing here touches the website.
