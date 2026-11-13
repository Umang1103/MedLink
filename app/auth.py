"""HPC (healthcare professional) login.

Demo users only. Replace USERS with a real identity provider / database
(e.g. SMC-B / HBA based auth in the German telematics infrastructure).
"""
import functools

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("auth", __name__)

# role "nurse": can load the medicine box. role "doctor": read-only view.
USERS = {
    "dr.demo": {
        "name": "Dr. Demo",
        "role": "doctor",
        "password_hash": generate_password_hash("demo1234"),
    },
    "nurse.weber": {
        "name": "M. Weber",
        "role": "nurse",
        "password_hash": generate_password_hash("demo1234"),
    },
}


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get("hpc") is None:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)

    return wrapped


@bp.before_app_request
def load_user():
    username = session.get("hpc_user")
    g.hpc = {"username": username, **USERS[username]} if username in USERS else None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = USERS.get(username)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["hpc_user"] = username
            return redirect(url_for("patient.lookup"))
        flash("Invalid username or password.")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
