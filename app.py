"""
Jupiter Senior Softball Association — website
---------------------------------------------
Phase 1: serves the homepage (content currently baked into the template).
Phase 2 (planned): read live content from the league Google Sheet and the
admin write-actions used by the Claude connector. Hooks are marked TODO below.

Run locally:
    pip install -r requirements.txt
    python app.py
    # open http://localhost:5000

Run in production (Render uses this via render.yaml):
    gunicorn app:app
"""

import os
from flask import Flask, render_template, jsonify, abort

app = Flask(__name__)


@app.route("/")
def home():
    # Phase 2 TODO: pull live values from the Google Sheet (Site Settings,
    # Blackboard, Sponsors, Breaking News, Game Day Notice) via a read-only
    # service account and pass them into the template as context.
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    """Lightweight health check for Render."""
    return jsonify(status="ok")


# Interior content pages (rebuilt natively from the league's Google Docs).
# slug -> page title. Each has a matching template in templates/pages/<slug>.html
PAGES = {
    "bylaws":          "JSSA Bylaws",
    "playing-rules":   "Playing Rules",
    "code-of-conduct": "Code of Conduct",
    "hall-of-fame":    "Hall of Fame",
    "pickup-games":    "Pickup Games Explained",
    "in-memoriam":     "In Memoriam",
}


@app.route("/<slug>")
def page(slug):
    title = PAGES.get(slug)
    if title is None:
        abort(404)
    return render_template(f"pages/{slug}.html", page_title=title)


if __name__ == "__main__":
    # Render provides PORT; default to 5000 for local dev.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
