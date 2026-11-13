#!/usr/bin/env python3
"""
add_polypharmacy_text_records.py
--------------------------------
Append the products of polypharmacy_collection_list.csv to medicines.csv / .xlsx
as TEXT-ONLY records.

Source: the facts shown at the top of each product page (PZN, name, pack size,
dosage form, active substance line, 'Anbieter', leaflet document date), noted
once per page on 2026-11-13 while building the list. No network access here,
nothing is downloaded. Photo-, leaflet- and appearance-based columns stay blank.

Existing records are never changed; duplicates (market + PZN) are skipped.
"""
import csv
import shutil
from datetime import datetime
from pathlib import Path

import finalize_medicines as f  # COLUMNS (26-column layout) and write_xlsx

ROOT = Path(__file__).parent.resolve()
LIST = ROOT / "polypharmacy_collection_list.csv"

# pzn: (product_name, dosage_form, pack, ingredient_form_as_stated, brand, company, doc_date)
PAGE = {
    "00524192": ("ATORVASTATIN BASICS 10 mg Filmtabletten", "Filmtabletten", "30", "Atorvastatin hemicalcium-1,5-Wasser", "ATORVASTATIN BASICS", "Basics GmbH", "2025-09"),
    "13817228": ("ATORVASTATIN AXiromed 10 mg Filmtabletten", "Filmtabletten", "50", "Atorvastatin hemicalcium-1,5-Wasser", "ATORVASTATIN AXiromed", "Medical Valley Invest AB", "2023-11"),
    "15784295": ("ATORVASTATIN Zentiva 10 mg Filmtabletten", "Filmtabletten", "30", "Atorvastatin hemicalcium-1,5-Wasser", "ATORVASTATIN Zentiva", "Zentiva Pharma GmbH", "2024-08"),
    "01249150": ("PANTOPRAZOL AL 20 mg magensaftresistente Tabletten", "Tabletten, magensaftgeschützte", "14", "Pantoprazol natrium-1,5-Wasser", "PANTOPRAZOL AL", "ALIUD Pharma GmbH", "2024-03"),
    "01271847": ("PANTOPRAZOL-CT 40 mg magensaftresistente Tabletten", "Tabletten, magensaftgeschützte", "100", "Pantoprazol natrium-1,5-Wasser", "PANTOPRAZOL-CT", "AbZ-Pharma GmbH", "2025-10"),
    "01647755": ("ELIQUIS 5 mg Filmtabletten", "Filmtabletten", "20", "Apixaban", "ELIQUIS", "Bristol-Myers Squibb GmbH & Co. KGaA / Pfizer Pharma GmbH", "2025-04"),
    "08400029": ("ELIQUIS 2,5 mg Filmtabletten", "Filmtabletten", "20", "Apixaban", "ELIQUIS", "Bristol-Myers Squibb GmbH & Co. KGaA / Pfizer Pharma GmbH", "2025-04"),
    "01343676": ("ASS-ratiopharm 100 mg TAH Tabletten", "Tabletten", "50", "Acetylsalicylsäure", "ASS-ratiopharm", "ratiopharm GmbH", "2025-01"),
    "07402204": ("ASS 100 mg HEXAL Tabletten", "Tabletten", "50", "Acetylsalicylsäure", "ASS HEXAL", "Hexal AG", "2024-12"),
    "09064645": ("ASS Dexcel 100 mg Tabletten", "Tabletten", "50", "Acetylsalicylsäure", "ASS Dexcel", "Dexcel Pharma GmbH", "2025-02"),
    "00824600": ("METFORMIN-CT 1.000 mg Filmtabletten", "Filmtabletten", "120", "Metformin hydrochlorid", "METFORMIN-CT", "AbZ-Pharma GmbH", "2025-01"),
    "00823919": ("METFORMIN-ratiopharm 1.000 mg Filmtabletten", "Filmtabletten", "120", "Metformin hydrochlorid", "METFORMIN-ratiopharm", "ratiopharm GmbH", "2025-01"),
    "17448940": ("METFORMIN Aurobindo 1000 mg Filmtabletten", "Filmtabletten", "120", "Metformin hydrochlorid", "METFORMIN Aurobindo", "PUREN Pharma GmbH & Co. KG", "2025-03"),
    "02634097": ("AMLODIPIN-ratiopharm 5 mg N Tabletten", "Tabletten", "20", "Amlodipin besilat", "AMLODIPIN-ratiopharm", "ratiopharm GmbH", "2022-08"),
    "07018581": ("AMLODIPIN HEXAL 5 mg Tabletten", "Tabletten", "20", "Amlodipin besilat", "AMLODIPIN HEXAL", "Hexal AG", "2022-06"),
    "04342967": ("AMLODIPIN besilat AbZ 5 mg Tabletten", "Tabletten", "50", "Amlodipin besilat", "AMLODIPIN besilat AbZ", "AbZ-Pharma GmbH", "2022-08"),
    "02050640": ("EUTHYROX 100 Mikrogramm Tabletten", "Tabletten", "50", "Levothyroxin natrium", "EUTHYROX", "Merck Healthcare Germany GmbH", "2025-11"),
    "00811738": ("L-THYROX HEXAL 100 Mikrogramm Tabletten", "Tabletten", "50", "Levothyroxin natrium", "L-THYROX HEXAL", "Hexal AG", "2023-06"),
    "01686206": ("ALLOPURINOL-ratiopharm 300 mg Tabletten", "Tabletten", "20", "Allopurinol", "ALLOPURINOL-ratiopharm", "ratiopharm GmbH", "2025-02"),
    "03399824": ("ALLOPURINOL AL 300 Tabletten", "Tabletten", "20", "Allopurinol", "ALLOPURINOL AL", "ALIUD Pharma GmbH", "2024-11"),
    "01014636": ("ALLOPURINOL AbZ 300 mg Tabletten", "Tabletten", "30", "Allopurinol", "ALLOPURINOL AbZ", "AbZ-Pharma GmbH", "2025-02"),
    "01896961": ("TAMSULOSIN BASICS 0,4 mg Hartkaps.retard.", "Retard-Kapseln", "50", "Tamsulosin hydrochlorid", "TAMSULOSIN BASICS", "Basics GmbH", "2024-12"),
    "14169949": ("TAMSULOSIN Zentiva 0,4 mg Hartk.verä.Wst.-Frs.", "Retard-Kapseln", "20", "Tamsulosin hydrochlorid", "TAMSULOSIN Zentiva", "Zentiva Pharma GmbH", "2024-05"),
    "07519857": ("HCT-CT 25 mg Tabletten", "Tabletten", "50", "Hydrochlorothiazid", "HCT-CT", "AbZ-Pharma GmbH", "2021-11"),
    "01550375": ("HCT-RATIOPHARM 25 mg Tabletten", "Tabletten", "30", "Hydrochlorothiazid", "HCT-RATIOPHARM", "ratiopharm GmbH", "2021-11"),
    "00271851": ("HCT HEXAL 25 mg Tabletten", "Tabletten", "30", "Hydrochlorothiazid", "HCT HEXAL", "Hexal AG", "2021-12"),
    "02709837": ("TELMISARTAN-ratiopharm 20 mg Tabletten", "Tabletten", "28", "Telmisartan", "TELMISARTAN-ratiopharm", "ratiopharm GmbH", "2026-03"),
    "07202936": ("TELMISARTAN AL 40 mg Tabletten", "Tabletten", "98", "Telmisartan", "TELMISARTAN AL", "ALIUD Pharma GmbH", "2024-12"),
    "08926671": ("TELMISARTAN HEXAL 20 mg Tabletten", "Tabletten", "28", "Telmisartan", "TELMISARTAN HEXAL", "Hexal AG", "2026-08"),
    "17418844": ("METAMIZOL Zentiva 500 mg Filmtabletten", "Filmtabletten", "10", "Metamizol natrium-1-Wasser", "METAMIZOL Zentiva", "Zentiva Pharma GmbH", "2024-11"),
    "09436064": ("METAMIZOL AbZ 500 mg Tabletten", "Tabletten", "50", "Metamizol natrium-1-Wasser", "METAMIZOL AbZ", "AbZ-Pharma GmbH", "2024-11"),
    "12526983": ("METAMIZOL Aristo 500 mg Tabletten", "Tabletten", "10", "Metamizol natrium-1-Wasser", "METAMIZOL Aristo", "Aristo Pharma GmbH", "2025-08"),
}

# Short English notes for product-specific points (from the list's notes_zh)
EXTRA = {
    "Atorvastatin": "Strength refers to atorvastatin; the salt (hemicalcium) amount is higher.",
    "Pantoprazol": "Gastro-resistant tablet: do not split or chew.",
    "Apixaban": "Original brand; 5 mg and 2.5 mg are different strengths, not interchangeable.",
    "Acetylsalicylsäure": "Low-dose ASS 100 (antiplatelet), not the pain-relief dose.",
    "Metformin": "Strength is the salt metformin hydrochloride.",
    "Amlodipin": "Strength refers to amlodipine; the salt (besilate) amount is higher.",
    "Levothyroxin": "Strength in micrograms; levothyroxine only (not a combination with iodide).",
    "Tamsulosin": "Prolonged-release hard capsule.",
    "Telmisartan": "Telmisartan only (not the combination with HCT).",
    "Metamizol": "Strength is the salt metamizole sodium monohydrate.",
}


def main():
    with open(f.MED_CSV, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        assert rd.fieldnames == f.COLUMNS, "medicines.csv is not in the 26-column layout"
        rows = list(rd)
    have = {(r["market"], r["pzn"]) for r in rows}
    with open(LIST, encoding="utf-8-sig", newline="") as fh:
        lst = list(csv.DictReader(fh))
    added, skipped = [], []
    for L in lst:
        pzn = L["pzn"]
        if (L["market"], pzn) in have:
            skipped.append(pzn)
            continue
        name, form, pack, ing_form, brand, company, doc = PAGE[pzn]
        assert pack == L["pack_size_units"] and name == L["example_product_name"], pzn
        notes = ["Text-only record from the product page; tablet photos, leaflet and appearance not collected.",
                 "Company taken from the product page."]
        if L["active_ingredient"] in EXTRA:
            notes.append(EXTRA[L["active_ingredient"]])
        rec = {c: "" for c in f.COLUMNS}
        rec.update({
            "pzn": pzn, "market": "DE", "product_name": name,
            "active_ingredient": L["active_ingredient"], "ingredient_form_as_stated": ing_form,
            "strength_as_stated": L["example_strength_as_stated"], "dosage_form": form, "pack_size": pack,
            "brand": brand, "pharmaceutical_company": company,
            "product_source_url": L["product_source_url"], "leaflet_revision_date": doc,
            "retrieved_at": "2026-11-13",
            "verification_status": "Checked against product page (text only); photos and leaflet not collected; not pharmacist verified.",
            "notes": " ".join(notes),
        })
        rows.append(rec)
        have.add((L["market"], pzn))
        added.append(pzn)
    bk = ROOT / "backups" / ("polypharmacy_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    bk.mkdir(parents=True)
    for p in (f.MED_CSV, f.MED_XLSX):
        shutil.copy2(p, bk / p.name)
    with open(f.MED_CSV, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=f.COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    f.write_xlsx(rows)
    print(f"added {len(added)} | skipped (already present) {len(skipped)} | total rows {len(rows)} | backup {bk}")


if __name__ == "__main__":
    main()
