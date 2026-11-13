"""Patient lookup by KVNR (Krankenversichertennummer) from the German health insurance card."""
import functools
import re

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from .auth import login_required

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
    p = PATIENTS.get(kvnr)
    return {"kvnr": kvnr, **p} if p else None


def patient_required(view):
    """Requires login and a selected patient; sets g.patient."""

    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        g.patient = get_patient(session.get("kvnr", ""))
        if g.patient is None:
            return redirect(url_for("patient.lookup"))
        return view(*args, **kwargs)

    return wrapped


@bp.route("/", methods=["GET", "POST"])
@login_required
def lookup():
    if request.method == "POST":
        kvnr = request.form.get("kvnr", "").strip().upper()
        if not is_valid_kvnr(kvnr):
            flash("Invalid patient ID. Expected 1 letter followed by 9 digits.")
        elif get_patient(kvnr) is None:
            flash("No patient found for this ID.")
        else:
            session["kvnr"] = kvnr
            return redirect(url_for("medicine_identification.index"))
    return render_template("lookup.html")


@bp.route("/patient/close", methods=["POST"])
@login_required
def close():
    session.pop("kvnr", None)
    return redirect(url_for("patient.lookup"))
