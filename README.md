# Jupiter Senior Softball Association — Website

A clean, mobile-friendly rebuild of the JSSA homepage. Flask app, deployed on
Render from GitHub. Built to be easy for a non-technical admin to maintain by
chatting with Claude (Phase 2).

## Project structure

```
jssa-website/
├── app.py                 # Flask app (serves the homepage)
├── requirements.txt       # Flask + gunicorn
├── render.yaml            # Render Blueprint (one-click deploy config)
├── Procfile               # fallback start command
├── templates/
│   └── index.html         # the homepage (self-contained: CSS + JS inline)
├── static/
│   ├── logos/             # sponsor logos (populated by tools/fetch_logos.py)
│   └── img/               # other images
└── tools/
    └── fetch_logos.py     # one-time: pull sponsor logos from Drive
```

## Run it locally

```bash
pip install -r requirements.txt
python app.py
# open http://localhost:5000
```

## Sponsor logos (do this once)

The sponsor logos live in the league's Google Drive. Pull them into the repo so
they render reliably everywhere:

```bash
python tools/fetch_logos.py
git add static/logos
git commit -m "Add sponsor logos"
git push
```

Any sponsor without a Drive logo falls back automatically to its brand mark
(by website domain) and then to a clean name card — nothing breaks.

## Deploy to Render

1. Create a new GitHub repo and push this folder to it:
   ```bash
   git init
   git add .
   git commit -m "Initial JSSA site"
   git branch -M main
   git remote add origin https://github.com/<your-username>/jssa-website.git
   git push -u origin main
   ```
2. In Render: **New → Blueprint**, connect the GitHub repo. Render reads
   `render.yaml` and stands up the web service on the free plan.
3. First deploy takes a couple minutes. After that, every `git push` to `main`
   auto-deploys.

Health check lives at `/healthz`.

## Roadmap

**Phase 1 (this repo).** Beautiful read-only homepage. Content is currently
baked into `templates/index.html` (board, sponsors, predictions, events, links).
Buttons link out to the existing Google Apps Script features (signups, prediction
contest, docs) so nothing the league relies on is lost.

**Phase 2 (planned).**
- Read live content from the league Google Sheet (Site Settings, Blackboard,
  Sponsors, Breaking News, Game Day Notice) via a read-only service account, so
  edits to the sheet show up on the site automatically. Hook is marked `TODO`
  in `app.py`.
- A small set of safe write-actions (post a rainout banner, update a notice,
  toggle a sponsor) exposed two ways: an on-site admin form, and a Claude
  connector so the admin can just chat ("post a rainout banner for Saturday").
- Native rebuild of the prediction contest.

## Notes

- The JSSA lighthouse logo is embedded directly in the page, so it always loads.
- Do not commit the Google Sheet's service-account credentials. When Phase 2
  lands, those go in Render environment variables, never in the repo.
