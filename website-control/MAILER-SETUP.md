# JSSA Mailer (Outbox) — setup guide

Goal: **every league email goes out from one dedicated account
(`jssagames@gmail.com`)**, while the core automation keeps running under the
owner (personal) account. If the mail account ever gets rate-limited, only email
pauses — nothing else breaks, and no email is lost (it just sends later).

This is done with an **Outbox**: normal triggers *queue* messages (write them to
a hidden tab); one trigger owned by `jssagames` *sends* them.

## How it works

- `queueEmail_(...)` — drop-in replacement for `MailApp.sendEmail` /
  `GmailApp.sendEmail`. Writes the message to a hidden **Outbox** tab. Runs as
  the owner (personal) account — no email permission needed.
- `processOutbox_()` — reads the Outbox and actually sends. **Runs on a trigger
  owned by `jssagames@gmail.com`**, so all mail is sent from the league address.

## One-time setup — per project

Do this in **each** Apps Script project that sends email (Registration, Pickup,
Website Control, Apparel):

1. **Give `jssagames@gmail.com` Editor access** to that project's spreadsheet.
2. **Add the Mailer file.** In the Apps Script editor: **＋ → Script**, name it
   `Mailer`, and paste in the contents of `Mailer.gs`.
3. **Swap the send calls.** In every *other* file, replace:
   - `MailApp.sendEmail(`  →  `queueEmail_(`
   - `GmailApp.sendEmail(` →  `queueEmail_(`
   Do **not** edit `Mailer.gs` itself — its real send uses `MailApp['sendEmail']`
   (bracket notation) on purpose, so a find/replace of `MailApp.sendEmail(` will
   skip it. (Test-only files like `TEST_*` can be left as-is or swapped; harmless
   either way.)
   `queueEmail_` accepts the exact same arguments those functions did, so no other
   code changes are needed.
4. **Save.**
5. **Log in as `jssagames@gmail.com`**, open the same project, and run
   **`installOutboxProcessor`** once. Approve the permission prompt (this is where
   `jssagames` is granted the "send email" permission). That creates the
   every-5-minutes sender trigger, owned by `jssagames`.

## Test it

1. Back as the owner account, run **`sendMailerTest`** — it queues a test email to
   you and creates the hidden **Outbox** tab.
2. Within ~5 minutes the row's **Status** flips from `QUEUED` to `SENT`, and the
   email arrives **from jssagames@gmail.com**. ✅

## Good to know

- **Delay:** email is no longer instant — it goes out on the next flush (default
  every 5 min; change `SEND_EVERY_MINUTES` in `Mailer.gs`). Fine for league mail.
- **Nothing is lost:** if the daily limit is hit, remaining messages stay `QUEUED`
  and send the next day automatically.
- **Free-Gmail limit:** a free Gmail account can send ~100 emails/day. On a busy
  game day that could be tight. If you regularly exceed it, move `jssagames` to a
  **Google Workspace** mailbox on the `jupiterseniorsoftball.com` domain (much
  higher limits, far less likely to be flagged). The Outbox code doesn't change —
  only which account owns `processOutbox_`.
- **Failures are visible:** a send that errors is marked `ERROR` with the reason in
  the Outbox, so nothing fails silently.

## Where email is sent from today (what to swap)

- **Registration:** `Notification Automation.gs` (admin + member/guest emails),
  `New.gs` (85+5 invitations).
- **Pickup:** `CaptainEmail`, `AutomationAlerts`, `Field Sort`, `NewMemberImport`,
  `WebApp` (sign-up confirmation).
- **Website Control:** `sendPredictionResultsEmails_` (results), plus the contact
  and sponsor form notifications.
- **Apparel:** order/overdue/ready notices (confirm once the code is captured).
