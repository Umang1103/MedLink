"""Pharmacy dispensing, supplemental medicine review queue, and patient capability links."""
from io import BytesIO
import json
from pathlib import Path
import re
import secrets
import sqlite3

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
import qrcode
import qrcode.image.svg

from .auth import pharmacy_required
from .medicine_catalog import search
from .prescriptions import connect

bp = Blueprint('pharmacy', __name__)

# Only identification information for the actual dispensed product is shared.
PATIENT_DETAIL_FIELDS = (
    'ingredient_form_as_stated', 'color', 'shape', 'imprint', 'score_line',
    'appearance_description_original', 'size_description', 'brand', 'pack_size',
    'manufacturer', 'pharmaceutical_company',
)


def medicine_details(medicine):
    return {key: medicine[key] for key in PATIENT_DETAIL_FIELDS if medicine and medicine.get(key)}



def init_database():
    pending = Path(current_app.config['PENDING_MEDICINES_DATABASE'])
    pending.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(pending))
    try:
        with db:
            # Deliberately contains no patient names, KVNRs or prescription IDs.
            db.execute('''CREATE TABLE IF NOT EXISTS pending_medicines (
                id INTEGER PRIMARY KEY, pzn TEXT NOT NULL, product_name TEXT NOT NULL,
                active_ingredient TEXT NOT NULL, strength TEXT NOT NULL, dosage_form TEXT NOT NULL,
                description TEXT NOT NULL, indication TEXT NOT NULL, photo TEXT NOT NULL,
                submitted_by TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'pending')''')
    finally:
        db.close()
    db = connect()
    try:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS dispensing (
                prescription_id INTEGER PRIMARY KEY REFERENCES prescriptions(id),
                pharmacy_username TEXT NOT NULL, completed_at TEXT,
                patient_token TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS dispensing_drafts (
                item_id INTEGER NOT NULL REFERENCES prescription_items(id),
                pharmacy_username TEXT NOT NULL, fields_json TEXT NOT NULL,
                photo TEXT NOT NULL DEFAULT '', PRIMARY KEY(item_id, pharmacy_username)
            );
            CREATE TABLE IF NOT EXISTS dispensing_history (
                id INTEGER PRIMARY KEY, prescription_id INTEGER NOT NULL REFERENCES prescriptions(id),
                pharmacy_username TEXT NOT NULL, completed_at TEXT NOT NULL,
                patient_token TEXT UNIQUE NOT NULL, items_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dispensed_items (
                item_id INTEGER PRIMARY KEY REFERENCES prescription_items(id),
                prescription_id INTEGER NOT NULL REFERENCES prescriptions(id),
                pzn TEXT NOT NULL, product_name TEXT NOT NULL, active_ingredient TEXT NOT NULL,
                strength TEXT NOT NULL, dosage_form TEXT NOT NULL,
                description TEXT NOT NULL, indication TEXT NOT NULL,
                images_json TEXT NOT NULL, photo TEXT NOT NULL,
                substituted INTEGER NOT NULL, replacement_reason TEXT NOT NULL,
                pharmacy_username TEXT NOT NULL, confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        if 'medicine_details_json' not in {row[1] for row in db.execute('PRAGMA table_info(dispensed_items)')}:
            db.execute("ALTER TABLE dispensed_items ADD COLUMN medicine_details_json TEXT NOT NULL DEFAULT ''")
            db.commit()
    finally:
        db.close()
    Path(current_app.config['DISPENSING_UPLOAD_ROOT']).mkdir(parents=True, exist_ok=True)


def parse_pzn(raw):
    """Accept explicit PZN, scanner's -PZN, or the 4150-prefixed EAN representation.

    Unknown GTIN/PPN encodings are rejected rather than guessing a product number.
    """
    raw = raw.strip()
    match = re.fullmatch(r'(?:PZN[\s:-]*|-)?([0-9]{8})', raw, re.IGNORECASE)
    if match:
        return match.group(1)
    if re.fullmatch(r'4150[0-9]{9}', raw):
        checksum = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(raw[:12])) % 10) % 10
        if checksum == int(raw[-1]):
            return raw[4:12]
    raise ValueError('Enter an 8-digit PZN or a supported PZN barcode. For other barcode formats, enter the printed PZN manually.')


def authorized_prescription(db, prescription_id):
    row = db.execute('SELECT * FROM prescriptions WHERE id = ?', (prescription_id,)).fetchone()
    if row is None or row['kvnr'] != session.get('pharmacy_kvnr'):
        abort(404)
    return row


def final_items(db, prescription_id):
    rows = db.execute('''SELECT d.*, i.units_per_dose, i.times_per_day, i.instructions
        FROM prescription_items i JOIN dispensed_items d ON d.item_id = i.id
        WHERE i.prescription_id = ? ORDER BY i.id''', (prescription_id,)).fetchall()
    return [dict(row, images=json.loads(row['images_json'])) for row in rows]


def catalogue_medicine(pzn):
    try:
        result = search(current_app.config['MEDICINES_DATABASE'], filters={'pzn': pzn}, limit=1)['results']
        return result[0] if result else None
    except FileNotFoundError:
        return None


def save_photo(upload):
    if not upload or not upload.filename:
        return ''
    try:
        with Image.open(upload.stream) as image:
            if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 20_000_000:
                raise ValueError('Use a JPEG, PNG or WebP photo up to 20 megapixels.')
            image = ImageOps.exif_transpose(image).convert('RGB')
            image.thumbnail((1600, 1600))
            filename = secrets.token_hex(20) + '.jpg'
            image.save(Path(current_app.config['DISPENSING_UPLOAD_ROOT']) / filename, 'JPEG', quality=90)
            return filename
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('Invalid image. Please upload a JPEG, PNG or WebP photo.')


@bp.route('/pharmacy/', methods=['GET', 'POST'])
@pharmacy_required
def lookup():
    prescriptions = []
    if request.method == 'POST':
        session.pop('pharmacy_kvnr', None)
        from .patient import is_valid_kvnr
        kvnr = request.form.get('kvnr', '').strip().upper()
        name = ' '.join(request.form.get('name', '').split()).casefold()
        birth = request.form.get('birth_date', '')
        db = connect()
        try:
            patient = db.execute('SELECT * FROM patients WHERE kvnr = ?', (kvnr,)).fetchone()
            if is_valid_kvnr(kvnr) and patient and ' '.join(patient['name'].split()).casefold() == name and patient['birth_date'] == birth:
                session['pharmacy_kvnr'] = kvnr
            else:
                flash('Patient details did not match. Check KVNR, full name and birth date.')
        finally:
            db.close()
    if session.get('pharmacy_kvnr'):
        db = connect()
        try:
            prescriptions = db.execute('''SELECT p.*, d.completed_at FROM prescriptions p
                LEFT JOIN dispensing d ON d.prescription_id = p.id WHERE p.kvnr = ? ORDER BY p.id DESC''',
                (session['pharmacy_kvnr'],)).fetchall()
        finally:
            db.close()
    return render_template('pharmacy_lookup.html', prescriptions=prescriptions)


def stage_draft(db, items, prescription_id):
    """Keep incomplete form data and validated images independently of dispensing validation."""
    item = next((i for i in items if str(i['id']) == request.form.get('item_id')), None)
    state = db.execute('SELECT * FROM dispensing WHERE prescription_id = ?', (prescription_id,)).fetchone()
    if not item or (state and (state['completed_at'] or state['pharmacy_username'] != g.hpc['username'])):
        return '', None
    previous = db.execute('SELECT photo FROM dispensing_drafts WHERE item_id = ? AND pharmacy_username = ?',
                          (item['id'], g.hpc['username'])).fetchone()
    photo = previous['photo'] if previous else ''
    error = None
    try:
        photo = save_photo(request.files.get('photo')) or photo
    except ValueError as exc:
        error = str(exc)
    fields = {key: request.form.get(key, '') for key in (
        'pzn', 'product_name', 'active_ingredient', 'strength', 'dosage_form',
        'description', 'indication', 'reason', 'equivalence_confirmed')}
    with db:
        db.execute("""INSERT INTO dispensing_drafts (item_id, pharmacy_username, fields_json, photo)
            VALUES (?, ?, ?, ?) ON CONFLICT(item_id, pharmacy_username) DO UPDATE SET
            fields_json=excluded.fields_json, photo=excluded.photo""",
            (item['id'], g.hpc['username'], json.dumps(fields), photo))
    return photo, error


@bp.get('/pharmacy/draft/<int:item_id>/photo')
@pharmacy_required
def draft_photo(item_id):
    db = connect()
    try:
        row = db.execute("""SELECT d.photo, i.prescription_id FROM dispensing_drafts d
            JOIN prescription_items i ON i.id = d.item_id
            WHERE d.item_id = ? AND d.pharmacy_username = ?""", (item_id, g.hpc['username'])).fetchone()
        if not row or not row['photo']:
            abort(404)
        authorized_prescription(db, row['prescription_id'])
        root = Path(current_app.config['DISPENSING_UPLOAD_ROOT']).resolve()
        filename = (root / row['photo']).resolve()
        if not filename.is_relative_to(root) or not filename.is_file():
            abort(404)
        return send_file(filename)
    finally:
        db.close()


@bp.route('/pharmacy/<int:prescription_id>', methods=['GET', 'POST'])
@pharmacy_required
def dispense(prescription_id):
    db = connect()
    photo = ''
    try:
        prescription = authorized_prescription(db, prescription_id)
        items = db.execute('SELECT * FROM prescription_items WHERE prescription_id = ? ORDER BY id', (prescription_id,)).fetchall()
        if request.method == 'POST':
            draft_error = None
            if request.form.get('action') == 'confirm':
                photo, draft_error = stage_draft(db, items, prescription_id)
            try:
                if draft_error:
                    raise ValueError(draft_error)
                db.execute('ATTACH DATABASE ? AS review', (current_app.config['PENDING_MEDICINES_DATABASE'],))
                with db:
                    db.execute('BEGIN IMMEDIATE')
                    state = db.execute('SELECT * FROM dispensing WHERE prescription_id = ?', (prescription_id,)).fetchone()
                    if state and state['completed_at']:
                        raise ValueError('This prescription has already been finalized and cannot be changed.')
                    if state and state['pharmacy_username'] != g.hpc['username']:
                        raise ValueError('Another pharmacy is already dispensing this prescription.')
                    db.execute('INSERT OR IGNORE INTO dispensing (prescription_id, pharmacy_username) VALUES (?, ?)',
                               (prescription_id, g.hpc['username']))
                    if request.form.get('action') == 'finalize':
                        count = db.execute('SELECT count(*) FROM dispensed_items WHERE prescription_id = ?', (prescription_id,)).fetchone()[0]
                        if count != len(items) or not items:
                            raise ValueError('Confirm every medicine before generating the final list.')
                        db.execute('''UPDATE dispensing SET completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                                      patient_token = ? WHERE prescription_id = ?''', (secrets.token_urlsafe(32), prescription_id))
                    else:
                        if request.form.get('action') != 'confirm':
                            raise ValueError('Invalid dispensing action.')
                        item = next((i for i in items if str(i['id']) == request.form.get('item_id')), None)
                        if item is None:
                            raise ValueError('Invalid prescription item.')
                        pzn = parse_pzn(request.form.get('pzn', ''))
                        replacement = pzn != item['pzn']
                        reason = request.form.get('reason', '').strip()
                        if replacement and (not reason or request.form.get('equivalence_confirmed') != 'yes'):
                            raise ValueError('For a substitute, provide the reason and confirm ingredient, strength, formulation and dosing suitability.')
                        medicine = catalogue_medicine(pzn)
                        name = medicine['product_name'] if medicine else request.form.get('product_name', '').strip()
                        ingredient = medicine['active_ingredient'] if medicine else request.form.get('active_ingredient', '').strip()
                        strength = medicine['strength_as_stated'] if medicine else request.form.get('strength', '').strip()
                        dosage_form = medicine['dosage_form'] if medicine else request.form.get('dosage_form', '').strip()
                        description = request.form.get('description', '').strip()
                        indication = request.form.get('indication', '').strip()
                        if not name:
                            raise ValueError('For an unknown PZN, enter the dispensed medicine name.')
                        if replacement and not ingredient:
                            raise ValueError('Enter the active ingredient for the substitute medicine.')
                        for value in (name, ingredient, strength, dosage_form, description, indication, reason):
                            if len(value) > 2000:
                                raise ValueError('Each text field must be at most 2000 characters.')
                        if replacement and ingredient.casefold().strip() != item['active_ingredient'].casefold().strip():
                            raise ValueError('The substitute must have the same active ingredient as prescribed. Resolve differing ingredient names with the doctor.')
                        saved_photo = photo
                        if not saved_photo:
                            previous = db.execute('SELECT photo FROM dispensed_items WHERE item_id = ? AND pzn = ?', (item['id'], pzn)).fetchone()
                            saved_photo = previous['photo'] if previous else ''
                        images = [{'path': i['path'], 'id': i['id']} for i in medicine['images']] if medicine else []
                        missing_information = not medicine or not images
                        if missing_information and not (description or saved_photo):
                            raise ValueError('For a medicine missing catalogue information or images, add a description or a photo.')
                        db.execute('''INSERT INTO dispensed_items (item_id, prescription_id, pzn, product_name,
                            active_ingredient, strength, dosage_form, description, indication, images_json, photo,
                            substituted, replacement_reason, pharmacy_username, medicine_details_json)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(item_id) DO UPDATE SET pzn=excluded.pzn, product_name=excluded.product_name,
                            active_ingredient=excluded.active_ingredient, strength=excluded.strength,
                            dosage_form=excluded.dosage_form, description=excluded.description, indication=excluded.indication,
                            images_json=excluded.images_json, photo=excluded.photo, substituted=excluded.substituted,
                            replacement_reason=excluded.replacement_reason, pharmacy_username=excluded.pharmacy_username,
                            medicine_details_json=excluded.medicine_details_json,
                            confirmed_at=CURRENT_TIMESTAMP''',
                            (item['id'], prescription_id, pzn, name, ingredient, strength, dosage_form, description,
                             indication, json.dumps(images), saved_photo, int(replacement), reason if replacement else '', g.hpc['username'], json.dumps(medicine_details(medicine))))
                        if missing_information or description or saved_photo or indication:
                            db.execute('''INSERT INTO review.pending_medicines
                                (pzn, product_name, active_ingredient, strength, dosage_form, description, indication, photo, submitted_by)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                                (pzn, name, ingredient, strength, dosage_form, description, indication, saved_photo, g.hpc['username']))
                    if request.form.get('action') == 'confirm':
                        db.execute('DELETE FROM dispensing_drafts WHERE item_id = ? AND pharmacy_username = ?',
                                   (request.form['item_id'], g.hpc['username']))
                flash('Final medicine list generated.' if request.form.get('action') == 'finalize' else 'Medicine confirmed.')
                return redirect(url_for('pharmacy.dispense', prescription_id=prescription_id))
            except ValueError as error:
                flash(str(error))
        state = db.execute('SELECT * FROM dispensing WHERE prescription_id = ?', (prescription_id,)).fetchone()
        dispensed = final_items(db, prescription_id)
        confirmed = {i['item_id']: i for i in dispensed}
        drafts = {row['item_id']: {'fields': json.loads(row['fields_json']), 'photo': row['photo']} for row in db.execute(
            '''SELECT d.* FROM dispensing_drafts d JOIN prescription_items i ON i.id = d.item_id
               WHERE i.prescription_id = ? AND d.pharmacy_username = ?''', (prescription_id, g.hpc['username']))}
        patient_url = patient_link(state['patient_token']) if state and state['completed_at'] else None
        return render_template('pharmacy_dispense.html', prescription=prescription, items=items, confirmed=confirmed,
                               state=state, final=dispensed, patient_url=patient_url, drafts=drafts)
    finally:
        db.close()


def patient_link(token):
    base = current_app.config['PUBLIC_BASE_URL'].rstrip('/')
    path = url_for('pharmacy.patient_list', token=token)
    return base + path if base else url_for('pharmacy.patient_list', token=token, _external=True)


def shared_state(db, token):
    row = db.execute('SELECT * FROM dispensing WHERE patient_token = ? AND completed_at IS NOT NULL', (token,)).fetchone()
    if row is None:
        row = db.execute('SELECT * FROM dispensing_history WHERE patient_token = ?', (token,)).fetchone()
    if row is None:
        abort(404)
    return row


def shared_items(db, state):
    return json.loads(state['items_json']) if 'items_json' in state.keys() else final_items(db, state['prescription_id'])


@bp.get('/medications/<token>')
def patient_list(token):
    db = connect()
    try:
        state = shared_state(db, token)
        items = patient_items(db, state)
    finally:
        db.close()
    return render_template('patient_medications.html', items=items, token=token, patient_share=True)


@bp.get('/medications/<token>/qr.svg')
def patient_qr(token):
    db = connect()
    try:
        shared_state(db, token)
    finally:
        db.close()
    # Entirely local generation: the patient link is never sent to a QR service.
    qr = qrcode.make(patient_link(token), image_factory=qrcode.image.svg.SvgPathImage)
    output = BytesIO()
    qr.save(output)
    output.seek(0)
    return send_file(output, mimetype='image/svg+xml')


@bp.get('/dispensing-media/<int:item_id>/<int:index>')
@bp.get('/medications/<token>/media/<int:item_id>/<int:index>')
def media(item_id, index, token=None):
    db = connect()
    try:
        if token:
            state = shared_state(db, token)
            row = next((item for item in shared_items(db, state) if item['item_id'] == item_id), None)
            if row is None:
                abort(404)
        else:
            row = db.execute('SELECT * FROM dispensed_items WHERE item_id = ?', (item_id,)).fetchone()
            if row is None and request.args.get('history'):
                row = db.execute('SELECT prescription_id FROM prescription_items WHERE id = ?', (item_id,)).fetchone()
            if row is None:
                abort(404)
            if not g.get('hpc'):
                abort(403)
            prescription = db.execute('SELECT * FROM prescriptions WHERE id = ?', (row['prescription_id'],)).fetchone()
            if g.hpc['role'] == 'doctor':
                if prescription['doctor_username'] != g.hpc['username']:
                    abort(404)
            elif g.hpc['role'] == 'pharmacy':
                authorized_prescription(db, row['prescription_id'])
            else:
                abort(403)
        if not token and request.args.get('history'):
            archived = db.execute('SELECT items_json FROM dispensing_history WHERE id = ? AND prescription_id = ?',
                                  (request.args['history'], row['prescription_id'])).fetchone()
            if archived is None:
                abort(404)
            row = next((item for item in json.loads(archived['items_json']) if item['item_id'] == item_id), None)
            if row is None:
                abort(404)
        if index == 0 and row['photo']:
            root = Path(current_app.config['DISPENSING_UPLOAD_ROOT']).resolve()
            filename = root / row['photo']
        else:
            images = json.loads(row['images_json'])
            try:
                path = images[index - 1]['path'] if index > 0 else None
            except IndexError:
                abort(404)
            if not path:
                abort(404)
            root = Path(current_app.config['MEDICINES_IMAGE_ROOT']).resolve()
            filename = (root / path).resolve()
        if not filename.is_relative_to(root) or not filename.is_file():
            abort(404)
        return send_file(filename)
    finally:
        db.close()


@bp.post('/pharmacy/<int:prescription_id>/refill')
@pharmacy_required
def refill(prescription_id):
    db = connect()
    try:
        authorized_prescription(db, prescription_id)
        with db:
            db.execute('BEGIN IMMEDIATE')
            state = db.execute('SELECT * FROM dispensing WHERE prescription_id = ?', (prescription_id,)).fetchone()
            if not state or not state['completed_at']:
                abort(409, description='Only a completed dispensing can be refilled.')
            db.execute("""INSERT INTO dispensing_history
                (prescription_id, pharmacy_username, completed_at, patient_token, items_json)
                VALUES (?, ?, ?, ?, ?)""",
                (prescription_id, state['pharmacy_username'], state['completed_at'], state['patient_token'],
                 json.dumps(final_items(db, prescription_id))))
            db.execute('DELETE FROM dispensed_items WHERE prescription_id = ?', (prescription_id,))
            db.execute('UPDATE dispensing SET completed_at = NULL, patient_token = NULL, pharmacy_username = ? WHERE prescription_id = ?',
                       (g.hpc['username'], prescription_id))
        flash('Refill started. Confirm every medicine again before generating the new list.')
        return redirect(url_for('pharmacy.dispense', prescription_id=prescription_id))
    finally:
        db.close()


def patient_items(db, state):
    """Final medicines only, without original prescription or substitution reasons."""
    results = []
    for item in shared_items(db, state):
        result = {key: item[key] for key in (
            'item_id', 'pzn', 'product_name', 'active_ingredient', 'strength', 'dosage_form',
            'description', 'indication', 'photo', 'images', 'units_per_dose', 'times_per_day',
            'instructions', 'substituted')}
        snapshot = item.get('medicine_details_json')
        # Legacy dispensing records have no snapshot: enrich by the actual PZN only.
        details = json.loads(snapshot) if snapshot else medicine_details(catalogue_medicine(item['pzn']))
        result['details'] = {key: details[key] for key in PATIENT_DETAIL_FIELDS if details.get(key)}
        results.append(result)
    return results


@bp.get('/pharmacy/<int:prescription_id>/print/<kind>')
@pharmacy_required
def print_dispensing(prescription_id, kind):
    if kind not in ('qr', 'list'):
        abort(404)
    db = connect()
    try:
        authorized_prescription(db, prescription_id)
        state = db.execute('SELECT * FROM dispensing WHERE prescription_id = ?', (prescription_id,)).fetchone()
        if not state or not state['completed_at']:
            abort(409, description='Complete dispensing before printing.')
        items = patient_items(db, state) if kind == 'list' else []
        return render_template('dispensing_print.html', kind=kind, token=state['patient_token'],
                               items=items, patient_url=patient_link(state['patient_token']), patient_share=True)
    finally:
        db.close()
