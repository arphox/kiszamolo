"""Offline safety checks for shared article and Codex runner helpers."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from bs4 import BeautifulSoup

from blog.enrichment.automation import common


class CommonHelpersTests(unittest.TestCase):
    def test_local_path_rejects_paths_outside_article_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(common.ClassificationError, "kilép"):
                common.local_path(root / "blog/content-mirror/articles", "../../outside.md", root / "blog/content-mirror/articles")

    def test_load_json_rejects_duplicate_object_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.json"
            path.write_text('{"x":1,"x":2}', encoding="utf-8")
            with self.assertRaisesRegex(common.ClassificationError, "Ismétlődő"):
                common.load_json(path)

    def test_codex_runner_is_ephemeral_read_only_and_copies_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "chart.png"
            image.write_bytes(b"image")

            def run(command, **kwargs):
                output_path = Path(command[command.index("--output-last-message") + 1])
                output_path.write_text('{"ok":true}', encoding="utf-8")
                copied = Path(command[command.index("--image") + 1])
                self.assertTrue(copied.is_file())
                self.assertNotEqual(copied, image)
                self.assertIn("--ephemeral", command)
                self.assertIn("--ignore-user-config", command)
                self.assertIn("--skip-git-repo-check", command)
                self.assertIn("--sandbox", command)
                self.assertIn("read-only", command)
                self.assertNotIn("--resume", command)
                self.assertEqual(command[1], "exec")
                self.assertEqual(command[command.index("--model") + 1], common.MODEL)
                self.assertIn(f"model_reasoning_effort='{common.REASONING}'", command)
                self.assertIn("forced_login_method='chatgpt'", command)
                for name in ("shell_tool", "multi_agent", "apps", "hooks"):
                    self.assertIn(name, command[command.index("--disable") + 1::2])
                schema_path = Path(command[command.index("--output-schema") + 1])
                self.assertEqual(json.loads(schema_path.read_text(encoding="utf-8")), {"type": "object"})
                self.assertEqual(kwargs["cwd"], copied.parent)
                self.assertEqual(kwargs["input"], "article")
                for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_THREAD_ID"):
                    self.assertNotIn(name, kwargs["env"])
                return mock.Mock(returncode=0, stdout='{"type":"turn.completed"}\n', stderr="")

            with mock.patch.object(common.shutil, "which", return_value="codex.exe"), \
                    mock.patch.object(common.subprocess, "run", side_effect=run) as called:
                result = common.codex_exec({"prompt": "article", "images": [image], "schema": {"type": "object"}})
            self.assertEqual(result, {"ok": True})
            called.assert_called_once()
            self.assertFalse(Path(called.call_args.kwargs["cwd"]).exists())

    def test_codex_nonzero_exit_keeps_plain_stderr_diagnostic(self):
        failure = mock.Mock(returncode=1, stdout="", stderr="ERROR: login token expired")
        with mock.patch.object(common.shutil, "which", return_value="codex.exe"), \
                mock.patch.object(common.subprocess, "run", return_value=failure):
            with self.assertRaises(common.ClassificationError) as raised:
                common.codex_exec({"prompt": "article", "images": [], "schema": {}})
        self.assertIn("login token expired", str(raised.exception))

    def test_article_row_uses_id_filename_without_reading_mirror_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            articles = root / "blog/content-mirror/articles"
            articles.mkdir(parents=True)
            row = {"id": 42, "type": "post", "title": "T", "url": "https://example.test"}
            common.catalog.write_article(articles / "one [42].md", row, "# T\n\nBody")
            loaded, path = common.article_row(root, "42")
            self.assertEqual(loaded["id"], 42)
            self.assertEqual(path, articles / "one [42].md")
            (root / common.catalog.INDEX_PATH).write_text(
                "this is intentionally not valid JSON", encoding="utf-8")
            loaded, path = common.article_row(root, "42")
            self.assertEqual(loaded["id"], 42)
            self.assertEqual(path, articles / "one [42].md")
            (articles / "two [42].md").write_text("duplicate", encoding="utf-8")
            with self.assertRaisesRegex(common.ClassificationError, "nem olvasható"):
                common.article_row(root, "42")

    def test_image_sources_read_original_candidates_from_article_html(self):
        image = BeautifulSoup(
            '<img src="small.png" srcset="small.png 100w, chart,full.png 1200w">', "html.parser").img
        self.assertEqual(common.image_sources(image), ["chart,full.png", "small.png", "small.png"])

    def test_decorative_replacement_only_changes_registered_sources(self):
        smile = "https://kiszamolo.hu/wp-includes/images/smilies/icon_smile.gif"
        body = (f"BODY\n![:)]({smile})\n<img src=\"{smile}\">\n"
                "![](https://example.test/unknown.gif)\n![](assets/ad.gif)")
        updated = common.replace_decorative_images(body, {smile: ":)"})
        self.assertEqual(updated.count(":)"), 2)
        self.assertIn("https://example.test/unknown.gif", updated)
        self.assertIn("assets/ad.gif", updated)

    def test_assignment_lock_serializes_separate_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acquired = root / "acquired.txt"
            source = ("from pathlib import Path\nimport sys\n"
                      "from blog.enrichment.automation.common import assignment_lock\n"
                      "p = Path(sys.argv[1])\n"
                      "with assignment_lock(p):\n"
                      "    (p / 'acquired.txt').write_text('ok')\n")
            with common.assignment_lock(root):
                self.assertTrue((root / ".local-cache/locks/blog-assignments.lock").is_file())
                child = subprocess.Popen([sys.executable, "-c", source, str(root)],
                                         cwd=common.ROOT, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True)
                time.sleep(0.1)
                self.assertFalse(acquired.exists())
            _stdout, stderr = child.communicate(timeout=15)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertEqual(acquired.read_text(encoding="utf-8"), "ok")

    def test_codex_runner_rejects_tool_events_even_when_result_file_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "image.png"
            image.write_bytes(b"png")

            def run(command, **_kwargs):
                Path(command[command.index("--output-last-message") + 1]).write_text('{"ok":true}', encoding="utf-8")
                return mock.Mock(returncode=0, stdout='{"type":"item.completed","item":{"type":"tool_call"}}\n', stderr="")

            with mock.patch.object(common.shutil, "which", return_value="codex.exe"), \
                    mock.patch.object(common.subprocess, "run", side_effect=run):
                with self.assertRaisesRegex(common.ClassificationError, "tiltott eszközt"):
                    common.codex_exec({"prompt": "article", "images": [image], "schema": {}})

    def test_codex_runner_preserves_bounded_quota_diagnostic(self):
        failure = mock.Mock(returncode=1, stdout='{"type":"error","message":"rate limit reached"}\n',
                            stderr="x" * 3000)
        with mock.patch.object(common.shutil, "which", return_value="codex.exe"), \
                mock.patch.object(common.subprocess, "run", return_value=failure):
            with self.assertRaises(common.ClassificationError) as raised:
                common.codex_exec({"prompt": "article", "images": [], "schema": {}})
        self.assertIn("rate limit reached", str(raised.exception))
        self.assertLessEqual(len(str(raised.exception)), 1100)

    def test_codex_timeout_is_a_single_technical_failure(self):
        with mock.patch.object(common.shutil, "which", return_value="codex.exe"), \
                mock.patch.object(common.subprocess, "run", side_effect=common.subprocess.TimeoutExpired("codex", 600)):
            with self.assertRaisesRegex(common.ClassificationError, "időtúllépés"):
                common.codex_exec({"prompt": "article", "images": [], "schema": {}})


if __name__ == "__main__":
    unittest.main()
