# MedLink
One picture list and one number per medicine, shared by the patient and the clinic, so it's much clearer which pill is meant, even on the phone.

## Starter template (Flask)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py          # http://127.0.0.1:5000
```

Demo logins (password `demo1234`): `nurse.weber` (loads the box) and `dr.demo` (doctor, read-only). Demo patient IDs (KVNR): `A123456780`, `K987654329`.

### Structure

- `app/auth.py`: HPC login (demo users, replace with real auth)
- `app/patient.py`: KVNR validation and patient lookup (demo data, replace with real source). Use `@patient_required` and `g.patient` in tab views.
- `app/medicine_identification.py` + `templates/medicine_identification.html`: Tab 1
- `app/medicine_intake.py` + `templates/medicine_intake.html` + `static/intake.js`: Tab 2, the 4x7 medicine box (SQLite in `instance/`). `app/medicine_db.py` is the local medicine table keyed by PZN (seeded with demo data); drop a pill image named `<PZN>.svg|png|jpg|webp` into `app/static/medicine_images/` and it is picked up automatically.

Each tab is its own blueprint and template, so teams can work in parallel without conflicts.
Set `MEDLINK_SECRET_KEY` for any non-local run.
