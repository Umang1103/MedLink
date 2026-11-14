"""Doctor prescription workflow and separate SQLite patient/prescription storage."""
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
import json

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .auth import doctor_required
from .medicine_catalog import search

bp = Blueprint('prescriptions', __name__, url_prefix='/prescribe')


def connect():
    db = sqlite3.connect(current_app.config['PRESCRIPTIONS_DATABASE'])
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def init_database():
    Path(current_app.config['PRESCRIPTIONS_DATABASE']).parent.mkdir(parents=True, exist_ok=True)
    db = connect()
    try:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS patients (
                kvnr TEXT PRIMARY KEY, name TEXT NOT NULL, birth_date TEXT NOT NULL,
                insurer TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS prescriptions (
                id INTEGER PRIMARY KEY, kvnr TEXT NOT NULL REFERENCES patients(kvnr),
                patient_name TEXT NOT NULL, birth_date TEXT NOT NULL,
                doctor_username TEXT NOT NULL, doctor_name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );
            CREATE TABLE IF NOT EXISTS prescription_items (
                id INTEGER PRIMARY KEY,
                prescription_id INTEGER NOT NULL REFERENCES prescriptions(id),
                pzn TEXT NOT NULL, product_name TEXT NOT NULL,
                active_ingredient TEXT NOT NULL, strength TEXT NOT NULL, dosage_form TEXT NOT NULL,
                units_per_dose TEXT NOT NULL, times_per_day INTEGER NOT NULL,
                instructions TEXT NOT NULL DEFAULT '',
                UNIQUE(prescription_id, pzn)
            );
            CREATE INDEX IF NOT EXISTS idx_prescriptions_patient ON prescriptions(kvnr);
        ''')
    finally:
        db.close()


def stored_patient(kvnr):
    db = connect()
    try:
        row = db.execute('SELECT * FROM patients WHERE kvnr = ?', (kvnr,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def validate(form):
    from .patient import get_patient, is_valid_kvnr
    name = form.get('name', '').strip()
    kvnr = form.get('kvnr', '').strip().upper()
    birth = form.get('birth_date', '')
    if not name or len(name) > 200:
        raise ValueError('Enter a patient name (up to 200 characters).')
    try:
        birthday = date.fromisoformat(birth)
        if birthday > date.today():
            raise ValueError()
    except ValueError:
        raise ValueError('Enter a valid birth date that is not in the future.')
    if not is_valid_kvnr(kvnr):
        raise ValueError('Invalid KVNR: enter one letter and nine digits with a valid check digit.')
    existing = get_patient(kvnr)
    if existing and (existing['name'].casefold() != name.casefold() or existing['birth_date'] != birth):
        raise ValueError('This KVNR already belongs to a patient with a different name or birth date.')
    pzns = [value.zfill(8) if value.isdigit() else value for value in form.getlist('pzn')]
    doses, frequencies, instructions = (form.getlist(k) for k in ('units_per_dose', 'times_per_day', 'instructions'))
    if not pzns or len(pzns) > 50 or not len(pzns) == len(doses) == len(frequencies) == len(instructions):
        raise ValueError('Select between 1 and 50 medicines and complete each dosage.')
    if len(set(pzns)) != len(pzns):
        raise ValueError('Each medicine can only be selected once.')
    items = []
    for pzn, dose, frequency, instruction in zip(pzns, doses, frequencies, instructions):
        result = search(current_app.config['MEDICINES_DATABASE'], filters={'pzn': pzn}, limit=1)
        if not result['results']:
            raise ValueError('A selected medicine is no longer available. Please select it again.')
        medicine = result['results'][0]
        try:
            amount = Decimal(dose)
            daily = int(frequency)
            if not amount.is_finite() or amount <= 0 or amount > 1000 or amount.as_tuple().exponent < -3 or not 1 <= daily <= 24:
                raise ValueError()
        except (ValueError, InvalidOperation):
            raise ValueError('Units per dose must be positive (up to 1000, at most 3 decimals); times per day must be an integer from 1 to 24.')
        if len(instruction) > 1000:
            raise ValueError('Additional instructions must be no longer than 1000 characters.')
        items.append((medicine, str(amount), daily, instruction.strip()))
    return {'kvnr': kvnr, 'name': name, 'birth_date': birth, 'insurer': existing.get('insurer', '') if existing else ''}, items


@bp.route('/', methods=['GET', 'POST'])
@doctor_required
def index():
    values = request.form if request.method == 'POST' else {}
    error = None
    if request.method == 'POST':
        try:
            patient, items = validate(request.form)
            db = connect()
            try:
                with db:
                    # Recheck identity under the write lock before inserting the prescription.
                    db.execute('BEGIN IMMEDIATE')
                    old = db.execute('SELECT * FROM patients WHERE kvnr = ?', (patient['kvnr'],)).fetchone()
                    if old and (old['name'].casefold() != patient['name'].casefold() or old['birth_date'] != patient['birth_date']):
                        raise ValueError('This KVNR already belongs to a different patient.')
                    db.execute('INSERT OR IGNORE INTO patients (kvnr, name, birth_date, insurer) VALUES (?, ?, ?, ?)',
                               (patient['kvnr'], patient['name'], patient['birth_date'], patient['insurer']))
                    cursor = db.execute('''INSERT INTO prescriptions
                        (kvnr, patient_name, birth_date, doctor_username, doctor_name) VALUES (?, ?, ?, ?, ?)''',
                        (patient['kvnr'], patient['name'], patient['birth_date'], g.hpc['username'], g.hpc['name']))
                    prescription_id = cursor.lastrowid
                    db.executemany('''INSERT INTO prescription_items
                        (prescription_id, pzn, product_name, active_ingredient, strength, dosage_form,
                         units_per_dose, times_per_day, instructions) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        [(prescription_id, m['pzn'], m['product_name'], m['active_ingredient'], m['strength_as_stated'],
                          m['dosage_form'], dose, daily, note) for m, dose, daily, note in items])
            finally:
                db.close()
            session['kvnr'] = patient['kvnr']
            flash('Prescription created and saved.')
            return redirect(url_for('prescriptions.detail', prescription_id=prescription_id))
        except ValueError as exc:
            error = str(exc)
        except FileNotFoundError:
            error = 'Medicine catalogue unavailable. Please build the medicine database first.'
    # Restore selected medicines and entered dosages after a validation error.
    selected = []
    if request.method == 'POST':
        for pzn, dose, daily, note in zip(*(request.form.getlist(k) for k in ('pzn', 'units_per_dose', 'times_per_day', 'instructions'))):
            try:
                found = search(current_app.config['MEDICINES_DATABASE'], filters={'pzn': pzn}, limit=1)['results']
            except FileNotFoundError:
                found = []
            if found:
                selected.append(dict(found[0], units_per_dose=dose, times_per_day=daily, instructions=note))
    return render_template('prescribe.html', values=values, error=error, selected=selected)


@bp.get('/history')
@doctor_required
def history():
    db = connect()
    try:
        prescriptions = db.execute('SELECT * FROM prescriptions WHERE doctor_username = ? ORDER BY id DESC', (g.hpc['username'],)).fetchall()
    finally:
        db.close()
    return render_template('prescription_history.html', prescriptions=prescriptions)


@bp.get('/<int:prescription_id>')
@doctor_required
def detail(prescription_id):
    db = connect()
    try:
        prescription = db.execute('SELECT * FROM prescriptions WHERE id = ? AND doctor_username = ?', (prescription_id, g.hpc['username'])).fetchone()
        if prescription is None:
            abort(404)
        context = prescription_view(db, prescription)
    finally:
        db.close()
    return render_template('prescription_detail.html', **context)


def prescription_view(db, prescription):
    """Build the shared doctor view after the caller has checked prescription ownership."""
    items = db.execute('SELECT * FROM prescription_items WHERE prescription_id = ? ORDER BY id', (prescription['id'],)).fetchall()
    from .pharmacy import final_items, catalogue_medicine
    dispensed = {item['item_id']: item for item in final_items(db, prescription['id'])}
    dispensing = db.execute('SELECT * FROM dispensing WHERE prescription_id = ?', (prescription['id'],)).fetchone()
    history = [dict(row, medicines=json.loads(row['items_json'])) for row in db.execute(
        'SELECT * FROM dispensing_history WHERE prescription_id = ? ORDER BY id DESC', (prescription['id'],))]
    if history and dispensing and not dispensing['completed_at']:
        dispensed = {item['item_id']: dict(item, history_id=history[0]['id']) for item in history[0]['medicines']}
    original_images = {item['id']: (catalogue_medicine(item['pzn']) or {}).get('images', []) for item in items}
    return dict(prescription=prescription, items=items, dispensed=dispensed, dispensing=dispensing, history=history, original_images=original_images)
