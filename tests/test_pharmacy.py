from io import BytesIO
from html import unescape
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app import create_app
from app.auth import accounts, password_hash
from app.pharmacy import parse_pzn
from app.prescriptions import connect


class PharmacyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = {
            'TESTING': True, 'PRESCRIPTIONS_DATABASE': str(root / 'prescriptions.sqlite3'),
            'ACCOUNTS_DATABASE': str(root / 'accounts.sqlite3'),
            'PENDING_MEDICINES_DATABASE': str(root / 'pending.sqlite3'),
            'DISPENSING_UPLOAD_ROOT': str(root / 'photos'),
            'PUBLIC_BASE_URL': 'https://medlink.example',
        }
        self.app = create_app(self.config)
        self.doctor = self.client('dr.demo')
        self.pharmacy = self.client('pharmacy.demo')
        self.anon = self.app.test_client()
        response = self.doctor.post('/prescribe/', data={
            'csrf_token': 'token', 'name': 'Erika Mustermann', 'birth_date': '1964-08-12', 'kvnr': 'A123456780',
            'pzn': ['06865959', '01015676'], 'units_per_dose': ['0.5', '1'],
            'times_per_day': ['2', '1'], 'instructions': ['After food', 'With water'],
        })
        self.assertEqual(response.status_code, 302)
        self.rx = int(response.location.rstrip('/').split('/')[-1])
        self.url = f'/pharmacy/{self.rx}'
        with self.app.app_context():
            db = connect()
            self.items = [r['id'] for r in db.execute('SELECT id FROM prescription_items ORDER BY id')]
            db.close()
        self.match()

    def client(self, username):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['hpc_user'] = username
            session['csrf_token'] = 'token'
        return client

    def match(self, **changes):
        data = {'csrf_token': 'token', 'name': 'Erika Mustermann', 'birth_date': '1964-08-12', 'kvnr': 'A123456780'}
        data.update(changes)
        return self.pharmacy.post('/pharmacy/', data=data)

    def confirm(self, index=0, **changes):
        data = {'csrf_token': 'token', 'action': 'confirm', 'item_id': str(self.items[index]),
                'pzn': ['06865959', '01015676'][index]}
        data.update(changes)
        return self.pharmacy.post(self.url, data=data, content_type='multipart/form-data')

    def state(self):
        with self.app.app_context():
            db = connect()
            try:
                row = db.execute('SELECT * FROM dispensing WHERE prescription_id = ?', (self.rx,)).fetchone()
                return dict(row) if row else None
            finally:
                db.close()

    def finish(self):
        return self.pharmacy.post(self.url, data={'csrf_token': 'token', 'action': 'finalize'})

    def test_role_login_account_storage_and_patient_matching(self):
        login = self.anon.get('/login')
        self.assertEqual(login.status_code, 200)
        with self.anon.session_transaction() as session:
            token = session['csrf_token']
        response = self.anon.post('/login', data={'csrf_token': token, 'username': 'pharmacy.demo', 'password': 'pharmacy1234'})
        self.assertTrue(response.location.endswith('/pharmacy/'))
        self.assertEqual(self.pharmacy.get('/prescribe/').status_code, 403)
        self.assertEqual(self.pharmacy.get('/').status_code, 403)
        self.assertEqual(self.doctor.get('/pharmacy/').status_code, 403)
        self.assertEqual(self.app.test_client().get('/prescribe/history').status_code, 302)
        self.match(name='Wrong Name')
        self.assertEqual(self.pharmacy.get(self.url).status_code, 404)
        self.match(birth_date='1960-01-01')
        self.assertEqual(self.pharmacy.get(self.url).status_code, 404)
        self.match()
        self.assertEqual(self.pharmacy.get(self.url).status_code, 200)
        with self.app.app_context():
            db = accounts()
            row = db.execute("SELECT * FROM users WHERE username = 'pharmacy.demo'").fetchone()
            self.assertNotEqual(row['password_hash'], 'pharmacy1234')
            self.assertNotIn('password', row.keys())
            db.close()

    def test_complete_and_patient_projection_and_stable_order(self):
        self.assertEqual(self.confirm(1).status_code, 302)  # deliberately dispense out of order
        self.assertEqual(self.confirm(0).status_code, 302)
        self.assertEqual(self.finish().status_code, 302)
        state = self.state()
        self.assertIsNotNone(state['completed_at'])
        token = state['patient_token']
        response = self.anon.get(f'/medications/{token}')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertLess(html.index('Bisoprolol-ratiopharm'), html.index('Enalapril AbZ'))
        self.assertNotIn('Erika', html)
        self.assertNotIn('A123456780', html)
        self.assertNotIn('replacement_reason', html)
        qr = self.anon.get(f'/medications/{token}/qr.svg')
        self.assertEqual(qr.mimetype, 'image/svg+xml')
        qr.close()
        # Verify the exact encoded URL without relying on a third-party QR service.
        with patch('app.pharmacy.qrcode.make', wraps=__import__('qrcode').make) as make:
            qr = self.anon.get(f'/medications/{token}/qr.svg')
            self.assertEqual(make.call_args.args[0], f'https://medlink.example/medications/{token}')
            qr.close()
        image = self.anon.get(f'/medications/{token}/media/{self.items[0]}/1')
        self.assertEqual(image.status_code, 200)
        image.close()
        self.assertEqual(self.anon.get(f'/dispensing-media/{self.items[0]}/1').status_code, 403)
        self.assertEqual(self.anon.get('/medications/not-a-token').status_code, 404)
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(self.state()['patient_token'], token)

    def test_unknown_substitute_photo_and_separate_review_database(self):
        photo = BytesIO()
        Image.new('RGB', (20, 20), 'yellow').save(photo, 'PNG')
        photo.seek(0)
        from app.medicine_catalog import DEFAULT_DB
        original = Path(DEFAULT_DB).read_bytes()
        response = self.confirm(pzn='99999999', product_name='Replacement Brand', active_ingredient='Bisoprolol',
                                strength='5 mg', dosage_form='Tabletten', reason='Brand unavailable',
                                equivalence_confirmed='yes', description='Yellow round tablet', indication='Pharmacy supplied purpose',
                                photo=(photo, 'tablet.png'))
        self.assertEqual(response.status_code, 302)
        self.confirm(1)
        self.finish()
        doctor = self.doctor.get(f'/prescribe/{self.rx}').get_data(as_text=True)
        self.assertIn('SUBSTITUTED', doctor)
        self.assertIn('06865959 → 99999999', doctor)
        self.assertIn('Brand unavailable', doctor)
        self.assertIn('Yellow round tablet', doctor)
        patient = self.anon.get(f"/medications/{self.state()['patient_token']}").get_data(as_text=True)
        self.assertIn('Replacement Brand', patient)
        self.assertNotIn('06865959', patient)
        self.assertNotIn('Bisoprolol-ratiopharm', patient)
        self.assertNotIn('Brand unavailable', patient)
        photo_response = self.anon.get(f"/medications/{self.state()['patient_token']}/media/{self.items[0]}/0")
        self.assertEqual(photo_response.mimetype, 'image/jpeg')
        photo_response.close()
        db = sqlite3.connect(self.config['PENDING_MEDICINES_DATABASE'])
        self.assertEqual(db.execute('SELECT pzn FROM pending_medicines').fetchone()[0], '99999999')
        fields = [r[1] for r in db.execute('PRAGMA table_info(pending_medicines)')]
        self.assertNotIn('kvnr', fields)
        self.assertNotIn('prescription_id', fields)
        db.close()
        self.assertEqual(original, Path(DEFAULT_DB).read_bytes())

    def test_doctor_patient_version_is_the_final_patient_list(self):
        initial = self.doctor.get('/patient-version')
        self.assertIn(b'No final patient list yet', initial.data)
        self.confirm(pzn='99999999', product_name='Patient View Brand', active_ingredient='Bisoprolol',
                     strength='5 mg', dosage_form='Tabletten', reason='Private pharmacy reason',
                     equivalence_confirmed='yes', description='Round yellow pill')
        self.confirm(1)
        self.finish()
        token = self.state()['patient_token']
        page = self.doctor.get('/patient-version').get_data(as_text=True)
        self.assertIn('Patient View Brand', page)
        self.assertIn('SUBSTITUTED', page)
        self.assertIn('Round yellow pill', page)
        self.assertIn('Your prescribed use', page)
        self.assertNotIn('Bisoprolol-ratiopharm', page)
        self.assertNotIn('Private pharmacy reason', page)
        self.assertIn(f'/medications/{token}/media/', page)
        self.pharmacy.post(self.url + '/refill', data={'csrf_token': 'token'})
        self.assertIn(b'last collected medicines', self.doctor.get('/patient-version').data)
        self.assertIn(b'Patient View Brand', self.doctor.get('/patient-version').data)
        self.confirm(0)
        self.confirm(1)
        self.finish()
        current = self.doctor.get('/patient-version').get_data(as_text=True)
        self.assertIn('Bisoprolol-ratiopharm', current)
        self.assertNotIn('Patient View Brand', current)
        self.assertEqual(self.pharmacy.get('/patient-version').status_code, 403)
        self.assertEqual(self.anon.get('/patient-version').status_code, 302)
        with self.app.app_context():
            db = connect()
            with db:
                db.execute("UPDATE prescriptions SET doctor_username = 'other.doctor'")
            db.close()
        self.assertNotIn(b'Bisoprolol-ratiopharm', self.doctor.get('/patient-version').data)

    def test_patient_identification_details_and_print_are_snapshotted(self):
        from app.pharmacy import catalogue_medicine
        with self.app.app_context():
            medicine = catalogue_medicine('06865959')
        self.confirm(0)
        self.confirm(1)
        self.finish()
        token = self.state()['patient_token']
        page = unescape(self.anon.get(f'/medications/{token}').get_data(as_text=True))
        printed = unescape(self.pharmacy.get(self.url + '/print/list').get_data(as_text=True))
        for text in (medicine['product_name'], medicine['active_ingredient'], medicine['strength_as_stated'],
                     medicine['dosage_form'], medicine['color'], medicine['shape'], medicine['imprint'],
                     medicine['score_line'], medicine['appearance_description_original'],
                     medicine['ingredient_form_as_stated'], medicine['brand'], medicine['manufacturer']):
            self.assertIn(text, page)
            self.assertIn(text, printed)
        self.assertIn('Medicine strength (as stated)', page)
        self.assertIn('Your prescribed use', page)
        # Updating the catalogue must not change a newly snapshotted patient's medication details.
        with patch('app.pharmacy.catalogue_medicine', side_effect=AssertionError('Should use the saved snapshot')):
            self.assertEqual(self.anon.get(f'/medications/{token}').status_code, 200)
        self.pharmacy.post(self.url + '/refill', data={'csrf_token': 'token'})
        archived = unescape(self.anon.get(f'/medications/{token}').get_data(as_text=True))
        self.assertIn(medicine['appearance_description_original'], archived)

    def test_replacement_details_and_legacy_enrichment_use_actual_pzn(self):
        from app.pharmacy import catalogue_medicine
        actual = {'pzn': '99999999', 'product_name': 'Blue Replacement Brand', 'active_ingredient': 'Bisoprolol',
                  'strength_as_stated': '5 mg', 'dosage_form': 'Tabletten', 'images': [],
                  'color': 'blue', 'shape': 'oval', 'imprint': 'ABC', 'score_line': 'none',
                  'size_description': '8 mm', 'appearance_description_original': 'Blue oval, 8 mm',
                  'ingredient_form_as_stated': 'Bisoprololfumarat', 'brand': 'Replacement Brand'}
        with patch('app.pharmacy.catalogue_medicine', return_value=actual):
            self.assertEqual(self.confirm(pzn='99999999', reason='Unavailable', equivalence_confirmed='yes',
                                          description='Pharmacy supplied pill description').status_code, 302)
        self.confirm(1)
        self.finish()
        token = self.state()['patient_token']
        page = self.anon.get(f'/medications/{token}').get_data(as_text=True)
        for value in ('Blue Replacement Brand', 'blue', 'oval', 'ABC', '8 mm', 'Pharmacy supplied pill description'):
            self.assertIn(value, page)
        self.assertNotIn('Bisoprolol-ratiopharm', page)
        self.assertNotIn('06865959', page)
        # Simulate a pre-upgrade record with no identification snapshot.
        with self.app.app_context():
            db = connect()
            with db:
                db.execute("UPDATE dispensed_items SET medicine_details_json = ''")
            db.close()
        def actual_lookup(pzn):
            self.assertNotEqual(pzn, '06865959')
            return actual if pzn == '99999999' else catalogue_medicine(pzn)
        with patch('app.pharmacy.catalogue_medicine', side_effect=actual_lookup):
            legacy = self.anon.get(f'/medications/{token}').get_data(as_text=True)
        self.assertIn('Blue oval, 8 mm', legacy)
        self.assertNotIn('Bisoprolol-ratiopharm', legacy)

    def test_open_patient_identification_shows_matching_prescriptions_and_dispensing(self):
        lookup = {'csrf_token': 'token', 'kvnr': 'A123456780'}
        response = self.doctor.post('/', data=lookup, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'Prescription #{self.rx}'.encode(), response.data)
        self.assertIn(b'Not yet dispensed', response.data)
        self.confirm(pzn='99999999', product_name='Identification Substitute', active_ingredient='Bisoprolol',
                     strength='5 mg', dosage_form='Tabletten', reason='No stock', equivalence_confirmed='yes',
                     description='White tablet')
        self.assertIn(b'Dispensing in progress', self.doctor.get('/identification/').data)
        self.confirm(1)
        self.finish()
        page = self.doctor.get('/identification/').get_data(as_text=True)
        self.assertIn('Dispensed', page)
        self.assertIn('SUBSTITUTED', page)
        self.assertIn('Identification Substitute', page)
        self.assertIn('Originally prescribed', page)
        self.assertIn('No stock', page)
        self.assertIn('White tablet', page)
        # Another patient's prescriptions must not appear in the selected patient's tab.
        other = self.doctor.post('/prescribe/', data={
            'csrf_token': 'token', 'name': 'Max Beispiel', 'birth_date': '1989-03-02', 'kvnr': 'K987654329',
            'pzn': ['06865959'], 'units_per_dose': ['1'], 'times_per_day': ['1'], 'instructions': [''],
        })
        other_id = int(other.location.rstrip('/').split('/')[-1])
        page = self.doctor.post('/', data=lookup, follow_redirects=True).get_data(as_text=True)
        self.assertNotIn(f'Prescription #{other_id}', page)
        self.assertIn(f'Prescription #{self.rx}', page)
        # Preserve the existing doctor ownership boundary at the second entry point.
        with self.app.app_context():
            db = connect()
            with db:
                db.execute("UPDATE prescriptions SET doctor_username = 'another.doctor' WHERE id = ?", (self.rx,))
            db.close()
        page = self.doctor.get('/identification/').get_data(as_text=True)
        self.assertNotIn('Identification Substitute', page)
        self.assertIn('No prescriptions from your account', page)
        self.assertEqual(self.pharmacy.get('/identification/').status_code, 403)

    def test_separate_qr_and_list_print_views(self):
        qr_url = self.url + '/print/qr'
        list_url = self.url + '/print/list'
        self.assertEqual(self.pharmacy.get(qr_url).status_code, 409)
        self.confirm(pzn='99999999', product_name='Printed Substitute', active_ingredient='Bisoprolol',
                     strength='5 mg', dosage_form='Tabletten', reason='Print private reason',
                     equivalence_confirmed='yes', description='Round tablet')
        self.confirm(1)
        self.finish()
        page = self.pharmacy.get(self.url).get_data(as_text=True)
        self.assertIn('Print QR</a>', page)
        self.assertIn('Print list</a>', page)
        self.assertIn('class="patient-qr"', page)
        qr_page = self.pharmacy.get(qr_url).get_data(as_text=True)
        self.assertIn('class="patient-qr"', qr_page)
        self.assertNotIn('Printed Substitute', qr_page)
        list_page = self.pharmacy.get(list_url).get_data(as_text=True)
        self.assertIn('Printed Substitute', list_page)
        self.assertIn('SUBSTITUTED', list_page)
        self.assertNotIn('class="patient-qr"', list_page)
        self.assertNotIn('Print private reason', list_page)
        self.assertNotIn('06865959', list_page)
        self.assertLess(list_page.index('Printed Substitute'), list_page.index('Enalapril AbZ'))
        self.assertEqual(self.pharmacy.get(self.url + '/print/unknown').status_code, 404)
        self.assertEqual(self.doctor.get(qr_url).status_code, 403)
        self.assertEqual(self.anon.get(list_url).status_code, 302)
        self.match(name='Wrong Name')
        self.assertEqual(self.pharmacy.get(qr_url).status_code, 404)

    def test_unchanged_medicine_has_no_original_actual_comparison(self):
        self.confirm(0)
        self.confirm(1)
        page = self.doctor.get(f'/prescribe/{self.rx}').get_data(as_text=True)
        self.assertNotIn('Originally prescribed', page)
        self.assertNotIn('Actual medicine:', page)
        self.assertIn('Dispensed as prescribed', page)
        self.assertIn('Bisoprolol-ratiopharm', page)
        self.assertIn('dispensing-photo', page)

    def test_incomplete_form_retains_text_checkbox_and_photo_across_refresh(self):
        photo = BytesIO()
        Image.new('RGB', (24, 24), 'white').save(photo, 'PNG')
        photo.seek(0)
        values = {'pzn': '99999999', 'product_name': 'Saved Draft Brand', 'active_ingredient': 'Bisoprolol',
                  'strength': '5 mg', 'dosage_form': 'Tabletten', 'description': 'Round white pill',
                  'indication': 'Supplied purpose', 'equivalence_confirmed': 'yes'}
        # Reason is missing, but even this early validation failure must keep the uploaded image.
        failed = self.confirm(**values, photo=(photo, 'draft.png'))
        self.assertEqual(failed.status_code, 200)
        self.assertIsNone(self.state())
        refreshed = self.pharmacy.get(self.url).get_data(as_text=True)
        for value in values.values():
            self.assertIn(value, refreshed)
        self.assertIn('value="yes" checked', refreshed)
        self.assertIn('Photo retained', refreshed)
        preview_url = f'/pharmacy/draft/{self.items[0]}/photo'
        preview = self.pharmacy.get(preview_url)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.mimetype, 'image/jpeg')
        saved_bytes = preview.data
        preview.close()
        self.assertEqual(self.doctor.get(preview_url).status_code, 403)
        self.assertEqual(self.anon.get(preview_url).status_code, 302)
        self.match(name='Incorrect Name')
        self.assertEqual(self.pharmacy.get(preview_url).status_code, 404)
        self.match()
        # Fail a second time without uploading; the same image must still be present.
        self.confirm(**dict(values, active_ingredient='Wrong ingredient'), reason='No stock')
        preview = self.pharmacy.get(preview_url)
        self.assertEqual(preview.data, saved_bytes)
        preview.close()
        self.assertEqual(self.confirm(**values, reason='No stock').status_code, 302)
        self.assertEqual(self.pharmacy.get(preview_url).status_code, 404)
        with self.app.app_context():
            db = connect()
            actual = db.execute('SELECT photo FROM dispensed_items WHERE item_id = ?', (self.items[0],)).fetchone()
            self.assertEqual((Path(self.config['DISPENSING_UPLOAD_ROOT']) / actual['photo']).read_bytes(), saved_bytes)
            self.assertEqual(db.execute('SELECT count(*) FROM dispensing_drafts').fetchone()[0], 0)
            db.close()

    def test_refill_archives_qr_photos_and_updates_doctor_marks(self):
        photo = BytesIO()
        Image.new('RGB', (16, 16), 'yellow').save(photo, 'PNG')
        photo.seek(0)
        self.confirm(pzn='99999999', product_name='First Round Brand', active_ingredient='Bisoprolol',
                     strength='5 mg', dosage_form='Tabletten', reason='No stock', equivalence_confirmed='yes',
                     photo=(photo, 'pill.png'))
        self.confirm(1)
        self.finish()
        old_token = self.state()['patient_token']
        old_url = f'/medications/{old_token}'
        patient = self.anon.get(old_url).get_data(as_text=True)
        self.assertIn('SUBSTITUTED', patient)
        self.assertNotIn('06865959', patient)
        doctor = self.doctor.get(f'/prescribe/{self.rx}').get_data(as_text=True)
        self.assertIn('is-substituted', doctor)
        self.assertIn('Originally prescribed Bisoprolol', doctor)
        self.assertIn('Pharmacy photo of First Round Brand', doctor)
        self.assertIn(b'Refill', self.pharmacy.get(self.url).data)
        refill_url = self.url + '/refill'
        self.assertEqual(self.doctor.post(refill_url, data={'csrf_token': 'token'}).status_code, 403)
        self.assertEqual(self.pharmacy.post(refill_url, data={'csrf_token': 'token'}).status_code, 302)
        self.assertIsNone(self.state()['completed_at'])
        self.assertEqual(self.pharmacy.post(refill_url, data={'csrf_token': 'token'}).status_code, 409)
        self.assertIn(b'First Round Brand', self.anon.get(old_url).data)
        self.assertIn(b'Refill in progress', self.doctor.get(f'/prescribe/{self.rx}').data)
        old_photo = self.anon.get(f'{old_url}/media/{self.items[0]}/0')
        self.assertEqual(old_photo.status_code, 200)
        old_photo.close()
        with self.app.app_context():
            db = connect()
            history_id = db.execute('SELECT id FROM dispensing_history').fetchone()[0]
            db.close()
        photo_response = self.doctor.get(f'/dispensing-media/{self.items[0]}/0?history={history_id}')
        self.assertEqual(photo_response.status_code, 200)
        photo_response.close()
        self.assertEqual(self.finish().status_code, 200)  # every item must be reconfirmed
        self.confirm(1)
        self.confirm(0)
        self.finish()
        new_token = self.state()['patient_token']
        self.assertNotEqual(old_token, new_token)
        new_patient = self.anon.get(f'/medications/{new_token}').get_data(as_text=True)
        self.assertIn('Dispensed as prescribed', new_patient)
        self.assertNotIn('First Round Brand', new_patient)
        self.assertLess(new_patient.index('Bisoprolol-ratiopharm'), new_patient.index('Enalapril AbZ'))
        current_doctor = self.doctor.get(f'/prescribe/{self.rx}').get_data(as_text=True)
        self.assertIn('is-prescribed', current_doctor)
        self.assertIn('Previous dispensing', current_doctor)
        self.assertIn('First Round Brand', current_doctor)
        self.assertIn(b'First Round Brand', self.anon.get(old_url).data)
        qr = self.anon.get(old_url + '/qr.svg')
        self.assertEqual(qr.status_code, 200)
        qr.close()
        form = self.pharmacy.post(refill_url, data={'csrf_token': 'token'}, follow_redirects=True)
        self.assertIn(b'Take pill photo', form.data)
        self.assertIn(b'Capture photo', form.data)

    def test_description_only_and_photo_only_supplements(self):
        common = {'pzn': '99999998', 'product_name': 'Description Only Brand',
                  'active_ingredient': 'Bisoprolol', 'strength': '5 mg', 'dosage_form': 'Tabletten',
                  'reason': 'Out of stock', 'equivalence_confirmed': 'yes'}
        self.assertEqual(self.confirm(**common, description='White round tablet').status_code, 302)
        photo = BytesIO()
        Image.new('RGB', (16, 16), 'white').save(photo, 'PNG')
        photo.seek(0)
        self.assertEqual(self.confirm(**dict(common, pzn='99999997', product_name='Photo Only Brand'),
                                      photo=(photo, 'medicine.png')).status_code, 302)
        # Reconfirming without a new upload must retain the existing photo.
        self.assertEqual(self.confirm(**dict(common, pzn='99999997', product_name='Photo Only Brand')).status_code, 302)
        with self.app.app_context():
            db = connect()
            row = db.execute('SELECT * FROM dispensed_items WHERE item_id = ?', (self.items[0],)).fetchone()
            self.assertTrue(row['photo'])
            self.assertEqual(row['description'], '')
            db.close()

    def test_validation_rollback_and_draft_privacy(self):
        self.assertEqual(self.finish().status_code, 200)
        self.assertIsNone(self.state())
        for changes in ({'pzn': 'bad barcode'},
                        {'pzn': '99999999'},
                        {'pzn': '99999999', 'reason': 'No stock', 'equivalence_confirmed': 'yes',
                         'product_name': 'Wrong ingredient', 'active_ingredient': 'Different ingredient'},
                        {'photo': (BytesIO(b'not a photo'), 'bad.jpg')}):
            with self.subTest(changes=changes):
                self.assertEqual(self.confirm(**changes).status_code, 200)
                self.assertIsNone(self.state())
        self.confirm()
        self.assertIsNone(self.state()['patient_token'])
        other_pharmacy = self.client('pharmacy.other')
        with self.app.app_context():
            db = accounts()
            with db:
                db.execute('INSERT INTO users(username,name,role,password_hash) VALUES (?,?,?,?)',
                           ('pharmacy.other', 'Other pharmacy', 'pharmacy', password_hash('password123')))
            db.close()
        with other_pharmacy.session_transaction() as session:
            session['pharmacy_kvnr'] = 'A123456780'
        response = other_pharmacy.post(self.url, data={'csrf_token': 'token', 'action': 'confirm',
                                                     'item_id': self.items[1], 'pzn': '01015676'})
        self.assertIn(b'Another pharmacy', response.data)
        self.assertEqual(self.pharmacy.post(self.url, data={'action': 'finalize'}).status_code, 400)

    def test_account_cli_and_disabled_accounts(self):
        result = self.app.test_cli_runner().invoke(args=['create-user', '--username', 'pharmacy.new',
                                                        '--name', 'New Pharmacy', '--role', 'pharmacy'],
                                                   input='newpassword123\nnewpassword123\n')
        self.assertEqual(result.exit_code, 0, result.output)
        with self.app.app_context():
            db = accounts()
            with db:
                db.execute("UPDATE users SET active = 0 WHERE username = 'pharmacy.demo'")
            db.close()
        self.assertEqual(self.pharmacy.get('/pharmacy/').status_code, 302)

    def test_barcode_parsing(self):
        for barcode in ('06865959', '-06865959', 'PZN: 06865959'):
            self.assertEqual(parse_pzn(barcode), '06865959')
        base = '415006865959'
        check = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(base)) % 10) % 10
        self.assertEqual(parse_pzn(base + str(check)), '06865959')
        for bad in ('123', 'some-06865959-stuff', base + str((check + 1) % 10)):
            with self.assertRaises(ValueError):
                parse_pzn(bad)


if __name__ == '__main__':
    unittest.main()
