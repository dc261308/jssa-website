# Email Send Counter — setup (one-time)

This powers the **Email Send Counter** page in the admin dashboard, which shows —
live — how many emails each league Gmail account has sent today and how many
sends are left before Gmail's ~100-a-day limit.

Because only code running *inside* a Gmail account can read that account's Sent
mail, each account gets its own tiny copy of the same script.

## Accounts watched
- `jssagames@gmail.com`
- `jssaadmin@gmail.com`
- `cosentinoteam@gmail.com`

## Steps

### 1. Deploy the script in each account
For **each** of the three accounts, signed in as that account:
1. Open <https://script.google.com> → **New project**.
2. Delete the sample code, paste all of [`email-usage.gs`](./email-usage.gs), Save.
3. Set `SECRET` to a long random phrase — **the same phrase in all three**.
4. **Run the `authorizeNow` function once** to grant Gmail access: pick it in
   the editor's function dropdown, click **Run**, choose the account, and on the
   "unverified app" screen click **Advanced → Go to … → Allow**. (Skipping this
   makes the web app return a "does not have permission" error.)
5. **Deploy → New deployment → Web app**:
   - **Execute as:** Me
   - **Who has access:** Anyone
6. Deploy and copy the **Web app URL** (ends in `/exec`).
7. Add `?key=YOUR_SECRET` to the end of that URL.

You'll end up with three URLs like:
```
https://script.google.com/macros/s/AAAA.../exec?key=YOUR_SECRET
https://script.google.com/macros/s/BBBB.../exec?key=YOUR_SECRET
https://script.google.com/macros/s/CCCC.../exec?key=YOUR_SECRET
```

### 2. Tell the website about them (Google Sheet — no Render needed)
Open the **control sheet** ("JSSA website control sheet_live", the same one with
the *Board Portal Links* tab) and find the **"Email Accounts"** tab. The website
creates it automatically the first time the counter page is opened, pre-filled
with the headers and one hidden example row:

| Account | Reporter URL | Show? |
|---|---|---|
| jssagames@gmail.com | `https://.../exec?key=YOUR_SECRET` | Yes |
| jssaadmin@gmail.com | `https://.../exec?key=YOUR_SECRET` | Yes |
| cosentinoteam@gmail.com | `https://.../exec?key=YOUR_SECRET` | Yes |

Fill in one row per account, paste each account's URL into **Reporter URL**, and
set **Show?** to **Yes**. That's it — the counter picks it up within a minute.

## Watch an account privately (not on the website)
To monitor an account for your own eyes only — never shown on the site — deploy
the reporter in that account exactly as above, but **do not** add it to the
"Email Accounts" tab. Instead, in a Google Sheet that only you can see, put:

```
=IMPORTDATA("https://.../exec?key=YOUR_SECRET&format=csv")
```

That fills a small two-column table (emails sent today, people reached, sends
left, etc.). The site never knows about it because it isn't on the "Email
Accounts" tab. Note: `IMPORTDATA` refreshes about once an hour on its own; for
an instant read, open the same URL (without `&format=csv`) in a browser.

## Notes
- The page labels each account automatically from what its script reports; the
  **Account** column is just a friendly label for you in the sheet.
- Add, hide, or remove an account anytime by editing the tab — set **Show?** to
  **No** to hide one. No code change, no Render access.
- A BCC blast to 80 people counts as **1** message but **80** recipients. The
  "sends left today" number uses recipients, since that's what Gmail limits.
- To change the daily limit, edit `DAILY_LIMIT` at the top of the script in each
  account.
