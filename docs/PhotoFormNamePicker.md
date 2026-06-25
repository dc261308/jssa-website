# Photo Form — auto-updating name picker (standalone Apps Script)

Goal: instead of typing their name on the photo-upload Google Form, each player
**picks their division, then selects their name** from a dropdown of just that
division's players. The list rebuilds itself **every night** from the
**"JSSA players"** tab in the pickup-game spreadsheet.

IMPORTANT: This is a **standalone** script — a brand-new, separate Apps Script
project. Do **NOT** paste it into the pickup-game spreadsheet's existing code
project (the one with ~26 files). Apps Script files in one project share a
namespace, so adding this could collide with the existing functions (e.g.
`onOpen`) and break the pickup game. Keeping it separate keeps everything safe.

The site already matches photos to players by name, so no website code changes.

## How the form ends up looking
1. **Page 1** — "Division" (RED / WHITE / BLUE). Picking one jumps to that page.
2. **Page 2** — "Select your name" (dropdown of only that division's players).
3. **Page 3** — the existing photo (file-upload) question, then Submit.

The script **replaces** the form's old typed name/team questions with this
two-step picker and **never touches the photo question** (Apps Script can't
create file-upload questions, so we keep the one already there).

## One-time setup (~5 minutes)
1. Get the **pickup-game spreadsheet ID**: open that Google Sheet and copy the
   long code in its web address, between `/d/` and `/edit`.
2. Get your **photo Form's edit URL**: open the Form in edit mode and copy the
   address (ends in `/edit`).
3. Go to **script.google.com → New project** (this is a NEW, separate project —
   not the pickup-game code).
4. Delete the sample code and **paste the whole script below**.
5. Fill in `SHEET_ID` and `FORM_URL` near the top.
6. Click **Save**.
7. In the function dropdown choose **installTriggers**, click **Run**, and
   approve the permission prompt the first time (Advanced → Go to project →
   Allow — it's your own script).
8. Open the **Execution log** — it prints e.g.
   `Done. Photo form now lists RED 40 · WHITE 40 · BLUE 40 players.`

After that it refreshes nightly. To refresh immediately (e.g. after adding
players), open this project, choose **updateNow**, and click **Run**.

## Requirements
- The Form must have **one file-upload question** (the photo); its title should
  include the word **photo**.
- The "JSSA players" tab needs headers **First Name**, **Last Name**,
  **Division** (an optional **Active** column is respected).

## The script

```javascript
/**
 * JSSA Photo Form name picker — STANDALONE Apps Script.
 * Create as a brand-new project at script.google.com (New project).
 * Do NOT paste into the pickup-game spreadsheet's existing code.
 */

// 1) Pickup-game spreadsheet ID — the long code in its web address,
//    between /d/ and /edit. It holds the "JSSA players" tab.
var SHEET_ID = 'PASTE_PICKUP_GAME_SPREADSHEET_ID';

// 2) Photo Form's edit URL (ends in /edit).
var FORM_URL = 'PASTE_PHOTO_FORM_EDIT_URL';

var DIVISIONS = ['RED', 'WHITE', 'BLUE'];
var Q_DIVISION = 'Division';
var Q_NAME = 'Select your name';

/** Run ONCE: refresh now and schedule a nightly refresh. */
function installTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'updatePhotoForm') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('updatePhotoForm').timeBased().everyDays(1).atHour(4).create();
  updateNow();
}

/** Run anytime to refresh the form now (see the Execution log for counts). */
function updateNow() {
  var c = updatePhotoForm();
  Logger.log('Done. Photo form now lists  RED ' + c.RED +
             ' · WHITE ' + c.WHITE + ' · BLUE ' + c.BLUE + '  players.');
}

/** Read the "JSSA players" tab (First Name / Last Name / Division). */
function readRoster() {
  var byDiv = { RED: [], WHITE: [], BLUE: [] };
  var sheets = SpreadsheetApp.openById(SHEET_ID).getSheets();

  var candidates = [];
  for (var s = 0; s < sheets.length; s++) {
    var values = sheets[s].getDataRange().getValues();
    for (var i = 0; i < values.length; i++) {
      var low = values[i].map(function (c) { return String(c || '').trim().toLowerCase(); });
      if (low.indexOf('first name') > -1 && low.indexOf('last name') > -1 &&
          low.indexOf('division') > -1) {
        candidates.push({ name: sheets[s].getName().toLowerCase(), values: values, hdr: i, low: low });
        break;
      }
    }
  }
  if (!candidates.length) {
    throw new Error('No tab with First Name / Last Name / Division headers was found.');
  }
  var pick = candidates.filter(function (c) { return c.name.indexOf('player') > -1; })[0] || candidates[0];

  var col = {};
  pick.low.forEach(function (h, ci) { col[h] = ci; });
  var fi = col['first name'], li = col['last name'], di = col['division'];
  var ai = (col['active'] !== undefined) ? col['active'] : -1;
  var seen = {};
  for (var r = pick.hdr + 1; r < pick.values.length; r++) {
    var row = pick.values[r];
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
  DIVISIONS.forEach(function (d) {
    byDiv[d].sort(function (a, b) { return a.toLowerCase() < b.toLowerCase() ? -1 : 1; });
  });
  return byDiv;
}

/** Rebuild the form's name picker. Returns counts per division. */
function updatePhotoForm() {
  var form = FormApp.openByUrl(FORM_URL);
  var roster = readRoster();

  var photoIds = {};
  form.getItems().forEach(function (it) {
    if (it.getType() === FormApp.ItemType.FILE_UPLOAD) photoIds[it.getId()] = true;
  });
  if (!Object.keys(photoIds).length) {
    throw new Error('No file-upload (photo) question found in the form.');
  }

  // Turn off any existing section branching first, so deleting the old
  // questions can't leave a dangling "go to section" reference (which triggers
  // "Invalid data updating form").
  form.getItems(FormApp.ItemType.MULTIPLE_CHOICE).forEach(function (it) {
    try {
      var mc = it.asMultipleChoiceItem();
      mc.setChoiceValues(mc.getChoices().map(function (c) { return c.getValue(); }));
    } catch (e) {}
  });
  form.getItems(FormApp.ItemType.LIST).forEach(function (it) {
    try {
      var li = it.asListItem();
      li.setChoiceValues(li.getChoices().map(function (c) { return c.getValue(); }));
    } catch (e) {}
  });
  form.getItems(FormApp.ItemType.PAGE_BREAK).forEach(function (it) {
    try { it.asPageBreakItem().setGoToPage(FormApp.PageNavigationType.CONTINUE); } catch (e) {}
  });

  // Delete everything except the photo question (end-to-start, safely).
  form.getItems().slice().reverse().forEach(function (it) {
    if (!photoIds[it.getId()]) {
      try { form.deleteItem(it); } catch (e) {}
    }
  });

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

  redBreak.setGoToPage(photoBreak);
  whiteBreak.setGoToPage(photoBreak);
  blueBreak.setGoToPage(photoBreak);

  var divQ = form.addMultipleChoiceItem()
    .setTitle(Q_DIVISION)
    .setHelpText('Pick your division, then choose your name on the next page.')
    .setRequired(true);
  divQ.setChoices([
    divQ.createChoice('RED', redBreak),
    divQ.createChoice('WHITE', whiteBreak),
    divQ.createChoice('BLUE', blueBreak)
  ]);

  form.moveItem(divQ.getIndex(), 0);
  form.getItems().forEach(function (it) {
    if (photoIds[it.getId()]) form.moveItem(it.getIndex(), form.getItems().length - 1);
  });

  return { RED: roster.RED.length, WHITE: roster.WHITE.length, BLUE: roster.BLUE.length };
}

function setNames(listItem, names) {
  listItem.setChoiceValues(names && names.length ? names : ['(no players listed yet)']);
}
```

## Notes
- Standalone = isolated. It never touches the pickup-game project's existing code.
- Dropdown options are the player's plain **First Last** name (sorted), exactly
  what the website matches on — so the card photo lines up every time.
- Re-running is safe (rebuilds the same structure). Past responses are kept.
- To undo: in this project's **Triggers** (clock icon), delete the daily trigger,
  then edit the form by hand if you wish. Nothing here touches the website.
