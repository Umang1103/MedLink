#!/usr/bin/env python3
"""
finalize_medicines.py
---------------------
Produce the clean, original-layout medicines.csv / medicines.xlsx (26 columns).

* Keeps every record and its checked values.
* Fills blank color / shape / imprint from tablet_photo_review.json
  (visual review of the local tablet photos). Text evidence always wins.
* Shortens company / manufacturer to company names, verification_status
  and notes to short plain statements.
* Drops the list-derived extra columns (they stay in the collection list CSV).
No network access. Backs up both files first.

Usage: python finalize_medicines.py
"""
import csv
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
MED_CSV, MED_XLSX = ROOT / "medicines.csv", ROOT / "medicines.xlsx"
COLUMNS = [
    "pzn", "market", "product_name", "active_ingredient", "ingredient_form_as_stated",
    "strength_as_stated", "dosage_form", "pack_size", "brand", "pharmaceutical_company",
    "manufacturer", "color", "shape", "imprint", "score_line",
    "appearance_description_original", "tablet_image_1", "tablet_image_2", "package_image",
    "leaflet_path", "product_source_url", "manufacturer_source_url", "leaflet_revision_date",
    "retrieved_at", "verification_status", "notes",
]

LEGAL = (r"GmbH\s*&\s*Co\.?\s*(?:Generica\s+)?KG\b|\bKGaA\b|\bGmbH\b|\bAG\b|\bKG\b|\bS\.p\.A\.|\bS\.[Rr]\.[Ll]\.?|"
         r"\bSRL\b|\bS\.A\.U\.?|\bS\.A\.S\.?|\bS\.L\.U\.?|\bS\.L\.|\bSL\b|\bS\.\s?A\.?|\bSA\b|\bB\.V\.?|\b[Bb][Vv]\b|"
         r"\bN\.V\.?|\bLtd\b\.?|\bLimited\b(?:\s+Company\b)?|\bSp\.\s*z\s*o\.\s*o\.?|\bs\.r\.o\.?|\bk\.s\.?|\ba\.s\.?|"
         r"\bd\.\s?d\.?|\bd\.o\.o\.?|\bZrt\b\.?|\bKft\b\.?|\bOy\b|\bAB\b|\bA/S\b|\bInc\b\.?|\bplc\b|\bAD\b|\bLda\b\.?|\bEOOD\b")
STREET = re.compile(r"\s+[\wäöüßÄÖÜ-]*(?:straße|strasse|str\.|damm|weg|allee|platz|gasse|ring|chaussee)\b.*$|"
                    r"\s+(?:Via|Calle|Avda\.?|Ul\.|ul\.|B-dul\.?|Polígon|Rua|Zac)\b.*$|\s+\d.*$", re.I)
BAD_START = re.compile(r"^(?:Für|Bitte|Die|Der|Das|Falls|phone|Phone|Tel|Fax|Stand|Betriebsstätte)\b")
COUNTRIES = (r"Deutschland|Polen|Irland|Malta|Niederlande|Ungarn|Spanien|Italien|Frankreich|Portugal|Rumänien|"
             r"Griechenland|Österreich|Finnland|Vereinigtes Königreich|Tschechische Republik|Tschechien|Slowenien|"
             r"Kroatien|Bulgarien|Island|Schweden|Belgien|Indien|Zypern|Lettland|Litauen|Slowakei|Schweiz|Dänemark|"
             r"Luxemburg|Estland|Norwegen|Großbritannien")  # not 'UK'/'Polska'/'Hungary': they occur inside names


# Where one site address ends and the next company name may begin:
# a postcode+city, a country word NOT directly followed by a legal form/')', or ';'.
BOUNDARY = re.compile(
    r"\b\d{4,5}\s+[A-ZÄÖÜ][\wäöüß.-]+(?:\s+(?:am|an der|im|ob der)\s+[A-ZÄÖÜ][\wäöüß]+)?"
    r"|\b(?:" + COUNTRIES + r")\b(?!\s*\))(?!\s+(?:" + LEGAL + r"))"
    r"|;")


def company_names(s):
    """'Hexal AG Industriestraße 25 83607 Holzkirchen' -> 'Hexal AG'; several companies -> joined by '; '."""
    s = re.sub(r"[\ue000-\uf8ff®]", "", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\bb -Pharma GmbH A Z\b", "AbZ-Pharma GmbH", s)  # logo-style 'AbZ' split by the PDF text layer
    if not s:
        return ""
    names = []
    for part in re.split(r";\s*(?:oder\s+)?|\s+(?:oder|Oder|ODER|und)\s*:?\s+(?=[A-Z])", s):
        part = re.sub(r"^(?:Pharmazeutischer Unternehmer|Hersteller|Zulassungsinhaber)\s*:?\s*", "", part.strip())
        legal = list(re.finditer(LEGAL, part))
        bounds = [x.end() for x in BOUNDARY.finditer(part)]  # on the full text, so look-aheads see the legal form
        found = []
        last_end = 0
        for m in legal:
            if m.start() < last_end:
                continue
            head_end = last_end + len(part[last_end:m.start()].rstrip(", "))  # keep 'Krka, d.d.' together
            b = [e for e in bounds if last_end <= e <= head_end]
            start = b[-1] if b else last_end
            name = part[start:m.end()].strip(" ,;:-")
            name = re.sub(r"^\d{4,5}\s+", "", name)
            if name and not BAD_START.match(name):
                found.append(name)
            last_end = m.end()
        if not found:  # no legal form at all: name ends before the street / number
            name = STREET.sub("", part).strip(" ,;:-")
            if name and len(name.split()) <= 5 and not BAD_START.match(name):
                found.append(name)
        for n in found:
            if len(n) > 2 and not re.fullmatch(r"(?:" + LEGAL + r")", n) and n not in names:
                names.append(n)
    return "; ".join(names)


def main():
    with open(MED_CSV, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        rows = list(rd)
    if rd.fieldnames == COLUMNS:
        raise SystemExit("medicines.csv is already in the final 26-column layout; nothing to do.")
    photos = json.loads((ROOT / "tablet_photo_review.json").read_text(encoding="utf-8"))
    out, stats = [], {"color": 0, "shape": 0, "imprint": 0, "imprint_none": 0}
    for r in rows:
        p = r["pzn"]
        rec = {c: r.get(c, "") for c in COLUMNS}
        notes = []
        ph = photos.get(p)
        from_photo = []
        if ph:
            colour, shape, imprint = ph
            if not rec["color"]:
                rec["color"] = colour; stats["color"] += 1; from_photo.append("colour")
            if not rec["shape"]:
                rec["shape"] = shape; stats["shape"] += 1; from_photo.append("shape")
            if not rec["imprint"] and imprint:
                rec["imprint"] = imprint
                if imprint == "none":
                    stats["imprint_none"] += 1
                else:
                    stats["imprint"] += 1; from_photo.append("imprint")
        if from_photo:
            notes.append(", ".join(from_photo).capitalize() + " taken from the tablet photo.")
        if not rec["imprint"] and rec["tablet_image_1"] and not rec["tablet_image_2"]:
            notes.append("Imprint unknown: only one side of the tablet is photographed.")
        elif not rec["imprint"] and rec["tablet_image_1"]:
            notes.append("Imprint not legible on the photos.")

        # short company names
        rec["pharmaceutical_company"] = company_names(rec["pharmaceutical_company"])
        rec["manufacturer"] = company_names(rec["manufacturer"])

        # key flags from the old notes, in plain words
        old = r.get("notes", "") + " " + r.get("imprint", "")
        if re.search(r"parallel import|PARALLEL IMPORT", old, re.I):
            m = re.search(r"(?:umgepackt von|repackager:)\s*([^,;.]+?(?:GmbH|AG))", old)
            notes.append(f"Parallel import, repackaged by {m.group(1).strip()}." if m else "Parallel import.")
        if rec["imprint"].startswith("CONFLICT"):
            notes.append("Imprint differs between leaflet and product page.")
        if re.search(r"COMBINATION PRODUCT", old):
            notes.append("Combination product (two active ingredients).")
        if not rec["leaflet_path"]:
            notes.append("No leaflet PDF available.")
        if not rec["tablet_image_1"]:
            notes.append("No tablet photo available.")
        if not rec["color"] or not rec["shape"]:
            notes.append("Colour/shape not available from any source.")
        rec["notes"] = " ".join(notes)
        rec["verification_status"] = "Checked against product page, leaflet and tablet photos; not pharmacist verified."
        out.append(rec)

    bk = ROOT / "backups" / ("finalize_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    bk.mkdir(parents=True)
    for f in (MED_CSV, MED_XLSX):
        shutil.copy2(f, bk / f.name)
    with open(MED_CSV, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(out)
    write_xlsx(out)
    print("rows:", len(out), "| filled from photos:", stats, "| backup:", bk)


def write_xlsx(records):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Medicines"
    for i, c in enumerate(COLUMNS, 1):
        cell = ws.cell(row=1, column=i, value=c)
        cell.fill = PatternFill("solid", start_color="1F4E79", end_color="1F4E79")
        cell.font = Font(bold=True, color="FFFFFF")
    for ri, r in enumerate(records, 2):
        for ci, c in enumerate(COLUMNS, 1):
            cell = ws.cell(row=ri, column=ci, value=r[c])
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if c == "pzn":
                cell.number_format = "@"
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    for ci, c in enumerate(COLUMNS, 1):
        width = max(len(str(ws.cell(row=r, column=ci).value or "")) for r in range(1, len(records) + 2))
        ws.column_dimensions[get_column_letter(ci)].width = min(width + 2, 45)
    wb.save(MED_XLSX)


if __name__ == "__main__":
    main()
