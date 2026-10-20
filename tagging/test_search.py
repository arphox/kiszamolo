"""Checks for search semantics, vocabulary loading and the CLI contract."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import search as search_module
from label import settings
from search import Catalog, HERE, parse_date


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tags = {"tags": [
            {"id": "broad", "name": "Tág", "synonyms": ["másik név"]},
            {"id": "middle", "name": "Közép", "broader": ["broad"]},
            {"id": "narrow", "name": "Szűk", "broader": ["middle"]},
            {"id": "other", "name": "Más"},
        ]}
        self.rows = [self.row(1, ["narrow", "other"], ["news", "advice"]),
                     self.row(2, ["broad"], ["advice"], "2020-01-02T00:00:00"),
                     self.row(3, [], [], "2020-01-03T00:00:00", "insufficient_content")]

    @staticmethod
    def row(article_id, tags, nature, published="2020-01-01T23:59:59", status="classified"):
        return {"id": article_id, "title": "Cím", "published_at": published,
                "tags": tags, "nature": nature, "path": "articles/example.md", "status": status}

    def catalog(self):
        return Catalog(self.tags, self.rows)

    def ids(self, **query):
        return [row["id"] for row in self.catalog().search(**query)["results"]]

    def test_transitive_search_and_no_upward_expansion(self):
        self.assertEqual(self.ids(tags=["Tág"]), [1, 2])
        self.assertEqual(self.ids(tags=["Szűk"]), [1])
        self.assertEqual(self.ids(tags=["Tág", "Más"]), [1])
        self.assertEqual(self.ids(tags=["Tág", "Tág"]), [1, 2])

    def test_aliases_case_whitespace_and_unicode(self):
        self.assertEqual(self.ids(tags=["  MÁSIK  név "]), [1, 2])
        self.assertEqual(self.ids(tags=["Ta\u0301g"]), [1, 2])
        self.assertEqual(self.ids(tags=["broad"]), [1, 2])

    def test_nature_and_inclusive_calendar_dates(self):
        self.assertEqual(self.ids(nature=["Hír vagy közlemény", "Tanács"]), [1])
        self.assertEqual(self.ids(tags=["Tág"], nature=["advice"],
                                  since="2020-01-01", until="2020-01-01"), [1])
        self.assertEqual(self.ids(since="2020-01-02", until="2020-01-02"), [2])

    def test_missing_content_is_visible_without_inferred_topics(self):
        self.assertEqual(self.ids(), [1, 2, 3])
        self.assertEqual(self.ids(status="insufficient_content"), [3])
        self.assertEqual(self.ids(tags=["Tág"], status="insufficient_content"), [])
        self.assertEqual(self.catalog().search()["insufficient_content_ids"], [3])

    def test_no_result_limit_and_machine_fields(self):
        self.rows = [self.row(i, ["narrow"], ["news"]) for i in range(150)]
        result = self.catalog().search(tags=["Tág"])
        self.assertEqual(result["count"], 150)
        self.assertEqual(len(result["results"]), 150)
        row = result["results"][0]
        self.assertEqual(row["tags"], [{"id": "narrow", "name": "Szűk"}])
        self.assertTrue(Path(row["local_path"]).is_absolute())
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_invalid_query_fails_instead_of_silently_ignoring_it(self):
        for query in ({"tags": ["unknown"]}, {"tags": [""]}, {"nature": ["unknown"]},
                      {"since": "2020-02-30"}, {"until": "20200101"},
                      {"since": "2020-02-01", "until": "2020-01-01"}, {"status": "unknown"}):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.catalog().search(**query)
        for value in ("2020-1-01", "2020-13-01", "2020-01-01T00:00", "", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_date(value)

    def test_invalid_parent_references_duplicates_self_and_cycles(self):
        for parent in ("absent", "broad", "narrow"):
            data = copy.deepcopy(self.tags)
            data["tags"][0]["broader"] = [parent]
            with self.subTest(parent=parent), self.assertRaises(ValueError):
                Catalog(data, self.rows)
        data = copy.deepcopy(self.tags)
        data["tags"][2]["broader"] = ["middle", "middle"]
        with self.assertRaises(ValueError):
            Catalog(data, self.rows)

    def test_multiple_parents_and_diamond_graph_are_valid(self):
        self.tags["tags"][3]["broader"] = ["broad"]
        self.tags["tags"][2]["broader"].append("other")
        self.assertEqual(self.ids(tags=["Tág"]), [1, 2])

    def test_alias_collision_is_rejected(self):
        self.tags["tags"][1]["synonyms"] = ["másik név"]
        with self.assertRaisesRegex(ValueError, "Többértelmű"):
            self.catalog()


class CatalogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "articles").mkdir()
        self.source = self.root / "articles" / "example.md"
        self.source.write_text("Article body.", encoding="utf-8")
        self.tags = {"tags": [
            {"id": "funds", "name": "Alapok", "rule": "Fund articles.", "synonyms": []},
            {"id": "etf", "name": "ETF", "rule": "ETF articles.", "synonyms": [], "broader": ["funds"]},
            {"id": "costs", "name": "Költségek", "rule": "Cost articles.", "synonyms": []},
        ]}
        self.tags_bytes = json.dumps(self.tags, ensure_ascii=False, indent=2).encode("utf-8")
        self.tags_path = self.root / "tags.json"
        self.tags_path.write_bytes(self.tags_bytes)
        tags_hash = hashlib.sha256(self.tags_bytes).hexdigest()
        settings_hash = settings(self.tags, tags_hash)["settings_sha256"]
        self.row = {"id": 1, "title": "Példa", "published_at": "2020-01-01T00:00:00",
                    "path": "articles/example.md", "markdown_sha256": hashlib.sha256(b"Article body.").hexdigest(),
                    "tags": ["etf", "costs"], "nature": ["explanation"], "status": "classified",
                    "settings_sha256": settings_hash}
        self.document = {"schema_version": 1, "scope": "fixture", "tags_sha256": tags_hash,
                         "articles": [self.row]}
        self.results_path = self.root / "results.json"
        self.results_path.write_text(json.dumps(self.document), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def load(self, results_path=None, overrides_path=None):
        with patch.object(search_module, "HERE", self.root), patch.object(search_module, "ROOT", self.root):
            return Catalog.load(results_path or self.results_path, overrides_path)

    def test_actual_vocabulary_has_expected_parent_links_and_keeps_costs(self):
        with (HERE / "tags.json").open(encoding="utf-8") as stream:
            tags = json.load(stream)
        catalog = Catalog(tags, [])
        self.assertIn("etf", catalog.expanded["investing"])
        self.assertIn("unit-linked", catalog.expanded["life-insurance"])
        self.assertIn("life-insurance", catalog.expanded["insurance"])
        self.assertIn("costs", catalog.tags)
        self.assertIn("mortgage-loans", catalog.expanded["debt"])

    def test_automatic_results_and_separate_manual_corrections(self):
        correction = {"tags_sha256": self.document["tags_sha256"], "articles": [{
            "id": 1, "markdown_sha256": self.row["markdown_sha256"],
            "tags": ["funds"], "nature": ["advice"], "reason": "Synthetic correction."}]}
        overrides = self.root / "overrides.json"
        overrides.write_text(json.dumps(correction), encoding="utf-8")
        original = self.results_path.read_bytes(), overrides.read_bytes()
        self.assertEqual(self.load().search(["ETF", "Költségek"])["count"], 1)
        catalog = self.load(overrides_path=overrides)
        self.assertEqual(catalog.scope, "fixture")
        self.assertEqual(catalog.search(["Alapok"])["count"], 1)
        self.assertEqual(catalog.search(["ETF", "Költségek"])["count"], 0)
        self.assertEqual(catalog.rows[0]["status"], "corrected")
        self.assertEqual((self.results_path.read_bytes(), overrides.read_bytes()), original)
        correction["articles"][0]["markdown_sha256"] = "changed"
        overrides.write_text(json.dumps(correction), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "más cikkszöveg"):
            self.load(overrides_path=overrides)

    def test_changed_source_or_settings_marks_saved_result_stale(self):
        self.source.write_text("Changed body.", encoding="utf-8")
        catalog = self.load()
        self.assertEqual(catalog.search()["unclassified_ids"], [1])
        self.assertEqual(catalog.search(["ETF"])["count"], 0)
        self.source.write_text("Article body.", encoding="utf-8")
        self.row["settings_sha256"] = "old settings"
        self.results_path.write_text(json.dumps(self.document), encoding="utf-8")
        self.assertEqual(self.load().search()["unclassified_ids"], [1])

    def test_old_tag_vocabulary_is_rejected(self):
        self.document["tags_sha256"] = "previous vocabulary"
        self.results_path.write_text(json.dumps(self.document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "régi fogalomtár"):
            self.load()

    def test_additive_catalog_keeps_old_rows_and_warns_for_unseen_tags(self):
        self.document["vocabulary_snapshot"] = {tag["id"]: tag for tag in self.tags["tags"]}
        self.row["labeling_sha256"] = settings(self.tags, self.document["tags_sha256"])["labeling_sha256"]
        self.row["evaluated_tag_ids"] = ["funds", "etf", "costs"]
        self.document["articles"] = [self.row]
        self.tags["tags"].append({"id": "shopping", "name": "Vásárlás", "rule": "Shopping", "synonyms": []})
        self.tags["tags"].append({"id": "new-fund-type", "name": "Új alaptípus", "rule": "New fund type",
                                   "synonyms": [], "broader": ["funds"]})
        self.tags["tags"].append({"id": "online-shopping", "name": "Online vásárlás", "rule": "Online shopping",
                                   "synonyms": [], "broader": ["shopping"]})
        new_bytes = json.dumps(self.tags, ensure_ascii=False, indent=2).encode("utf-8")
        self.tags_path.write_bytes(new_bytes)
        self.results_path.write_text(json.dumps(self.document), encoding="utf-8")
        catalog = self.load()
        self.assertEqual(catalog.search(["ETF", "Költségek"])["count"], 1)
        self.assertEqual(catalog.search(["Alapok"])["unevaluated_ids"], [1])
        self.assertEqual(catalog.search(["Online vásárlás"])["unevaluated_ids"], [1])

    def test_changed_or_removed_definition_is_rejected(self):
        self.document["vocabulary_snapshot"] = json.loads(json.dumps({tag["id"]: tag for tag in self.tags["tags"]}))
        self.tags["tags"][0]["rule"] = "Changed rule"
        self.tags_path.write_text(json.dumps(self.tags), encoding="utf-8")
        self.results_path.write_text(json.dumps(self.document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "módosított vagy eltávolított"):
            self.load()
        self.tags_path.write_bytes(self.tags_bytes)
        self.document["vocabulary_snapshot"] = json.loads(json.dumps({tag["id"]: tag for tag in self.tags["tags"]}))
        self.tags["tags"].pop()
        self.tags_path.write_text(json.dumps(self.tags), encoding="utf-8")
        self.results_path.write_text(json.dumps(self.document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "módosított vagy eltávolított"):
            self.load()

    def test_legacy_exact_catalog_backfills_but_unknown_legacy_hash_is_rejected(self):
        self.assertEqual(self.load().search(["ETF"])["count"], 1)
        self.assertEqual(self.load().rows[0]["evaluated_tag_ids"], ["costs", "etf", "funds"])
        self.document["tags_sha256"] = "unknown old vocabulary"
        self.results_path.write_text(json.dumps(self.document), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "régi fogalomtár"):
            self.load()

    def test_cli_errors_have_nonzero_exit_and_no_json(self):
        with patch.object(search_module, "HERE", self.root), patch.object(search_module, "ROOT", self.root):
            for args in (["nincs-ilyen"], ["--from", "2020-99-01"],
                         ["--nature", "nincs-ilyen"], ["--bad"]):
                output, errors = io.StringIO(), io.StringIO()
                with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                    with patch.object(sys, "argv", ["search.py", "--results", str(self.results_path), *args, "--json"]), \
                            redirect_stdout(output), redirect_stderr(errors):
                        search_module.main()
                self.assertEqual(error.exception.code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertNotIn("Traceback", errors.getvalue())

    def test_cli_human_output(self):
        output = io.StringIO()
        with patch.object(search_module, "HERE", self.root), patch.object(search_module, "ROOT", self.root), \
                patch.object(sys, "argv", ["search.py", "--results", str(self.results_path), "ETF"]), \
                redirect_stdout(output):
            search_module.main()
        self.assertIn("1 találat", output.getvalue())
        self.assertIn("Példa", output.getvalue())
        self.assertIn("Tagek: ETF; Költségek", output.getvalue())


if __name__ == "__main__":
    unittest.main()
