"""Exercise the full update workflow without remote requests or model calls."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import update


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "raw_source/content-mirror"
        self.articles = self.root / "articles"
        self.source.mkdir(parents=True)
        self.articles.mkdir()
        (self.root / "topics").mkdir()
        (self.root / "classification").mkdir()
        (self.root / "classification/README.md").write_text(
            (Path(__file__).resolve().parent.parent / "classification/README.md").read_text(encoding="utf-8"),
            encoding="utf-8")
        self.records = {}
        self.assignments = {}
        self.content_assignments = {}
        self.write_json(self.root / "classification/assignments.json", {"articles": {}})
        self.write_json(self.root / "topics/topics.json", {"topics": [{"id": "topic"}, {"id": "old"}]})
        sync_patch = mock.patch.object(update.sync, "run_sync", return_value={"incomplete_records": 0})
        self.addCleanup(sync_patch.stop)
        self.sync_mock = sync_patch.start()
        self.codex_exec = update.classify.codex_exec
        model_patch = mock.patch.object(update.classify, "codex_exec", return_value={
            "topics": [{"id": "topic", "evidence": "The article covers this topic."}], "review_reason": None})
        self.addCleanup(model_patch.stop)
        self.model = model_patch.start()
        content_patch = mock.patch.object(update.content_classifier, "codex_exec", return_value={
            "content_nature": [{"id": "explanatory", "evidence": "The article explains a method."}],
            "review_case": None})
        self.addCleanup(content_patch.stop)
        self.content_model = content_patch.start()
        self.confirm = mock.Mock(return_value=True)
        self.output = []

    def write_json(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def add_post(self, aid, *, assigned=False, pending=False, incomplete=False,
                 content_assigned=False, content_pending=False):
        self.records[f"post:{aid}"] = dict(id=aid, key=f"post:{aid}", type="post", title=f"Title {aid}",
            directory=f"records/posts/{aid}", path=f"{aid}.md", status="active", published_at="2026-10-21",
            acquisition_status="incomplete" if incomplete else "complete", classification_pending=pending,
            content_classification_pending=content_pending)
        (self.articles / f"{aid}.md").write_text(f"# Title {aid}\n\nFull content for article {aid}.", encoding="utf-8")
        if assigned:
            self.assignments[str(aid)] = {"topics": ["old"]}
        if content_assigned:
            self.content_assignments[str(aid)] = {"content_nature": []}

    def prepare(self):
        state = {"schema_version": 1, "records": self.records, "assets": {}, "articles_root": str(self.articles)}
        self.write_json(self.source / "state.json", state)
        self.write_json(self.root / "topics/assignments.json", {"articles": self.assignments})
        self.write_json(self.root / "classification/assignments.json", {"articles": self.content_assignments})
        update.sync.publish_article_index(self.articles, state)

    def run_update(self):
        self.prepare()
        return update.run_update(self.root, progress=self.output.append, confirm=self.confirm)

    def stored_state(self):
        return json.loads((self.source / "state.json").read_text(encoding="utf-8"))

    def stored_assignments(self):
        return json.loads((self.root / "topics/assignments.json").read_text(encoding="utf-8"))["articles"]

    def stored_content(self):
        return json.loads((self.root / "classification/assignments.json").read_text(encoding="utf-8"))["articles"]

    def test_no_changes_use_no_ai_and_do_not_request_approval(self):
        self.add_post(1, assigned=True)
        report = self.run_update()
        self.model.assert_not_called()
        self.content_model.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(report["pending_article_ids"], [])
        self.assertEqual(self.stored_assignments(), self.assignments)

    def test_new_article_uses_independent_calls_and_stores(self):
        self.add_post(1, pending=True)
        report = self.run_update()
        self.model.assert_called_once()
        self.content_model.assert_called_once()
        self.assertEqual(self.stored_assignments()["1"], {"topics": ["topic"]})
        self.assertEqual(self.stored_content()["1"], {"content_nature": ["explanatory"]})
        self.assertEqual(report["content_classified"], [1])
        self.assertEqual(report["content_pending_article_ids"], [])

    def test_content_only_work_keeps_topics_untouched(self):
        self.add_post(1, assigned=True, content_pending=True)
        report = self.run_update()
        self.model.assert_not_called()
        self.content_model.assert_called_once()
        self.assertEqual(self.stored_assignments(), self.assignments)
        self.assertEqual(report["content_classified"], [1])

    def test_topic_review_does_not_block_content_classification(self):
        self.add_post(1, pending=True)
        self.model.return_value = {"topics": [], "review_reason": "A new topic is needed."}
        report = self.run_update()
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(report["content_pending_article_ids"], [])
        self.assertEqual(report["content_classified"], [1])
        self.assertNotIn("1", self.stored_assignments())
        self.assertIn("1", self.stored_content())

    def test_content_review_retries_without_repeating_successful_topics(self):
        self.add_post(1, pending=True)
        self.content_model.side_effect = update.classify.ReviewRequired("Missing required image.")
        first = self.run_update()
        self.assertEqual(first["pending_article_ids"], [])
        self.assertEqual(first["content_pending_article_ids"], [1])
        self.assertEqual(first["content_review_required"][0]["id"], 1)
        self.model.reset_mock()
        self.content_model.side_effect = None
        second = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.model.assert_not_called()
        self.assertEqual(second["content_classified"], [1])
        self.assertEqual(second["content_pending_article_ids"], [])
        self.assertEqual(self.stored_assignments()["1"], {"topics": ["topic"]})

    def test_evaluated_empty_content_is_not_unprocessed(self):
        self.add_post(1, assigned=True, content_assigned=True)
        report = self.run_update()
        self.model.assert_not_called()
        self.content_model.assert_not_called()
        self.assertEqual(self.stored_content(), self.content_assignments)

    def test_content_reclassification_error_preserves_old_record(self):
        self.add_post(1, assigned=True, content_assigned=True, content_pending=True)
        self.content_model.side_effect = update.classify.ClassificationError("Model failed.")
        report = self.run_update()
        self.assertEqual(report["content_errors"][0]["id"], 1)
        self.assertEqual(report["content_pending_article_ids"], [1])
        self.assertEqual(self.stored_content(), self.content_assignments)
        self.model.assert_not_called()

    def test_shared_reclassification_approval_counts_articles_once(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, pending=True, content_assigned=True, content_pending=True)
        report = self.run_update()
        self.confirm.assert_called_once_with(6)
        self.assertEqual(report["reclassified"], list(range(1, 7)))
        self.assertEqual(report["content_reclassified"], list(range(1, 7)))

    def test_interrupted_content_keeps_its_own_unfinished_work(self):
        self.add_post(1, assigned=True, content_pending=True)
        self.add_post(2, assigned=True, content_pending=True)
        self.content_model.side_effect = [self.content_model.return_value, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.run_update()
        records = self.stored_state()["records"]
        self.assertFalse(records["post:1"].get("content_classification_pending"))
        self.assertTrue(records["post:2"].get("content_classification_pending"))
        self.content_model.side_effect = None
        self.content_model.reset_mock()
        report = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.model.assert_not_called()
        self.content_model.assert_called_once()
        self.assertEqual(report["content_classified"], [2])

    def test_five_changed_articles_are_reclassified_automatically(self):
        for aid in range(1, 6):
            self.add_post(aid, assigned=True, pending=True)
        report = self.run_update()
        self.assertEqual(report["reclassified"], list(range(1, 6)))
        self.assertEqual(self.model.call_count, 5)
        self.confirm.assert_not_called()
        self.assertFalse(any(item.get("classification_pending") for item in self.stored_state()["records"].values()))
        index = [json.loads(line) for line in (self.articles / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertFalse(any(item.get("classification_pending") for item in index))

    def test_six_changed_articles_require_approval_after_new_articles_are_done(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, pending=True)
        self.add_post(7, pending=True)

        def approve(count):
            self.assertEqual(count, 6)
            self.assertEqual(self.stored_assignments()["7"]["topics"], ["topic"])
            self.assertEqual(self.model.call_count, 1)
            return True

        self.confirm.side_effect = approve
        report = self.run_update()
        self.confirm.assert_called_once_with(6)
        self.assertEqual(report["classified"], [7])
        self.assertEqual(report["reclassified"], list(range(1, 7)))
        self.assertEqual(report["pending_article_ids"], [])

    def test_refused_reclassification_survives_next_run_and_new_articles_are_always_classified(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, pending=True)
        # More than five new articles must never trigger the reclassification limit.
        for aid in range(7, 14):
            self.add_post(aid, pending=True)
        self.confirm.return_value = False
        report = self.run_update()
        self.assertEqual(report["classified"], list(range(7, 14)))
        self.assertEqual(report["reclassified"], [])
        self.assertEqual(report["pending_article_ids"], list(range(1, 7)))
        self.assertEqual(self.model.call_count, 7)
        self.assertEqual(self.stored_assignments()["1"], {"topics": ["old"]})
        self.confirm.return_value = True
        self.model.reset_mock()
        report = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(report["reclassified"], list(range(1, 7)))
        self.assertEqual(report["classified"], [])
        self.assertEqual(self.model.call_count, 6)

    def test_preexisting_unassigned_article_is_also_classified_without_pending_flag(self):
        self.add_post(1)
        report = self.run_update()
        self.assertEqual(report["classified"], [1])
        self.model.assert_called_once()

    def test_review_and_model_failure_preserve_old_topics_and_do_not_stop_other_articles(self):
        for aid in range(1, 4):
            self.add_post(aid, assigned=True, pending=True)
        self.model.side_effect = [dict(topics=[], review_reason="Need a human decision."),
                                  update.classify.ClassificationError("Model unavailable"), self.model.return_value]
        report = self.run_update()
        self.assertEqual(report["reclassified"], [3])
        self.assertEqual(report["pending_article_ids"], [1, 2])
        self.assertEqual(report["review_required"][0]["id"], 1)
        self.assertEqual(report["errors"][0]["id"], 2)
        self.assertEqual(self.stored_assignments()["1"], {"topics": ["old"]})
        self.assertEqual(self.stored_assignments()["2"], {"topics": ["old"]})
        self.assertTrue(any("Need a human decision" in line for line in self.output))

    def test_codex_error_details_reach_console_and_saved_report(self):
        self.add_post(1, assigned=True, pending=True)
        details = "ERROR: Session expired; please log in."
        self.model.side_effect = lambda request: self.codex_exec(request, executable="codex")
        process = mock.Mock(returncode=1, stdout="", stderr=details)
        with mock.patch.object(update.classify.subprocess, "run", return_value=process):
            report = self.run_update()
        saved = json.loads((self.articles / "update-report.json").read_text(encoding="utf-8"))
        self.assertIn(details, saved["errors"][0]["reason"])
        self.assertTrue(any(details in line for line in self.output))
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(self.stored_assignments()["1"], {"topics": ["old"]})

    def test_incomplete_article_never_reaches_model_but_complete_articles_continue(self):
        self.add_post(1, pending=True, incomplete=True)
        self.add_post(2, pending=True)
        self.sync_mock.return_value["incomplete_records"] = 1
        report = self.run_update()
        self.assertEqual(report["classified"], [2])
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(report["incomplete_downloads"], 1)
        self.model.assert_called_once()

    def test_interruption_keeps_only_unfinished_work_pending_and_releases_locks(self):
        self.add_post(1, assigned=True, pending=True)
        self.add_post(2, assigned=True, pending=True)
        self.model.side_effect = [self.model.return_value, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.run_update()
        self.assertFalse(self.stored_state()["records"]["post:1"].get("classification_pending"))
        self.assertTrue(self.stored_state()["records"]["post:2"]["classification_pending"])
        self.model.side_effect = None
        report = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(report["reclassified"], [2])

    def test_source_and_article_locks_block_sync_during_classification(self):
        self.add_post(1)
        response = self.model.return_value

        def model(request):
            for directory in (self.source, self.articles):
                with self.assertRaises(OSError), update.sync.exclusive_run(directory):
                    self.fail("A competing sync obtained the lock")
            return response

        self.model.side_effect = model
        self.run_update()

    def test_fatal_sync_failure_stops_before_ai_or_approval(self):
        self.sync_mock.side_effect = ValueError("Inventory failed")
        with self.assertRaisesRegex(ValueError, "Inventory failed"):
            update.run_update(self.root, confirm=self.confirm, progress=self.output.append)
        self.model.assert_not_called()
        self.confirm.assert_not_called()

    def test_cli_exit_codes(self):
        self.add_post(1)
        self.prepare()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(update.main([], root=self.root), 0)
            self.model.side_effect = KeyboardInterrupt()
            self.add_post(2)
            self.prepare()
            self.assertEqual(update.main([], root=self.root), 130)
            self.sync_mock.side_effect = ValueError("Failed")
            self.assertEqual(update.main([], root=self.root), 1)

    def test_cli_returns_two_when_work_is_pending(self):
        self.add_post(1, incomplete=True)
        self.prepare()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(update.main([], root=self.root), 2)
        self.model.assert_not_called()

    def test_cli_returns_two_for_content_only_failure(self):
        self.add_post(1, assigned=True, content_pending=True)
        self.prepare()
        self.content_model.side_effect = update.classify.ClassificationError("Model failed.")
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(update.main([], root=self.root), 2)
        self.model.assert_not_called()

    def test_unassigned_old_content_does_not_trigger_bulk_model_calls(self):
        self.add_post(1, assigned=True)
        self.prepare()
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(update.main([], root=self.root), 0)
        self.content_model.assert_not_called()
        report = json.loads((self.articles / "update-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report.get("content_pending_article_ids"), [])

    def test_real_sync_and_classification_integrate_without_network_or_ai(self):
        from test_sync import FakeClient, post
        from remote import Inventory

        # Start from an empty mirror and exercise the actual sync persistence.
        records = [post(1, body="<p>Full body.</p>")]
        client = FakeClient()

        def inventory(client, kind, **kwargs):
            fields = ("id", "type", "status", "modified", "modified_gmt", "date_gmt")
            return Inventory([{key: row[key] for key in fields} for row in records] if kind == "posts" else [], [])

        def details(client, kind, ids, **kwargs):
            return Inventory([row.copy() for row in records if row["id"] in ids] if kind == "posts" else [], [])

        def sync_runner(source_root, **kwargs):
            return REAL_SYNC(source_root, client=client, inventory_loader=inventory,
                             record_loader=details, metadata_loader=lambda *a, **k: Inventory([], []), **kwargs)

        self.write_json(self.root / "topics/assignments.json", {"articles": {}})
        self.sync_mock.side_effect = sync_runner
        first = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(first["classified"], [1])
        client.calls.clear()
        self.model.reset_mock()
        second = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(second["pending_article_ids"], [])
        self.model.assert_not_called()
        self.assertEqual(client.calls, [])
        records[0].update(modified="2026-10-21T10:00:00", modified_gmt="2026-10-21T10:00:00")
        third = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(third["reclassified"], [])
        self.model.assert_not_called()
        records[0].update(modified="2026-10-21T11:00:00", modified_gmt="2026-10-21T11:00:00",
                          content={"rendered": "<p>Changed body.</p>", "protected": False})
        fourth = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(fourth["reclassified"], [1])
        self.model.assert_called_once()
        self.confirm.assert_not_called()


REAL_SYNC = update.sync.run_sync


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
