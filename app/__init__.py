"""MedLink application factory."""
import os
import secrets

from flask import Flask, abort, request, session


def create_app(test_config=None) -> Flask:
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
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    from . import auth, patient, medicine_identification, medicine_intake

    app.register_blueprint(auth.bp)
    app.register_blueprint(patient.bp)
    app.register_blueprint(medicine_identification.bp)
    app.register_blueprint(medicine_intake.bp)
    from .medicine_catalog import bp as catalog_bp, DEFAULT_DB, DATA

    app.config["MEDICINES_DATABASE"] = os.environ.get("MEDLINK_MEDICINES_DB", str(DEFAULT_DB))
    app.config["MEDICINES_IMAGE_ROOT"] = str(DATA / "images")
    app.register_blueprint(catalog_bp)
    from . import prescriptions

    app.config["PRESCRIPTIONS_DATABASE"] = os.environ.get(
        "MEDLINK_PRESCRIPTIONS_DB", os.path.join(app.instance_path, "prescriptions.sqlite3")
    )
    app.config.update(
        ACCOUNTS_DATABASE=os.environ.get("MEDLINK_ACCOUNTS_DB", os.path.join(app.instance_path, "accounts.sqlite3")),
        PENDING_MEDICINES_DATABASE=os.environ.get("MEDLINK_PENDING_DB", os.path.join(app.instance_path, "pending_medicines.sqlite3")),
        DISPENSING_UPLOAD_ROOT=os.path.join(app.instance_path, "dispensing_photos"),
        SEED_DEMO_ACCOUNTS=os.environ.get("MEDLINK_SEED_DEMO", "1") == "1",
        PUBLIC_BASE_URL=os.environ.get("MEDLINK_PUBLIC_BASE_URL", ""),
        MAX_CONTENT_LENGTH=12 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    app.register_blueprint(prescriptions.bp)
    from . import pharmacy
    app.register_blueprint(pharmacy.bp)
    app.cli.add_command(auth.create_user)
    with app.app_context():
        auth.init_accounts()
        prescriptions.init_database()
        pharmacy.init_database()
    return app
