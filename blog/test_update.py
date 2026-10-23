"""Exercise the full update workflow without remote requests or model calls."""

from contextlib import nullcontext, redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from blog import update

REAL_SYNC = update.sync.run_sync


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.articles = self.root / "blog/content-mirror/articles"
        self.articles.mkdir(parents=True)
        self.index_path = self.root / update.catalog.INDEX_PATH
        (self.root / "blog/enrichment").mkdir(parents=True)
        self.records = {}
        self.assignments = {}
        self.processing = {"articles": {}}
        self.write_json(self.root / "blog/enrichment/assignments.json", {"articles": {}})
        self.write_json(self.root / "blog/enrichment/topics.json", {"topics": [{"id": "topic"}, {"id": "old"}]})
        (self.root / "blog/enrichment/README.md").write_text("rules", encoding="utf-8")
        sync_patch = mock.patch.object(update.sync, "run_sync", return_value={"incomplete_records": 0, "post_ids": []})
        self.addCleanup(sync_patch.stop)
        self.sync_mock = sync_patch.start()
        self.model = mock.Mock(side_effect=self.classify)
        self.real_classify_article = update.classify.classify_article
        self.real_prepare_article = update.classify.prepare_article
        self.classify_patch = mock.patch.object(update.classify, "classify_article", self.model)
        self.addCleanup(self.classify_patch.stop)
        self.classify_patch.start()
        prep_patch = mock.patch.object(update.classify, "prepare_article", side_effect=self.prepare_article)
        self.addCleanup(prep_patch.stop)
        self.prepare_mock = prep_patch.start()
        self.confirm = mock.Mock(return_value=True)
        self.output = []

    def write_json(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def add_post(self, aid, *, assigned=False, topics_changed=False,
                 content_assigned=False, content_changed=False, source_unavailable=False):
        modified_gmt = "2026-10-21T08:00:00"
        self.records[f"post:{aid}"] = dict(id=aid, type="post", title=f"Title {aid}",
            url=f"https://kiszamolo.hu/p{aid}/", published_at="2026-10-21T10:00:00",
            modified_at="2026-10-21T10:00:00", modified_at_gmt=modified_gmt,
            derived_view_available=not source_unavailable)
        body = f"# Title {aid}\n\nFull content for article {aid}.\n"
        article_dir = self.articles / "2026" / "10"
        update.catalog.write_article(article_dir / f"2026-10-21 10.00 Title {aid} [{aid}].md",
                                     self.records[f"post:{aid}"], body)
        record = self.assignments.setdefault(str(aid), {})
        markers = self.processing["articles"].setdefault(str(aid), {})
        fingerprint = update.classify.processing_store.article_fingerprint(
            self.records[f"post:{aid}"], body)
        old_fingerprint = update.classify.processing_store.article_fingerprint(
            self.records[f"post:{aid}"], f"# Title {aid}\n\nOld content for article {aid}.\n")
        if assigned:
            record["topics"] = ["old"]
            markers["topics"] = {"modified_gmt": "2026-10-20T08:00:00" if topics_changed else modified_gmt,
                                  "content_sha256": old_fingerprint if topics_changed else fingerprint}
        if content_assigned:
            record["content_nature"] = ["reporting"]
            markers["content_nature"] = {
                "modified_gmt": "2026-10-20T08:00:00" if content_changed else modified_gmt,
                "content_sha256": old_fingerprint if content_changed else fingerprint}
        if not record:
            self.assignments.pop(str(aid), None)
            self.processing["articles"].pop(str(aid), None)

    def prepare_article(self, article_id, root=None):
        record = self.records.get(f"post:{article_id}", {})
        if record.get("derived_view_available") is False:
            raise update.classify.ReviewRequired("The main article source is unavailable.")
        return nullcontext({"article_id": str(article_id)})

    def classify(self, article_id, *, dimension, force=False, prepared=None, **kwargs):
        path = self.root / "blog/enrichment/assignments.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        record = data["articles"].setdefault(str(article_id), {})
        record[dimension] = ["topic"] if dimension == "topics" else ["explanatory"]
        path.write_text(json.dumps(data), encoding="utf-8")
        return {dimension: record[dimension], "saved": True}

    def prepare(self):
        self.write_json(self.root / "blog/enrichment/assignments.json", {"articles": self.assignments})
        update.catalog.save_index(self.root, {
            str(record["id"]): record["modified_at_gmt"] for record in self.records.values()})
        update.classify.processing_store.save_processing(self.root, self.processing)

    def run_update(self):
        self.prepare()
        if isinstance(self.sync_mock.return_value, dict):
            local_ids = [record["id"] for record in self.records.values() if record["type"] == "post"]
            if local_ids and not self.sync_mock.return_value.get("post_ids"):
                self.sync_mock.return_value["post_ids"] = local_ids
        return update.run_update(self.root, progress=self.output.append, confirm=self.confirm)

    def stored_state(self):
        return update.classify.processing_store.load_processing(self.root)

    def stored_assignments(self):
        return json.loads((self.root / "blog/enrichment/assignments.json").read_text(encoding="utf-8"))["articles"]

    def test_no_changes_use_no_model_and_do_not_request_approval(self):
        self.add_post(1, assigned=True, content_assigned=True)
        report = self.run_update()
        self.model.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(report["pending_article_ids"], [])
        self.assertEqual(report["content_pending_article_ids"], [])
        self.assertEqual(json.loads(self.index_path.read_text(encoding="utf-8")),
                         {"1": "2026-10-21T08:00:00"})
        self.assertTrue(update.catalog.find_article(self.root, 1).name.endswith("[1].md"))

    def test_download_failure_details_are_shown_without_report_file(self):
        self.sync_mock.return_value = {
            "incomplete_records": 1,
            "post_ids": [42],
            "errors": [{"record": "post:42", "error": "Timed out"}],
        }
        report = self.run_update()
        self.assertEqual(report["incomplete_downloads"], 1)
        self.assertTrue(any("post:42" in line and "Timed out" in line for line in self.output))
        self.assertFalse((self.articles / "update-report.json").exists())

    def test_new_article_uses_two_dimension_calls_and_shared_preparation(self):
        self.add_post(1)
        report = self.run_update()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.prepare_mock.call_count, 1)
        self.assertEqual(self.stored_assignments()["1"], {
            "topics": ["topic"], "content_nature": ["explanatory"]})
        self.assertEqual(report["classified"], [1])
        self.assertEqual(report["content_classified"], [1])
        self.assertEqual(report["content_pending_article_ids"], [])

    def test_missing_field_is_classified_even_if_other_dimension_exists(self):
        self.add_post(1, assigned=True)
        report = self.run_update()
        self.assertEqual(self.model.call_count, 1)
        self.model.assert_called_once()
        self.assertEqual(self.model.call_args.kwargs["dimension"], "content_nature")
        self.assertEqual(self.stored_assignments()["1"], {
            "topics": ["old"], "content_nature": ["explanatory"]})
        self.assertEqual(report["classified"], [])
        self.assertEqual(report["content_classified"], [1])

    def test_partial_failure_retries_only_missing_dimension_and_preserves_success(self):
        self.add_post(1)
        calls = []

        def model(article_id, *, dimension, **kwargs):
            calls.append(dimension)
            if dimension == "topics":
                raise update.classify.ReviewRequired("Needs topic review.")
            return self.classify(article_id, dimension=dimension, **kwargs)

        self.model.side_effect = model
        first = self.run_update()
        self.assertEqual(first["pending_article_ids"], [1])
        self.assertEqual(first["content_pending_article_ids"], [])
        self.assertEqual(self.stored_assignments()["1"], {"content_nature": ["explanatory"]})
        self.assertEqual(first["review_required"][0]["id"], 1)

        self.model.reset_mock()
        self.model.side_effect = self.classify
        self.prepare_mock.reset_mock()
        second = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(self.model.call_args.kwargs["dimension"], "topics")
        self.assertEqual(self.prepare_mock.call_count, 1)
        self.assertEqual(second["classified"], [1])
        self.assertEqual(second["content_classified"], [])

    def test_failed_reclassification_preserves_old_value_and_old_processing_marker(self):
        self.add_post(1, assigned=True, topics_changed=True, content_assigned=True)
        self.model.side_effect = update.classify.ClassificationError("Model failed.")
        report = self.run_update()
        self.assertEqual(report["errors"][0]["id"], 1)
        self.assertEqual(self.stored_assignments()["1"]["topics"], ["old"])
        processing = self.stored_state()["articles"]["1"]
        self.assertEqual(processing["topics"]["modified_gmt"], "2026-10-20T08:00:00")
        self.assertEqual(processing["content_nature"]["modified_gmt"], "2026-10-21T08:00:00")

    def test_model_failure_details_reach_console_without_report_files(self):
        self.add_post(1, assigned=True, topics_changed=True, content_assigned=True, content_changed=True)
        details = "ERROR: Session expired; please log in."
        self.model.side_effect = update.classify.ClassificationError(details)
        report = self.run_update()
        self.assertIn(details, report["errors"][0]["reason"])
        self.assertIn(details, report["content_errors"][0]["reason"])
        self.assertFalse((self.articles / "update-report.json").exists())
        self.assertTrue(any(details in line for line in self.output))
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(report["content_pending_article_ids"], [1])
        self.assertEqual(self.stored_assignments()["1"], {
            "topics": ["old"], "content_nature": ["reporting"]})

    def test_five_changed_article_union_is_automatic(self):
        for aid in range(1, 6):
            self.add_post(aid, assigned=True, topics_changed=True, content_assigned=True, content_changed=True)
        report = self.run_update()
        self.confirm.assert_not_called()
        self.assertEqual(report["reclassified"], list(range(1, 6)))
        self.assertEqual(report["content_reclassified"], list(range(1, 6)))
        self.assertEqual(self.model.call_count, 10)

    def test_six_changed_union_asks_once_after_new_dimension_work(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, topics_changed=True, content_assigned=True, content_changed=True)
        self.add_post(7, assigned=True)

        def approve(count):
            self.assertEqual(count, 6)
            self.assertEqual(self.stored_assignments()["7"]["content_nature"], ["explanatory"])
            self.assertEqual(self.model.call_count, 1)
            return True

        self.confirm.side_effect = approve
        report = self.run_update()
        self.confirm.assert_called_once_with(6)
        self.assertEqual(report["content_classified"], [7])
        self.assertEqual(report["reclassified"], list(range(1, 7)))
        self.assertEqual(report["content_reclassified"], list(range(1, 7)))

    def test_disjoint_changed_dimensions_count_each_article_once(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, topics_changed=aid <= 3,
                          content_assigned=True, content_changed=aid >= 4)

        def approve(count):
            self.assertEqual(count, 6)
            return True

        self.confirm.side_effect = approve
        report = self.run_update()
        self.confirm.assert_called_once_with(6)
        self.assertEqual(report["reclassified"], [1, 2, 3])
        self.assertEqual(report["content_reclassified"], [4, 5, 6])

    def test_declined_bulk_reclassification_preserves_values_and_processing_markers(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, topics_changed=True,
                          content_assigned=True, content_changed=True)
        original = {str(aid): {"topics": ["old"], "content_nature": ["reporting"]}
                    for aid in range(1, 7)}
        self.confirm.return_value = False
        report = self.run_update()
        self.assertEqual(report["reclassification_approved"], False)
        self.assertEqual(report["reclassified"], [])
        self.assertEqual(report["content_reclassified"], [])
        self.assertEqual(self.model.call_count, 0)
        self.assertEqual(self.stored_assignments(), original)
        for record in self.stored_state()["articles"].values():
            self.assertEqual(record["topics"]["modified_gmt"], "2026-10-20T08:00:00")
            self.assertEqual(record["content_nature"]["modified_gmt"], "2026-10-20T08:00:00")
        self.assertEqual(report["pending_article_ids"], list(range(1, 7)))
        self.assertEqual(report["content_pending_article_ids"], list(range(1, 7)))

    def test_preparation_failure_is_reported_for_each_requested_dimension(self):
        self.add_post(1)
        self.prepare_mock.side_effect = update.classify.ReviewRequired("No usable source.")
        report = self.run_update()
        self.assertEqual(report["review_required"][0]["id"], 1)
        self.assertEqual(report["content_review_required"][0]["id"], 1)
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(report["content_pending_article_ids"], [1])

    def test_interrupted_work_persists_only_unfinished_dimension(self):
        self.add_post(1, assigned=True, topics_changed=True, content_assigned=True, content_changed=True)
        def interrupt_after_topics(article_id, *, dimension, **kwargs):
            if dimension == "content_nature":
                raise KeyboardInterrupt()
            return self.classify(article_id, dimension=dimension, **kwargs)

        self.model.side_effect = interrupt_after_topics
        with self.assertRaises(KeyboardInterrupt):
            self.run_update()
        state = self.stored_state()["articles"]["1"]
        self.assertEqual(state["topics"]["modified_gmt"], "2026-10-21T08:00:00")
        self.assertEqual(state["content_nature"]["modified_gmt"], "2026-10-20T08:00:00")
        index = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.assertEqual(index, {"1": "2026-10-21T08:00:00"})
        self.index_path.unlink()
        rebuilt = update.catalog.load_index(self.root)
        self.assertEqual(rebuilt, {"1": "2026-10-21T08:00:00"})

    def test_unavailable_main_source_does_not_block_other_articles(self):
        self.add_post(1, source_unavailable=True)
        self.add_post(2)
        report = self.run_update()
        self.assertEqual(report["classified"], [2])
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(report["content_pending_article_ids"], [1])
        self.assertEqual(report["incomplete_downloads"], 0)

    def test_cli_partial_success_survives_interrupt_review_and_timeout(self):
        for error, code in ((KeyboardInterrupt(), 130), (update.classify.ReviewRequired("Review"), 2),
                            (update.classify.subprocess.TimeoutExpired("codex", 1), 1)):
            with self.subTest(error=type(error).__name__):
                self.add_post(1)
                self.prepare()
                self.sync_mock.return_value["post_ids"] = [1]
                def classify_or_fail(aid, *, dimension, **kwargs):
                    if dimension == "content_nature":
                        raise error
                    return self.classify(aid, dimension=dimension)
                self.model.side_effect = classify_or_fail
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(update.classify.main(["1"], root=self.root), code)
                self.assertEqual(self.stored_assignments()["1"], {"topics": ["topic"]})
                record = self.stored_state()["articles"]["1"]
                self.assertEqual(record["topics"]["modified_gmt"], "2026-10-21T08:00:00")
                self.assertNotIn("content_nature", record)
                index = json.loads(self.index_path.read_text(encoding="utf-8"))
                self.assertEqual(index, {"1": "2026-10-21T08:00:00"})

    def test_processing_write_failure_keeps_old_marker_and_other_dimension_runs(self):
        self.add_post(1)
        original_write = update.classify.processing_store.save_processing
        failed = False

        def fail_first_dimension_processing_save(root, processing):
            nonlocal failed
            if not failed and processing["articles"].get("1", {}).get("topics"):
                failed = True
                raise OSError("Processing write failed.")
            return original_write(root, processing)

        with mock.patch.object(update.classify.processing_store, "save_processing",
                               side_effect=fail_first_dimension_processing_save):
            report = self.run_update()

        self.assertEqual(report["errors"][0]["id"], 1)
        self.assertEqual(report["content_classified"], [1])
        state = self.stored_state()["articles"]["1"]
        self.assertNotIn("topics", state)
        self.assertEqual(state["content_nature"]["modified_gmt"], "2026-10-21T08:00:00")
        self.assertEqual(self.stored_assignments()["1"], {
            "topics": ["topic"], "content_nature": ["explanatory"]})
        self.model.reset_mock()
        retried = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(retried["reclassified"], [1])
        self.assertEqual(retried["content_reclassified"], [])
        self.assertEqual(self.stored_state()["articles"]["1"]["topics"]["modified_gmt"],
                         "2026-10-21T08:00:00")

    def test_technical_failure_in_first_dimension_does_not_block_second(self):
        self.add_post(1)

        def fail_topics(article_id, *, dimension, **kwargs):
            if dimension == "topics":
                raise update.classify.ClassificationError("Topic model failed.")
            return self.classify(article_id, dimension=dimension, **kwargs)

        self.model.side_effect = fail_topics
        report = self.run_update()
        self.assertEqual(report["errors"][0]["id"], 1)
        self.assertEqual(report["content_classified"], [1])
        self.assertEqual(self.stored_assignments()["1"], {"content_nature": ["explanatory"]})
        state = self.stored_state()["articles"]["1"]
        self.assertNotIn("topics", state)
        self.assertIn("content_nature", state)

    def test_unified_classifier_persists_both_dimensions_in_real_workflow(self):
        self.add_post(1)
        self.sync_mock.return_value["post_ids"] = [1]
        actual_rules = (Path(__file__).resolve().parent.parent / "blog/enrichment/README.md").read_text(
            encoding="utf-8")
        (self.root / "blog/enrichment/README.md").write_text(actual_rules, encoding="utf-8")
        self.write_json(self.root / "blog/enrichment/topics.json", {"topics": [
            {"id": "topic", "name": "Pénzügy", "rule": "Személyes pénzügyek érdemi tárgyalása.",
             "synonyms": [], "broader": []}]})

        def response(request, **kwargs):
            properties = request["schema"]["properties"]
            if "topics" in properties:
                return {"topics": [{"id": "topic", "evidence": "Full content for article 1."}],
                        "review_case": None}
            return {"content_nature": [{"id": "explanatory", "evidence": "The article explains a method."}],
                    "review_case": None}

        model = mock.Mock(side_effect=response)
        with mock.patch.object(update.classify, "classify_article", self.real_classify_article), \
                mock.patch.object(update.classify, "prepare_article", self.real_prepare_article), \
                mock.patch.object(update.classify, "codex_exec", model), \
                mock.patch.object(update.classify.topic_classifier, "codex_exec", model), \
                mock.patch.object(update.classify.media, "compact_model_images", return_value=[]):
            report = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)

        self.assertEqual(model.call_count, 2, report)
        self.assertEqual(self.stored_assignments()["1"], {
            "topics": ["topic"], "content_nature": ["explanatory"]})
        self.assertEqual(report["pending_article_ids"], [])
        self.assertEqual(report["content_pending_article_ids"], [])

    def test_wp_metadata_only_change_advances_processing_without_model_or_approval(self):
        self.add_post(1, assigned=True, content_assigned=True)
        self.prepare()
        record = self.records["post:1"]
        record["modified_at_gmt"] = "2026-10-22T08:00:00"
        article_path = update.catalog.find_article(self.root, 1)
        metadata, body = update.catalog.read_article(article_path)
        metadata["modified_at_gmt"] = "2026-10-22T08:00:00"
        update.catalog.write_article(article_path, metadata, body)
        update.catalog.save_index(self.root, {"1": "2026-10-22T08:00:00"})
        self.sync_mock.return_value["post_ids"] = [1]

        report = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)

        self.model.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(report["reclassified"], [])
        self.assertEqual(report["content_reclassified"], [])
        state = self.stored_state()["articles"]["1"]
        self.assertEqual(state["topics"]["modified_gmt"], "2026-10-22T08:00:00")
        self.assertEqual(state["content_nature"]["modified_gmt"], "2026-10-22T08:00:00")

    def test_real_sync_and_unified_assignment_integrate_without_network_or_ai(self):
        from test_sync import FakeClient, post
        from remote import Inventory

        records = [post(1, body="<p>Full body.</p>")]
        client = FakeClient()

        def inventory(client, kind, **kwargs):
            fields = ("id", "type", "status", "modified", "modified_gmt", "date_gmt", "link")
            rows = [{key: row[key] for key in fields} for row in records] if kind == "posts" else []
            return Inventory(rows, [])

        def details(client, kind, ids, **kwargs):
            rows = [row.copy() for row in records if row["id"] in ids] if kind == "posts" else []
            return Inventory(rows, [])

        def sync_runner(source_root, **kwargs):
            return REAL_SYNC(source_root, client=client, inventory_loader=inventory,
                             record_loader=details, metadata_loader=lambda *a, **k: Inventory([], []), **kwargs)

        self.sync_mock.side_effect = sync_runner
        first = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(first["classified"], [1])
        self.assertEqual(first["content_classified"], [1])
        self.assertEqual(self.stored_assignments()["1"], {
            "topics": ["topic"], "content_nature": ["explanatory"]})
        client.page_calls.clear()
        self.model.reset_mock()
        second = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(second["pending_article_ids"], [])
        self.assertEqual(second["content_pending_article_ids"], [])
        self.model.assert_not_called()
        self.assertEqual(client.page_calls, [])
        records[0].update(modified="2026-10-21T11:00:00", modified_gmt="2026-10-21T11:00:00",
                          content={"rendered": "<p>Changed body.</p>", "protected": False})
        third = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(third["reclassified"], [1])
        self.assertEqual(third["content_reclassified"], [1])
        self.model.assert_called()
        self.confirm.assert_not_called()

    def test_update_lock_is_held_during_classification(self):
        self.add_post(1)

        def model(article_id, *, dimension, **kwargs):
            with self.assertRaises(OSError):
                with update.catalog.exclusive_run(self.root):
                    pass
            return self.classify(article_id, dimension=dimension, **kwargs)

        self.model.side_effect = model
        self.run_update()

    def test_fatal_sync_failure_stops_before_classification_or_approval(self):
        self.sync_mock.side_effect = ValueError("Inventory failed")
        with self.assertRaisesRegex(ValueError, "Inventory failed"):
            update.run_update(self.root, confirm=self.confirm, progress=self.output.append)
        self.model.assert_not_called()
        self.confirm.assert_not_called()

    def test_cli_returns_two_for_dimension_failure(self):
        self.add_post(1, assigned=True, content_assigned=True, content_changed=True)
        self.model.side_effect = update.classify.ClassificationError("Model failed.")
        self.prepare()
        self.sync_mock.return_value["post_ids"] = [1]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(update.main([], root=self.root), 2)


class ApprovalTests(unittest.TestCase):
    def test_piped_yes_is_not_human_approval(self):
        with mock.patch.object(update.sys, "stdin", StringIO("igen\n")), \
                mock.patch("builtins.input") as prompt, redirect_stdout(StringIO()):
            self.assertFalse(update.confirm_reclassification(6))
        prompt.assert_not_called()

    def test_only_explicit_yes_from_interactive_terminal_approves(self):
        for answer, expected in (("igen", True), (" IGEN ", True), ("", False), ("y", False), ("nem", False)):
            with self.subTest(answer=answer), mock.patch.object(update.sys, "stdin") as terminal, \
                    mock.patch("builtins.input", return_value=answer), redirect_stdout(StringIO()):
                terminal.isatty.return_value = True
                self.assertEqual(update.confirm_reclassification(6), expected)

    def test_closed_interactive_input_declines(self):
        with mock.patch.object(update.sys, "stdin") as terminal, \
                mock.patch("builtins.input", side_effect=EOFError), redirect_stdout(StringIO()):
            terminal.isatty.return_value = True
            self.assertFalse(update.confirm_reclassification(6))


if __name__ == "__main__":
    unittest.main()
