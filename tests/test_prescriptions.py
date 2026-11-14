import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.patient import get_patient, is_valid_kvnr
from app.prescriptions import connect


class PrescriptionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'prescriptions.sqlite3')
        self.config = {'TESTING': True, 'PRESCRIPTIONS_DATABASE': self.path,
                       'ACCOUNTS_DATABASE': str(Path(self.temp.name) / 'accounts.sqlite3'),
                       'PENDING_MEDICINES_DATABASE': str(Path(self.temp.name) / 'pending.sqlite3'),
                       'DISPENSING_UPLOAD_ROOT': str(Path(self.temp.name) / 'photos')}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['hpc_user'] = 'dr.demo'
            session['csrf_token'] = 'test-token'
        self.kvnr = next(f'B12345678{i}' for i in range(10) if is_valid_kvnr(f'B12345678{i}'))

    def payload(self, **changes):
        data = {'csrf_token': 'test-token', 'name': 'Test Patient', 'birth_date': '1990-01-02', 'kvnr': self.kvnr,
                'pzn': ['06865959', '01015676'], 'units_per_dose': ['0.5', '1'],
                'times_per_day': ['2', '1'], 'instructions': ['After food', 'With water']}
        data.update(changes)
        return data

    def counts(self):
        with self.app.app_context():
            db = connect()
            try:
                return tuple(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                             for table in ('patients', 'prescriptions', 'prescription_items'))
            finally:
                db.close()

    def test_doctor_home_and_name_lookup_with_explicit_patient_selection(self):
        home = self.client.get('/').get_data(as_text=True)
        self.assertIn('Patient records', home)
        self.assertIn('Prescribe', home)
        self.assertNotIn('name="q"', home)
        self.client.post('/prescribe/', data=self.payload(name='Erika Mustermann'))
        response = self.client.post('/patients', data={'csrf_token': 'test-token', 'q': ' eRiKa '})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'A123456780', response.data)
        self.assertIn(self.kvnr.encode(), response.data)
        self.assertIn(b'1964-08-12', response.data)
        self.assertIn(b'1990-01-02', response.data)
        selected = self.client.post('/patients', data={'csrf_token': 'test-token', 'action': 'open', 'kvnr': self.kvnr}, follow_redirects=True)
        self.assertIn(b'>Prescription</a>', selected.data)
        self.assertIn(b'>Patient version</a>', selected.data)
        self.assertNotIn(b'Medicine Identification', selected.data)
        with self.client.session_transaction() as session:
            self.assertEqual(session['kvnr'], self.kvnr)
        exact = self.client.post('/patients', data={'csrf_token': 'test-token', 'q': self.kvnr.lower()})
        self.assertEqual(exact.status_code, 302)
        no_match = self.client.post('/patients', data={'csrf_token': 'test-token', 'q': 'No such patient xyz'})
        self.assertIn(b'No patient found', no_match.data)
        invalid = self.client.post('/patients', data={'csrf_token': 'test-token', 'q': 'A000000001'})
        self.assertIn(b'Invalid patient ID', invalid.data)

    def test_create_persist_reopen_and_print(self):
        response = self.client.post('/prescribe/', data=self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.counts(), (1, 1, 2))
        detail = self.client.get(response.location)
        self.assertIn(b'Test Patient', detail.data)
        self.assertIn(b'0.5 unit(s) per dose, 2 time(s) per day', detail.data)
        self.assertIn(b'Bisoprolol-ratiopharm', detail.data)
        self.assertIn(b'Print prescription', detail.data)
        other_app = create_app(self.config)
        with other_app.app_context():
            self.assertEqual(get_patient(self.kvnr)['birth_date'], '1990-01-02')
        response = self.client.post('/', data={'csrf_token': 'test-token', 'kvnr': self.kvnr}, follow_redirects=True)
        self.assertIn(b'Test Patient', response.data)
        self.assertIn(b'Test Patient', self.client.get('/prescribe/history').data)
        self.client.post('/prescribe/', data=self.payload())
        self.assertEqual(self.counts(), (1, 2, 4))

    def test_invalid_requests_create_no_records_and_preserve_form(self):
        for changes in ({'kvnr': 'A000000001'}, {'birth_date': '2999-01-01'}, {'name': ''},
                        {'pzn': ['99999999', '01015676']}, {'units_per_dose': ['NaN', '1']},
                        {'units_per_dose': ['-1', '1']}, {'times_per_day': ['1.5', '1']},
                        {'pzn': ['06865959', '06865959']}, {'instructions': ['a']},
                        {'pzn': [], 'units_per_dose': [], 'times_per_day': [], 'instructions': []}):
            with self.subTest(changes=changes):
                response = self.client.post('/prescribe/', data=self.payload(**changes))
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'role="alert"', response.data)
                self.assertEqual(self.counts(), (0, 0, 0))
        response = self.client.post('/prescribe/', data=self.payload(kvnr='bad'))
        self.assertIn(b'Test Patient', response.data)
        self.assertIn(b'After food', response.data)
        self.assertIn(b'06865959', response.data)

    def test_identity_conflict(self):
        self.client.post('/prescribe/', data=self.payload())
        response = self.client.post('/prescribe/', data=self.payload(name='Different Patient'))
        self.assertIn(b'different name or birth date', response.data)
        self.assertEqual(self.counts(), (1, 1, 2))
        response = self.client.post('/prescribe/', data=self.payload(kvnr='A123456780'))
        self.assertIn(b'different name or birth date', response.data)
        self.assertEqual(self.counts(), (1, 1, 2))

    def test_login_csrf_and_ownership(self):
        anonymous = self.app.test_client()
        for path in ('/prescribe/', '/prescribe/history', '/prescribe/1'):
            self.assertEqual(anonymous.get(path).status_code, 302)
        self.assertEqual(self.client.post('/prescribe/', data=self.payload(csrf_token='wrong')).status_code, 400)
        self.client.post('/prescribe/', data=self.payload())
        with self.app.app_context():
            db = connect()
            with db:
                db.execute("UPDATE prescriptions SET doctor_username = 'another.doctor'")
            db.close()
        self.assertEqual(self.client.get('/prescribe/1').status_code, 404)
        self.assertNotIn(b'Test Patient', self.client.get('/prescribe/history').data)


if __name__ == '__main__':
    unittest.main()
