"""Role-based login backed by a separate SQLite account database."""
import functools
import hashlib
import sqlite3
from pathlib import Path

import click
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask.cli import with_appcontext
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint('auth', __name__)


def accounts():
    db = sqlite3.connect(current_app.config['ACCOUNTS_DATABASE'])
    db.row_factory = sqlite3.Row
    return db


def password_hash(password):
    return generate_password_hash(password, method='scrypt' if hasattr(hashlib, 'scrypt') else 'pbkdf2:sha256')


def init_accounts():
    Path(current_app.config['ACCOUNTS_DATABASE']).parent.mkdir(parents=True, exist_ok=True)
    db = accounts()
    try:
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY, name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('doctor', 'pharmacy')),
                password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)''')
            if current_app.config['SEED_DEMO_ACCOUNTS']:
                for username, name, role, password in (
                    ('dr.demo', 'Dr. Demo', 'doctor', 'demo1234'),
                    ('pharmacy.demo', 'Demo Pharmacy', 'pharmacy', 'pharmacy1234')):
                    if not db.execute('SELECT 1 FROM users WHERE username = ?', (username,)).fetchone():
                        db.execute('INSERT INTO users (username, name, role, password_hash) VALUES (?, ?, ?, ?)',
                                   (username, name, role, password_hash(password)))
    finally:
        db.close()


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.get('hpc') is None:
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorate(view):
        @functools.wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.hpc['role'] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorate


doctor_required = roles_required('doctor')
pharmacy_required = roles_required('pharmacy')


@bp.before_app_request
def load_user():
    username = session.get('hpc_user')
    g.hpc = None
    if username:
        db = accounts()
        try:
            row = db.execute('SELECT username, name, role FROM users WHERE username = ? AND active = 1', (username,)).fetchone()
            g.hpc = dict(row) if row else None
        finally:
            db.close()


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        db = accounts()
        try:
            user = db.execute('SELECT * FROM users WHERE username = ? AND active = 1', (username,)).fetchone()
        finally:
            db.close()
        if user and check_password_hash(user['password_hash'], request.form.get('password', '')):
            session.clear()
            session['hpc_user'] = username
            return redirect(url_for('pharmacy.lookup' if user['role'] == 'pharmacy' else 'patient.home'))
        flash('Invalid username or password.')
    return render_template('login.html')


@bp.route('/logout', methods=['POST'])
@login_required
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


@click.command('create-user')
@click.option('--username', prompt=True)
@click.option('--name', prompt=True)
@click.option('--role', type=click.Choice(['doctor', 'pharmacy']), prompt=True)
@click.password_option(confirmation_prompt=True)
@with_appcontext
def create_user(username, name, role, password):
    """Create a doctor/pharmacy account; password is entered without echo."""
    if len(password) < 10 or not username.strip() or not name.strip():
        raise click.ClickException('Use a nonempty username/name and a password of at least 10 characters.')
    db = accounts()
    try:
        with db:
            db.execute('INSERT INTO users (username, name, role, password_hash) VALUES (?, ?, ?, ?)',
                       (username.strip(), name.strip(), role, password_hash(password)))
    except sqlite3.IntegrityError:
        raise click.ClickException('Username already exists.')
    finally:
        db.close()
    click.echo('Account created.')
