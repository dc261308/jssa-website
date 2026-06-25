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
import re
import threading
import urllib.request

from flask import (
    Flask, render_template, jsonify, abort,
    request, redirect, url_for, session,
)

import sheets

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


@app.after_request
def _revalidate_html(resp):
    """Tell browsers to always check for the freshest page (HTML only), so a
    deploy is seen immediately instead of a stale cached copy. An ETag keeps it
    cheap: unchanged pages return a tiny 304, not the whole page again. Images,
    CSS and other assets keep their own caching."""
    try:
        if (request.method == "GET" and resp.status_code == 200
                and resp.headers.get("Content-Type", "").startswith("text/html")):
            resp.headers["Cache-Control"] = "no-cache"
            resp.add_etag()
            resp.make_conditional(request)
    except Exception:
        pass
    return resp


# ----------------------------------------------------------------------------
# Public site
# ----------------------------------------------------------------------------
@app.route("/")
def home():
    try:
        notice = sheets.active_notice()
    except Exception:
        notice = None
    # The red "Game Day Rosters Are Posted" button. A manual switch on the
    # Website Controls tab can force it ON or OFF; otherwise (AUTO) it follows
    # the published-rosters + noon-timer logic in game_day_teams().
    try:
        mode = sheets.roster_button_mode()  # 'ON' / 'OFF' / 'AUTO'
        if mode == "OFF":
            teams_posted = False
        elif mode == "ON":
            teams_posted = True
        else:
            teams_posted = sheets.game_day_teams() is not None
    except Exception:
        teams_posted = False
    try:
        blackboard = sheets.blackboard_posts()
    except Exception:
        blackboard = []
    try:
        board = sheets.board_members()
    except Exception:
        board = []
    try:
        sponsor_list = sheets.sponsors()
    except Exception:
        sponsor_list = []
    try:
        pred_odds = sheets.prediction_odds()
    except Exception:
        pred_odds = []
    try:
        pred_board = sheets.prediction_leaderboard(5)
    except Exception:
        pred_board = []
    try:
        pred_stats = sheets.prediction_analytics()
    except Exception:
        pred_stats = {}
    # League accuracy = % of all member picks correct — same number the
    # leaderboard shows, so the homepage and leaderboard always agree.
    try:
        league_acc = sheets.prediction_league_accuracy()
    except Exception:
        league_acc = None
    # Count this homepage visit and read the running total to show in the footer.
    try:
        sheets.record_home_view()
        views = sheets.home_view_count()
    except Exception:
        views = None
    return render_template("index.html", notice=notice,
                           teams_posted=teams_posted, blackboard=blackboard,
                           board=board, sponsors=sponsor_list,
                           pred_odds=pred_odds, pred_board=pred_board,
                           pred_stats=pred_stats, league_acc=league_acc,
                           views=views)


@app.route("/pickup")
def pickup():
    """Live preliminary roster for the next pickup game — who's signed up so
    far, grouped by division, with a countdown to the signup deadline."""
    try:
        game = sheets.pickup_next_game()
    except Exception:
        game = None
    return render_template("pages/pickup.html",
                           page_title="Next Pickup Game", game=game)


@app.route("/teams")
def teams():
    # The Website Controls "Game Day Button = OFF" switch fully un-publishes:
    # it hides the homepage button AND makes this page read "not posted yet",
    # so a finished test doesn't leave teams on the site.
    try:
        data = None if sheets.roster_button_mode() == "OFF" else sheets.game_day_teams()
    except Exception:
        data = None
    return render_template("teams.html", teams=data)


@app.route("/teams/debug")
def teams_debug():
    """Temporary: shows raw sheet rows so we can diagnose parsing issues."""
    import json
    try:
        ws = sheets._teams_worksheet()
        rows = ws.get_all_values()
        return "<pre>" + json.dumps(rows[:10], indent=2) + "</pre>"
    except Exception as e:
        return "<pre>ERROR: " + str(e) + "</pre>"


@app.route("/league/debug")
def league_debug():
    """Temporary diagnostic for the league (Control Sheet) data: lists every tab
    with its header row (headers only, no data) plus what league_season() parsed.
    Helps spot a mis-named tab or a clobbered header row. Remove once verified."""
    out = {}
    try:
        out["service_account_email"] = sheets.service_account_email()
    except Exception:
        pass
    try:
        sh = sheets._control_sheet(readonly=True)
        tabs = sheets._control_tabs(sh)
        out["tabs"] = [{"title": t, "rows": len(v),
                        "header": [str(c) for c in (v[0] if v else [])]}
                       for t, v in tabs]
    except Exception as e:
        out["tabs_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        sheets._season_cache["ts"] = 0.0   # force a fresh read, ignore the cache
        s = sheets.league_season()
        out["parsed"] = {
            "schedule": len(s["schedule"]),
            "results": len(s["results"]),
            "rosters": {k: len(v) for k, v in s["rosters"].items()},
            "standings": {k: len(v) for k, v in s["standings"].items()},
        }
    except Exception as e:
        out["parsed_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        sheets._profiles_cache["ts"] = 0.0   # force a fresh read
        profs = sheets.player_profiles()
        out["profiles"] = {
            "count": len(profs),
            "slugs": sorted(profs.keys()),
            "with_photo": sorted(s for s, p in profs.items() if p.get("photo_id")),
        }
    except Exception as e:
        out["profiles_error"] = "%s: %s" % (type(e).__name__, e)
    # Can the service account actually READ each submitted photo? 200 = yes
    # (and shows the type); 403/404 = the upload folder isn't shared with it.
    try:
        import requests
        creds = sheets._drive_creds()
        checks = []
        for slug, p in sheets.player_profiles().items():
            pid = p.get("photo_id")
            if not pid:
                continue
            m = requests.get("https://www.googleapis.com/drive/v3/files/%s" % pid,
                             params={"fields": "name,mimeType", "supportsAllDrives": "true"},
                             headers={"Authorization": "Bearer " + creds.token},
                             timeout=10)
            info = {"slug": slug, "drive_status": m.status_code}
            if m.status_code == 200:
                info["mime"] = m.json().get("mimeType")
            checks.append(info)
        out["photo_check"] = checks
    except Exception as e:
        out["photo_check_error"] = "%s: %s" % (type(e).__name__, e)
    return jsonify(out)


@app.route("/league/player/<slug>")
def player_profile(slug):
    try:
        player = sheets.player_cards().get(slug)
    except Exception:
        player = None
    if not player:
        abort(404)
    return render_template("pages/player-profile.html",
                           page_title=player["name"], player=player)


_PROFILE_PHOTO_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


@app.route("/league/player/photo/<file_id>")
def player_photo(file_id):
    if not _PROFILE_PHOTO_RE.match(file_id):
        abort(404)
    try:
        data, ctype = sheets.fetch_profile_photo_bytes(file_id)
    except Exception:
        data, ctype = None, None
    if not data:
        abort(404)
    from flask import Response
    resp = Response(data, mimetype=ctype or "image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/teams/preview")
def teams_preview():
    """Design preview using sample data — remove this route before next season."""
    data = {
        "date": "Monday, June 23, 2026",
        "park": "Maplewood Park",
        "info": "BP Starts at 8:30 AM",
        "total_players": 41,
        "fields": [
            {
                "name": "Maplewood West — 9:00 AM",
                "home": [
                    {"name": "Harold Fravel",   "pos": "OF",      "captain": True},
                    {"name": "Lewie Bergman",   "pos": "2B, MF",  "captain": False},
                    {"name": "Mike Clementi",   "pos": "3B, OF",  "captain": False},
                    {"name": "Tom Cosentino",   "pos": "SS, MF",  "captain": False},
                    {"name": "Steven Klein",    "pos": "IF, OF",  "captain": False},
                    {"name": "Steve Kurman",    "pos": "P",       "captain": False},
                    {"name": "Joe Mercurio",    "pos": "OF, IF",  "captain": False},
                    {"name": "Antonio Papa",    "pos": "OF, IF",  "captain": False},
                    {"name": "Dave Pelsor",     "pos": "P",       "captain": False},
                    {"name": "Vic Troiano",     "pos": "P, 2B",   "captain": False},
                ],
                "visitor": [
                    {"name": "Steve Gibelli",     "pos": "IF",      "captain": True},
                    {"name": "John Berilla",      "pos": "OF, IF",  "captain": False},
                    {"name": "John Cariero",      "pos": "OF",      "captain": False},
                    {"name": "Norm Falick",       "pos": "2B, MF",  "captain": False},
                    {"name": "Jorge Garcia",      "pos": "1B, 2B",  "captain": False},
                    {"name": "Gilbert Morejon",   "pos": "SS, MF",  "captain": False},
                    {"name": "Chris Okolichany",  "pos": "OF, P",   "captain": False},
                    {"name": "Mick Sipula",       "pos": "OF, IF",  "captain": False},
                    {"name": "Don Spieller",      "pos": "OF",      "captain": False},
                    {"name": "John Sullivan",     "pos": "P, OF",   "captain": False},
                    {"name": "Steve Tanis",       "pos": "OF, IF",  "captain": False},
                ],
            },
            {
                "name": "Maplewood East — 9:00 AM",
                "home": [
                    {"name": "Chase St James",  "pos": "P, IF",   "captain": True},
                    {"name": "Joe Baldwin",     "pos": "OF",      "captain": False},
                    {"name": "John Buckman",    "pos": "1B, P",   "captain": False},
                    {"name": "Robert Davis",    "pos": "P, 1B",   "captain": False},
                    {"name": "Sid Dinerstein",  "pos": "OF, IF",  "captain": False},
                    {"name": "Ken Mair",        "pos": "SS, OF",  "captain": False},
                    {"name": "Jeff McCrave",    "pos": "OF",      "captain": False},
                    {"name": "Mike Richmond",   "pos": "OF",      "captain": False},
                    {"name": "Barry Skolnik",   "pos": "P",       "captain": False},
                    {"name": "Dick Wendling",   "pos": "IF, OF",  "captain": False},
                ],
                "visitor": [
                    {"name": "Arnold Jungkind",   "pos": "OF, 1B, P", "captain": True},
                    {"name": "Allen Adams",       "pos": "OF, IF",    "captain": False},
                    {"name": "Doug Adams",        "pos": "OF, 1B",    "captain": False},
                    {"name": "Jeff Barron",       "pos": "IF",        "captain": False},
                    {"name": "Norm Haltrich",     "pos": "OF",        "captain": False},
                    {"name": "Scott Johnson",     "pos": "2B, MF",    "captain": False},
                    {"name": "Mike McClanahan",   "pos": "P",         "captain": False},
                    {"name": "Ralph Randazzo",    "pos": "OF",        "captain": False},
                    {"name": "Ricky Steckler",    "pos": "IF",        "captain": False},
                    {"name": "Paul Straubinger",  "pos": "P, IF, OF", "captain": False},
                ],
            },
        ],
    }
    return render_template("teams.html", teams=data)


@app.route("/players")
def players():
    try:
        rosters = sheets.division_rosters()
    except Exception:
        rosters = {"RED": [], "WHITE": [], "BLUE": []}
    return render_template("pages/players.html",
                           page_title="Players by Division", rosters=rosters)


# Organized-league sections (fall/winter season, ~mid-Oct through March).
# Each will read its data from the league Google Sheet once that's set up; until
# then they show a friendly "season runs October-March" placeholder so the page
# never looks broken or empty. Tuple is (page title, short eyebrow label).
LEAGUE_SECTIONS = {
    "teams":     ("Teams by Division", "Organized League"),
    "rosters":   ("Team Rosters",      "Organized League"),
    "schedules": ("League Schedule",  "Organized League"),
    "results":   ("Game Results",     "Organized League"),
    "standings": ("League Standings", "Organized League"),
}


@app.route("/league/<section>")
def league_section(section):
    meta = LEAGUE_SECTIONS.get(section)
    if meta is None:
        abort(404)
    title, eyebrow = meta
    try:
        season = sheets.league_season()
    except Exception:
        season = {"standings": {"RED": [], "WHITE": [], "BLUE": []},
                  "schedule": [], "results": [],
                  "rosters": {"RED": [], "WHITE": [], "BLUE": []}}
    # Does this section have any data to show yet? If not, the template falls
    # back to the friendly "off-season" message.
    has_data = {
        "rosters":   any(season["rosters"].values()),
        "schedules": bool(season["schedule"]),
        "results":   bool(season["results"]),
        "standings": any(season["standings"].values()),
        "teams":     any(season["rosters"].values()),
    }.get(section, False)
    template = "pages/league-section.html"
    section_template = {
        "teams":     "pages/league-teams.html",
        "rosters":   "pages/league-rosters.html",
        "schedules": "pages/league-schedule.html",
        "results":   "pages/league-results.html",
        "standings": "pages/league-standings.html",
    }.get(section)
    if section_template and has_data:
        template = section_template
    return render_template(template,
                           page_title=title, section_eyebrow=eyebrow,
                           section=section, season=season)


@app.route("/healthz")
def healthz():
    return jsonify(status="ok")


_PHOTO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


@app.route("/highlights/photo/<file_id>")
def highlights_photo(file_id):
    if not _PHOTO_ID_RE.match(file_id):
        abort(404)
    try:
        data, ctype = sheets.fetch_photo_bytes(file_id)
    except Exception:
        data, ctype = None, None
    if not data:
        abort(404)
    from flask import Response
    resp = Response(data, mimetype=ctype or "image/jpeg")
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp


@app.route("/highlights/debug")
def highlights_debug():
    """Read-only check of what the photo upload folder actually contains —
    counts and file types, no ids or names. Handy when uploads aren't showing."""
    try:
        return jsonify(sheets.highlights_debug())
    except Exception as e:
        return jsonify(error=type(e).__name__), 500


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
    ctx = {"page_title": title}
    if slug == "in-memoriam":
        try:
            ctx["entries"] = sheets.in_memoriam_entries()
        except Exception:
            ctx["entries"] = []
    elif slug == "hall-of-fame":
        try:
            ctx["inductees"] = sheets.hof_entries()
        except Exception:
            ctx["inductees"] = []
    return render_template(f"pages/{slug}.html", **ctx)


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
    try:
        views = sheets.home_view_count()
    except Exception:
        views = None
    return render_template("admin/dashboard.html",
                           configured=configured, notices=notices,
                           error=error, views=views)


@app.route("/admin/scores")
@login_required
def admin_scores():
    try:
        games = sheets.schedule_games_for_scoring()
    except Exception:
        games = []
    return render_template("admin/scores.html", games=games,
                           saved=request.args.get("saved"))


@app.route("/admin/scores/save", methods=["POST"])
@login_required
def admin_scores_save():
    row = request.form.get("row", "").strip()
    score_home = request.form.get("score_home", "").strip()
    score_away = request.form.get("score_away", "").strip()
    ok = False
    if row.isdigit() and score_home != "" and score_away != "":
        try:
            ok = sheets.set_game_score(int(row), score_home, score_away)
        except Exception:
            ok = False
    return redirect(url_for("admin_scores", saved=("1" if ok else "0")))


@app.route("/admin/predictions")
@login_required
def admin_predictions():
    try:
        games = sheets.prediction_games_for_scoring()
    except Exception:
        games = []
    return render_template("admin/predictions.html", games=games,
                           saved=request.args.get("saved"))


_APPS_SCRIPT_SCORE_URL = (
    "https://script.google.com/macros/s/AKfycbwqXbN6B6WNa7Dye3NJcUWzmNrMETCZWjW2F8Jr"
    "jmhKb7F3idebOxiBeRm1Fpzpx1ij/exec"
    "?action=score_predictions&key=JSSA_PREDICTION_SYNC_2026"
)


def _trigger_scoring():
    try:
        urllib.request.urlopen(_APPS_SCRIPT_SCORE_URL, timeout=30)
    except Exception:
        pass


@app.route("/admin/predictions/save", methods=["POST"])
@login_required
def admin_predictions_save():
    row = request.form.get("row", "").strip()
    winner = request.form.get("winner", "").strip().upper()[:1]
    ok = False
    if row.isdigit() and winner in ("H", "V", "T"):
        try:
            ok = sheets.set_prediction_winner(int(row), winner)
        except Exception:
            ok = False
    if ok:
        threading.Thread(target=_trigger_scoring, daemon=True).start()
    return redirect(url_for("admin_predictions", saved=("1" if ok else "0")))


@app.route("/admin/directory")
@login_required
def admin_directory():
    try:
        directory = sheets.player_directory()
    except Exception:
        directory = {"headers": [], "players": []}
    return render_template("admin/directory.html", directory=directory)


@app.route("/admin/communications")
@login_required
def admin_communications():
    try:
        audiences = sheets.comm_audiences()
    except Exception:
        audiences = {"all": [], "divisions": {"RED": [], "WHITE": [], "BLUE": []},
                     "board": []}
    return render_template("admin/communications.html", audiences=audiences)


@app.route("/admin/notices/add", methods=["POST"])
@login_required
def admin_add():
    ntype = request.form.get("type", "announcement")
    message = request.form.get("message", "").strip()
    created_by = request.form.get("created_by", "").strip()
    url = request.form.get("url", "").strip()
    link_text = request.form.get("link_text", "").strip()
    if message:
        try:
            sheets.add_notice(ntype, message, created_by, url, link_text)
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


@app.route("/admin/notices/<nid>/edit", methods=["POST"])
@login_required
def admin_edit(nid):
    message = request.form.get("message", "").strip()
    if message:
        try:
            sheets.update_notice(nid, message)
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



# ----------------------------------------------------------------------------
# Blackboard admin
# ----------------------------------------------------------------------------
@app.route("/admin/blackboard")
@login_required
def admin_blackboard():
    configured = sheets.is_configured()
    posts = []
    error = None
    if configured:
        try:
            posts = sheets.list_bb_posts()
        except Exception as e:
            error = str(e)
    editing = None
    edit_id = request.args.get("edit")
    if edit_id:
        for p in posts:
            if str(p.get("id")) == str(edit_id):
                editing = p
                break
    return render_template("admin/blackboard.html",
                           configured=configured, posts=posts,
                           error=error, editing=editing)


@app.route("/admin/blackboard/add", methods=["POST"])
@login_required
def admin_bb_add():
    try:
        sheets.add_bb_post(request.form)
    except Exception:
        pass
    return redirect(url_for("admin_blackboard"))


@app.route("/admin/blackboard/<bid>/update", methods=["POST"])
@login_required
def admin_bb_update(bid):
    try:
        sheets.update_bb_post(bid, request.form)
    except Exception:
        pass
    return redirect(url_for("admin_blackboard"))


@app.route("/admin/blackboard/<bid>/toggle", methods=["POST"])
@login_required
def admin_bb_toggle(bid):
    active = request.form.get("active") == "1"
    try:
        sheets.set_bb_active(bid, active)
    except Exception:
        pass
    return redirect(url_for("admin_blackboard"))


@app.route("/admin/blackboard/<bid>/delete", methods=["POST"])
@login_required
def admin_bb_delete(bid):
    try:
        sheets.delete_bb_post(bid)
    except Exception:
        pass
    return redirect(url_for("admin_blackboard"))



# ----------------------------------------------------------------------------
# In Memoriam admin
# ----------------------------------------------------------------------------
@app.route("/admin/memoriam")
@login_required
def admin_memoriam():
    configured = sheets.is_configured()
    entries, error = [], None
    if configured:
        try:
            entries = sheets.list_mem_entries()
        except Exception as e:
            error = str(e)
    editing = None
    edit_id = request.args.get("edit")
    if edit_id:
        for e in entries:
            if str(e.get("id")) == str(edit_id):
                editing = e
                break
    return render_template("admin/manage-memoriam.html",
                           configured=configured, entries=entries,
                           error=error, editing=editing)


@app.route("/admin/memoriam/add", methods=["POST"])
@login_required
def admin_mem_add():
    try:
        sheets.add_mem_entry(request.form)
    except Exception:
        pass
    return redirect(url_for("admin_memoriam"))


@app.route("/admin/memoriam/<eid>/update", methods=["POST"])
@login_required
def admin_mem_update(eid):
    try:
        sheets.update_mem_entry(eid, request.form)
    except Exception:
        pass
    return redirect(url_for("admin_memoriam"))


@app.route("/admin/memoriam/<eid>/toggle", methods=["POST"])
@login_required
def admin_mem_toggle(eid):
    active = request.form.get("active") == "1"
    try:
        sheets.set_mem_active(eid, active)
    except Exception:
        pass
    return redirect(url_for("admin_memoriam"))


@app.route("/admin/memoriam/<eid>/delete", methods=["POST"])
@login_required
def admin_mem_delete(eid):
    try:
        sheets.delete_mem_entry(eid)
    except Exception:
        pass
    return redirect(url_for("admin_memoriam"))


# ----------------------------------------------------------------------------
# Hall of Fame admin
# ----------------------------------------------------------------------------
@app.route("/admin/hof")
@login_required
def admin_hof():
    configured = sheets.is_configured()
    entries, error = [], None
    if configured:
        try:
            entries = sheets.list_hof_entries()
        except Exception as e:
            error = str(e)
    editing = None
    edit_id = request.args.get("edit")
    if edit_id:
        for e in entries:
            if str(e.get("id")) == str(edit_id):
                editing = e
                break
    return render_template("admin/manage-hof.html",
                           configured=configured, entries=entries,
                           error=error, editing=editing)


@app.route("/admin/hof/add", methods=["POST"])
@login_required
def admin_hof_add():
    try:
        sheets.add_hof_entry(request.form)
    except Exception:
        pass
    return redirect(url_for("admin_hof"))


@app.route("/admin/hof/<eid>/update", methods=["POST"])
@login_required
def admin_hof_update(eid):
    try:
        sheets.update_hof_entry(eid, request.form)
    except Exception:
        pass
    return redirect(url_for("admin_hof"))


@app.route("/admin/hof/<eid>/toggle", methods=["POST"])
@login_required
def admin_hof_toggle(eid):
    active = request.form.get("active") == "1"
    try:
        sheets.set_hof_active(eid, active)
    except Exception:
        pass
    return redirect(url_for("admin_hof"))


@app.route("/admin/hof/<eid>/delete", methods=["POST"])
@login_required
def admin_hof_delete(eid):
    try:
        sheets.delete_hof_entry(eid)
    except Exception:
        pass
    return redirect(url_for("admin_hof"))



# ----------------------------------------------------------------------------
# Board of Directors admin
# ----------------------------------------------------------------------------
@app.route("/admin/board")
@login_required
def admin_board():
    configured = sheets.is_configured()
    members, error = [], None
    if configured:
        try:
            members = sheets.list_board_members()
        except Exception as e:
            error = str(e)
    editing = None
    edit_id = request.args.get("edit")
    if edit_id:
        for m in members:
            if str(m.get("id")) == str(edit_id):
                editing = m
                break
    return render_template("admin/manage-board.html",
                           configured=configured, members=members,
                           error=error, editing=editing)


@app.route("/admin/board/add", methods=["POST"])
@login_required
def admin_board_add():
    try:
        sheets.add_board_member(request.form)
    except Exception:
        pass
    return redirect(url_for("admin_board"))


@app.route("/admin/board/<mid>/update", methods=["POST"])
@login_required
def admin_board_update(mid):
    try:
        sheets.update_board_member(mid, request.form)
    except Exception:
        pass
    return redirect(url_for("admin_board"))


@app.route("/admin/board/<mid>/toggle", methods=["POST"])
@login_required
def admin_board_toggle(mid):
    active = request.form.get("active") == "1"
    try:
        sheets.set_board_active(mid, active)
    except Exception:
        pass
    return redirect(url_for("admin_board"))


@app.route("/admin/board/<mid>/delete", methods=["POST"])
@login_required
def admin_board_delete(mid):
    try:
        sheets.delete_board_member(mid)
    except Exception:
        pass
    return redirect(url_for("admin_board"))



# ----------------------------------------------------------------------------
# Sponsors admin
# ----------------------------------------------------------------------------
@app.route("/admin/sponsors")
@login_required
def admin_sponsors():
    configured = sheets.is_configured()
    items, error = [], None
    if configured:
        try:
            items = sheets.list_sponsors()
        except Exception as e:
            error = str(e)
    editing = None
    edit_id = request.args.get("edit")
    if edit_id:
        for s in items:
            if str(s.get("id")) == str(edit_id):
                editing = s
                break
    return render_template("admin/manage-sponsors.html",
                           configured=configured, items=items,
                           error=error, editing=editing)


@app.route("/admin/sponsors/add", methods=["POST"])
@login_required
def admin_sponsor_add():
    try:
        sheets.add_sponsor(request.form)
    except Exception:
        pass
    return redirect(url_for("admin_sponsors"))


@app.route("/admin/sponsors/<sid>/update", methods=["POST"])
@login_required
def admin_sponsor_update(sid):
    try:
        sheets.update_sponsor(sid, request.form)
    except Exception:
        pass
    return redirect(url_for("admin_sponsors"))


@app.route("/admin/sponsors/<sid>/toggle", methods=["POST"])
@login_required
def admin_sponsor_toggle(sid):
    active = request.form.get("active") == "1"
    try:
        sheets.set_sponsor_active(sid, active)
    except Exception:
        pass
    return redirect(url_for("admin_sponsors"))


@app.route("/admin/sponsors/<sid>/delete", methods=["POST"])
@login_required
def admin_sponsor_delete(sid):
    try:
        sheets.delete_sponsor(sid)
    except Exception:
        pass
    return redirect(url_for("admin_sponsors"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
