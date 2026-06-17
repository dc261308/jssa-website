"""
Jupiter Senior Softball Association — website
---------------------------------------------
Public site + a simple admin area for posting game-day weather/cancellation
updates and announcements, which drive the live banner on the homepage.

Admin content is stored in a dedicated "JSSA Website Content" Google Sheet via
a service account (see sheets.py). Everything degrades gracefully if that isn't
configured yet: the public site runs normally and /admin shows a setup notice.

Run locally:
    pip install -r requirements.txt
    python app.py            # http://localhost:5000

Production (Render uses render.yaml):
    gunicorn app:app

Environment variables (set in Render):
    SECRET_KEY                   — random string for signing sessions
    ADMIN_PASSWORD               — shared password for the admin area
    GOOGLE_SERVICE_ACCOUNT_JSON  — full service-account key JSON
    SHEET_ID                     — id of the JSSA Website Content sheet
"""

import os
import hmac
import functools

from flask import (
    Flask, render_template, jsonify, abort,
    request, redirect, url_for, session,
)

import sheets

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


# ----------------------------------------------------------------------------
# Public site
# ----------------------------------------------------------------------------
@app.route("/")
def home():
    try:
        notice = sheets.active_notice()
    except Exception:
        notice = None
    return render_template("index.html", notice=notice)


@app.route("/healthz")
def healthz():
    return jsonify(status="ok")


# Interior content pages (rebuilt natively from the league's Google Docs).
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


# ----------------------------------------------------------------------------
# Admin area
# ----------------------------------------------------------------------------
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        pw = request.form.get("password", "")
        if ADMIN_PASSWORD and hmac.compare_digest(pw, ADMIN_PASSWORD):
            session["admin"] = True
            dest = request.args.get("next") or url_for("admin_dashboard")
            return redirect(dest)
        error = "Incorrect password."
    return render_template("admin/login.html",
                           error=error,
                           configured=bool(ADMIN_PASSWORD))


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
@login_required
def admin_dashboard():
    configured = sheets.is_configured()
    notices = []
    error = None
    if configured:
        try:
            notices = sheets.list_notices()
        except Exception as e:
            error = str(e)
    return render_template("admin/dashboard.html",
                           configured=configured, notices=notices, error=error)


@app.route("/admin/notices/add", methods=["POST"])
@login_required
def admin_add():
    ntype = request.form.get("type", "announcement")
    message = request.form.get("message", "").strip()
    created_by = request.form.get("created_by", "").strip()
    if message:
        try:
            sheets.add_notice(ntype, message, created_by)
        except Exception:
            pass
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/notices/<nid>/toggle", methods=["POST"])
@login_required
def admin_toggle(nid):
    active = request.form.get("active") == "1"
    try:
        sheets.set_active(nid, active)
    except Exception:
        pass
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/notices/<nid>/delete", methods=["POST"])
@login_required
def admin_delete(nid):
    try:
        sheets.delete_notice(nid)
    except Exception:
        pass
    return redirect(url_for("admin_dashboard"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
