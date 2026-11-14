# MedLink

MedLink connects doctors, pharmacies, and patients through numbered medication lists with photos. Doctors prescribe medicines, pharmacies record what was actually dispensed, and patients receive a QR link to their final medication list. Medicine numbers remain consistent across the doctor and patient views, including when a pharmacy substitutes a product.

## Run locally

Use the conda `hackathon` environment with Python 3.11:

```bash
# Create the environment if it does not exist yet.
conda env create -f environment.yml

conda activate hackathon
python -m pip install -r requirements.txt
python run.py
```

Open <http://127.0.0.1:5000>. If port 5000 is occupied:

```bash
conda run --no-capture-output -n hackathon python -m flask --app run:app run --port 5055
```

Account, prescription, and pending medicine databases are initialized automatically on startup. The medicine catalogue is generated separately from the supplied CSV and image files.

### Demo accounts

| Role | Username | Password |
| --- | --- | --- |
| Doctor | `dr.demo` | `demo1234` |
| Pharmacy | `pharmacy.demo` | `pharmacy1234` |

Demo patients:

| Name | Birth date | KVNR |
| --- | --- | --- |
| Erika Mustermann | 1964-08-12 | `A123456780` |
| Max Beispiel | 1989-03-02 | `K987654329` |

## Doctor workflow

### Home and patient records

After login, the doctor home page offers two side-by-side actions:

- **Patient records**: search for a patient and review prescriptions and dispensing information.
- **Prescribe**: create a prescription for a new or existing patient.

Patient records supports exact KVNR lookup and case-insensitive partial name searches. KVNRs are checked for format and check digit. Name searches show matching patients with their birth dates and KVNRs; the doctor explicitly selects a record to avoid confusing patients with the same name.

Once a patient is open, the available tabs include:

- **Prescription**: replaces the former Medicine Identification tab. Shows prescriptions issued by the current doctor for the selected patient, including dispensing status, substitutions, photos, and refill history.
- **Patient version**: displays the same final medicine cards the patient sees, including actual dispensed products, identification details, images, and usage instructions. During an unfinished refill, it continues to show the last completed dispensing. Before the first dispensing is completed, it explains that no final list is available.
- **Medicine Intake Supervision**: remains a placeholder for future intake tracking.

The top-level **Prescriptions** page also lists the current doctor's prescription history.

### Create a prescription

Enter the patient's name, birth date, and valid KVNR. If a KVNR already belongs to a patient with a different name or birth date, the application rejects the conflicting identity rather than overwriting it.

Search for medicines by typing one or more characters. Search is case-insensitive and matches a **continuous prefix** of only these fields:

- `product_name`
- `active_ingredient`
- `pzn`

For example, `Furo` can match `FURORESE`, while `rese` does not match that product name. Descriptions, manufacturers, and other catalogue fields are excluded from the general search.

Select multiple medicines and specify units per dose, times per day, and optional instructions for each. Fractional quantities such as `0.5` are supported. **Create prescription** saves the patient and prescription, then opens a printable detail page. Newly saved patients can subsequently be found through Patient records.

### Review dispensing and substitutions

Prescription views share the same display logic across Patient records and the top-level Prescriptions page:

- Medicines dispensed without substitution show one set of medicine information and a green **Dispensed as prescribed** status.
- Substituted medicines show a red **SUBSTITUTED** status, original and actual medicine information and images, the PZN change, the substitution reason, and pharmacy confirmation details.
- Medicine order and numbering remain unchanged.
- Previous dispensing rounds can be expanded in the history. During a refill, the main view retains the last completed round until the new round is finalized.

## Pharmacy workflow

### Find a prescription and confirm medicines

Pharmacy accounts use the same login form and are redirected to the pharmacy interface. To retrieve prescriptions, match the patient's **KVNR, full name, and birth date**. A failed match clears the previously selected patient scope.

For each prescription item, enter the actual dispensed PZN or scan the package barcode. Supported input includes an eight-digit PZN, `-PZN`, `PZN: XXXXXXXX`, and a valid 13-digit barcode beginning with `4150`. Other GTIN, GS1, or PPN encodings are not guessed; enter the printed PZN manually when a barcode cannot be parsed.

USB/Bluetooth scanners can type into the PZN field. **Scan with camera** uses the browser's `BarcodeDetector` where available. Camera scanning requires browser support, permission, and HTTPS or localhost. A scan populates the field; the pharmacy still confirms the medicine explicitly.

### Record a substitution

If the actual PZN differs from the prescribed PZN, provide a substitution reason and confirm that the active ingredient, strength, formulation, and dosing suitability have been checked. The application requires matching active ingredient names; it does not determine clinical interchangeability or resolve ingredient synonyms automatically.

Original prescription information is preserved. The substitution mark and actual medicine details become visible to the prescribing doctor.

### Supplement missing medicine information

For a medicine absent from the catalogue, enter its name and either a description or a photo. A substitute also requires the matching active ingredient. Additional information can include strength, dosage form, appearance, and indication.

**Take pill photo** opens the camera directly where supported. Use **Capture photo** to take and preview an image, or **Close camera** to stop the camera. Existing JPEG, PNG, and WebP files can also be uploaded. Images are validated, resized, and re-encoded as JPEG without retaining original EXIF metadata.

Supplementary information is saved separately from the base medicine catalogue and is also recorded in a pending medicine database for later review. That review queue has no patient name, KVNR, or prescription ID fields. It does not automatically publish additions to the catalogue or grant pharmaceutical companies access to patient records.

### Retain incomplete forms

When a submitted medicine form fails validation, entered text, checkbox state, and valid uploaded or captured photos are retained in a private draft for that pharmacy account. Refreshing the prescription page restores the draft. The pharmacy can fill in missing fields and resubmit without uploading the image again.

Drafts do not count as dispensed medicines. Successful confirmation transfers the retained image to the dispensing record and clears the corresponding draft. Invalid image files must be selected again.

### Complete dispensing and refill

Every medicine must be confirmed before **Complete dispensing & generate patient QR** becomes available. Finalizing locks that dispensing round and generates its patient link and QR code.

Completed prescriptions offer **Refill · start new dispensing**. Starting a refill archives the previous round and requires every medicine to be confirmed again. Completing the refill updates the doctor's main dispensing view and creates a new patient QR link. Previous rounds, photos, and QR links remain associated with their original medication lists.

An in-progress dispensing round can only be edited by the pharmacy account that started it. An authenticated pharmacy with a matching patient can start a new refill after the previous round is complete.

## Patient lists, QR codes, and printing

The patient list preserves prescription order and shows the actual medicines supplied, whether substituted or not. Available information includes:

- Product name, PZN, active ingredient, ingredient form, strength, and dosage form.
- Brand, pack size, manufacturer, and pharmaceutical company.
- Photos, colour, shape, imprint, score line, and original appearance descriptions, including size information when supplied by the source.
- Pharmacy descriptions and indications, units per dose, times per day, and additional instructions.

Medicine strength is displayed separately from the patient's prescribed usage. Missing dimensions or other details are not inferred from photos.

Patients see a **SUBSTITUTED** mark when applicable, but do not see the original product details or internal substitution reason. Patient pages do not display the patient's name, birth date, or KVNR.

New dispensing records snapshot identification details so later catalogue updates do not change those records. Older records without a snapshot can display available catalogue details using the actual dispensed PZN, never the original substituted product's PZN.

QR codes are generated locally without sending the patient link to an external QR service. Anyone holding the random patient link can view that round's final medicines and images; the link does not provide access to prescription searches or substitution history.

The pharmacy page continues to display the generated QR code and offers separate actions:

- **Print QR**: opens a dedicated page containing the QR code and its access link.
- **Print list**: opens the final patient medication list, including images, identification details, usage, and substitution marks.

Print pages wait for images to load before opening the print dialog and also provide a manual print button.

For phone access, configure `MEDLINK_PUBLIC_BASE_URL` to an address the phone can reach. A QR pointing to `127.0.0.1` points back to the phone itself, not the application server.

## Medicine catalogue and API

The SQLite catalogue is built from `Hackthon/data/doc/medicines.csv` and `Hackthon/data/images/`. It preserves all CSV fields, stores PZNs as text to retain leading zeroes, and associates each medicine with its available images. Medicines without images remain searchable.

```bash
conda run -n hackathon python -m app.medicine_catalog build
conda run -n hackathon python -m app.medicine_catalog search --pzn 06865959
conda run -n hackathon python -m app.medicine_catalog search Bisoprolol
```

The importer validates PZNs and image associations before replacing the generated database. Rebuild it after changing source data.

Example read-only endpoints:

```text
GET /api/medicines?q=Bisoprolol
GET /api/medicines?pzn=06865959
GET /api/medicines?color=white&shape=round
GET /api/medicines/06865959
```

`q` uses the three-field prefix search described above. Explicit field filters can still query other CSV fields. List responses include `total`, `limit`, `offset`, and `results`; medicine results contain image paths and URLs. The default page size is 20, with a maximum of 200.

## Accounts, storage, and permissions

| Location | Contents |
| --- | --- |
| `instance/accounts.sqlite3` | Usernames, display names, doctor/pharmacy roles, salted password hashes, and active flags. |
| `instance/prescriptions.sqlite3` | Patients, original prescriptions, medicine snapshots, dispensing records, private drafts, refill history, and patient link tokens. |
| `instance/dispensing_photos/` | Private pharmacy-supplied medicine photos. |
| `instance/pending_medicines.sqlite3` | Pending supplementary medicine information for later review. |
| `Hackthon/data/medicines.sqlite3` | Base medicine catalogue, read-only during prescribing and dispensing. |

Passwords are verified with Werkzeug and stored as salted hashes, using scrypt in the `hackathon` environment. Demo credentials are defined in `app/auth.py` for initialization; existing account passwords are not overwritten at startup.

Create an account with an interactive, non-echoed password prompt:

```bash
conda run --no-capture-output -n hackathon python -m flask --app run:app create-user
```

Choose `doctor` or `pharmacy`. New account passwords must contain at least ten characters. Set `MEDLINK_SEED_DEMO=0` to prevent creation of missing demo accounts; this does **not** disable demo accounts already stored in the database. An administrator can disable an existing account by setting its `users.active` value to `0`.

Roles and record permissions are checked on the server. Doctors access their own prescriptions. Pharmacies must match patient identity before viewing or dispensing that patient's prescriptions and cannot use doctor prescribing routes. Draft images require the owning pharmacy account; dispensing images require an authorized professional or the corresponding final patient link.

The public medicine catalogue contains no patient records. SQLite files and private photos are outside the static directory, and `instance/` is excluded from Git. Application permissions do not replace operating-system file permissions: the service account and server administrator retain filesystem access.

### Configuration

| Environment variable | Purpose |
| --- | --- |
| `MEDLINK_SECRET_KEY` | Stable session signing secret; set for any non-local deployment. |
| `MEDLINK_ACCOUNTS_DB` | Override the account database path. |
| `MEDLINK_PRESCRIPTIONS_DB` | Override the patient/prescription database path. |
| `MEDLINK_PENDING_DB` | Override the pending medicine database path. |
| `MEDLINK_MEDICINES_DB` | Override the medicine catalogue path. |
| `MEDLINK_SEED_DEMO` | Set to `0` to stop seeding missing demo accounts. |
| `MEDLINK_PUBLIC_BASE_URL` | Externally reachable application base URL used in patient QR links. |

Example:

```bash
export MEDLINK_PUBLIC_BASE_URL=https://your-medlink-host.example
```

This variable does not deploy the application or configure HTTPS. Back up account, prescription, and pending databases together with the private photo directory.

## Project structure

| File | Responsibility |
| --- | --- |
| `app/__init__.py` | Application configuration, blueprint registration, CSRF checks, and database initialization. |
| `app/auth.py` | Account storage, login, role checks, and account creation CLI. |
| `app/patient.py` | Doctor home, KVNR validation, patient search/selection, and Patient version. |
| `app/prescriptions.py` | Prescription creation, history, and shared doctor prescription view. |
| `app/medicine_identification.py` | Patient-specific Prescription tab; retains its existing route/module name. |
| `app/medicine_intake.py` | Intake supervision placeholder. |
| `app/medicine_catalog.py` | Catalogue import, CLI search, and medicine/image API. |
| `app/pharmacy.py` | Patient matching, dispensing, substitutions, drafts, photos, refills, patient links, and print views. |
| `app/templates/` | Shared doctor/patient medicine cards and role-specific pages. |
| `app/static/` | Styles, prescription search, camera capture/scanning, and print behavior. |
| `tests/` | Automated catalogue, prescription, authentication, and pharmacy workflow checks. |

## Validation and current scope

```bash
conda run -n hackathon python -m unittest discover -s tests -v
```

The current suite contains 24 tests covering catalogue integrity, restricted prefix searches, identity validation, roles and ownership, patient searches, substitutions, retained drafts and images, refill snapshots, patient views, and separate QR/list print pages.

Camera capture/scanning and physical printing still require device/browser testing. This application records prescriptions and dispensing within MedLink; it does not implement electronic prescription signing, pharmacy transmission, or clinical medication decision support.
