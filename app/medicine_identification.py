"""Tab 1: Medicine Identification. Add your routes/logic here."""
from flask import Blueprint, g, render_template

from .patient import patient_required

bp = Blueprint("medicine_identification", __name__, url_prefix="/identification")


@bp.route("/")
@patient_required
def index():
    # TODO: implement medicine identification (g.patient holds the current patient).
    return render_template("medicine_identification.html", patient=g.patient, active_tab="identification")
