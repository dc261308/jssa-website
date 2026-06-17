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
