import csv
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.medicine_catalog import DATA, build_database, connect, search


class CatalogueTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "catalog.sqlite3"
        self.report = build_database(database=self.db)
        self.app = create_app({
            'PRESCRIPTIONS_DATABASE': str(Path(self.temp.name) / 'prescriptions.sqlite3'),
            'ACCOUNTS_DATABASE': str(Path(self.temp.name) / 'accounts.sqlite3'),
            'PENDING_MEDICINES_DATABASE': str(Path(self.temp.name) / 'pending.sqlite3'),
            'DISPENSING_UPLOAD_ROOT': str(Path(self.temp.name) / 'photos'),
        })
        self.app.config.update(TESTING=True, MEDICINES_DATABASE=str(self.db))
        self.client = self.app.test_client()

    def test_source_roundtrip_and_relationships(self):
        with (DATA / "doc/medicines.csv").open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(self.report["medicines"], len(rows))
        for original in rows:
            actual = search(self.db, filters={"pzn": original["pzn"]})["results"][0]
            for key, value in original.items():
                self.assertEqual(actual[key], value)
            self.assertEqual(len(actual["images"]), sum(bool(original[k]) for k in ("tablet_image_1", "tablet_image_2", "package_image")))
        db = connect(self.db)
        try:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            db.close()

    def test_search_api_and_image_bytes(self):
        result = self.client.get("/api/medicines?pzn=6865959").get_json()
        item = result["results"][0]
        self.assertEqual(item["pzn"], "06865959")
        self.assertEqual(len(item["images"]), 2)
        response = self.client.get(item["images"][0]["url"])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("image/"))
        self.assertEqual(response.data, (DATA / "images" / item["images"][0]["path"]).read_bytes())
        response.close()
        self.assertGreater(self.client.get("/api/medicines?q=BISOPROLOL").get_json()["total"], 0)
        self.assertGreater(self.client.get("/api/medicines?color=white&shape=round").get_json()["total"], 0)
        self.assertEqual(self.client.get("/api/medicines/06865959").status_code, 200)
        self.assertEqual(self.client.get("/api/medicines/99999999").status_code, 404)
        self.assertEqual(self.client.get("/api/medicines?limit=-1").status_code, 400)
        self.assertEqual(self.client.get("/api/medicines?invalid=value").status_code, 400)
        self.assertEqual(search(self.db, "' OR 1=1 --")["total"], 0)

    def test_query_is_ordered_prefix_of_only_name_ingredient_or_pzn(self):
        db = connect(self.db)
        try:
            with db:
                db.execute("""UPDATE medicines SET product_name='PrefixNameXYZ',
                    active_ingredient='IngredientStartXYZ', notes='NotesOnlyXYZ',
                    manufacturer='ManufacturerOnlyXYZ' WHERE pzn='06865959'""")
        finally:
            db.close()
        def matched(query):
            result = self.client.get('/api/medicines', query_string={'q': query, 'limit': 200}).get_json()
            return any(row['pzn'] == '06865959' for row in result['results'])
        for query in ('P', 'Pre', 'prefixname', 'IngredientStart', '06865'):
            self.assertTrue(matched(query), query)
        for query in ('NameXYZ', 'StartXYZ', 'PrefixXYZ', 'XYZName', '6865959',
                      'NotesOnlyXYZ', 'ManufacturerOnlyXYZ', '%', '_'):
            self.assertFalse(matched(query), query)

    def test_failed_import_preserves_database(self):
        old = self.db.read_bytes()
        with self.assertRaises(ValueError):
            build_database(image_root=Path(self.temp.name), database=self.db)
        self.assertEqual(self.db.read_bytes(), old)


if __name__ == "__main__":
    unittest.main()
