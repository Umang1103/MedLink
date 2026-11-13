"""Local medicine database: PZN -> name, strength, pill appearance, image.

Stored in the `medicine` table of instance/medlink.db with the PZN as primary
key. The table is seeded with made-up demo data on first use; replace/extend it
with the real data (e.g. via an import script). A pill image is picked up
automatically when a file named after the PZN exists in
app/static/medicine_images/ (svg, png, jpg, jpeg or webp).

lookup() returns:
    {"pzn", "name", "strength", "form", "shape", "color", "color2", "scored",
     "imprint", "image_url" (str | None)}   or None if the PZN is unknown.
`shape` is one of: round, oval, capsule. Without an image the UI draws the pill
from shape/color.
"""
import os

from flask import current_app, url_for

from . import db

# Made-up demo data. The PZNs are example numbers with valid check digits, not real products.
_DEMO = [
    ("90000015", "Metoprolol succinate", "47.5 mg", "tablet", "round", "#F7F7F2", None, 1, "M 47"),
    ("90000021", "Furosemide", "40 mg", "tablet", "round", "#F5F5EE", None, 1, "F 40"),
    ("90000038", "Ramipril", "5 mg", "capsule", "capsule", "#F2C94C", "#F7F7F2", 0, "R 5"),
    ("90000044", "Apixaban", "5 mg", "tablet", "oval", "#E8A0B4", None, 0, "A 5"),
    ("90000050", "Spironolactone", "25 mg", "tablet", "round", "#E9D8B4", None, 1, "S 25"),
    ("90000067", "Atorvastatin", "20 mg", "tablet", "oval", "#F4F4F4", None, 0, "A 20"),
]
_IMAGE_EXTS = ("svg", "png", "jpg", "jpeg", "webp")


def _conn():
    conn = db.connect()
    if conn.execute("SELECT 1 FROM medicine LIMIT 1").fetchone() is None:
        with conn:
            conn.executemany(
                "INSERT INTO medicine (pzn, name, strength, form, shape, color, color2, scored, imprint) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                _DEMO,
            )
    return conn


def _image_url(pzn):
    for ext in _IMAGE_EXTS:
        rel = f"medicine_images/{pzn}.{ext}"
        if os.path.isfile(os.path.join(current_app.static_folder, rel)):
            return url_for("static", filename=rel)
    return None


def _row_to_dict(row):
    med = dict(row)
    med["scored"] = bool(med["scored"])
    med["image_url"] = _image_url(med["pzn"])
    return med


def lookup(pzn: str):
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM medicine WHERE pzn = ?", (pzn,)).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def catalog():
    """All medicines as {pzn: medicine}; used for the quick-pick list in the UI."""
    conn = _conn()
    try:
        rows = conn.execute("SELECT * FROM medicine ORDER BY pzn").fetchall()
    finally:
        conn.close()
    return {r["pzn"]: _row_to_dict(r) for r in rows}
