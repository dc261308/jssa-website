# Recovery Runbook — when the `jssaadmin` Google account is restricted

**Plain-English guide for Tom.** This is what to do when Google restricts the
`jssaadmin` account and the league's automations start failing. Nothing here is a
code change — it is all done inside Google (Apps Script, Sheets). This same file
lives in all three repos (`jssa-website`, `jssa-pickup-game`,
`jssa-registration-payment`) because the problem spans all three.

_First written 2026-07-01, after a Gmail restriction on `jssaadmin` caused the
registration form-submit automation to fail with "Authorization is required to
perform that action."_

---

## 1. What actually happens (and why "just Gmail" still breaks things)

Every automatic action in the league's Google Sheets is a **trigger** or a
**web-app deployment**, and each one **runs as the Google account that created
it** — almost always `jssaadmin`.

When Google restricts that account — **even if it's "only" the Gmail side, and
even if you can still open Google Drive** — two things break:

1. **Anything that sends email fails** (welcome emails, notifications, captain
   emails, alerts, results emails).
2. **Background authorization is pulled.** Google invalidates the stored
   permission that lets automations run *unattended*. This shows up as the error
   **"Authorization is required to perform that action"** — even on actions that
   have nothing to do with email (like writing a row into a sheet).

**Why you can still use Drive but the triggers can't run:** they are two
different kinds of access.
- When *you* click around Drive/Sheets, you're **actively logged in** — Google
  re-verifies you live.
- Triggers run **in the background on stored credentials** — and those are
  exactly what a restriction invalidates.

So: **the triggers are affected even when the restriction looks email-only.**
Don't assume otherwise — check the actual error, which will say "Authorization
is required."

---

## 2. The reliable fix: re-establish critical triggers under a HEALTHY account

The dependable path (works no matter what the restriction covers) is to
re-create the important triggers under a **different, unrestricted Google
account** that has edit access to the sheets. That account has clean
authorization, so both the spreadsheet steps *and* the emails work.

You can *also* try re-authorizing `jssaadmin` (open the script as `jssaadmin`,
run any one function, accept the permission prompt) — but if Gmail is still
restricted, the **email steps will keep failing** until Google lifts it. The
healthy account is the safe bet while you appeal.

### How to re-create a trigger (do this per item in the tables below)
1. Log in as the **healthy account** and open the Google Sheet.
2. **Extensions → Apps Script.**
3. Click the **⏰ Triggers** icon (left sidebar).
4. **Delete** the broken trigger (the one owned by `jssaadmin`, shown failing).
5. **+ Add Trigger** → choose the function, event type, and time shown below →
   Save → **authorize as the healthy account** when prompted.

### How to fix a WEB APP (deployment)
- Open the project → **Deploy → Manage deployments → edit (✏️)**.
- Set **Execute as: Me** (healthy account) and **Who has access: Anyone**.
- **Version: New version**, then **Deploy**.
- ⚠️ **Never pick "New deployment"** — that changes the `/exec` URL and breaks the
  hard-coded links (see §5).

---

## 3. Do this now — priority order

1. **💵 Registration form-submit** (`ia_on_form_submit`) — money/signups. Re-create
   under the healthy account, then **check today's Newmembers / Renewals / Guest /
   85+5 tabs against Payment Reconciliation** and re-file anything missed while it
   was down (helpers: `ia_process_latest_newmember_row`, `ia_backfill_all_source_tabs`).
2. **🌐 Confirm website links work** — click the homepage "Sign up now" / "Sign Up
   for Pickup" button; it should open the schedule assistant, not an error.
3. **🌙 Pickup-game nightly jobs** — re-create any failing time-based triggers
   before the next game day.
4. **🏆 Prediction leaderboard web app** — already re-deployed from another account
   (2026-07-01). Just confirm it still loads without a permission popup.

---

## 4. Full inventory of what can break

### Registration & Payment System — sheet: "Registration and Payment System -LIVE"
Repo: `jssa-registration-payment`

| Automation | File | Type | Sends email? | Notes |
|---|---|---|---|---|
| `ia_on_form_submit` | Intake Automation.gs | On form submit | Yes (Guest welcome) | **Most urgent.** Files signups into Payment Reconciliation. |
| `onFormSubmit` | Registration.gs | On form submit | — | Second form-submit handler. |
| `createRegistrationFullBackup` | Backup Automation.gs | Time — daily midnight | — | Nightly backup. |
| `masterScheduler` | Master Scheduler.gs | Time — recurring | — | Membership-matrix updater; needs a time-based trigger. |
| Notification emails | Notification Automation.gs | (called by above) | **Yes** (GmailApp) | Admin + member notices; also pings the pickup web app (`triggerPickupSync_`). |
| `onOpen` menu | New.gs | On open | — | Only matters when someone opens the sheet. |

### Pickup Game System — pickup-game sheet
Repo: `jssa-pickup-game`

| Automation | File | Type | Sends email? | Notes |
|---|---|---|---|---|
| **Player Portal web app** (`doGet`) | WebApp | Web app | Yes (confirmations) | Public "Sign Up for Pickup" / "Schedule Assistant". **URL is hard-coded across the website — see §5.** |
| `doPost` | Field Sort | Web app | — | Backend endpoint. |
| `onEdit` | Field Sort | On edit | Yes (some paths) | Reacts to sheet edits (roster sync, captain text). |
| `scheduledImportAndSyncPlayers_` | ScheduledAutomation | Time | — | Player import/sync. |
| `scheduledFullAssignmentWorkflow_` | ScheduledAutomation | Time — daily 3 PM | — | Builds the day's team assignments. |
| `rebalanceSchedule` | Field Sort | Time — daily midnight | — | Schedule rebalance. |
| `backupPickupGameSheet` | Field Sort | Time — daily midnight | — | Nightly backup. |
| `resetGameAssignmentDaily` | Field Sort | Time — daily 5 AM | — | Resets assignments. |
| `hideGameDayTeamsView` | Field Sort | Time | — | Hides the view after game day. |
| `importAndSyncPlayers` | Field Sort | Time | — | Player sync (also run via the web-app `?sync=1`). |
| `scheduledEmergencyBackupRefresh` | EmergencyBackup | Time | — | Emergency backup. |
| Captain / alert emails | Field Sort, AutomationAlerts, CaptainEmail | (called by above) | **Yes** (MailApp) | Fail while Gmail is restricted. |
| `onOpen` menu | Field Sort | On open | — | Menu only. |

### Prediction Contest — "Website Control" sheet
Repo: `jssa-website` (folder `website-control/`)

| Automation | File | Type | Sends email? | Notes |
|---|---|---|---|---|
| **Leaderboard/ballot web app** (`doGet`) | (Apps Script) | Web app | — | **URL hard-coded across the website — see §5.** Re-deployed 2026-07-01. |
| In-sheet scoring (`onEdit`) | (Apps Script) | On edit | — | Scores when a Winner is typed into the sheet. |
| Results emails (`sendPredictionResultsEmails_`) | PredictionEngine.gs | (called by scoring) | **Yes** (MailApp) | Game-day recap emails. |

### NOT affected: the public website itself
The Flask website (`jssa-website`, hosted on Render) reads Google Sheets through
a **separate Google service account**, *not* `jssaadmin`. So the website's own
pages keep working even while `jssaadmin` is restricted. Only the Apps Script
automations above are affected.

---

## 5. Hard-coded web-app URLs — handle with care

Two web-app links are **hard-coded in many places** and must **keep the same
URL**. Only ever update them with **Deploy → Manage deployments → edit → New
version** — never "New deployment."

- **Pickup Player Portal:** `…/macros/s/AKfycbzLLz9E…/exec`
  Used on the homepage ("Sign up now"), Teams page, Pickup Games pages, "Board
  Minutes" (`?page=minutes`), **and** pinged by the registration system
  (`triggerPickupSync_`). If this URL changes, all of those break at once.
- **Prediction Leaderboard/Ballot:** `…/macros/s/AKfycbwqXbN6…/exec`
  Used on the homepage and in the prediction pages/emails.

If a "New deployment" was made by mistake and the URL changed, either revert to
the old deployment, or update every reference (search the repos for the URL) —
but keeping the URL stable is far easier.

---

## 6. Cautions

- **Duplicate triggers / double runs.** Triggers belong to whoever created them.
  If you re-create a job under the healthy account **and** `jssaadmin` later comes
  back, *both* can fire — double emails, double backups. **Write down every
  trigger you add**, and once `jssaadmin` is restored, delete the duplicates.
- **Ownership blocker.** If a project/sheet is *owned* by `jssaadmin`, a healthy
  account with only edit access may not be able to manage existing deployments
  (you'll see an empty/blocked "Manage deployments" screen). Fastest fixes:
  recover `jssaadmin`, or have `jssaadmin` **transfer ownership** to the healthy
  account before any restriction becomes permanent. Add the healthy account as an
  **Editor** on every sheet now, either way.

---

## 7. When `jssaadmin` is restored

1. Confirm Gmail sending works again (send a test).
2. Decide which account should own the automations going forward. Running them
   from a stable account you control reduces the chance of a repeat.
3. **Remove duplicate triggers** created during the outage so nothing runs twice.
4. Re-check the two web apps in §5 still resolve to their original URLs.
