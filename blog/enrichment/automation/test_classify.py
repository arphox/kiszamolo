"""Offline tests for content-nature assignment."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from blog.enrichment.automation import classify


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get("CLASSIFICATION_TEST_TMP"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "blog/enrichment").mkdir(parents=True)
        (self.root / "blog/content-mirror/articles/2026/10").mkdir(parents=True)
        self.rules = Path(classify.ROOT / "blog/enrichment/README.md").read_text(encoding="utf-8")
        (self.root / "blog/enrichment/README.md").write_text(self.rules, encoding="utf-8")
        (self.root / "blog/enrichment/topics.json").write_bytes(
            (classify.ROOT / "blog/enrichment/topics.json").read_bytes())
        self.path = self.root / "blog/enrichment/assignments.json"
        self.path.write_text('{\n  "articles": {}\n}\n', encoding="utf-8")
        self.rows = [dict(id=aid, type="post", title=f"Article {aid}", url=f"https://example.test/{aid}",
                          modified_at="2026-10-01T00:00:00", modified_at_gmt="2026-10-01T00:00:00",
                          path=f"2026/10/Article {aid} [{aid}].md")
                     for aid in (10, 20, 30)]
        for row in self.rows:
            self.write_body(row, f"# {row['title']}\n\nBODY_ONLY_FOR_{row['id']}")
        self.processing_path = self.root / "blog/enrichment/processing.json"
        self.processing_path.write_text('{"articles": {}}\n', encoding="utf-8")
        self.response = {
            "content_nature": [{"id": "explanatory", "evidence": "A működést részletesen kifejti."}],
            "review_case": None,
        }
        self.transport = mock.Mock(return_value=self.response)

    def write_body(self, row, body):
        classify.topic_classifier.catalog.write_article(
            self.root / "blog/content-mirror/articles" / row["path"], row, body)



    def records(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["articles"]

    def add_articles(self, ids):
        for aid in ids:
            row = dict(id=aid, type="post", title=f"Article {aid}", url=f"https://example.test/{aid}",
                       modified_at="2026-10-01T00:00:00", modified_at_gmt="2026-10-01T00:00:00",
                       path=f"2026/10/Article {aid} [{aid}].md")
            self.rows.append(row)
            self.write_body(row, f"# {row['title']}\n\nBODY_ONLY_FOR_{row['id']}")

    def write_processing(self, records):
        progress = {"articles": records}
        classify.processing_store.save_processing(self.root, progress)
        return progress

    def marker(self, row, body, *, modified_gmt=None):
        metadata = {**row}
        return {"modified_gmt": modified_gmt or row["modified_at_gmt"],
                "content_sha256": classify.processing_store.article_fingerprint(metadata, body)}

    def run_article(self, aid=10, *, force=False):
        return classify.classify_article(aid, root=self.root, transport=self.transport, force=force)

    def test_new_record_stores_only_nature_and_keeps_evidence_ephemeral(self):
        result = self.run_article()
        self.assertTrue(result["saved"])
        self.assertEqual(result["content_nature"], ["explanatory"])
        self.assertEqual(result["evidence"], {"explanatory": "A működést részletesen kifejti."})
        record = self.records()["10"]
        self.assertEqual(record, {"content_nature": ["explanatory"]})
        self.assertNotIn(b'"evidence"', self.path.read_bytes())
        request = self.transport.call_args.args[0]
        self.assertIn("BODY_ONLY_FOR_10", request["prompt"])
        self.assertNotIn("BODY_ONLY_FOR_20", request["prompt"])
        self.assertNotIn("quick-tip", request["prompt"])


    def test_new_final_result_requires_a_nature(self):
        self.transport.return_value = {"content_nature": [{"id": "reporting", "evidence": "Tényszerű eredményt ismertet."}], "review_case": None}
        result = self.run_article()
        self.assertEqual(result["content_nature"], ["reporting"])
        self.assertTrue(result["saved"])
        self.assertFalse(self.run_article()["saved"])
        self.transport.assert_called_once()

    def test_single_article_rejects_invalid_id_before_model_call(self):
        with self.assertRaises(classify.ClassificationError):
            classify.classify_article("0", root=self.root, transport=self.transport)
        self.transport.assert_not_called()

    def test_save_preserves_every_nature(self):
        self.transport.return_value = {
            "content_nature": [{"id": nature, "evidence": f"Evidence for {nature}."}
                               for nature in classify.NATURE_IDS],
            "review_case": None,
        }
        self.run_article()
        record = self.records()["10"]
        self.assertEqual(record, {"content_nature": sorted(classify.NATURE_IDS)})
        self.assertNotIn(b'"evidence"', self.path.read_bytes())

    def test_all_seven_natures_and_review_cases_have_explicit_contract(self):
        self.assertEqual(classify.NATURE_IDS, (
            "explanatory", "mindset", "news", "promotional", "announcement", "reporting", "curated"))
        for nature in classify.NATURE_IDS:
            with self.subTest(nature=nature):
                selected, evidence = classify.validate_result({
                    "content_nature": [{"id": nature, "evidence": "Konkrét tartalmi bizonyíték."}],
                    "review_case": None})
                self.assertEqual(selected["content_nature"], [nature])
                self.assertEqual(evidence[nature], "Konkrét tartalmi bizonyíték.")
        for case_type in ("taxonomy_unmatched", "source_unavailable"):
            with self.subTest(case_type=case_type), self.assertRaises(classify.ContentReviewRequired) as error:
                classify.validate_result({"content_nature": [], "review_case": {
                    "type": case_type, "reason": "Ellenőrizhető, konkrét ok."}})
            self.assertEqual(error.exception.review_case["type"], case_type)
        with self.assertRaises(classify.ClassificationError):
            classify.validate_result({"content_nature": [], "review_case": None})
        contradictory = {"content_nature": self.response["content_nature"],
                         "review_case": {"type": "taxonomy_unmatched", "reason": "Indok"}}
        with self.assertRaises(classify.ClassificationError):
            classify.validate_result(contradictory)
        malformed_id = {"content_nature": [{"id": [], "evidence": "Indok"}], "review_case": None}
        with self.assertRaises(classify.ClassificationError):
            classify.validate_result(malformed_id)
        malformed_case = {"content_nature": [], "review_case": {"type": [], "reason": "Indok"}}
        with self.assertRaises(classify.ClassificationError):
            classify.validate_result(malformed_case)


    def test_assignment_records_reject_unknown_fields(self):
        record = {"content_nature": ["reporting"], "unexpected": []}
        with self.assertRaises(classify.ClassificationError):
            classify.validate_assignments({"articles": {"10": record}})

    def test_force_replaces_record_and_review_leaves_existing_unchanged(self):
        self.run_article()
        before = self.path.read_bytes()
        self.transport.return_value = {"content_nature": [], "review_case": {
            "type": "source_unavailable", "reason": "Tartalom hiányzik."}}
        with self.assertRaises(classify.ReviewRequired):
            self.run_article(force=True)
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_nature_evidence_and_duplicate_nature_are_rejected(self):
        bad_responses = [
            {**self.response, "content_nature": [{"id": "news", "evidence": "  "}]},
            {**self.response, "content_nature": self.response["content_nature"] * 2},
        ]
        for bad in bad_responses:
            with self.subTest(bad=bad), self.assertRaises(classify.ClassificationError):
                classify.validate_result(bad)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{\n  "articles": {}\n}\n')

    def test_invalid_existing_file_is_preserved(self):
        self.path.write_text('{"articles":{"10":{"content_nature":["reporting","news"]}}}', encoding="utf-8")
        before = self.path.read_bytes()
        with self.assertRaises(classify.ClassificationError):
            self.run_article()
        self.assertEqual(self.path.read_bytes(), before)
        self.transport.assert_not_called()

    def test_concurrent_unrelated_assignment_is_preserved(self):
        def transport(request):
            existing = {"content_nature": ["reporting"]}
            self.path.write_text(json.dumps({"articles": {"20": existing}}), encoding="utf-8")
            return self.response
        classify.classify_article(10, root=self.root, transport=transport)
        self.assertEqual(set(self.records()), {"10", "20"})

    def test_same_article_change_during_model_call_prevents_save(self):
        def transport(request):
            current = {"content_nature": ["reporting"]}
            self.path.write_text(json.dumps({"articles": {"10": current}}), encoding="utf-8")
            return self.response
        with self.assertRaisesRegex(classify.ClassificationError, "közben megváltozott"):
            classify.classify_article(10, root=self.root, transport=transport)
        self.assertEqual(set(self.records()), {"10"})

    def test_rules_change_during_model_call_prevents_save(self):
        original = self.rules
        def transport(request):
            (self.root / "blog/enrichment/README.md").write_text(
                original.replace("A cikk legalább", "A cikk mindig", 1), encoding="utf-8")
            return self.response
        with self.assertRaisesRegex(classify.ClassificationError, "szabályzat"):
            classify.classify_article(10, root=self.root, transport=transport)
        self.assertEqual(self.records(), {})

    def test_custom_model_and_reasoning_are_passed_without_changing_topic_defaults(self):
        with mock.patch.object(classify, "codex_exec", return_value=self.response) as call:
            result = classify.classify_article(10, root=self.root, model="gpt-6-luna",
                                               reasoning_effort="high")
        self.assertTrue(result["saved"])
        self.assertEqual(call.call_args.kwargs, {"model": "gpt-6-luna", "reasoning_effort": "high"})






    def test_invalid_model_response_retries_once_but_review_does_not(self):
        invocations = []
        invalid = {"wrong": "shape"}

        def recover(request):
            invocations.append(request)
            return invalid if len(invocations) == 1 else self.response

        selected, _ = classify.evaluate_request({"prompt": "fresh call"}, recover)
        self.assertEqual(selected["content_nature"], ["explanatory"])
        self.assertEqual(invocations, [{"prompt": "fresh call"}, {"prompt": "fresh call"}])

        review_calls = []

        def request_review(request):
            review_calls.append(request)
            return {"content_nature": [], "review_case": {
                "type": "source_unavailable", "reason": "A cikk nagy része hiányzik."}}

        with self.assertRaises(classify.ReviewRequired):
            classify.evaluate_request({"prompt": "review"}, request_review)
        self.assertEqual(review_calls, [{"prompt": "review"}])

    def test_repeated_invalid_model_response_is_raised_after_one_retry(self):
        invocations = []

        def always_invalid(request):
            invocations.append(request)
            return {"wrong": "shape"}

        with self.assertRaises(classify.ClassificationError):
            classify.evaluate_request({"prompt": "invalid"}, always_invalid)
        self.assertEqual(invocations, [{"prompt": "invalid"}, {"prompt": "invalid"}])

    def test_assignment_replace_retries_permission_error_without_repeat_model_call(self):
        self.transport.return_value = {"content_nature": [{"id": "reporting", "evidence": "Tényszerű eredményt ismertet."}], "review_case": None}
        prior = {"content_nature": ["reporting"]}
        self.path.write_text(json.dumps({"articles": {"20": prior}}), encoding="utf-8")
        old = self.path.read_bytes()
        real_replace = os.replace
        replace_calls = 0

        def fail_twice_then_replace(source, target):
            nonlocal replace_calls
            replace_calls += 1
            if replace_calls <= 2:
                raise PermissionError("temporary file lock")
            return real_replace(source, target)

        with mock.patch.object(classify.os, "replace", side_effect=fail_twice_then_replace), \
                mock.patch.object(classify.time, "sleep") as sleep:
            result = self.run_article()
        self.assertTrue(result["saved"])
        self.assertEqual(replace_calls, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.1, 0.2])
        self.assertEqual(set(self.records()), {"10", "20"})
        self.transport.assert_called_once()
        self.assertNotEqual(self.path.read_bytes(), old)

    def test_permanent_assignment_replace_permission_error_is_bounded_and_preserves_file(self):
        self.transport.return_value = {"content_nature": [{"id": "reporting", "evidence": "Tényszerű eredményt ismertet."}], "review_case": None}
        prior = {"content_nature": ["reporting"]}
        self.path.write_text(json.dumps({"articles": {"20": prior}}), encoding="utf-8")
        old = self.path.read_bytes()
        with mock.patch.object(classify.os, "replace", side_effect=PermissionError("persistent lock")) as replace, \
                mock.patch.object(classify.time, "sleep") as sleep:
            with self.assertRaisesRegex(PermissionError, "persistent lock"):
                self.run_article()
        self.assertEqual(replace.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.1, 0.2, 0.4])
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(set(self.records()), {"20"})
        self.assertFalse(list(self.path.parent.glob(".classification-*.tmp")))
        self.transport.assert_called_once()

    def test_cli_returns_two_for_unavailable_required_content(self):
        self.rows[0]["derived_view_available"] = False
        self.write_body(self.rows[0], f"# {self.rows[0]['title']}\n\nBODY_ONLY_FOR_{self.rows[0]['id']}")
        before = self.path.read_bytes()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(classify.main(["10", "--dimension", "content_nature"], root=self.root, transport=self.transport), 2)
        self.transport.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_unchanged_processing_marker_skips_article_lookup_body_and_model(self):
        row = self.rows[0]
        body = f"# {row['title']}\n\nBODY_ONLY_FOR_{row['id']}"
        assignments = {"10": {"topics": ["costs"]}}
        progress = self.write_processing({"10": {"topics": self.marker(row, body)}})
        with mock.patch.object(classify.catalog, "find_article", side_effect=AssertionError("article lookup")), \
                mock.patch.object(classify.catalog, "read_article", side_effect=AssertionError("body read")):
            outcomes = list(classify.process_article("10", root=self.root, assignments=assignments,
                processing=progress, dimensions=("topics",), modified_gmt=row["modified_at_gmt"],
                transport=self.transport))
        self.assertEqual(outcomes, [])
        self.transport.assert_not_called()

    def test_timestamp_only_change_updates_marker_without_model_or_media_fetch(self):
        row = self.rows[0]
        body = f"# {row['title']}\n\nBODY_ONLY_FOR_{row['id']}"
        old, current = "2026-09-01T00:00:00", "2026-11-01T00:00:00"
        assignments = {"10": {"topics": ["costs"]}}
        progress = self.write_processing({"10": {"topics": self.marker(row, body, modified_gmt=old)}})
        with mock.patch.object(classify.media, "fetch_remote_image", side_effect=AssertionError("media fetch")):
            source = classify.reconcile_source("10", root=self.root, assignments=assignments,
                processing=progress, modified_gmt=current, dimensions=("topics",))
        self.assertEqual(source["content_sha256"], self.marker(row, body)["content_sha256"])
        self.assertEqual(progress["articles"]["10"]["topics"]["modified_gmt"], current)
        self.transport.assert_not_called()

    def test_failed_dimension_retries_without_repeating_successful_dimension(self):
        row = self.rows[0]
        body = f"# {row['title']}\n\nBODY_ONLY_FOR_{row['id']}"
        version, old = "2026-11-01T00:00:00", {"modified_gmt": "2026-09-01T00:00:00", "content_sha256": "0" * 64}
        assignments = {"10": {"topics": ["costs"], "content_nature": ["news"]}}
        progress = self.write_processing({"10": {"topics": old, "content_nature": old}})
        calls = []

        def first_run(request):
            dimension = "topics" if "topics" in request["schema"]["properties"] else "content_nature"
            calls.append(dimension)
            if dimension == "topics":
                return {"topics": [{"id": "costs", "evidence": "Költségeket tárgyal."}], "review_case": None}
            raise classify.ClassificationError("temporary nature failure")

        outcomes = list(classify.process_article("10", root=self.root, assignments=assignments,
            processing=progress, modified_gmt=version, transport=first_run))
        self.assertEqual(calls, ["topics", "content_nature"])
        self.assertIsInstance(outcomes[1][1], classify.ClassificationError)
        self.assertEqual(progress["articles"]["10"]["topics"]["modified_gmt"], version)
        self.assertEqual(progress["articles"]["10"]["content_nature"], old)

        def retry(request):
            calls.append("topics" if "topics" in request["schema"]["properties"] else "content_nature")
            return {"content_nature": [{"id": "reporting", "evidence": "Tényeket sorol fel."}], "review_case": None}

        list(classify.process_article("10", root=self.root, assignments=assignments,
            processing=progress, modified_gmt=version, transport=retry))
        self.assertEqual(calls, ["topics", "content_nature", "content_nature"])

    def test_processing_does_not_write_mirror_index_or_article_flags(self):
        row = self.rows[0]
        response = {"content_nature": [{"id": "news", "evidence": "Aktuális hírt közöl."}], "review_case": None}
        with redirect_stdout(io.StringIO()):
            self.assertEqual(classify.main(["10", "--dimension", "content_nature"], root=self.root,
                                           transport=mock.Mock(return_value=response)), 0)
        article_path = self.root / "blog/content-mirror/articles" / row["path"]
        metadata, _body = classify.catalog.read_article(article_path)
        self.assertNotIn("classification_pending", metadata)
        self.assertNotIn("content_classification_pending", metadata)
        self.assertFalse((self.root / classify.catalog.INDEX_PATH).exists())
        progress_before = self.processing_path.read_bytes()
        classify.catalog.rebuild_index(self.root)
        self.assertEqual(self.processing_path.read_bytes(), progress_before)


if __name__ == "__main__":
    unittest.main()
