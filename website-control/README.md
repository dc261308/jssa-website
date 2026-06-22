# Website Control — Apps Script (the prediction-contest engine)

> **What this is:** the prediction contest's brain is a **Google Apps Script bound to
> the "Website Control" Google Sheet** (open that sheet → Extensions → Apps Script).
> It is deployed as a **Web App** (`/exec`) and runs entirely inside Google — it is
> **not** deployed by Render and was historically **not in any GitHub repo**. That
> invisibility is what made the June 2026 bugs so hard to find. This folder exists so
> the next chat (and the next person) can see how it works without guessing.
>
> The live source still lives in the Apps Script editor. Treat the notes below as the
> map; see "Capturing the actual source" at the bottom for getting a faithful copy.

## Who does what (it's spread across three apps)

| Piece | Where it runs | Role in the contest |
|---|---|---|
| **Website Control Apps Script** | bound to the Website Control sheet | Serves the ballot, **builds the Prediction Games tab**, scores, computes analytics/leaderboard. The brain. |
| `jssa-website` (this Flask app) | Render | Only **reads** the resulting tabs for the homepage + admin scoring page. |
| `jssa-pickup-game` Apps Script | bound to the pickup-game sheet | During publish, **pings** the web app (`create_prediction_games`). Sends no data; does no writing itself. |

Because the brain is invisible to the two repos, a failure in it looks like "nothing
happened" from either side — exactly what bit us.

## The three spreadsheets

| Role | Spreadsheet ID | Key tabs |
|---|---|---|
| **Website Control** (this script is bound here) | `1Bpb1PGs2-egEql9rgIsNzFWlRKSrBYLxWdy1NeFkmaM` | Prediction Games, Prediction Picks, Prediction Analytics, Prediction Metrics, Prediction Leaderboard, Prediction Champions, Prediction Control (settings) |
| **Public Game Day sheet** | `1oHgGae0aXVVsr7t9hmDmoLxZWO5p9rLFPebSsXoFfAA` | `Game_Day_Teams` (published rosters) — read by the builder **and** by this Flask site |
| **Master roster** | `1YHKk8GLM9kqzSoWFxuUtFCH-B6crZ7SP5m4vogJVBwg` | `JSSA Players` (member validation), `Game Assignment Sheet` |

## Web-app routing (`doGet`)

- `?action=create_prediction_games&key=…` → `createPredictionGamesFromGameDayTeams()`
  builds the **Prediction Games** tab from `Game_Day_Teams`.
  **⚠️ It always replies `"Prediction games created."` no matter how many rows it
  actually wrote** — so the pickup-game automation log can show SUCCESS while nothing
  was written. The log is the script grading its own homework; trust the *tab*, not the log.
- `?action=score_predictions&key=…` → `finalizePredictionResultsForLatestScoredDate()`
  (the full scoring chain — see below).
- `?view=predictions_test` → PredictionBallot, `?view=prediction_leaderboard` → leaderboard,
  `?view=prediction_info` / `prediction_champions` → info pages.

## The pipeline, end to end

1. **Ballot** (`PredictionBallot.html`) — member enters email (`validatePredictionPlayerByEmail`
   checks `JSSA Players`), gets open games (`getPredictionGamesForBallot`, reads the
   **Prediction Games** tab), submits picks (`savePredictionSubmission` → appends to
   **Prediction Picks**).
2. **Publish** (pickup-game) pings `create_prediction_games` → `createPredictionGamesFromGameDayTeams()`
   reads `Game_Day_Teams` and appends one row per field to **Prediction Games**.
3. **Score** — two paths:
   - **From the website admin** (`/admin/predictions`) → writes the Winner, then calls
     `score_predictions` → `finalizePredictionResultsForLatestScoredDate()`, which runs the
     **full** chain: `scorePredictions` → `updatePredictionAnalytics` → `updatePredictionLeaderboard`
     → `updatePredictionChampions` → `updatePredictionMetrics`.
   - **Typing the Winner straight into the sheet** → `onEdit` → `handlePredictionGamesEdit_`
     (see bug #2 — this path is missing a step).
4. **Homepage** (Flask) reads **Prediction Metrics** ("Season insights") and **Prediction
   Leaderboard**.

## ⚠️ The golden rule: field names must match everywhere

Scoring and analytics join Picks ↔ Games on **(Game Date + Field)** as an **exact string
match**. The field label must be identical across the `Game_Day_Teams` columns, the
**Prediction Games** tab, and the **Prediction Picks** tab. When the league moved from
`Field 1`–`Field 4` to `Maplewood East` / `Maplewood West`, this broke in two places.
**If predictions ever stop scoring, check the field labels match first.**

## Bugs found & fixed — 2026-06-22

**1. The builder ignored renamed fields (publishing wrote nothing).**
`getPredictionGamesFromGameDayTeams_()` hard-coded the field columns as `Field 1`–`Field 4`,
so on the new `Maplewood East/West` sheet it matched zero columns and wrote zero rows — while
the web app still replied "Prediction games created." Fixed by reading **any** non-blank
header column that has an `H CAPTAIN` and a `V CAPTAIN` below it, so it works whatever the
fields are named:

```js
function getPredictionGamesFromGameDayTeams_() {
  const ss = SpreadsheetApp.openById('1oHgGae0aXVVsr7t9hmDmoLxZWO5p9rLFPebSsXoFfAA');
  const sheet = ss.getSheetByName('Game_Day_Teams');
  if (!sheet) throw new Error('Game_Day_Teams sheet not found.');

  const values = sheet.getDataRange().getDisplayValues();
  const displayDate = String(sheet.getRange('A2').getDisplayValue() || '').trim();

  let headerRowIndex = -1;
  for (let r = 0; r < values.length; r++) {
    if (String(values[r][0] || '').trim() === "Today's Players") { headerRowIndex = r; break; }
  }
  if (headerRowIndex === -1) throw new Error('Could not find Game_Day_Teams header row.');

  const headerRow = values[headerRowIndex];

  // Any column after the player-name column with a non-blank header is a candidate
  // field — whatever it's named this season. We confirm it's a real game by finding
  // both an H CAPTAIN and a V CAPTAIN below it, so stray columns are ignored.
  const fieldColumns = [];
  for (let c = 1; c < headerRow.length; c++) {
    const header = String(headerRow[c] || '').trim();
    if (header) fieldColumns.push({ field: header, col: c });
  }

  const games = [];
  fieldColumns.forEach(fieldInfo => {
    let homeCaptain = '', visitorCaptain = '';
    for (let r = headerRowIndex + 1; r < values.length; r++) {
      const playerName = String(values[r][0] || '').trim();
      const mark = String(values[r][fieldInfo.col] || '').trim().toUpperCase();
      if (!playerName) continue;
      if (mark === 'H CAPTAIN') homeCaptain = playerName;
      if (mark === 'V CAPTAIN') visitorCaptain = playerName;
    }
    if (homeCaptain && visitorCaptain) {
      games.push({ gameDate: displayDate, field: fieldInfo.field,
                   homeCaptain: homeCaptain, visitorCaptain: visitorCaptain });
    }
  });
  return games;
}
```

**2. In-sheet scoring leaves the homepage "Season insights" stale (recommended fix).**
`handlePredictionGamesEdit_()` (the `onEdit` path used when you type a Winner directly into
the Prediction Games tab) runs `scorePredictions`, `updatePredictionAnalytics`,
`updatePredictionLeaderboard`, `updatePredictionChampions` — but **not**
`updatePredictionMetrics()`. The homepage Season insights read the **Prediction Metrics** tab,
so they don't refresh. Scoring **from the website admin** runs the full chain and updates
everything. Permanent fix: add `updatePredictionMetrics();` to `handlePredictionGamesEdit_`
right after `updatePredictionChampions();`.

## Deployment gotcha

The web-app URL (`…/macros/s/AKfycb…/exec`) is **hard-coded** in
`jssa-pickup-game/Field Sort` and in `jssa-website/app.py`. If this script is ever
re-deployed as a **new** deployment, Google issues a brand-new URL and **both callers
silently break**. Always deploy via **Manage deployments → edit the existing deployment →
New version**, never "New deployment."

## Known cosmetic mismatch

The ballot / leaderboard pages tell members "**4 picks** to qualify," but the
`Minimum Monthly Predictions` setting on the Prediction Control tab is **6**. Reconcile when convenient.

## Capturing the actual source

The `.gs` and `.html` files still live only in the Apps Script editor. To get a faithful,
updatable copy into this folder (recommended), use Google's **clasp** CLI once:

```
npm i -g @google/clasp
clasp login
clasp clone <SCRIPT_ID>     # Script ID is in Apps Script → Project Settings
```

Then the pulled files can be committed here and kept in sync. Until then, this README is the
authoritative map of how the engine works.
