"""Tab 2: Medicine Intake Supervision. Add your routes/logic here."""
from flask import Blueprint, g, render_template

from .patient import patient_required

bp = Blueprint("medicine_intake", __name__, url_prefix="/intake")


@bp.route("/")
@patient_required
def index():
    # TODO: implement intake supervision (g.patient holds the current patient).
    return render_template("medicine_intake.html", patient=g.patient, active_tab="intake")
