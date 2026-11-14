"""Tab 1: Medicine Identification. Add your routes/logic here."""
from flask import Blueprint, g, render_template

from .patient import patient_required

bp = Blueprint("medicine_identification", __name__, url_prefix="/identification")


@bp.route("/")
@patient_required
def index():
    from .prescriptions import connect, prescription_view

    db = connect()
    try:
        prescriptions = db.execute(
            'SELECT * FROM prescriptions WHERE kvnr = ? AND doctor_username = ? ORDER BY id DESC',
            (g.patient['kvnr'], g.hpc['username']),
        ).fetchall()
        records = [prescription_view(db, row) for row in prescriptions]
    finally:
        db.close()
    return render_template("medicine_identification.html", patient=g.patient,
                           active_tab="identification", records=records)
