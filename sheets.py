"""
sheets.py — read/write the website's content sheet via a Google service account.

Design notes
------------
* Points at a DEDICATED "JSSA Website Content" spreadsheet (SHEET_ID), NOT the
  league's member sheet. The service account therefore never has access to
  member emails or passwords — only website content.
* All website notices live on one tab (WebsiteNotices), auto-created if missing.
* The homepage's active-notice lookup is cached briefly so we don't hit the
  Google API on every page view.
* Everything degrades gracefully: if the service account / sheet id aren't
  configured yet, is_configured() returns False and the public site runs
  normally with no dynamic banner.

Required environment variables (set in Render):
    GOOGLE_SERVICE_ACCOUNT_JSON  — full JSON of the service-account key
    SHEET_ID                     — id of the "JSSA Website Content" spreadsheet
"""

import os
import json
import time
import uuid
import datetime
import threading
import re

SHEET_ID = os.environ.get("SHEET_ID", "").strip()
_SA_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

NOTICES_TAB = "WebsiteNotices"
HEADERS = ["id", "type", "message", "active", "created_by", "created_at", "url", "link_text"]

_CACHE_TTL = 300  # seconds (5 min — reduces API quota pressure)
_cache = {"notice": None, "ts": 0.0}
_lock = threading.Lock()


def is_configured():
    """True only when both the service account and sheet id are present."""
    return bool(SHEET_ID and _SA_JSON)


def _worksheet():
    """Return the WebsiteNotices worksheet, creating it (with headers) if needed."""
    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(_SA_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(NOTICES_TAB)
        _ensure_headers(ws, HEADERS)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=NOTICES_TAB, rows=200, cols=len(HEADERS))
        ws.update([HEADERS], "A1")
    return ws


def _ensure_headers(ws, headers):
    """Make sure the sheet's header row contains every column we expect.
    Any missing columns (e.g. a newly added 'url') are appended on the end so
    older sheets keep working without manual editing."""
    try:
        existing = ws.row_values(1)
    except Exception:
        existing = []
    missing = [h for h in headers if h not in existing]
    if not existing:
        ws.update([headers], "A1")
    elif missing:
        ws.update([existing + missing], "A1")


def _is_true(value):
    return str(value).strip().upper() in ("TRUE", "1", "YES", "Y")


def list_notices():
    """All notices, newest first. Returns a list of dicts."""
    if not is_configured():
        return []
    ws = _worksheet()
    records = ws.get_all_records(expected_headers=HEADERS)
    return list(reversed(records))


def active_notice():
    """
    The single notice to show in the site banner, or None.
    Weather/cancellation notices take priority over announcements.
    Cached for _CACHE_TTL seconds; last good value is kept on error.
    """
    now = time.time()
    with _lock:
        if now - _cache["ts"] < _CACHE_TTL:
            return _cache["notice"]

    notice = None
    try:
        if is_configured():
            actives = [r for r in list_notices() if _is_true(r.get("active"))]
            weather = [r for r in actives if str(r.get("type")) == "weather"]
            chosen = (weather or actives)[0] if actives else None
            if chosen:
                notice = {
                    "type": str(chosen.get("type") or "announcement"),
                    "message": str(chosen.get("message") or ""),
                    "url": str(chosen.get("url") or "").strip(),
                    "link_text": str(chosen.get("link_text") or "").strip(),
                }
        with _lock:
            _cache["notice"] = notice
            _cache["ts"] = now
        return notice
    except Exception:
        # On any API hiccup, keep showing the last known value rather than break.
        return _cache["notice"]


def _invalidate():
    with _lock:
        _cache["ts"] = 0.0


def add_notice(ntype, message, created_by, url="", link_text=""):
    ntype = "weather" if ntype == "weather" else "announcement"
    row = [
        uuid.uuid4().hex[:8],
        ntype,
        message,
        "TRUE",
        created_by or "Admin",
        datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        url.strip() if url else "",
        link_text.strip() if link_text else "",
    ]
    ws = _worksheet()
    ws.append_row(row, value_input_option="USER_ENTERED")
    _invalidate()


def set_active(notice_id, active):
    ws = _worksheet()
    records = ws.get_all_records(expected_headers=HEADERS)
    col = HEADERS.index("active") + 1
    for i, rec in enumerate(records):
        if str(rec.get("id")) == str(notice_id):
            ws.update_cell(i + 2, col, "TRUE" if active else "FALSE")
            break
    _invalidate()


def delete_notice(notice_id):
    ws = _worksheet()
    records = ws.get_all_records(expected_headers=HEADERS)
    for i, rec in enumerate(records):
        if str(rec.get("id")) == str(notice_id):
            ws.delete_rows(i + 2)
            break
    _invalidate()


def update_notice(notice_id, message):
    """Change the wording of an existing notice in place."""
    ws = _worksheet()
    records = ws.get_all_records(expected_headers=HEADERS)
    col = HEADERS.index("message") + 1
    for i, rec in enumerate(records):
        if str(rec.get("id")) == str(notice_id):
            ws.update_cell(i + 2, col, message)
            break
    _invalidate()


# ----------------------------------------------------------------------------
# Game Day Teams — read-only display of the league's PUBLIC teams sheet.
#
# Points at a SEPARATE, already-public spreadsheet ("JSSA Public Member View —
# Schedule & Teams"). We only ever read the roster block (names, positions,
# Home/Visitor, captain flags). We never read or display emails — the public
# sheet has none next to players, and member emails live in Tom's private
# back-end sheets that this app never touches.
# ----------------------------------------------------------------------------
TEAMS_SHEET_ID = os.environ.get(
    "TEAMS_SHEET_ID", "1oHgGae0aXVVsr7t9hmDmoLxZWO5p9rLFPebSsXoFfAA"
).strip()
TEAMS_TAB = os.environ.get("TEAMS_TAB", "Game_Day_Teams").strip()

# The master member list (Players by Division) lives in its own spreadsheet,
# separate from the game-day schedule sheet above. Defaults to the league's
# master roster sheet; override with ROSTER_SHEET_ID in Render if it ever moves.
ROSTER_SHEET_ID = os.environ.get(
    "ROSTER_SHEET_ID", "1YHKk8GLM9kqzSoWFxuUtFCH-B6crZ7SP5m4vogJVBwg"
).strip()

_teams_cache = {"data": None, "ts": 0.0}
_TEAMS_TTL = 300  # seconds


def teams_is_configured():
    return bool(TEAMS_SHEET_ID and _SA_JSON)


def _teams_worksheet():
    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(_SA_JSON)
    # Read-only scope is all we need for the public teams sheet.
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(TEAMS_SHEET_ID)
    try:
        return sh.worksheet(TEAMS_TAB)
    except Exception:
        # Fall back to the first tab if the name ever changes.
        return sh.get_worksheet(0)


def _clean(s):
    return str(s or "").strip()


def _is_field_label(text):
    """A column header that names a playing field/venue slot."""
    t = _clean(text).lower()
    return t.startswith("field") or "maplewood" in t


def _parse_marker(cell):
    """
    Turn a field-column cell into (side, is_captain) or None.
    Examples: 'H' -> ('Home', False); 'V Captain' -> ('Visitor', True).
    """
    t = _clean(cell)
    if not t:
        return None
    up = t.upper()
    side = None
    if up.startswith("H"):
        side = "Home"
    elif up.startswith("V"):
        side = "Visitor"
    if side is None:
        return None
    is_captain = "CAPTAIN" in up
    return side, is_captain


def _parse_teams_block(rows):
    """
    Parse the topmost game-day block from the worksheet's raw rows.
    Returns a structured dict or None.
    """
    # 1) Find the title row ("JSSA Game Day Teams").
    start = None
    for i, row in enumerate(rows):
        if row and "game day teams" in _clean(row[0]).lower():
            start = i
            break
    if start is None:
        return None

    # 2) Pull the human-readable date / park / info lines that follow the title,
    #    up until the counts row or the column-header row.
    date_str, park_str, info_str = "", "", ""
    header_idx = None
    field_cols = []  # (col_index, label)

    j = start + 1
    info_lines = []
    while j < len(rows):
        row = rows[j]
        first = _clean(row[0]) if row else ""
        low = first.lower()

        # Column header row: "Today's Players | Preferred Positions | Field ..."
        if low.startswith("today's player") or low.startswith("todays player"):
            header_idx = j
            for ci, cell in enumerate(row):
                if _is_field_label(cell):
                    field_cols.append((ci, _clean(cell)))
            break

        # Counts row like "48,16,0,16,16" — skip it.
        digits = [c for c in row if _clean(c)]
        if first and first.replace(",", "").isdigit():
            j += 1
            continue

        if first:
            info_lines.append(first)
        j += 1

    if header_idx is None or not field_cols:
        return None

    # Assign date / park / extra info from the collected lines.
    if len(info_lines) >= 1:
        date_str = info_lines[0]
    if len(info_lines) >= 2:
        park_str = info_lines[1]
    extras = [ln for ln in info_lines[2:] if ln]
    # Drop the prediction-game promo & "emails discontinued" reminder lines.
    extras = [
        e for e in extras
        if "prediction" not in e.lower() and "email" not in e.lower()
    ]
    info_str = " · ".join(extras)

    # 3) Build a field container per field column.
    fields = []
    col_to_field = {}
    for ci, label in field_cols:
        f = {"name": label, "home": [], "visitor": []}
        col_to_field[ci] = f
        fields.append(f)

    # 4) Walk player rows until a blank/terminating row.
    k = header_idx + 1
    while k < len(rows):
        row = rows[k]
        name = _clean(row[0]) if row else ""
        if not name:
            break
        # Stop if we hit the next block's title (shouldn't happen for topmost).
        if "game day teams" in name.lower():
            break

        positions = _clean(row[1]) if len(row) > 1 else ""

        placed = False
        for ci, fld in col_to_field.items():
            cell = row[ci] if len(row) > ci else ""
            parsed = _parse_marker(cell)
            if parsed:
                side, is_captain = parsed
                entry = {
                    "name": name,
                    "pos": positions,
                    "captain": is_captain,
                }
                (fld["home"] if side == "Home" else fld["visitor"]).append(entry)
                placed = True
                break
        k += 1
        if not placed:
            # Row had a name but no recognizable side marker; skip quietly.
            continue

    # Sort each side so captains come first, then alphabetical.
    for f in fields:
        for key in ("home", "visitor"):
            f[key].sort(key=lambda e: (not e["captain"], e["name"].lower()))

    total = sum(len(f["home"]) + len(f["visitor"]) for f in fields)

    return {
        "date": date_str,
        "park": park_str,
        "info": info_str,
        "fields": fields,
        "total_players": total,
    }


def game_day_teams():
    """
    Structured current-game roster, or None if unavailable.
    Cached briefly so we don't hit the Google API on every page view.
    """
    now = time.time()
    with _lock:
        if _teams_cache["data"] is not None and now - _teams_cache["ts"] < _TEAMS_TTL:
            return _teams_cache["data"]

    data = None
    try:
        if teams_is_configured():
            ws = _teams_worksheet()
            rows = ws.get_all_values()
            data = _parse_teams_block(rows)
        with _lock:
            _teams_cache["data"] = data
            _teams_cache["ts"] = now
        return data
    except Exception:
        # On any hiccup, return the last good value (may be None).
        return _teams_cache["data"]


# ----------------------------------------------------------------------------
# Blackboard posts (homepage "Blackboard" section)
# ----------------------------------------------------------------------------
# Lives on a "Blackboard" tab in the same JSSA Website Content sheet, auto-created
# if missing. Each row is one post. Edit the sheet -> the homepage updates; no
# code change or redeploy. If the tab is empty or the API hiccups, the homepage
# falls back to its built-in cards, so it never looks broken.

BLACKBOARD_TAB = "Blackboard"
BB_HEADERS = ["id", "when", "title", "body", "image",
              "link_url", "link_text", "sign", "side", "active", "order"]

_bb_cache = {"data": None, "ts": 0.0}

_DRIVE_RE = re.compile(r"(?:/d/|[?&]id=)([A-Za-z0-9_-]{25,})")


def _to_int(v):
    try:
        return int(str(v).strip() or 0)
    except Exception:
        return 0


def _img_url(s):
    """Normalize a sheet image value into something an <img> can load.
    Accepts a Google Drive share link, a bare Drive file id, a /static path,
    or any full http(s) URL. Returns '' for blank."""
    s = (s or "").strip()
    if not s:
        return ""
    if s.startswith("/"):
        return s
    low = s.lower()
    if ("drive.google" in low) or ("docs.google" in low):
        m = _DRIVE_RE.search(s)
        if m:
            return "https://lh3.googleusercontent.com/d/%s=w1000" % m.group(1)
        return s
    if low.startswith("http"):
        return s
    if re.fullmatch(r"[A-Za-z0-9_-]{25,}", s):
        return "https://lh3.googleusercontent.com/d/%s=w1000" % s
    return s


def _bb_worksheet():
    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(_SA_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(BLACKBOARD_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=BLACKBOARD_TAB, rows=100, cols=len(BB_HEADERS))
        ws.update([BB_HEADERS], "A1")
    return ws


def blackboard_posts():
    """Active Blackboard posts, ordered by the 'order' column (low to high).
    Cached for _CACHE_TTL seconds; last good value kept on error."""
    now = time.time()
    with _lock:
        if _bb_cache["data"] is not None and now - _bb_cache["ts"] < _CACHE_TTL:
            return _bb_cache["data"]

    try:
        posts = []
        if is_configured():
            ws = _bb_worksheet()
            rows = ws.get_all_records(expected_headers=BB_HEADERS)
            for r in rows:
                if not _is_true(r.get("active")):
                    continue
                title = str(r.get("title") or "").strip()
                body = str(r.get("body") or "").strip()
                if not (title or body):
                    continue
                posts.append({
                    "when": str(r.get("when") or "").strip(),
                    "title": title,
                    "body": body,
                    "image": _img_url(str(r.get("image") or "")),
                    "link_url": str(r.get("link_url") or "").strip(),
                    "link_text": str(r.get("link_text") or "").strip(),
                    "sign": str(r.get("sign") or "").strip(),
                    "side": (str(r.get("side") or "").strip().lower() or "left"),
                    "order": _to_int(r.get("order")),
                })
            posts.sort(key=lambda p: p["order"])
        with _lock:
            _bb_cache["data"] = posts
            _bb_cache["ts"] = now
        return posts
    except Exception:
        return _bb_cache["data"] or []


# ----------------------------------------------------------------------------
# Blackboard admin (manage posts from inside the site's /admin area)
# ----------------------------------------------------------------------------
def _bb_invalidate():
    with _lock:
        _bb_cache["ts"] = 0.0


def list_bb_posts():
    """All Blackboard posts (active and inactive), ordered. For the admin list."""
    if not is_configured():
        return []
    ws = _bb_worksheet()
    records = ws.get_all_records(expected_headers=BB_HEADERS)
    out = [{k: rec.get(k, "") for k in BB_HEADERS} for rec in records]
    out.sort(key=lambda r: _to_int(r.get("order")))
    return out


def _bb_next_order(records):
    mx = 0
    for r in records:
        mx = max(mx, _to_int(r.get("order")))
    return mx + 1


def _bb_clean_side(v):
    s = (v or "").strip().lower()
    return "right" if s == "right" else "left"


def add_bb_post(fields):
    ws = _bb_worksheet()
    records = ws.get_all_records(expected_headers=BB_HEADERS)
    order = fields.get("order")
    order = _to_int(order) if str(order or "").strip() else _bb_next_order(records)
    row = [
        uuid.uuid4().hex[:8],
        (fields.get("when") or "").strip(),
        (fields.get("title") or "").strip(),
        (fields.get("body") or "").strip(),
        (fields.get("image") or "").strip(),
        (fields.get("link_url") or "").strip(),
        (fields.get("link_text") or "").strip(),
        (fields.get("sign") or "").strip(),
        _bb_clean_side(fields.get("side")),
        "TRUE",
        order,
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    _bb_invalidate()


def update_bb_post(post_id, fields):
    ws = _bb_worksheet()
    records = ws.get_all_records(expected_headers=BB_HEADERS)
    for i, rec in enumerate(records):
        if str(rec.get("id")) == str(post_id):
            rownum = i + 2
            new_vals = {
                "when": (fields.get("when") or "").strip(),
                "title": (fields.get("title") or "").strip(),
                "body": (fields.get("body") or "").strip(),
                "image": (fields.get("image") or "").strip(),
                "link_url": (fields.get("link_url") or "").strip(),
                "link_text": (fields.get("link_text") or "").strip(),
                "sign": (fields.get("sign") or "").strip(),
                "side": _bb_clean_side(fields.get("side")),
            }
            if str(fields.get("order") or "").strip():
                new_vals["order"] = _to_int(fields.get("order"))
            for key, val in new_vals.items():
                ws.update_cell(rownum, BB_HEADERS.index(key) + 1, val)
            break
    _bb_invalidate()


def set_bb_active(post_id, active):
    ws = _bb_worksheet()
    records = ws.get_all_records(expected_headers=BB_HEADERS)
    col = BB_HEADERS.index("active") + 1
    for i, rec in enumerate(records):
        if str(rec.get("id")) == str(post_id):
            ws.update_cell(i + 2, col, "TRUE" if active else "FALSE")
            break
    _bb_invalidate()


def delete_bb_post(post_id):
    ws = _bb_worksheet()
    records = ws.get_all_records(expected_headers=BB_HEADERS)
    for i, rec in enumerate(records):
        if str(rec.get("id")) == str(post_id):
            ws.delete_rows(i + 2)
            break
    _bb_invalidate()


# ----------------------------------------------------------------------------
# Highlights photos — read the Google Form's upload folder via the service acct
# ----------------------------------------------------------------------------
# The form drops uploaded photos into a Drive folder. Share that folder (Viewer)
# with the service account, then set PHOTOS_FOLDER_ID. We list the newest images
# and serve their bytes through the app (the files stay private to Drive).

PHOTOS_FOLDER_ID = os.environ.get("PHOTOS_FOLDER_ID", "").strip()
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
_PHOTOS_TTL = 300
_photos_cache = {"ids": None, "ts": 0.0}


def photos_configured():
    return bool(PHOTOS_FOLDER_ID and _SA_JSON)


def _drive_creds():
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import Request
    info = json.loads(_SA_JSON)
    creds = Credentials.from_service_account_info(info, scopes=_DRIVE_SCOPES)
    creds.refresh(Request())
    return creds


def highlight_photo_ids(limit=12):
    """Drive file ids of the newest images in the upload folder, newest first.
    Cached; last good value kept on error."""
    now = time.time()
    with _lock:
        if _photos_cache["ids"] is not None and now - _photos_cache["ts"] < _PHOTOS_TTL:
            return _photos_cache["ids"]
    try:
        ids = []
        if photos_configured():
            import requests
            creds = _drive_creds()
            params = {
                "q": ("'%s' in parents and mimeType contains 'image/' "
                      "and trashed = false") % PHOTOS_FOLDER_ID,
                "orderBy": "createdTime desc",
                "pageSize": limit,
                "fields": "files(id)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            r = requests.get("https://www.googleapis.com/drive/v3/files",
                             params=params,
                             headers={"Authorization": "Bearer " + creds.token},
                             timeout=12)
            if r.status_code == 200:
                ids = [f["id"] for f in r.json().get("files", [])]
        with _lock:
            _photos_cache["ids"] = ids
            _photos_cache["ts"] = now
        return ids
    except Exception:
        return _photos_cache["ids"] or []


def fetch_photo_bytes(file_id):
    """Return (bytes, content_type) for one image id, or (None, None).
    Only serves ids that are currently in the highlights set (safety)."""
    if not photos_configured():
        return None, None
    if file_id not in highlight_photo_ids():
        return None, None
    try:
        import requests
        creds = _drive_creds()
        r = requests.get("https://www.googleapis.com/drive/v3/files/%s" % file_id,
                         params={"alt": "media", "supportsAllDrives": "true"},
                         headers={"Authorization": "Bearer " + creds.token},
                         timeout=20)
        if r.status_code != 200:
            return None, None
        return r.content, r.headers.get("Content-Type", "image/jpeg")
    except Exception:
        return None, None


# ----------------------------------------------------------------------------
# In Memoriam + Hall of Fame — admin-managed entries appended to those pages
# ----------------------------------------------------------------------------
# Each lives on its own tab in the same JSSA Website Content sheet, auto-created
# if missing. The hardcoded entries already on the pages stay as they are; these
# admin entries render in addition to them. Same graceful-fallback behavior as
# the Blackboard: if the sheet/API hiccups, the pages still show their built-ins.

MEMORIAM_TAB = "InMemoriam"
MEM_HEADERS = ["id", "name", "when", "image", "order", "active"]
HOF_TAB = "HallOfFame"
HOF_HEADERS = ["id", "year", "name", "body", "image", "order", "active"]

_mem_cache = {"data": None, "ts": 0.0}
_hof_cache = {"data": None, "ts": 0.0}


def _simple_worksheet(tab, headers):
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(_SA_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=200, cols=len(headers))
        ws.update([headers], "A1")
    return ws


def _next_order(records):
    mx = 0
    for r in records:
        mx = max(mx, _to_int(r.get("order")))
    return mx + 1


def _paras(body):
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        return []
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


# ---- In Memoriam ----------------------------------------------------------
def _mem_invalidate():
    with _lock:
        _mem_cache["ts"] = 0.0


def in_memoriam_entries():
    """Active admin-added In Memoriam entries, ordered. Public page."""
    now = time.time()
    with _lock:
        if _mem_cache["data"] is not None and now - _mem_cache["ts"] < _CACHE_TTL:
            return _mem_cache["data"]
    try:
        out = []
        if is_configured():
            ws = _simple_worksheet(MEMORIAM_TAB, MEM_HEADERS)
            for r in ws.get_all_records(expected_headers=MEM_HEADERS):
                if not _is_true(r.get("active")):
                    continue
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                out.append({
                    "name": name,
                    "when": str(r.get("when") or "").strip(),
                    "image": _img_url(str(r.get("image") or "")),
                    "order": _to_int(r.get("order")),
                })
            out.sort(key=lambda e: e["order"])
        with _lock:
            _mem_cache["data"] = out
            _mem_cache["ts"] = now
        return out
    except Exception:
        return _mem_cache["data"] or []


def list_mem_entries():
    if not is_configured():
        return []
    ws = _simple_worksheet(MEMORIAM_TAB, MEM_HEADERS)
    out = [{k: rec.get(k, "") for k in MEM_HEADERS}
           for rec in ws.get_all_records(expected_headers=MEM_HEADERS)]
    out.sort(key=lambda r: _to_int(r.get("order")))
    return out


def add_mem_entry(fields):
    ws = _simple_worksheet(MEMORIAM_TAB, MEM_HEADERS)
    records = ws.get_all_records(expected_headers=MEM_HEADERS)
    order = fields.get("order")
    order = _to_int(order) if str(order or "").strip() else _next_order(records)
    ws.append_row([
        uuid.uuid4().hex[:8],
        (fields.get("name") or "").strip(),
        (fields.get("when") or "").strip(),
        (fields.get("image") or "").strip(),
        order, "TRUE",
    ], value_input_option="USER_ENTERED")
    _mem_invalidate()


def update_mem_entry(entry_id, fields):
    ws = _simple_worksheet(MEMORIAM_TAB, MEM_HEADERS)
    for i, rec in enumerate(ws.get_all_records(expected_headers=MEM_HEADERS)):
        if str(rec.get("id")) == str(entry_id):
            vals = {
                "name": (fields.get("name") or "").strip(),
                "when": (fields.get("when") or "").strip(),
                "image": (fields.get("image") or "").strip(),
            }
            if str(fields.get("order") or "").strip():
                vals["order"] = _to_int(fields.get("order"))
            for k, v in vals.items():
                ws.update_cell(i + 2, MEM_HEADERS.index(k) + 1, v)
            break
    _mem_invalidate()


def set_mem_active(entry_id, active):
    ws = _simple_worksheet(MEMORIAM_TAB, MEM_HEADERS)
    col = MEM_HEADERS.index("active") + 1
    for i, rec in enumerate(ws.get_all_records(expected_headers=MEM_HEADERS)):
        if str(rec.get("id")) == str(entry_id):
            ws.update_cell(i + 2, col, "TRUE" if active else "FALSE")
            break
    _mem_invalidate()


def delete_mem_entry(entry_id):
    ws = _simple_worksheet(MEMORIAM_TAB, MEM_HEADERS)
    for i, rec in enumerate(ws.get_all_records(expected_headers=MEM_HEADERS)):
        if str(rec.get("id")) == str(entry_id):
            ws.delete_rows(i + 2)
            break
    _mem_invalidate()


# ---- Hall of Fame ---------------------------------------------------------
def _hof_invalidate():
    with _lock:
        _hof_cache["ts"] = 0.0


def hof_entries():
    """Active admin-added Hall of Fame inductees, ordered. Public page."""
    now = time.time()
    with _lock:
        if _hof_cache["data"] is not None and now - _hof_cache["ts"] < _CACHE_TTL:
            return _hof_cache["data"]
    try:
        out = []
        if is_configured():
            ws = _simple_worksheet(HOF_TAB, HOF_HEADERS)
            for r in ws.get_all_records(expected_headers=HOF_HEADERS):
                if not _is_true(r.get("active")):
                    continue
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                out.append({
                    "year": str(r.get("year") or "").strip(),
                    "name": name,
                    "body_paras": _paras(str(r.get("body") or "")),
                    "image": _img_url(str(r.get("image") or "")),
                    "order": _to_int(r.get("order")),
                })
            out.sort(key=lambda e: e["order"])
        with _lock:
            _hof_cache["data"] = out
            _hof_cache["ts"] = now
        return out
    except Exception:
        return _hof_cache["data"] or []


def list_hof_entries():
    if not is_configured():
        return []
    ws = _simple_worksheet(HOF_TAB, HOF_HEADERS)
    out = [{k: rec.get(k, "") for k in HOF_HEADERS}
           for rec in ws.get_all_records(expected_headers=HOF_HEADERS)]
    out.sort(key=lambda r: _to_int(r.get("order")))
    return out


def add_hof_entry(fields):
    ws = _simple_worksheet(HOF_TAB, HOF_HEADERS)
    records = ws.get_all_records(expected_headers=HOF_HEADERS)
    order = fields.get("order")
    order = _to_int(order) if str(order or "").strip() else _next_order(records)
    ws.append_row([
        uuid.uuid4().hex[:8],
        (fields.get("year") or "").strip(),
        (fields.get("name") or "").strip(),
        (fields.get("body") or "").strip(),
        (fields.get("image") or "").strip(),
        order, "TRUE",
    ], value_input_option="USER_ENTERED")
    _hof_invalidate()


def update_hof_entry(entry_id, fields):
    ws = _simple_worksheet(HOF_TAB, HOF_HEADERS)
    for i, rec in enumerate(ws.get_all_records(expected_headers=HOF_HEADERS)):
        if str(rec.get("id")) == str(entry_id):
            vals = {
                "year": (fields.get("year") or "").strip(),
                "name": (fields.get("name") or "").strip(),
                "body": (fields.get("body") or "").strip(),
                "image": (fields.get("image") or "").strip(),
            }
            if str(fields.get("order") or "").strip():
                vals["order"] = _to_int(fields.get("order"))
            for k, v in vals.items():
                ws.update_cell(i + 2, HOF_HEADERS.index(k) + 1, v)
            break
    _hof_invalidate()


def set_hof_active(entry_id, active):
    ws = _simple_worksheet(HOF_TAB, HOF_HEADERS)
    col = HOF_HEADERS.index("active") + 1
    for i, rec in enumerate(ws.get_all_records(expected_headers=HOF_HEADERS)):
        if str(rec.get("id")) == str(entry_id):
            ws.update_cell(i + 2, col, "TRUE" if active else "FALSE")
            break
    _hof_invalidate()


def delete_hof_entry(entry_id):
    ws = _simple_worksheet(HOF_TAB, HOF_HEADERS)
    for i, rec in enumerate(ws.get_all_records(expected_headers=HOF_HEADERS)):
        if str(rec.get("id")) == str(entry_id):
            ws.delete_rows(i + 2)
            break
    _hof_invalidate()


# ----------------------------------------------------------------------------
# Board of Directors — admin-managed, seeded with the current board
# ----------------------------------------------------------------------------
# On first use the tab is auto-created and filled with the 12 members currently
# on the homepage, so the site looks identical on day one. The homepage reads
# this tab; if it's empty or the API hiccups, board_members() returns [] and the
# homepage falls back to its built-in list. Editing/deleting here updates the site.

BOARD_TAB = "BoardMembers"
BOARD_HEADERS = ["id", "name", "role", "division", "order", "active"]
_board_cache = {"data": None, "ts": 0.0}

_BOARD_SEED = [
    ("Richie Sewell",  "Commissioner",       ""),
    ("John Cariero",   "Vice Commissioner",  ""),
    ("Steven Klein",   "Executive Director", ""),
    ("Mike Igneri",    "Director",           "red"),
    ("Joe Santos",     "Director",           "red"),
    ("Rick Tuyn",      "Director",           "red"),
    ("Jay Stollman",   "Director",           "white"),
    ("Vic Troiano",    "Director",           "white"),
    ("Dick Wendling",  "Director",           "white"),
    ("Ron Bialosky",   "Director",           "blue"),
    ("Jeff McCrave",   "Director",           "blue"),
    ("Miriam Ruffolo", "Director",           "blue"),
]


def _board_worksheet():
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(_SA_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(BOARD_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=BOARD_TAB, rows=200, cols=len(BOARD_HEADERS))
        ws.update([BOARD_HEADERS], "A1")
        seed = [[uuid.uuid4().hex[:8], name, role, div, i, "TRUE"]
                for i, (name, role, div) in enumerate(_BOARD_SEED, start=1)]
        if seed:
            ws.append_rows(seed, value_input_option="USER_ENTERED")
    return ws


def _initials(name):
    words = [w for w in re.split(r"\s+", (name or "").strip())
             if re.search(r"[A-Za-z0-9]", w)]
    s = "".join((re.sub(r"[^A-Za-z0-9]", "", w)[:1] for w in words[:2]))
    return (s or (name or "?")[:1]).upper()


def _board_div(v):
    s = (v or "").strip().lower()
    return s if s in ("red", "white", "blue") else ""


def _board_invalidate():
    with _lock:
        _board_cache["ts"] = 0.0


def board_members():
    """Active board members, ordered. [] if not set up -> homepage falls back."""
    now = time.time()
    with _lock:
        if _board_cache["data"] is not None and now - _board_cache["ts"] < _CACHE_TTL:
            return _board_cache["data"]
    try:
        out = []
        if is_configured():
            ws = _board_worksheet()
            for r in ws.get_all_records(expected_headers=BOARD_HEADERS):
                if not _is_true(r.get("active")):
                    continue
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                role = str(r.get("role") or "").strip()
                div = _board_div(r.get("division"))
                role_display = role + (" \u00b7 " + div.capitalize() if div else "")
                out.append({
                    "name": name,
                    "role": role_display,
                    "division": div,
                    "initials": _initials(name),
                    "order": _to_int(r.get("order")),
                })
            out.sort(key=lambda m: m["order"])
        with _lock:
            _board_cache["data"] = out
            _board_cache["ts"] = now
        return out
    except Exception:
        return _board_cache["data"] or []


def list_board_members():
    if not is_configured():
        return []
    ws = _board_worksheet()
    out = [{k: rec.get(k, "") for k in BOARD_HEADERS}
           for rec in ws.get_all_records(expected_headers=BOARD_HEADERS)]
    out.sort(key=lambda r: _to_int(r.get("order")))
    return out


def add_board_member(fields):
    ws = _board_worksheet()
    records = ws.get_all_records(expected_headers=BOARD_HEADERS)
    order = fields.get("order")
    order = _to_int(order) if str(order or "").strip() else _next_order(records)
    ws.append_row([
        uuid.uuid4().hex[:8],
        (fields.get("name") or "").strip(),
        (fields.get("role") or "").strip(),
        _board_div(fields.get("division")),
        order, "TRUE",
    ], value_input_option="USER_ENTERED")
    _board_invalidate()


def update_board_member(member_id, fields):
    ws = _board_worksheet()
    for i, rec in enumerate(ws.get_all_records(expected_headers=BOARD_HEADERS)):
        if str(rec.get("id")) == str(member_id):
            vals = {
                "name": (fields.get("name") or "").strip(),
                "role": (fields.get("role") or "").strip(),
                "division": _board_div(fields.get("division")),
            }
            if str(fields.get("order") or "").strip():
                vals["order"] = _to_int(fields.get("order"))
            for k, v in vals.items():
                ws.update_cell(i + 2, BOARD_HEADERS.index(k) + 1, v)
            break
    _board_invalidate()


def set_board_active(member_id, active):
    ws = _board_worksheet()
    col = BOARD_HEADERS.index("active") + 1
    for i, rec in enumerate(ws.get_all_records(expected_headers=BOARD_HEADERS)):
        if str(rec.get("id")) == str(member_id):
            ws.update_cell(i + 2, col, "TRUE" if active else "FALSE")
            break
    _board_invalidate()


def delete_board_member(member_id):
    ws = _board_worksheet()
    for i, rec in enumerate(ws.get_all_records(expected_headers=BOARD_HEADERS)):
        if str(rec.get("id")) == str(member_id):
            ws.delete_rows(i + 2)
            break
    _board_invalidate()


# ----------------------------------------------------------------------------
# Division rosters — the league's master member list, grouped by division
# ----------------------------------------------------------------------------
# Lives on the same schedule spreadsheet as the game-day teams. We locate the
# roster header row ("First Name / Last Name / Division / Position") on any tab,
# read names + positions, and group by RED/WHITE/BLUE. Email addresses are in
# that sheet but are NEVER read or returned here. Cached; fails safe to {}.

_roster_cache = {"data": None, "ts": 0.0}
_ROSTER_TTL = 300  # seconds


def _norm_div(v):
    s = (v or "").strip().upper()
    if s.startswith("R"):
        return "RED"
    if s.startswith("W"):
        return "WHITE"
    if s.startswith("B"):
        return "BLUE"
    return ""


def division_rosters():
    """{'RED':[{name,pos}], 'WHITE':[...], 'BLUE':[...]} from the master roster.
    Emails are intentionally ignored. Cached; last good value kept on error."""
    now = time.time()
    with _lock:
        if _roster_cache["data"] is not None and now - _roster_cache["ts"] < _ROSTER_TTL:
            return _roster_cache["data"]
    try:
        result = {"RED": [], "WHITE": [], "BLUE": []}
        if ROSTER_SHEET_ID and _SA_JSON:
            import gspread
            from google.oauth2.service_account import Credentials
            info = json.loads(_SA_JSON)
            scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
            creds = Credentials.from_service_account_info(info, scopes=scopes)
            gc = gspread.authorize(creds)
            sh = gc.open_by_key(ROSTER_SHEET_ID)

            rows = hdr_idx = cols = None
            for ws in sh.worksheets():
                vals = ws.get_all_values()
                for i, r in enumerate(vals):
                    low = [_clean(c).lower() for c in r]
                    if "first name" in low and "last name" in low and "division" in low:
                        hdr_idx = i
                        cols = {name: ci for ci, name in enumerate(low)}
                        rows = vals
                        break
                if rows is not None:
                    break

            if rows is not None:
                fi = cols.get("first name")
                li = cols.get("last name")
                di = cols.get("division")
                pi = cols.get("position", cols.get("preferred positions"))
                ai = cols.get("active")
                seen = set()
                for r in rows[hdr_idx + 1:]:
                    first = _clean(r[fi]) if fi is not None and len(r) > fi else ""
                    last = _clean(r[li]) if li is not None and len(r) > li else ""
                    div = _norm_div(r[di]) if di is not None and len(r) > di else ""
                    pos = _clean(r[pi]) if pi is not None and len(r) > pi else ""
                    name = (first + " " + last).strip()
                    if not name or not div:
                        continue
                    # Skip anyone not marked Active (when the column exists).
                    if ai is not None and len(r) > ai and not _is_true(r[ai]):
                        continue
                    key = name.lower()
                    if key in seen:          # roster can repeat across tabs
                        continue
                    seen.add(key)
                    result[div].append({"name": name, "pos": pos})
                for d in result:
                    result[d].sort(key=lambda e: e["name"].split()[-1].lower())
        with _lock:
            _roster_cache["data"] = result
            _roster_cache["ts"] = now
        return result
    except Exception:
        return _roster_cache["data"] or {"RED": [], "WHITE": [], "BLUE": []}


# Board-only player directory — name, division, email and phone only.
# Reads the same ROSTER_SHEET_ID sheet. Skill ratings and other internal
# columns are intentionally left out. The Phone column appears automatically
# once a header containing "phone" is added to the sheet — no code change
# needed. Admin-only, so a short cache is fine.

_dir_cache = {"data": None, "ts": 0.0}
_DIR_TTL = 120  # 2 min — admin tool, freshness matters more here


def player_directory():
    """Board-portal member list: name, division, email, phone.
    Returns {'players': [{'name','div','email','phone'}]} sorted by division
    (RED/WHITE/BLUE) then last name. The phone field stays blank until a
    "Phone" column is added to the sheet."""
    now = time.time()
    with _lock:
        if _dir_cache["data"] is not None and now - _dir_cache["ts"] < _DIR_TTL:
            return _dir_cache["data"]
    blank = {"players": []}
    try:
        if not (ROSTER_SHEET_ID and _SA_JSON):
            return blank
        import gspread
        from google.oauth2.service_account import Credentials
        info = json.loads(_SA_JSON)
        scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(ROSTER_SHEET_ID)

        rows = cols = None
        for ws in sh.worksheets():
            vals = ws.get_all_values()
            for i, r in enumerate(vals):
                low = [_clean(c).lower() for c in r]
                if "first name" in low and "last name" in low and "division" in low:
                    cols = {name: ci for ci, name in enumerate(low)}
                    rows = vals[i + 1:]
                    break
            if rows is not None:
                break

        if rows is None:
            return blank

        fi = cols.get("first name")
        li = cols.get("last name")
        di = cols.get("division")
        ei = cols.get("email")
        # Match any header that mentions "phone" (Phone, Phone Number, Cell, …).
        pi = next((ci for name, ci in cols.items() if "phone" in name), None)

        def cell(r, ci):
            return _clean(r[ci]) if ci is not None and len(r) > ci else ""

        div_order = {"RED": 0, "WHITE": 1, "BLUE": 2, "": 3}
        players = []
        for r in rows:
            first = cell(r, fi)
            last = cell(r, li)
            name = (first + " " + last).strip()
            if not name:
                continue
            div = _norm_div(cell(r, di))
            players.append({
                "name": name,
                "div": div,
                "email": cell(r, ei),
                "phone": cell(r, pi),
                "sort_key": (div_order.get(div, 3), last.lower(), first.lower()),
            })
        players.sort(key=lambda p: p["sort_key"])

        result = {"players": players}
        with _lock:
            _dir_cache["data"] = result
            _dir_cache["ts"] = now
        return result
    except Exception:
        return _dir_cache["data"] or blank
# ----------------------------------------------------------------------------


def comm_audiences():
    """Group members into ready-to-email audiences for the admin Communications
    tool. Returns:
      {'all':       [{name, email, phone}],
       'divisions': {'RED':[...], 'WHITE':[...], 'BLUE':[...]},
       'board':     [{name, email, phone, role}]}
    Board members carry no email of their own in the board sheet, so we match
    them to the member directory by name to fill in their address."""
    players = player_directory().get("players", [])

    def has_email(p):
        return bool(p.get("email"))

    def slim(p):
        return {"name": p["name"], "email": p.get("email", ""),
                "phone": p.get("phone", "")}

    all_m = [slim(p) for p in players if has_email(p)]
    divisions = {d: [slim(p) for p in players if p.get("div") == d and has_email(p)]
                 for d in ("RED", "WHITE", "BLUE")}

    by_name = {}
    for p in players:
        if has_email(p):
            by_name.setdefault(p["name"].strip().lower(), p)
    board = []
    try:
        for m in board_members():
            match = by_name.get(m["name"].strip().lower())
            board.append({
                "name": m["name"],
                "email": match.get("email", "") if match else "",
                "phone": match.get("phone", "") if match else "",
                "role": m.get("role", ""),
            })
    except Exception:
        board = []

    return {"all": all_m, "divisions": divisions, "board": board}
# ----------------------------------------------------------------------------
# All of this lives in tabs inside the league's "Website Control Sheet" (the
# same spreadsheet that runs the prediction contest and catches form
# submissions). We only READ here; scores are written back separately via
# set_game_score() so the sheet's own formulas keep doing the math.
CONTROL_SHEET_ID = os.environ.get(
    "CONTROL_SHEET_ID", "1Bpb1PGs2-egEql9rgIsNzFWlRKSrBYLxWdy1NeFkmaM"
).strip()

_season_cache = {"data": None, "ts": 0.0}
_SEASON_TTL = 300  # seconds


def _control_sheet(readonly=True):
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(_SA_JSON)
    scope = "spreadsheets.readonly" if readonly else "spreadsheets"
    scopes = ["https://www.googleapis.com/auth/" + scope]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(CONTROL_SHEET_ID)


def _control_tabs(sh):
    """Read EVERY tab's values in a single batch API call, returning
    [(title, all_values), ...]. One request instead of one-per-tab keeps us
    well under Google's read-per-minute quota even though the control sheet has
    many tabs."""
    meta = sh.fetch_sheet_metadata()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if not titles:
        return []
    ranges = ["'%s'" % t.replace("'", "''") for t in titles]
    resp = sh.values_batch_get(ranges)
    out = []
    for title, vr in zip(titles, resp.get("valueRanges", [])):
        out.append((title, vr.get("values", [])))
    return out


def _match_tab(tabs, required):
    """Find the first (title, values) whose header row contains every name in
    `required`. Returns (title, values, header_index, {name: col})."""
    for title, vals in tabs:
        for i, r in enumerate(vals):
            low = [_clean(c).lower() for c in r]
            if all(req in low for req in required):
                return title, vals, i, {name: ci for ci, name in enumerate(low)}
    return None, None, None, None


def _row_reader(cols):
    def reader(r):
        def cell(name, *alts):
            for n in (name,) + alts:
                ci = cols.get(n)
                if ci is not None and len(r) > ci:
                    return _clean(r[ci])
            return ""
        return cell
    return reader


def league_season():
    """Everything the public league pages need, read from the Control Sheet:
        {'standings': {RED/WHITE/BLUE: [team,...]},
         'schedule':  [game,...],
         'results':   [game,...],
         'rosters':   {RED/WHITE/BLUE: [{team, players:[...]}]}}
    Cached for _SEASON_TTL seconds; last good value kept on error."""
    now = time.time()
    with _lock:
        if _season_cache["data"] is not None and now - _season_cache["ts"] < _SEASON_TTL:
            return _season_cache["data"]

    blank = {"standings": {"RED": [], "WHITE": [], "BLUE": []},
             "schedule": [], "results": [],
             "rosters": {"RED": [], "WHITE": [], "BLUE": []}}
    try:
        data = {"standings": {"RED": [], "WHITE": [], "BLUE": []},
                "schedule": [], "results": [],
                "rosters": {"RED": [], "WHITE": [], "BLUE": []}}
        if CONTROL_SHEET_ID and _SA_JSON:
            sh = _control_sheet(readonly=True)
            tabs = _control_tabs(sh)          # one batch read for all tabs

            # --- Standings ---
            _, rows, hi, cols = _match_tab(tabs, ["team", "wins", "losses"])
            if rows is not None:
                for r in rows[hi + 1:]:
                    g = _row_reader(cols)(r)
                    team = g("team", "team name")
                    div = _norm_div(g("division"))
                    if not team or not div:
                        continue
                    data["standings"][div].append({
                        "team": team,
                        "wins": g("wins"), "losses": g("losses"),
                        "pct": g("win %", "win%", "pct"),
                        "gb": g("games back", "gb"),
                        "rf": g("runs for"), "ra": g("runs against"),
                        "diff": g("run diff", "diff"),
                    })

            # --- Schedule ---
            _, rows, hi, cols = _match_tab(tabs, ["home team", "away team", "status"])
            if rows is not None:
                for r in rows[hi + 1:]:
                    g = _row_reader(cols)(r)
                    home = g("home team")
                    away = g("away team")
                    if not (home or away):
                        continue
                    data["schedule"].append({
                        "division": _norm_div(g("division")),
                        "date": g("date"), "time": g("time"), "field": g("field"),
                        "home": home, "away": away,
                        "score_home": g("score home"), "score_away": g("score away"),
                        "status": g("status"),
                    })

            # --- Results ---
            _, rows, hi, cols = _match_tab(tabs, ["result"])
            if rows is not None and ("home team" in cols and "away team" in cols):
                for r in rows[hi + 1:]:
                    g = _row_reader(cols)(r)
                    home = g("home team")
                    away = g("away team")
                    result = g("result")
                    if not (home or away) or not result:
                        continue
                    data["results"].append({
                        "division": _norm_div(g("division")),
                        "date": g("date"), "time": g("time"), "field": g("field"),
                        "home": home, "away": away,
                        "home_score": g("home score", "score home"),
                        "away_score": g("away score", "score away"),
                        "result": result,
                    })

            # --- Rosters (Players tab: Team Name + player names) ---
            _, rows, hi, cols = _match_tab(
                tabs, ["team name", "player first name", "player last name"])
            if rows is not None:
                bucket = {}  # (div, team) -> [names]
                order = []
                for r in rows[hi + 1:]:
                    g = _row_reader(cols)(r)
                    team = g("team name")
                    div = _norm_div(g("division"))
                    name = (g("player first name") + " " + g("player last name")).strip()
                    if not team or not div or not name:
                        continue
                    key = (div, team)
                    if key not in bucket:
                        bucket[key] = []
                        order.append(key)
                    bucket[key].append(name)
                for (div, team) in order:
                    players = sorted(bucket[(div, team)],
                                     key=lambda n: n.split()[-1].lower())
                    data["rosters"][div].append({"team": team, "players": players})

        with _lock:
            _season_cache["data"] = data
            _season_cache["ts"] = now
        return data
    except Exception:
        return _season_cache["data"] or blank


def schedule_games_for_scoring():
    """List of scheduled games for the admin score form, each tagged with the
    sheet row number so a score can be written straight back to the right row:
        [{row, division, date, time, field, home, away,
          score_home, score_away, status}]"""
    try:
        if not (CONTROL_SHEET_ID and _SA_JSON):
            return []
        sh = _control_sheet(readonly=True)
        tabs = _control_tabs(sh)
        _, rows, hi, cols = _match_tab(tabs, ["home team", "away team", "status"])
        if rows is None:
            return []
        out = []
        for idx in range(hi + 1, len(rows)):
            r = rows[idx]
            g = _row_reader(cols)(r)
            home = g("home team")
            away = g("away team")
            if not (home or away):
                continue
            out.append({
                "row": idx + 1,  # gspread rows are 1-based
                "division": _norm_div(g("division")),
                "date": g("date"), "time": g("time"), "field": g("field"),
                "home": home, "away": away,
                "score_home": g("score home"), "score_away": g("score away"),
                "status": g("status"),
            })
        return out
    except Exception:
        return []


def set_game_score(row, score_home, score_away, status="Final"):
    """Write a final score back into the Schedule tab for one game row. Updates
    the 'Score Home', 'Score Away' and 'Status' columns; the sheet's own
    formulas then update Results and Standings. Returns True on success."""
    try:
        if not (CONTROL_SHEET_ID and _SA_JSON):
            return False
        import gspread
        sh = _control_sheet(readonly=False)
        tabs = _control_tabs(sh)
        title, rows, hi, cols = _match_tab(tabs, ["home team", "away team", "status"])
        if title is None:
            return False
        ws = sh.worksheet(title)
        sh_i = cols.get("score home")
        sa_i = cols.get("score away")
        st_i = cols.get("status")
        updates = []
        if sh_i is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(row, sh_i + 1),
                            "values": [[str(score_home)]]})
        if sa_i is not None:
            updates.append({"range": gspread.utils.rowcol_to_a1(row, sa_i + 1),
                            "values": [[str(score_away)]]})
        if st_i is not None and status:
            updates.append({"range": gspread.utils.rowcol_to_a1(row, st_i + 1),
                            "values": [[status]]})
        if updates:
            ws.batch_update(updates)
        with _lock:                      # force the public pages to refresh
            _season_cache["ts"] = 0.0
        return True
    except Exception:
        return False



# ----------------------------------------------------------------------------
# Prediction Contest — report game winners from the admin panel.
# ----------------------------------------------------------------------------
# The contest lives entirely in the Control Sheet and is driven by formulas:
#   * "Prediction Games" tab  — one row per game. Columns include Game Date,
#     Field, Home Captain, Visitor Captain, Status, Winner (H/V), Scored.
#     Tom normally types H or V into the Winner column by hand each day.
#   * "Prediction Picks" tab  — one row per person per game, with their
#     Predicted Winner (H/V). When a game's Winner is filled in, the sheet's
#     own formulas flow it into the Picks tab (Actual Winner / Correct /
#     Points) and on into the matrix + leaderboard.
# So the only manual step we replace is writing H/V into the Games tab's
# Winner column. set_prediction_winner() does exactly that and nothing else,
# leaving every formula to do the rest. We also read the Picks tab to show a
# quick "X picked Home / Y picked Visitor" tally as Tom scores.

def _pred_pick_tallies(tabs):
    """{(game_date, field): {'H': count, 'V': count, 'total': n}} from the
    Prediction Picks tab, so the admin page can show how the league voted."""
    _, rows, hi, cols = _match_tab(tabs, ["predicted winner", "game date", "field"])
    tallies = {}
    if rows is None:
        return tallies
    for r in rows[hi + 1:]:
        g = _row_reader(cols)(r)
        date = g("game date")
        field = g("field")
        pick = g("predicted winner").upper()[:1]
        if not date or not field:
            continue
        key = (date, field)
        t = tallies.setdefault(key, {"H": 0, "V": 0, "total": 0})
        if pick in ("H", "V"):
            t[pick] += 1
            t["total"] += 1
    return tallies


def prediction_games_for_scoring():
    """List of prediction-contest games for the admin 'report winners' page,
    each tagged with its sheet row number so the winner can be written straight
    back to the right row:
        [{row, date, field, home, away, winner, scored,
          picks_home, picks_away, picks_total}]
    'home'/'away' are the captain names; 'winner' is '' / 'H' / 'V'."""
    try:
        if not (CONTROL_SHEET_ID and _SA_JSON):
            return []
        sh = _control_sheet(readonly=True)
        tabs = _control_tabs(sh)
        title, rows, hi, cols = _match_tab(
            tabs, ["home captain", "visitor captain", "winner"])
        if rows is None:
            return []
        tallies = _pred_pick_tallies(tabs)
        out = []
        for idx in range(hi + 1, len(rows)):
            r = rows[idx]
            g = _row_reader(cols)(r)
            home = g("home captain")
            away = g("visitor captain")
            if not (home or away):
                continue
            date = g("game date")
            field = g("field")
            t = tallies.get((date, field), {"H": 0, "V": 0, "total": 0})
            out.append({
                "row": idx + 1,  # gspread rows are 1-based
                "date": date,
                "field": field,
                "home": home,
                "away": away,
                "status": g("status"),
                "winner": g("winner").upper()[:1],
                "scored": _is_true(g("scored")),
                "picks_home": t["H"],
                "picks_away": t["V"],
                "picks_total": t["total"],
            })
        return out
    except Exception:
        return []


def set_prediction_winner(row, winner):
    """Write the winning side ('H' or 'V') into the Prediction Games tab's
    'Winner' column for one game row. The sheet's own formulas then score the
    Picks tab, the matrix and the leaderboard. Returns True on success."""
    winner = str(winner or "").strip().upper()[:1]
    if winner not in ("H", "V"):
        return False
    try:
        if not (CONTROL_SHEET_ID and _SA_JSON):
            return False
        import gspread
        sh = _control_sheet(readonly=False)
        tabs = _control_tabs(sh)
        title, rows, hi, cols = _match_tab(
            tabs, ["home captain", "visitor captain", "winner"])
        if title is None:
            return False
        wi = cols.get("winner")
        if wi is None:
            return False
        ws = sh.worksheet(title)
        ws.update_cell(row, wi + 1, winner)
        return True
    except Exception:
        return False


# Live odds for the homepage — how the league is currently picking the next
# game day's games. Reads the same Prediction Games / Picks data the admin
# scoring page uses, keeps only games not yet scored, and turns the Home/
# Visitor pick counts into percentages. Cached briefly so it feels "live"
# without hammering the Sheets API on every page view.
_pred_odds_cache = {"data": None, "ts": 0.0}
_PRED_ODDS_TTL = 120  # 2 minutes


def _norm_pred_date(s):
    """Canonicalize a game date to YYYY-MM-DD so the Games tab (which stores
    dates like '6/19/2026') and the Picks tab (which stores '2026-06-19') line
    up. Unknown formats are returned trimmed, as-is."""
    s = (s or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        return "%04d-%02d-%02d" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        return "%04d-%02d-%02d" % (y, mo, d)
    return s


def prediction_odds():
    """Open (not-yet-scored) prediction games for the next game day, with live
    vote percentages: [{date, field, home, away, picks_home, picks_away,
    total, home_pct, away_pct}]. Returns [] if nothing is open or set up.

    Reads the control sheet directly and joins the Games and Picks tabs on a
    NORMALIZED (date, field) key, so differing date formats between the two
    tabs still match."""
    now = time.time()
    with _lock:
        c = _pred_odds_cache
        if c["data"] is not None and now - c["ts"] < _PRED_ODDS_TTL:
            return c["data"]
    out = []
    try:
        if CONTROL_SHEET_ID and _SA_JSON:
            sh = _control_sheet(readonly=True)
            tabs = _control_tabs(sh)

            # 1) Tally picks per normalized (date, field) from the Picks tab.
            _, prows, phi, pcols = _match_tab(
                tabs, ["predicted winner", "game date", "field"])
            tallies = {}
            if prows is not None:
                preader = _row_reader(pcols)
                for r in prows[phi + 1:]:
                    g = preader(r)
                    key = (_norm_pred_date(g("game date")),
                           g("field").strip().lower())
                    if not key[0] or not key[1]:
                        continue
                    pick = g("predicted winner").upper()[:1]
                    t = tallies.setdefault(key, {"H": 0, "V": 0, "total": 0})
                    if pick in ("H", "V"):
                        t[pick] += 1
                        t["total"] += 1

            # 2) Read the Games tab, keep only games still open (not scored,
            #    no winner recorded yet).
            _, grows, ghi, gcols = _match_tab(
                tabs, ["home captain", "visitor captain", "winner"])
            open_games = []
            if grows is not None:
                greader = _row_reader(gcols)
                for r in grows[ghi + 1:]:
                    g = greader(r)
                    home = g("home captain")
                    away = g("visitor captain")
                    if not (home or away):
                        continue
                    if _is_true(g("scored")) or g("winner").strip():
                        continue
                    raw = g("game date")
                    open_games.append({
                        "date_raw": raw,
                        "date": _norm_pred_date(raw),
                        "field": g("field"),
                        "home": home,
                        "away": away,
                    })

            # 3) Keep only the latest open game day, then attach the tallies.
            if open_games:
                latest = max(g["date"] for g in open_games)
                for g in open_games:
                    if g["date"] != latest:
                        continue
                    t = tallies.get((g["date"], g["field"].strip().lower()),
                                    {"H": 0, "V": 0, "total": 0})
                    total, h, v = t["total"], t["H"], t["V"]
                    hp = round(h * 100 / total) if total else None
                    vp = (100 - hp) if hp is not None else None
                    out.append({
                        "date": g["date_raw"],
                        "field": g["field"],
                        "home": g["home"],
                        "away": g["away"],
                        "picks_home": h,
                        "picks_away": v,
                        "total": total,
                        "home_pct": hp,
                        "away_pct": vp,
                    })
    except Exception:
        out = []
    with _lock:
        _pred_odds_cache["data"] = out
        _pred_odds_cache["ts"] = now
    return out


# ----------------------------------------------------------------------------
# Sponsors — admin-managed, seeded with the current sponsor list
# ----------------------------------------------------------------------------
# On first use the tab is created and filled with the sponsors currently on the
# homepage, so the site looks identical on day one. The homepage reads this tab;
# if it's empty/the API hiccups, the homepage falls back to its built-in list.

# Legacy compatibility: the original sponsor list referenced /static/logos/*.png
# files that don't serve in production. Any Sponsors tab seeded before this fix
# still holds those dead paths, so we transparently map them to the Drive image
# that actually renders. New seeds already use the Drive id directly.
_LEGACY_LOGO_FIX = {
    "/static/logos/american-sr-health.png": "1le3nK1MdPqY8qH0Tgzb1BUjbjMuyTB4E",
    "/static/logos/panera.png": "1pAOFUafHOkAH6fHIFeJjWTU4PwPMlIAM",
    "/static/logos/stephen-denny.png": "1Th9uRAjEfW-vhLkDisx1DBCJBvh0Z8Xc",
    "/static/logos/golf-club.png": "1_e6wYNqDJOGEsqDDudUpvrFIPqK_t5sE",
    "/static/logos/royal-cafe.png": "166z3zdu8zPfTwJfj-m7enarLRooCTCaY",
    "/static/logos/team1-sports.png": "17zkWfSVqNtZ2Uvhe-L4IyhT2VYF275Bo",
    "/static/logos/uncle-micks.png": "1jU_X9_jiqiDKENNJiaY2Z1ucDs09S-tz",
    "/static/logos/1000-north.png": "1WVIh0rPpM3U1SKXwvHxSRn2jsF8ICsXm",
    "/static/logos/cindy-sojka.png": "1Gffs-pHQ0Cfxqun3FiaKa_ExsEjmhezC",
    "/static/logos/mike-parenti.png": "1Ewyhqu9_JMxo6MZOxhnboelNIzKBPzxw",
    "/static/logos/food-shack.png": "1Wih3oO98Az7QhVmhkrNa8_k1E1FzelwT",
    "/static/logos/se-rods.png": "1pft9Wt4I3g1rZiIkDGHKZju7rpBGPXFF",
    "/static/logos/village-scoop-shack.png": "1xza0JCWClLBISuFTRjSjbPTDFC-RY6n8",
}

SPONSOR_TAB = "Sponsors"
SPONSOR_HEADERS = ["id", "name", "tagline", "website", "logo", "phone", "order", "active"]
_sponsor_cache = {"data": None, "ts": 0.0}

_SPONSOR_SEED = [
    ("American Sr Health Services", "Health, Life & Long Term Insurance \u00b7 Medicare Supplements", "http://www.healthyseniorstc.com/", "1le3nK1MdPqY8qH0Tgzb1BUjbjMuyTB4E", ""),
    ("Panera Bread", "Try our new Mix & Match Value Menu today", "https://www.panerabread.com/", "1pAOFUafHOkAH6fHIFeJjWTU4PwPMlIAM", ""),
    ("Stephen K. Denny A/C & Pool Heating", "Striving to exceed our customer's expectations", "http://www.stephenkdenny.com/", "1Th9uRAjEfW-vhLkDisx1DBCJBvh0Z8Xc", ""),
    ("The Golf Club of Jupiter", "Nestled in the heart of Jupiter", "https://golfclubofjupiter.com/", "1_e6wYNqDJOGEsqDDudUpvrFIPqK_t5sE", ""),
    ("Royal Cafe", "Best breakfast in Jupiter!", "http://royalcafejupiter.com/", "166z3zdu8zPfTwJfj-m7enarLRooCTCaY", ""),
    ("Team 1 Sports \u2014 Alan Tanner", "Softball & baseball equipment", "https://teammikenworth.com/", "17zkWfSVqNtZ2Uvhe-L4IyhT2VYF275Bo", ""),
    ("Uncle Mick's Bar & Grill", "Best bar and grill in Jupiter!", "http://www.unclemicks.com/", "1jU_X9_jiqiDKENNJiaY2Z1ucDs09S-tz", ""),
    ("1000 North", "South Florida's premier waterfront restaurant", "http://1000north.com/", "1WVIh0rPpM3U1SKXwvHxSRn2jsF8ICsXm", ""),
    ("Cindy A. Sojka, P.A.", "Personal injury attorneys", "http://cindysojkalaw.com/", "1Gffs-pHQ0Cfxqun3FiaKa_ExsEjmhezC", ""),
    ("Mike Parenti Comedy Show", "Coming soon!", "https://mikeparenti.com/", "1Ewyhqu9_JMxo6MZOxhnboelNIzKBPzxw", ""),
    ("Food Shack", "A Jupiter favorite", "http://www.littlemoirsjupiter.com/", "1Wih3oO98Az7QhVmhkrNa8_k1E1FzelwT", ""),
    ("South East Rods & Customs", "Share our passion for cars", "http://serodsandcustoms.com/", "1pft9Wt4I3g1rZiIkDGHKZju7rpBGPXFF", ""),
    ("Village Scoop Shack", "Ice cream & cereal bar", "https://www.villagescoopshack.com/", "1xza0JCWClLBISuFTRjSjbPTDFC-RY6n8", ""),
    ("Benaim Eye Aesthetics", "Eye doctor", "http://www.benaimeye.com/", "https://benaimeye.com/wp-content/uploads/2024/07/Benaim-Eye-Aesthetics-e1721242084342.png", ""),
    ("Shuster Eye Center", "Eye doctor", "http://www.shustereyecenter.com/", "https://shustereyecenter.com/wp-content/uploads/2016/07/shuster-eye-logo-black-1.png", ""),
    ("Hibiscus Streatery", "Little Moir's Jupiter", "http://www.littlemoirsjupiter.com/hibiscus-streatery", "https://littlemoirs.com/wp-content/uploads/2024/10/cropped-Little-Moirs-Favicon-270x270.png", ""),
    ("Debra Stollman, Realtor", "Donohue Real Estate", "http://www.donohuerealestate.com/", "https://cdn.agentimagehosting.com/wwoCYFk8F1NupDuCInC1l/2020/05/dono-logo.png", ""),
    ("Horizon Care Services", "North Palm Beach", "http://www.horizoncareservices.com/", "https://www.horizoncareservices.com/wp-content/uploads/2025/11/Horizon-Logo-Excellence-new-logo.png", ""),
    ("Treasure Coast Carpet", "Tequesta, FL", "http://treasurecoastcarpet.com/", "", ""),
    ("Carmine's Restorante", "Restaurant \u00b7 Jupiter", "http://carminescfp.com/", "https://carminescfp.com/wp-content/uploads/2023/02/logo-avada-retro-1.png", ""),
    ("Bradley Molineux", "LPL Securities & Advisory Services", "", "", "772-221-3100"),
]


def _sponsor_worksheet():
    import gspread
    from google.oauth2.service_account import Credentials
    info = json.loads(_SA_JSON)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(SPONSOR_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SPONSOR_TAB, rows=200, cols=len(SPONSOR_HEADERS))
        ws.update([SPONSOR_HEADERS], "A1")
        seed = [[uuid.uuid4().hex[:8], n, t, u, l, p, i, "TRUE"]
                for i, (n, t, u, l, p) in enumerate(_SPONSOR_SEED, start=1)]
        if seed:
            ws.append_rows(seed, value_input_option="USER_ENTERED")
    return ws


def _sponsor_invalidate():
    with _lock:
        _sponsor_cache["ts"] = 0.0


def sponsors():
    """Active sponsors, ordered. Shaped for the homepage script (n,t,u,l,p).
    [] if not set up -> homepage falls back to its built-in list."""
    now = time.time()
    with _lock:
        if _sponsor_cache["data"] is not None and now - _sponsor_cache["ts"] < _CACHE_TTL:
            return _sponsor_cache["data"]
    try:
        out = []
        if is_configured():
            ws = _sponsor_worksheet()
            for r in ws.get_all_records(expected_headers=SPONSOR_HEADERS):
                if not _is_true(r.get("active")):
                    continue
                name = str(r.get("name") or "").strip()
                if not name:
                    continue
                out.append({
                    "n": name,
                    "t": str(r.get("tagline") or "").strip(),
                    "u": str(r.get("website") or "").strip(),
                    "l": _img_url(_LEGACY_LOGO_FIX.get(str(r.get("logo") or "").strip(), str(r.get("logo") or ""))),
                    "p": str(r.get("phone") or "").strip(),
                    "order": _to_int(r.get("order")),
                })
            out.sort(key=lambda s: s["order"])
        with _lock:
            _sponsor_cache["data"] = out
            _sponsor_cache["ts"] = now
        return out
    except Exception:
        return _sponsor_cache["data"] or []


def list_sponsors():
    if not is_configured():
        return []
    ws = _sponsor_worksheet()
    out = [{k: rec.get(k, "") for k in SPONSOR_HEADERS}
           for rec in ws.get_all_records(expected_headers=SPONSOR_HEADERS)]
    out.sort(key=lambda r: _to_int(r.get("order")))
    return out


def add_sponsor(fields):
    ws = _sponsor_worksheet()
    records = ws.get_all_records(expected_headers=SPONSOR_HEADERS)
    order = fields.get("order")
    order = _to_int(order) if str(order or "").strip() else _next_order(records)
    ws.append_row([
        uuid.uuid4().hex[:8],
        (fields.get("name") or "").strip(),
        (fields.get("tagline") or "").strip(),
        (fields.get("website") or "").strip(),
        (fields.get("logo") or "").strip(),
        (fields.get("phone") or "").strip(),
        order, "TRUE",
    ], value_input_option="USER_ENTERED")
    _sponsor_invalidate()


def update_sponsor(sponsor_id, fields):
    ws = _sponsor_worksheet()
    for i, rec in enumerate(ws.get_all_records(expected_headers=SPONSOR_HEADERS)):
        if str(rec.get("id")) == str(sponsor_id):
            vals = {
                "name": (fields.get("name") or "").strip(),
                "tagline": (fields.get("tagline") or "").strip(),
                "website": (fields.get("website") or "").strip(),
                "logo": (fields.get("logo") or "").strip(),
                "phone": (fields.get("phone") or "").strip(),
            }
            if str(fields.get("order") or "").strip():
                vals["order"] = _to_int(fields.get("order"))
            for k, v in vals.items():
                ws.update_cell(i + 2, SPONSOR_HEADERS.index(k) + 1, v)
            break
    _sponsor_invalidate()


def set_sponsor_active(sponsor_id, active):
    ws = _sponsor_worksheet()
    col = SPONSOR_HEADERS.index("active") + 1
    for i, rec in enumerate(ws.get_all_records(expected_headers=SPONSOR_HEADERS)):
        if str(rec.get("id")) == str(sponsor_id):
            ws.update_cell(i + 2, col, "TRUE" if active else "FALSE")
            break
    _sponsor_invalidate()


def delete_sponsor(sponsor_id):
    ws = _sponsor_worksheet()
    for i, rec in enumerate(ws.get_all_records(expected_headers=SPONSOR_HEADERS)):
        if str(rec.get("id")) == str(sponsor_id):
            ws.delete_rows(i + 2)
            break
    _sponsor_invalidate()
