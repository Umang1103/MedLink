#!/usr/bin/env python3
"""
build_gallery.py
----------------
Build gallery.html: a local, offline medicine picture gallery from medicines.csv.

  python build_gallery.py            -> writes gallery.html and opens it in the browser
  python build_gallery.py --no-open  -> only writes the file

At build time every record is checked:
  * tablet photo usable  = file exists and is a real JPEG/PNG
  * leaflet usable       = file exists and is a real PDF
  * needs review         = no usable photo, no usable leaflet, colour/shape
                           missing, imprint unknown, or an open issue in notes
The header numbers are computed from these checks. Re-run after editing the CSV.
The page only references local files; nothing is uploaded or fetched.
"""
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
CSV = ROOT / "medicines.csv"
OUT = ROOT / "gallery.html"

COLOR_CATS = [  # (category, regex on the English colour text)
    ("white", r"white|cream"), ("yellow", r"yellow"), ("orange", r"orange|apricot|peach"),
    ("pink", r"pink"), ("red", r"\bred|reddish"), ("brown", r"brown"), ("beige", r"beige"),
    ("blue", r"blue"), ("green", r"green"), ("violet", r"violet|purple"), ("grey", r"gr[ae]y"),
]
SHAPE_CATS = [
    ("round", r"\bround"), ("oval", r"\boval"), ("oblong", r"oblong"), ("capsule-shaped", r"capsule"),
    ("diamond-shaped", r"diamond"), ("heart-shaped", r"heart"), ("teardrop-shaped", r"teardrop"),
    ("polygonal", r"octagonal|hexagonal|pentagonal|triangular"),
]
ISSUE_IN_NOTES = re.compile(r"differ|conflict|unknown|not legible|not collected", re.I)


def is_image(p):
    try:
        b = p.read_bytes()[:8]
    except OSError:
        return False
    return b[:3] == b"\xff\xd8\xff" or b == b"\x89PNG\r\n\x1a\n"


def is_pdf(p):
    try:
        return p.read_bytes()[:4] == b"%PDF"
    except OSError:
        return False


def cats(text, table):
    t = (text or "").lower()
    return [name for name, pat in table if re.search(pat, t)]


def main():
    with open(CSV, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    items = []
    for r in rows:
        imgs = [r[c] for c in ("tablet_image_1", "tablet_image_2") if r.get(c) and is_image(ROOT / r[c])]
        leaflet = r.get("leaflet_path", "")
        leaflet_ok = bool(leaflet) and is_pdf(ROOT / leaflet)
        reasons = []
        if not imgs:
            reasons.append("No usable tablet photo")
        if not leaflet_ok:
            reasons.append("No usable leaflet PDF")
        if not r["color"].strip():
            reasons.append("Colour missing")
        if not r["shape"].strip():
            reasons.append("Shape missing")
        if not r["imprint"].strip():
            reasons.append("Imprint unknown")
        if ISSUE_IN_NOTES.search(r.get("notes", "")):
            reasons.append("Open issue in notes")
        it = {k: r.get(k, "") for k in r}
        it.update(images=[i.replace("\\", "/") for i in imgs], leaflet_ok=leaflet_ok,
                  color_cats=cats(r["color"], COLOR_CATS) or (["other"] if r["color"].strip() else []),
                  shape_cats=cats(r["shape"], SHAPE_CATS) or (["other"] if r["shape"].strip() else []),
                  review=reasons)
        items.append(it)
    stats = {"total": len(items), "with_photo": sum(bool(i["images"]) for i in items),
             "with_leaflet": sum(i["leaflet_ok"] for i in items),
             "review": sum(bool(i["review"]) for i in items),
             "built": datetime.now().strftime("%Y-%m-%d %H:%M")}
    data = json.dumps({"items": items, "stats": stats}, ensure_ascii=False).replace("</", "<\\/")
    OUT.write_text(TEMPLATE.replace("/*__DATA__*/null", data), encoding="utf-8")
    print(f"wrote {OUT.name}: {stats}")
    if "--no-open" not in sys.argv:
        import webbrowser
        webbrowser.open(OUT.as_uri())  # show the gallery in the default browser
        print("opened", OUT.as_uri())


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Medicine Gallery</title>
<style>
  :root { --bg:#f4f6f8; --card:#fff; --ink:#1d2733; --muted:#5f6b78; --line:#dde3ea; --accent:#1f4e79; --warn:#b45309; --warnbg:#fff4e5; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Microsoft YaHei",sans-serif; background:var(--bg); color:var(--ink); }
  header { background:var(--accent); color:#fff; padding:18px 24px; }
  header h1 { margin:0 0 4px; font-size:22px; }
  header p { margin:0; opacity:.85; font-size:13px; }
  .stats { display:flex; gap:12px; flex-wrap:wrap; padding:16px 24px 0; }
  .stat { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 18px; min-width:170px; }
  .stat b { display:block; font-size:28px; }
  .stat span { color:var(--muted); font-size:13px; }
  .stat.warn b { color:var(--warn); }
  .controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center; padding:16px 24px; position:sticky; top:0; background:var(--bg); z-index:5; border-bottom:1px solid var(--line); }
  .controls input, .controls select { padding:8px 10px; border:1px solid var(--line); border-radius:8px; font-size:14px; background:#fff; }
  .controls input[type=search] { flex:1 1 260px; }
  .controls label { font-size:14px; color:var(--muted); display:flex; align-items:center; gap:6px; }
  button { cursor:pointer; border:1px solid var(--accent); background:var(--accent); color:#fff; border-radius:8px; padding:8px 14px; font-size:14px; }
  button.ghost { background:#fff; color:var(--accent); }
  button:disabled { opacity:.45; cursor:not-allowed; }
  #count { color:var(--muted); font-size:14px; margin-left:auto; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:16px; padding:18px 24px 110px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; display:flex; flex-direction:column; cursor:pointer; transition:box-shadow .15s; }
  .card:hover { box-shadow:0 4px 14px rgba(0,0,0,.08); }
  .card.selected { outline:3px solid var(--accent); }
  .thumb { aspect-ratio:1/1; background:#8c9196; display:flex; align-items:center; justify-content:center; color:#fff; font-size:14px; }
  .thumb img { width:100%; height:100%; object-fit:cover; display:block; }
  .body { padding:10px 12px 12px; display:flex; flex-direction:column; gap:4px; flex:1; }
  .name { font-weight:600; font-size:14px; line-height:1.3; }
  .meta { font-size:13px; color:var(--muted); }
  .badges { display:flex; flex-wrap:wrap; gap:4px; margin-top:4px; }
  .badge { font-size:11px; padding:2px 7px; border-radius:20px; background:#e8eef5; color:#2b4a68; }
  .badge.warn { background:var(--warnbg); color:var(--warn); }
  .cmp { margin-top:auto; padding-top:8px; font-size:13px; color:var(--muted); display:flex; align-items:center; gap:6px; }
  .empty { padding:40px 24px; color:var(--muted); }
  .bar { position:fixed; left:0; right:0; bottom:0; background:#fff; border-top:1px solid var(--line); padding:12px 24px; display:flex; align-items:center; gap:12px; z-index:6; }
  .bar .sel { flex:1; font-size:14px; color:var(--muted); }
  dialog { border:none; border-radius:14px; padding:0; width:min(1000px,95vw); max-height:92vh; }
  dialog::backdrop { background:rgba(20,30,40,.55); }
  .dlg-head { display:flex; justify-content:space-between; align-items:center; padding:14px 20px; border-bottom:1px solid var(--line); position:sticky; top:0; background:#fff; }
  .dlg-head h2 { margin:0; font-size:18px; }
  .dlg-body { padding:16px 20px 22px; overflow:auto; }
  .pics { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }
  .pics img { width:260px; max-width:100%; border-radius:8px; }
  table { border-collapse:collapse; width:100%; font-size:14px; }
  th, td { text-align:left; vertical-align:top; padding:7px 8px; border-bottom:1px solid var(--line); }
  th { width:210px; color:var(--muted); font-weight:500; }
  tr.diff td { background:#fff8db; }
  .links { display:flex; gap:10px; flex-wrap:wrap; margin:12px 0; }
  .links a { color:var(--accent); font-weight:600; }
  .review { background:var(--warnbg); color:var(--warn); border-radius:8px; padding:8px 12px; font-size:14px; margin-bottom:12px; }
  .cmp-grid td img { width:100%; max-width:220px; border-radius:8px; }
  footer { color:var(--muted); font-size:12px; padding:0 24px 90px; }
</style>
</head>
<body>
<header>
  <h1>Medicine Gallery</h1>
  <p id="sub"></p>
  <p style="margin-top:6px"><a href="workflow.html" style="color:#fff;font-weight:600">Open demo: Prescription → Pharmacy Dispensing → Patient Medicine Card</a></p>
</header>
<section class="stats">
  <div class="stat"><b id="s-total"></b><span>Products</span></div>
  <div class="stat"><b id="s-photo"></b><span>With usable photo</span></div>
  <div class="stat"><b id="s-leaflet"></b><span>With usable leaflet</span></div>
  <div class="stat warn"><b id="s-review"></b><span>Needs review</span></div>
</section>
<section class="controls">
  <input type="search" id="q" placeholder="Search PZN or name">
  <select id="f-ing"></select>
  <select id="f-brand"></select>
  <select id="f-color"></select>
  <select id="f-shape"></select>
  <label><input type="checkbox" id="f-review"> Needs review only</label>
  <button class="ghost" id="reset">Reset</button>
  <span id="count"></span>
</section>
<main class="grid" id="grid"></main>
<footer>Local prototype. Tablet photos © ABDATA | Dosing; product data from apotheken-umschau.de. Not licensed for reuse: do not publish. Not clinically or pharmacist verified.</footer>
<div class="bar">
  <span class="sel" id="sel">Select two products to compare</span>
  <button class="ghost" id="clear" disabled>Clear</button>
  <button id="compare" disabled>Compare</button>
</div>
<dialog id="dlg"><div class="dlg-head"><h2 id="dlg-title"></h2><button class="ghost" id="dlg-close">Close</button></div><div class="dlg-body" id="dlg-body"></div></dialog>

<script>
const DATA = /*__DATA__*/null;
const items = DATA.items, S = DATA.stats;
const $ = id => document.getElementById(id);
function el(tag, attrs, ...kids) {           // safe DOM builder: text is never parsed as HTML
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
}

$("s-total").textContent = S.total;
$("s-photo").textContent = S.with_photo;
$("s-leaflet").textContent = S.with_leaflet;
$("s-review").textContent = S.review;
$("sub").textContent = `Built ${S.built} from medicines.csv · Click a card for details · Tick two cards to compare`;

function fillSelect(sel, label, values) {
  sel.append(el("option", {value: "", text: label}));
  for (const v of values) sel.append(el("option", {value: v, text: v}));
}
const uniq = a => [...new Set(a.filter(Boolean))].sort((x, y) => x.localeCompare(y, "de"));
fillSelect($("f-ing"), "All ingredients", uniq(items.map(i => i.active_ingredient)));
fillSelect($("f-brand"), "All brands", uniq(items.map(i => i.brand)));
fillSelect($("f-color"), "All colours", uniq(items.flatMap(i => i.color_cats)));
fillSelect($("f-shape"), "All shapes", uniq(items.flatMap(i => i.shape_cats)));

const selected = [];
function matches(i) {
  const q = $("q").value.trim().toLowerCase();
  if (q) {
    const hay = [i.pzn, i.pzn.replace(/^0+/, ""), i.product_name, i.brand, i.active_ingredient].join(" ").toLowerCase();
    if (!q.split(/\s+/).every(t => hay.includes(t))) return false;
  }
  if ($("f-ing").value && i.active_ingredient !== $("f-ing").value) return false;
  if ($("f-brand").value && i.brand !== $("f-brand").value) return false;
  if ($("f-color").value && !i.color_cats.includes($("f-color").value)) return false;
  if ($("f-shape").value && !i.shape_cats.includes($("f-shape").value)) return false;
  if ($("f-review").checked && !i.review.length) return false;
  return true;
}

function card(i) {
  const isSel = selected.includes(i);
  const thumb = el("div", {class: "thumb"}, i.images.length
    ? el("img", {src: i.images[0], alt: "Tablet photo of " + i.product_name, loading: "lazy"})
    : document.createTextNode("No photo"));
  const badges = el("div", {class: "badges"});
  if (i.color) badges.append(el("span", {class: "badge", text: i.color}));
  if (i.shape) badges.append(el("span", {class: "badge", text: i.shape}));
  if (i.review.length) badges.append(el("span", {class: "badge warn", text: "Needs review", title: i.review.join("; ")}));
  const box = el("input", {type: "checkbox"});
  box.checked = isSel;
  box.addEventListener("click", ev => { ev.stopPropagation(); toggleSelect(i); });
  return el("article", {class: "card" + (isSel ? " selected" : ""), onclick: () => showDetail(i)},
    thumb,
    el("div", {class: "body"},
      el("div", {class: "name", text: i.product_name}),
      el("div", {class: "meta", text: "PZN " + i.pzn + " · " + i.strength_as_stated}),
      el("div", {class: "meta", text: i.brand}),
      badges,
      el("label", {class: "cmp", onclick: ev => ev.stopPropagation()}, box, document.createTextNode("Compare"))));
}

function render() {
  const list = items.filter(matches);
  const grid = $("grid");
  grid.replaceChildren(...list.map(card));
  if (!list.length) grid.append(el("div", {class: "empty", text: "No products match"}));
  $("count").textContent = `Showing ${list.length} of ${items.length}`;
  $("sel").textContent = selected.length
    ? "Selected: " + selected.map(i => i.product_name + " (" + i.pzn + ")").join("  vs  ")
    : "Select two products to compare";
  $("compare").disabled = selected.length !== 2;
  $("clear").disabled = !selected.length;
}

function toggleSelect(i) {
  const k = selected.indexOf(i);
  if (k >= 0) selected.splice(k, 1);
  else { if (selected.length === 2) selected.shift(); selected.push(i); }
  render();
}

const FIELDS = [
  ["PZN", "pzn"], ["Market", "market"], ["Product name", "product_name"], ["Active ingredient", "active_ingredient"],
  ["Ingredient form (as stated)", "ingredient_form_as_stated"], ["Strength", "strength_as_stated"],
  ["Dosage form", "dosage_form"], ["Pack size", "pack_size"], ["Brand", "brand"],
  ["Pharmaceutical company", "pharmaceutical_company"], ["Manufacturer", "manufacturer"],
  ["Colour", "color"], ["Shape", "shape"], ["Imprint", "imprint"], ["Score line", "score_line"],
  ["Appearance (leaflet, German)", "appearance_description_original"], ["Leaflet revision", "leaflet_revision_date"],
  ["Retrieved", "retrieved_at"], ["Verification status", "verification_status"], ["Notes", "notes"],
];
const pics = i => el("div", {class: "pics"}, ...(i.images.length
  ? i.images.map(src => el("a", {href: src, target: "_blank"}, el("img", {src, alt: "Tablet photo"})))
  : [el("div", {class: "thumb", style: "width:260px", text: "No photo"})]));
function links(i) {
  const box = el("div", {class: "links"});
  if (i.leaflet_ok) box.append(el("a", {href: i.leaflet_path, target: "_blank", text: "Open leaflet PDF"}));
  else box.append(el("span", {text: "No leaflet PDF available"}));
  if (i.product_source_url) box.append(el("a", {href: i.product_source_url, target: "_blank", rel: "noopener noreferrer", text: "Product page"}));
  if (i.manufacturer_source_url) box.append(el("a", {href: i.manufacturer_source_url, target: "_blank", rel: "noopener noreferrer", text: "Manufacturer page"}));
  return box;
}

function openDialog(title, body) {
  $("dlg-title").textContent = title;
  $("dlg-body").replaceChildren(...body);
  $("dlg").showModal();
}

function showDetail(i) {
  const rows = FIELDS.filter(([, k]) => i[k]).map(([l, k]) => el("tr", {}, el("th", {text: l}), el("td", {text: i[k]})));
  openDialog(i.product_name, [
    i.review.length ? el("div", {class: "review", text: "Needs review: " + i.review.join("; ")}) : null,
    pics(i), links(i), el("table", {}, el("tbody", {}, ...rows)),
  ].filter(Boolean));
}

function showCompare() {
  const [a, b] = selected;
  const head = el("tr", {}, el("th", {text: ""}), el("td", {}, pics(a), links(a)), el("td", {}, pics(b), links(b)));
  const rows = FIELDS.map(([l, k]) => el("tr", {class: (a[k] || "") !== (b[k] || "") ? "diff" : ""},
    el("th", {text: l}), el("td", {text: a[k] || "—"}), el("td", {text: b[k] || "—"})));
  rows.push(el("tr", {}, el("th", {text: "Needs review"}),
    el("td", {text: a.review.join("; ") || "—"}), el("td", {text: b.review.join("; ") || "—"})));
  openDialog("Compare (differences highlighted)", [el("table", {class: "cmp-grid"}, el("tbody", {}, head, ...rows))]);
}

for (const id of ["q", "f-ing", "f-brand", "f-color", "f-shape", "f-review"]) $(id).addEventListener("input", render);
$("reset").addEventListener("click", () => {
  $("q").value = ""; for (const id of ["f-ing", "f-brand", "f-color", "f-shape"]) $(id).value = "";
  $("f-review").checked = false; render();
});
$("compare").addEventListener("click", showCompare);
$("clear").addEventListener("click", () => { selected.length = 0; render(); });
$("dlg-close").addEventListener("click", () => $("dlg").close());
$("dlg").addEventListener("click", ev => { if (ev.target === $("dlg")) $("dlg").close(); });
render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
