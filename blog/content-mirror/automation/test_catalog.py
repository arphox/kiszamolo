"""Update-index data stays minimal; file identity comes from filenames."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import catalog


class CatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.metadata = {
            "id": 10, "type": "post", "title": "Árvíztűrő cikk",
            "url": "https://kiszamolo.hu/test/",
            "published_at": "2026-10-01T10:00:00",
            "modified_at": "2026-10-23T12:00:00",
            "modified_at_gmt": "2026-10-23T10:00:00",
        }
        self.relative = "2026/10/2026-10-01 10.00 Árvíztűrő cikk [10].md"
        self.path = catalog.article_path(self.root, self.relative)
        self.body = "# Árvíztűrő cikk\n\nA teljes cikk tartalma.\n"
        self.index = {"10": self.metadata["modified_at_gmt"]}

    def write(self):
        catalog.write_article(self.path, self.metadata, self.body)

    def test_index_has_only_ids_and_exact_wordpress_utc_values(self):
        self.write()
        catalog.save_index(self.root, self.index)
        self.assertEqual(json.loads((self.root / catalog.INDEX_PATH).read_text(encoding="utf-8")), self.index)
        header, body = catalog.read_article(self.path)
        self.assertEqual(header["modified_at_gmt"], catalog.load_index(self.root)["10"])
        self.assertEqual(body, self.body)

    def test_existing_index_does_not_open_articles(self):
        catalog.save_index(self.root, self.index)
        with mock.patch.object(catalog, "read_article", side_effect=AssertionError("Article opened")), \
                mock.patch.object(catalog, "read_metadata", side_effect=AssertionError("Header opened")):
            self.assertEqual(catalog.load_index(self.root), self.index)

    def test_file_lookup_uses_ids_without_opening_headers(self):
        self.write()
        with mock.patch.object(catalog, "read_metadata", side_effect=AssertionError("Header opened")):
            self.assertEqual(catalog.find_article(self.root, 10), self.path)
            self.assertEqual(catalog.list_article_paths(self.root), {"10": self.path})

    def test_index_is_formatted_and_ordered_by_numeric_id(self):
        index = {"10": self.index["10"], "2": "2026-10-23T09:00:00"}
        catalog.save_index(self.root, index)
        path = self.root / catalog.INDEX_PATH
        content = path.read_text(encoding="utf-8")
        self.assertEqual(list(json.loads(content)), ["2", "10"])
        self.assertIn('\n  "10": "2026-10-23T10:00:00"', content)
        before = path.read_bytes()
        catalog.save_index(self.root, dict(reversed(list(index.items()))))
        self.assertEqual(path.read_bytes(), before)

    def test_missing_index_rebuilds_from_headers_only(self):
        self.write()
        with mock.patch.object(catalog, "read_article", side_effect=AssertionError("Body loaded")):
            self.assertEqual(catalog.load_index(self.root), self.index)
        self.assertEqual(catalog.INDEX_PATH.parent, catalog.ARTICLES_PATH)

    def test_rebuild_reproduces_exact_index_bytes(self):
        self.write()
        catalog.save_index(self.root, self.index)
        path = self.root / catalog.INDEX_PATH
        before = path.read_bytes()
        path.unlink()
        catalog.rebuild_index(self.root)
        self.assertEqual(path.read_bytes(), before)

    def test_rebuild_rejects_header_and_filename_identity_mismatch(self):
        self.write()
        wrong = self.path.with_name("Wrong ID [20].md")
        catalog.write_article(wrong, self.metadata, self.body)
        with self.assertRaisesRegex(ValueError, "IDs differ"):
            catalog.rebuild_index(self.root)

    def test_duplicate_filename_ids_are_rejected(self):
        self.write()
        catalog.write_article(self.path.with_name("Duplicate [10].md"), self.metadata, self.body)
        with self.assertRaisesRegex(ValueError, "Multiple article files"):
            catalog.list_article_paths(self.root)

    def test_update_index_rejects_metadata_objects_and_invalid_ids(self):
        for index in ({"10": self.metadata}, {"post:10": self.index["10"]}, {"10": "invalid"}):
            with self.subTest(index=index), self.assertRaises(ValueError):
                catalog.save_index(self.root, index)

    def test_article_paths_cannot_escape_collection(self):
        for relative in ("../outside.md", "C:/outside.md", "/outside.md"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                catalog.article_path(self.root, relative)

    def test_invalid_index_is_not_silently_overwritten(self):
        self.write()
        index = self.root / catalog.INDEX_PATH
        index.write_text("broken\n", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            catalog.load_index(self.root)
        self.assertEqual(index.read_text(encoding="utf-8"), "broken\n")


if __name__ == "__main__":
    unittest.main()
