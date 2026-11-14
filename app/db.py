"""Shared SQLite connection and schema (instance/medlink.db)."""
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app

LOCAL_TZ = ZoneInfo("Europe/Berlin")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS compartment_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kvnr TEXT NOT NULL,
    week TEXT NOT NULL DEFAULT '',  -- ISO calendar week, e.g. '2026-W46'
    day INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    pzn TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    loaded_by TEXT NOT NULL,
    loaded_at TEXT NOT NULL
);

-- Audit trail of changes per compartment (current / previous modification in the tile).
CREATE TABLE IF NOT EXISTS compartment_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kvnr TEXT NOT NULL,
    week TEXT NOT NULL DEFAULT '',  -- ISO calendar week, e.g. '2026-W46'
    day INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    action TEXT NOT NULL,          -- 'load' | 'remove'
    pzn TEXT NOT NULL,
    by_name TEXT NOT NULL,
    at TEXT NOT NULL
);

-- Comments between doctor and caregiver, attached to a compartment.
CREATE TABLE IF NOT EXISTS compartment_comment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kvnr TEXT NOT NULL,
    week TEXT NOT NULL DEFAULT '',  -- ISO calendar week, e.g. '2026-W46'
    day INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    author TEXT NOT NULL,
    role TEXT NOT NULL,            -- 'doctor' | 'nurse'
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Local medicine database, keyed by PZN. Images live in static/medicine_images/<PZN>.<ext>.
CREATE TABLE IF NOT EXISTS medicine (
    pzn TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    strength TEXT NOT NULL DEFAULT '',
    form TEXT NOT NULL DEFAULT '',
    shape TEXT NOT NULL DEFAULT 'round',
    color TEXT NOT NULL DEFAULT '#F4F4F4',
    color2 TEXT,
    scored INTEGER NOT NULL DEFAULT 0,
    imprint TEXT NOT NULL DEFAULT ''
);
"""


_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_entry_patient ON compartment_entry (kvnr, week, day, slot);
CREATE INDEX IF NOT EXISTS idx_event_patient ON compartment_event (kvnr, week, day, slot);
CREATE INDEX IF NOT EXISTS idx_comment_patient ON compartment_comment (kvnr, week, day, slot);
"""

# table -> timestamp column used to backfill `week` for rows created before weeks existed
_WEEK_TABLES = {
    "compartment_entry": "loaded_at",
    "compartment_event": "at",
    "compartment_comment": "created_at",
}


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(compartment_entry)")}
    if "qty" not in cols:  # databases created by the first prototype
        conn.execute("ALTER TABLE compartment_entry ADD COLUMN qty INTEGER NOT NULL DEFAULT 1")
    for table, ts_col in _WEEK_TABLES.items():
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "week" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN week TEXT NOT NULL DEFAULT ''")
        for row in conn.execute(f"SELECT id, {ts_col} AS ts FROM {table} WHERE week = ''").fetchall():
            y, w, _ = datetime.fromisoformat(row["ts"]).astimezone(LOCAL_TZ).isocalendar()
            conn.execute(f"UPDATE {table} SET week = ? WHERE id = ?", (f"{y}-W{w:02d}", row["id"]))
    conn.executescript(_INDEXES)


def connect() -> sqlite3.Connection:
    os.makedirs(current_app.instance_path, exist_ok=True)
    conn = sqlite3.connect(os.path.join(current_app.instance_path, "medlink.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    with conn:
        _migrate(conn)
    return conn
