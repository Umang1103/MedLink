#!/usr/bin/env python3
"""
build_workflow.py
-----------------
Build workflow.html: offline demo of
    Prescription  ->  Pharmacy Dispensing  ->  Patient Medicine Card
with Doctor, Pharmacy and Patient views that share the same saved records.

  python build_workflow.py            -> writes workflow.html and opens it
  python build_workflow.py --no-open  -> only writes the file

Inputs (unchanged by this script):
  medicines.csv                         product database (same as the gallery)
  mock_prescription_anna_mueller.json   prescription extracted from the mock screenshot

Records (prescription versions, dispensings, issued cards, patient-reported
medicines, audit log) are saved in the browser's localStorage; use Export /
Import in the page to keep or move them. Fictional demo data only: no
E-Rezept or ePA integration.
"""
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from build_gallery import is_image, is_pdf

ROOT = Path(__file__).parent.resolve()
OUT = ROOT / "workflow.html"
MOCK = ROOT / "mock_prescription_anna_mueller.json"
DB_FIELDS = ["pzn", "product_name", "active_ingredient", "strength_as_stated", "dosage_form", "pack_size",
             "brand", "pharmaceutical_company", "color", "shape", "imprint"]


def load_db():
    with open(ROOT / "medicines.csv", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    db = []
    for r in rows:
        d = {k: r.get(k, "") for k in DB_FIELDS}
        d["images"] = [r[c].replace("\\", "/") for c in ("tablet_image_1", "tablet_image_2")
                       if r.get(c) and is_image(ROOT / r[c])]
        d["leaflet_path"] = r["leaflet_path"] if r.get("leaflet_path") and is_pdf(ROOT / r["leaflet_path"]) else ""
        db.append(d)
    return db


def seed_state(mock):
    rx = mock["prescription"]
    items = []
    for it in rx["items"]:
        items.append({
            "item_id": f"{rx['rx_id']}-I{it['no']:02d}",   # internal, stable across versions
            "med_no": it["no"],                              # patient-facing, never reused
            "ingredient": it["ingredient"], "product_name": None, "strength": it["strength"],
            "dosage_form": None, "prescribed_pzn": None,
            "dose": dict(it["dose"]), "dose_unit": None,
            "purpose": it["purpose"], "purpose_note": "Translated from the source",
            "status": "active", "stopped_in_version": None,
        })
    n = len(items)
    return {
        "schema": 1,
        "patient": dict(mock["patient"]),
        "nextMedNo": n + 1, "nextItem": n + 1,
        "prescriptions": [{
            "rx_id": rx["rx_id"], "source": mock["source"],
            "versions": [{
                "version": 1, "status": "draft",
                "created_at": mock["source"]["extracted_on"], "created_by": "Extracted from mock screenshot",
                "confirmed_at": None, "confirmed_by": None, "change_note": "Initial extraction",
                "prescription_date": rx["prescription_date"], "prescriber": rx["prescriber"],
                "items": items,
            }],
        }],
        "dispensings": [], "reported": [], "cards": [],
        "audit": [{"at": mock["source"]["extracted_on"], "who": "system",
                   "what": f"Prescription {rx['rx_id']} v1 extracted from mock screenshot ({n} items), awaiting review"}],
    }


def main():
    mock = json.loads(MOCK.read_text(encoding="utf-8"))
    db = load_db()
    payload = {"db": db, "seed": {"state": seed_state(mock), "scope": mock["card_scope_note"]},
               "built": datetime.now().strftime("%Y-%m-%d %H:%M")}
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(TEMPLATE.replace("/*__DATA__*/null", data), encoding="utf-8")
    print(f"wrote {OUT.name}: {len(db)} database products, {len(mock['prescription']['items'])} prescription items")
    if "--no-open" not in sys.argv:
        import webbrowser
        webbrowser.open(OUT.as_uri())


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prescription → Dispensing → Medicine Card (Demo)</title>
<style>
  :root { --bg:#f4f6f8; --ink:#1d2733; --muted:#5f6b78; --line:#dde3ea; --accent:#1f4e79; --warn:#b45309; --warnbg:#fff4e5;
          --bad:#b42318; --badbg:#fde8e7; --ok:#1e7a3c; --okbg:#e6f4ea; --diff:#fff3bf; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; background:var(--bg); color:var(--ink); font-size:14px; }
  header.top { background:var(--accent); color:#fff; padding:14px 22px; display:flex; flex-wrap:wrap; gap:12px; align-items:center; }
  header.top h1 { margin:0; font-size:19px; flex:1 1 auto; }
  header.top a { color:#fff; }
  .tabs { display:flex; gap:6px; }
  .tab { background:transparent; color:#fff; border:1px solid rgba(255,255,255,.6); }
  .tab.on { background:#fff; color:var(--accent); }
  .demo { background:#fff4e5; color:#7a4100; padding:8px 22px; font-size:13px; border-bottom:1px solid #f1d6b0; }
  main { padding:18px 22px 60px; max-width:1400px; margin:0 auto; }
  section.box { background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 16px; margin-bottom:16px; }
  section.box h2 { margin:0 0 10px; font-size:17px; }
  section.box h3 { margin:0; font-size:15px; }
  button { cursor:pointer; border:1px solid var(--accent); background:var(--accent); color:#fff; border-radius:8px; padding:7px 12px; font-size:14px; }
  button.ghost { background:#fff; color:var(--accent); }
  button.small { padding:4px 9px; font-size:13px; }
  button:disabled { opacity:.45; cursor:not-allowed; }
  input, select, textarea { font:inherit; padding:5px 7px; border:1px solid var(--line); border-radius:6px; background:#fff; }
  input.nc, td.nc, span.nc { background:var(--warnbg); color:var(--warn); }
  input.nc::placeholder { color:var(--warn); }
  table { border-collapse:collapse; width:100%; }
  th, td { text-align:left; vertical-align:top; padding:6px 7px; border-bottom:1px solid var(--line); }
  th { color:var(--muted); font-weight:600; font-size:13px; }
  td.diff { background:var(--diff); }
  tr.stopped td { color:#98a2ad; text-decoration:line-through; }
  .badge { display:inline-block; font-size:12px; padding:2px 8px; border-radius:20px; background:#e8eef5; color:#2b4a68; margin-left:6px; }
  .badge.draft { background:var(--warnbg); color:var(--warn); }
  .badge.confirmed { background:var(--okbg); color:var(--ok); }
  .badge.superseded { background:#eceff2; color:#6b7580; }
  .msg { border-radius:8px; padding:8px 12px; margin:8px 0; }
  .msg.warn { background:var(--warnbg); color:var(--warn); }
  .msg.bad { background:var(--badbg); color:var(--bad); }
  .msg.ok { background:var(--okbg); color:var(--ok); }
  .msg.info { background:#e8eef5; color:#2b4a68; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin:8px 0; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  .muted { color:var(--muted); }
  .pics img { height:110px; border-radius:6px; margin-right:6px; }
  .cap { font-size:12px; color:var(--muted); }
  details summary { cursor:pointer; color:var(--accent); }
  td input.dose { width:46px; text-align:center; }
  td input.w-s { width:90px; } td input.w-m { width:130px; } td input.w-l { width:170px; }
  label.chk { display:flex; gap:6px; align-items:flex-start; }
  /* ---- A4 card ---- */
  .a4 { width:210mm; min-height:297mm; margin:0 auto; background:#fff; padding:10mm; box-shadow:0 2px 12px rgba(0,0,0,.15); color:#000; font-size:10pt; }
  .card-head { display:flex; justify-content:space-between; gap:8mm; border-bottom:2px solid #1f4e79; padding-bottom:3mm; margin-bottom:3mm; }
  .card-head h1 { margin:0; font-size:18pt; color:#1f4e79; }
  .pinfo td, .pinfo th { border:none; padding:0.6mm 2mm; font-size:9pt; }
  .pinfo th { color:#444; }
  .scope { border:1px solid #b45309; background:#fff8ee; padding:2mm 3mm; font-size:9pt; margin-bottom:3mm; }
  table.meds th { font-size:8.5pt; color:#222; border-bottom:1.5px solid #333; padding:1.2mm; }
  table.meds td { font-size:9pt; padding:1.2mm; border-bottom:1px solid #bbb; }
  table.meds tr { page-break-inside:avoid; break-inside:avoid; }
  table.meds td.no { font-size:15pt; font-weight:700; color:#1f4e79; width:9mm; text-align:center; }
  table.meds td.dose { text-align:center; font-size:12pt; font-weight:700; width:11mm; }
  table.meds { table-layout:fixed; }
  table.meds col.c-no { width:9mm; } table.meds col.c-ph { width:30mm; } table.meds col.c-med { width:40mm; }
  table.meds col.c-ing { width:25mm; } table.meds col.c-pzn { width:18mm; } table.meds col.c-dose { width:11mm; }
  table.meds td.pzn { white-space:nowrap; font-size:8.5pt; }
  table.meds td { overflow-wrap:break-word; }
  table.meds th.dh { font-size:7.5pt; text-align:center; }
  .ph { display:flex; gap:1mm; }
  .ph img { height:14mm; width:14mm; object-fit:cover; border-radius:1mm; }
  .ph .none { height:14mm; width:29mm; border:1px dashed #999; font-size:7.5pt; color:#555; display:flex; align-items:center; justify-content:center; text-align:center; }
  .nas { font-size:7pt; color:#444; }
  .cnote { font-size:7.5pt; color:#7a4100; }
  .card-sec { margin-top:4mm; font-size:9pt; }
  .card-sec h2 { font-size:10.5pt; margin:0 0 1mm; color:#1f4e79; }
  .card-foot { margin-top:5mm; border-top:1px solid #999; padding-top:2mm; font-size:7.5pt; color:#444; }
  @page { size:A4 portrait; margin:10mm; }
  @media print {
    body { background:#fff; }
    .noprint { display:none !important; }
    main { padding:0; max-width:none; }
    .a4 { box-shadow:none; margin:0; width:auto; min-height:0; padding:0; }
  }
</style>
</head>
<body>
<header class="top noprint">
  <h1>Prescription → Pharmacy Dispensing → Patient Medicine Card</h1>
  <div class="tabs">
    <button class="tab" data-view="doctor">Doctor</button>
    <button class="tab" data-view="pharmacy">Pharmacy</button>
    <button class="tab" data-view="patient">Patient</button>
  </div>
  <a href="gallery.html">Medicine Gallery</a>
  <button class="ghost small" id="export">Export records</button>
  <button class="ghost small" id="import">Import records</button>
  <input type="file" id="import-file" accept="application/json" hidden>
  <button class="ghost small" id="reset">Reset demo</button>
</header>
<div class="demo noprint">Demo with fictional mock data. Not connected to E-Rezept or ePA. All three views use the same saved records (stored in this browser).</div>
<main id="view"></main>

<script>
const DATA = /*__DATA__*/null;
const DB = DATA.db, SEED = DATA.seed;
const byPzn = new Map(DB.map(d => [d.pzn, d]));
const KEY = "medlink-workflow-demo-v1";
const NC = "Needs confirmation";
const SLOTS = [["morning", "Morning"], ["noon", "Noon"], ["evening", "Evening"], ["bedtime", "Bedtime"]];
const $ = id => document.getElementById(id);
function el(tag, attrs, ...kids) {           // safe DOM builder: text is never parsed as HTML
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v; else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(Infinity)) if (k !== null && k !== undefined && k !== false) e.append(k);
  return e;
}
const kidsOf = ks => ks.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false);
const put = (parent, ...ks) => parent.append(...kidsOf(ks));        // append, skipping empty values
const putOnly = (parent, ...ks) => parent.replaceChildren(...kidsOf(ks));
const clone = o => JSON.parse(JSON.stringify(o));
const today = () => new Date().toISOString().slice(0, 10);
const nowIso = () => new Date().toISOString().slice(0, 16).replace("T", " ");
const isNC = v => v === null || v === undefined || v === "";
const show = v => isNC(v) ? NC : String(v);
const doseText = v => isNC(v) ? NC : (String(v) === "0" ? "–" : String(v));
const blankToNull = v => (v === undefined || String(v).trim() === "") ? null : String(v).trim();
const num = s => {
  const m = String(s || "").match(/(\d+(?:[.,]\d+)?)\s*(mg|µg|mcg|microgram|mikrogramm|g)\b/i);
  if (!m) return null;
  const n = parseFloat(m[1].replace(",", ".")), u = m[2].toLowerCase();
  return u === "g" ? n * 1000 : /µg|mcg|micro|mikro/.test(u) ? n / 1000 : n;
};
const sameIng = (a, b) => String(a || "").trim().toLowerCase() === String(b || "").trim().toLowerCase();
const normPzn = s => { const d = String(s || "").replace(/\D/g, ""); return d && d.length <= 8 ? d.padStart(8, "0") : null; };
const msg = (kind, text) => el("div", {class: "msg " + kind, text});

let storageOk = true;
function load() {
  try { const s = JSON.parse(localStorage.getItem(KEY) || "null"); if (s && s.schema === 1) return s; }
  catch (e) { storageOk = false; }
  return clone(SEED.state);
}
let state = load();
let view = ["doctor", "pharmacy", "patient"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "doctor";
const ui = {disp: {}, cardSel: "preview", issuer: "", reported: {name: "", how: ""}};
function persist() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { storageOk = false; } }
function save() { persist(); render(); }
function log(who, what) { state.audit.push({at: nowIso(), who: who || "unknown", what}); }

const RX = () => state.prescriptions[0];
const confirmedV = () => [...RX().versions].reverse().find(v => v.status === "confirmed") || null;
const draftV = () => RX().versions.find(v => v.status === "draft") || null;
const dispFor = itemId => state.dispensings.filter(d => d.item_id === itemId && d.confirmed)
  .sort((a, b) => (a.date + a.recorded_at).localeCompare(b.date + b.recorded_at));
const ncCount = v => (isNC(v.prescription_date) ? 1 : 0) + (isNC(v.prescriber) ? 1 : 0) +
  v.items.filter(i => i.status === "active").reduce((n, i) =>
    n + ["product_name", "dosage_form", "prescribed_pzn", "dose_unit", "purpose"].filter(k => isNC(i[k])).length +
    SLOTS.filter(([k]) => isNC(i.dose[k])).length, 0);

function render() {
  for (const b of document.querySelectorAll(".tab")) b.classList.toggle("on", b.dataset.view === view);
  const root = $("view");
  root.replaceChildren();
  if (!storageOk) put(root, msg("bad", "Browser storage is not available: changes are lost when this page closes. Use Export records to keep them."));
  ({doctor: renderDoctor, pharmacy: renderPharmacy, patient: renderPatient})[view](root);
}

/* ======================= Doctor view ======================= */
function patientBox(editable) {
  const p = state.patient;
  const field = (label, key, type) => {
    const v = p[key];
    const cell = editable
      ? el("td", {}, el("input", {type: type || "text", value: v ?? "", class: isNC(v) ? "nc" : "", placeholder: NC,
          onchange: ev => { const nv = blankToNull(ev.target.value); if (nv !== p[key]) { log("doctor", `Patient ${label}: ${show(p[key])} → ${show(nv)}`); p[key] = nv; save(); } }}))
      : el("td", {class: isNC(v) ? "nc" : "", text: show(v)});
    return el("tr", {}, el("th", {text: label}), cell);
  };
  return el("section", {class: "box"}, el("h2", {text: "Patient"}),
    el("table", {}, el("tbody", {},
      field("Name", "name"),
      el("tr", {}, el("th", {text: "Patient ID"}), el("td", {text: p.patient_id + " (" + p.patient_id_note + ")"})),
      el("tr", {}, el("th", {text: "Age (as stated)"}), el("td", {text: show(p.age_as_stated)})),
      field("Date of birth", "date_of_birth", "date"),
      el("tr", {}, el("th", {text: "Conditions (as stated on the source)"}), el("td", {text: (p.conditions_as_stated || []).join(", ") || NC})),
      el("tr", {}, el("th", {text: "Data"}), el("td", {text: p.fictional ? "Fictional demo patient" : "—"})))));
}

function itemTable(v, editable) {
  const cols = ["#", "Ingredient", "Product name", "Strength", "Dosage form", "Prescribed PZN",
                ...SLOTS.map(s => s[1]), "Dose unit", "Treatment purpose", "Status"];
  const head = el("tr", {}, cols.map(c => el("th", {text: c})));
  const rows = [...v.items].sort((a, b) => a.med_no - b.med_no).map(it => {
    const stopped = it.status === "stopped";
    const inp = (key, cls, getter, setter) => {
      const val = getter ? getter() : it[key];
      return el("td", {}, el("input", {class: (cls || "") + (isNC(val) ? " nc" : ""), value: val ?? "", placeholder: NC, disabled: stopped,
        onchange: ev => {
          let nv = blankToNull(ev.target.value);
          if (key === "prescribed_pzn" && nv !== null) { nv = normPzn(nv); if (!nv) { alert("A PZN has up to 8 digits."); render(); return; } }
          setter ? setter(nv) : (it[key] = nv); persist(); render();
        }}));
    };
    const txt = val => el("td", {class: isNC(val) ? "nc" : "", text: show(val)});
    if (!editable) return el("tr", {class: stopped ? "stopped" : ""},
      el("td", {text: "#" + it.med_no}), txt(it.ingredient), txt(it.product_name), txt(it.strength), txt(it.dosage_form), txt(it.prescribed_pzn),
      SLOTS.map(([k]) => el("td", {class: isNC(it.dose[k]) ? "nc" : "", text: doseText(it.dose[k])})),
      txt(it.dose_unit), el("td", {class: isNC(it.purpose) ? "nc" : "", text: show(it.purpose) + (it.purpose_note ? " (" + it.purpose_note.toLowerCase() + ")" : "")}),
      el("td", {text: stopped ? `Stopped (v${it.stopped_in_version})` : "Active"}));
    return el("tr", {class: stopped ? "stopped" : ""},
      el("td", {text: "#" + it.med_no}),
      inp("ingredient", "w-m"), inp("product_name", "w-l"), inp("strength", "w-s"), inp("dosage_form", "w-m"), inp("prescribed_pzn", "w-s"),
      SLOTS.map(([k]) => inp(k, "dose", () => it.dose[k], nv => { it.dose[k] = nv; })),
      inp("dose_unit", "w-s"), inp("purpose", "w-l"),
      el("td", {}, el("select", {disabled: false, onchange: ev => {
        it.status = ev.target.value; it.stopped_in_version = it.status === "stopped" ? v.version : null; persist(); render();
      }}, el("option", {value: "active", text: "Active", selected: !stopped}), el("option", {value: "stopped", text: "Stopped", selected: stopped}))));
  });
  return el("div", {style: "overflow-x:auto"}, el("table", {}, el("thead", {}, head), el("tbody", {}, rows)));
}

function diffVersions(prev, cur) {
  if (!prev) return ["Initial version"];
  const out = [];
  const keys = [["ingredient", "ingredient"], ["product_name", "product name"], ["strength", "strength"], ["dosage_form", "dosage form"],
                ["prescribed_pzn", "PZN"], ["dose_unit", "dose unit"], ["purpose", "purpose"], ["status", "status"]];
  for (const it of cur.items) {
    const old = prev.items.find(x => x.item_id === it.item_id);
    if (!old) { out.push(`Added #${it.med_no} ${show(it.ingredient)}`); continue; }
    for (const [k, l] of keys) if ((old[k] ?? null) !== (it[k] ?? null)) out.push(`#${it.med_no} ${l}: ${show(old[k])} → ${show(it[k])}`);
    for (const [k, l] of SLOTS) if ((old.dose[k] ?? null) !== (it.dose[k] ?? null)) out.push(`#${it.med_no} ${l.toLowerCase()} dose: ${doseText(old.dose[k])} → ${doseText(it.dose[k])}`);
  }
  for (const k of ["prescription_date", "prescriber"]) if ((prev[k] ?? null) !== (cur[k] ?? null)) out.push(`${k.replace("_", " ")}: ${show(prev[k])} → ${show(cur[k])}`);
  return out.length ? out : ["No changes"];
}

function renderDoctor(root) {
  put(root, patientBox(true));
  const d = draftV(), c = confirmedV(), v = d || c, rx = RX();
  const box = el("section", {class: "box"},
    el("div", {class: "row"}, el("h2", {style: "margin:0", text: `Prescription ${rx.rx_id} · version ${v.version}`}), el("span", {class: "badge " + v.status, text: v.status})));
  if (d) {
    if (d.version === 1) put(box, msg("info", "Extracted from the mock screenshot. Check every field against the source before confirming. Fields marked “Needs confirmation” were not visible on the source; nothing was guessed."));
    else put(box, msg("info", `Draft of version ${d.version}. Dispensing and the patient card keep using version ${c ? c.version : "—"} until this draft is confirmed. A dose change here never changes what was dispensed.`));
    put(box, el("div", {class: "row"},
      el("label", {}, "Prescription date ", el("input", {type: "date", value: d.prescription_date ?? "", class: isNC(d.prescription_date) ? "nc" : "",
        onchange: ev => { d.prescription_date = blankToNull(ev.target.value); persist(); render(); }})),
      el("label", {}, "Prescriber ", el("input", {value: d.prescriber ?? "", placeholder: NC, class: isNC(d.prescriber) ? "nc" : "",
        onchange: ev => { d.prescriber = blankToNull(ev.target.value); persist(); render(); }})),
      d.version > 1 ? el("label", {}, "Reason for change ", el("input", {class: "w-l", value: d.change_note ?? "", placeholder: "required",
        onchange: ev => { d.change_note = blankToNull(ev.target.value); persist(); }})) : null));
    put(box, itemTable(d, true));
    put(box, el("div", {class: "row"}, el("button", {class: "ghost small", text: "+ Add medicine (gets the next new number)", onclick: () => {
      const it = {item_id: `${rx.rx_id}-I${String(state.nextItem++).padStart(2, "0")}`, med_no: state.nextMedNo++, ingredient: null, product_name: null,
        strength: null, dosage_form: null, prescribed_pzn: null, dose: {morning: null, noon: null, evening: null, bedtime: null}, dose_unit: null,
        purpose: null, purpose_note: null, status: "active", stopped_in_version: null};
      d.items.push(it); log("doctor", `Added medicine #${it.med_no} to draft v${d.version}`); save();
    }})));
    const n = ncCount(d);
    const who = el("input", {placeholder: "Doctor name", value: ui.doctor || "", oninput: ev => ui.doctor = ev.target.value});
    const ok = el("input", {type: "checkbox"});
    put(box, el("h3", {style: "margin-top:12px", text: "Review and confirm"}),
      n ? msg("warn", `${n} field(s) still say “Needs confirmation”. They stay marked in every view and on the card until filled in.`) : msg("ok", "All fields are filled in."),
      d.version > 1 ? el("ul", {class: "muted"}, diffVersions(c, d).map(t => el("li", {text: t}))) : null,
      el("label", {class: "chk"}, ok, el("span", {text: "I have checked this prescription against the source and confirm it."})),
      el("div", {class: "row"}, who,
        el("button", {text: `Confirm version ${d.version}`, onclick: () => {
          if (!ok.checked) return alert("Please tick the confirmation box.");
          if (!who.value.trim()) return alert("Please enter the doctor's name.");
          if (d.version > 1 && !d.change_note) return alert("Please enter the reason for the change.");
          if (d.items.some(i => i.status === "active" && isNC(i.ingredient))) return alert("Every active medicine needs an ingredient.");
          if (c) c.status = "superseded";
          d.status = "confirmed"; d.confirmed_at = nowIso(); d.confirmed_by = who.value.trim();
          log(d.confirmed_by, `Confirmed ${rx.rx_id} v${d.version}`); save();
        }}),
        d.version > 1 ? el("button", {class: "ghost", text: "Discard draft", onclick: () => {
          if (!confirm("Discard this draft? Medicine numbers it used stay retired.")) return;
          rx.versions = rx.versions.filter(x => x !== d); log("doctor", `Discarded draft v${d.version}`); save();
        }}) : null));
  } else {
    put(box, el("div", {class: "row muted"}, `Confirmed ${c.confirmed_at} by ${c.confirmed_by}. Prescription date: `,
      el("span", {class: isNC(c.prescription_date) ? "nc" : "", text: show(c.prescription_date)}), " · Prescriber: ",
      el("span", {class: isNC(c.prescriber) ? "nc" : "", text: show(c.prescriber)})));
    put(box, itemTable(c, false));
    put(box, el("div", {class: "row"}, el("button", {text: `Change prescription (creates version ${Math.max(...rx.versions.map(x => x.version)) + 1})`, onclick: () => {
      const nv = clone(c);
      Object.assign(nv, {version: Math.max(...rx.versions.map(x => x.version)) + 1, status: "draft", created_at: nowIso(), created_by: "doctor",
        confirmed_at: null, confirmed_by: null, change_note: null});
      rx.versions.push(nv); log("doctor", `Started draft v${nv.version}`); save();
    }})));
  }
  put(root, box);
  const src = rx.source;
  put(root, el("section", {class: "box"}, el("h2", {text: "Source of the extraction"}),
    el("p", {text: `${src.type}. Heading: “${src.heading_as_shown}”. ${src.layout_as_shown}. Extracted ${src.extracted_on}.`}),
    el("ul", {}, src.notes.map(t => el("li", {text: t})))));
  put(root, historyBox(), auditBox());
}

function historyBox() {
  const vs = RX().versions;
  return el("section", {class: "box"}, el("h2", {text: "Prescription version history"}),
    el("table", {}, el("thead", {}, el("tr", {}, ["Version", "Status", "Created", "Confirmed", "Reason", "Changes"].map(t => el("th", {text: t})))),
      el("tbody", {}, vs.map((v, i) => el("tr", {},
        el("td", {text: "v" + v.version}), el("td", {}, el("span", {class: "badge " + v.status, text: v.status})),
        el("td", {text: `${v.created_at} · ${v.created_by}`}), el("td", {text: v.confirmed_at ? `${v.confirmed_at} · ${v.confirmed_by}` : "—"}),
        el("td", {text: v.change_note || "—"}),
        el("td", {}, el("ul", {style: "margin:0;padding-left:16px"}, diffVersions(i ? vs[i - 1] : null, v).map(t => el("li", {text: t})))))))));
}
function auditBox() {
  return el("section", {class: "box"}, el("details", {}, el("summary", {text: `Audit log (${state.audit.length} entries)`}),
    el("table", {}, el("tbody", {}, [...state.audit].reverse().map(a => el("tr", {}, el("td", {text: a.at}), el("td", {text: a.who}), el("td", {text: a.what})))))));
}

/* ======================= Pharmacy view ======================= */
function analyse(item, pzn, last) {
  const p = pzn ? byPzn.get(pzn) || null : null;
  const pi = num(item.strength), pp = p ? num(p.strength_as_stated) : null;
  return {
    p,
    notInDb: !!pzn && !p,
    ingMismatch: !!p && !sameIng(p.active_ingredient, item.ingredient),
    strengthMismatch: !!p && pi !== null && pp !== null && pi !== pp,
    strengthUnknown: !!p && (pi === null || pp === null),
    formDiffers: !!p && !isNC(item.dosage_form) && !sameIng(p.dosage_form, item.dosage_form),
    pznDiffers: !!pzn && !isNC(item.prescribed_pzn) && pzn !== item.prescribed_pzn,
    brandSwitch: !!pzn && !!last && last.pzn !== pzn,
  };
}

function renderPharmacy(root) {
  const c = confirmedV();
  put(root, patientBox(false));
  if (!c) { put(root, msg("warn", "No confirmed prescription yet. The doctor must review and confirm the extracted prescription before anything can be dispensed.")); return; }
  put(root, el("section", {class: "box"},
    el("h2", {text: `Dispensing against ${RX().rx_id} version ${c.version}`}),
    el("p", {class: "muted", text: `Confirmed ${c.confirmed_at} by ${c.confirmed_by}. Choose the product actually handed out for each medicine. Nothing is selected or approved automatically.`}),
    draftV() ? msg("info", `A draft version ${draftV().version} exists but is not confirmed; dispensing uses version ${c.version}.`) : null));
  for (const it of [...c.items].sort((a, b) => a.med_no - b.med_no)) {
    if (it.status !== "active") { put(root, msg("info", `#${it.med_no} ${show(it.ingredient)}: stopped in version ${it.stopped_in_version}. Not dispensed. Previous refills stay in the history.`)); continue; }
    put(root, dispensePanel(it, c));
  }
}

function dispensePanel(item, c) {
  const u = ui.disp[item.item_id] ||= {pzn: "", typed: "", date: today(), qty: "", reason: "", pharmacist: ui.pharmacist || "", manualName: "", cSwitch: false, cMissing: false, cOk: false};
  const box = el("section", {class: "box"});
  const paint = () => {
    const hist = dispFor(item.item_id), last = hist.at(-1) || null;
    const a = analyse(item, u.pzn, last), p = a.p;
    const cands = DB.filter(d => sameIng(d.active_ingredient, item.ingredient))
      .sort((x, y) => (num(y.strength_as_stated) === num(item.strength)) - (num(x.strength_as_stated) === num(item.strength)) || x.product_name.localeCompare(y.product_name));
    const sel = el("select", {onchange: ev => { u.pzn = ev.target.value; u.typed = ""; u.cSwitch = u.cMissing = u.cOk = false; paint(); }},
      el("option", {value: "", text: cands.length ? `— choose from ${cands.length} ${item.ingredient} products in the database —` : "— no product with this ingredient in the database —"}),
      cands.map(d => el("option", {value: d.pzn, selected: d.pzn === u.pzn,
        text: `${d.pzn} · ${d.product_name} · pack ${d.pack_size}` + (num(d.strength_as_stated) === num(item.strength) ? " · strength matches" : " · OTHER STRENGTH")})));
    const typed = el("input", {class: "w-m", placeholder: "or enter PZN", value: u.typed, oninput: ev => u.typed = ev.target.value});
    const lookup = el("button", {class: "ghost small", text: "Look up", onclick: () => {
      const z = normPzn(u.typed); if (!z) return alert("Enter a PZN with up to 8 digits.");
      u.pzn = z; u.cSwitch = u.cMissing = u.cOk = false; paint();
    }});
    // ---- comparison table: prescribed | selected | last dispensed
    const lp = last ? last.product : null;
    const rowsDef = [
      ["Ingredient", item.ingredient, p?.active_ingredient, lp?.active_ingredient, a.ingMismatch],
      ["Product name", item.product_name, p?.product_name, lp?.product_name, false],
      ["Strength", item.strength, p?.strength_as_stated, lp?.strength_as_stated, a.strengthMismatch],
      ["Dosage form", item.dosage_form, p?.dosage_form, lp?.dosage_form, a.formDiffers],
      ["PZN", item.prescribed_pzn, u.pzn || null, last?.pzn, a.pznDiffers],
      ["Brand", null, p?.brand, lp?.brand, a.brandSwitch && lp && p && lp.brand !== p.brand],
      ["Pharmaceutical company", null, p?.pharmaceutical_company, lp?.pharmaceutical_company, false],
    ];
    const cmp = el("table", {}, el("thead", {}, el("tr", {}, ["", "Prescribed (v" + c.version + ")", "Selected for dispensing", "Last dispensed"].map(t => el("th", {text: t})))),
      el("tbody", {}, rowsDef.map(([l, pr, se, la, bad]) => el("tr", {},
        el("th", {text: l}),
        el("td", {class: (l === "Brand" || l === "Pharmaceutical company") ? "" : (isNC(pr) ? "nc" : ""), text: (l === "Brand" || l === "Pharmaceutical company") ? "—" : show(pr)}),
        el("td", {class: bad ? "diff" : "", text: u.pzn ? (se ?? (a.notInDb ? "Not in database" : "—")) : "—"}),
        el("td", {class: last && se && la && la !== se ? "diff" : "", text: last ? (la ?? "—") : "No previous dispensing"})))));
    const pics = el("div", {class: "grid2"},
      el("div", {}, el("div", {class: "cap", text: "Selected product · database photo · not actual size"}),
        p && p.images.length ? el("div", {class: "pics"}, p.images.map(s => el("img", {src: s, alt: "Tablet photo"}))) : el("div", {class: "muted", text: u.pzn ? "No photo available" : "Select a product"})),
      el("div", {}, el("div", {class: "cap", text: "Last dispensed · not actual size"}),
        lp && lp.images.length ? el("div", {class: "pics"}, lp.images.map(s => el("img", {src: s, alt: "Tablet photo"}))) : el("div", {class: "muted", text: last ? "No photo" : "—"})));
    // ---- flags
    const flags = [];
    if (a.ingMismatch) flags.push(msg("bad", "Active ingredient differs from the prescription. This cannot be recorded as a substitution here; contact the prescriber."));
    if (a.notInDb) flags.push(msg("bad", `PZN ${u.pzn} is not in the medicine database: no verified details or photo. It will not be replaced by another product.`));
    if (!cands.length) flags.push(msg("warn", `No ${item.ingredient} product is in the database.`));
    else if (!cands.some(d => num(d.strength_as_stated) === num(item.strength))) flags.push(msg("warn", `No ${item.ingredient} ${item.strength} product is in the database; the listed products have other strengths.`));
    if (a.strengthMismatch) flags.push(msg("warn", `Strength differs: prescribed ${item.strength}, selected ${p.strength_as_stated}. Needs a documented reason; the prescribed dose is not changed.`));
    if (a.strengthUnknown) flags.push(msg("warn", "Strength could not be compared automatically; check it manually."));
    if (isNC(item.dosage_form)) flags.push(msg("warn", "Prescribed dosage form: Needs confirmation (cannot be compared)."));
    if (a.formDiffers) flags.push(msg("warn", `Dosage form differs: prescribed ${item.dosage_form}, selected ${p.dosage_form}.`));
    if (a.pznDiffers) flags.push(msg("warn", `Substitution: prescribed PZN ${item.prescribed_pzn}, selected ${u.pzn}. Needs a reason.`));
    if (isNC(item.prescribed_pzn) && u.pzn) flags.push(msg("info", "No PZN on the prescription (prescribed by ingredient); the product choice is documented here."));
    if (a.brandSwitch) flags.push(msg("warn", `Brand switch: last dispensed PZN ${last.pzn} (${lp?.brand || "unknown"}). The patient keeps medicine number #${item.med_no} only after you confirm the switch.`));
    const reasonNeeded = a.strengthMismatch || a.pznDiffers || a.brandSwitch || a.formDiffers;
    // ---- form
    const form = el("div", {},
      el("div", {class: "row"},
        el("label", {}, "Dispensing date ", el("input", {type: "date", value: u.date, oninput: ev => u.date = ev.target.value})),
        el("label", {}, "Quantity ", el("input", {class: "w-l", placeholder: "e.g. 1 pack of 30 tablets", value: u.qty, oninput: ev => u.qty = ev.target.value})),
        el("label", {}, "Pharmacist ", el("input", {class: "w-m", value: u.pharmacist, oninput: ev => { u.pharmacist = ev.target.value; ui.pharmacist = ev.target.value; }}))),
      a.notInDb ? el("div", {class: "row"}, el("label", {}, "Product name as on the pack ", el("input", {class: "w-l", value: u.manualName, oninput: ev => u.manualName = ev.target.value}))) : null,
      el("div", {class: "row"}, el("label", {style: "flex:1"}, (reasonNeeded ? "Substitution / switch reason (required) " : "Substitution reason (if applicable) "),
        el("textarea", {rows: 2, style: "width:100%", oninput: ev => u.reason = ev.target.value}, u.reason))),
      a.brandSwitch ? el("label", {class: "chk"}, el("input", {type: "checkbox", checked: u.cSwitch, onchange: ev => u.cSwitch = ev.target.checked}),
        el("span", {text: `I confirm this brand switch. The patient keeps medicine number #${item.med_no}; the card photo and PZN change to this product.`})) : null,
      a.notInDb ? el("label", {class: "chk"}, el("input", {type: "checkbox", checked: u.cMissing, onchange: ev => u.cMissing = ev.target.checked}),
        el("span", {text: "I confirm this product is not in the database and record it without verified details or photo."})) : null,
      el("label", {class: "chk"}, el("input", {type: "checkbox", checked: u.cOk, onchange: ev => u.cOk = ev.target.checked}),
        el("span", {text: "I have checked the product against the prescription and confirm this dispensing."})),
      el("div", {class: "row"}, el("button", {text: `Record dispensing for #${item.med_no}`, disabled: !u.pzn || a.ingMismatch, onclick: () => {
        const err = [];
        if (!u.pzn) err.push("Select or enter a PZN.");
        if (a.ingMismatch) err.push("Active ingredient differs.");
        if (!u.date) err.push("Enter the dispensing date.");
        if (!u.qty.trim()) err.push("Enter the quantity.");
        if (!u.pharmacist.trim()) err.push("Enter the pharmacist's name.");
        if (reasonNeeded && !u.reason.trim()) err.push("Enter the substitution / switch reason.");
        if (a.brandSwitch && !u.cSwitch) err.push("Confirm the brand switch.");
        if (a.notInDb && !u.cMissing) err.push("Confirm recording a product that is not in the database.");
        if (!u.cOk) err.push("Tick the final confirmation.");
        if (err.length) return alert(err.join("\n"));
        const prod = p ? {product_name: p.product_name, active_ingredient: p.active_ingredient, strength_as_stated: p.strength_as_stated,
            dosage_form: p.dosage_form, brand: p.brand, pharmaceutical_company: p.pharmaceutical_company, images: p.images, leaflet_path: p.leaflet_path}
          : {product_name: blankToNull(u.manualName), active_ingredient: null, strength_as_stated: null, dosage_form: null, brand: null,
            pharmaceutical_company: null, images: [], leaflet_path: ""};
        const rec = {disp_id: "DSP-" + String(state.dispensings.length + 1).padStart(4, "0"), rx_id: RX().rx_id, rx_version: c.version,
          item_id: item.item_id, med_no: item.med_no, pzn: u.pzn, in_db: !!p, product: prod, date: u.date, quantity: u.qty.trim(),
          substitution_reason: blankToNull(u.reason), brand_switch: a.brandSwitch, brand_switch_confirmed: a.brandSwitch && u.cSwitch,
          previous_pzn: last ? last.pzn : null,
          flags: Object.keys(a).filter(k => k !== "p" && a[k]), pharmacist: u.pharmacist.trim(), confirmed: true, recorded_at: nowIso()};
        state.dispensings.push(rec);
        log(rec.pharmacist, `Dispensed #${item.med_no} PZN ${rec.pzn} (${rec.disp_id})` + (rec.brand_switch ? `, brand switch from ${rec.previous_pzn}` : "") + (rec.in_db ? "" : ", NOT IN DATABASE"));
        ui.disp[item.item_id] = null; save();
      }})));
    // ---- refill history
    const histT = hist.length ? el("table", {}, el("thead", {}, el("tr", {}, ["Date", "PZN", "Product", "Quantity", "Rx version", "Pharmacist", "Flags / reason"].map(t => el("th", {text: t})))),
      el("tbody", {}, [...hist].reverse().map(h => el("tr", {},
        el("td", {text: h.date}), el("td", {text: h.pzn}), el("td", {text: h.product.product_name || "Not in database"}), el("td", {text: h.quantity}),
        el("td", {text: "v" + h.rx_version}), el("td", {text: h.pharmacist}),
        el("td", {text: [h.flags.join(", "), h.substitution_reason].filter(Boolean).join(" · ") || "—"})))))
      : el("p", {class: "muted", text: "No dispensing recorded yet."});
    putOnly(box, 
      el("div", {class: "row"}, el("h2", {style: "margin:0", text: `#${item.med_no} ${show(item.ingredient)} ${show(item.strength)}`}),
        el("span", {class: "muted", text: `Dose: ${SLOTS.map(([k, l]) => l + " " + doseText(item.dose[k])).join(" · ")} · Unit: ${show(item.dose_unit)}`})),
      el("div", {class: "row"}, sel, typed, lookup), cmp, flags, pics, el("h3", {style: "margin-top:10px", text: "Record dispensing"}), form,
      el("details", {open: hist.length > 0}, el("summary", {text: `Dispensing history (${hist.length})`}), histT));
  };
  paint();
  return box;
}

/* ======================= Patient view & card ======================= */
function cardModel() {
  const c = confirmedV();
  if (!c) return null;
  const items = [...c.items].sort((a, b) => a.med_no - b.med_no);
  const rows = items.filter(i => i.status === "active").map(i => {
    const hist = dispFor(i.item_id), last = hist.at(-1) || null;
    const notes = [];
    if (last && last.brand_switch && last.brand_switch_confirmed) notes.push(`New brand since ${last.date} (before: PZN ${last.previous_pzn}). Same medicine number.`);
    if (last && !last.in_db) notes.push("Product not in the database: no photo, details not verified.");
    const ps = num(i.strength), ds = last && last.product.strength_as_stated ? num(last.product.strength_as_stated) : null;
    if (last && ps !== null && ds !== null && ps !== ds) notes.push(`Prescribed strength ${i.strength} differs from the dispensed product (${last.product.strength_as_stated}). Ask your pharmacy.`);
    if (!last) notes.push("Not yet dispensed: no product photo.");
    return {med_no: i.med_no, ingredient: i.ingredient, prescribed_strength: i.strength, dose: clone(i.dose), dose_unit: i.dose_unit, purpose: i.purpose,
            dispensed: last ? {pzn: last.pzn, date: last.date, ...clone(last.product)} : null, notes};
  });
  return {patient: clone(state.patient), generated_at: nowIso(), rx_id: RX().rx_id, rx_version: c.version, rx_confirmed_at: c.confirmed_at,
          rows, stopped: items.filter(i => i.status === "stopped").map(i => ({med_no: i.med_no, ingredient: i.ingredient, version: i.stopped_in_version})),
          reported: clone(state.reported), scope: SEED.scope};
}

function renderCard(m, label) {
  const P = m.patient;
  const info = [["Name", P.name], ["Patient ID", P.patient_id], ["Date of birth", P.date_of_birth], ["Card date", m.generated_at.slice(0, 10)],
                ["Card version", label], ["Based on", `${m.rx_id} v${m.rx_version} (confirmed ${m.rx_confirmed_at.slice(0, 10)})`]];
  const unitAllNC = m.rows.every(r => isNC(r.dose_unit));   // say it once instead of on every row
  const rows = m.rows.map(r => {
    const d = r.dispensed;
    const unitNote = unitAllNC ? null : el("div", {class: isNC(r.dose_unit) ? "cnote" : "nas", text: "Unit: " + show(r.dose_unit)});
    const photos = d && d.images.length
      ? el("div", {}, el("div", {class: "ph"}, d.images.slice(0, 2).map(s => el("img", {src: s, alt: "Tablet photo"}))), el("div", {class: "nas", text: "Not actual size"}))
      : el("div", {class: "ph"}, el("div", {class: "none", text: d ? "No photo available" : "Not yet dispensed"}));
    return el("tr", {},
      el("td", {class: "no", text: "#" + r.med_no}), el("td", {}, photos),
      el("td", {}, el("b", {text: d ? (d.product_name || "Product name not recorded") : r.ingredient}),
        el("div", {text: d ? (d.brand ? "Brand: " + d.brand : "") : "Prescribed: " + show(r.ingredient) + " " + show(r.prescribed_strength)}),
        unitNote, r.notes.map(t => el("div", {class: "cnote", text: t}))),
      el("td", {text: d && d.active_ingredient ? `${d.active_ingredient} · ${d.strength_as_stated}` : `${show(r.ingredient)} · ${show(r.prescribed_strength)} (prescribed)`}),
      el("td", {class: "pzn", text: d ? d.pzn : "—"}),
      SLOTS.map(([k]) => el("td", {class: "dose", text: doseText(r.dose[k])})),
      el("td", {text: isNC(r.purpose) ? "—" : r.purpose}));
  });
  return el("div", {class: "a4", id: "card"},
    el("div", {class: "card-head"},
      el("div", {}, el("h1", {text: "My Medicine Card"}), el("div", {text: "Demo · fictional patient"})),
      el("table", {class: "pinfo"}, el("tbody", {}, info.map(([k, v]) => el("tr", {}, el("th", {text: k}), el("td", {text: show(v)})))))),
    el("div", {class: "scope", text: "Scope: " + m.scope}),
    el("table", {class: "meds"},
      el("colgroup", {}, ["c-no", "c-ph", "c-med", "c-ing", "c-pzn", "c-dose", "c-dose", "c-dose", "c-dose", "c-pur"].map(c => el("col", {class: c}))),
      el("thead", {}, el("tr", {}, ["#", "Photos", "Medicine", "Ingredient · strength", "PZN", "Morning", "Noon", "Evening", "Bedtime", "Purpose"]
        .map(t => el("th", {text: t, class: ["Morning", "Noon", "Evening", "Bedtime"].includes(t) ? "dh" : null})))),
      el("tbody", {}, rows)),
    el("p", {class: "nas", text: "Numbers show how many units to take at each time (– = none), as confirmed by the doctor. Photos and PZN show the product last handed out by the pharmacy."}),
    unitAllNC ? el("p", {class: "cnote", text: "Dose unit (for example tablet): Needs confirmation for all medicines."}) : null,
    m.stopped.length ? el("div", {class: "card-sec"}, el("h2", {text: "Stopped medicines (numbers are not reused)"}),
      el("div", {text: m.stopped.map(s => `#${s.med_no} ${show(s.ingredient)} (stopped in prescription v${s.version})`).join(" · ")})) : null,
    el("div", {class: "card-sec"}, el("h2", {text: "Medicines you told us you also take (not dispensed here, not checked)"}),
      m.reported.length ? el("ul", {style: "margin:0;padding-left:5mm"}, m.reported.map(r => el("li", {text: `${r.name}${r.how ? " — " + r.how : ""} (reported ${r.date})`})))
        : el("div", {text: "None reported."})),
    el("div", {class: "card-foot", text: "Fictional demo data. Not connected to E-Rezept or ePA and not an official medication plan (BMP). Photos: © ABDATA | Dosing, not actual size. Do not change how you take your medicines without talking to your doctor or pharmacist."}));
}

function renderPatient(root) {
  const c = confirmedV();
  if (!c) { put(root, patientBox(false), msg("warn", "No confirmed prescription yet. The card can be created after the doctor confirms the prescription.")); return; }
  const issued = state.cards;
  if (ui.cardSel !== "preview" && !issued[ui.cardSel]) ui.cardSel = "preview";
  const pick = el("select", {onchange: ev => { ui.cardSel = ev.target.value === "preview" ? "preview" : Number(ev.target.value); render(); }},
    el("option", {value: "preview", text: "Current preview (not issued)", selected: ui.cardSel === "preview"}),
    issued.map((cv, i) => el("option", {value: i, selected: ui.cardSel === i, text: `Version ${cv.card_version} · issued ${cv.issued_at} by ${cv.issued_by}`})));
  const who = el("input", {placeholder: "Issued by", value: ui.issuer, oninput: ev => ui.issuer = ev.target.value});
  const model = ui.cardSel === "preview" ? cardModel() : issued[ui.cardSel].model;
  const label = ui.cardSel === "preview" ? "Preview, not issued" : `${issued[ui.cardSel].card_version} (${issued[ui.cardSel].issued_at.slice(0, 10)})`;
  const notDispensed = model.rows.filter(r => !r.dispensed).length;
  put(root, el("section", {class: "box noprint"},
    el("h2", {text: "Patient Medicine Card"}),
    el("div", {class: "row"}, "Show: ", pick,
      el("button", {text: "Print / Save as PDF", onclick: () => window.print()}),
      ui.cardSel === "preview" ? [who, el("button", {class: "ghost", text: `Issue as version ${issued.length + 1}`, onclick: () => {
        if (!ui.issuer.trim()) return alert("Enter who issues the card.");
        state.cards.push({card_version: issued.length + 1, issued_at: nowIso(), issued_by: ui.issuer.trim(), model: cardModel()});
        log(ui.issuer.trim(), `Issued medicine card version ${issued.length}`); ui.cardSel = issued.length - 1; save();
      }})] : null),
    el("p", {class: "muted", text: "In the print dialog choose “Save as PDF” to create a PDF. Issued versions are frozen snapshots; the preview always uses the latest confirmed prescription and the latest recorded dispensing."}),
    notDispensed ? msg("warn", `${notDispensed} medicine(s) have no recorded dispensing yet, so they appear without a product photo.`) : null));
  put(root, renderCard(model, label));
  const f = ui.reported;
  put(root, el("section", {class: "box noprint", style: "margin-top:16px"},
    el("h2", {text: "Medicines the patient reports taking"}),
    el("p", {class: "muted", text: "Recorded as told by the patient. Kept separate from dispensed medicines and not checked against the database or the prescription."}),
    el("div", {class: "row"},
      el("input", {class: "w-l", placeholder: "Medicine name", value: f.name, oninput: ev => f.name = ev.target.value}),
      el("input", {class: "w-l", placeholder: "How taken (as told)", value: f.how, oninput: ev => f.how = ev.target.value}),
      el("button", {class: "ghost", text: "Add reported medicine", onclick: () => {
        if (!f.name.trim()) return alert("Enter the medicine name.");
        state.reported.push({name: f.name.trim(), how: f.how.trim(), date: today()});
        log("patient", `Reported taking: ${f.name.trim()}`); ui.reported = {name: "", how: ""}; save();
      }})),
    state.reported.length ? el("ul", {}, state.reported.map((r, i) => el("li", {}, `${r.name}${r.how ? " — " + r.how : ""} (reported ${r.date}) `,
      el("button", {class: "ghost small", text: "Remove", onclick: () => { log("patient", `Removed reported: ${r.name}`); state.reported.splice(i, 1); save(); }}))))
      : el("p", {class: "muted", text: "None reported."})));
}

/* ======================= wiring ======================= */
for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => { view = b.dataset.view; location.hash = view; render(); });
$("reset").addEventListener("click", () => {
  if (!confirm("Reset the demo? All saved prescriptions, dispensings, cards and reported medicines in this browser are replaced by the original mock data.")) return;
  state = clone(SEED.state); ui.disp = {}; ui.cardSel = "preview"; save();
});
$("export").addEventListener("click", () => {
  const a = el("a", {href: URL.createObjectURL(new Blob([JSON.stringify(state, null, 2)], {type: "application/json"})),
                     download: `medlink-demo-records-${today()}.json`});
  document.body.append(a); a.click(); a.remove();
});
$("import").addEventListener("click", () => $("import-file").click());
$("import-file").addEventListener("change", async ev => {
  const f = ev.target.files[0]; if (!f) return;
  try {
    const s = JSON.parse(await f.text());
    if (s.schema !== 1 || !Array.isArray(s.prescriptions)) throw new Error("not a records file from this demo");
    if (!confirm("Replace the records in this browser with the imported file?")) return;
    state = s; ui.disp = {}; ui.cardSel = "preview"; save();
  } catch (e) { alert("Import failed: " + e.message); }
  ev.target.value = "";
});
window.addEventListener("hashchange", () => { const h = location.hash.slice(1); if (["doctor", "pharmacy", "patient"].includes(h) && h !== view) { view = h; render(); } });
render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
