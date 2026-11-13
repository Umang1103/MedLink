#!/usr/bin/env python3
"""
recheck_medicines.py
--------------------
Offline re-verification and correction of medicines.csv / medicines.xlsx.

Evidence used per PZN (all local, saved at collection time):
  * <PZN>/raw_extract.json   product-page data from apotheken-umschau.de
  * <PZN>/leaflet_text.txt   leaflet text layer (or the PDF itself)
  * heart_failure_collection_list*.csv   for the list-derived columns
NO network requests are made.

Usage:
    python recheck_medicines.py           # dry run -> recheck_report.csv
    python recheck_medicines.py --apply   # back up, apply changes, rewrite csv + xlsx

Policy:
  * Hand-reviewed records (the first 16) keep their values unless the evidence
    contradicts them; then a reviewed correction from REVIEWED_FIXES is applied.
  * Auto-extracted records get values re-derived from the evidence. A value that
    cannot be derived unambiguously is left blank (or kept if it is verified)
    and flagged in the report.
"""
import csv
import json
import re
import shutil
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))
MED_CSV, MED_XLSX = ROOT / "medicines.csv", ROOT / "medicines.xlsx"
REPORT = ROOT / "recheck_report.csv"
LISTS = [ROOT / "heart_failure_collection_list_expanded.csv", ROOT / "heart_failure_collection_list.csv"]
TODAY = "2026-11-13"
NOTE_TAG = "Rechecked offline 2026-11-13"
LIST_COLS = ["category", "hf_context_zh", "mechanism_zh", "clinical_role_zh", "strength_note_zh",
             "clinical_source_url", "mechanism_source_url", "dosage_form_as_listed",
             "pharmaceutical_company_as_listed", "ingredient_content_as_listed",
             "product_hf_indication_status", "scope_note_zh", "notes_zh", "checked_on"]
MONTHS = {"januar": "01", "februar": "02", "märz": "03", "maerz": "03", "april": "04", "mai": "05",
          "juni": "06", "juli": "07", "august": "08", "september": "09", "oktober": "10",
          "november": "11", "dezember": "12"}

# Reviewed corrections for hand-curated records (evidence in recheck_report.csv).
REVIEWED_FIXES = {
    "06865959": {
        "active_ingredient": "Bisoprolol",
        "imprint": "5",
        "score_line": "single score line on one side (wedge-shaped)",
    },
}

_pdf_cache = {}


# --------------------------------------------------------------------------- utils
def flat(s):
    return re.sub(r"\s+", " ", s or "").strip()


def squash(s):
    s = unicodedata.normalize("NFKC", s or "").replace("\u00ad", "")
    s = re.sub(r"[„“”\"'’‚®™*]", "", s)
    return re.sub(r"[-–—\s.,:;()]", "", s).lower()


def leaflet_text(rec):
    p = ROOT / rec["pzn"] / "leaflet_text.txt"
    if p.exists():
        t = p.read_text(encoding="utf-8")
    elif rec["leaflet_path"] and (ROOT / rec["leaflet_path"]).exists():
        key = rec["leaflet_path"]
        if key not in _pdf_cache:
            from pypdf import PdfReader
            _pdf_cache[key] = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(ROOT / key)).pages)
        t = _pdf_cache[key]
    else:
        return ""
    t = unicodedata.normalize("NFKC", t)
    # Soft hyphens: at a line break they split a word; elsewhere some PDFs use them as real hyphens.
    # Lowercase continuation = split word ("Mikro\xad kristallin"); uppercase/digit = real hyphen ("D\xad 89150").
    t = re.sub(r"\s*\u00ad\s*(?=[a-zäöüß])", "", t)
    t = re.sub(r"\s*\u00ad\s*", "-", t)
    t = re.sub(r"([a-zäöüß])-\s*\n\s*([a-zäöüß])", r"\1\2", t)  # undo line-break hyphenation
    t = flat(t)
    t = re.sub(r"wei\s+ß\s*e", "weiße", t)
    return t


def load_lists():
    out = {}
    for p in LISTS:
        if p.exists():
            with open(p, encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    out.setdefault((r["market"], r["pzn"]), r)
    return out


# --------------------------------------------------------------------- derivations
# \b is essential: without it "Tel" matches inside "Arzneimittel".
_CT = r"(?:Tel(?:efon)?\.?|Telefax|Fax|E-?Mail|Internet|Website|Hotline|Postanschrift|(?:Kostenfreie\s+)?Service-?Nummer)"
CONTACT = re.compile(r"\s*\b" + _CT + r"(?=[\s.:(\d-]).*?(?=\s+\b" + _CT + r"\b|$)", re.I)
STOP = (r"(?:Diese (?:Packungs|Gebrauchs)(?:beilage|information)|Dieses Arzneimittel ist|Falls Sie weitere|"
        r"Weitere Informationsquellen|Versionscode|Mitvertrieb|Parallel vertrieben|Stand:|Zur Teilung|"
        r"Liebe Patientin|Hinweis|GEBRAUCHSINFORMATION|Gebrauchsinformation|Seite \d|Ausführliche Informationen|"
        r"Örtliche|Vertrieb|Sehr geehrte|Was in dieser Packungsbeilage|Die folgenden Informationen|"
        r"Diese Arzneimittel sind|in den Mitgliedsstaaten|in den Mitgliedstaaten)")
LEGAL = re.compile(r"\b(GmbH|AG|KG|S\.?A\.?U?|B\.?V\.?|Ltd|Limited|S\.p\.A\.?|S\.r\.l\.?|SRL|Sp\. ?z ?o\.? ?o\.?|s\.r\.o\.?|Zrt\.?|d\.d\.|a\.s\.|Oy|A/S|AB|N\.V\.|Inc\.?|Pharma|Laboratorios|Farmac)", re.I)


def clean_company(s):
    s = re.split(STOP, s or "")[0]
    s = re.sub(r"\S+@\S+", " ", s)
    s = re.sub(r"(?:https?://|www\.)\S+", " ", s)
    s = CONTACT.sub(" ", s)
    s = re.sub(r"\s+(?:oder|or)\s*$", "", flat(s))
    return flat(re.sub(r"\s*[|·•]\s*", " ", s)).strip(" ,;:-")


def split_mfr(s):
    parts = [clean_company(x) for x in re.split(r"\s+(?:oder|Oder|ODER)\s*:?\s+", s) if clean_company(x)]
    return "; oder ".join(parts)


def derive_companies(lt):
    """Return (company, manufacturer, extra_note) from the leaflet section 6."""
    note = []
    if re.search(r"Parallel vertrieben|Parallelvertreiber|Importeur|umgepackt von", lt):
        m = re.search(r"(?:Parallel vertrieben und umgepackt von|umgepackt von|Importeur:?)\s*(.{3,120}?)(?:\s+(?:Zulassungsinhaber|Hersteller|Falls|Diese)|$)", lt)
        note.append("parallel import/repackager: " + clean_company(m.group(1)) if m else "parallel import")
    if re.search(r"Mitvertrieb", lt):
        m = re.search(r"Mitvertrieb:?\s*(.{3,120}?)\s+(?=" + STOP + r"|Diese)", lt)
        note.append("co-distributor (Mitvertrieb): " + clean_company(m.group(1)) if m else "has Mitvertrieb")
    heads = list(re.finditer(r"(Pharmazeutischer Unternehmer|Zulassungsinhaber|Inhaber der Zulassung)", lt))
    if not heads:
        return "", "", note
    seg = lt[heads[-1].start():][:1500]
    m = re.match(r"(?:Pharmazeutischer Unternehmer|Zulassungsinhaber|Inhaber der Zulassung)\s+und\s+Hersteller\s*:?\s*(.+?)\s*(?=" + STOP + r"|$)", seg)
    if m:
        body = re.sub(r"^(?:Pharmazeutischer Unternehmer|Zulassungsinhaber)\s*:?\s*", "", m.group(1))
        if re.search(r"\bHersteller\s*:?", body):
            a, b = re.split(r"\bHersteller\s*:?\s*", body, maxsplit=1)
            return clean_company(a), split_mfr(b), note
        c = clean_company(body)
        return c, c, note
    m = re.match(r"(?:Pharmazeutischer Unternehmer|Zulassungsinhaber|Inhaber der Zulassung)\s*:?\s*(.+?)\s+Hersteller\s*:?\s*(.+?)\s*(?=" + STOP + r"|$)", seg)
    if m:
        return clean_company(m.group(1)), split_mfr(m.group(2)), note
    return "", "", note


def plausible_company(s):
    # Long values are legitimate multi-site manufacturer lists; 900 chars bounds runaway captures.
    return bool(s) and 4 < len(s) < 900 and bool(LEGAL.search(s)) and s.lower() not in ("und", "and")


def ingredient_form(lt, page_subs):
    m = re.search(r"\bDer Wirkstoff ist\s*:?\s*([A-ZÄÖÜ][^.;]{2,90}?(?:\(Ph\.\s?Eur\.\))?)(?=\s*[.;]|\s+(?:Jede|1|Eine|Ein|Die|-|–|•))", lt)
    if m:
        v = flat(m.group(1)).rstrip(" .")
        if not re.search(r"\d", v) and len(v) < 90:
            return v, "leaflet 'Der Wirkstoff ist …'"
    if page_subs:
        m = re.match(r"(.+?)\s+\d", page_subs[0])
        if m:
            return flat(m.group(1)), "product page Wirkstoff(e)"
    return "", ""


def page_imprint(phys):
    m = re.search(r"Sonstige Merkmale \(Prägungen/Aufschrift\)\s*(.+?)(?:\s+Besonderheiten|\s+Mehr Infos|$)", phys)
    if not m:
        return ""
    toks = re.findall(r'"([^"]+)"', m.group(1))
    return " / ".join(t.strip() for t in toks) if toks else flat(m.group(1))


def score_from_page(phys):
    def f(label, stop):
        m = re.search(label + r"\s*:\s*(.+?)\s*(?=" + stop + r")", phys)
        return m.group(1).strip() if m else ""
    arr = f("Anordnung", r"Spaltung|$")
    if not arr:
        return ""
    if arr.startswith("ohne"):
        return "none"
    lage = f("Lage", r"Form|Anordnung")
    form = f("Form", r"Anordnung")
    kind = {"Einzelkerbe": "single score line", "Kreuzkerbe": "cross score", "Parallelkerbe": "parallel score lines",
            "2-Parallelkerbe": "two parallel score lines", "3-Parallelkerbe": "three parallel score lines",
            "Doppelkerbe": "double score line"}.get(lage, "score line" if not lage else f"score ({lage})")
    side = {"einseitig": "on one side", "beidseitig": "on both sides"}.get(arr, f"({arr})")
    shp = {"keilförmig": "wedge-shaped", "rinnenförmig": "groove-shaped", "keil-/rinnenförmig": "wedge-/groove-shaped",
           "stufenförmig": "step-shaped"}.get(form, form)
    return f"{kind} {side}" + (f" ({shp})" if shp else "")


COLOR_WORDS = [  # (pattern on German appearance text, English); compounds first
    (r"gelblich\s*-?\s*weiß|weiß\s*-?\s*gelblich", "yellowish white"),
    (r"beige\s*-?\s*braun", "beige-brown"), (r"rot\s*-?\s*braun|rötlich\s*-?\s*braun|braunrot", "red-brown"),
    (r"blau\s*-?\s*grün|grün\s*-?\s*blau", "blue-green"),
    (r"gebrochen\s*weiß|cremeweiß|weißlich", "off-white"), (r"weiß|weiss", "white"),
    (r"hell\s*gelb|blassgelb", "light yellow"), (r"gelblich", "yellowish"), (r"gelb", "yellow"),
    (r"hell\s*orange|orange", "orange"), (r"apricot|aprikos", "apricot"), (r"pfirsich", "peach"),
    (r"hell\s*rosa|blassrosa", "light pink"), (r"rosa|pink", "pink"),
    (r"hell\s*rot", "light red"), (r"rötlich", "reddish"), (r"\brot", "red"),
    (r"hell\s*braun|beige-?braun", "light brown"), (r"braun", "brown"), (r"beige", "beige"), (r"creme", "cream"),
    (r"hell\s*grün|grün", "green"), (r"hell\s*blau|blau", "blue"), (r"violett|lila", "violet"), (r"grau", "grey"),
]
SHAPE_WORDS = [
    (r"herzförmig", "heart-shaped"), (r"rautenförmig|diamantförmig|diamantenförmig", "diamond-shaped"),
    (r"kapselförmig", "capsule-shaped"), (r"achteckig", "octagonal"), (r"sechseckig", "hexagonal"),
    (r"fünfeckig", "pentagonal"), (r"dreieckig", "triangular"), (r"\brund", "round"), (r"\boval", "oval"),
    (r"länglich|oblong", "oblong"), (r"Hartkapsel|Kapsel\b", "capsule"),
]
PROFILE = [(r"bikonvex|beidseit(?:s|ig) gewölbt|beidseitig konvex", "biconvex"), (r"konvex|gewölbt", "convex"),
           (r"\bflach", "flat")]


def colour_shape(app):
    """English colour / shape from one strength-specific German appearance paragraph."""
    if not app:
        return "", ""
    head = re.split(r"\b(?:mit|Prägung|Aufschrift|Bruchkerbe|auf (?:der|einer|beiden))\b", app, maxsplit=1)[0]
    found = []
    used = []
    for pat, en in COLOR_WORDS:
        for m in re.finditer(pat, head, re.I):
            if not any(a <= m.start() < b for a, b in used):
                found.append((m.start(), en))
                used.append((m.start(), m.end()))
    found.sort()
    cols = list(dict.fromkeys(en for _, en in found))
    colour = ""
    if cols:
        between = re.search(r"\bbis\b", head)
        colour = (" to ".join(cols[:2]) if between and len(cols) >= 2 else ", ".join(cols))
        if re.search(r"gesprenkelt|gepunktet|Punkten|Sprenkel", app, re.I):
            colour += ", speckled"
        if re.search(r"marmoriert", app, re.I):
            colour += ", marbled"
    shapes = list(dict.fromkeys(en for pat, en in SHAPE_WORDS if re.search(pat, app, re.I)))
    prof = next((en for pat, en in PROFILE if re.search(pat, app, re.I)), "")
    shape = ", ".join(shapes + ([prof] if prof else []))
    return colour, shape


ABBR = {"ca", "z", "b", "ph", "eur", "nr", "bzw", "ggf", "max", "min", "evtl", "inkl", "u", "s", "o", "abs",
        "d", "h", "e", "mind", "zzgl", "etc", "vgl"}
PACK_CUT = re.compile(r"\s*\b(?:Originalpackung|Bündelpackung|Klinikpackung|Blisterpackung(?:en)?|Packungsgrößen?|"
                      r"Die Aluminium|Die (?:Film)?[Tt]abletten (?:sind|werden) in|Es werden möglicherweise)\b.*$")


def tidy_sentences(a):
    """Trim layout debris so the excerpt is whole sentences of the leaflet. Returns (text, complete)."""
    a = re.sub(r"\s*\u00ad\s*(?=[a-zäöüß])", "", a or "")
    a = re.sub(r"\s*\u00ad\s*", "-", a)
    a = flat(a)
    a = re.sub(r"\.(?=[A-ZÄÖÜ][a-zäöü])", ". ", a)              # "Seite.Bisoprolol" -> sentence break
    a = re.sub(r"^[\s:;,.®-]+", "", a)
    a = re.sub(r"^(?:(?:Film|Retard)?[Tt]abletten\s+)(?=[A-ZÄÖÜ])", "", a)
    if re.match(r"^[\wÄÖÜäöü®-]+\.\s+[A-ZÄÖÜ]", a):           # leading "Filmtablette." debris
        a = re.sub(r"^[\wÄÖÜäöü®-]+\.\s+", "", a)
    a = PACK_CUT.sub("", a)
    ends = [m.end() for m in re.finditer(r"[.!](?=\s|$)", a)
            if not re.search(r"(?:^|[\s(])(" + "|".join(ABBR) + r")$", a[:m.start()], re.I)]
    if ends and ends[-1] < len(a):
        a = a[:ends[-1]]
    elif not ends:
        a = re.sub(r"\s+[A-ZÄÖÜ][\w-]+(?:\s+[\w-]+){0,3}\s+\d+(?:,\d+)?\s*mg\b[^.]*$", "", a)  # trailing name fragment
    a = a.strip(" ®")
    return a, a.endswith((".", "!"))


def appearance(rec, lt):
    try:
        import normalize_medicines as nm  # local-file parser only; never calls its network code
        a, _ = nm.pdf_appearance(rec)
    except Exception:
        a = ""
    if a:
        return tidy_sentences(a)[0], "leaflet (strength-specific paragraph)"
    m = re.search(r"aus-?\s?(?:sieht|sehen) und Inhalt der Packung:?\s*(.+?)\s*(?:Pharmazeutischer Unternehmer|Zulassungsinhaber)", lt)
    if not m:
        return "", ""
    sec = re.split(r"\b(?:Blisterpackungen|Packungsgrößen|Packungen mit|Es werden|ist in Packungen|sind in Packungen)\b", m.group(1))[0]
    mg = set(re.findall(r"\b\d+(?:[,.]\d+)?\s*mg\b", sec))
    if len(mg) > 1 or len(sec) > 900:
        return "", "ambiguous (several strengths in one section)"
    return tidy_sentences(sec)[0], "leaflet section 6"


def revision(lt):
    lt = re.sub(r"über\s*-?\s*arbeitet", "überarbeitet", lt)
    verb = r"(?:überarbeitet|geändert|genehmigt)"
    m = list(re.finditer(verb + r"\s*(?:im|am|:)?\s*([A-Za-zäöüÄÖÜ]+)\s+(\d{4})", lt))
    if m:
        word = m[-1].group(1).lower().replace("okotber", "oktober")
        mon = MONTHS.get(word)
        if mon:
            return f"{m[-1].group(2)}-{mon}"
    m = list(re.finditer(verb + r"\s*(?:im|am|:)?\s*(?:\d{1,2}\.)?(\d{1,2})[./](\d{4})", lt))
    if m:
        return f"{m[-1].group(2)}-{int(m[-1].group(1)):02d}"
    m = list(re.finditer(r"Stand\s*:?\s*(\d{1,2})[./](\d{4})", lt))
    if m:
        return f"{m[-1].group(2)}-{int(m[-1].group(1)):02d}"
    m = list(re.finditer(r"Stand:?\s*([A-Za-zäöüÄÖÜ]+)\s+(\d{4})", lt))
    if m and MONTHS.get(m[-1].group(1).lower()):
        return f"{m[-1].group(2)}-{MONTHS[m[-1].group(1).lower()]}"
    return ""


# ------------------------------------------------------------------------ recheck
def recheck(rec, raw, lt, lrow, curated):
    pzn = rec["pzn"]
    page = raw.get("page", {})
    phys = page.get("physical_block_raw", "")
    new = dict(rec)
    rows = []

    def log(col, verdict, value, source, note=""):
        rows.append({"pzn": pzn, "record_type": "curated" if curated else "auto", "column": col,
                     "old_value": rec.get(col, ""), "new_value": value if value is not None else rec.get(col, ""),
                     "verdict": verdict, "source": source, "note": note})
        if value is not None:
            new[col] = value

    sq = squash(lt)

    # pzn / market
    p = page.get("pzn_on_page", "")
    log("pzn", "OK" if p == pzn else ("MISMATCH" if p else "NO_EVIDENCE"), None, "product page PZN", f"page: {p}")
    log("market", "OK" if rec["market"] == "DE" else "FIXED", "DE", "German PZN")

    # product name / brand
    pt = re.sub(r"^Beipackzettel von\s+|\s*-\s*Beipackzettel$", "", page.get("page_title", ""))
    same = squash(rec["product_name"]) == squash(pt)
    log("product_name", "OK" if same else "REVIEW", None, "product page title",
        "" if same else f"page title: {pt!r}")
    bs = rec["product_name"].lower().startswith(rec["brand"].lower()) and bool(rec["brand"])
    log("brand", "OK" if bs else "REVIEW", None, "prefix of product_name")

    # active ingredient (moiety) - catalogue INN, confirmed by page/leaflet
    lai = (lrow or {}).get("active_ingredient", "")
    cand = REVIEWED_FIXES.get(pzn, {}).get("active_ingredient") or (lai if not curated else rec["active_ingredient"])
    parts = [x.strip() for x in cand.split("/") if x.strip()]
    subs_txt = squash(" ".join(page.get("substances_on_page", [])))
    conf = all(squash(x) in sq or squash(x) in subs_txt for x in parts)
    if cand != rec["active_ingredient"]:
        log("active_ingredient", "FIXED" if conf else "REVIEW", cand if conf else None,
            "collection list INN, confirmed in leaflet/page", "moiety name; salt form kept in ingredient_form_as_stated")
    else:
        log("active_ingredient", "OK" if conf else "REVIEW", None, "leaflet/page")

    # ingredient form as stated
    form, fsrc = ingredient_form(lt, page.get("substances_on_page", []))
    old = rec["ingredient_form_as_stated"]
    if curated:
        ok = squash(old) in sq or (form and squash(form) in squash(old))
        log("ingredient_form_as_stated", "OK" if ok else "REVIEW", None, "leaflet", f"derived: {form!r}")
    elif form and squash(form) != squash(old):
        log("ingredient_form_as_stated", "FIXED", form, fsrc, "strength text removed; substance/salt form only")
    else:
        log("ingredient_form_as_stated", "OK" if form else "REVIEW", None if form else "", fsrc or "not derivable")

    # strength
    st = rec["strength_as_stated"]
    in_name = squash(st) in squash(rec["product_name"])
    in_subs = squash(st) in subs_txt or squash(st) in sq
    log("strength_as_stated", "OK" if (in_name or in_subs) else "REVIEW", None, "product name / page substances / leaflet",
        "" if in_name else "strength not literally in product name")

    # dosage form, pack size
    dfp = re.split(r"\s+Dosierung", page.get("dosage_form_on_page", ""))[0].strip()
    if dfp and squash(dfp) != squash(rec["dosage_form"]):
        log("dosage_form", "FIXED" if not curated else "REVIEW", dfp if not curated else None, "product page Darreichungsform")
    else:
        log("dosage_form", "OK" if dfp else "NO_EVIDENCE", None, "product page Darreichungsform")
    m = re.match(r"(\d+)\s+St", page.get("pack_size_on_page", ""))
    if m and m.group(1) != rec["pack_size"]:
        log("pack_size", "FIXED", m.group(1), "product page Packungsgröße")
    else:
        log("pack_size", "OK" if m else "NO_EVIDENCE", None, "product page Packungsgröße", page.get("pack_size_on_page", ""))

    # companies
    comp, mfr, cnote = derive_companies(lt)
    for col, val in (("pharmaceutical_company", comp), ("manufacturer", mfr)):
        old = rec[col]
        if curated:
            names = [flat(x).split(",")[0] for x in re.split(r";\s*(?:oder\s+)?", old) if x.strip()]
            ok = all(squash(n) in sq for n in names) and bool(names)
            log(col, "OK" if ok else "REVIEW", None, "leaflet section 6")
            continue
        if plausible_company(val):
            log(col, "OK" if squash(val) == squash(old) else "FIXED", val, "leaflet section 6",
                "contact details removed" if old and squash(clean_company(old)) == squash(val) else "")
        else:
            cleaned = clean_company(old)
            if plausible_company(cleaned):
                log(col, "OK" if cleaned == old else "FIXED", cleaned, "existing value, contact details removed",
                    "leaflet section 6 not parseable; value not re-confirmed")
            else:
                log(col, "BLANKED" if old else "NO_EVIDENCE", "", "not derivable from leaflet",
                    f"previous value not a company name: {old[:60]!r}" if old else "")
    prov = page.get("provider_on_page", "")
    if cnote:
        log("notes", "INFO", None, "leaflet", "; ".join(cnote))

    # appearance
    app, asrc = appearance(rec, lt)
    # An appearance excerpt must describe the dosage form; reject captures like a bare substance name.
    looks = lambda s: bool(re.search(r"(?i)tablette|kapsel|rund|oval|länglich|weiß|gelb|rosa|rot|blau|grün|orange|braun", s or ""))
    if app and not looks(app):
        app, asrc = "", "derived text is not an appearance description"
    old_app = rec["appearance_description_original"]
    old_ok = bool(old_app) and squash(old_app) in sq
    if curated:
        log("appearance_description_original", "OK" if old_ok else "REVIEW", None, "leaflet",
            "" if old_ok else "not verbatim in text layer (curated, reassembled or normalised)")
        use_app = old_app
    elif app and (not old_ok or squash(app) != squash(old_app)):
        done = app.endswith((".", "!"))
        log("appearance_description_original", "FIXED" if done else "REVIEW", app, asrc,
            ("previous text not verbatim in leaflet; " if old_app and not old_ok else "")
            + ("" if done else "excerpt has no closing full stop in the text layer"))
        use_app = app
    elif old_ok and looks(old_app):
        t, done = tidy_sentences(old_app)
        if t != old_app:
            log("appearance_description_original", "FIXED" if done else "REVIEW", t, "existing excerpt, layout debris trimmed",
                "" if done else "excerpt has no closing full stop in the text layer")
        else:
            log("appearance_description_original", "OK" if done else "REVIEW", None, "leaflet",
                "" if done else "excerpt has no closing full stop in the text layer")
        use_app = t
    else:
        log("appearance_description_original", "BLANKED" if old_app else "NO_EVIDENCE", "", asrc or "no leaflet",
            "not derivable unambiguously")
        use_app = ""
    colour, shape = colour_shape(use_app)
    for col, val in (("color", colour), ("shape", shape)):
        old = rec[col]
        if curated:
            log(col, "OK" if old else "REVIEW", None, "curated English normalisation of leaflet text", f"auto-derived: {val!r}")
        elif val:
            log(col, "OK" if val == old else "FIXED", val, "auto-normalised from appearance_description_original")
        else:
            log(col, "BLANKED" if old else "NO_EVIDENCE", "", "no strength-specific appearance text",
                f"previous {old!r} not traceable to the leaflet" if old else "")

    # imprint
    pim = page_imprint(phys)
    old = rec["imprint"]
    fix = REVIEWED_FIXES.get(pzn, {}).get("imprint")
    if fix is not None:
        log("imprint", "OK" if fix == old else "FIXED", fix, "product page Sonstige Merkmale",
            "" if fix == old else "delimiter quotes removed")
    elif curated:
        toks = re.findall(r'"([^"]+)"', old) or [old]
        ok = (not old and not pim) or all(squash(t) in squash(pim) or squash(t) in sq for t in toks if t)
        log("imprint", "OK" if ok or old.startswith("CONFLICT") else "REVIEW", None, "product page / leaflet", f"page: {pim!r}")
    elif pim:
        log("imprint", "OK" if pim == old else "FIXED", pim, "product page Sonstige Merkmale (copied exactly)")
    else:
        keep = bool(old) and squash(old) in sq
        log("imprint", "OK" if keep or not old else "BLANKED", None if keep else "", "leaflet" if keep else "no imprint on page",
            "" if keep or not old else f"previous {old!r} not traceable")

    # score line
    sc = score_from_page(phys)
    fix = REVIEWED_FIXES.get(pzn, {}).get("score_line")
    old = rec["score_line"]
    if fix:
        log("score_line", "OK" if fix == old else "FIXED", fix, "product page Angaben zu Kerben",
            "" if fix == old else "'Spaltung: nein' describes the notch edge, not divisibility")
    elif curated:
        key = {"none": "none", "on one side": "one side", "on both sides": "both sides"}
        k = next((v for kk, v in key.items() if kk in sc), "")
        ok = (k in old.lower()) if k else True
        log("score_line", "OK" if ok else "REVIEW", None, "product page Angaben zu Kerben", f"derived: {sc!r}")
    elif sc:
        log("score_line", "OK" if sc == old else "FIXED", sc, "product page Angaben zu Kerben (translated)")
    else:
        log("score_line", "BLANKED" if old else "NO_EVIDENCE", "", "no score data on page")

    # files
    n_img = len(page.get("original_image_urls", []))
    for i, col in ((1, "tablet_image_1"), (2, "tablet_image_2")):
        v = rec[col]
        if v:
            fp = ROOT / v
            ok = fp.exists() and fp.read_bytes()[:3] in (b"\xff\xd8\xff", b"\x89PN") and i <= n_img
            log(col, "OK" if ok else "MISMATCH", None, "local file + page image count", f"page images: {n_img}")
        else:
            log(col, "OK" if i > n_img else "MISMATCH", None, "page image count", f"page images: {n_img}")
    pk = rec["package_image"]
    log("package_image", "OK" if not pk or (ROOT / pk).exists() else "MISMATCH", None, "local file")
    lp = rec["leaflet_path"]
    has_pdf = bool(page.get("pdf_links"))
    if lp:
        ok = (ROOT / lp).exists() and (ROOT / lp).read_bytes()[:4] == b"%PDF"
        log("leaflet_path", "OK" if ok else "MISMATCH", None, "local file %PDF")
    else:
        log("leaflet_path", "MISMATCH" if has_pdf else "OK", None, "page PDF links", "page lists a PDF but none saved" if has_pdf else "no PDF on page")

    # urls
    u = rec["product_source_url"]
    lu = (lrow or {}).get("product_source_url", u)
    ok = u == lu and re.search(re.escape(pzn.lstrip("0")) + r"\.html$", u)
    log("product_source_url", "OK" if ok else "REVIEW", None, "collection list / PZN suffix")
    mu = rec["manufacturer_source_url"]
    log("manufacturer_source_url", "OK" if not mu or pzn.lstrip("0") in mu else "REVIEW", None, "blank = not cross-checked")

    # dates
    rv = revision(lt)
    rsrc = "leaflet 'zuletzt überarbeitet' / 'Stand'"
    if not rv and rec["leaflet_path"]:
        # Image-only PDF: fall back to the document date shown next to the PDF link.
        dd = next((d.get("doc_date", "") for d in page.get("pdf_links", []) if d.get("doc_date")), "")
        dd = dd or raw.get("downloads", {}).get("leaflet", {}).get("doc_date", "")
        m = re.search(r"(\d{2})\.(\d{4})", dd or "")
        if m:
            rv, rsrc = f"{m.group(2)}-{m.group(1)}", "product page 'Dokumentdatum' (leaflet has no text layer)"
    old = rec["leaflet_revision_date"]
    if rv and rv != old:
        log("leaflet_revision_date", "FIXED", rv, rsrc)
    elif rv:
        log("leaflet_revision_date", "OK", None, rsrc)
    else:
        log("leaflet_revision_date", "NO_EVIDENCE" if not old else "REVIEW", None, "leaflet",
            "revision date not found in leaflet text" if lt else "no leaflet")
    ra = rec["retrieved_at"]
    log("retrieved_at", "OK" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ra) else "FIXED", ra[:10], "date only (schema)")

    # list-derived extra columns
    if lrow:
        for c in LIST_COLS:
            if c in rec and lrow.get(c, "") != rec[c]:
                log(c, "FIXED", lrow.get(c, ""), "collection list")

    # status / notes
    vs = rec["verification_status"]
    if not curated:
        vs = ("PZN and pack size checked against saved product page; leaflet fields re-derived offline "
              "2026-11-13; images file-validated, not visually reviewed; not clinically or pharmacist verified.")
    elif "not clinically or pharmacist verified" not in vs:
        vs = vs.rstrip(". ") + "; not clinically or pharmacist verified."
    log("verification_status", "OK" if vs == rec["verification_status"] else "FIXED", vs, "workflow rule")
    notes = rec["notes"]
    if NOTE_TAG not in notes:
        add = f"{NOTE_TAG} against saved product page and leaflet (see recheck_report.csv)."
        if cnote:
            add += " Leaflet: " + "; ".join(cnote) + "."
        if prov and not curated:
            add += f" Product page Anbieter: {prov}."
        notes = (notes.rstrip() + " " + add).strip()
    log("notes", "OK" if notes == rec["notes"] else "FIXED", notes, "recheck")
    return new, rows


# ------------------------------------------------------------------------- output
def write_xlsx(fields, records):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Medicines"
    for i, c in enumerate(fields, 1):
        cell = ws.cell(row=1, column=i, value=c)
        cell.fill = PatternFill("solid", start_color="1F4E79", end_color="1F4E79")
        cell.font = Font(bold=True, color="FFFFFF")
    for ri, r in enumerate(records, 2):
        for ci, c in enumerate(fields, 1):
            cell = ws.cell(row=ri, column=ci, value=r.get(c, ""))
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if c == "pzn":
                cell.number_format = "@"
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    for ci in range(1, len(fields) + 1):
        width = max(len(str(ws.cell(row=r, column=ci).value or "")) for r in range(1, min(len(records), 400) + 2))
        ws.column_dimensions[get_column_letter(ci)].width = min(width + 2, 55)
    wb.save(MED_XLSX)


def main():
    apply = "--apply" in sys.argv
    with open(MED_CSV, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        fields = list(rd.fieldnames)
        records = list(rd)
    lists = load_lists()
    out, report = [], []
    for rec in records:
        rawp = ROOT / rec["pzn"] / "raw_extract.json"
        raw = json.loads(rawp.read_text(encoding="utf-8")) if rawp.exists() else {}
        curated = not rec["verification_status"].startswith(("PZN verified", "PZN and pack size checked"))
        new, rows = recheck(rec, raw, leaflet_text(rec), lists.get((rec["market"], rec["pzn"])), curated)
        out.append(new)
        report.extend(rows)
    with open(REPORT, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(report[0].keys()), quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(report)
    counts = Counter(r["verdict"] for r in report)
    print("records:", len(out), "| verdicts:", dict(counts))
    by_col = Counter((r["column"], r["verdict"]) for r in report if r["verdict"] not in ("OK", "INFO"))
    for (c, v), n in sorted(by_col.items()):
        print(f"  {c:34} {v:12} {n}")
    if apply:
        bk = ROOT / "backups" / ("recheck_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        bk.mkdir(parents=True)
        for f in (MED_CSV, MED_XLSX):
            shutil.copy2(f, bk / f.name)
        with open(MED_CSV, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, quoting=csv.QUOTE_ALL)
            w.writeheader()
            w.writerows(out)
        write_xlsx(fields, out)
        print("applied; backup:", bk)


if __name__ == "__main__":
    main()
