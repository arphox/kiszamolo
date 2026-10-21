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
        (self.root / "tagging").mkdir()
        self.records = {}
        self.assignments = {}
        self.write_json(self.root / "tagging/tags.json", {"tags": [{"id": "topic"}, {"id": "old"}]})
        sync_patch = mock.patch.object(update.sync, "run_sync", return_value={"incomplete_records": 0})
        self.addCleanup(sync_patch.stop)
        self.sync_mock = sync_patch.start()
        model_patch = mock.patch.object(update.label, "codex_exec", return_value={
            "tags": [{"id": "topic", "evidence": "The article covers this topic."}], "review_reason": None})
        self.addCleanup(model_patch.stop)
        self.model = model_patch.start()
        self.confirm = mock.Mock(return_value=True)
        self.output = []

    def write_json(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def add_post(self, aid, *, assigned=False, pending=False, incomplete=False):
        self.records[f"post:{aid}"] = dict(id=aid, key=f"post:{aid}", type="post", title=f"Title {aid}",
            directory=f"records/posts/{aid}", path=f"{aid}.md", status="active", published_at="2026-10-21",
            acquisition_status="incomplete" if incomplete else "complete", tagging_pending=pending)
        (self.articles / f"{aid}.md").write_text(f"# Title {aid}\n\nFull content for article {aid}.", encoding="utf-8")
        if assigned:
            self.assignments[str(aid)] = {"tags": ["old"]}

    def prepare(self):
        state = {"schema_version": 1, "records": self.records, "assets": {}, "articles_root": str(self.articles)}
        self.write_json(self.source / "state.json", state)
        self.write_json(self.root / "tagging/assignments.json", {"articles": self.assignments})
        update.sync.publish_article_index(self.articles, state)

    def run_update(self):
        self.prepare()
        return update.run_update(self.root, progress=self.output.append, confirm=self.confirm)

    def stored_state(self):
        return json.loads((self.source / "state.json").read_text(encoding="utf-8"))

    def stored_assignments(self):
        return json.loads((self.root / "tagging/assignments.json").read_text(encoding="utf-8"))["articles"]

    def test_no_changes_use_no_ai_and_do_not_request_approval(self):
        self.add_post(1, assigned=True)
        report = self.run_update()
        self.model.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(report["pending_article_ids"], [])
        self.assertEqual(self.stored_assignments(), self.assignments)

    def test_five_changed_articles_are_retagged_automatically(self):
        for aid in range(1, 6):
            self.add_post(aid, assigned=True, pending=True)
        report = self.run_update()
        self.assertEqual(report["retagged"], list(range(1, 6)))
        self.assertEqual(self.model.call_count, 5)
        self.confirm.assert_not_called()
        self.assertFalse(any(item.get("tagging_pending") for item in self.stored_state()["records"].values()))
        index = [json.loads(line) for line in (self.articles / "index.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertFalse(any(item.get("tagging_pending") for item in index))

    def test_six_changed_articles_require_approval_after_new_articles_are_done(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, pending=True)
        self.add_post(7, pending=True)

        def approve(count):
            self.assertEqual(count, 6)
            self.assertEqual(self.stored_assignments()["7"]["tags"], ["topic"])
            self.assertEqual(self.model.call_count, 1)
            return True

        self.confirm.side_effect = approve
        report = self.run_update()
        self.confirm.assert_called_once_with(6)
        self.assertEqual(report["labeled"], [7])
        self.assertEqual(report["retagged"], list(range(1, 7)))
        self.assertEqual(report["pending_article_ids"], [])

    def test_refused_retagging_survives_next_run_and_new_articles_are_always_labeled(self):
        for aid in range(1, 7):
            self.add_post(aid, assigned=True, pending=True)
        # More than five new articles must never trigger the retagging limit.
        for aid in range(7, 14):
            self.add_post(aid, pending=True)
        self.confirm.return_value = False
        report = self.run_update()
        self.assertEqual(report["labeled"], list(range(7, 14)))
        self.assertEqual(report["retagged"], [])
        self.assertEqual(report["pending_article_ids"], list(range(1, 7)))
        self.assertEqual(self.model.call_count, 7)
        self.assertEqual(self.stored_assignments()["1"], {"tags": ["old"]})
        self.confirm.return_value = True
        self.model.reset_mock()
        report = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(report["retagged"], list(range(1, 7)))
        self.assertEqual(report["labeled"], [])
        self.assertEqual(self.model.call_count, 6)

    def test_preexisting_unassigned_article_is_also_labeled_without_pending_flag(self):
        self.add_post(1)
        report = self.run_update()
        self.assertEqual(report["labeled"], [1])
        self.model.assert_called_once()

    def test_review_and_model_failure_preserve_old_tags_and_do_not_stop_other_articles(self):
        for aid in range(1, 4):
            self.add_post(aid, assigned=True, pending=True)
        self.model.side_effect = [dict(tags=[], review_reason="Need a human decision."),
                                  update.label.LabelError("Model unavailable"), self.model.return_value]
        report = self.run_update()
        self.assertEqual(report["retagged"], [3])
        self.assertEqual(report["pending_article_ids"], [1, 2])
        self.assertEqual(report["review_required"][0]["id"], 1)
        self.assertEqual(report["errors"][0]["id"], 2)
        self.assertEqual(self.stored_assignments()["1"], {"tags": ["old"]})
        self.assertEqual(self.stored_assignments()["2"], {"tags": ["old"]})
        self.assertTrue(any("Need a human decision" in line for line in self.output))

    def test_incomplete_article_never_reaches_model_but_complete_articles_continue(self):
        self.add_post(1, pending=True, incomplete=True)
        self.add_post(2, pending=True)
        self.sync_mock.return_value["incomplete_records"] = 1
        report = self.run_update()
        self.assertEqual(report["labeled"], [2])
        self.assertEqual(report["pending_article_ids"], [1])
        self.assertEqual(report["incomplete_downloads"], 1)
        self.model.assert_called_once()

    def test_interruption_keeps_only_unfinished_work_pending_and_releases_locks(self):
        self.add_post(1, assigned=True, pending=True)
        self.add_post(2, assigned=True, pending=True)
        self.model.side_effect = [self.model.return_value, KeyboardInterrupt()]
        with self.assertRaises(KeyboardInterrupt):
            self.run_update()
        self.assertFalse(self.stored_state()["records"]["post:1"].get("tagging_pending"))
        self.assertTrue(self.stored_state()["records"]["post:2"]["tagging_pending"])
        self.model.side_effect = None
        report = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(report["retagged"], [2])

    def test_source_and_article_locks_block_sync_during_labeling(self):
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

    def test_real_sync_and_labeling_integrate_without_network_or_ai(self):
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

        self.write_json(self.root / "tagging/assignments.json", {"articles": {}})
        self.sync_mock.side_effect = sync_runner
        first = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(first["labeled"], [1])
        client.calls.clear()
        self.model.reset_mock()
        second = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(second["pending_article_ids"], [])
        self.model.assert_not_called()
        self.assertEqual(client.calls, [])
        records[0].update(modified="2026-10-21T10:00:00", modified_gmt="2026-10-21T10:00:00")
        third = update.run_update(self.root, progress=self.output.append, confirm=self.confirm)
        self.assertEqual(third["retagged"], [1])
        self.model.assert_called_once()
        self.confirm.assert_not_called()


REAL_SYNC = update.sync.run_sync


class ApprovalTests(unittest.TestCase):
    def test_piped_yes_is_not_human_approval(self):
        with mock.patch.object(update.sys, "stdin", StringIO("igen\n")), \
                mock.patch("builtins.input") as prompt, redirect_stdout(StringIO()):
            self.assertFalse(update.confirm_retagging(6))
        prompt.assert_not_called()

    def test_only_explicit_yes_from_interactive_terminal_approves(self):
        for answer, expected in (("igen", True), (" IGEN ", True), ("", False), ("y", False), ("nem", False)):
            with self.subTest(answer=answer), mock.patch.object(update.sys, "stdin") as terminal, \
                    mock.patch("builtins.input", return_value=answer), redirect_stdout(StringIO()):
                terminal.isatty.return_value = True
                self.assertEqual(update.confirm_retagging(6), expected)

    def test_closed_interactive_input_declines(self):
        with mock.patch.object(update.sys, "stdin") as terminal, \
                mock.patch("builtins.input", side_effect=EOFError), redirect_stdout(StringIO()):
            terminal.isatty.return_value = True
            self.assertFalse(update.confirm_retagging(6))


if __name__ == "__main__":
    unittest.main()
