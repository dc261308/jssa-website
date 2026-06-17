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
HEADERS = ["id", "type", "message", "active", "created_by", "created_at"]

_CACHE_TTL = 30  # seconds
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
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=NOTICES_TAB, rows=200, cols=len(HEADERS))
        ws.update([HEADERS], "A1")
    return ws


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


def add_notice(ntype, message, created_by):
    ntype = "weather" if ntype == "weather" else "announcement"
    row = [
        uuid.uuid4().hex[:8],
        ntype,
        message,
        "TRUE",
        created_by or "Admin",
        datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
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

_teams_cache = {"data": None, "ts": 0.0}
_TEAMS_TTL = 120  # seconds


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
_PHOTOS_TTL = 120
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
