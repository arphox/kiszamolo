"""Offline checks for article labeling and canonical assignment updates."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from tagging import label


class LabelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=os.environ.get("TAGGING_TEST_TMP"))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "tagging").mkdir()
        (self.root / "articles/2026/10").mkdir(parents=True)
        (self.root / "articles/assets").mkdir()
        self.vocabulary = {"tags": [dict(id=tid, name=tid, rule="Meaningful topic")
                                     for tid in ("bank-deposit", "costs", "investing")]}
        self.write_json("tagging/tags.json", self.vocabulary)
        self.rows = [dict(id=aid, type="post", title=f"Article {aid}", path=f"2026/10/{aid}.md")
                     for aid in (10, 20, 30, 40)]
        self.write_index()
        for row in self.rows:
            (self.root / "articles" / row["path"]).write_text(
                f"# {row['title']}\n\nBODY_ONLY_FOR_{row['id']}", encoding="utf-8")
        self.path = self.root / "tagging/assignments.json"
        self.path.write_bytes(b'{\n  "articles": {\n    "10": {"tags":["investing"]},\n    "30": {"tags":["costs"]}\n  }\n}\n')
        self.before = self.path.read_bytes()
        self.response = {"tags": [{"id": "costs", "evidence": "A konkrét költségeket magyarázza."},
                                  {"id": "bank-deposit", "evidence": "A bankbetét működését ismerteti."}],
                         "review_reason": None}
        self.transport = mock.Mock(return_value=self.response)

    def write_json(self, relative, data):
        (self.root / relative).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def write_index(self):
        (self.root / "articles/index.jsonl").write_text(
            "\n".join(json.dumps(row) for row in self.rows) + "\n", encoding="utf-8")

    def body(self, text):
        (self.root / "articles/2026/10/20.md").write_text(text, encoding="utf-8")

    def run_article(self, aid=20, *, force=False):
        return label.label_article(aid, root=self.root, transport=self.transport, force=force)

    def test_saves_only_requested_article_with_canonical_format(self):
        result = self.run_article()
        self.assertTrue(result["saved"])
        self.assertEqual(result["tags"], ["bank-deposit", "costs"])
        expected = self.before.replace(b'    "30":', b'    "20": {"tags":["bank-deposit","costs"]},\n    "30":')
        self.assertEqual(self.path.read_bytes(), expected)
        self.transport.assert_called_once()
        request = self.transport.call_args.args[0]
        self.assertIn("BODY_ONLY_FOR_20", request["prompt"])
        self.assertNotIn("BODY_ONLY_FOR_10", request["prompt"])
        self.assertNotIn("BODY_ONLY_FOR_30", request["prompt"])
        self.assertEqual(request["images"], [])
        self.assertEqual(set(request["schema"]["properties"]), {"tags", "review_reason"})

    def test_existing_assignment_skips_model_and_write(self):
        result = self.run_article(10)
        self.assertFalse(result["saved"])
        self.assertEqual(result["tags"], ["investing"])
        self.transport.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_force_relabels_existing_assignment(self):
        result = self.run_article(10, force=True)
        self.assertTrue(result["saved"])
        self.assertEqual(result["tags"], ["bank-deposit", "costs"])
        records = json.loads(self.path.read_bytes())["articles"]
        self.assertEqual(records["10"], {"tags": ["bank-deposit", "costs"]})
        self.assertEqual(records["30"], {"tags": ["costs"]})
        self.transport.assert_called_once()

    def test_force_model_and_review_failures_preserve_existing_tags(self):
        self.transport.side_effect = label.LabelError("Model failure")
        with self.assertRaises(label.LabelError):
            self.run_article(10, force=True)
        self.assertEqual(self.path.read_bytes(), self.before)

        self.transport.side_effect = None
        self.transport.return_value = dict(self.response, review_reason="Humán döntés szükséges.")
        with self.assertRaises(label.ReviewRequired):
            self.run_article(10, force=True)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_force_blocks_concurrent_same_article_change(self):
        def transport(request):
            current = json.loads(self.path.read_bytes())
            current["articles"]["10"] = {"tags": ["costs"]}
            self.write_json("tagging/assignments.json", current)
            return self.response
        self.transport.side_effect = transport
        with self.assertRaisesRegex(label.LabelError, "közben megváltozott"):
            self.run_article(10, force=True)
        self.assertEqual(json.loads(self.path.read_bytes())["articles"]["10"], {"tags": ["costs"]})

    def test_force_preserves_other_article_changes(self):
        def transport(request):
            current = json.loads(self.path.read_bytes())
            current["articles"]["30"] = {"tags": ["investing"]}
            current["articles"]["40"] = {"tags": ["bank-deposit"]}
            self.write_json("tagging/assignments.json", current)
            return self.response
        self.transport.side_effect = transport
        self.run_article(10, force=True)
        records = json.loads(self.path.read_bytes())["articles"]
        self.assertEqual(records["10"], {"tags": ["bank-deposit", "costs"]})
        self.assertEqual(records["30"], {"tags": ["investing"]})
        self.assertEqual(records["40"], {"tags": ["bank-deposit"]})

    def test_incomplete_article_needs_review_before_model(self):
        self.rows[0]["acquisition_status"] = "incomplete"
        self.rows[1]["acquisition_status"] = "incomplete"
        self.write_index()
        for force in (False, True):
            with self.subTest(force=force), self.assertRaises(label.ReviewRequired):
                self.run_article(20 if not force else 10, force=force)
        self.transport.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_missing_article_stops_before_model_or_write(self):
        for aid in (999, 20):
            with self.subTest(aid=aid):
                if aid == 20:
                    (self.root / "articles/2026/10/20.md").unlink()
                with self.assertRaises(label.LabelError):
                    self.run_article(aid)
                self.transport.assert_not_called()
                self.assertEqual(self.path.read_bytes(), self.before)

    def test_duplicate_index_record_and_escaping_path_are_rejected(self):
        original = self.rows.copy()
        for rows in (original + [original[1]], [dict(original[1], path="../outside.md")]):
            with self.subTest(rows=rows):
                self.rows = rows
                self.write_index()
                with self.assertRaises(label.LabelError):
                    self.run_article()
                self.transport.assert_not_called()
                self.assertEqual(self.path.read_bytes(), self.before)

    def test_empty_and_unavailable_content_need_review(self):
        self.body("---\nmetadata: value\n---\n# Article 20\n")
        with self.assertRaises(label.ReviewRequired):
            self.run_article()
        self.rows[1]["derived_view_available"] = False
        self.write_index()
        with self.assertRaises(label.ReviewRequired):
            self.run_article()
        self.transport.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_bad_model_results_do_not_change_assignments(self):
        invalid = [None, {}, {"tags": [], "review_reason": None},
                   {"tags": [{"id": "unknown", "evidence": "Reason"}], "review_reason": None},
                   {"tags": [{"id": "costs", "evidence": " "}], "review_reason": None},
                   {"tags": [self.response["tags"][0]] * 2, "review_reason": None},
                   dict(self.response, review_reason=" "), dict(self.response, extra="unwanted")]
        for response in invalid:
            with self.subTest(response=response):
                self.transport.return_value = response
                with self.assertRaises(label.LabelError):
                    self.run_article()
                self.assertEqual(self.path.read_bytes(), self.before)

    def test_review_reason_blocks_even_nonempty_tags(self):
        self.transport.return_value = dict(self.response, review_reason="A fogalomtár bővítése szükséges.")
        with self.assertRaisesRegex(label.ReviewRequired, "fogalomtár"):
            self.run_article()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_model_failure_is_not_retried(self):
        self.transport.side_effect = label.LabelError("Failure")
        with self.assertRaises(label.LabelError):
            self.run_article()
        self.transport.assert_called_once()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_oversized_input_stops_without_truncating_or_calling(self):
        with mock.patch.object(label, "MAX_REQUEST_BYTES", 1), self.assertRaises(label.ReviewRequired):
            self.run_article()
        self.transport.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_local_images_prefer_original_and_are_deduplicated(self):
        for name in ("thumb.png", "full.png"):
            (self.root / "articles/assets" / name).write_bytes(b"image")
        self.body('[![Chart](../../assets/thumb.png)](../../assets/full.png)\n\n'
                  '![Again](../../assets/full.png)\n\nBODY')
        self.run_article()
        self.assertEqual(self.transport.call_args.args[0]["images"], [self.root / "articles/assets/full.png"])

    def test_html_srcset_prefers_largest_available_image(self):
        for name in ("thumb.png", "full.png"):
            (self.root / "articles/assets" / name).write_bytes(b"image")
        self.body('<img src="../../assets/thumb.png" srcset="../../assets/thumb.png 100w, ../../assets/full.png 800w">')
        self.run_article()
        self.assertEqual(self.transport.call_args.args[0]["images"], [self.root / "articles/assets/full.png"])

    def test_bracketed_alt_text_cannot_hide_missing_image(self):
        self.body('Article body\n\n![Chart [2026]](../../assets/missing.png)')
        with self.assertRaisesRegex(label.ReviewRequired, "Hiányzó helyi kép"):
            self.run_article()
        self.transport.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_original_source_resolves_srcset_lost_in_markdown(self):
        self.body('Article body\n\n![Chart [2026]](../../assets/small.png)')
        source = self.root / "raw_source/content-mirror/records/posts/20"
        source.mkdir(parents=True)
        for name in ("small.png", "full.png", "chart,full.png"):
            (self.root / "articles/assets" / name).write_bytes(b"image")
        self.rows[1].update(source_record="records/posts/20", url="https://kiszamolo.hu/test/")
        self.write_index()
        self.write_json("raw_source/content-mirror/state.json", {"assets": {
            f"https://example.test/{name}": {"path": f"assets/{name}"} for name in ("small.png", "full.png", "chart,full.png")}})
        for markup, preferred in (
            ('<img src="https://example.test/small.png" srcset="https://example.test/small.png 100w, https://example.test/full.png 1200w">', "full.png"),
            ('<img src="https://example.test/small.png" srcset="https://example.test/small.png 100w, https://example.test/chart,full.png 1200w">', "chart,full.png"),
            ('<picture><source srcset="https://example.test/full.png 2x"><img src="https://example.test/small.png"></picture>', "full.png")):
            with self.subTest(markup=markup):
                self.path.write_bytes(self.before)
                source.joinpath("view-source.html").write_text(markup, encoding="utf-8")
                self.run_article()
                self.assertEqual(self.transport.call_args.args[0]["images"], [self.root / "articles/assets" / preferred])

    def test_failed_featured_image_requires_review_even_without_markdown_image(self):
        self.rows[1]["featured_media_error"] = "HTTP 503"
        self.write_index()
        with self.assertRaisesRegex(label.ReviewRequired, "kiemelt kép"):
            self.run_article()
        self.transport.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_known_remote_decorations_do_not_require_images(self):
        for body in (
            'BODY\n![:)](https://kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif)\n'
            '[![](https://kiszamolo.hu/hirdetes2.gif)](https://kiszamolo.hu/penzugyi-tanacsadas/)',
            'BODY\n<img src="//kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif" alt=":-)">\n'
            '<a href="https://kiszamolo.hu/penzugyi-tanacsadas/"><img src="//kiszamolo.hu/hirdetes2.gif" title="1 > 0"></a>',
        ):
            with self.subTest(body=body):
                self.path.write_bytes(self.before)
                self.body(body)
                self.run_article()
                request = self.transport.call_args.args[0]
                self.assertEqual(request["images"], [])
                self.assertIn("BODY\n:)", request["prompt"])
                self.assertNotIn("hirdetes2.gif", request["prompt"])
                self.assertNotIn("icon_smile.gif", request["prompt"])

    def test_registered_local_decorations_do_not_require_images(self):
        source = self.root / "raw_source/content-mirror/records/posts/20"
        source.mkdir(parents=True)
        source.joinpath("view-source.html").write_text(
            '<img src="//kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif">'
            '<img src="//kiszamolo.hu/hirdetes2.gif">', encoding="utf-8")
        self.rows[1].update(source_record="records/posts/20", url="https://kiszamolo.hu/test/")
        self.write_index()
        self.write_json("raw_source/content-mirror/state.json", {"assets": {
            "https://kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif": {"path": "assets/smile.gif"},
            "https://kiszamolo.hu/hirdetes2.gif": {"path": "assets/ad.gif"},
        }})
        for body in ('BODY\n![:)](../../assets/smile.gif)\n![](../../assets/ad.gif)',
                     'BODY\n<img src="../../assets/smile.gif">\n<img src="../../assets/ad.gif">'):
            with self.subTest(body=body):
                self.path.write_bytes(self.before)
                self.body(body)
                self.run_article()
                request = self.transport.call_args.args[0]
                self.assertEqual(request["images"], [])
                self.assertIn("BODY\n:)", request["prompt"])
                self.assertNotIn("assets/", request["prompt"])

    def test_decorations_do_not_hide_unknown_or_missing_images(self):
        smile = '![:)](https://kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif)'
        for image in ('![Chart](../../assets/missing.png)',
                      '![:)](https://example.test/wp-includes/images/smilies/icon_smile.gif)',
                      '![](../../assets/hirdetes2.gif)',
                      '<img src="https://kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif" '
                      'srcset="https://example.test/chart.png 2x">',
                      '<picture><source srcset="https://example.test/chart.png 2x">'
                      '<img src="https://kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif"></picture>',
                      '<a href="https://example.test/chart.png">'
                      '<img src="https://kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif"></a>',
                      f'[{smile}](https://example.test/chart.png)'):
            with self.subTest(image=image):
                self.body(f"BODY\n{smile}\n{image}")
                with self.assertRaises(label.ReviewRequired):
                    self.run_article()
                self.assertEqual(self.path.read_bytes(), self.before)
        self.transport.assert_not_called()

    def test_missing_external_unsupported_images_and_attachments_need_review(self):
        cases = ['![Chart](../../assets/missing.png)', '![Chart](https://example.test/chart.png)',
                 '![Chart](../../assets/chart.gif)', '[File](../../assets/report.pdf)',
                 '[File](../../assets/report.rtf)',
                 '[Video](https://youtu.be/abc)', '<iframe src="https://example.test/embed"></iframe>']
        (self.root / "articles/assets/chart.gif").write_bytes(b"GIF89a")
        for body in cases:
            with self.subTest(body=body):
                self.body(body)
                with self.assertRaises(label.ReviewRequired):
                    self.run_article()
                self.assertEqual(self.path.read_bytes(), self.before)
        self.transport.assert_not_called()

    def test_unknown_external_embed_from_index_needs_review(self):
        self.rows[1]["embed_urls"] = ["https://example.test/player"]
        self.write_index()
        with self.assertRaises(label.ReviewRequired):
            self.run_article()
        self.transport.assert_not_called()

    def test_promotional_content_is_available_for_the_model_to_judge(self):
        image = self.root / "articles/assets/promo.png"
        image.write_bytes(b"image")
        self.body('BODY\n\nHa szeretnéd tudni, hogy új poszt jelent meg a blogban\n![Ad](../../assets/promo.png)')
        self.run_article()
        request = self.transport.call_args.args[0]
        self.assertEqual(request["images"], [image])
        self.assertIn("promo.png", request["prompt"])

    def test_image_names_never_bypass_media_checks(self):
        self.body('BODY\n\n![Chart](../../assets/Biztositas.png)')
        with self.assertRaisesRegex(label.ReviewRequired, "Hiányzó helyi kép"):
            self.run_article()
        image = self.root / "articles/assets/Biztositas.png"
        image.write_bytes(b"image")
        self.run_article()
        self.assertEqual(self.transport.call_args.args[0]["images"], [image])

    def test_picture_and_comma_urls_survive_variant_selection(self):
        (self.root / "articles/assets/small.png").write_bytes(b"image")
        (self.root / "articles/assets/chart,full.png").write_bytes(b"image")
        for markup in (
            '<img src="../../assets/small.png" srcset="../../assets/small.png 100w, ../../assets/chart,full.png 1200w">',
            '<picture><source srcset="../../assets/chart,full.png 2x"><img src="../../assets/small.png"></picture>'):
            with self.subTest(markup=markup):
                images = label.article_images(markup, self.root / "articles/2026/10/20.md", self.root / "articles")
                self.assertEqual(images, [self.root / "articles/assets/chart,full.png"])

    def test_reloads_assignments_and_preserves_other_changes(self):
        def transport(request):
            current = json.loads(self.path.read_bytes())
            current["articles"]["30"] = {"tags": ["investing"]}
            current["articles"]["40"] = {"tags": ["bank-deposit"]}
            self.write_json("tagging/assignments.json", current)
            return self.response
        self.transport.side_effect = transport
        self.run_article()
        result = json.loads(self.path.read_bytes())["articles"]
        self.assertEqual(result["30"], {"tags": ["investing"]})
        self.assertEqual(result["40"], {"tags": ["bank-deposit"]})
        self.assertEqual(result["20"]["tags"], ["bank-deposit", "costs"])

    def test_an_assignment_created_during_model_call_wins(self):
        def transport(request):
            current = json.loads(self.path.read_bytes())
            current["articles"]["20"] = {"tags": ["investing"]}
            self.write_json("tagging/assignments.json", current)
            return self.response
        self.transport.side_effect = transport
        result = self.run_article()
        self.assertFalse(result["saved"])
        self.assertEqual(result["tags"], ["investing"])

    def test_changed_vocabulary_prevents_save(self):
        def transport(request):
            self.vocabulary["tags"][0]["rule"] = "Changed meaning"
            self.write_json("tagging/tags.json", self.vocabulary)
            return self.response
        self.transport.side_effect = transport
        with self.assertRaisesRegex(label.LabelError, "fogalomtár"):
            self.run_article()
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_failed_atomic_replace_preserves_file_and_releases_lock(self):
        with mock.patch.object(label.os, "replace", side_effect=PermissionError("Denied")):
            with self.assertRaises(PermissionError):
                self.run_article()
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(list((self.root / "tagging").glob("*.tmp")), [])
        self.assertTrue(self.run_article()["saved"])

    def test_two_simultaneous_writers_preserve_both_articles(self):
        barrier = threading.Barrier(2)
        def transport(request):
            barrier.wait(timeout=5)
            return self.response
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(label.label_article, aid, self.root, transport) for aid in (20, 40)]
            for future in futures:
                self.assertTrue(future.result(timeout=15)["saved"])
        self.assertEqual(set(json.loads(self.path.read_bytes())["articles"]), {"10", "20", "30", "40"})

    def test_corrupt_canonical_file_is_not_overwritten(self):
        for text in ('{"articles":{"10":{"tags":[]}}}',
                     '{"articles":{"10":{"tags":["unknown"]}}}',
                     '{"articles":{},"articles":{}}'):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(label.LabelError):
                    self.run_article()
                self.assertEqual(self.path.read_text(encoding="utf-8"), text)
        self.transport.assert_not_called()

    def test_cli_exit_codes_and_messages(self):
        for response, expected in [(self.response, 0), (dict(self.response, review_reason="Question"), 2)]:
            self.path.write_bytes(self.before)
            self.transport.return_value = response
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = label.main(["20"], root=self.root, transport=self.transport)
            self.assertEqual(code, expected)
            self.assertIn("Mentve" if code == 0 else "nem mentettem", out.getvalue() + err.getvalue())
        self.path.write_bytes(self.before)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(label.main(["999"], root=self.root, transport=self.transport), 1)
        self.assertEqual(self.path.read_bytes(), self.before)

    def test_cli_force_relabels_existing_assignment(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = label.main(["10", "--force"], root=self.root, transport=self.transport)
        self.assertEqual(code, 0)
        self.assertIn("Mentve: 10", out.getvalue())
        self.transport.assert_called_once()

    def test_codex_receives_one_isolated_request_and_local_image_copy(self):
        image = self.root / "articles/assets/chart,full.png"
        image.write_bytes(b"image-data")
        request = label.make_request("Title", "ONLY_ONE_BODY", [image], self.vocabulary)
        invocations = []
        def run(command, **kwargs):
            invocations.append((command, kwargs))
            copied = Path(command[command.index("--image") + 1])
            self.assertEqual(copied.read_bytes(), image.read_bytes())
            self.assertNotEqual(copied, image)
            self.assertEqual(copied.parent, kwargs["cwd"])
            self.assertFalse(copied.is_relative_to(self.root))
            result = Path(command[command.index("--output-last-message") + 1])
            result.write_text(json.dumps(self.response), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed"}', stderr="")
        with mock.patch.object(label.subprocess, "run", side_effect=run), mock.patch.dict(
                os.environ, {"OPENAI_API_KEY": "secret", "CODEX_API_KEY": "secret", "CODEX_THREAD_ID": "thread"}):
            self.assertEqual(label.codex_exec(request, executable="codex"), self.response)
        self.assertEqual(len(invocations), 1)
        command, kwargs = invocations[0]
        self.assertEqual(command[1], "exec")
        for flag in ("--ephemeral", "--ignore-user-config", "--output-schema"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--model") + 1], "gpt-6.1-sol")
        self.assertIn("model_reasoning_effort='medium'", command)
        self.assertIn("forced_login_method='chatgpt'", command)
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(kwargs["input"], request["prompt"])
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_THREAD_ID"):
            self.assertNotIn(key, kwargs["env"])
        self.assertFalse(Path(kwargs["cwd"]).exists())

    def test_cli_failure_or_tool_use_never_returns_a_result(self):
        request = label.make_request("Title", "BODY", [], self.vocabulary)
        for failure in ("timeout", "exit", "tool", "missing", "invalid-json"):
            with self.subTest(failure=failure):
                def run(command, **kwargs):
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(command, 1)
                    if failure != "missing":
                        Path(command[command.index("--output-last-message") + 1]).write_text(
                            "{" if failure == "invalid-json" else json.dumps(self.response), encoding="utf-8")
                    return SimpleNamespace(returncode=1 if failure == "exit" else 0,
                                           stdout='{"type":"item.completed","item":{"type":"command_execution"}}' if failure == "tool" else "", stderr="")
                with mock.patch.object(label.subprocess, "run", side_effect=run):
                    with self.assertRaises((label.LabelError, OSError, ValueError)):
                        label.codex_exec(request, executable="codex")


class AssignmentDataTests(unittest.TestCase):
    def test_every_article_has_nonempty_valid_tags(self):
        here = Path(__file__).resolve().parent
        vocabulary = json.loads((here / "tags.json").read_bytes())
        valid_ids = {tag["id"] for tag in vocabulary["tags"]}
        assignments = json.loads((here / "assignments.json").read_bytes())
        self.assertTrue(assignments["articles"])
        label.validate_assignments(assignments, valid_ids)


if __name__ == "__main__":
    unittest.main()
