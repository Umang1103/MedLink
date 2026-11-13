"""MedLink application factory."""
import os
import secrets

from flask import Flask, abort, request, session


def create_app() -> Flask:
    app = Flask(__name__)
    # Set MEDLINK_SECRET_KEY in production. A random key is used for dev only
    # (sessions are reset on every restart).
    app.config["SECRET_KEY"] = os.environ.get("MEDLINK_SECRET_KEY") or secrets.token_hex(32)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    @app.before_request
    def csrf_protect():
        if request.method == "POST":
            token = session.get("csrf_token")
            if not token or token != request.form.get("csrf_token"):
                abort(400, "Invalid CSRF token")

    @app.context_processor
    def inject_csrf():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return {"csrf_token": session["csrf_token"]}

    @app.after_request
    def no_cache(resp):
        # Patient data must not be cached by the browser.
        resp.headers["Cache-Control"] = "no-store"
        return resp

    from . import auth, patient, medicine_identification, medicine_intake

    app.register_blueprint(auth.bp)
    app.register_blueprint(patient.bp)
    app.register_blueprint(medicine_identification.bp)
    app.register_blueprint(medicine_intake.bp)
    return app
