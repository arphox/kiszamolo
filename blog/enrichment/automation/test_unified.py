"""Offline tests for the unified assignment registry and independent decisions."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from blog.enrichment.automation import classify


class UnifiedClassifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "blog/enrichment").mkdir(parents=True)
        (self.root / "blog/content-mirror/articles/2026/10").mkdir(parents=True)
        (self.root / "blog/enrichment/README.md").write_text(
            (classify.ROOT / "blog/enrichment/README.md").read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "blog/enrichment/topics.json").write_bytes(
            (classify.ROOT / "blog/enrichment/topics.json").read_bytes())
        self.assignments = self.root / "blog/enrichment/assignments.json"
        self.assignments.write_text('{\n  "articles": {}\n}\n', encoding="utf-8")
        self.article = self.root / "blog/content-mirror/articles/2026/10/Cím [10].md"
        self.row = {"id": 10, "type": "post", "title": "Cím", "url": "https://example.test/cikk",
                    "modified_at": "2026-10-01T00:00:00", "modified_at_gmt": "2026-10-01T00:00:00",
                    "path": "2026/10/Cím [10].md"}
        self.write_article("# Cím\n\nA cikk érdemi szövege.")

    def write_article(self, body):
        classify.common.catalog.write_article(self.article, self.row, body)

    def test_schema_accepts_partial_records(self):
        result = classify.validate_assignments({"articles": {
            "10": {"topics": ["costs"]},
            "20": {"content_nature": ["news"]},
            "30": {"topics": ["costs"], "content_nature": ["reporting"]},
        }}, {"costs"})
        self.assertEqual(result["10"], {"topics": ["costs"]})
        self.assertEqual(result["20"], {"content_nature": ["news"]})

    def test_schema_rejects_empty_assignment_lists(self):
        for record in ({"topics": []}, {"content_nature": []},
                       {"topics": [], "content_nature": ["reporting"]},
                       {"topics": ["costs"], "content_nature": []}):
            with self.subTest(record=record), self.assertRaises(classify.ClassificationError):
                classify.validate_assignments({"articles": {"10": record}}, {"costs"})

    def test_dimension_save_preserves_other_dimension_and_evidence_is_ephemeral(self):
        initial = {"articles": {"10": {"topics": ["costs"]}}}
        self.assignments.write_text(json.dumps(initial), encoding="utf-8")
        response = {"content_nature": [{"id": "reporting", "evidence": "Érdemi adatokat közöl."}], "review_case": None}
        result = classify.classify_article(10, self.root, mock.Mock(return_value=response), dimension="content_nature")
        self.assertTrue(result["saved"])
        record = classify.load_json(self.assignments)["articles"]["10"]
        self.assertEqual(record, {"topics": ["costs"], "content_nature": ["reporting"]})
        self.assertNotIn(b"evidence", self.assignments.read_bytes())

    def test_topic_review_case_keeps_prior_result(self):
        self.assignments.write_text(json.dumps({"articles": {"10": {"topics": ["costs"]}}}), encoding="utf-8")
        response = {"topics": [], "review_case": {"type": "source_unavailable", "reason": "A fő kép hiányzik."}}
        with self.assertRaises(classify.ReviewRequired):
            classify.classify_article(10, self.root, mock.Mock(return_value=response), dimension="topics", force=True)
        self.assertEqual(classify.load_json(self.assignments)["articles"]["10"]["topics"], ["costs"])

    def test_prepared_input_is_reused_and_media_failures_are_disclosed(self):
        self.write_article("# Cím\n\nA cikk szövege.\n\n![ábra](https://example.com/chart.png)")
        with mock.patch.object(classify.media, "fetch_remote_image", side_effect=classify.media.MediaError("offline")) as fetch:
            with classify.prepare_article(10, self.root) as prepared:
                topics_request = classify._dimension_request(prepared, "topics")
                nature_request = classify._dimension_request(prepared, "content_nature")
                self.assertIn("A cikk szövege", topics_request["prompt"])
                self.assertIn("A cikk szövege", nature_request["prompt"])
                self.assertIn("https://example.com/chart.png", topics_request["prompt"])
                self.assertIn("https://example.com/chart.png", nature_request["prompt"])
                self.assertEqual(prepared["images"], [])
            fetch.assert_called_once()

    def test_remote_linked_original_is_processed_without_persistent_assets_or_state(self):
        self.write_article(
            "# Cím\n\nA cikk szövege.\n\n[![ábra](https://media.example/thumb.png)]"
            "(https://media.example/original.png)")
        _metadata, markdown = classify.common.catalog.read_article(self.article)
        remote, _notes = classify.discover_media(markdown)
        self.assertEqual(remote, ["https://media.example/original.png"])
        self.assertEqual(_notes, [])
        fetched = []
        with mock.patch.object(classify.media, "fetch_remote_image",
                               side_effect=lambda url, _directory: fetched.append(url) or []) as fetch:
            with classify.prepare_article(10, self.root) as prepared:
                self.assertEqual(prepared["images"], [])
            self.assertEqual(fetched, ["https://media.example/original.png"])
            fetch.assert_called_once()
        self.assertFalse((self.root / "blog/content-mirror/articles/assets").exists())
        self.assertFalse((self.root / ".local-cache/blog/content-mirror/state.json").exists())

    def test_partial_success_keeps_saved_dimension_when_other_dimension_needs_review(self):
        topic_response = {"topics": [{"id": "costs", "evidence": "Költségeket tárgyal."}], "review_case": None}
        nature_review = {"content_nature": [], "review_case": {
            "type": "taxonomy_unmatched", "reason": "A hozzáférhető cikk nem illik egyik jellegbe sem."}}
        with classify.prepare_article(10, self.root) as prepared:
            saved = classify.classify_article(10, self.root, mock.Mock(return_value=topic_response),
                                              dimension="topics", prepared=prepared)
            self.assertTrue(saved["saved"])
            with self.assertRaises(classify.ReviewRequired):
                classify.classify_article(10, self.root, mock.Mock(return_value=nature_review),
                                          dimension="content_nature", prepared=prepared)
        self.assertEqual(classify.load_json(self.assignments)["articles"]["10"], {"topics": ["costs"]})

    def test_model_call_rejects_rules_changed_during_decision(self):
        response = {"content_nature": [{"id": "news", "evidence": "Aktuális eseményt ismertet."}], "review_case": None}
        rules_path = self.root / "blog/enrichment/README.md"

        def change_rules(_request):
            current = rules_path.read_text(encoding="utf-8")
            rules_path.write_text(current.replace("Mindkét besorolás", "Changed. Mindkét besorolás", 1), encoding="utf-8")
            return response

        with self.assertRaisesRegex(classify.ClassificationError, "szabályzat"):
            classify.classify_article(10, self.root, change_rules, dimension="content_nature")
        self.assertEqual(classify.load_json(self.assignments)["articles"], {})

    def test_cli_default_runs_both_dimensions_from_one_prepared_article(self):
        calls = []

        def transport(request):
            calls.append(request)
            if "topics" in request["schema"]["properties"]:
                return {"topics": [{"id": "costs", "evidence": "Költségeket tárgyal."}], "review_case": None}
            return {"content_nature": [{"id": "reporting", "evidence": "Tényeket sorol fel."}], "review_case": None}

        from contextlib import redirect_stdout
        import io
        with redirect_stdout(io.StringIO()):
            self.assertEqual(classify.main(["10"], root=self.root, transport=transport), 0)
        record = classify.load_json(self.assignments)["articles"]["10"]
        self.assertEqual(record, {"topics": ["costs"], "content_nature": ["reporting"]})
        self.assertEqual(len(calls), 2)

    def test_standalone_entrypoint_shows_cli_help_without_model_access(self):
        result = subprocess.run([sys.executable, "-B", "blog/enrichment/automation/classify.py", "--help"],
                                cwd=classify.ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dimension", result.stdout)

    def test_local_image_reference_is_rejected_clearly(self):
        self.write_article("# Cím\n\n![ábra](assets/chart.png)")
        with self.assertRaisesRegex(classify.ClassificationError, r"csak eredeti HTTP\(S\) képeket"):
            with classify.prepare_article(10, self.root):
                pass

    def test_pending_failed_force_reclassification_retries_and_preserves_old_value(self):
        self.write_article("# Cím\n\nA cikk érdemi szövege.")
        self.assignments.write_text(json.dumps({"articles": {"10": {"content_nature": ["news"]}}}), encoding="utf-8")
        failing = mock.Mock(side_effect=classify.ClassificationError("temporary error"))
        with self.assertRaises(classify.ClassificationError):
            classify.classify_article(10, self.root, failing, dimension="content_nature", force=True)
        self.assertEqual(classify.load_json(self.assignments)["articles"]["10"]["content_nature"], ["news"])
        succeeding = mock.Mock(return_value={"content_nature": [{"id": "reporting", "evidence": "A cikk eredményeket mutat."}], "review_case": None})
        result = classify.classify_article(10, self.root, succeeding, dimension="content_nature", force=True)
        self.assertTrue(result["saved"])
        succeeding.assert_called_once()

    def test_local_video_reference_is_rejected_and_remote_media_is_disclosed(self):
        with self.assertRaisesRegex(classify.ClassificationError, r"csak eredeti HTTP\(S\) média-URL-eket"):
            classify.discover_media('[videó](clip.mp4)')
        for body in (
            '[![kép](../../assets/thumb.png)](https://media.example/original.png)',
            '<img src="chart.png" srcset="https://media.example/chart.png 2x">',
            '<video><source src="clip"></video>',
            '<video poster="poster.png"></video>',
            '<picture><source srcset="chart.png 1x, https://media.example/chart-large.png 2x"></picture>',
            '<source data-srcset="chart.png 1x, https://media.example/chart-large.png 2x">',
            '<source data-src="chart.png">',
            '<source src="clip">',
            '<source data-src="clip">',
            '[melléklet](file:///C:/Users/example/report.pdf)',
            '<a download href="report">letöltés</a>',
        ):
            with self.subTest(body=body), self.assertRaises(classify.ClassificationError):
                classify.discover_media(body)
        images, notes = classify.discover_media(
            '<video poster="https://media.example/poster.png" src="https://media.example/clip.mp4"></video>')
        self.assertEqual(images, ["https://media.example/poster.png"])
        self.assertEqual(notes, ["A hivatkozott videó vagy melléklet nincs feldolgozva: https://media.example/clip.mp4"])
        self.assertEqual(classify.discover_media(
            '<picture><source srcset="https://media.example/chart.png 2x"><img src="https://media.example/chart-small.png"></picture>'),
            (["https://media.example/chart.png"], []))
        self.assertEqual(classify.discover_media('<source src="https://media.example/file">'),
                         ([], ["A hivatkozott videó vagy melléklet nincs feldolgozva: https://media.example/file"]))
        self.assertEqual(classify.discover_media('<a download href="https://media.example/report">letöltés</a>'),
                         ([], ["A hivatkozott videó vagy melléklet nincs feldolgozva: https://media.example/report"]))
        self.assertEqual(classify.discover_media('[cikk](other.md) [rész](#resz)'), ([], []))

    def test_runner_failures_do_not_retry_for_either_dimension(self):
        for dimension in ("topics", "content_nature"):
            with self.subTest(dimension=dimension):
                runner = mock.Mock(side_effect=classify.ClassificationError("quota or execution failure"))
                with self.assertRaises(classify.ClassificationError):
                    classify.classify_article(10, self.root, runner, dimension=dimension)
                runner.assert_called_once()

    def test_invalid_response_retries_once_and_saves_for_either_dimension(self):
        for dimension, category in (("topics", "costs"), ("content_nature", "reporting")):
            for invalid in ({"wrong": "shape"}, json.JSONDecodeError("Invalid JSON", "", 0)):
                with self.subTest(dimension=dimension, invalid=invalid):
                    response = {dimension: [{"id": category, "evidence": "Konkrét bizonyíték."}],
                                "review_case": None}
                    runner = mock.Mock(side_effect=[invalid, response])
                    result = classify.classify_article(10, self.root, runner, dimension=dimension, force=True)
                    self.assertTrue(result["saved"])
                    self.assertEqual(runner.call_count, 2)
                    self.assertEqual(result["evidence"], {category: "Konkrét bizonyíték."})
                    self.assertEqual(classify.load_json(self.assignments)["articles"]["10"][dimension], [category])

    def test_invalid_results_are_rejected_without_saving_for_either_dimension(self):
        for dimension, category in (("topics", "costs"), ("content_nature", "reporting")):
            item = {"id": category, "evidence": "Konkrét bizonyíték."}
            invalid_results = [
                {"wrong": "shape"},
                {dimension: [], "review_case": None},
                {dimension: [item, item], "review_case": None},
                {dimension: [{"id": "unknown", "evidence": "Indok"}], "review_case": None},
                {dimension: [{"id": category, "evidence": "  "}], "review_case": None},
                {dimension: [item], "review_case": {"type": "source_unavailable", "reason": "Hiányzik."}},
                {dimension: [], "review_case": {"type": "unknown", "reason": "Indok"}},
            ]
            for response in invalid_results:
                with self.subTest(dimension=dimension, response=response):
                    before = self.assignments.read_bytes()
                    runner = mock.Mock(return_value=response)
                    with self.assertRaises(classify.ClassificationError):
                        classify.classify_article(10, self.root, runner, dimension=dimension)
                    self.assertEqual(runner.call_count, 2)
                    self.assertEqual(self.assignments.read_bytes(), before)

    def test_review_cases_do_not_retry_or_save_for_either_dimension(self):
        for dimension in ("topics", "content_nature"):
            for case_type in ("source_unavailable", "taxonomy_unmatched"):
                with self.subTest(dimension=dimension, case_type=case_type):
                    response = {dimension: [], "review_case": {"type": case_type, "reason": "Indok"}}
                    runner = mock.Mock(return_value=response)
                    before = self.assignments.read_bytes()
                    with self.assertRaises(classify.ContentReviewRequired) as error:
                        classify.classify_article(10, self.root, runner, dimension=dimension)
                    self.assertEqual(error.exception.review_case, response["review_case"])
                    runner.assert_called_once()
                    self.assertEqual(self.assignments.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
