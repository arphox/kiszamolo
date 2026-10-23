"""Offline tests for content-nature assignment."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from classification import classify


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get("CLASSIFICATION_TEST_TMP"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "classification").mkdir()
        (self.root / "articles/2026/10").mkdir(parents=True)
        self.rules = Path(classify.ROOT / "classification/README.md").read_text(encoding="utf-8")
        (self.root / "classification/README.md").write_text(self.rules, encoding="utf-8")
        self.path = self.root / "classification/assignments.json"
        self.path.write_text('{\n  "articles": {}\n}\n', encoding="utf-8")
        self.rows = [dict(id=aid, type="post", title=f"Article {aid}", path=f"2026/10/{aid}.md")
                     for aid in (10, 20, 30)]
        self.write_index()
        for row in self.rows:
            (self.root / "articles" / row["path"]).write_text(
                f"# {row['title']}\n\nBODY_ONLY_FOR_{row['id']}", encoding="utf-8")
        self.response = {
            "content_nature": [{"id": "explanatory", "evidence": "A működést részletesen kifejti."}],
            "review_case": None,
        }
        self.transport = mock.Mock(return_value=self.response)

    def write_index(self):
        (self.root / "articles/index.jsonl").write_text(
            "\n".join(json.dumps(row) for row in self.rows) + "\n", encoding="utf-8")

    def test_missing_optional_source_metadata_does_not_block_readable_article(self):
        self.rows[0]["source_record"] = "records/posts/10"
        self.write_index()
        path = self.root / "articles" / self.rows[0]["path"]
        path.write_text("# Article 10\n\nReadable full article.\n\n![chart](chart.gif)", encoding="utf-8")
        (path.parent / "chart.gif").write_bytes(b"image-placeholder")
        prepared = classify.prepare_request(10, self.root)[3]
        self.assertIn("Readable full article.", prepared["prompt"])
        self.assertEqual(prepared["images"], [path.parent / "chart.gif"])

    def test_local_video_is_collected_for_frames_without_fetching_external_videos(self):
        path = self.root / "articles" / self.rows[0]["path"]
        video = path.parent / "clip.mp4"
        video.write_bytes(b"video-placeholder")
        path.write_text("# Article 10\n\n[clip](clip.mp4)\n[external](https://example.com/clip.mp4)", encoding="utf-8")
        request = classify.prepare_request(10, self.root)[3]
        self.assertEqual(request["videos"], [video])
        frame = path.parent / "frame.png"
        frame.write_bytes(b"frame-placeholder")
        with mock.patch.object(classify.media, "normalize_local_video", return_value=[frame]), \
                mock.patch.object(classify.topic_classifier, "codex_exec", return_value=self.response) as transport:
            classify.codex_exec(request)
        self.assertEqual(transport.call_args.args[0]["images"], [frame])
        self.assertIn("videójának időrendben", transport.call_args.args[0]["prompt"])

    def records(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["articles"]

    def add_articles(self, ids):
        for aid in ids:
            row = dict(id=aid, type="post", title=f"Article {aid}", path=f"2026/10/{aid}.md")
            self.rows.append(row)
            (self.root / "articles" / row["path"]).write_text(
                f"# {row['title']}\n\nBODY_ONLY_FOR_{row['id']}", encoding="utf-8")
        self.write_index()

    def pending_state(self, ids=(10, 20, 30)):
        records = {}
        for aid in ids:
            key = f"post:{aid}"
            records[key] = {
                "id": aid, "key": key, "type": "post", "title": f"Article {aid}",
                "path": f"2026/10/{aid}.md", "directory": f"records/posts/{aid}",
                "published_at": f"2026-10-{aid:02d}T00:00:00Z", "status": "publish",
                "acquisition_status": "complete", "classification_pending": True,
                "content_classification_pending": True,
            }
        path = self.root / "raw_source/content-mirror/state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"records": records}), encoding="utf-8")
        return path

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

    def test_prompt_instructions_keep_article_preparation(self):
        default_request = classify.make_request("Cím", "Törzsszöveg", [], "Szabályok")
        self.assertTrue(default_request["prompt"].startswith(classify.PROMPT_INSTRUCTIONS))
        self.assertIn("Törzsszöveg", default_request["prompt"])
        self.assertIn("Szabályok", default_request["prompt"])
        self.assertEqual(set(default_request["schema"]["properties"]), {"content_nature", "review_case"})

        _, _, _, prepared = classify.prepare_request(10, root=self.root)
        self.assertTrue(prepared["prompt"].startswith(classify.PROMPT_INSTRUCTIONS))
        self.assertIn("BODY_ONLY_FOR_10", prepared["prompt"])

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

    def test_assignment_save_guard_rejects_new_empty_nature_result(self):
        with self.assertRaises(classify.ClassificationError):
            classify.save_assignment(self.root, "10", {"content_nature": []}, {}, classify.rules_text(self.root))

    def test_legacy_empty_nature_record_remains_readable(self):
        legacy = {"content_nature": []}
        self.path.write_text(json.dumps({"articles": {"10": legacy}}), encoding="utf-8")
        self.assertEqual(classify.validate_assignments(json.loads(self.path.read_text())), {"10": legacy})

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
            existing = {"content_nature": []}
            self.path.write_text(json.dumps({"articles": {"20": existing}}), encoding="utf-8")
            return self.response
        classify.classify_article(10, root=self.root, transport=transport)
        self.assertEqual(set(self.records()), {"10", "20"})

    def test_same_article_change_during_model_call_prevents_save(self):
        def transport(request):
            current = {"content_nature": []}
            self.path.write_text(json.dumps({"articles": {"10": current}}), encoding="utf-8")
            return self.response
        with self.assertRaisesRegex(classify.ClassificationError, "közben megváltozott"):
            classify.classify_article(10, root=self.root, transport=transport)
        self.assertEqual(set(self.records()), {"10"})

    def test_rules_change_during_model_call_prevents_save(self):
        original = self.rules
        def transport(request):
            (self.root / "classification/README.md").write_text(
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

    def test_prepare_uses_accessible_body_despite_media_and_embed_metadata(self):
        row = self.rows[0]
        row.update({
            "url": "https://kiszamolo.hu/article/10",
            "acquisition_status": "incomplete",
            "featured_media_error": "featured image unavailable",
            "embed_urls": ["https://www.youtube.com/embed/abc"],
            "derived_view_available": True,
        })
        self.write_index()
        article = self.root / "articles" / row["path"]
        article.write_text(
            "# Article 10\n\nReadable article body. [PDF](files/guide.pdf) "
            "[sheet](files/data.xls) [video](https://youtu.be/abc)", encoding="utf-8")

        _, _, _, request = classify.prepare_request(10, root=self.root)

        self.assertIn("Readable article body", request["prompt"])
        self.assertIn("files/guide.pdf", request["prompt"])
        self.assertIn("files/data.xls", request["prompt"])
        self.assertIn("https://youtu.be/abc", request["prompt"])
        self.assertIn("https://www.youtube.com/embed/abc", request["prompt"])
        self.assertEqual(request["images"], [])

    def test_prepare_collects_local_gif_and_explicit_remote_inline_image(self):
        row = self.rows[0]
        row["url"] = "https://kiszamolo.hu/article/10"
        self.write_index()
        local = self.root / "articles/2026/10/local.gif"
        local.write_bytes(b"GIF placeholder; normalization is tested separately")
        article = self.root / "articles" / row["path"]
        article.write_text(
            "# Article 10\n\nBody. ![local](local.gif) "
            "![remote](https://images.example/picture.gif)", encoding="utf-8")

        _, _, _, request = classify.prepare_request(10, root=self.root)

        self.assertEqual(request["images"], [local])
        self.assertEqual(request["remote_images"], ["https://images.example/picture.gif"])
        self.transport.assert_not_called()

    def test_prepare_still_rejects_image_paths_outside_articles(self):
        article = self.root / "articles" / self.rows[0]["path"]
        article.write_text("# Article 10\n\nBody. ![escape](../../../outside.gif)", encoding="utf-8")
        with self.assertRaisesRegex(classify.ClassificationError, "kilép a cikkgyűjteményből"):
            classify.prepare_request(10, root=self.root)
        self.transport.assert_not_called()

    def test_codex_exec_normalizes_gif_and_keeps_pngs_alive_during_model_call(self):
        from PIL import Image

        gif_path = self.root / "articles/2026/10/animation.gif"
        first, second = Image.new("RGB", (3, 2), "red"), Image.new("RGB", (3, 2), "blue")
        first.save(gif_path, format="GIF", save_all=True, append_images=[second], duration=100)
        observed = []

        def inspect_prepared(request, **kwargs):
            observed.extend(request["images"])
            self.assertEqual(len(request["images"]), 2)
            for image_path in request["images"]:
                self.assertTrue(image_path.is_file())
                self.assertEqual(image_path.suffix, ".png")
                with Image.open(image_path) as image:
                    self.assertEqual(image.format, "PNG")
            self.assertEqual(len({image.parent for image in request["images"]}), 1)
            return self.response

        with mock.patch.object(classify.topic_classifier, "codex_exec", side_effect=inspect_prepared) as call:
            result = classify.codex_exec({"prompt": "body", "images": [gif_path]})

        self.assertIs(result, self.response)
        call.assert_called_once()
        self.assertEqual(len(observed), 2)

    def test_remote_media_failure_is_ignored_without_changing_model_request(self):
        expected_request = {"prompt": "accessible text", "images": [], "schema": {}}

        with mock.patch.object(classify.media, "fetch_remote_image",
                               side_effect=classify.media.MediaError("remote image unavailable")), \
                mock.patch.object(classify.topic_classifier, "codex_exec", return_value=self.response) as call:
            result = classify.codex_exec({**expected_request, "remote_images": ["https://img.example/a.gif"]})

        self.assertIs(result, self.response)
        call.assert_called_once()
        submitted = call.call_args.args[0]
        self.assertEqual(submitted["images"], [])
        self.assertEqual(submitted["prompt"], expected_request["prompt"])
        self.assertNotIn("missing", submitted["prompt"].casefold())

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
        prior = {"content_nature": []}
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
        prior = {"content_nature": []}
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

    def test_cli_acknowledges_only_saved_records_and_preserves_topic_flag(self):
        state_path = self.pending_state((10,))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(classify.main(["10"], root=self.root, transport=self.transport), 0)
        record = json.loads(state_path.read_text(encoding="utf-8"))["records"]["post:10"]
        self.assertNotIn("content_classification_pending", record)
        self.assertTrue(record["classification_pending"])
        published = json.loads((self.root / "articles/index.jsonl").read_text(encoding="utf-8"))
        self.assertNotIn("content_classification_pending", published)

    def test_ack_retry_republishes_index_after_first_publish_failure(self):
        state_path = self.pending_state((10,))
        original_publish = classify.publish_article_index
        publish_calls = 0

        def fail_first_publish(*args, **kwargs):
            nonlocal publish_calls
            publish_calls += 1
            if publish_calls == 1:
                raise OSError("index publish failed once")
            return original_publish(*args, **kwargs)

        with mock.patch.object(classify, "publish_article_index", side_effect=fail_first_publish):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = classify.main(["10"], root=self.root,
                                     transport=lambda _request: {
                                         "content_nature": [{"id": "reporting", "evidence": "Tényszerű eredményt ismertet."}], "review_case": None})
        self.assertEqual(code, 0)
        self.assertEqual(publish_calls, 2)
        state = json.loads(state_path.read_text(encoding="utf-8"))["records"]
        self.assertNotIn("content_classification_pending", state["post:10"])
        index = [json.loads(line) for line in (self.root / "articles/index.jsonl").read_text().splitlines()]
        self.assertNotIn("content_classification_pending", index[0])

    def test_cached_record_does_not_acknowledge_pending_source(self):
        state_path = self.pending_state((10,))
        saved, evidence = classify.validate_result(self.response)
        self.path.write_text(json.dumps({"articles": {"10": saved}}), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(classify.main(["10"], root=self.root, transport=self.transport), 0)
        record = json.loads(state_path.read_text(encoding="utf-8"))["records"]["post:10"]
        self.assertTrue(record["content_classification_pending"])
        self.transport.assert_not_called()

    def test_cli_returns_two_for_unavailable_required_content(self):
        self.rows[0]["derived_view_available"] = False
        self.write_index()
        before = self.path.read_bytes()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(classify.main(["10"], root=self.root, transport=self.transport), 2)
        self.transport.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
