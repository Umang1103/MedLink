"""SQLite medicine catalogue, import CLI, and read-only HTTP API."""
import argparse
import csv
import json
from pathlib import Path
import re
import sqlite3
import tempfile

from flask import Blueprint, abort, current_app, jsonify, request, send_from_directory, url_for

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "Hackthon" / "data"
DEFAULT_DB = DATA / "medicines.sqlite3"
IMAGE_FIELDS = ("tablet_image_1", "tablet_image_2", "package_image")
bp = Blueprint("medicine_catalog", __name__, url_prefix="/api/medicines")


def connect(database):
    db = sqlite3.connect(str(database))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def build_database(csv_path=DATA / "doc" / "medicines.csv", image_root=DATA / "images", database=DEFAULT_DB):
    """Validate sources before atomically replacing the generated catalogue."""
    image_root = Path(image_root).resolve()
    database = Path(database)
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames
        if not fields or "pzn" not in fields or "product_name" not in fields:
            raise ValueError("CSV requires pzn and product_name columns")
        if len(set(fields)) != len(fields) or any(not re.fullmatch(r"[a-z][a-z0-9_]*", f) for f in fields):
            raise ValueError("CSV column names must be unique SQL-safe identifiers")
        rows = list(reader)
    seen, images = set(), []
    for row in rows:
        pzn = row["pzn"]
        if not re.fullmatch(r"\d{8}", pzn) or pzn in seen:
            raise ValueError(f"Invalid or duplicate PZN: {pzn!r}")
        seen.add(pzn)
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Malformed CSV row for {pzn}")
        for field in IMAGE_FIELDS:
            relative = row.get(field, "")
            if not relative:
                continue
            file = (image_root / relative).resolve()
            if not file.is_relative_to(image_root) or not file.is_file() or Path(relative).parts[0] != pzn:
                raise ValueError(f"Missing or mismatched image for {pzn}: {relative}")
            images.append((pzn, field, relative))
    database.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=database.parent, suffix=".sqlite3", delete=False) as temp:
        temporary = Path(temp.name)
    try:
        db = connect(temporary)
        try:
            with db:
                columns = ", ".join(f'"{f}" TEXT NOT NULL' + (" PRIMARY KEY" if f == "pzn" else "") for f in fields)
                db.execute(f"CREATE TABLE medicines ({columns})")
                db.execute('''CREATE TABLE medicine_images (
                    id INTEGER PRIMARY KEY, pzn TEXT NOT NULL REFERENCES medicines(pzn),
                    kind TEXT NOT NULL, path TEXT NOT NULL,
                    UNIQUE(pzn, kind), UNIQUE(path))''')
                quoted = ", ".join(f'"{f}"' for f in fields)
                db.executemany(f'INSERT INTO medicines ({quoted}) VALUES ({", ".join("?" for _ in fields)})',
                               ([row[f] for f in fields] for row in rows))
                db.executemany("INSERT INTO medicine_images (pzn, kind, path) VALUES (?, ?, ?)", images)
                for field in ("product_name", "active_ingredient", "brand", "manufacturer"):
                    if field in fields:
                        db.execute(f'CREATE INDEX idx_medicines_{field} ON medicines("{field}")')
                db.execute("PRAGMA user_version = 1")
        finally:
            db.close()
        temporary.replace(database)
    finally:
        temporary.unlink(missing_ok=True)
    return {"medicines": len(rows), "images": len(images),
            "without_tablet_images": [row["pzn"] for row in rows if not any(row.get(f) for f in IMAGE_FIELDS[:2])],
            "database": str(database)}


def search(database=DEFAULT_DB, query="", filters=None, limit=20, offset=0):
    """Prefix search by product name, active ingredient or PZN; filters are ANDed."""
    if not Path(database).is_file():
        raise FileNotFoundError(f"Catalogue not found: {database}. Run python -m app.medicine_catalog build")
    db = connect(database)
    try:
        fields = [row[1] for row in db.execute("PRAGMA table_info(medicines)")]
        # Python casefold supports Unicode (SQLite's built-in lower/NOCASE is ASCII-only).
        db.create_function("casefold", 1, lambda value: (value or "").casefold(), deterministic=True)
        where, params = [], []
        query = query.strip()
        if query:
            search_fields = ("product_name", "active_ingredient", "pzn")
            where.append("(" + " OR ".join(f'instr(casefold("{field}"), ?) = 1' for field in search_fields) + ")")
            params.extend([query.casefold()] * len(search_fields))
        for field, value in (filters or {}).items():
            if field not in fields:
                raise ValueError(f"Unknown medicine field: {field}")
            if field == "pzn":
                where.append('"pzn" = ?')
                params.append(value.zfill(8) if value.isdigit() else value)
            else:
                where.append(f'instr(casefold("{field}"), ?) > 0')
                params.append(value.casefold())
        clause = " WHERE " + " AND ".join(where) if where else ""
        total = db.execute("SELECT count(*) FROM medicines" + clause, params).fetchone()[0]
        rows = db.execute("SELECT * FROM medicines" + clause + " ORDER BY pzn LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["images"] = [dict(img) for img in db.execute(
                "SELECT id, kind, path FROM medicine_images WHERE pzn = ? ORDER BY kind", (row["pzn"],))]
            results.append(item)
        return {"total": total, "limit": limit, "offset": offset, "results": results}
    finally:
        db.close()


def api_search(**kwargs):
    try:
        result = search(current_app.config["MEDICINES_DATABASE"], **kwargs)
    except FileNotFoundError as error:
        abort(503, description=str(error))
    except ValueError as error:
        abort(400, description=str(error))
    for item in result["results"]:
        for img in item["images"]:
            img["url"] = url_for("medicine_catalog.image", image_id=img["id"])
    return result


@bp.get("")
@bp.get("/")
def lookup():
    try:
        limit = int(request.args.get("limit", "20"))
        offset = int(request.args.get("offset", "0"))
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError()
    except ValueError:
        abort(400, description="limit must be 1–200 and offset must be nonnegative")
    filters = {key: value for key, value in request.args.items() if key not in ("q", "limit", "offset")}
    return jsonify(api_search(query=request.args.get("q", ""), filters=filters, limit=limit, offset=offset))


@bp.get("/<pzn>")
def detail(pzn):
    result = api_search(filters={"pzn": pzn}, limit=1)
    if not result["results"]:
        abort(404)
    return jsonify(result["results"][0])


@bp.get("/images/<int:image_id>")
def image(image_id):
    database = current_app.config["MEDICINES_DATABASE"]
    if not Path(database).is_file():
        abort(503, description="Medicine catalogue has not been built")
    db = connect(database)
    try:
        row = db.execute("SELECT path FROM medicine_images WHERE id = ?", (image_id,)).fetchone()
    finally:
        db.close()
    if row is None:
        abort(404)
    return send_from_directory(current_app.config["MEDICINES_IMAGE_ROOT"], row["path"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--csv", type=Path, default=DATA / "doc" / "medicines.csv")
    build.add_argument("--images", type=Path, default=DATA / "images")
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    query = sub.add_parser("search")
    query.add_argument("query", nargs="?", default="")
    query.add_argument("--db", type=Path, default=DEFAULT_DB)
    query.add_argument("--pzn")
    query.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if args.command == "build":
        result = build_database(args.csv, args.images, args.db)
    else:
        result = search(args.db, args.query, {"pzn": args.pzn} if args.pzn else None, args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
