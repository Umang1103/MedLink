#!/usr/bin/env python3
"""
collect_medications.py
----------------------
Batch workflow for the German medication database.

Stage 1  (python collect_medications.py fetch)
    Reads heart_failure_collection_list.csv, visits every product_source_url,
    downloads the images and the leaflet PDF that the page itself links to,
    verifies file types by magic bytes, and writes <PZN>/raw_extract.json.
    Rows whose PZN already exists in medicines.csv are skipped.

Stage 2  (python collect_medications.py build)
    Merges raw_extract.json + curated_fields.json (hand-reviewed normalised
    English fields) into medicines.csv / medicines.xlsx and writes
    <PZN>/sources.json.  Existing rows are preserved; a PZN already in
    medicines.csv is never overwritten.

Rules: no guessing. Missing values stay blank. Nothing is bypassed: only
URLs that the product page itself exposes are downloaded.
Downloaded images/PDFs are NOT licensed for reuse; do not publish them.
"""
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent.resolve()
LIST_CSV = ROOT / "heart_failure_collection_list.csv"
MED_CSV = ROOT / "medicines.csv"
MED_XLSX = ROOT / "medicines.xlsx"
CURATED = ROOT / "curated_fields.json"
UA = {"User-Agent": "Mozilla/5.0 (hackathon research script; contact: local user)"}
REFRESH = "--refresh" in sys.argv
DELAY = 2.0  # seconds between requests

COLUMNS = [
    "pzn", "market", "product_name", "active_ingredient", "ingredient_form_as_stated",
    "strength_as_stated", "dosage_form", "pack_size", "brand", "pharmaceutical_company",
    "manufacturer", "color", "shape", "imprint", "score_line",
    "appearance_description_original", "tablet_image_1", "tablet_image_2", "package_image",
    "leaflet_path", "product_source_url", "manufacturer_source_url", "leaflet_revision_date",
    "retrieved_at", "verification_status", "notes",
]


def read_list():
    with open(LIST_CSV, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def existing_pzns():
    if not MED_CSV.exists():
        return set()
    with open(MED_CSV, encoding="utf-8-sig", newline="") as fh:
        return {r["pzn"].strip() for r in csv.DictReader(fh)}


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def sniff(data: bytes):
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


def jpeg_size(data: bytes):
    """Return (w, h) of the main image: largest SOF frame found."""
    best = (0, 0)
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        seg = (data[i + 2] << 8) + data[i + 3]
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            h = (data[i + 5] << 8) + data[i + 6]
            w = (data[i + 7] << 8) + data[i + 8]
            if w * h > best[0] * best[1]:
                best = (w, h)
        i += 2 + seg
    return best


def parse_page(html: str, base: str):
    soup = BeautifulSoup(html, "html.parser")
    text = clean(soup.get_text(" "))
    out = {}
    h1 = soup.find("h1")
    out["page_title"] = clean(h1.get_text()) if h1 else ""

    m = re.search(r"PZN:\s*(\d{7,8})", text)
    out["pzn_on_page"] = m.group(1).zfill(8) if m else ""
    m = re.search(r"Packungsgröße:\s*(.+?)\s+Abgabeform:", text)
    out["pack_size_on_page"] = clean(m.group(1)) if m else ""
    m = re.search(r"Abgabeform:\s*(.+?)\s+Darreichungsform:", text)
    out["dispensing_status"] = clean(m.group(1)) if m else ""
    m = re.search(r"Darreichungsform:\s*(.+?)\s+(?:weitere|Sonstige|In diesem)", text)
    out["dosage_form_on_page"] = clean(m.group(1)) if m else ""
    # Read the definition value directly; the surrounding text includes navigation.
    for dt in soup.find_all("dt"):
        label = clean(dt.get_text()).rstrip(":")
        field = {"PZN": "pzn_on_page", "Packungsgröße": "pack_size_on_page",
                 "Abgabeform": "dispensing_status", "Darreichungsform": "dosage_form_on_page"}.get(label)
        dd = dt.find_next_sibling("dd")
        if field and dd:
            out[field] = clean(dd.get_text(" "))
            if field == "pzn_on_page":
                out[field] = out[field].zfill(8)

    # Provider (Anbieter)
    prov = ""
    for dt in soup.find_all("dt"):
        if "Anbieter" in dt.get_text():
            dd = dt.find_next_sibling("dd")
            if dd:
                prov = clean(dd.get_text(" ").replace("www.", " www."))
    out["provider_on_page"] = prov

    # Active substances block
    subs = []
    for p in soup.find_all("p"):
        if clean(p.get_text()).startswith("Wirkstoff(e)"):
            ul = p.find_next_sibling("ul")
            if ul:
                subs = [clean(li.get_text(" ")) for li in ul.find_all("li")]
            break
    out["substances_on_page"] = subs

    # Excipients
    m = re.search(r"Sonstige Bestandteile (.+?) (?:Weitere Bestandteile|In diesem Beipackzettel)", text)
    out["excipients_on_page"] = clean(m.group(1)) if m else ""

    # Images
    prim = soup.find("meta", attrs={"itemprop": "primaryImageOfPage"})
    out["primary_image_url"] = prim["content"] if prim and prim.get("content") else ""
    pack_alt = ""
    for img in soup.find_all("img"):
        if "Packshot" in (img.get("alt") or ""):
            pack_alt = img.get("src", "")
            break
    out["packshot_url"] = pack_alt
    blk = soup.find("div", class_="dosageFormImages")
    imgs = []
    if blk:
        for img in blk.find_all("img"):
            src = img.get("src")
            if src and src not in imgs:
                imgs.append(urljoin(base, src))
    out["original_image_urls"] = imgs

    # Physical characteristics (text of the Originalbilder section)
    m = re.search(r"Originalbilder des Präparats (.+?) (?:Anwendungsgebiete|Dosierung und Anwendung)", text)
    out["physical_block_raw"] = clean(m.group(1)) if m else ""

    # PDF links
    pdfs = []
    for a in soup.find_all("a", href=True):
        if "?file=" in a["href"] or a["href"].lower().endswith(".pdf"):
            row = a.find_parent("div", class_="download-item")
            pdfs.append({
                "url": urljoin(base, a["href"]),
                "label": clean(a.get_text()),
                "doc_date": (clean(row.find("span", class_="small").get_text()) if row and row.find("span", class_="small") else ""),
            })
    out["pdf_links"] = pdfs

    t = soup.find("time", attrs={"itemprop": "lastReviewed"})
    out["page_last_reviewed"] = t.get("datetime", "") if t else ""
    return out


def download(sess, url, dest: Path, kinds):
    r = sess.get(url, headers=UA, timeout=60)
    info = {"url": url, "http_status": r.status_code,
            "content_type": r.headers.get("Content-Type", "")}
    if r.status_code != 200:
        info["ok"] = False
        info["error"] = f"HTTP {r.status_code}"
        return info
    kind = sniff(r.content)
    if kind not in kinds:
        info["ok"] = False
        info["error"] = f"unexpected content (sniffed={kind})"
        return info
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_bytes() != r.content:
        # Never overwrite a different existing file; keep the new one alongside.
        dest = dest.with_name(dest.stem + "_new" + dest.suffix)
        info["note"] = "existing file differed; saved under a new name"
    dest.write_bytes(r.content)
    info.update(ok=True, kind=kind, bytes=len(r.content), saved_as=dest.name)
    if kind == "jpeg":
        info["pixels"] = "%dx%d" % jpeg_size(r.content)
    return info


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    rd = PdfReader(str(path))
    return "\n".join((p.extract_text() or "") for p in rd.pages)


def pdf_fields(path: Path):
    """Verbatim helper extracts from the leaflet. Nothing is normalised here."""
    txt = pdf_text(path)
    flat = clean(txt)
    f = {"pdf_pages": None}
    m = re.search(r"Wie\s+(?:.{0,80}?)\s*aussieht und Inhalt der Packung\s*(.+?)\s*Pharmazeutischer Unternehmer", flat)
    f["section6_raw"] = clean(m.group(1)) if m else ""
    m = re.search(r"Pharmazeutischer Unternehmer\s*(.+?)\s*Hersteller\s*(.+?)\s*(?:Diese Packungsbeilage|Dieses Arzneimittel ist|Weitere Informationsquellen|Versionscode)", flat)
    f["company_raw"] = clean(m.group(1)) if m else ""
    f["manufacturer_raw"] = clean(m.group(2)) if m else ""
    m = re.search(r"(?:zuletzt\s+)?(?:überarbeitet|geändert)\s+im\s+([A-Za-zäöüÄÖÜ]+\s+\d{4})", flat)
    f["revision_raw"] = m.group(1) if m else ""
    m = re.search(r"Wirkstoff(?:e)?:?\s*(.{0,200})", flat)
    f["wirkstoff_raw"] = clean(m.group(1))[:200] if m else ""
    m = re.search(r"Was\s+.{0,60}?enthält\s*(.{0,500})", flat)
    f["composition_raw"] = clean(m.group(1))[:500] if m else ""
    f["pdf_title"] = ""
    return f


def fetch():
    rows = read_list()
    have = existing_pzns()
    sess = requests.Session()
    summary = []
    for row in rows:
        pzn = row["pzn"].strip()
        url = row["product_source_url"].strip()
        if pzn in have:
            print(f"[SKIP] {pzn} already in medicines.csv")
            summary.append((pzn, "skipped-existing"))
            continue
        folder = ROOT / pzn
        raw_path = folder / "raw_extract.json"
        if raw_path.exists() and not REFRESH:
            print(f"[SKIP] {pzn} already fetched (use 'fetch --refresh' to redo)")
            summary.append((pzn, "skipped-fetched"))
            continue
        print(f"[FETCH] {pzn} {row['example_product_name']}")
        rec = {"pzn": pzn, "list_row": row, "retrieved_at": datetime.now(timezone.utc).isoformat(),
               "downloads": {}, "issues": []}
        try:
            r = sess.get(url, headers=UA, timeout=60)
            rec["page_http_status"] = r.status_code
            if r.status_code != 200:
                rec["issues"].append(f"product page HTTP {r.status_code}")
            else:
                rec["page"] = parse_page(r.text, url)
        except Exception as exc:  # network errors are recorded, not hidden
            rec["issues"].append(f"product page fetch failed: {exc}")
        time.sleep(DELAY)
        page = rec.get("page")
        if page:
            if page["pzn_on_page"] != pzn:
                rec["issues"].append(f"PZN mismatch: list={pzn} page={page['pzn_on_page']!r}")
            folder.mkdir(parents=True, exist_ok=True)
            for idx, iu in enumerate(page["original_image_urls"], start=1):
                dest = folder / f"{pzn}_original_image_{idx}.jpg"
                try:
                    rec["downloads"][f"image_{idx}"] = download(sess, iu, dest, {"jpeg", "png"})
                except Exception as exc:
                    rec["downloads"][f"image_{idx}"] = {"url": iu, "ok": False, "error": str(exc)}
                time.sleep(DELAY)
            if not page["original_image_urls"]:
                rec["issues"].append("no product images listed on page")
            if page["pdf_links"]:
                pl = page["pdf_links"][0]
                dest = folder / f"{pzn}_leaflet.pdf"
                try:
                    d = download(sess, pl["url"], dest, {"pdf"})
                    if not d.get("ok") and "html" in d.get("content_type", ""):
                        # The ?file= link is a public viewer page that embeds the
                        # real PDF URL. Follow that link (no access gate involved).
                        viewer = sess.get(pl["url"], headers=UA, timeout=60).text
                        found = sorted(set(re.findall(
                            r'https://image\.wub-service\.de/[^"\'\s<>]+\.pdf', viewer)))
                        time.sleep(DELAY)
                        if len(found) == 1:
                            d = download(sess, found[0], dest, {"pdf"})
                            d["viewer_page"] = pl["url"]
                        else:
                            d["error"] += f"; viewer page PDF links found: {found}"
                            if len(found) > 1:
                                rec["issues"].append(f"ambiguous: {len(found)} PDFs in viewer page")
                    d["label"], d["doc_date"] = pl["label"], pl["doc_date"]
                    rec["downloads"]["leaflet"] = d
                    if d.get("ok"):
                        rec["leaflet_fields"] = pdf_fields(dest)
                except Exception as exc:
                    rec["downloads"]["leaflet"] = {"url": pl["url"], "ok": False, "error": str(exc)}
                time.sleep(DELAY)
                if len(page["pdf_links"]) > 1:
                    rec["issues"].append(f"{len(page['pdf_links'])} PDF links on page; only the first was downloaded")
            else:
                rec["issues"].append("no leaflet PDF link on page")
        folder.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        ok = [k for k, v in rec["downloads"].items() if v.get("ok")]
        bad = [k for k, v in rec["downloads"].items() if not v.get("ok")]
        print(f"        downloaded={ok} failed={bad} issues={rec['issues']}")
        summary.append((pzn, "fetched"))
    print("\nStage 1 done:", summary)


# Default provenance per field; curated "field_notes" add detail per product.
DEFAULT_SOURCE = {
    "pzn": "product_page (verified equal to list)",
    "market": "derived (German PZN)",
    "product_name": "leaflet_pdf title (German, preserved)",
    "active_ingredient": "leaflet_pdf section 6",
    "ingredient_form_as_stated": "leaflet_pdf section 6",
    "strength_as_stated": "product_name / leaflet_pdf section 6 (label strength, not converted)",
    "dosage_form": "product_page Darreichungsform + leaflet_pdf",
    "pack_size": "product_page Packungsgröße (verified for this PZN)",
    "brand": "product_name",
    "pharmaceutical_company": "leaflet_pdf section 6 (Pharmazeutischer Unternehmer / Zulassungsinhaber)",
    "manufacturer": "leaflet_pdf section 6 (Hersteller)",
    "color": "leaflet_pdf section 6, normalised to English",
    "shape": "leaflet_pdf section 6, normalised to English",
    "imprint": "product_page 'Sonstige Merkmale' + leaflet_pdf",
    "score_line": "product_page 'Angaben zu Kerben' + leaflet_pdf",
    "appearance_description_original": "leaflet_pdf section 6 (German, verbatim)",
    "tablet_image_1": "product_page 'Originalbilder des Präparats'",
    "tablet_image_2": "product_page 'Originalbilder des Präparats'",
    "package_image": "none available",
    "leaflet_path": "product_page 'PDF-Dokumente' link",
    "product_source_url": "heart_failure_collection_list.csv",
    "manufacturer_source_url": "not cross-checked",
    "leaflet_revision_date": "leaflet_pdf 'zuletzt überarbeitet im …'",
    "retrieved_at": "collection run date",
    "verification_status": "workflow status",
    "notes": "collector notes",
}


def build():
    """Merge curated records into medicines.csv / .xlsx and write sources.json."""
    if not CURATED.exists():
        sys.exit("curated_fields.json not found. Review raw_extract.json files first.")
    curated = json.loads(CURATED.read_text(encoding="utf-8"))
    existing = {}
    if MED_CSV.exists():
        with open(MED_CSV, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                existing[r["pzn"].strip()] = {c: r.get(c, "") for c in COLUMNS}
    added = []
    for pzn, entry in curated.items():
        if pzn.startswith("_"):
            continue
        if pzn in existing and not entry.get("_replace_existing"):
            print(f"[SKIP] {pzn} already present in medicines.csv")
            continue
        rec = {c: entry["record"].get(c, "") for c in COLUMNS}
        assert rec["pzn"] == pzn and len(pzn) == 8 and pzn.isdigit(), pzn
        for fld in ("tablet_image_1", "tablet_image_2", "package_image", "leaflet_path"):
            if rec[fld] and not (ROOT / rec[fld]).exists():
                print(f"[WARN] {pzn}: {fld} path missing, blanking: {rec[fld]}")
                rec[fld] = ""
        existing[pzn] = rec
        write_sources(pzn, rec, entry)
        added.append(pzn)
    write_outputs(list(existing.values()))
    print("Added/updated:", added)


def write_sources(pzn, rec, entry):
    raw_path = ROOT / pzn / "raw_extract.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    page = raw.get("page", {})
    dls = raw.get("downloads", {})
    notes = entry.get("field_notes", {})
    field_sources = {}
    for c in COLUMNS:
        fs = {"value": rec[c], "source": DEFAULT_SOURCE[c]}
        if c in notes:
            fs["note"] = notes[c]
        field_sources[c] = fs
    files = {}
    for key, fld in (("image_1", "tablet_image_1"), ("image_2", "tablet_image_2"), ("leaflet", "leaflet_path")):
        d = dls.get(key)
        if not d:
            continue
        files[fld] = {
            "local_path": rec[fld],
            "download_url": d.get("url"),
            "viewer_page": d.get("viewer_page"),
            "download_ok": d.get("ok"),
            "verified_type": d.get("kind"),
            "bytes": d.get("bytes"),
            "pixels": d.get("pixels"),
            "document_date_on_page": d.get("doc_date"),
        }
    sources = {
        "pzn": pzn,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "retrieved_at": raw.get("retrieved_at", rec["retrieved_at"]),
        "product_page": {
            "url": rec["product_source_url"],
            "http_status": raw.get("page_http_status"),
            "pzn_on_page": page.get("pzn_on_page"),
            "pack_size_on_page": page.get("pack_size_on_page"),
            "provider_on_page (Anbieter)": page.get("provider_on_page"),
            "substances_on_page": page.get("substances_on_page"),
            "physical_data_on_page": page.get("physical_block_raw"),
            "page_last_reviewed": page.get("page_last_reviewed"),
            "data_provider": "ABDATA Pharma-Daten-Service",
        },
        "files": files,
        "image_classification": entry.get("image_classification", {}),
        "field_sources": field_sources,
        "unresolved_issues": entry.get("issues", []) + raw.get("issues", []),
        "reuse_permission": (
            "unverified - image XMP: creator 'Dosing GmbH Heidelberg', rights 'ABDATA "
            "Pharma-Daten-Service', usage terms 'Verwendung nach Lizenzbestimmungen'. "
            "No explicit open licence found. Do not publish or deploy."
        ),
        "attribution": {
            "product_page": "ABDATA Pharma-Daten-Service via apotheken-umschau.de",
            "images": "© ABDATA | Dosing (watermark preserved, files unmodified)",
            "leaflet_pdf": "marketing authorisation holder's patient leaflet, hosted by image.wub-service.de",
        },
    }
    (ROOT / pzn / "sources.json").write_text(
        json.dumps(sources, ensure_ascii=False, indent=2), encoding="utf-8")


def write_outputs(records):
    if MED_CSV.exists():
        with open(MED_CSV, encoding="utf-8-sig", newline="") as fh:
            extra = [c for c in (csv.DictReader(fh).fieldnames or []) if c not in COLUMNS]
        if extra:
            sys.exit(f"Refusing to write: medicines.csv has {len(extra)} extra columns this writer would drop "
                     f"({extra[:3]}...). Use collect_expanded.py or recheck_medicines.py instead.")
    with open(MED_CSV, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_ALL)
        w.writeheader()
        for r in records:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
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
            cell = ws.cell(row=ri, column=ci, value=r.get(c, ""))
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if c == "pzn":
                cell.number_format = "@"
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    for ci in range(1, len(COLUMNS) + 1):
        L = get_column_letter(ci)
        width = max(len(str(ws.cell(row=r, column=ci).value or "")) for r in range(1, len(records) + 2))
        ws.column_dimensions[L].width = min(width + 2, 55)
    wb.save(MED_XLSX)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch":
        fetch()
    elif cmd == "build":
        build()
    else:
        sys.exit("usage: python collect_medications.py fetch|build")
