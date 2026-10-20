"""Offline safeguards for the article labeler; all temporary data stays outside the archive."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tagging import label


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("TAGGING_TEST_TMP"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "articles").mkdir()
        self.old_root = label.ROOT
        label.ROOT = self.root
        self.addCleanup(setattr, label, "ROOT", self.old_root)
        self.vocab = {"tags": [{"id": "investing", "name": "Befektetés", "rule": "Investing"}]}
        self.setting = label.settings(self.vocab)
        self.row = {"id": 12, "title": "Test title", "published_at": "2010-01-01T00:00:00",
                    "path": "articles/test.md"}
        self.article_path.write_text("---\nid: 12\ntags: old-blog-tag\n---\n# Test title\n\nMeaningful sentence about investing.", encoding="utf-8")
        self.state_path = self.root / "run.json"
        self.state = label.load_state(self.state_path, "pilot", self.setting)
        self.state["_path"] = str(self.state_path)

    @property
    def article_path(self):
        return self.root / "articles" / "test.md"

    def response(self, *, tags=None, text=None, usage=None, model="gpt-6-luna"):
        result = {"status": "classified", "tags": tags if tags is not None else [{"id": "investing", "evidence": "Meaningful sentence"}],
                  "nature": ["explanation"], "missing_topics": [], "notes": ""}
        return {"text": text if text is not None else json.dumps(result),
                "usage": usage or {"input_tokens": 10, "output_tokens": 5}, "model": model,
                "request_id": "req-test", "cli_version": "codex-cli test", "elapsed_seconds": 0.1}

    def run_one(self, transport, max_requests=150, reprocess=False):
        return label.classify(self.row, self.state, self.setting, {"investing"}, max_requests, True, transport, reprocess)

    def test_success_is_saved_and_resumed_without_second_call(self):
        calls = []
        transport = lambda request: (calls.append(request) or self.response())
        first = self.run_one(transport)
        self.assertEqual(first["tags"], ["investing"])
        self.assertEqual(first["returned_model"], "gpt-6-luna")
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["articles"][0]["status"], "classified")
        self.assertEqual(saved["attempts"][0]["state"], "complete")
        loaded = label.load_state(self.state_path, "pilot", self.setting)
        loaded["_path"] = str(self.state_path)
        resumed = label.classify(self.row, loaded, self.setting, {"investing"}, 150, True, transport)
        self.assertEqual(resumed["status"], "classified")
        self.assertEqual(len(calls), 1)

    def test_changed_input_is_stale_until_explicit_reprocess(self):
        old = self.run_one(lambda request: self.response())
        self.state["articles"].append(old)
        self.article_path.write_text("Changed meaningful sentence.", encoding="utf-8")
        stale = label.classify(self.row, self.state, self.setting, {"investing"}, 150, True, lambda request: self.fail("called"))
        self.assertEqual(stale["status"], "stale")
        updated = label.classify(self.row, self.state, self.setting, {"investing"}, 150, True,
                                 lambda request: self.response(), reprocess=True)
        self.assertEqual(updated["status"], "classified")

    def test_changed_settings_require_explicit_reprocess(self):
        old = self.run_one(lambda request: self.response())
        self.state["articles"].append(old)
        with mock.patch.object(label, "make_prompt", lambda tags: "changed core prompt" if tags else "changed empty prompt"):
            changed = label.settings(self.vocab)
        stale = label.classify(self.row, self.state, changed, {"investing"}, 150, True,
                               lambda request: self.fail("called"))
        self.assertEqual(stale["status"], "stale")
        updated = label.classify(self.row, self.state, changed, {"investing"}, 150, True,
                                 lambda request: self.response(), reprocess=True)
        self.assertEqual(updated["status"], "classified")

    def test_additive_vocabulary_keeps_old_rows_searchable_and_resumable(self):
        calls = []
        first = label.classify(self.row, self.state, self.setting, {"investing"}, 150, True,
                               lambda request: (calls.append(request) or self.response()))
        label.upsert_article(self.state, first)
        label.atomic_write(self.state_path, self.state)
        expanded = {"tags": [*self.vocab["tags"], {"id": "costs", "name": "Költségek", "rule": "Costs"}]}
        setting = label.settings(expanded)
        loaded = label.load_state(self.state_path, "pilot", setting)
        loaded["_path"] = str(self.state_path)
        resumed = label.classify(self.row, loaded, setting, {"investing", "costs"}, 150, True,
                                 lambda request: self.fail("additive tag caused a repeat call"))
        self.assertEqual(resumed["status"], "classified")
        self.assertEqual(resumed["evaluated_tag_ids"], ["investing"])
        self.assertEqual(len(calls), 1)
        reprocessed = label.classify(self.row, loaded, setting, {"investing", "costs"}, 150, True,
                                     lambda request: self.response(tags=[{"id": "investing", "evidence": "Investing"},
                                                                        {"id": "costs", "evidence": "Costs"}]),
                                     reprocess=True)
        self.assertEqual(reprocessed["evaluated_tag_ids"], ["costs", "investing"])
        self.assertEqual(reprocessed["settings_sha256"], setting["settings_sha256"])
        second_row = dict(self.row, id=13, title="Second", path="articles/second.md")
        (self.root / second_row["path"]).write_text("Second meaningful article.", encoding="utf-8")
        second = label.classify(second_row, loaded, setting, {"investing", "costs"}, 150, True,
                                lambda request: self.response(tags=[{"id": "costs", "evidence": "Costs"}]))
        label.upsert_article(loaded, reprocessed)
        label.upsert_article(loaded, second)
        label.atomic_write(self.state_path, loaded)
        reloaded = label.load_state(self.state_path, "pilot", setting)
        self.assertEqual({row["id"] for row in reloaded["articles"]}, {12, 13})
        for row in reloaded["articles"]:
            self.assertEqual(row["status"], "classified")
            self.assertEqual(row["evaluated_tag_ids"], ["costs", "investing"])
        self.assertEqual(reloaded["vocabulary_snapshot"], setting["vocabulary_snapshot"])
        for row in reloaded["articles"]:
            resumed = label.classify(second_row if row["id"] == 13 else self.row, reloaded, setting,
                                     {"investing", "costs"}, 150, True,
                                     lambda request: self.fail("mixed run repeated a successful request"))
            self.assertEqual(resumed["status"], "classified")

    def test_changed_or_removed_tag_definition_makes_rows_stale(self):
        old = self.run_one(lambda request: self.response())
        label.upsert_article(self.state, old)
        label.atomic_write(self.state_path, self.state)
        changed = {"tags": [{**self.vocab["tags"][0], "rule": "Changed rule"}]}
        changed_setting = label.settings(changed)
        loaded = label.load_state(self.state_path, "pilot", changed_setting)
        self.assertEqual(loaded["articles"][0]["status"], "stale")
        self.assertEqual(loaded["articles"][0]["tags"], [])
        removed_setting = label.settings({"tags": []})
        removed = label.load_state(self.state_path, "pilot", removed_setting)
        self.assertEqual(removed["articles"][0]["status"], "stale")

    def test_mixed_run_preserves_older_article_coverage_after_restart(self):
        old = self.run_one(lambda request: self.response())
        label.upsert_article(self.state, old)
        label.atomic_write(self.state_path, self.state)
        expanded = {"tags": [*self.vocab["tags"], {"id": "costs", "name": "Költségek", "rule": "Costs"}]}
        setting = label.settings(expanded)
        loaded = label.load_state(self.state_path, "pilot", setting)
        loaded["_path"] = str(self.state_path)
        second_row = dict(self.row, id=13, title="Second", path="articles/second.md")
        (self.root / second_row["path"]).write_text("Second meaningful article.", encoding="utf-8")
        second = label.classify(second_row, loaded, setting, {"investing", "costs"}, 150, True,
                                lambda request: self.response(tags=[{"id": "costs", "evidence": "Costs"}]))
        label.upsert_article(loaded, second)
        label.atomic_write(self.state_path, loaded)
        reloaded = label.load_state(self.state_path, "pilot", setting)
        for row in (self.row, second_row):
            result = label.classify(row, reloaded, setting, {"investing", "costs"}, 150, True,
                                    lambda request: self.fail("mixed run repeated a request"))
            self.assertEqual(result["status"], "classified")
        self.assertEqual(reloaded["articles"][0]["evaluated_tag_ids"], ["investing"])
        self.assertEqual(reloaded["articles"][0]["fingerprint"], old["fingerprint"])
        self.assertEqual(reloaded["articles"][1]["evaluated_tag_ids"], ["costs", "investing"])

    def test_compatible_error_retries_with_current_fingerprint_then_resumes(self):
        failed = self.run_one(lambda request: (_ for _ in ()).throw(label.LabelError("temporary")))
        self.assertEqual(failed["status"], "error")
        label.upsert_article(self.state, failed)
        label.atomic_write(self.state_path, self.state)
        expanded = {"tags": [*self.vocab["tags"], {"id": "costs", "name": "Költségek", "rule": "Costs"}]}
        setting = label.settings(expanded)
        loaded = label.load_state(self.state_path, "pilot", setting)
        loaded["_path"] = str(self.state_path)
        result = label.classify(self.row, loaded, setting, {"investing", "costs"}, 150, True,
                                lambda request: self.response())
        self.assertEqual(result["status"], "classified")
        self.assertEqual(result["settings_sha256"], setting["settings_sha256"])
        label.upsert_article(loaded, result)
        label.atomic_write(self.state_path, loaded)
        reloaded = label.load_state(self.state_path, "pilot", setting)
        resumed = label.classify(self.row, reloaded, setting, {"investing", "costs"}, 150, True,
                                 lambda request: self.fail("successful current settings retried"))
        self.assertEqual(resumed["status"], "classified")

    def test_failed_row_with_changed_source_or_core_settings_will_not_retry(self):
        failed = self.run_one(lambda request: (_ for _ in ()).throw(label.LabelError("temporary")))
        original_failed = dict(failed)
        original_source = self.article_path.read_text(encoding="utf-8")
        label.upsert_article(self.state, failed)
        self.article_path.write_text("Changed source text.", encoding="utf-8")
        changed = label.classify(self.row, self.state, self.setting, {"investing"}, 150, True,
                                 lambda request: self.fail("changed source retried"))
        self.assertEqual(changed["status"], "stale")
        self.article_path.write_text(original_source, encoding="utf-8")
        with mock.patch.object(label, "MODEL", "different-model"):
            core_setting = label.settings(self.vocab)
        label.upsert_article(self.state, original_failed)
        changed_core = label.classify(self.row, self.state, core_setting, {"investing"}, 150, True,
                                      lambda request: self.fail("changed core settings retried"))
        self.assertEqual(changed_core["status"], "stale")

    def test_model_change_makes_rows_stale(self):
        old = self.run_one(lambda request: self.response())
        label.upsert_article(self.state, old)
        label.atomic_write(self.state_path, self.state)
        with mock.patch.object(label, "MODEL", "different-model"):
            changed_setting = label.settings(self.vocab)
        loaded = label.load_state(self.state_path, "pilot", changed_setting)
        self.assertEqual(loaded["articles"][0]["status"], "stale")

    def test_legacy_backfill_and_preview_leave_saved_bytes_unchanged(self):
        result = self.run_one(lambda request: self.response())
        legacy = dict(self.state)
        legacy.pop("vocabulary_snapshot", None)
        legacy["articles"] = [{key: value for key, value in result.items()
                               if key not in ("labeling_sha256", "evaluated_tag_ids")}]
        label.atomic_write(self.state_path, legacy)
        before = self.state_path.read_bytes()
        loaded = label.load_state(self.state_path, "pilot", self.setting)
        self.assertIn("labeling_sha256", loaded["articles"][0])
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_cli_version_is_provenance_not_semantic_settings(self):
        later = label.settings(self.vocab, cli_version="codex-cli 999")
        self.assertEqual(self.setting["settings_sha256"], later["settings_sha256"])

    def test_cleaner_helper_change_invalidates_saved_settings(self):
        markdown = "# TEST TITLE\n\nBody."
        original = label.clean_markdown(markdown, "Test title")
        with mock.patch.object(label, "normalize", lambda value: value):
            self.assertNotEqual(label.clean_markdown(markdown, "Test title"), original)
            changed = label.settings(self.vocab)
        self.assertNotEqual(changed["settings_sha256"], self.setting["settings_sha256"])

    def test_preview_does_not_create_or_change_output(self):
        output = self.root / "preview.json"
        with mock.patch.object(label, "article_rows", return_value=[self.row]), \
             mock.patch.object(label, "codex_exec", side_effect=AssertionError("preview called model")):
            self.assertEqual(label.main(["--output", str(output)]), 0)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".json.lock").exists())
            label.atomic_write(output, self.state)
            before = output.read_bytes()
            label.main(["--output", str(output)])
            self.assertEqual(output.read_bytes(), before)

    def test_retries_are_limited_to_three_across_restarts(self):
        calls = []
        result = self.run_one(lambda request: (calls.append(request) or (_ for _ in ()).throw(label.LabelError("timeout"))))
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(self.state["attempts"]), 3)
        loaded = label.load_state(self.state_path, "pilot", self.setting)
        loaded["_path"] = str(self.state_path)
        result = label.classify(self.row, loaded, self.setting, {"investing"}, 150, True,
                                lambda request: self.fail("fourth attempt"))
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(loaded["attempts"]), 3)
        self.assertEqual(len(calls), 3)

    def test_invalid_or_refused_output_is_not_classified(self):
        for response in (self.response(text="not json"),
                         {"text": json.dumps({"status": "classified", "tags": [{"id": "unknown", "evidence": "x"}],
                                              "nature": [], "missing_topics": [], "notes": ""})}):
            result = self.run_one(lambda request, response=response: response)
            self.assertEqual(result["status"], "error")
            self.assertFalse(result["tags"])
            self.state["attempts"].clear()

    def test_request_cap_and_durable_pending_attempt(self):
        with self.assertRaisesRegex(label.LabelError, "request cap"):
            self.run_one(lambda request: self.fail("called"), max_requests=0)
        class SimulatedCrash(BaseException):
            pass
        def crash(request):
            saved = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["attempts"][0]["state"], "pending")
            self.assertEqual(saved["attempts"][0]["provider"], "chatgpt-subscription")
            raise SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.run_one(crash, max_requests=1)
        self.assertEqual(len(json.loads(self.state_path.read_text(encoding="utf-8"))["attempts"]), 1)

    def test_missing_title_only_and_oversized_input_have_no_request(self):
        self.article_path.unlink()
        missing = label.classify(self.row, self.state, self.setting, {"investing"}, 10, False, None)
        self.assertEqual(missing["status"], "error")
        self.article_path.write_text("---\nid: 12\n---\n# Test title\n", encoding="utf-8")
        title_only = label.classify(self.row, self.state, self.setting, {"investing"}, 10, False, None)
        self.assertEqual(title_only["status"], "insufficient_content")
        self.assertEqual(title_only["tags"], [])
        self.article_path.write_text("x" * label.MAX_REQUEST_BYTES, encoding="utf-8")
        oversized = label.classify(self.row, self.state, self.setting, {"investing"}, 10, False, None)
        self.assertEqual(oversized["status"], "input_too_long")

    def test_override_file_is_never_touched(self):
        override = self.root / "overrides.json"
        override.write_text('{"manual":true}', encoding="utf-8")
        self.run_one(lambda request: self.response())
        self.assertEqual(override.read_text(encoding="utf-8"), '{"manual":true}')

    def test_output_lock_rejects_a_second_writer(self):
        lock = label.output_lock(self.state_path)
        try:
            with self.assertRaisesRegex(label.LabelError, "locked"):
                label.output_lock(self.state_path)
        finally:
            lock.unlink()

    def test_main_releases_lock_on_subscription_error_and_malformed_state(self):
        output = self.root / "main.json"
        with mock.patch.object(label, "codex_cli_version", return_value="test-cli"), \
             mock.patch.object(label, "require_chatgpt_login", side_effect=label.FatalCodexError("not logged in")):
            code = label.main(["--run", "--output", str(output)])
        self.assertEqual(code, 2)
        self.assertFalse(Path(str(output) + ".lock").exists())
        output.write_text("not json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            label.main(["--output", str(output)])
        self.assertFalse(Path(str(output) + ".lock").exists())

    def test_login_status_accepts_chatgpt_confirmation_on_stderr(self):
        with mock.patch.object(label.shutil, "which", return_value="codex"), \
             mock.patch.object(label.subprocess, "run", return_value=SimpleNamespace(
                 returncode=0, stdout="", stderr="Logged in using ChatGPT\n")):
            label.require_chatgpt_login()

    def test_request_cap_stop_is_recorded_and_nonzero(self):
        output = self.root / "cap.json"
        tags = label.load_json(label.HERE / "tags.json")
        setting = label.settings(tags, label.sha((label.HERE / "tags.json").read_bytes()))
        state = label.load_state(output, "pilot", setting)
        state["attempts"] = [{"id": 999, "fingerprint": "old", "provider": "chatgpt-subscription", "state": "pending"}]
        label.atomic_write(output, state)
        with mock.patch.object(label, "article_rows", return_value=[self.row]):
            code = label.main(["--run", "--max-requests", "1", "--output", str(output)],
                              transport=lambda request: self.fail("request cap should stop before CLI"))
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(saved["summary"]["stop_reason"], "request_cap_reached")
        self.assertEqual(saved["summary"]["request_count"], 1)
        self.assertFalse(Path(str(output) + ".lock").exists())

    def test_codex_exec_uses_isolated_chatgpt_subscription_settings(self):
        captured = {}
        expected = self.response()
        event = {"type": "turn.completed", "model": "gpt-6-luna",
                 "usage": {"input_tokens": 12, "output_tokens": 6}}
        def fake_run(command, **kwargs):
            captured.update(command=command, kwargs=kwargs)
            result_path = Path(command[command.index("--output-last-message") + 1])
            result_path.write_text(expected["text"], encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout=json.dumps(event) + "\n", stderr="")
        request = label.make_request(self.row["title"], "Meaningful body", self.setting)
        secrets = {"OPENAI_API_KEY": "api-secret", "CODEX_API_KEY": "codex-secret", "CODEX_THREAD_ID": "thread"}
        with mock.patch.dict(os.environ, secrets), mock.patch.object(label.shutil, "which", return_value="codex"), \
             mock.patch.object(label.subprocess, "run", side_effect=fake_run):
            result = label.codex_exec(request)
        command = captured["command"]
        kwargs = captured["kwargs"]
        self.assertIn("--sandbox", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertIn("forced_login_method='chatgpt'", command)
        for tool in ("shell_tool", "multi_agent", "apps", "hooks"):
            self.assertIn(tool, command)
        self.assertTrue(kwargs["cwd"])
        self.assertNotIn(str(label.ROOT), kwargs["cwd"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["input"], request["prompt"])
        for secret in secrets:
            self.assertNotIn(secret, kwargs["env"])
        self.assertEqual(result["usage"]["input_tokens"], 12)
        self.assertEqual(result["model"], "gpt-6-luna")

    def test_articles_use_separate_ephemeral_processes_and_contexts(self):
        requests = [label.make_request("First", "FIRST_ARTICLE_ONLY", self.setting),
                    label.make_request("Second", "SECOND_ARTICLE_ONLY", self.setting)]
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            result_path = Path(command[command.index("--output-last-message") + 1])
            result_path.write_text(self.response()["text"], encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with mock.patch.object(label.shutil, "which", return_value="codex"), \
             mock.patch.object(label.subprocess, "run", side_effect=fake_run):
            for request in requests:
                label.codex_exec(request)

        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0][1]["cwd"], calls[1][1]["cwd"])
        for index, (command, kwargs) in enumerate(calls):
            self.assertEqual(command[1], "exec")
            self.assertIn("--ephemeral", command)
            self.assertNotIn("resume", command)
            self.assertNotIn("--last", command)
            self.assertEqual(kwargs["input"], requests[index]["prompt"])
            self.assertNotIn(requests[1 - index]["body"], kwargs["input"])

    def test_tool_event_is_rejected_and_subscription_limit_is_fatal(self):
        usage, model, request_id, tool_used = label.cli_usage(json.dumps({"type": "item.started",
                                                                          "item": {"type": "command_execution"}}))
        self.assertTrue(tool_used)
        self.assertIsNone(model)
        def failed_run(command, **kwargs):
            return SimpleNamespace(returncode=1,
                                    stdout=json.dumps({"type": "error", "message": "Usage limit reached"}),
                                    stderr="")
        request = label.make_request(self.row["title"], "Some article", self.setting)
        with mock.patch.object(label.shutil, "which", return_value="codex"), \
             mock.patch.object(label.subprocess, "run", side_effect=failed_run):
            with self.assertRaisesRegex(label.FatalCodexError, "usage limit"):
                label.codex_exec(request)


if __name__ == "__main__":
    unittest.main()
