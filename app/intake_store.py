"""Storage for the medicine compartment box (4 slots x 7 days per patient)."""
import re
from datetime import date, datetime, timedelta, timezone

from . import db

DAYS = [("Mo", "Monday"), ("Tu", "Tuesday"), ("We", "Wednesday"), ("Th", "Thursday"),
        ("Fr", "Friday"), ("Sa", "Saturday"), ("Su", "Sunday")]
SLOTS = [("Morn", "Morning"), ("Noon", "Noon"), ("Eve", "Evening"), ("Night", "Night")]

MAX_COMMENT_LEN = 500


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- calendar weeks (ISO 8601, e.g. "2026-W46") ----
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def week_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def current_week() -> str:
    return week_id(datetime.now(db.LOCAL_TZ).date())


def parse_week(value):
    """Return a normalized week id if `value` is a real ISO week, else None."""
    m = _WEEK_RE.match(value or "")
    if not m:
        return None
    try:
        date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        return None
    return value


def week_monday(week: str) -> date:
    y, w = _WEEK_RE.match(week).groups()
    return date.fromisocalendar(int(y), int(w), 1)


def week_label(week: str) -> str:
    mon = week_monday(week)
    sun = mon + timedelta(days=6)
    return f"CW {int(week[-2:])} / {week[:4]} ({mon:%d.%m.}-{sun:%d.%m.})"


def week_options(selected: str, before: int = 8, after: int = 12):
    """[(week_id, label)] around the current week, always including `selected`."""
    base = week_monday(current_week())
    ids = {week_id(base + timedelta(weeks=i)) for i in range(-before, after + 1)}
    ids.add(selected)
    return [(w, week_label(w)) for w in sorted(ids, key=week_monday)]


def is_valid_pzn(pzn: str) -> bool:
    """8-digit PZN: weights 1..7 on the first 7 digits, sum mod 11 must equal the 8th digit."""
    if not re.fullmatch(r"[0-9]{8}", pzn):
        return False
    check = sum(int(d) * w for w, d in enumerate(pzn[:7], start=1)) % 11
    return check != 10 and check == int(pzn[7])


def extract_pzn(raw: str):
    """Pull a valid PZN out of whatever a scanner returned, or None.

    Handles plain 8 digits, Code 39 ("-12345678" / "PZN-12345678"), the
    securPharm Data Matrix ("...9N12345678...") and GTIN-13 ("4150" + PZN + check).
    """
    raw = (raw or "").strip()
    candidates = []
    if re.fullmatch(r"\d{8}", raw):
        candidates.append(raw)
    m = re.search(r"PZN[-\s:]*(\d{8})", raw, re.I) or re.fullmatch(r"-(\d{8})", raw)
    if m:
        candidates.append(m.group(1))
    m = re.search(r"9N(\d{8})", raw)
    if m:
        candidates.append(m.group(1))
    m = re.search(r"4150(\d{8})\d", raw)
    if m:
        candidates.append(m.group(1))
    return next((c for c in candidates if is_valid_pzn(c)), None)


def add_entries(kvnr: str, week: str, cells, pzn: str, qty: int, loaded_by: str) -> None:
    """cells: iterable of (day, slot). Also writes one audit event per compartment."""
    now = _now()
    cells = list(cells)
    conn = db.connect()
    try:
        with conn:
            conn.executemany(
                "INSERT INTO compartment_entry (kvnr, week, day, slot, pzn, qty, loaded_by, loaded_at) VALUES (?,?,?,?,?,?,?,?)",
                [(kvnr, week, d, s, pzn, qty, loaded_by, now) for d, s in cells],
            )
            conn.executemany(
                "INSERT INTO compartment_event (kvnr, week, day, slot, action, pzn, by_name, at) VALUES (?,?,?,?,?,?,?,?)",
                [(kvnr, week, d, s, "load", pzn, loaded_by, now) for d, s in cells],
            )
    finally:
        conn.close()


def delete_entry(kvnr: str, entry_id: int, removed_by: str) -> None:
    conn = db.connect()
    try:
        with conn:
            row = conn.execute(
                "SELECT * FROM compartment_entry WHERE id = ? AND kvnr = ?", (entry_id, kvnr)
            ).fetchone()
            if row is None:
                return
            conn.execute("DELETE FROM compartment_entry WHERE id = ?", (entry_id,))
            conn.execute(
                "INSERT INTO compartment_event (kvnr, week, day, slot, action, pzn, by_name, at) VALUES (?,?,?,?,?,?,?,?)",
                (kvnr, row["week"], row["day"], row["slot"], "remove", row["pzn"], removed_by, _now()),
            )
    finally:
        conn.close()


def _fetch(sql, params):
    conn = db.connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def list_entries(kvnr: str, week: str):
    return _fetch(
        "SELECT * FROM compartment_entry WHERE kvnr = ? AND week = ? ORDER BY loaded_at, id", (kvnr, week)
    )


def list_events(kvnr: str, week: str):
    """Newest first."""
    return _fetch(
        "SELECT * FROM compartment_event WHERE kvnr = ? AND week = ? ORDER BY at DESC, id DESC", (kvnr, week)
    )


def add_comment(kvnr: str, week: str, day: int, slot: int, author: str, role: str, body: str) -> None:
    conn = db.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO compartment_comment (kvnr, week, day, slot, author, role, body, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (kvnr, week, day, slot, author, role, body, _now()),
            )
    finally:
        conn.close()


def list_comments(kvnr: str, week: str):
    """Newest first."""
    return _fetch(
        "SELECT * FROM compartment_comment WHERE kvnr = ? AND week = ? ORDER BY created_at DESC, id DESC",
        (kvnr, week),
    )


def comment_counts_by_week(kvnr: str):
    """{week: count}, so the week dropdown can hint where comments exist."""
    return {
        r["week"]: r["n"]
        for r in _fetch("SELECT week, COUNT(*) AS n FROM compartment_comment WHERE kvnr = ? GROUP BY week", (kvnr,))
    }
