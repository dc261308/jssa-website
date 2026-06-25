# Photo Form — auto-updating name picker (standalone Apps Script)

Goal: instead of typing their name on the photo-upload Google Form, each player
**picks their division, then selects their name** from a dropdown of just that
division's players. The list rebuilds itself **every night** from the
**"JSSA players"** tab in the pickup-game spreadsheet.

IMPORTANT: This is a **standalone** script — a brand-new, separate Apps Script
project. Do **NOT** paste it into the pickup-game spreadsheet's existing code
project (the one with ~26 files); function-name collisions could disrupt the
pickup game. The site already matches photos to players by name, so no website
code changes.

## Stable-by-design (important)
The updater builds the Division → name → photo structure **once**, then on every
later run only **refreshes the dropdown choices in place**. It does NOT delete
and recreate the questions, because recreating them makes Google Forms add a new
**response column** every time — which scatters each uploader's name across many
columns. By refreshing in place, the response sheet stays stable: one name
column per division, so you can always tell who uploaded what (and delete/replace
a row to remove/replace a photo).

## How the form looks
1. **Page 1** — "Division" (RED / WHITE / BLUE). Picking one jumps to that page.
2. **Page 2** — "Select your name" (dropdown of only that division's players).
3. **Page 3** — the existing photo (file-upload) question, then Submit.

## One-time setup (~5 minutes)
1. Get the **pickup-game spreadsheet ID** (the long code in its web address,
   between `/d/` and `/edit`).
2. Get your **photo Form's edit URL** (open the Form in edit mode; ends in
   `/edit`).
3. Go to **script.google.com → New project** (a NEW, separate project).
4. Delete the sample code and **paste the whole script below**.
5. Fill in `SHEET_ID` and `FORM_URL` near the top. Click **Save**.
6. In the function dropdown choose **installTriggers**, click **Run**, and
   approve the permission prompt the first time.
7. The **Execution log** prints e.g.
   `Done. Photo form now lists RED 40 · WHITE 40 · BLUE 40 players.`

After that it refreshes nightly; run **updateNow** to refresh immediately.

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

// 1) Pickup-game spreadsheet ID — it holds the "JSSA players" tab.
var SHEET_ID = 'PASTE_PICKUP_GAME_SPREADSHEET_ID';

// 2) Photo Form's EDIT url (ends in /edit).
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
  // Sort each division by LAST name (first name breaks ties).
  DIVISIONS.forEach(function (d) {
    byDiv[d].sort(function (a, b) {
      var la = lastName(a), lb = lastName(b);
      if (la !== lb) return la < lb ? -1 : 1;
      return a.toLowerCase() < b.toLowerCase() ? -1 : 1;
    });
  });
  return byDiv;
}

/** The last word of a full name, lowercased (used to sort by last name). */
function lastName(full) {
  var p = String(full || '').trim().split(/\s+/);
  return p[p.length - 1].toLowerCase();
}

/**
 * Refresh the name lists. If the Division -> name structure already exists, we
 * update the dropdowns IN PLACE — recreating them each run is what made Google
 * Forms add a fresh response column every time (scattering the names). Only the
 * very first run builds the structure.
 */
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

  var nd = findNameDropdowns(form);
  if (nd.RED && nd.WHITE && nd.BLUE) {
    setNames(nd.RED, roster.RED);
    setNames(nd.WHITE, roster.WHITE);
    setNames(nd.BLUE, roster.BLUE);
    return countOf(roster);
  }

  buildForm(form, roster, photoIds);
  return countOf(roster);
}

function countOf(roster) {
  return { RED: roster.RED.length, WHITE: roster.WHITE.length, BLUE: roster.BLUE.length };
}

/** Find each division's existing name dropdown by walking the sections. */
function findNameDropdowns(form) {
  var out = {}, cur = '';
  form.getItems().forEach(function (it) {
    var t = it.getType();
    if (t === FormApp.ItemType.PAGE_BREAK) {
      var u = String(it.getTitle() || '').toUpperCase();
      cur = u.indexOf('RED') === 0 ? 'RED' : u.indexOf('WHITE') === 0 ? 'WHITE'
          : u.indexOf('BLUE') === 0 ? 'BLUE' : '';
    } else if (t === FormApp.ItemType.LIST && cur && !out[cur]) {
      out[cur] = it.asListItem();
    }
  });
  return out;
}

/** First-time build of the Division -> name -> photo structure. */
function buildForm(form, roster, photoIds) {
  // Turn off existing branching so deleting old questions can't leave a
  // dangling "go to section" reference ("Invalid data updating form").
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
}

function setNames(listItem, names) {
  listItem.setChoiceValues(names && names.length ? names : ['(no players listed yet)']);
}
```

## Managing uploads
- Each upload's name lands in the **"Select your name"** column for that
  player's division (plus the **Division** column and the **Photo Upload** link).
- To remove or replace someone's photo: find their row and delete it (or
  clear/replace the Photo Upload link). The card reverts/updates within minutes.
- The empty/duplicate columns left over from earlier testing are harmless; the
  website ignores them and no new ones are created now.
- Optional future enhancement: an onFormSubmit trigger could write a single
  consolidated "Player" column next to each photo. Not built (keeps it simple).
