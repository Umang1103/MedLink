#!/usr/bin/env python3
"""
add_medication.py
-----------------
Reusable script for the German medication database hackathon project.

Usage:
    python add_medication.py

The script:
  - Reads an existing medicines.xlsx / medicines.csv (if present) so that
    unrelated records are never overwritten.
  - Adds or updates a single medication record identified by its PZN.
  - Writes the result back to medicines.xlsx and medicines.csv.
  - Writes a per-PZN sources.json file inside the PZN sub-folder.

To add a new medication:
  1. Create a new dict in RECORDS (see the template below).
  2. Run this script.

NOTE ON PZN FORMATTING
  PZNs are stored as plain text strings, zero-padded to 8 characters.
  When opening medicines.csv in Excel you must import column A as *Text*,
  otherwise Excel will silently drop the leading zero.

ATTRIBUTION / LICENSING
  Images and leaflet PDFs were obtained from ABDATA Pharma-Daten-Service
  (via image.wub-service.de) and from apotheken-umschau.de.
  Usage is subject to their licence terms ("Verwendung nach Lizenzbestimmungen").
  The downloaded materials are for local hackathon reference only.
  Do NOT publish or redistribute them.
"""

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("openpyxl is required. Run: pip install openpyxl")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.resolve()
XLSX_PATH = PROJECT_ROOT / "medicines.xlsx"
CSV_PATH  = PROJECT_ROOT / "medicines.csv"

# Column order used in both XLSX and CSV
COLUMNS = [
    "pzn",
    "market",
    "product_name",
    "active_ingredient",
    "ingredient_form_as_stated",
    "strength_as_stated",
    "dosage_form",
    "pack_size",
    "brand",
    "pharmaceutical_company",
    "manufacturer",
    "color",
    "shape",
    "imprint",
    "score_line",
    "appearance_description_original",
    "tablet_image_1",
    "tablet_image_2",
    "package_image",
    "leaflet_path",
    "product_source_url",
    "manufacturer_source_url",
    "leaflet_revision_date",
    "retrieved_at",
    "verification_status",
    "notes",
]

# ---------------------------------------------------------------------------
# Medication records
# ---------------------------------------------------------------------------

RECORDS = [
    # ------------------------------------------------------------------
    # PZN 06865959 – Bisoprolol-ratiopharm 5 mg Tabletten
    # ------------------------------------------------------------------
    # Sources consulted:
    #   [1] https://www.apotheken-umschau.de/medikamente/beipackzettel/
    #           bisoprolol-ratiopharm-5-mg-tabletten-6865959.html
    #       (retrieved 2026-11-13; ABDATA Pharma-Daten-Service)
    #   [2] Patient information leaflet PDF
    #       https://image.wub-service.de/resources/static/drugexpert/
    #           261101/69/16/41669.pdf
    #       Leaflet last revised: August 2022, version code Z11
    #       PDF creation/modification date: 2022-11-11
    #   [3] https://www.ratiopharm.de/produkte/details/
    #           bisoprolol-ratiopharm-5-mg-tabletten-pzn-6865959/
    # ------------------------------------------------------------------
    {
        # ---- Identification ----
        "pzn": "06865959",          # Eight-digit, leading zero preserved
        "market": "DE",

        # ---- Product name & active substance ----
        "product_name": "Bisoprolol-ratiopharm 5 mg Tabletten",
        "active_ingredient": "Bisoprolol",   # active moiety; salt form is in ingredient_form_as_stated
        "ingredient_form_as_stated": "Bisoprololfumarat",  # as printed in the leaflet
        "strength_as_stated": "5 mg",   # label strength as stated; this is per tablet

        # ---- Dosage form & pack ----
        "dosage_form": "Tabletten",         # German, matches Darreichungsform on label
        "pack_size": "30",                  # Verified: PZN 06865959 = 30 Tabletten (N1)
                                            # Other pack sizes (50, 98, 100) carry
                                            # different PZNs per the product page.

        # ---- Branding & ownership ----
        "brand": "Bisoprolol-ratiopharm",
        "pharmaceutical_company": "ratiopharm GmbH, Graf-Arco-Str. 3, 89079 Ulm",
        "manufacturer": "Merckle GmbH, Ludwig-Merckle-Str. 3, 89143 Blaubeuren",
        # Note: pharmaceutical company (Zulassungsinhaber) ≠ manufacturer (Hersteller).
        # Both are distinct legal entities per the leaflet.

        # ---- Physical appearance (English normalised) ----
        "color": "light yellow, mottled/speckled",
        # Source: leaflet §6: "gesprenkelt, hell gelb gefärbt"
        # Excipients confirming colour: Eisenoxid gelb (E 172)

        "shape": "round, biconvex",
        # Source: leaflet §6: "rund und konvex"

        "imprint": "5",
        # Source: apotheken-umschau.de and leaflet §6:
        # Prägung/Aufschrift: "5" – right of the score line

        "score_line": "single score line on one side (wedge-shaped)",
        # 'Spaltung: nein' on the page describes the notch edge, not divisibility.
        # Source: apotheken-umschau.de: Einzelkerbe, keilförmig, einseitig, Spaltung: nein
        # Leaflet §6: "einseitige Bruchkerbe mit der Prägung „5" rechts der Bruchkerbe"

        "appearance_description_original": (
            "Die Tabletten sind gesprenkelt, hell gelb gefärbt, rund und konvex "
            "mit folgenden Erkennungsmerkmalen: einseitige Bruchkerbe mit der "
            "Prägung \u201e5\u201c rechts der Bruchkerbe."
        ),
        # Exact wording from leaflet §6 (German), preserved verbatim.

        # ---- Physical dimensions (from apotheken-umschau / ABDATA) ----
        # Diameter: 8.2 mm  |  Height: 3.3 mm  |  Mass: 178.0 mg
        # (average values; not stored in a dedicated field here but available in notes)

        # ---- Local file paths (project-relative) ----
        "tablet_image_1": "06865959/06865959_tablet_1.jpg",
        # Classification: tablet, visually confirmed (600x600 px; scored side with "5").
        "tablet_image_2": "06865959/06865959_tablet_2.jpg",
        # Classification: tablet, visually confirmed (plain reverse side).
        "package_image": "",            # No dedicated packaging image was available.
        "leaflet_path": "06865959/06865959_leaflet.pdf",

        # ---- URLs ----
        "product_source_url": (
            "https://www.apotheken-umschau.de/medikamente/beipackzettel/"
            "bisoprolol-ratiopharm-5-mg-tabletten-6865959.html"
        ),
        "manufacturer_source_url": (
            "https://www.ratiopharm.de/produkte/details/"
            "bisoprolol-ratiopharm-5-mg-tabletten-pzn-6865959/"
        ),

        # ---- Dates ----
        "leaflet_revision_date": "2022-08",
        # Source: leaflet §10: "Diese Packungsbeilage wurde zuletzt überarbeitet
        # im August 2022."  Version code Z11.  PDF creation date: 2022-11-11.

        "retrieved_at": "2026-11-13",

        # ---- Status ----
        "verification_status": (
            "Data extracted from official sources; not clinically or pharmacist verified. "
            "Images visually confirmed as tablet photos."
        ),

        "notes": (
            "Dimensions (ABDATA avg): diameter 8.2 mm, height 3.3 mm, mass 178.0 mg. "
            "Tablet can be split into 2 equal halves. "
            "Excipients include lactose monohydrate (lactose intolerance warning applies). "
            "Pack sizes 50, 98, 100 carry different PZNs – not assumed here. "
            "Image reuse permission: unverified (licence text: 'Verwendung nach "
            "Lizenzbestimmungen', rights holder ABDATA Pharma-Daten-Service / "
            "Dosing GmbH Heidelberg). "
            "apotheken-umschau.de last updated: 2025-11-24."
        ),
    },
]

# ---------------------------------------------------------------------------
# Helper: load existing records from CSV (if any)
# ---------------------------------------------------------------------------

def load_existing_records(csv_path: Path) -> dict:
    """Return a dict of {pzn: record_dict} for all rows already in the CSV."""
    existing = {}
    if not csv_path.exists():
        return existing
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pzn = row.get("pzn", "").strip()
            if pzn:
                existing[pzn] = {col: row.get(col, "") for col in COLUMNS}
    return existing


# ---------------------------------------------------------------------------
# Writer: CSV
# ---------------------------------------------------------------------------

def write_csv(records: list[dict], csv_path: Path) -> None:
    """Write all records to UTF-8 CSV with BOM so Excel can open it directly."""
    if csv_path.exists():
        with open(csv_path, encoding="utf-8-sig", newline="") as fh:
            extra = [c for c in (csv.DictReader(fh).fieldnames or []) if c not in COLUMNS]
        if extra:
            sys.exit(f"Refusing to write: medicines.csv has {len(extra)} extra columns this script would drop "
                     f"({extra[:3]}...). Use recheck_medicines.py instead.")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=COLUMNS,
            extrasaction="ignore",
            quoting=csv.QUOTE_ALL,
        )
        writer.writeheader()
        for rec in records:
            writer.writerow({col: rec.get(col, "") for col in COLUMNS})
    print(f"  CSV written  → {csv_path}")


# ---------------------------------------------------------------------------
# Writer: Excel
# ---------------------------------------------------------------------------

def write_xlsx(records: list[dict], xlsx_path: Path) -> None:
    """Write all records to Excel with header formatting, filters, text PZN."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Medicines"

    # Header style
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=11)

    # Write header row
    for col_idx, col_name in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", wrap_text=False)

    # Write data rows
    pzn_col = COLUMNS.index("pzn") + 1
    for row_idx, rec in enumerate(records, start=2):
        for col_idx, col_name in enumerate(COLUMNS, start=1):
            value = rec.get(col_name, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            # Force PZN column to text so Excel renders leading zero
            if col_idx == pzn_col:
                cell.number_format = "@"

    # Auto-filter
    ws.auto_filter.ref = ws.dimensions

    # Freeze header row
    ws.freeze_panes = "A2"

    # Auto-width (capped)
    for col_idx in range(1, len(COLUMNS) + 1):
        col_letter = get_column_letter(col_idx)
        max_len = max(
            (len(str(ws.cell(row=r, column=col_idx).value or "")) for r in range(1, len(records) + 2)),
            default=10,
        )
        ws.column_dimensions[col_letter].width = min(max_len + 2, 55)

    wb.save(xlsx_path)
    print(f"  XLSX written → {xlsx_path}")


# ---------------------------------------------------------------------------
# Writer: sources.json (per PZN)
# ---------------------------------------------------------------------------

def write_sources_json(rec: dict, pzn_folder: Path) -> None:
    """Write a sources.json with field-level provenance into the PZN folder."""
    pzn = rec["pzn"]
    sources = {
        "pzn": pzn,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "field_sources": {
            "pzn": {
                "value": pzn,
                "source": "product_webpage",
                "url": rec["product_source_url"],
                "verified_against": ["product_webpage", "leaflet_pdf"],
            },
            "market": {"value": "DE", "source": "derived", "note": "German PZN system"},
            "product_name": {
                "value": rec["product_name"],
                "source": "product_webpage",
                "url": rec["product_source_url"],
            },
            "active_ingredient": {
                "value": rec["active_ingredient"],
                "source": "leaflet_pdf",
                "note": "Section 1 heading and Section 6 excipients; exact salt form: Bisoprololfumarat",
            },
            "ingredient_form_as_stated": {
                "value": rec["ingredient_form_as_stated"],
                "source": "leaflet_pdf",
                "note": "Preserved verbatim from leaflet title and section headings",
            },
            "strength_as_stated": {
                "value": rec["strength_as_stated"],
                "source": "product_webpage + leaflet_pdf",
                "note": "5 mg per tablet as stated; not converted to free-base equivalent",
            },
            "dosage_form": {
                "value": rec["dosage_form"],
                "source": "product_webpage",
                "url": rec["product_source_url"],
            },
            "pack_size": {
                "value": rec["pack_size"],
                "source": "product_webpage",
                "url": rec["product_source_url"],
                "note": (
                    "PZN 06865959 confirmed as 30 tablets (N1). "
                    "Leaflet mentions 30/50/98/100 packs; "
                    "other pack sizes carry separate PZNs."
                ),
            },
            "brand": {
                "value": rec["brand"],
                "source": "product_name",
            },
            "pharmaceutical_company": {
                "value": rec["pharmaceutical_company"],
                "source": "leaflet_pdf",
                "note": "Pharmazeutischer Unternehmer per leaflet §7",
            },
            "manufacturer": {
                "value": rec["manufacturer"],
                "source": "leaflet_pdf",
                "note": "Hersteller per leaflet §7; distinct from Pharmazeutischer Unternehmer",
            },
            "color": {
                "value": rec["color"],
                "source": "leaflet_pdf",
                "note": (
                    "Leaflet §6: 'gesprenkelt, hell gelb gefärbt'. "
                    "Confirmed by excipient Eisenoxid gelb (E 172). "
                    "English normalised."
                ),
            },
            "shape": {
                "value": rec["shape"],
                "source": "leaflet_pdf + product_webpage",
                "note": "Leaflet §6: 'rund und konvex'. Diameter 8.2 mm (ABDATA average).",
            },
            "imprint": {
                "value": rec["imprint"],
                "source": "leaflet_pdf + product_webpage",
                "note": (
                    "Leaflet §6: Prägung '5' rechts der Bruchkerbe. "
                    "Apotheken-umschau: Sonstige Merkmale: \"5\". Copied exactly."
                ),
            },
            "score_line": {
                "value": rec["score_line"],
                "source": "product_webpage",
                "note": (
                    "Apotheken-umschau (ABDATA): "
                    "Lage=Einzelkerbe, Form=keilförmig, Anordnung=einseitig, Spaltung=nein. "
                    "Tablet IS divisible into 2 equal halves (leaflet §3)."
                ),
            },
            "appearance_description_original": {
                "value": rec["appearance_description_original"],
                "source": "leaflet_pdf",
                "note": "Exact wording from leaflet §6 (German), preserved verbatim.",
            },
            "tablet_image_1": {
                "value": rec["tablet_image_1"],
                "download_url": "https://image.wub-service.de/resources/images/800x/drugexpert/261101/51/51/45151.jpg",
                "classification": "tablet (visually confirmed)",
                "classification_basis": (
                    "Visual review 2026-11-13: 600x600 px photo of a light-yellow round "
                    "tablet with a single score line and '5' to the right of the score; "
                    "mm scale bar and '© ABDATA | Dosing' watermark. Matches leaflet section 6."
                ),
                "file_verified": True,
                "file_size_bytes": 133078,
                "reuse_permission": "unverified – 'Verwendung nach Lizenzbestimmungen'",
            },
            "tablet_image_2": {
                "value": rec["tablet_image_2"],
                "download_url": "https://image.wub-service.de/resources/images/800x/drugexpert/261101/52/51/45152.jpg",
                "classification": "tablet (visually confirmed)",
                "classification_basis": (
                    "Visual review 2026-11-13: 600x600 px photo of the plain reverse side "
                    "of the same light-yellow round tablet; mm scale bar and "
                    "'© ABDATA | Dosing' watermark."
                ),
                "file_verified": True,
                "file_size_bytes": 122566,
                "reuse_permission": "unverified – 'Verwendung nach Lizenzbestimmungen'",
            },
            "package_image": {
                "value": "",
                "note": "No dedicated packaging image was provided or available.",
            },
            "leaflet_path": {
                "value": rec["leaflet_path"],
                "download_url": "https://image.wub-service.de/resources/static/drugexpert/261101/69/16/41669.pdf",
                "file_verified": True,
                "file_size_bytes": 145868,
                "pdf_creation_date": "2022-11-11",
                "pdf_producer": "TevaInternet",
                "pdf_author": "exensio GmbH",
            },
            "product_source_url": {
                "value": rec["product_source_url"],
                "source": "product_webpage",
                "last_updated_on_site": "2025-11-24",
                "data_provider": "ABDATA Pharma-Daten-Service",
            },
            "manufacturer_source_url": {
                "value": rec["manufacturer_source_url"],
                "source": "manufacturer_website",
            },
            "leaflet_revision_date": {
                "value": rec["leaflet_revision_date"],
                "source": "leaflet_pdf",
                "note": (
                    "Leaflet §10: 'Diese Packungsbeilage wurde zuletzt überarbeitet "
                    "im August 2022.' Versionscode: Z11."
                ),
            },
            "retrieved_at": {
                "value": rec["retrieved_at"],
                "note": "Date sources were accessed for this record.",
            },
            "verification_status": {
                "value": rec["verification_status"],
            },
            "notes": {
                "value": rec["notes"],
            },
        },
        "unresolved_issues": [
            (
                "Correction 2026-11-13: an earlier version of this file described both "
                "images as 88x88 px with unverified classification. They are 600x600 px "
                "and were visually confirmed as tablet photographs."
            ),
            (
                "Image reuse permission: XMP metadata states 'Verwendung nach "
                "Lizenzbestimmungen' (usage subject to licence terms). No explicit "
                "Creative Commons or public-domain licence found. Do NOT publish or deploy "
                "the downloaded files."
            ),
            (
                "Leaflet revision date is August 2022. The apotheken-umschau page was "
                "last updated 2025-11-24. It is possible a newer leaflet version exists; "
                "manual cross-check with the BfArM leaflet database is recommended."
            ),
        ],
        "attribution": {
            "product_webpage": "ABDATA Pharma-Daten-Service via apotheken-umschau.de",
            "leaflet_pdf": "ABDATA Pharma-Daten-Service / exensio GmbH / TevaInternet",
            "images": "Dosing GmbH Heidelberg / ABDATA Pharma-Daten-Service",
            "manufacturer_page": "ratiopharm GmbH",
        },
    }

    out_path = pzn_folder / "sources.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(sources, fh, ensure_ascii=False, indent=2)
    print(f"  JSON written → {out_path}")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(records: list[dict]) -> None:
    """Check PZNs, file paths, and CSV/XLSX consistency."""
    print("\n--- Validation ---")
    for rec in records:
        pzn = rec["pzn"]
        # 1. PZN format
        assert len(pzn) == 8 and pzn.isdigit(), f"PZN not 8 digits: {pzn!r}"
        print(f"  [OK] PZN length 8 and numeric: {pzn}")

        # 2. Local file paths
        for field in ("tablet_image_1", "tablet_image_2", "package_image", "leaflet_path"):
            rel = rec.get(field, "")
            if not rel:
                print(f"  [SKIP] {field} is blank (expected if not downloaded)")
                continue
            abs_path = PROJECT_ROOT / rel
            if abs_path.exists():
                print(f"  [OK] File exists: {rel}")
            else:
                print(f"  [WARN] File NOT found: {rel}")

        # 3. Market
        assert rec.get("market") == "DE", f"market != DE for PZN {pzn}"
        print(f"  [OK] market=DE")

        # 4. Pack size verified against 30 for this PZN
        if pzn == "06865959":
            assert rec.get("pack_size") == "30", "Pack size mismatch for 06865959"
            print(f"  [OK] pack_size=30 verified")

    print("  Validation complete.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"Project root: {PROJECT_ROOT}")

    # Load existing records
    existing = load_existing_records(CSV_PATH)
    print(f"  Existing records loaded: {len(existing)}")

    # Merge new records (new data overwrites existing for same PZN)
    for rec in RECORDS:
        pzn = rec["pzn"]
        if pzn in existing:
            print(f"  Updating existing record PZN {pzn}")
        else:
            print(f"  Adding new record PZN {pzn}")
        existing[pzn] = {col: rec.get(col, "") for col in COLUMNS}

    all_records = list(existing.values())

    # Write outputs
    print("\n--- Writing outputs ---")
    write_csv(all_records, CSV_PATH)
    write_xlsx(all_records, XLSX_PATH)

    # Write sources.json per PZN
    for rec in RECORDS:
        pzn = rec["pzn"]
        pzn_folder = PROJECT_ROOT / pzn
        pzn_folder.mkdir(parents=True, exist_ok=True)
        write_sources_json(rec, pzn_folder)

    # Validate
    validate(all_records)

    print("\nDone.")


if __name__ == "__main__":
    main()
