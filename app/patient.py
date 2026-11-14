"""Patient lookup by KVNR (Krankenversichertennummer) from the German health insurance card."""
import functools
import re

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .auth import doctor_required

bp = Blueprint("patient", __name__)

KVNR_RE = re.compile(r"^[A-Z][0-9]{9}$")

# Demo data. Replace with a real data source. All KVNRs below pass the checksum.
PATIENTS = {
    "A123456780": {"name": "Erika Mustermann", "birth_date": "1964-08-12", "insurer": "Demo-Krankenkasse"},
    "K987654329": {"name": "Max Beispiel", "birth_date": "1989-03-02", "insurer": "Demo-Krankenkasse"},
}


def is_valid_kvnr(kvnr: str) -> bool:
    """Format check plus check digit (letter -> 2 digits, weights 1/2, cross sum, mod 10)."""
    if not KVNR_RE.match(kvnr):
        return False
    digits = f"{ord(kvnr[0]) - 64:02d}" + kvnr[1:9]
    total = 0
    for i, ch in enumerate(digits):
        p = int(ch) * (1 if i % 2 == 0 else 2)
        total += p // 10 + p % 10
    return total % 10 == int(kvnr[9])


def get_patient(kvnr: str):
    from .prescriptions import stored_patient

    p = stored_patient(kvnr) or PATIENTS.get(kvnr)
    return {"kvnr": kvnr, **p} if p else None


def patient_required(view):
    """Requires login and a selected patient; sets g.patient."""

    @functools.wraps(view)
    @doctor_required
    def wrapped(*args, **kwargs):
        g.patient = get_patient(session.get("kvnr", ""))
        if g.patient is None:
            return redirect(url_for("patient.lookup"))
        return view(*args, **kwargs)

    return wrapped


@bp.get("/")
@doctor_required
def home():
    return render_template("doctor_home.html")


@bp.route("/", methods=["POST"])
@bp.route("/patients", methods=["GET", "POST"])
@doctor_required
def lookup():
    matches = []
    query = request.form.get("q", request.form.get("kvnr", "")).strip()
    if request.method == "POST":
        kvnr = request.form.get("kvnr", "").strip().upper()
        opening = request.form.get("action") == "open" or (kvnr and not request.form.get("q"))
        if opening or KVNR_RE.fullmatch(query.upper()):
            kvnr = kvnr if opening else query.upper()
            if not is_valid_kvnr(kvnr):
                flash("Invalid patient ID. Expected a valid KVNR including its check digit.")
            elif get_patient(kvnr) is None:
                flash("No patient found for this ID.")
            else:
                session["kvnr"] = kvnr
                return redirect(url_for("medicine_identification.index"))
        elif not query or len(query) > 200:
            flash("Enter a patient name or KVNR (up to 200 characters).")
        else:
            from .prescriptions import connect
            db = connect()
            try:
                patients = {key: dict(value, kvnr=key) for key, value in PATIENTS.items()}
                patients.update({row['kvnr']: dict(row) for row in db.execute('SELECT * FROM patients')})
            finally:
                db.close()
            needle = " ".join(query.split()).casefold()
            matches = sorted((p for p in patients.values() if needle in " ".join(p['name'].split()).casefold()),
                             key=lambda p: (p['name'].casefold(), p['birth_date'], p['kvnr']))
            if not matches:
                flash("No patient found. Check the name or KVNR.")
    return render_template("lookup.html", matches=matches, query=query)


@bp.get("/patient-version")
@patient_required
def patient_version():
    from .prescriptions import connect
    from .pharmacy import patient_items
    db = connect()
    records = []
    try:
        prescriptions = db.execute('SELECT * FROM prescriptions WHERE kvnr = ? AND doctor_username = ? ORDER BY id DESC',
                                   (g.patient['kvnr'], g.hpc['username'])).fetchall()
        for prescription in prescriptions:
            state = db.execute('SELECT * FROM dispensing WHERE prescription_id = ?', (prescription['id'],)).fetchone()
            refill = bool(state and not state['completed_at'])
            completed = state if state and state['completed_at'] else db.execute(
                'SELECT * FROM dispensing_history WHERE prescription_id = ? ORDER BY id DESC LIMIT 1',
                (prescription['id'],)).fetchone()
            records.append(dict(prescription=prescription, refill=refill, completed=completed,
                                medicines=patient_items(db, completed) if completed else []))
    finally:
        db.close()
    return render_template('doctor_patient_version.html', patient=g.patient, active_tab='patient_version', records=records)


@bp.route("/patient/close", methods=["POST"])
@doctor_required
def close():
    session.pop("kvnr", None)
    return redirect(url_for("patient.lookup"))
