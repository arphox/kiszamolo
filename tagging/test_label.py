"""Offline safeguards for the article labeler; all temporary data stays outside the archive."""

import json
import os
from pathlib import Path
import signal
import tempfile
import threading
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

    def test_atomic_write_retries_transient_permission_error(self):
        value = {"articles": [{"id": 12, "status": "classified"}], "_path": "ignored"}
        destination = self.root / "atomic.json"
        destination.write_text('{"old":true}\n', encoding="utf-8")
        original_replace = os.replace
        calls = []

        def replace_with_transient_errors(source, target):
            calls.append((Path(source), Path(target)))
            if len(calls) < 3:
                raise PermissionError("temporary sharing violation")
            original_replace(source, target)

        with mock.patch.object(label.os, "replace", side_effect=replace_with_transient_errors), \
             mock.patch.object(label.time, "sleep") as sleep:
            label.atomic_write(destination, value)

        self.assertEqual(len(calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.1])
        self.assertEqual(json.loads(destination.read_text(encoding="utf-8")),
                         {"articles": [{"id": 12, "status": "classified"}]})

    def test_atomic_write_propagates_after_exhausted_permission_errors(self):
        destination = self.root / "atomic.json"
        original = b'{"old":true}\n'
        destination.write_bytes(original)
        error = PermissionError("permanent sharing violation")
        with mock.patch.object(label.os, "replace", side_effect=error) as replace, \
             mock.patch.object(label.time, "sleep") as sleep:
            with self.assertRaises(PermissionError) as raised:
                label.atomic_write(destination, {"new": True})

        self.assertIs(raised.exception, error)
        self.assertEqual(replace.call_count, 5)
        self.assertEqual([round(call.args[0], 2) for call in sleep.call_args_list], [0.05, 0.1, 0.15, 0.2])
        self.assertEqual(destination.read_bytes(), original)

    def test_atomic_write_does_not_retry_other_replace_errors(self):
        destination = self.root / "atomic.json"
        original = b'{"old":true}\n'
        destination.write_bytes(original)
        error = OSError("disk error")
        with mock.patch.object(label.os, "replace", side_effect=error) as replace, \
             mock.patch.object(label.time, "sleep") as sleep:
            with self.assertRaises(OSError) as raised:
                label.atomic_write(destination, {"new": True})

        self.assertIs(raised.exception, error)
        replace.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(destination.read_bytes(), original)

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

    def test_cached_resume_skips_per_record_write_for_both_worker_modes(self):
        record = self.run_one(lambda request: self.response())
        for workers in (1, 2):
            with self.subTest(workers=workers):
                output = self.root / f"cached-{workers}.json"
                seeded = dict(self.state)
                seeded["articles"] = [dict(record)]
                label.atomic_write(output, seeded)
                writes = []
                original_write = label.atomic_write

                def tracked_write(path, state):
                    writes.append(path)
                    return original_write(path, state)

                original_load_json = label.load_json
                def load_test_vocab(path):
                    return self.vocab if Path(path) == label.HERE / "tags.json" else original_load_json(path)

                with mock.patch.object(label, "load_json", side_effect=load_test_vocab), \
                     mock.patch.object(label, "article_rows", return_value=[self.row]), \
                     mock.patch.object(label, "atomic_write", side_effect=tracked_write):
                    self.assertEqual(label.main(["--run", "--workers", str(workers), "--output", str(output)],
                                                transport=lambda request: self.fail("cached row requested model call")), 0)

                self.assertEqual(writes, [output])
                saved = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(saved["articles"][0], record)

    def test_cached_source_change_persists_stale_transition_immediately(self):
        record = self.run_one(lambda request: self.response())
        self.article_path.write_text("Changed meaningful sentence.", encoding="utf-8")
        for workers in (1, 2):
            with self.subTest(workers=workers):
                output = self.root / f"changed-{workers}.json"
                seeded = dict(self.state)
                seeded["articles"] = [dict(record)]
                label.atomic_write(output, seeded)
                writes = []
                original_write = label.atomic_write

                def tracked_write(path, state):
                    writes.append([dict(article) for article in state["articles"]])
                    return original_write(path, state)

                original_load_json = label.load_json
                def load_test_vocab(path):
                    return self.vocab if Path(path) == label.HERE / "tags.json" else original_load_json(path)

                with mock.patch.object(label, "load_json", side_effect=load_test_vocab), \
                     mock.patch.object(label, "article_rows", return_value=[self.row]), \
                     mock.patch.object(label, "atomic_write", side_effect=tracked_write):
                    self.assertEqual(label.main(["--run", "--workers", str(workers), "--output", str(output)],
                                                transport=lambda request: self.fail("stale row requested model call")), 2)

                self.assertGreaterEqual(len(writes), 2)
                self.assertEqual(writes[0][0]["status"], "stale")
                self.assertEqual(writes[0][0]["tags"], [])

    def test_cached_legacy_metadata_is_persisted_by_final_write(self):
        setting = label.settings(self.vocab, label.sha((label.HERE / "tags.json").read_bytes()))
        output = self.root / "cached-legacy.json"
        legacy = label.load_state(output, "pilot", setting)
        legacy["_path"] = str(output)
        record = label.classify(self.row, legacy, setting, {"investing"}, 150, True,
                                lambda request: self.response())
        legacy = dict(legacy)
        legacy.pop("vocabulary_snapshot", None)
        legacy["articles"] = [{key: value for key, value in record.items()
                               if key not in ("labeling_sha256", "evaluated_tag_ids")}]
        label.atomic_write(output, legacy)
        writes = []
        original_write = label.atomic_write

        def tracked_write(path, state):
            writes.append(path)
            return original_write(path, state)

        original_load_json = label.load_json
        def load_test_vocab(path):
            return self.vocab if Path(path) == label.HERE / "tags.json" else original_load_json(path)

        with mock.patch.object(label, "load_json", side_effect=load_test_vocab), \
             mock.patch.object(label, "article_rows", return_value=[self.row]), \
             mock.patch.object(label, "atomic_write", side_effect=tracked_write):
            self.assertEqual(label.main(["--run", "--workers", "1", "--output", str(output)],
                                        transport=lambda request: self.fail("cached legacy row requested model call")), 0)

        self.assertEqual(writes, [output])
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["articles"][0]["labeling_sha256"], setting["labeling_sha256"])
        self.assertEqual(saved["articles"][0]["evaluated_tag_ids"], ["investing"])
        self.assertEqual(saved["vocabulary_snapshot"], setting["vocabulary_snapshot"])

    def test_cached_additive_vocabulary_snapshot_is_persisted_by_final_write(self):
        record = self.run_one(lambda request: self.response())
        output = self.root / "cached-additive.json"
        seeded = dict(self.state)
        seeded["articles"] = [dict(record)]
        label.atomic_write(output, seeded)
        expanded = {"tags": [*self.vocab["tags"],
                             {"id": "costs", "name": "Költségek", "rule": "Costs"}]}
        writes = []
        original_write = label.atomic_write
        original_load_json = label.load_json

        def tracked_write(path, state):
            writes.append(path)
            return original_write(path, state)

        def load_test_vocab(path):
            return expanded if Path(path) == label.HERE / "tags.json" else original_load_json(path)

        with mock.patch.object(label, "load_json", side_effect=load_test_vocab), \
             mock.patch.object(label, "article_rows", return_value=[self.row]), \
             mock.patch.object(label, "atomic_write", side_effect=tracked_write):
            self.assertEqual(label.main(["--run", "--workers", "1", "--output", str(output)],
                                        transport=lambda request: self.fail("cached additive row requested model call")), 0)

        self.assertEqual(writes, [output])
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(set(saved["vocabulary_snapshot"]), {"investing", "costs"})

    def test_parallel_identical_reprocess_persists_completed_attempt_on_sigint(self):
        output = self.root / "identical-reprocess-interrupted.json"
        setting = label.settings(self.vocab, label.sha((label.HERE / "tags.json").read_bytes()))
        seeded = label.load_state(output, "pilot", setting)
        seeded["_path"] = str(output)
        record = label.classify(self.row, seeded, setting, {"investing"}, 150, True,
                                lambda request: self.response())
        seeded = dict(seeded)
        seeded["articles"] = [dict(record)]
        label.atomic_write(output, seeded)
        response = self.response(usage={"input_tokens": 23, "output_tokens": 7})
        original_parse = label.parse_output
        interrupted = []

        def interrupt_then_parse(text, allowed):
            if not interrupted:
                interrupted.append(True)
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            return original_parse(text, allowed)

        original_load_json = label.load_json
        def load_test_vocab(path):
            return self.vocab if Path(path) == label.HERE / "tags.json" else original_load_json(path)

        with mock.patch.object(label, "load_json", side_effect=load_test_vocab), \
             mock.patch.object(label, "article_rows", return_value=[self.row]), \
             mock.patch.object(label, "parse_output", side_effect=interrupt_then_parse):
            with self.assertRaises(KeyboardInterrupt):
                label.main(["--run", "--workers", "2", "--reprocess", "--output", str(output)],
                           transport=lambda request: response)

        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(interrupted, [True])
        self.assertEqual(saved["articles"][0], record)
        self.assertEqual(saved["attempts"][-1]["state"], "complete")
        self.assertEqual(saved["attempts"][-1]["usage"], response["usage"])

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
            self.assertEqual(label.main(["--workers", "2", "--output", str(output)]), 0)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".json.lock").exists())
            label.atomic_write(output, self.state)
            before = output.read_bytes()
            label.main(["--output", str(output)])
            self.assertEqual(output.read_bytes(), before)

    def test_four_workers_overlap_and_coordinator_owns_writes(self):
        output = self.root / "four-workers.json"
        rows = [self.row]
        for article_id in range(13, 16):
            row = dict(self.row, id=article_id, title=f"Article {article_id}",
                       path=f"articles/{article_id}.md")
            (self.root / row["path"]).write_text("Another meaningful article.", encoding="utf-8")
            rows.append(row)
        all_started = threading.Event()
        release = threading.Event()
        calls = []
        write_threads = []
        original_write = label.atomic_write

        def transport(request):
            calls.append(threading.get_ident())
            if len(calls) == 4:
                all_started.set()
            if not release.wait(3):
                raise AssertionError("four article requests did not overlap")
            return {"text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                        "missing_topics": [], "notes": ""})}

        def tracked_write(path, state):
            write_threads.append(threading.get_ident())
            return original_write(path, state)

        with mock.patch.object(label, "article_rows", return_value=rows), \
             mock.patch.object(label, "atomic_write", side_effect=tracked_write):
            result = []
            runner = threading.Thread(target=lambda: result.append(label.main(
                ["--run", "--workers", "4", "--output", str(output)], transport=transport)))
            runner.start()
            try:
                self.assertTrue(all_started.wait(3), "four article requests should overlap")
            finally:
                release.set()
                runner.join(5)
        self.assertFalse(runner.is_alive())
        self.assertEqual(result, [0])
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(set(calls)), 4)
        self.assertTrue(write_threads)
        self.assertEqual(set(write_threads), {runner.ident})
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual({row["id"] for row in saved["articles"]}, {12, 13, 14, 15})

    def test_four_workers_reserve_only_remaining_global_request_budget(self):
        output = self.root / "capped-four-workers.json"
        rows = [self.row]
        for article_id in range(13, 16):
            row = dict(self.row, id=article_id, title=f"Article {article_id}",
                       path=f"articles/{article_id}.md")
            (self.root / row["path"]).write_text("Another meaningful article.", encoding="utf-8")
            rows.append(row)
        calls = []
        with mock.patch.object(label, "article_rows", return_value=rows):
            code = label.main(["--run", "--workers", "4", "--max-requests", "3",
                               "--output", str(output)],
                              transport=lambda request: (calls.append(request) or {
                                  "text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                                      "missing_topics": [], "notes": ""})}))
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(saved["attempts"]), 3)
        self.assertEqual(saved["summary"]["request_count"], 3)
        self.assertEqual(saved["summary"]["stop_reason"], "request_cap_reached")

    def test_four_workers_reject_values_outside_supported_range(self):
        for workers in ("0", "5"):
            with self.subTest(workers=workers), self.assertRaises(SystemExit) as raised:
                label.main(["--workers", workers])
            self.assertEqual(raised.exception.code, 2)

    def test_two_workers_overlap_and_coordinator_owns_writes(self):
        output = self.root / "parallel.json"
        second = dict(self.row, id=13, title="Second", path="articles/second.md")
        (self.root / second["path"]).write_text("Another meaningful article.", encoding="utf-8")
        both_started = threading.Event()
        release = threading.Event()
        calls = []
        write_threads = []
        original_write = label.atomic_write

        def transport(request):
            calls.append(threading.get_ident())
            if len(calls) == 2:
                both_started.set()
            if not release.wait(3):
                raise AssertionError("parallel transports did not get released")
            return {"text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                        "missing_topics": [], "notes": ""})}

        def tracked_write(path, state):
            write_threads.append(threading.get_ident())
            return original_write(path, state)

        with mock.patch.object(label, "article_rows", return_value=[self.row, second]), \
             mock.patch.object(label, "atomic_write", side_effect=tracked_write):
            result = []
            runner = threading.Thread(target=lambda: result.append(label.main(
                ["--run", "--workers", "2", "--output", str(output)], transport=transport)))
            runner.start()
            try:
                self.assertTrue(both_started.wait(3), "two article requests should overlap")
            finally:
                release.set()
                runner.join(5)
        self.assertFalse(runner.is_alive())
        self.assertEqual(result, [0])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 2)
        self.assertTrue(write_threads)
        self.assertEqual(set(write_threads), {runner.ident})
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual({row["id"] for row in saved["articles"]}, {12, 13})

    def test_two_workers_reserve_only_remaining_global_request_budget(self):
        output = self.root / "capped-parallel.json"
        second = dict(self.row, id=13, title="Second", path="articles/second.md")
        (self.root / second["path"]).write_text("Another meaningful article.", encoding="utf-8")
        calls = []
        with mock.patch.object(label, "article_rows", return_value=[self.row, second]):
            code = label.main(["--run", "--workers", "2", "--max-requests", "1", "--output", str(output)],
                              transport=lambda request: (calls.append(request) or {
                                  "text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                                      "missing_topics": [], "notes": ""})}))
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 1)
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["attempts"]), 1)
        self.assertEqual(saved["summary"]["stop_reason"], "request_cap_reached")

    def test_two_workers_keep_request_and_settings_and_resume_identical(self):
        serial_output = self.root / "serial.json"
        parallel_output = self.root / "parallel-same.json"
        serial_requests = []
        parallel_requests = []
        response = {"text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                        "missing_topics": [], "notes": ""})}
        with mock.patch.object(label, "article_rows", return_value=[self.row]):
            self.assertEqual(label.main(["--run", "--workers", "1", "--output", str(serial_output)],
                                        transport=lambda request: (serial_requests.append(request) or response)), 0)
            self.assertEqual(label.main(["--run", "--workers", "2", "--output", str(parallel_output)],
                                        transport=lambda request: (parallel_requests.append(request) or response)), 0)
            self.assertEqual(label.main(["--run", "--workers", "2", "--output", str(parallel_output)],
                                        transport=lambda request: self.fail("completed request was repeated")), 0)
        self.assertEqual(serial_requests, parallel_requests)
        serial = json.loads(serial_output.read_text(encoding="utf-8"))["articles"][0]
        parallel = json.loads(parallel_output.read_text(encoding="utf-8"))["articles"][0]
        self.assertEqual(serial["fingerprint"], parallel["fingerprint"])
        self.assertEqual(serial["settings_sha256"], parallel["settings_sha256"])

    def test_two_workers_retries_share_the_global_cap(self):
        output = self.root / "retry-cap-parallel.json"
        calls = []
        with mock.patch.object(label, "article_rows", return_value=[self.row]):
            code = label.main(["--run", "--workers", "2", "--max-requests", "2", "--output", str(output)],
                              transport=lambda request: (calls.append(request) or
                                  (_ for _ in ()).throw(label.LabelError("temporary"))))
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(saved["attempts"]), 2)
        self.assertEqual([attempt["number"] for attempt in saved["attempts"]], [1, 2])
        self.assertEqual(saved["summary"]["stop_reason"], "request_cap_reached")

    def test_two_workers_malformed_response_at_cap_still_drains_inflight_result(self):
        output = self.root / "malformed-cap-parallel.json"
        second = dict(self.row, id=13, title="Second", path="articles/second.md")
        (self.root / second["path"]).write_text("Another meaningful article.", encoding="utf-8")
        second_started = threading.Event()
        release_second = threading.Event()
        calls = []

        def transport(request):
            calls.append(request)
            if len(calls) == 1:
                return {"text": "invalid response"}
            second_started.set()
            if not release_second.wait(3):
                raise AssertionError("in-flight request was not released")
            return {"text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                        "missing_topics": [], "notes": ""})}

        with mock.patch.object(label, "article_rows", return_value=[self.row, second]):
            result = []
            runner = threading.Thread(target=lambda: result.append(label.main(
                ["--run", "--workers", "2", "--max-requests", "2", "--output", str(output)],
                transport=transport)))
            runner.start()
            try:
                self.assertTrue(second_started.wait(3))
                release_second.set()
            finally:
                release_second.set()
                runner.join(5)
        self.assertFalse(runner.is_alive())
        self.assertEqual(result, [2])
        self.assertEqual(len(calls), 2)
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["summary"]["stop_reason"], "request_cap_reached")
        self.assertEqual([attempt["state"] for attempt in saved["attempts"]], ["failed", "complete"])
        self.assertEqual(next(row for row in saved["articles"] if row["id"] == 13)["status"], "classified")

    def test_two_workers_interrupt_during_result_collection_drains_saved_outcome(self):
        output = self.root / "interrupted-parallel.json"
        response = {"text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                        "missing_topics": [], "notes": ""})}

        class Future:
            def __init__(self):
                self.calls = 0

            def result(self):
                self.calls += 1
                if self.calls == 1:
                    raise KeyboardInterrupt()
                return response

        future = Future()

        class Executor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def submit(self, transport, request):
                return future

        with mock.patch.object(label, "article_rows", return_value=[self.row]), \
             mock.patch.object(label, "ThreadPoolExecutor", return_value=Executor()), \
             mock.patch.object(label, "wait", return_value=({future}, set())):
            with self.assertRaises(KeyboardInterrupt):
                label.main(["--run", "--workers", "2", "--output", str(output)],
                           transport=lambda request: self.fail("fake executor should provide the response"))
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["articles"][0]["status"], "classified")
        self.assertEqual(saved["attempts"][0]["state"], "complete")
        self.assertFalse(Path(str(output) + ".lock").exists())

    def test_two_workers_interrupt_during_commit_saves_current_and_ready_results(self):
        output = self.root / "commit-interrupted-parallel.json"
        second = dict(self.row, id=13, title="Second", path="articles/second.md")
        (self.root / second["path"]).write_text("Another meaningful article.", encoding="utf-8")
        response = {"text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                        "missing_topics": [], "notes": ""})}
        calls = []
        writes = []
        original_upsert = label.upsert_article

        def interrupt_once(state, record):
            writes.append(record["id"])
            if len(writes) == 1:
                raise KeyboardInterrupt()
            return original_upsert(state, record)

        def wait_for_all(futures, return_when):
            for future in futures:
                future.result(timeout=3)
            return set(futures), set()

        with mock.patch.object(label, "article_rows", return_value=[self.row, second]), \
             mock.patch.object(label, "upsert_article", side_effect=interrupt_once), \
             mock.patch.object(label, "wait", side_effect=wait_for_all):
            with self.assertRaises(KeyboardInterrupt):
                label.main(["--run", "--workers", "2", "--output", str(output)],
                           transport=lambda request: (calls.append(request) or response))
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(calls), 2)
        self.assertEqual({row["id"] for row in saved["articles"]}, {12, 13})
        self.assertEqual([attempt["state"] for attempt in saved["attempts"]], ["complete", "complete"])
        self.assertFalse(Path(str(output) + ".lock").exists())

    def test_two_workers_defer_sigint_until_coordinator_commit_finishes(self):
        output = self.root / "deferred-sigint-parallel.json"
        second = dict(self.row, id=13, title="Second", path="articles/second.md")
        (self.root / second["path"]).write_text("Another meaningful article.", encoding="utf-8")
        response = {"text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                        "missing_topics": [], "notes": ""})}
        original_upsert = label.upsert_article
        interrupted = []

        def signal_once(state, record):
            if not interrupted:
                interrupted.append(record["id"])
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            return original_upsert(state, record)

        def wait_for_all(futures, return_when):
            for future in futures:
                future.result(timeout=3)
            return set(futures), set()

        with mock.patch.object(label, "article_rows", return_value=[self.row, second]), \
             mock.patch.object(label, "upsert_article", side_effect=signal_once), \
             mock.patch.object(label, "wait", side_effect=wait_for_all):
            with self.assertRaises(KeyboardInterrupt):
                label.main(["--run", "--workers", "2", "--output", str(output)],
                           transport=lambda request: response)
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(interrupted), 1)
        self.assertIn(interrupted[0], {12, 13})
        self.assertEqual({row["id"] for row in saved["articles"]}, {12, 13})
        self.assertEqual([attempt["state"] for attempt in saved["attempts"]], ["complete", "complete"])
        self.assertFalse(Path(str(output) + ".lock").exists())

    def test_two_workers_sigint_during_invalid_response_does_not_submit_retry(self):
        output = self.root / "sigint-invalid-response-parallel.json"
        original_parse = label.parse_output
        calls = []
        interrupted = []

        def interrupt_then_parse(text, allowed):
            if not interrupted:
                interrupted.append(True)
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            return original_parse(text, allowed)

        with mock.patch.object(label, "article_rows", return_value=[self.row]), \
             mock.patch.object(label, "parse_output", side_effect=interrupt_then_parse):
            with self.assertRaises(KeyboardInterrupt):
                label.main(["--run", "--workers", "2", "--output", str(output)],
                           transport=lambda request: (calls.append(request) or {"text": "invalid"}))
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(interrupted, [True])
        self.assertEqual(len(saved["attempts"]), 1)
        self.assertEqual(saved["attempts"][0]["state"], "failed")
        self.assertFalse(Path(str(output) + ".lock").exists())

    def test_two_workers_sigint_after_reservation_cancels_before_first_submit(self):
        output = self.root / "sigint-before-submit-parallel.json"
        original_write = label.atomic_write
        calls = []
        interrupted = []

        def interrupt_after_reservation(path, state):
            original_write(path, state)
            if state.get("attempts") and not interrupted:
                interrupted.append(True)
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)

        with mock.patch.object(label, "article_rows", return_value=[self.row]), \
             mock.patch.object(label, "atomic_write", side_effect=interrupt_after_reservation):
            with self.assertRaises(KeyboardInterrupt):
                label.main(["--run", "--workers", "2", "--output", str(output)],
                           transport=lambda request: (calls.append(request) or {"text": "unused"}))
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(calls, [])
        self.assertEqual(interrupted, [True])
        self.assertEqual(saved["attempts"], [])
        self.assertFalse(Path(str(output) + ".lock").exists())

    def test_two_workers_reject_duplicate_input_before_dispatch(self):
        output = self.root / "duplicate-parallel.json"
        with mock.patch.object(label, "article_rows", return_value=[self.row, self.row]):
            code = label.main(["--run", "--workers", "2", "--output", str(output)],
                              transport=lambda request: self.fail("duplicate rows must not dispatch"))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["summary"]["stop_reason"],
                         "processing_error")
        self.assertFalse(Path(str(output) + ".lock").exists())

    def test_two_workers_fatal_stops_starts_and_drains_inflight_success(self):
        output = self.root / "fatal-parallel.json"
        second = dict(self.row, id=13, title="Second", path="articles/second.md")
        (self.root / second["path"]).write_text("Another meaningful article.", encoding="utf-8")
        fatal_started = threading.Event()
        second_started = threading.Event()
        release_second = threading.Event()
        calls = []

        def transport(request):
            calls.append(threading.get_ident())
            if len(calls) == 2:
                second_started.set()
                if not release_second.wait(3):
                    raise AssertionError("in-flight request was not released")
                return {"text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                            "missing_topics": [], "notes": ""})}
            fatal_started.set()
            raise label.FatalCodexError("subscription limit")

        with mock.patch.object(label, "article_rows", return_value=[self.row, second]):
            result = []
            runner = threading.Thread(target=lambda: result.append(label.main(
                ["--run", "--workers", "2", "--output", str(output)], transport=transport)))
            runner.start()
            try:
                self.assertTrue(fatal_started.wait(3))
                self.assertTrue(second_started.wait(3))
                release_second.set()
            finally:
                release_second.set()
                runner.join(5)
        self.assertFalse(runner.is_alive())
        self.assertEqual(result, [2])
        self.assertEqual(len(calls), 2)
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["summary"]["stop_reason"], "subscription_or_cli_error")
        self.assertEqual(next(row for row in saved["articles"] if row["id"] == 13)["status"], "classified")
        self.assertFalse(Path(str(output) + ".lock").exists())

    def test_four_workers_fatal_stops_new_starts_and_drains_inflight(self):
        output = self.root / "fatal-four-workers.json"
        rows = [self.row]
        for article_id in range(13, 17):
            row = dict(self.row, id=article_id, title=f"Article {article_id}",
                       path=f"articles/{article_id}.md")
            (self.root / row["path"]).write_text("Another meaningful article.", encoding="utf-8")
            rows.append(row)
        all_started = threading.Event()
        release_successes = threading.Event()
        draining = threading.Event()
        lock = threading.Lock()
        calls = []
        wait_calls = []
        original_wait = label.wait

        def transport(request):
            with lock:
                calls.append(request)
                ordinal = len(calls)
                if ordinal == 4:
                    all_started.set()
            if not all_started.wait(3):
                raise AssertionError("initial four requests were not dispatched")
            if ordinal == 1:
                raise label.FatalCodexError("subscription limit")
            if not release_successes.wait(3):
                raise AssertionError("in-flight requests were not drained")
            return {"text": json.dumps({"status": "classified", "tags": [], "nature": [],
                                        "missing_topics": [], "notes": ""})}

        def track_drain(futures, return_when):
            wait_calls.append(True)
            if len(wait_calls) > 1:
                draining.set()
            return original_wait(futures, return_when=return_when)

        with mock.patch.object(label, "article_rows", return_value=rows), \
             mock.patch.object(label, "wait", side_effect=track_drain):
            result = []
            runner = threading.Thread(target=lambda: result.append(label.main(
                ["--run", "--workers", "4", "--output", str(output)], transport=transport)))
            runner.start()
            try:
                self.assertTrue(draining.wait(3), "coordinator should drain outstanding requests after fatal error")
                release_successes.set()
            finally:
                release_successes.set()
                runner.join(5)
        self.assertFalse(runner.is_alive())
        self.assertEqual(result, [2])
        self.assertEqual(len(calls), 4)
        saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(saved["summary"]["stop_reason"], "subscription_or_cli_error")
        self.assertEqual(sum(attempt["state"] == "complete" for attempt in saved["attempts"]), 3)
        self.assertFalse(Path(str(output) + ".lock").exists())

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


class AssignmentDataTests(unittest.TestCase):
    def test_every_article_has_nonempty_valid_tags(self):
        here = Path(__file__).resolve().parent
        vocabulary = json.loads((here / "tags.json").read_bytes())
        valid_ids = {tag["id"] for tag in vocabulary["tags"]}
        assignments = json.loads((here / "assignments.json").read_bytes())
        self.assertEqual(set(assignments), {"articles"})
        self.assertTrue(assignments["articles"])
        for article_id, record in assignments["articles"].items():
            with self.subTest(article_id=article_id):
                self.assertEqual(set(record), {"tags"})
                self.assertIsInstance(record["tags"], list)
                self.assertTrue(record["tags"])
                self.assertEqual(record["tags"], sorted(set(record["tags"])))
                self.assertLessEqual(set(record["tags"]), valid_ids)


if __name__ == "__main__":
    unittest.main()
