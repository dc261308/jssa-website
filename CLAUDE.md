# Jupiter Senior Softball Association — Website

A Flask website for a senior (55+) slow-pitch softball league in Jupiter, FL.
It is maintained mostly by **Tom**, the league organizer, who is sharp but **not a developer** — he makes changes by asking you in plain English. Be friendly, explain what you're doing in simple terms, keep edits small and reversible, and always tell him how to undo a change if he doesn't like it.

## How it's deployed
- Hosted on **Render**. Every push to the `main` branch **auto-deploys** (takes ~1 minute).
- A failed build does **not** take the site down — Render keeps the last good version live. So the worst case of a bad change is "nothing happened," never "the site is down."
- Normal flow: make the change, explain it plainly, commit with a clear message.
- For anything bigger or riskier, **open a pull request** so Tom can review before it goes live.

## Project map
- `app.py` — Flask routes: homepage, `/teams`, `/players`, interior pages, and `/admin`.
- `sheets.py` — reads/writes **Google Sheets** via a service account (announcements, sponsors, board members, Hall of Fame, In Memoriam, rosters).
- `templates/index.html` — the homepage. **This file is large because the league logo and a custom font ("Sablon Up") are embedded directly as base64. That's intentional — do not strip them out.**
- `templates/pages/` — interior pages: `hall-of-fame`, `in-memoriam`, `players`, `bylaws`, `playing-rules`, `code-of-conduct`, `pickup-games`.
- `templates/admin/` — the password-protected admin panel Tom uses to edit content (dashboard, blackboard, manage-board, manage-hof, manage-memoriam, manage-sponsors, login).
- `static/` — images and logos, including `static/jssa-banner.png` (the league masthead banner).
- `render.yaml`, `Procfile`, `requirements.txt` — deploy/config. Leave these alone unless explicitly asked.

## Design system — keep changes consistent
- **Fonts:** Archivo (display/headings) + Inter (body). The hero title uses the embedded **Sablon Up College** varsity font.
- **Colors:** navy `#16233f`, blue `#2b5bd0`, red `#d8262b`, light background `#f3f6fb`. League divisions are **RED / WHITE / BLUE**.
- **Audience is older** — always favor large text, high contrast, big tap targets, and clear, obvious buttons. Accessibility for seniors beats cleverness on every visual change.

## Content that lives in Google Sheets (not in code)
Announcements/weather notices, sponsors, board members, Hall of Fame, In Memoriam, and rosters come from **Google Sheets** and are normally edited through the `/admin` panel — **not** by editing code. If Tom wants to change that kind of content, point him to the admin panel. Only edit the underlying templates/logic if he specifically asks for a layout or design change.

## Guardrails — important
- **Never print, move, or change secrets or keys.** The site's settings (admin password, the Google service-account key, sheet IDs) live in **Render's Environment settings**, *not* in this repo. Never add them to code or commit them.
- Don't delete pages, the admin panel, or the embedded logo/font unless Tom explicitly asks.
- Keep older users in mind on every visual change (size, contrast, simplicity).
- After each change, summarize in plain English what you did and how to undo it.
