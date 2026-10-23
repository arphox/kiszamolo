"""Offline tests for the unified assignment registry and independent decisions."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from classification import classify


class UnifiedClassifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "classification").mkdir()
        (self.root / "articles/2026/10").mkdir(parents=True)
        (self.root / "classification/README.md").write_text(
            (classify.ROOT / "classification/README.md").read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "classification/topics.json").write_bytes(
            (classify.ROOT / "classification/topics.json").read_bytes())
        self.assignments = self.root / "classification/assignments.json"
        self.assignments.write_text('{\n  "articles": {}\n}\n', encoding="utf-8")
        self.article = self.root / "articles/2026/10/10.md"
        self.article.write_text("# Cím\n\nA cikk érdemi szövege.", encoding="utf-8")
        row = {"id": 10, "type": "post", "title": "Cím", "path": "2026/10/10.md"}
        (self.root / "articles/index.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    def test_schema_accepts_legacy_empty_topics_and_partial_records(self):
        result = classify.validate_assignments({"articles": {
            "10": {"topics": []},
            "20": {"content_nature": ["news"]},
            "30": {"topics": ["costs"], "content_nature": ["reporting"]},
        }}, {"costs"})
        self.assertEqual(result["10"], {"topics": []})
        self.assertEqual(result["20"], {"content_nature": ["news"]})

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
        self.article.write_text("# Cím\n\nA cikk szövege.\n\n![ábra](https://example.com/chart.png)", encoding="utf-8")
        with mock.patch.object(classify.media, "fetch_remote_image", side_effect=classify.media.MediaError("offline")) as fetch:
            with classify.prepare_article(10, self.root) as prepared:
                topics_request = classify._dimension_request(prepared, "topics", self.root)
                nature_request = classify._dimension_request(prepared, "content_nature", self.root)
                self.assertIn("A cikk szövege", topics_request["prompt"])
                self.assertIn("A cikk szövege", nature_request["prompt"])
                self.assertIn("https://example.com/chart.png", topics_request["prompt"])
                self.assertIn("https://example.com/chart.png", nature_request["prompt"])
                self.assertEqual(prepared["images"], [])
            fetch.assert_called_once()

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
        rules_path = self.root / "classification/README.md"

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
        result = subprocess.run([sys.executable, "-B", "classification/classify.py", "--help"],
                                cwd=classify.ROOT, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dimension", result.stdout)

    def test_linked_local_image_uses_available_original_instead_of_thumbnail(self):
        thumbnail = self.root / "articles/2026/10/thumb.gif"
        original = self.root / "articles/2026/10/original.gif"
        thumbnail.write_bytes(b"thumb")
        original.write_bytes(b"original")
        self.article.write_text("# Cím\n\n[![ábra](thumb.gif)](original.gif)", encoding="utf-8")
        with mock.patch.object(classify.media, "normalize_local_image", side_effect=lambda _image, _dir: [original]), \
                mock.patch.object(classify.media, "compact_model_images", side_effect=lambda images, _dir: images):
            with classify.prepare_article(10, self.root) as prepared:
                self.assertIn(original, prepared["images"])
                self.assertNotIn(thumbnail, prepared["images"])
                self.assertFalse(any("thumb.gif" in note or "original.gif" in note
                                     for note in prepared["source_notes"]))

    def test_pending_failed_force_reclassification_retries_and_preserves_old_value(self):
        state_path = self.root / "raw_source/content-mirror/state.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text(json.dumps({"records": {"post:10": {
            "content_classification_pending": True}}}), encoding="utf-8")
        self.assignments.write_text(json.dumps({"articles": {"10": {"content_nature": ["news"]}}}), encoding="utf-8")
        failing = mock.Mock(side_effect=classify.ClassificationError("temporary error"))
        with self.assertRaises(classify.ClassificationError):
            classify.classify_article(10, self.root, failing, dimension="content_nature")
        self.assertEqual(classify.load_json(self.assignments)["articles"]["10"]["content_nature"], ["news"])
        succeeding = mock.Mock(return_value={"content_nature": [{"id": "reporting", "evidence": "A cikk eredményeket mutat."}], "review_case": None})
        result = classify.classify_article(10, self.root, succeeding, dimension="content_nature")
        self.assertTrue(result["saved"])
        succeeding.assert_called_once()

    def test_runner_failures_do_not_retry_for_either_dimension(self):
        for dimension in ("topics", "content_nature"):
            with self.subTest(dimension=dimension):
                runner = mock.Mock(side_effect=classify.ClassificationError("quota or execution failure"))
                with self.assertRaises(classify.ClassificationError):
                    classify.classify_article(10, self.root, runner, dimension=dimension)
                runner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
