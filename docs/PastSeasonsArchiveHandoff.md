# Past Seasons Archive — Feature Handoff

A portable spec for adding a "Past Seasons" archive to a **Google Apps Script
web app driven by a Google Sheet** (same architecture as the Prime Time and
JSSA sites). Hand this to the other repo's developer or AI assistant.

---

## 1. What it does (plain English)

The site always displays whatever is in the *live* tabs (Schedule, Players,
Standings). When a new season is loaded, the old season is overwritten and
disappears. This feature lets the league **save each finished season** and
**browse it on the website forever**, without cluttering the live tabs.

Two parts:

1. **A one-click "Archive this Season" tool** in the Google Sheet's menu bar.
   At season's end the organizer clicks it, types a season name (e.g. `2025`),
   and the current Schedule (with scores), Rosters, and Standings are copied
   into Archive tabs — stamped with that season name. The live tabs are untouched.
2. **A "Past Seasons" page** on the website with a year dropdown. Pick a season
   and it shows that season's Schedule & Results, Standings, and Rosters in the
   exact same styled format as the current pages.

---

## 2. Key design decisions (the "why")

- **Store the archive in Sheet tabs, NOT in Google Drive copies.** The web app
  can only read its own bound Spreadsheet, so to *display* old seasons the data
  must live in tabs the app can read. (Drive snapshots are fine for backup, but
  the site can't render them.)
- **One "Season" column, not a tab-per-year.** Three growing Archive tabs
  (Schedule / Rosters / Standings), each row tagged with the season. Ten years
  later it's still three tabs, not thirty.
- **Idempotent archiving.** Re-archiving the same season name first deletes that
  season's existing rows, then re-writes them — so fixing a score and clicking
  again just refreshes, never duplicates.
- **Reuse the existing renderers.** Factor the live standings-table and
  roster-card builders into standalone functions, then call the same builders
  for archived data. The archive looks identical to the live pages for free.

---

## 3. Server side (Code.gs / Apps Script)

### 3a. Add a custom menu
```js
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Season Tools')
    .addItem('Archive this Season', 'archiveCurrentSeason')
    .addToUi();
}
```
(If the project already has an `onOpen`, merge this menu into it.)

### 3b. The archive action (prompts for a season name)
```js
function archiveCurrentSeason() {
  var ui = SpreadsheetApp.getUi();
  var resp = ui.prompt('Archive this Season',
    'Type a name for the season (e.g. 2025). This copies the current ' +
    'Schedule, Rosters, and Standings into the Archive tabs. It does NOT ' +
    'change your live tabs.', ui.ButtonSet.OK_CANCEL);
  if (resp.getSelectedButton() !== ui.Button.OK) return;
  var season = (resp.getResponseText() || '').trim();
  if (!season) { ui.alert('No season name entered — nothing archived.'); return; }

  var ss = SpreadsheetApp.getActive(); // or openById(...) on your site
  var c = {
    games:     archiveSchedule_(ss, season),
    rosters:   archiveRosters_(ss, season),
    standings: archiveStandings_(ss, season)
  };
  ui.alert('Season "' + season + '" archived!\n\n• ' + c.games + ' games\n• ' +
    c.rosters + ' players\n• ' + c.standings + ' standings rows');
}
```

### 3c. Two helpers
```js
function getOrCreateSheet_(ss, name, headers) {
  var sh = ss.getSheetByName(name);
  if (!sh) {
    sh = ss.insertSheet(name);
    sh.getRange(1, 1, 1, headers.length).setValues([headers]).setFontWeight('bold');
    sh.setFrozenRows(1);
  }
  return sh;
}

// Delete existing rows for this season (column A) so re-archiving is clean.
function clearSeasonRows_(sh, season) {
  var last = sh.getLastRow();
  if (last < 2) return;
  var vals = sh.getRange(2, 1, last - 1, 1).getValues();
  for (var i = vals.length - 1; i >= 0; i--) {
    if (String(vals[i][0]).trim() === season) sh.deleteRow(i + 2);
  }
}
```

### 3d. The three "copy current → archive" functions
Each reads the live tab and appends its rows to the archive tab with the season
in column A. Example for the schedule (adapt column indexes to your tab):
```js
function archiveSchedule_(ss, season) {
  var src = ss.getSheetByName('Schedule');         // <-- your live schedule tab
  if (!src) return 0;
  var last = src.getLastRow();
  if (last < 2) return 0;
  // Live Schedule columns: Division,Date,Time,Field,Away,Home,AwayScore,HomeScore,Status
  var data = src.getRange(2, 1, last - 1, 9).getValues();
  var rows = data
    .filter(function(r){ return r.join('').trim() !== ''; })
    .map(function(r){ return [season, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]]; });
  var sh = getOrCreateSheet_(ss, 'Archive Schedule',
    ['Season','Division','Date','Time','Field','Away','Home','Away Score','Home Score','Status']);
  clearSeasonRows_(sh, season);
  if (rows.length) sh.getRange(sh.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
  return rows.length;
}
```
`archiveRosters_` and `archiveStandings_` follow the same shape against the
Players and Standings tabs. **Store raw Date/Time cells** (not formatted strings)
so they can be re-formatted and sorted on read.

### 3e. Read functions for the website
```js
function getArchivedSeasons() {
  var ss = SpreadsheetApp.getActive();
  var set = {};
  ['Archive Schedule','Archive Rosters','Archive Standings'].forEach(function(n){
    var sh = ss.getSheetByName(n); if (!sh || sh.getLastRow() < 2) return;
    sh.getRange(2,1,sh.getLastRow()-1,1).getValues().forEach(function(v){
      var s = String(v[0]).trim(); if (s) set[s] = true;
    });
  });
  return Object.keys(set).sort(function(a,b){ return a < b ? 1 : -1; }); // newest first
}

// getArchivedSchedule(season) / getArchivedRosters(season) / getArchivedStandings(season):
// read the matching Archive tab, filter rows where column A === season, and
// return objects in the SAME shape your existing get-functions return, so the
// front-end can reuse its renderers. (Re-format Date/Time and compute a sort key.)
```

### 3f. Archive tab schemas
| Tab | Columns |
|---|---|
| Archive Schedule | Season, Division, Date, Time, Field, Away, Home, Away Score, Home Score, Status |
| Archive Rosters | Season, Division, Team, First Name, Last Name |
| Archive Standings | Season, Division, Team, W, L, Win %, GB, RF, RA, Diff |

---

## 4. Client side (the front-end HTML/JS)

1. **Refactor first:** pull the live standings-table HTML builder and the
   roster-cards HTML builder into standalone functions
   (`buildStandingsTable(data)`, `buildRosterCards(data)`) and have the live
   pages call them. Now they're reusable.
2. **Add a "Past Seasons" menu link** → `showPastSeasons()`.
3. **`showPastSeasons()`** calls `getArchivedSeasons()`, renders a
   `<select>` dropdown of seasons, and loads the newest by default.
4. **`loadSeason(season)`** fires the three archive readers in parallel and,
   when all three return, renders three sections using the reused builders:
   *Schedule & Results* (a simple chronological games table with the winner in
   green), *Standings*, *Rosters*. Cache each season after first load.

Sketch:
```js
function showPastSeasons() {
  renderPage('Past Seasons', 'Loading…');
  google.script.run.withSuccessHandler(function(seasons){
    if (!seasons || !seasons.length) { renderPage('Past Seasons', 'No past seasons yet.'); return; }
    var sel = '<select id="seasonSelect" onchange="loadSeason(this.value)">' +
      seasons.map(function(s,i){ return '<option'+(i?'':' selected')+'>'+s+'</option>'; }).join('') +
      '</select><div id="seasonBody"></div>';
    renderPage('Past Seasons', 'Choose a season: ' + sel);
    loadSeason(seasons[0]);
  }).getArchivedSeasons();
}

function loadSeason(season) {
  var body = document.getElementById('seasonBody'), got = {};
  function render() {
    if (got.sched===undefined || got.rost===undefined || got.stand===undefined) return;
    body.innerHTML =
      '<h3>Schedule & Results</h3>' + buildArchiveGames(got.sched) +
      '<h3>Standings</h3>'         + buildStandingsTable(got.stand) +
      '<h3>Rosters</h3>'           + buildRosterCards(got.rost);
  }
  google.script.run.withSuccessHandler(function(d){ got.sched=d||[]; render(); }).getArchivedSchedule(season);
  google.script.run.withSuccessHandler(function(d){ got.rost =d||[]; render(); }).getArchivedRosters(season);
  google.script.run.withSuccessHandler(function(d){ got.stand=d||[]; render(); }).getArchivedStandings(season);
}
```

---

## 5. End-of-season workflow for the organizer
1. In the Sheet: **Season Tools → Archive this Season** → type the year → OK.
   (First run asks for Google authorization once — that's normal.)
2. Clear the live tabs and load the new season as usual.
3. The finished season now appears on the site under **Past Seasons**.

---

## 6. Adaptation checklist for the other site
- [ ] Match your **live tab names** (Schedule / Players / Standings equivalents).
- [ ] Match the **column order** of those tabs in the `archive*_` functions.
- [ ] Make the archive read functions return objects in the **same shape** your
      existing get-functions already return (so the renderers can be reused).
- [ ] Factor your standings/roster renderers into reusable builders before wiring
      the archive page.
- [ ] No new OAuth scopes are needed beyond Spreadsheet access (and Drive if the
      project already uses it) — but the first menu run will prompt the owner to
      authorize once.
