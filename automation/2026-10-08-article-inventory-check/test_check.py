import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location("inventory_check", Path(__file__).with_name("check.py"))
check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = check
spec.loader.exec_module(check)


def post(number):
    return dict(id=number, status="publish", type="post", date="2026-10-08T12:00:00",
                link=f"https://kiszamolo.hu/post-{number}/", title={"rendered": "A &amp; <b>B</b>"})


class InventoryTests(unittest.TestCase):
    def test_complete_pagination_and_final_probe(self):
        calls = []
        def fetch(page, size):
            calls.append((page, size))
            if size == 1:
                return [post(1)], 101, 101
            return ([post(i) for i in range(1, 101)] if page == 1 else [post(101)]), 101, 2
        with patch.object(check.time, "sleep"):
            web = check.load_web(fetch)
        self.assertEqual(len(web), 101)
        self.assertEqual(calls, [(1, 100), (2, 100), (1, 1)])
        self.assertEqual(next(iter(web.values())).title, "A & B")

    def test_rejects_incomplete_and_duplicate_pages(self):
        for posts, total in [([post(1)], 2), ([post(1), post(1)], 2)]:
            with self.subTest(total=total), self.assertRaises(ValueError):
                check.load_web(lambda page, size: (posts, total, 1))

    def test_rejects_changed_count_on_final_probe(self):
        with self.assertRaisesRegex(ValueError, "changed"):
            check.load_web(lambda page, size: ([post(1)], 1, 1) if size == 100 else ([post(1)], 2, 2))

    def test_rejects_changed_count_during_pagination(self):
        def fetch(page, size):
            return ([post(i) for i in range(1, 101)], 101, 2) if page == 1 else ([post(101), post(102)], 102, 2)
        with patch.object(check.time, "sleep"), self.assertRaisesRegex(ValueError, "changed"):
            check.load_web(fetch)

    def test_transient_http_retries_are_bounded(self):
        error = HTTPError(check.API, 503, "Unavailable", {}, None)
        with patch.object(check, "urlopen", side_effect=error) as request, patch.object(check.time, "sleep") as sleep, self.assertRaises(HTTPError):
            check.fetch_page(1, 100)
        self.assertEqual(request.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        error = HTTPError(check.API, 404, "Missing", {}, None)
        with patch.object(check, "urlopen", side_effect=error) as request, self.assertRaises(HTTPError):
            check.fetch_page(1, 100)
        self.assertEqual(request.call_count, 1)

    def test_normalization_is_conservative(self):
        self.assertEqual(check.normalize_url("https://KISZAMOLO.HU/Case"), "https://kiszamolo.hu/Case/")
        self.assertNotEqual(check.normalize_url("https://kiszamolo.hu/Case"), check.normalize_url("https://kiszamolo.hu/case"))
        for url in ["https://example.com/a", "https://kiszamolo.hu/a?x=1", "https://kiszamolo.hu/a#b"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                check.normalize_url(url)

    def test_comparison_only_stats_paths(self):
        article = lambda n: check.Article(str(n), "2026-10-08T12:00:00", f"https://kiszamolo.hu/{n}/", Path(f"/{n}.txt"))
        local = {str(n): article(n) for n in [1, 2, 3]}
        web = {str(n): article(n) for n in [1, 2, 4]}
        with patch.object(Path, "is_file", side_effect=[True, False, False]), patch.object(Path, "open", side_effect=AssertionError("Article file read")):
            report = check.compare(local, web)
        self.assertEqual((report.common_count, report.common_files_present), (2, 1))
        self.assertEqual([a.title for a in report.web_only], ["4"])
        self.assertEqual([a.title for a in report.missing_files], ["2", "3"])
        self.assertEqual([a.title for a in report.local_only], ["3"])
        output = io.StringIO()
        check.render_report(report, output)
        self.assertNotIn("\033", output.getvalue())
        self.assertIn("33.33%", output.getvalue())

    def test_invalid_local_paths_and_duplicate_urls(self):
        record = dict(title="Test", published_at="2026-10-08T12:00:00", url="https://kiszamolo.hu/a/", path="2026/10/a.txt")
        for path in ["../a.txt", "/a.txt", "C:/a.txt", "2026/../a.txt"]:
            with self.subTest(path=path), patch.object(Path, "open", return_value=io.StringIO(json.dumps(record | {"path": path}))), self.assertRaises(ValueError):
                check.load_local()
        lines = json.dumps(record) + "\n" + json.dumps(record)
        with patch.object(Path, "open", return_value=io.StringIO(lines)), self.assertRaisesRegex(ValueError, "Duplicate"):
            check.load_local()

    def test_failure_does_not_render_report(self):
        with patch.object(check, "load_local", return_value={}), patch.object(check, "load_web", side_effect=ValueError("Incomplete API")), patch.object(check, "render_report") as render, patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(check.main(), 1)
            render.assert_not_called()


if __name__ == "__main__":
    unittest.main()
