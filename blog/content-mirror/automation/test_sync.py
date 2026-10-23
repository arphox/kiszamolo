"""Focused tests for the minimal article update index."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from remote import Inventory, MissingMediaError, Response
import catalog
import sync


def post(number=1, *, modified="2020-01-01T00:00:00", body="<p>Original body.</p>",
         title=None, date="2019-12-31T22:00:00"):
    return {
        "id": number, "type": "post", "status": "publish",
        "date": date, "date_gmt": "2019-12-31T21:00:00",
        "modified": modified, "modified_gmt": modified,
        "link": f"https://kiszamolo.hu/post-{number}/",
        "title": {"rendered": title or f"Article {number}"},
        "content": {"rendered": body, "protected": False}, "featured_media": 0,
    }


class FakeClient:
    def __init__(self):
        self.page_calls = []
        self.page_html = {}
        self.page_errors = {}

    def request_bytes(self, url, **kwargs):
        self.page_calls.append(url)
        if url in self.page_errors:
            raise self.page_errors[url]
        return Response(self.page_html.get(url, b"<main><p>Fallback body.</p></main>"),
                        200, {}, url)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = FakeClient()
        self.records = [post()]
        self.detail_calls = []
        self.fail_details = False
        self.fail_media = False

    def tearDown(self):
        self.temp.cleanup()

    def inventory(self, client, kind, **kwargs):
        rows = [record for record in self.records if record["type"] + "s" == kind]
        fields = ("id", "type", "status", "modified", "modified_gmt", "date_gmt", "link")
        return Inventory([{field: record[field] for field in fields} for record in rows], [])

    def details(self, client, kind, ids, **kwargs):
        self.detail_calls.append((kind, list(ids)))
        if self.fail_details:
            raise OSError("temporary API failure")
        rows = [record for record in self.records
                if record["id"] in ids and record["type"] + "s" == kind]
        return Inventory(json.loads(json.dumps(rows)), [])

    def media(self, client, kind, ids, **kwargs):
        if self.fail_media:
            raise OSError("temporary media API failure")
        rows = [{"id": item_id, "media_type": "image",
                 "source_url": f"https://kiszamolo.hu/media-{item_id}.png",
                 "alt_text": "", "caption": {"rendered": ""}} for item_id in ids]
        return Inventory(rows, [])

    def run_update(self, **kwargs):
        return sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                             record_loader=self.details, metadata_loader=self.media,
                             progress=lambda _: None, **kwargs)

    def index(self):
        return catalog.load_index(self.root)

    def article(self, number=1):
        return catalog.find_article(self.root, number)

    def test_fresh_clone_writes_canonical_article_and_only_id_timestamp_index(self):
        report = self.run_update()
        self.assertEqual(report["changed_article_ids"], [1])
        self.assertEqual(self.detail_calls, [("posts", [1])])
        metadata, _ = catalog.read_article(self.article())
        self.assertEqual(self.index(), {"1": self.records[0]["modified_gmt"]})
        self.assertTrue((self.root / catalog.INDEX_PATH).is_file())
        self.assertFalse({"classification_pending", "content_classification_pending"} & set(metadata))
        articles = self.root / catalog.ARTICLES_PATH
        self.assertEqual(len(list(articles.rglob("*.md"))), 1)
        self.assertTrue(self.article().name.endswith(" [1].md"))

    def test_unchanged_run_does_not_open_article_or_fetch_full_record(self):
        self.run_update()
        self.detail_calls.clear()
        unchanged = self.article()
        original_read_text = Path.read_text

        def guarded_read_text(path, *args, **kwargs):
            if path.resolve() == unchanged.resolve():
                raise AssertionError("read unchanged article")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded_read_text):
            report = self.run_update()
        self.assertEqual(self.detail_calls, [])
        self.assertEqual(report["changed_article_ids"], [])

    def test_new_article_does_not_read_existing_article_bodies(self):
        self.run_update()
        unchanged = self.article()
        original_read_text = Path.read_text
        self.records.append(post(2))

        def guarded_read_text(path, *args, **kwargs):
            if path.resolve() == unchanged.resolve():
                raise AssertionError("read unchanged article")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded_read_text):
            report = self.run_update()
        self.assertEqual(report["changed_article_ids"], [2])

    def test_old_article_update_stores_exact_wordpress_gmt_value_in_both_places(self):
        self.run_update()
        self.records[0] = post(modified="2024-03-04T05:06:07")
        self.run_update()
        metadata, _ = catalog.read_article(self.article())
        self.assertEqual(self.index(), {"1": "2024-03-04T05:06:07"})
        self.assertEqual(metadata["modified_at_gmt"], "2024-03-04T05:06:07")

    def test_failed_detail_does_not_advance_index_and_next_run_retries(self):
        self.run_update()
        old_stamp = self.index()["1"]
        self.records[0] = post(modified="2025-01-01T00:00:00", body="<p>Updated.</p>")
        self.fail_details = True
        report = self.run_update()
        self.assertEqual(report["incomplete_records"], 1)
        self.assertEqual(self.index()["1"], old_stamp)
        self.fail_details = False
        self.run_update()
        self.assertEqual(self.index()["1"], "2025-01-01T00:00:00")

    def test_failed_index_save_does_not_leak_timestamp_into_later_record_save(self):
        self.run_update()
        old_stamp = self.index()["1"]
        self.records[0] = post(modified="2025-01-01T00:00:00")
        self.records.append(post(2))
        real_save = sync.save_index
        attempts = 0

        def fail_first_save(root, index):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("index is temporarily unavailable")
            return real_save(root, index)

        with patch.object(sync, "save_index", fail_first_save):
            report = self.run_update()
        self.assertEqual(self.index()["1"], old_stamp)
        self.assertIn("2", self.index())
        self.assertIn("1", {item["record"] for item in report["errors"]})

    def test_missing_article_file_is_restored_from_remote(self):
        self.run_update()
        old_article = self.article()
        old_article.unlink()
        report = self.run_update()
        self.assertTrue(self.article().is_file())
        self.assertEqual(report["incomplete_records"], 0)
        self.assertEqual(self.index()["1"], self.records[0]["modified_gmt"])

    def test_missing_index_rebuilds_once_from_article_front_matter(self):
        self.run_update()
        (self.root / catalog.INDEX_PATH).unlink()
        self.detail_calls.clear()
        self.run_update()
        self.assertEqual(self.detail_calls, [])
        self.assertEqual(self.index(), {"1": self.records[0]["modified_gmt"]})

    def test_exclusions_skip_download_and_prune_even_when_not_in_remote_census(self):
        self.records.append(post(104048))
        report = self.run_update()
        self.assertEqual(self.detail_calls, [("posts", [1])])
        self.assertIn("post:104048", report["excluded_records"])
        self.assertNotIn("104048", self.index())

        metadata = {"id": 104048, "type": "post", "title": "Excluded",
                    "url": "https://kiszamolo.hu/excluded/",
                    "published_at": "2010-01-01T00:00:00",
                    "published_at_gmt": "2010-01-01T00:00:00",
                    "modified_at": "2010-01-01T00:00:00",
                    "modified_at_gmt": "2010-01-01T00:00:00",
                    "derived_view_available": True}
        path = self.root / catalog.ARTICLES_PATH / "2010/01/2010-01-01 00.00 Excluded [104048].md"
        catalog.write_article(path, metadata, "# Excluded\n")
        index = self.index()
        index["104048"] = metadata["modified_at_gmt"]
        catalog.save_index(self.root, index)
        self.records = [post()]
        self.run_update()
        self.assertNotIn("104048", self.index())
        self.assertFalse(path.exists())

    def test_empty_api_body_uses_public_html_fallback(self):
        self.records = [post(body="")]
        self.client.page_html[self.records[0]["link"]] = b"<main><h1>Article 1</h1><p>From page.</p></main>"
        self.run_update()
        metadata, body = catalog.read_article(self.article())
        self.assertTrue(metadata["derived_view_available"])
        self.assertIn("From page", body)

    def test_transient_fallback_failure_preserves_old_article_and_index(self):
        self.records = [post(body="")]
        self.run_update()
        before = self.article().read_bytes()
        old_stamp = self.index()["1"]
        self.records[0] = post(modified="2024-01-01T00:00:00", body="")
        self.client.page_errors[self.records[0]["link"]] = OSError("offline")
        report = self.run_update()
        self.assertEqual(report["incomplete_records"], 1)
        self.assertEqual(self.article().read_bytes(), before)
        self.assertEqual(self.index()["1"], old_stamp)

    def test_permanent_fallback_404_is_saved_as_incomplete(self):
        self.records = [post(body="")]
        url = self.records[0]["link"]
        self.client.page_errors[url] = HTTPError(url, 404, "Not Found", {}, None)
        report = self.run_update()
        metadata, _ = catalog.read_article(self.article())
        self.assertEqual(report["incomplete_records"], 1)
        self.assertFalse(metadata["derived_view_available"])

    def test_protected_update_preserves_old_article_and_retries(self):
        self.run_update()
        before = self.article().read_bytes()
        old_stamp = self.index()["1"]
        self.records[0] = post(modified="2024-01-01T00:00:00", body="<p>Enter password.</p>")
        self.records[0]["content"]["protected"] = True

        report = self.run_update()
        self.assertEqual(report["incomplete_records"], 1)
        self.assertIn("protected", report["errors"][0]["error"].lower())
        self.assertEqual(self.article().read_bytes(), before)
        self.assertEqual(self.index()["1"], old_stamp)
        self.assertEqual(self.client.page_calls, [])

        self.records[0]["content"] = {"protected": False, "rendered": "<p>Accessible again.</p>"}
        report = self.run_update()
        self.assertEqual(report["incomplete_records"], 0)
        self.assertEqual(self.index()["1"], "2024-01-01T00:00:00")
        self.assertIn("Accessible again", self.article().read_text(encoding="utf-8"))

    def test_new_protected_article_is_not_published(self):
        self.records[0]["content"]["protected"] = True
        report = self.run_update()
        self.assertEqual(report["incomplete_records"], 1)
        self.assertEqual(self.index(), {})
        self.assertEqual(catalog.list_article_paths(self.root), {})

    def test_missing_featured_media_does_not_block_unrelated_update(self):
        self.records.append(post(2))
        self.run_update()
        old_second = self.article(2).read_bytes()
        old_stamp = self.index()["2"]
        self.records = [post(1, modified="2024-01-01T00:00:00", body="<p>First update.</p>"),
                        post(2, modified="2024-01-01T00:00:00", body="<p>Second update.</p>")]
        self.records[0]["featured_media"] = 11
        self.records[1]["featured_media"] = 22
        partial_media = Inventory([{"id": 11, "media_type": "image",
                                    "source_url": "https://kiszamolo.hu/media-11.png"}], [])
        with patch.object(self, "media", side_effect=MissingMediaError({22}, partial_media)):
            report = self.run_update()

        self.assertEqual(report["incomplete_records"], 1)
        self.assertEqual([item["record"] for item in report["errors"]], ["2"])
        self.assertEqual(self.index()["1"], "2024-01-01T00:00:00")
        self.assertIn("First update", self.article(1).read_text(encoding="utf-8"))
        self.assertIn("media-11.png", self.article(1).read_text(encoding="utf-8"))
        self.assertEqual(self.article(2).read_bytes(), old_second)
        self.assertEqual(self.index()["2"], old_stamp)

        self.detail_calls.clear()
        retry_report = self.run_update()
        self.assertEqual(retry_report["incomplete_records"], 0)
        self.assertEqual(self.detail_calls, [("posts", [2])])
        self.assertIn("Second update", self.article(2).read_text(encoding="utf-8"))

    def test_failed_featured_media_does_not_publish_new_article(self):
        self.records.append(post(2))
        self.records[1]["featured_media"] = 55
        self.fail_media = True
        report = self.run_update()
        self.assertIn("2", {item["record"] for item in report["errors"]})
        self.assertNotIn("2", self.index())
        self.assertFalse(any(path.name.endswith(" [2].md")
                             for path in (self.root / catalog.ARTICLES_PATH).rglob("*.md")))

    def test_remote_absent_article_remains_a_reserved_local_path(self):
        self.records = [post(2, title="Preserved path"), post(1)]
        self.run_update()
        old_article = self.article(2)
        old_bytes = old_article.read_bytes()
        self.records = [post(1), post(3, title="Preserved path")]
        self.run_update()
        self.assertEqual(old_article.read_bytes(), old_bytes)
        self.assertNotEqual(self.article(3), old_article)
        self.assertIn(2, self.run_update()["post_ids"])

    def test_updated_article_keeps_local_links_to_targets_absent_from_census(self):
        source = post(1, body='<p><a href="https://kiszamolo.hu/post-2/">Post</a> '
                              '<a href="https://kiszamolo.hu/post-3/">Page</a></p>')
        page = post(3)
        page["type"] = "page"
        self.records = [source, post(2), page]
        self.run_update()
        targets = [self.article(2), self.article(3)]
        self.records = [dict(source, modified="2024-01-01T00:00:00",
                             modified_gmt="2024-01-01T00:00:00")]

        self.run_update()
        body = catalog.read_article(self.article(1))[1]
        self.assertNotIn("https://kiszamolo.hu/post-2/", body)
        self.assertNotIn("https://kiszamolo.hu/post-3/", body)
        self.assertIn("%5B2%5D.md", body)
        self.assertIn("%5B3%5D.md", body)
        self.assertTrue(all(path.is_file() for path in targets))

    def test_title_update_keeps_filename_and_existing_local_link_stable(self):
        self.records = [post(1, body='<p><a href="https://kiszamolo.hu/post-2/">Second</a></p>'), post(2)]
        self.run_update()
        old_target = self.article(2)
        source = self.article(1).read_text(encoding="utf-8")
        self.assertIn("Article%202%20%5B2%5D.md", source)
        self.records[1] = post(2, modified="2024-01-01T00:00:00", title="Renamed second article",
                               date="2020-02-03T04:05:06")
        self.run_update()
        self.assertEqual(self.article(2), old_target)
        metadata, _ = catalog.read_article(old_target)
        self.assertEqual(metadata["title"], "Renamed second article")
        self.assertEqual(metadata["published_at"], "2020-02-03T04:05:06")
        self.assertTrue(old_target.exists())

    def test_index_save_failure_after_title_and_date_change_retries_in_place(self):
        self.run_update()
        original_path = self.article()
        old_stamp = self.index()["1"]
        self.records[0] = post(1, modified="2024-01-01T00:00:00", title="Updated title",
                               date="2020-02-03T04:05:06")
        real_save = sync.save_index
        attempts = 0

        def fail_first_save(root, index):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("index is temporarily unavailable")
            return real_save(root, index)

        with patch.object(sync, "save_index", fail_first_save):
            first_report = self.run_update()
        self.assertEqual(self.index()["1"], old_stamp)
        self.assertIn("1", {item["record"] for item in first_report["errors"]})
        self.assertEqual(self.article(), original_path)

        retry_report = self.run_update()
        self.assertEqual(retry_report["errors"], [])
        metadata, _ = catalog.read_article(original_path)
        self.assertEqual(metadata["title"], "Updated title")
        self.assertEqual(metadata["published_at"], "2020-02-03T04:05:06")
        paths = catalog.list_article_paths(self.root)
        self.assertEqual(list(paths), ["1"])
        self.assertEqual(paths["1"], original_path)
        self.assertEqual(self.index()["1"], "2024-01-01T00:00:00")

    def test_technical_anchor_referenced_by_another_article_is_preserved(self):
        self.records = [
            post(1, body='<a href="https://kiszamolo.hu/post-2/#attachment_123">Link</a>'),
            post(2, body='<div id="attachment_123">Target</div>'),
        ]
        self.run_update()
        target = self.article(2).read_text(encoding="utf-8")
        self.assertIn('id="attachment_123"', target)


if __name__ == "__main__":
    unittest.main()
