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
        self.source = self.root / ".local-cache/blog/content-mirror"
        self.articles = self.root / "blog/content-mirror/articles"
        self.source.mkdir(parents=True)
        self.articles.mkdir(parents=True)
        (self.root / "blog/enrichment").mkdir(parents=True)
        self.records = {}
        self.assignments = {}
        self.write_json(self.root / "blog/enrichment/assignments.json", {"articles": {}})
        self.write_json(self.root / "blog/enrichment/topics.json", {"topics": [{"id": "topic"}, {"id": "old"}]})
        (self.root / "blog/enrichment/README.md").write_text("rules", encoding="utf-8")
        sync_patch = mock.patch.object(update.sync, "run_sync", return_value={"incomplete_records": 0})
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

    def add_post(self, aid, *, assigned=False, pending=False, incomplete=False,
                 content_assigned=False, content_pending=False, source_unavailable=False):
        self.records[f"post:{aid}"] = dict(id=aid, key=f"post:{aid}", type="post", title=f"Title {aid}",
            directory=f"records/posts/{aid}", path=f"{aid}.md", status="active", published_at="2026-10-21",
            acquisition_status="incomplete" if incomplete else "complete",
            derived_view_available=not source_unavailable,
            classification_pending=pending, content_classification_pending=content_pending)
        (self.articles / f"{aid}.md").write_text(f"# Title {aid}\n\nFull content for article {aid}.", encoding="utf-8")
        record = self.assignments.setdefault(str(aid), {})
        if assigned:
            record["topics"] = ["old"]
        if content_assigned:
            record["content_nature"] = []
        if not record:
            self.assignments.pop(str(aid), None)

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
        state = {"schema_version": 1, "records": self.records, "assets": {}, "articles_root": str(self.articles)}
        self.write_json(self.source / "state.json", state)
        self.write_json(self.root / "blog/enrichment/assignments.json", {"articles": self.assignments})
        update.sync.publish_article_index(self.articles, state)

    def run_update(self):
        self.prepare()
        return update.run_update(self.root, progress=self.output.append, confirm=self.confirm)

    def stored_state(self):
        return json.loads((self.source / "state.json").read_text(encoding="utf-8"))

    def stored_assignments(self):
        return json.loads((self.root / "blog/enrichment/assignments.json").read_text(encoding="utf-8"))["articles"]

    def test_no_changes_use_no_model_and_do_not_request_approval(self):
        self.add_post(1, assigned=True, content_assigned=True)
        report = self.run_update()
        self.model.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(report["pending_article_ids"], [])
        self.assertEqual(report["content_pending_article_ids"], [])

    def test_new_article_uses_two_dimension_calls_and_shared_preparation(self):
        self.add_post(1, pending=True)
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
        self.add_post(1, pending=True)
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

    def test_failed_reclassification_preserves_old_value_and_pending_flag(self):
        self.add_post(1, assigned=True, pending=True, content_assigned=True)
        self.model.side_effect = update.classify.ClassificationError("Model failed.")
        report = self.run_update()
        self.assertEqual(report["errors"][0]["id"], 1)
        self.assertEqual(self.stored_assignments()["1"]["topics"], ["old"])
        self.assertTrue(self.stored_state()["records"]["post:1"]["classification_pending"])
        self.assertFalse(self.stored_state()["records"]["post:1"].get("content_classification_pending"))

    def test_model_failure_details_reach_console_and_saved_report(self):
        self.add_post(1, assigned=True, pending=True, content_assigned=True, content_pending=True)
        details = "ERROR: Session expired; please log in."
        self.model.side_effect = update.classify.ClassificationError(details)
        report = self.run_update()
        saved = json.loads((self.articles / "update-report.json").read_text(encoding="utf-8"))
        self.assertIn(details, saved["errors"][0]["reason"])
        self.assertIn(details, saved["content_errors"][0]["reason"])
        self.assertTrue(any(details in line for line in self.output))
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(report["content_pending_article_ids"], [1])
        self.assertEqual(self.stored_assignments()["1"], {
            "topics": ["old"], "content_nature": []})

    def test_five_changed_article_union_is_automatic(self):
        for aid in range(1, 6):
            self.add_post(aid, assigned=True, pending=True, content_assigned=True, content_pending=True)
        report = self.run_update()
        self.confirm.assert_not_called()
        self.assertEqual(report["reclassified"], list(range(1, 6)))
        self.assertEqual(report["content_reclassified"], list(range(1, 6)))
        self.assertEqual(self.model.call_count, 10)

    def test_six_changed_union_asks_once_after_new_dimension_work(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, pending=True, content_assigned=True, content_pending=True)
        self.add_post(7, assigned=True, content_pending=True)

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
            self.add_post(aid, assigned=True, pending=aid <= 3,
                          content_assigned=True, content_pending=aid >= 4)

        def approve(count):
            self.assertEqual(count, 6)
            return True

        self.confirm.side_effect = approve
        report = self.run_update()
        self.confirm.assert_called_once_with(6)
        self.assertEqual(report["reclassified"], [1, 2, 3])
        self.assertEqual(report["content_reclassified"], [4, 5, 6])

    def test_declined_bulk_reclassification_preserves_both_values_and_pending_flags(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, pending=True,
                          content_assigned=True, content_pending=True)
        original = {str(aid): {"topics": ["old"], "content_nature": []}
                    for aid in range(1, 7)}
        self.confirm.return_value = False
        report = self.run_update()
        self.assertEqual(report["reclassification_approved"], False)
        self.assertEqual(report["reclassified"], [])
        self.assertEqual(report["content_reclassified"], [])
        self.assertEqual(self.model.call_count, 0)
        self.assertEqual(self.stored_assignments(), original)
        for record in self.stored_state()["records"].values():
            self.assertTrue(record["classification_pending"])
            self.assertTrue(record["content_classification_pending"])
        self.assertEqual(report["pending_article_ids"], list(range(1, 7)))
        self.assertEqual(report["content_pending_article_ids"], list(range(1, 7)))

    def test_preparation_failure_is_reported_for_each_requested_dimension(self):
        self.add_post(1, pending=True)
        self.prepare_mock.side_effect = update.classify.ReviewRequired("No usable source.")
        report = self.run_update()
        self.assertEqual(report["review_required"][0]["id"], 1)
        self.assertEqual(report["content_review_required"][0]["id"], 1)
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(report["content_pending_article_ids"], [1])

    def test_interrupted_work_persists_only_unfinished_dimension(self):
        self.add_post(1, assigned=True, pending=True, content_assigned=True, content_pending=True)
        def interrupt_after_topics(article_id, *, dimension, **kwargs):
            if dimension == "content_nature":
                raise KeyboardInterrupt()
            return self.classify(article_id, dimension=dimension, **kwargs)

        self.model.side_effect = interrupt_after_topics
        with self.assertRaises(KeyboardInterrupt):
            self.run_update()
        state = self.stored_state()["records"]["post:1"]
        self.assertFalse(state.get("classification_pending"))
        self.assertTrue(state.get("content_classification_pending"))
        index = [json.loads(line) for line in (self.articles / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertFalse(index[0].get("classification_pending"))
        self.assertTrue(index[0].get("content_classification_pending"))
        report = json.loads((self.articles / "update-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["pending_article_ids"], [])
        self.assertEqual(report["content_pending_article_ids"], [1])

    def test_unavailable_main_source_does_not_block_other_articles(self):
        self.add_post(1, pending=True, source_unavailable=True)
        self.add_post(2, pending=True)
        report = self.run_update()
        self.assertEqual(report["classified"], [2])
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(report["content_pending_article_ids"], [1])
        self.assertEqual(report["incomplete_downloads"], 0)

    def test_cli_partial_success_survives_interrupt_review_and_timeout(self):
        for error, code in ((KeyboardInterrupt(), 130), (update.classify.ReviewRequired("Review"), 2),
                            (update.classify.subprocess.TimeoutExpired("codex", 1), 1)):
            with self.subTest(error=type(error).__name__):
                self.add_post(1, pending=True)
                self.prepare()
                def classify_or_fail(aid, *, dimension, **kwargs):
                    if dimension == "content_nature":
                        raise error
                    return self.classify(aid, dimension=dimension)
                self.model.side_effect = classify_or_fail
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    self.assertEqual(update.classify.main(["1"], root=self.root), code)
                self.assertEqual(self.stored_assignments()["1"], {"topics": ["topic"]})
                record = self.stored_state()["records"]["post:1"]
                self.assertNotIn("classification_pending", record)
                self.assertTrue(record["content_classification_pending"])
                index = json.loads((self.articles / "index.jsonl").read_text(encoding="utf-8"))
                self.assertNotIn("classification_pending", index)
                self.assertTrue(index["content_classification_pending"])

    def test_state_write_failure_keeps_retry_flag_and_other_dimension_runs(self):
        self.add_post(1, pending=True)
        original_write = update.classify.atomic_write
        failed = False

        def fail_first_dimension_state_save(path, data):
            nonlocal failed
            if Path(path).name == "state.json" and not failed:
                failed = True
                raise OSError("State write failed.")
            return original_write(path, data)

        with mock.patch.object(update.classify, "atomic_write", side_effect=fail_first_dimension_state_save):
            report = self.run_update()

        self.assertEqual(report["errors"][0]["id"], 1)
        self.assertEqual(report["content_classified"], [1])
        state = self.stored_state()["records"]["post:1"]
        self.assertTrue(state["classification_pending"])
        self.assertFalse(state.get("content_classification_pending"))
        self.assertEqual(self.stored_assignments()["1"], {
            "topics": ["topic"], "content_nature": ["explanatory"]})
        self.model.reset_mock()
        retried = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(retried["reclassified"], [1])
        self.assertEqual(retried["content_reclassified"], [])
        self.assertFalse(self.stored_state()["records"]["post:1"].get("classification_pending"))

    def test_technical_failure_in_first_dimension_does_not_block_second(self):
        self.add_post(1, pending=True)

        def fail_topics(article_id, *, dimension, **kwargs):
            if dimension == "topics":
                raise update.classify.ClassificationError("Topic model failed.")
            return self.classify(article_id, dimension=dimension, **kwargs)

        self.model.side_effect = fail_topics
        report = self.run_update()
        self.assertEqual(report["errors"][0]["id"], 1)
        self.assertEqual(report["content_classified"], [1])
        self.assertEqual(self.stored_assignments()["1"], {"content_nature": ["explanatory"]})
        state = self.stored_state()["records"]["post:1"]
        self.assertTrue(state["classification_pending"])
        self.assertFalse(state.get("content_classification_pending"))

    def test_unified_classifier_persists_both_dimensions_in_real_workflow(self):
        self.add_post(1, pending=True)
        self.prepare()
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

    def test_real_sync_and_unified_assignment_integrate_without_network_or_ai(self):
        from test_sync import FakeClient, post
        from remote import Inventory

        records = [post(1, body="<p>Full body.</p>")]
        client = FakeClient()

        def inventory(client, kind, **kwargs):
            fields = ("id", "type", "status", "modified", "modified_gmt", "date_gmt")
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
        client.calls.clear()
        self.model.reset_mock()
        second = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(second["pending_article_ids"], [])
        self.assertEqual(second["content_pending_article_ids"], [])
        self.model.assert_not_called()
        self.assertEqual(client.calls, [])
        records[0].update(modified="2026-10-21T11:00:00", modified_gmt="2026-10-21T11:00:00",
                          content={"rendered": "<p>Changed body.</p>", "protected": False})
        third = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(third["reclassified"], [1])
        self.assertEqual(third["content_reclassified"], [1])
        self.model.assert_called()
        self.confirm.assert_not_called()

    def test_sync_locks_are_held_during_classification(self):
        self.add_post(1)

        def model(article_id, *, dimension, **kwargs):
            for directory in (self.source, self.articles):
                with self.assertRaises(OSError):
                    with update.sync.exclusive_run(directory):
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
        self.add_post(1, assigned=True, content_pending=True)
        self.model.side_effect = update.classify.ClassificationError("Model failed.")
        self.prepare()
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
