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
4. **Deploy → New deployment → Web app**:
   - **Execute as:** Me
   - **Who has access:** Anyone
5. Deploy, approve the permissions, and copy the **Web app URL** (ends in `/exec`).
6. Add `?key=YOUR_SECRET` to the end of that URL.

You'll end up with three URLs like:
```
https://script.google.com/macros/s/AAAA.../exec?key=YOUR_SECRET
https://script.google.com/macros/s/BBBB.../exec?key=YOUR_SECRET
https://script.google.com/macros/s/CCCC.../exec?key=YOUR_SECRET
```

### 2. Tell the website about them (Render)
In Render → the `jssa-website` service → **Environment**, add one variable:

- **Key:** `EMAIL_USAGE_URLS`
- **Value:** the three URLs above, separated by commas (no spaces):
  ```
  https://.../exec?key=YOUR_SECRET,https://.../exec?key=YOUR_SECRET,https://.../exec?key=YOUR_SECRET
  ```

Save. Render redeploys in about a minute, and the Email Send Counter goes live.

## Notes
- The page labels each account automatically from what its script reports — the
  order of the URLs doesn't matter, and you can add or remove an account later
  just by editing this one variable (no code change).
- A BCC blast to 80 people counts as **1** message but **80** recipients. The
  "sends left today" number uses recipients, since that's what Gmail limits.
- To change the daily limit, edit `DAILY_LIMIT` at the top of the script in each
  account.
