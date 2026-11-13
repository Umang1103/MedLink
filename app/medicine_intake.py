"""Tab 2: Medicine Intake Supervision - digital 4x7 medicine compartment box.

Rows = 4 times of day, columns = 7 weekdays. Clicking a compartment opens its
tile: the nurse scans the medicine pack (PZN), the doctor and the nurse can
leave comments (e.g. dose changes). Doctors get the box read-only otherwise.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import intake_store as store
from . import medicine_db
from .patient import patient_required

bp = Blueprint("medicine_intake", __name__, url_prefix="/intake")

LOCAL_TZ = ZoneInfo("Europe/Berlin")


def _parse_cell(day, slot):
    """Return (day, slot) ints if valid, else None."""
    try:
        d, s = int(day), int(slot)
    except (TypeError, ValueError):
        return None
    if 0 <= d < len(store.DAYS) and 0 <= s < len(store.SLOTS):
        return d, s
    return None


def _nurse_only():
    if g.hpc["role"] != "nurse":
        abort(403)


def _fmt(iso):
    return datetime.fromisoformat(iso).astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")


def _decorate_entry(entry):
    entry["med"] = medicine_db.lookup(entry["pzn"])
    entry["loaded_at_local"] = _fmt(entry["loaded_at"])
    return entry


def _decorate_event(ev):
    med = medicine_db.lookup(ev["pzn"])
    ev["name"] = med["name"] if med else None
    ev["at_local"] = _fmt(ev["at"])
    return ev


def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _similar(a, b, tolerance=30):
    """Same shape and nearly the same color (RGB distance)."""
    if a["shape"] != b["shape"]:
        return False
    return sum((x - y) ** 2 for x, y in zip(_rgb(a["color"]), _rgb(b["color"]))) <= tolerance ** 2


def lookalike_groups(entries):
    """Groups of different medicines in one compartment that look alike (shape + color)."""
    meds = {e["pzn"]: e["med"] for e in entries if e["med"]}
    groups = []
    for pzn, med in meds.items():
        for group in groups:
            if any(_similar(med, meds[other]) for other in group):
                group.append(pzn)
                break
        else:
            groups.append([pzn])
    return [[meds[p]["name"] for p in g] for g in groups if len(g) > 1]


def _week():
    """Selected calendar week from query/form, defaulting to the current week."""
    return store.parse_week(request.values.get("week")) or store.current_week()


def _cell_url(cell, week):
    return url_for(".index", week=week, day=cell[0], slot=cell[1], open=1)


@bp.route("/")
@patient_required
def index():
    kvnr = g.patient["kvnr"]
    week = _week()
    cells, last_loaded = {}, None
    for e in store.list_entries(kvnr, week):
        cells.setdefault((e["day"], e["slot"]), []).append(_decorate_entry(e))
        last_loaded = e

    events = {}
    for ev in store.list_events(kvnr, week):  # newest first
        events.setdefault((ev["day"], ev["slot"]), []).append(_decorate_event(ev))

    comments = {}
    for c in store.list_comments(kvnr, week):  # newest first
        c["created_local"] = _fmt(c["created_at"])
        comments.setdefault((c["day"], c["slot"]), []).append(c)

    recent_comments = sorted(
        (dict(c, cell=cell, url=_cell_url(cell, week)) for cell, lst in comments.items() for c in lst),
        key=lambda c: (c["created_at"], c["id"]),
        reverse=True,
    )[:5]

    selected = _parse_cell(request.args.get("day"), request.args.get("slot"))
    sel_entries = cells.get(selected, []) if selected else []
    sel_events = events.get(selected, []) if selected else []
    return render_template(
        "medicine_intake.html",
        patient=g.patient,
        active_tab="intake",
        week=week,
        current_week_id=store.current_week(),
        week_options=store.week_options(week),
        week_comments=store.comment_counts_by_week(kvnr),
        is_current_week=week == store.current_week(),
        prev_week=store.week_id(store.week_monday(week) - timedelta(weeks=1)),
        next_week=store.week_id(store.week_monday(week) + timedelta(weeks=1)),
        week_label=store.week_label(week),
        days=store.DAYS,
        slots=store.SLOTS,
        cells=cells,
        comment_counts={cell: len(lst) for cell, lst in comments.items()},
        recent_comments=recent_comments,
        selected=selected,
        open_tile=selected is not None and request.args.get("open") == "1",
        selected_entries=sel_entries,
        current_mod=sel_events[0] if sel_events else None,
        previous_mod=sel_events[1] if len(sel_events) > 1 else None,
        selected_comments=comments.get(selected, []) if selected else [],
        lookalikes=lookalike_groups(sel_entries),
        today=datetime.now(LOCAL_TZ).weekday() if week == store.current_week() else None,
        filled_count=len(cells),
        last_loaded=last_loaded,
        last_loaded_local=_fmt(last_loaded["loaded_at"]) if last_loaded else None,
        catalog=medicine_db.catalog(),
        can_edit=g.hpc["role"] == "nurse",
        max_comment=store.MAX_COMMENT_LEN,
    )


@bp.route("/lookup")
@patient_required
def lookup():
    """JSON: resolve a scanned code / typed PZN to a medicine (used by the tile's scan section)."""
    pzn = store.extract_pzn(request.args.get("code", ""))
    if pzn is None:
        return jsonify(ok=False, error="Not a valid PZN. Check the code and try again.")
    return jsonify(ok=True, pzn=pzn, medicine=medicine_db.lookup(pzn))


@bp.route("/load", methods=["POST"])
@patient_required
def load():
    _nurse_only()
    cell = _parse_cell(request.form.get("day"), request.form.get("slot"))
    week = _week()
    if cell is None:
        return redirect(url_for(".index", week=week))
    pzn = store.extract_pzn(request.form.get("pzn", ""))
    try:
        qty = max(1, min(4, int(request.form.get("qty", "1"))))
    except ValueError:
        qty = 1
    if pzn is None:
        flash("Invalid PZN. Expected 8 digits with a valid check digit.")
    else:
        days = range(len(store.DAYS)) if request.form.get("scope") == "week" else [cell[0]]
        store.add_entries(g.patient["kvnr"], week, [(d, cell[1]) for d in days], pzn, qty, g.hpc["name"])
        where = "all 7 days, " if len(days) > 1 else ""
        flash(f"Saved PZN {pzn} to {where}{store.SLOTS[cell[1]][1].lower()}.", "ok")
    return redirect(_cell_url(cell, week))


@bp.route("/remove", methods=["POST"])
@patient_required
def remove():
    _nurse_only()
    cell = _parse_cell(request.form.get("day"), request.form.get("slot"))
    try:
        store.delete_entry(g.patient["kvnr"], int(request.form.get("entry_id", "")), g.hpc["name"])
    except ValueError:
        pass
    week = _week()
    return redirect(_cell_url(cell, week) if cell else url_for(".index", week=week))


@bp.route("/comment", methods=["POST"])
@patient_required
def comment():
    """Doctor and nurse can both comment on a compartment (dose changes, caregiving notes)."""
    cell = _parse_cell(request.form.get("day"), request.form.get("slot"))
    week = _week()
    if cell is None:
        return redirect(url_for(".index", week=week))
    body = request.form.get("body", "").strip()
    if not body:
        flash("Comment is empty.")
    elif len(body) > store.MAX_COMMENT_LEN:
        flash(f"Comment is too long (max {store.MAX_COMMENT_LEN} characters).")
    else:
        store.add_comment(g.patient["kvnr"], week, cell[0], cell[1], g.hpc["name"], g.hpc["role"], body)
        flash("Comment added.", "ok")
    return redirect(_cell_url(cell, week))
