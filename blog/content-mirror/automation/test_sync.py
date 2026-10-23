"""Deterministic sync lifecycle tests with no remote network access."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from remote import Download, Inventory, Response, TransientDownloadError
import sync


def post(number=1, *, modified="2011-01-01T00:00:00", date="2011-01-01T00:00:00", body=None):
    return dict(id=number, type="post", status="publish", date=date, date_gmt=date,
                modified=modified, modified_gmt=modified, link=f"https://kiszamolo.hu/p{number}/",
                title={"rendered": f"Title {number}"},
                content={"rendered": body or '<p>A <a href="/p2/#part">link</a></p><img src="/a.png"><img src="https://other.test/b.png">',
                         "protected": False}, categories=[], tags=[])


def page(number=1, *, modified="2011-01-01T00:00:00", date="2011-01-01T00:00:00", body=None):
    record = post(number, modified=modified, date=date, body=body)
    record["type"] = "page"
    record["link"] = f"https://kiszamolo.hu/page-{number}/"
    record["title"]["rendered"] = f"Page {number}"
    return record


class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.http_failures = {}
        self.invalid_media = set()
        self.exceptions = {}
        self.payloads = {}
        self.live_absent_candidate = False

    def request_bytes(self, url, **kwargs):
        if self.live_absent_candidate:
            return Response(b"{}", 200, {}, url)
        raise HTTPError(url, 404, "Not found", {}, None)

    def download(self, url, target, conditional_headers=None, **kwargs):
        self.calls.append((url, conditional_headers))
        if url in self.exceptions:
            raise self.exceptions[url]
        if url in self.fail:
            raise URLError("Simulated network failure")
        if url in self.http_failures:
            status = self.http_failures[url]
            raise HTTPError(url, status, "Simulated HTTP failure", {}, None)
        if url in self.invalid_media:
            raise ValueError("Expected media, received text/html")
        body = self.payloads.get(url, b"data")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return Download(200, {"ETag": '"tag"', "Content-Type": "text/html" if url.endswith("/") else "image/png"}, url, len(body), hashlib.sha256(body).hexdigest())


class ExclusionFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "exclusions.json"

    def load(self, text):
        self.path.write_text(text, encoding="utf-8")
        return sync.load_exclusions(self.path)

    def test_loads_record_keys_and_preserves_reasons(self):
        records = {"page:2": "Adminisztratív oldal.", "post:104197": "Maintenance notice."}
        self.assertEqual(self.load(json.dumps(records, ensure_ascii=False)), records)
        self.assertEqual(self.load("{}"), {})

    def test_rejects_non_object_root(self):
        for text in ("[]", "null", '"reason"', "42"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "must be an object"):
                self.load(text)

    def test_rejects_invalid_record_keys(self):
        for key in ("2", "posts:2", "other:2", "post:0", "page:01", "post:-2", "post:2\n"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Invalid exclusion key"):
                self.load(json.dumps({key: "Reason"}))

    def test_requires_nonempty_string_reason(self):
        for reason in ("", " \t\n", None, 0, False, [], {}):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, "nonempty string"):
                self.load(json.dumps({"post:2": reason}))

    def test_rejects_duplicate_keys(self):
        with self.assertRaisesRegex(ValueError, "Duplicate exclusion key"):
            self.load('{"post:2":"First reason","post:2":"Second reason"}')

    def test_rejects_malformed_json(self):
        with self.assertRaises(json.JSONDecodeError):
            self.load('{"post:2":')

    def test_missing_file_does_not_silently_disable_exclusions(self):
        with self.assertRaises(FileNotFoundError):
            sync.load_exclusions(self.path)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "mirror"
        self.articles = Path(self.temp.name) / "articles"
        self.client = FakeClient()
        self.records = [post(), post(2)]
        self.pages = []
        self.detail_calls = []

    def tearDown(self):
        self.temp.cleanup()

    def inventory(self, client, kind, **kwargs):
        fields = ("id", "type", "status", "modified", "modified_gmt", "date_gmt")
        records = self.records if kind == "posts" else self.pages if kind == "pages" else []
        return Inventory([{k:r[k] for k in fields} for r in records], [])

    def details(self, client, kind, ids, **kwargs):
        self.detail_calls.append((kind, list(ids)))
        records = self.records if kind == "posts" else self.pages if kind == "pages" else []
        return Inventory([json.loads(json.dumps(r)) for r in records
                          if r["id"] in ids and r["type"] + "s" == kind], [])

    def run_sync(self, **kwargs):
        metadata_loader = kwargs.pop("metadata_loader", lambda *a, **k: Inventory([], []))
        return sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                             record_loader=self.details, metadata_loader=metadata_loader,
                             progress=lambda _: None, **kwargs)

    def state(self):
        return json.loads((self.root / "state.json").read_text(encoding="utf-8"))

    def clear_classification_pending(self, *keys):
        state = self.state()
        for key in keys or tuple(state["records"]):
            if state["records"].get(key, {}).get("type") == "post":
                state["records"][key]["classification_pending"] = False
                state["records"][key]["content_classification_pending"] = False
        (self.root / "state.json").write_bytes(sync.json_bytes(state))

    def article_file(self, number, kind="post"):
        return self.articles / self.state()["records"][f"{kind}:{number}"]["path"]

    def test_full_download_local_links_and_external_policy(self):
        self.client.fail.update({"https://kiszamolo.hu/a.png", "https://other.test/b.png"})
        report = self.run_sync()
        self.assertEqual(report["incomplete_records"], 0)
        self.assertEqual({url for url, _ in self.client.calls},
                         {"https://kiszamolo.hu/p1/", "https://kiszamolo.hu/p2/"})
        text = self.article_file(1).read_text(encoding="utf-8")
        self.assertIn("2011-01-01%2000.00%20Title%202.md#part", text)
        self.assertIn("https://kiszamolo.hu/a.png", text)
        self.assertFalse(list(self.articles.rglob("*.html")))
        self.assertFalse((self.articles / "assets").exists())
        self.assertTrue((self.root / "records/posts/1/source.html").exists())
        self.assertIn("https://other.test/b.png", text)
        self.assertNotIn("assets", self.state())
        self.assertEqual(self.state()["records"]["post:1"]["external_assets"],
                         ["https://kiszamolo.hu/a.png", "https://other.test/b.png"])

    def test_media_source_statistics_refresh_only_during_offline_rebuild(self):
        self.run_sync()
        self.assertFalse((self.articles / "media-sources.json").exists())
        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        stats_path = self.articles / "media-sources.json"
        before = stats_path.read_bytes()
        self.assertIn("[Médiaforrások statisztikája](MEDIA-SOURCES.md)",
                      (self.articles / "INDEX.md").read_text(encoding="utf-8"))
        self.records[0] = post(modified="2026-10-19T00:00:00",
            body='<p>Updated</p><img src="https://outside.test/new.png">')
        self.run_sync()
        self.assertEqual(stats_path.read_bytes(), before)
        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        refreshed = json.loads(stats_path.read_text(encoding="utf-8"))
        self.assertIn("outside.test", refreshed)

    def test_unchanged_taxonomy_does_not_trigger_fetch_or_rerender(self):
        self.records[0]["categories"] = [5]
        current_name = "Finance"
        calls = []

        def metadata(client, kind, **kwargs):
            calls.append(kind)
            return Inventory([{"id": 5, "name": current_name}] if kind == "categories" else [], [])

        sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
            record_loader=self.details, metadata_loader=metadata, progress=lambda _: None)
        self.assertEqual(self.state()["records"]["post:1"]["category_names"], ["Finance"])
        calls.clear()
        current_name = "Updated category"
        with patch("sync.render_views", side_effect=AssertionError("Unchanged articles rerendered")):
            sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                record_loader=self.details, metadata_loader=metadata, progress=lambda _: None)
        self.assertEqual(calls, [])
        self.assertEqual(self.state()["records"]["post:1"]["category_names"], ["Finance"])

    def test_only_explicitly_excluded_record_is_skipped_before_fetching(self):
        self.records.append(post(104048))
        self.records[1]["content"]["protected"] = True
        report = self.run_sync()
        self.assertEqual(set(self.state()["records"]), {"post:1", "post:2"})
        self.assertEqual(self.detail_calls, [("posts", [1, 2])])
        self.assertFalse((self.root / "records/posts/104048").exists())
        self.assertNotIn("https://kiszamolo.hu/p104048/", [url for url, _ in self.client.calls])
        self.assertEqual(set(report["excluded_records"]), {"post:104048"})
        self.assertEqual(self.state()["records"]["post:2"]["acquisition_status"], "incomplete")

    def test_approved_page_and_post_exclusions_skip_hydration_and_public_downloads(self):
        excluded_pages = [int(key.split(":")[1]) for key in sync.EXCLUDED_RECORDS if key.startswith("page:")]
        excluded_posts = [int(key.split(":")[1]) for key in sync.EXCLUDED_RECORDS if key.startswith("post:")]
        self.records = [post(number) for number in [1, *excluded_posts]]
        self.pages = [page(number) for number in [500, *excluded_pages]]

        report = self.run_sync()

        self.assertEqual(self.detail_calls, [("posts", [1]), ("pages", [500])])
        self.assertEqual(report["excluded_records"], sync.EXCLUDED_RECORDS)
        self.assertEqual(set(self.state()["records"]), {"post:1", "page:500"})
        downloaded_urls = {url for url, _ in self.client.calls}
        self.assertIn("https://kiszamolo.hu/p1/", downloaded_urls)
        self.assertIn("https://kiszamolo.hu/page-500/", downloaded_urls)
        for number in excluded_posts:
            self.assertNotIn(f"https://kiszamolo.hu/p{number}/", downloaded_urls)
            self.assertFalse((self.root / f"records/posts/{number}").exists())
        for number in excluded_pages:
            self.assertNotIn(f"https://kiszamolo.hu/page-{number}/", downloaded_urls)
            self.assertFalse((self.root / f"records/pages/{number}").exists())

    def test_exclusion_prunes_existing_files_and_keeps_shared_media(self):
        for mode in ("sync", "rebuild"):
            with self.subTest(mode=mode):
                self.records = [post(), post(104048, body='<img src="/a.png"><img src="/private.png">')]
                self.records[0]["content"]["rendered"] += '<a href="/p104048/">Policy</a>'
                with patch("sync.EXCLUDED_RECORDS", {}):
                    self.run_sync()
                old_article = self.article_file(104048)
                self.assertTrue(old_article.exists())
                self.client.calls.clear()
                self.detail_calls.clear()
                if mode == "sync":
                    self.run_sync()
                else:
                    # A missing excluded source must not prevent an offline rebuild.
                    (self.root / "records/posts/104048/source.json").unlink()
                    with patch("sync.HttpClient", side_effect=AssertionError("Network forbidden")):
                        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
                self.assertEqual(set(self.state()["records"]), {"post:1"})
                self.assertFalse(old_article.exists())
                self.assertFalse((self.root / "records/posts/104048").exists())
                self.assertFalse((self.articles / "assets").exists())
                self.assertNotIn("assets", self.state())
                self.assertIn("[Policy](https://kiszamolo.hu/p104048/)", self.article_file(1).read_text(encoding="utf-8"))
                self.assertNotIn("104048", (self.articles / "index.jsonl").read_text(encoding="utf-8"))
                self.assertNotIn("Title 104048", (self.articles / "INDEX.md").read_text(encoding="utf-8"))
                self.assertEqual(self.client.calls, [])
                self.assertEqual(self.detail_calls, [])

    def test_exclusion_link_fallback_survives_inventory_failure(self):
        self.records = [post(body='<a href="/p104048/">Policy</a>'), post(104048)]
        with patch("sync.EXCLUDED_RECORDS", {}):
            self.run_sync()
        incoming = self.article_file(1)
        self.assertIn("104048.md", incoming.read_text(encoding="utf-8"))

        def failed_inventory(*args, **kwargs):
            raise OSError("Simulated inventory failure")

        with self.assertRaisesRegex(OSError, "inventory failure"):
            sync.run_sync(self.root, client=self.client, inventory_loader=failed_inventory,
                record_loader=self.details, metadata_loader=lambda *a, **k: Inventory([], []),
                progress=lambda _: None)
        self.assertIn("https://kiszamolo.hu/p104048/", incoming.read_text(encoding="utf-8"))
        self.assertNotIn("104048.md", incoming.read_text(encoding="utf-8"))
        self.assertEqual(set(self.state()["records"]), {"post:1"})

        self.run_sync()
        self.assertIn("https://kiszamolo.hu/p104048/", incoming.read_text(encoding="utf-8"))

    def test_excluded_source_directory_is_validated_before_deleting_any_files(self):
        self.records.append(post(104048))
        with patch("sync.EXCLUDED_RECORDS", {}):
            self.run_sync()
        state = self.state()
        state["records"]["post:104048"]["directory"] = "records/posts/1"
        with self.assertRaisesRegex(ValueError, "directory mismatch"):
            sync.prune_excluded(self.root, self.articles, state)
        self.assertTrue(self.article_file(104048).exists())
        self.assertTrue((self.root / "records/posts/1/source.json").exists())

    def test_legacy_anchor_references_survive_targeted_render_without_reading_referer(self):
        self.records[0] = post(body='<a href="/p2/#headline-38-120338">Másik cikk</a>')
        self.records[1] = post(2, body='<h2 id="headline-38-120338">Célpont</h2>')
        self.run_sync()
        self.assertIn('#headline-38-120338)', self.article_file(1).read_text(encoding="utf-8"))
        self.assertIn('<a id="headline-38-120338"></a>',
                      self.article_file(2).read_text(encoding="utf-8"))
        state = self.state()
        for item in state["records"].values():
            item.pop("referenced_anchors", None)
        (self.root / "state.json").write_bytes(sync.json_bytes(state))
        self.records[1] = post(2, modified="2026-10-19T00:00:00",
                               body='<h2 id="headline-38-120338">Changed target</h2>')
        incoming = self.article_file(1)
        original_read_text = Path.read_text

        def guarded_read_text(path, *args, **kwargs):
            if path == incoming:
                raise AssertionError("Unchanged referring article was read")
            return original_read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded_read_text):
            self.run_sync()
        self.assertIn('<a id="headline-38-120338"></a>',
                      self.article_file(2).read_text(encoding="utf-8"))
        self.records[0] = post(modified="2026-10-19T00:00:00", body='<p>Nincs hivatkozás.</p>')
        self.run_sync()
        self.assertIn('headline-38-120338', self.article_file(2).read_text(encoding="utf-8"))

    def test_unchanged_timestamp_reuses_cache_and_timestamp_less_change_is_ignored(self):
        self.run_sync()
        self.client.calls.clear()
        self.detail_calls.clear()
        self.run_sync()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.detail_calls, [])
        self.records[0] = post(body="<p>Changed without modified timestamp</p>")
        self.run_sync()
        self.assertEqual(self.detail_calls, [])
        self.assertNotIn("Changed without modified timestamp", self.article_file(1).read_text())
        self.records[0]["modified"] = self.records[0]["modified_gmt"] = "2026-10-19T00:00:00"
        report = self.run_sync()
        self.assertEqual(report["full_records_fetched"], 1)
        self.assertIn("Changed without modified timestamp", self.article_file(1).read_text())

    def test_new_posts_are_pending_and_index_exposes_registry_flag(self):
        self.run_sync()
        state = self.state()
        self.assertTrue(state["records"]["post:1"]["classification_pending"])
        self.assertTrue(state["records"]["post:1"]["content_classification_pending"])
        entries = [json.loads(line) for line in (self.articles / "index.jsonl").read_text().splitlines()]
        self.assertTrue(next(item for item in entries if item["key"] == "post:1")["classification_pending"])
        self.assertTrue(next(item for item in entries if item["key"] == "post:1")["content_classification_pending"])

    def test_changed_post_sets_pending_and_unchanged_refresh_does_not(self):
        self.run_sync()
        self.clear_classification_pending()
        self.records[0] = post(modified="2026-10-08T12:00:00", body="<p>Changed</p>")
        self.run_sync()
        self.assertTrue(self.state()["records"]["post:1"]["classification_pending"])
        self.assertTrue(self.state()["records"]["post:1"]["content_classification_pending"])
        self.clear_classification_pending()
        self.run_sync()
        self.assertFalse(self.state()["records"]["post:1"]["classification_pending"])
        self.assertFalse(self.state()["records"]["post:1"]["content_classification_pending"])

    def test_metadata_only_refresh_does_not_queue_post(self):
        self.run_sync()
        self.clear_classification_pending()
        record = self.records[0]
        record.update(modified="2026-10-08T12:00:00", modified_gmt="2026-10-08T12:00:00",
                      date="2012-01-01T00:00:00", date_gmt="2012-01-01T00:00:00",
                      link="https://kiszamolo.hu/renamed-link/", excerpt={"rendered": "New excerpt"},
                      categories=[12], tags=[34], yoast_head_json={"title": "New SEO title"})
        self.run_sync()
        self.assertFalse(self.state()["records"]["post:1"]["classification_pending"])

    def test_refresh_preserves_independent_classifier_pending_flags(self):
        self.run_sync()
        for topic_pending, content_pending in ((False, True), (True, False)):
            state = self.state()
            state["records"]["post:1"].update(classification_pending=topic_pending,
                                               content_classification_pending=content_pending)
            (self.root / "state.json").write_bytes(sync.json_bytes(state))
            self.run_sync()
            item = self.state()["records"]["post:1"]
            self.assertEqual(item["classification_pending"], topic_pending)
            self.assertEqual(item["content_classification_pending"], content_pending)

    def test_classification_content_fields_queue_post(self):
        changes = (
            (0, lambda record: record["title"].update(rendered="Changed title")),
            (0, lambda record: record["content"].update(rendered="<p>Changed body</p>")),
            (0, lambda record: record["content"].update(protected=True)),
            (0, lambda record: record.update(featured_media=99)),
            (99, lambda record: record.update(featured_media=0)),
            (99, lambda record: record.update(featured_media=100)),
        )
        for initial_media, change in changes:
            with self.subTest(initial_media=initial_media, change=change):
                with tempfile.TemporaryDirectory() as temporary:
                    self.root = Path(temporary) / "mirror"
                    self.articles = Path(temporary) / "articles"
                    self.client = FakeClient()
                    self.records = [post()]
                    if initial_media:
                        self.records[0]["featured_media"] = initial_media
                    self.pages = []
                    self.detail_calls = []
                    self.run_sync()
                    self.clear_classification_pending()
                    record = self.records[0]
                    change(record)
                    record["modified"] = record["modified_gmt"] = "2026-10-08T12:00:00"
                    self.run_sync()
                    self.assertTrue(self.state()["records"]["post:1"]["classification_pending"])
                    self.assertTrue(self.state()["records"]["post:1"]["content_classification_pending"])

    def test_missing_valid_previous_source_conservatively_queues_post(self):
        self.run_sync()
        self.clear_classification_pending()
        (self.root / "records/posts/1/source.json").write_text("{}", encoding="utf-8")
        self.records[0]["modified"] = self.records[0]["modified_gmt"] = "2026-10-08T12:00:00"
        self.run_sync()
        self.assertTrue(self.state()["records"]["post:1"]["classification_pending"])

    def test_featured_metadata_only_queues_when_rendered_fields_change(self):
        self.records[0]["featured_media"] = 100
        current = {"id": 100, "media_type": "image", "source_url": "https://kiszamolo.hu/hero.png",
                   "alt_text": "Hero", "caption": {"rendered": "Caption"},
                   "modified": "2020-01-01", "description": "SEO", "sizes": {"full": {"width": 1}}}

        def metadata(client, kind, **kwargs):
            return Inventory([json.loads(json.dumps(current))] if kind == "media" else [], [])

        self.run_sync(metadata_loader=metadata)
        self.clear_classification_pending()
        for field, value in (("modified", "2021-01-01"), ("description", "Updated SEO"),
                             ("title", {"rendered": "Updated attachment title"}),
                             ("sizes", {"full": {"width": 2}})):
            current[field] = value
        self.records[0]["modified"] = self.records[0]["modified_gmt"] = "2026-10-08T12:00:00"
        self.run_sync(metadata_loader=metadata)
        self.assertFalse(self.state()["records"]["post:1"]["classification_pending"])

        for index, (field, value) in enumerate((("source_url", "https://kiszamolo.hu/updated.png"),
                                                ("alt_text", "Updated alt"),
                                                ("caption", {"rendered": "Updated caption"}),
                                                ("media_type", "video")), 9):
            with self.subTest(field=field):
                self.clear_classification_pending()
                current[field] = value
                self.records[0]["modified"] = self.records[0]["modified_gmt"] = f"2026-10-{index:02}T12:00:00"
                self.run_sync(metadata_loader=metadata)
                self.assertTrue(self.state()["records"]["post:1"]["classification_pending"])
                self.assertTrue(self.state()["records"]["post:1"]["content_classification_pending"])

    def test_pending_survives_sync_and_offline_rebuild(self):
        self.run_sync()
        self.records[0] = post(modified="2026-10-08T12:00:00", body="<p>Changed</p><img src='/a.png'>")
        self.run_sync()
        self.assertTrue(self.state()["records"]["post:1"]["classification_pending"])
        self.run_sync()
        self.assertTrue(self.state()["records"]["post:1"]["classification_pending"])
        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        self.assertTrue(self.state()["records"]["post:1"]["classification_pending"])
        entries = [json.loads(line) for line in (self.articles / "index.jsonl").read_text().splitlines()]
        self.assertTrue(next(item for item in entries if item["key"] == "post:1")["classification_pending"])
        self.assertTrue(next(item for item in entries if item["key"] == "post:1")["content_classification_pending"])

    def test_existing_legacy_record_without_pending_flag_is_not_mass_queued(self):
        self.run_sync()
        state = self.state()
        for item in state["records"].values():
            item.pop("classification_pending", None)
            item.pop("content_classification_pending", None)
        (self.root / "state.json").write_bytes(sync.json_bytes(state))
        self.run_sync()
        self.assertFalse(self.state()["records"]["post:1"]["classification_pending"])
        self.assertFalse(self.state()["records"]["post:1"]["content_classification_pending"])

    def test_legacy_protected_record_remains_incomplete_and_is_retried(self):
        self.records = [post()]
        self.records[0]["content"]["protected"] = True
        self.run_sync()
        state = self.state()
        state["records"]["post:1"].pop("protected_content")
        (self.root / "state.json").write_bytes(sync.json_bytes(state))
        self.detail_calls.clear()
        report = self.run_sync()
        self.assertEqual(self.detail_calls, [("posts", [1])])
        self.assertEqual(report["incomplete_records"], 1)
        self.assertTrue(self.state()["records"]["post:1"]["protected_content"])
        self.assertEqual(self.state()["records"]["post:1"]["acquisition_status"], "incomplete")

    def test_permanent_public_page_failure_is_skipped_after_article_change(self):
        self.records = [post(body="<p>API body</p>")]
        url = self.records[0]["link"]
        self.client.http_failures[url] = 404
        report = self.run_sync()
        self.assertEqual(self.state()["records"]["post:1"]["page"]["status"], "unavailable")
        self.assertEqual(report["incomplete_records"], 0)
        self.assertEqual(report["page_failures"], {})

        self.client.calls.clear()
        self.run_sync()
        self.assertNotIn(url, [called_url for called_url, _ in self.client.calls])
        self.records[0] = post(modified="2011-01-02T00:00:00", body="<p>Changed API body</p>")
        self.client.calls.clear()
        report = self.run_sync()
        self.assertNotIn(url, [called_url for called_url, _ in self.client.calls])
        self.assertEqual(self.state()["records"]["post:1"]["page"]["status"], "unavailable")
        self.assertEqual(report["incomplete_records"], 0)
        self.records[0] = post(modified="2011-01-03T00:00:00", body="<p>New page URL</p>")
        self.records[0]["link"] = "https://kiszamolo.hu/p1-new/"
        self.client.calls.clear()
        self.run_sync()
        self.assertIn(self.records[0]["link"], [called_url for called_url, _ in self.client.calls])
        self.assertEqual(self.state()["records"]["post:1"]["page"]["requested_url"], self.records[0]["link"])

    def test_transient_public_page_failure_is_retried(self):
        self.records = [post(body="<p>API body</p>")]
        url = self.records[0]["link"]
        self.client.fail.add(url)
        self.run_sync()
        self.assertEqual(self.state()["records"]["post:1"]["page"]["status"], "failed")
        self.client.fail.clear()
        self.client.calls.clear()
        self.run_sync()
        self.assertIn(url, [called_url for called_url, _ in self.client.calls])
        self.assertEqual(self.state()["records"]["post:1"]["page"]["status"], "downloaded")

    def test_unavailable_page_does_not_repeat_empty_api_record_fetch(self):
        self.records = []
        self.pages = [page(body=" ")]
        url = self.pages[0]["link"]
        self.client.http_failures[url] = 404
        self.run_sync()
        item = self.state()["records"]["page:1"]
        self.assertEqual(item["page"]["status"], "unavailable")
        self.assertFalse(item["derived_view_available"])
        self.assertEqual(item["acquisition_status"], "incomplete")

        self.detail_calls.clear()
        self.client.calls.clear()
        self.run_sync()
        self.assertEqual(self.detail_calls, [])
        self.assertEqual(self.client.calls, [])

    def test_missing_remote_retained_and_reactivation(self):
        self.run_sync()
        old = self.article_file(2)
        old_bytes = old.read_bytes()
        self.records = [post()]
        self.run_sync()
        self.assertEqual(self.state()["records"]["post:2"]["status"], "remote_missing")
        self.assertEqual(old.read_bytes(), old_bytes)
        self.assertIn("remote_missing", (self.articles / "index.jsonl").read_text())
        self.records.append(post(2))
        self.run_sync()
        self.assertEqual(self.state()["records"]["post:2"]["status"], "active")
        self.assertNotIn("remote_missing_since", self.state()["records"]["post:2"])

    def test_partial_sample_does_not_mark_missing_or_advance_watermark(self):
        self.run_sync()
        previous = self.state()["last_completed_sync_at"]
        self.records = [post()]
        self.run_sync(sample_ids={1})
        self.assertEqual(self.state()["records"]["post:2"]["status"], "active")
        self.assertEqual(self.state()["last_completed_sync_at"], previous)

    def test_inventory_failure_leaves_state_and_index_unchanged(self):
        self.run_sync()
        before = (self.root / "state.json").read_bytes(), (self.articles / "index.jsonl").read_bytes()
        def broken(client, kind, **kwargs):
            if kind == "pages":
                raise ValueError("Incomplete inventory")
            return self.inventory(client, kind)
        with self.assertRaises(ValueError):
            sync.run_sync(self.root, client=self.client, inventory_loader=broken,
                          metadata_loader=lambda *a, **k: Inventory([], []), progress=lambda _: None)
        self.assertEqual(before, ((self.root / "state.json").read_bytes(), (self.articles / "index.jsonl").read_bytes()))

    def test_live_missing_candidate_aborts_without_false_status(self):
        self.run_sync()
        before = (self.root / "state.json").read_bytes()
        self.records = [post()]
        self.client.live_absent_candidate = True
        with self.assertRaisesRegex(ValueError, "absence could not be confirmed"):
            self.run_sync()
        self.assertEqual((self.root / "state.json").read_bytes(), before)

    def test_featured_media_and_taxonomy_names_are_preserved(self):
        self.records[0]["featured_media"] = 100
        self.records[0]["categories"] = [5]
        def metadata(client, kind, **kwargs):
            if kind == "categories":
                return Inventory([{"id": 5, "name": "Finance"}], [])
            if kind == "media":
                return Inventory([{"id": 100, "media_type": "image", "source_url": "https://kiszamolo.hu/hero.png",
                                   "alt_text": "Hero", "caption": {"rendered": "Caption"}}], [])
            return Inventory([], [])
        sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                      record_loader=self.details, metadata_loader=metadata, progress=lambda _: None)
        state = self.state()
        self.assertIn("https://kiszamolo.hu/hero.png", state["records"]["post:1"]["external_assets"])
        self.assertTrue((self.root / "records/posts/1/source-featured-media.json").is_file())
        text = self.article_file(1).read_text(encoding="utf-8")
        self.assertIn('category_names: ["Finance"]', text)
        self.assertIn("Hero", text)
        self.assertIn("Caption", text)
        def failed_metadata(client, kind, **kwargs):
            if kind == "media":
                raise OSError("Temporary media API outage")
            return metadata(client, kind, **kwargs)
        self.records[0]["modified"] = self.records[0]["modified_gmt"] = "2026-10-19T00:00:00"
        sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                      record_loader=self.details, metadata_loader=failed_metadata, progress=lambda _: None)
        self.assertIn(b"Hero", self.article_file(1).read_bytes())
        self.assertIn("https://kiszamolo.hu/hero.png", self.state()["records"]["post:1"]["external_assets"])
        self.assertEqual(self.state()["records"]["post:1"]["acquisition_status"], "incomplete")
        self.detail_calls.clear()
        report = sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                               record_loader=self.details, metadata_loader=metadata, progress=lambda _: None)
        self.assertEqual(self.detail_calls, [("posts", [1])])
        self.assertEqual(report["incomplete_records"], 0)

    def test_featured_media_replacement_outage_allows_offline_rebuild_and_retry(self):
        self.records[0]["featured_media"] = 100
        unavailable = False

        def metadata(client, kind, **kwargs):
            if kind == "media":
                if unavailable:
                    raise OSError("Temporary media API outage")
                return Inventory([{"id": media_id, "media_type": "image",
                                   "source_url": f"https://kiszamolo.hu/hero{media_id}.png",
                                   "alt_text": f"Hero {media_id}"} for media_id in kwargs["ids"]], [])
            return Inventory([], [])

        def run():
            return sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                                 record_loader=self.details, metadata_loader=metadata, progress=lambda _: None)

        run()
        cached_path = self.root / "records/posts/1/source-featured-media.json"
        old_metadata = cached_path.read_bytes()
        self.records[0]["featured_media"] = 101
        self.records[0]["modified"] = self.records[0]["modified_gmt"] = "2011-01-02T00:00:00"
        unavailable = True
        for _ in range(2):
            self.assertEqual(run()["incomplete_records"], 1)
            before_state = self.state()
            before_report = (self.articles / "last-report.json").read_bytes()
            before_markdown = self.article_file(1).read_bytes()
            self.assertIsNone(before_state["records"]["post:1"]["featured_media_sha256"])
            self.assertNotIn(b"Hero 100", before_markdown)
            with patch("sync.HttpClient", side_effect=AssertionError("Network forbidden")):
                sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
            self.assertEqual(self.state(), before_state)
            self.assertEqual((self.articles / "last-report.json").read_bytes(), before_report)
            self.assertEqual(self.article_file(1).read_bytes(), before_markdown)
            self.assertEqual(cached_path.read_bytes(), old_metadata)
        unavailable = False
        self.assertEqual(run()["incomplete_records"], 0)
        self.assertIn("Hero 101", self.article_file(1).read_text(encoding="utf-8"))
        self.assertEqual(json.loads(cached_path.read_bytes())["id"], 101)
        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)

    def test_unverified_featured_cache_is_ignored_during_metadata_outage(self):
        self.records[0]["featured_media"] = 100
        unavailable = False

        def metadata(client, kind, **kwargs):
            if kind == "media":
                if unavailable:
                    raise OSError("Temporary media API outage")
                return Inventory([{"id": 100, "media_type": "image",
                                   "source_url": "https://kiszamolo.hu/hero.png", "alt_text": "Verified hero"}], [])
            return Inventory([], [])

        def run():
            return sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                                 record_loader=self.details, metadata_loader=metadata, progress=lambda _: None)

        run()
        unavailable = True
        cached_path = self.root / "records/posts/1/source-featured-media.json"
        stale = {"id": 100, "media_type": "image", "source_url": "https://kiszamolo.hu/stale.png",
                 "alt_text": "Unverified hero"}
        for raw in (sync.json_bytes(stale), b"invalid JSON"):
            with self.subTest(raw=raw):
                cached_path.write_bytes(raw)
                before = self.article_file(1).read_bytes()
                self.assertNotIn(b"Unverified hero", before)
                with self.assertRaises(ValueError):
                    sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
                self.assertEqual(self.article_file(1).read_bytes(), before)
                self.assertEqual(cached_path.read_bytes(), raw)

    def test_offline_rebuild_still_rejects_damaged_tracked_featured_metadata(self):
        self.records[0]["featured_media"] = 100

        def metadata(client, kind, **kwargs):
            return Inventory([{"id": 100, "media_type": "image",
                               "source_url": "https://kiszamolo.hu/hero.png"}] if kind == "media" else [], [])

        sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                      record_loader=self.details, metadata_loader=metadata, progress=lambda _: None)
        cached_path = self.root / "records/posts/1/source-featured-media.json"
        original = cached_path.read_bytes()
        for raw in (None, b"invalid JSON", sync.json_bytes({"id": 999})):
            with self.subTest(raw=raw):
                if raw is None:
                    cached_path.unlink()
                else:
                    cached_path.write_bytes(raw)
                try:
                    with self.assertRaises(ValueError):
                        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
                finally:
                    cached_path.write_bytes(original)

    def test_empty_page_api_uses_public_content_and_downloads_its_images(self):
        page = post()
        page["type"] = "page"
        page["content"]["rendered"] = ""
        def inventory(client, kind, **kwargs):
            fields = ("id", "type", "status", "modified", "modified_gmt", "date_gmt")
            return Inventory([{k:page[k] for k in fields}] if kind == "pages" else [], [])
        def run():
            return sync.run_sync(self.root, client=self.client, inventory_loader=inventory,
                                 record_loader=lambda *a, **k: Inventory([page], []),
                                 metadata_loader=lambda *a, **k: Inventory([], []), progress=lambda _: None)
        self.assertEqual(run()["incomplete_records"], 1)
        self.assertFalse(self.state()["records"]["page:1"]["derived_view_available"])
        self.client.payloads[page["link"]] = b'<nav>Navigation</nav><div class="ct-inner-content"><h1>Title 1</h1><p>Actual page</p><img src="/builder.png"></div><footer>Footer</footer>'
        report = run()
        self.assertEqual(report["refreshed_pages"], 1)
        self.assertEqual(report["full_records_fetched"], 1)
        self.assertEqual(report["incomplete_records"], 0)
        text = self.article_file(1, "page").read_text(encoding="utf-8")
        self.assertIn("Actual page", text)
        self.assertNotIn("Navigation", text)
        self.assertNotIn("Footer", text)
        self.assertIn("https://kiszamolo.hu/builder.png", text)
        self.assertEqual(self.state()["records"]["page:1"]["derived_source"], "public_page_content")

    def test_cached_page_repair_preserves_public_body_assets_and_embeds(self):
        self.records = []
        self.pages = [page()]
        self.pages[0]["content"]["rendered"] = ""
        self.client.payloads[self.pages[0]["link"]] = (
            b'<div class="ct-inner-content"><p>Actual page</p><img src="/builder.png">'
            b'<iframe src="https://www.youtube.com/embed/example"></iframe></div>')
        self.run_sync()
        before = self.state()["records"]["page:1"]
        before_markdown = self.article_file(1, "page").read_bytes()
        for sample_ids in (None, {1}):
            with self.subTest(sample_ids=sample_ids):
                self.article_file(1, "page").unlink()
                self.client.calls.clear()
                self.detail_calls.clear()
                self.run_sync(sample_ids=sample_ids)
                after = self.state()["records"]["page:1"]
                self.assertEqual(after["external_assets"], before["external_assets"])
                self.assertEqual(after["embed_urls"], before["embed_urls"])
                self.assertEqual(self.article_file(1, "page").read_bytes(), before_markdown)
                self.assertEqual(self.client.calls, [])
                self.assertEqual(self.detail_calls, [])

    def test_new_article_localizes_existing_links_without_rerendering_referer(self):
        self.records = [post(body='<a href="http://www.kiszamolo.hu/p2/#part">Next</a>'
                                 '<iframe src="https://kiszamolo.hu/p2/embed/"></iframe>')]
        self.run_sync()
        incoming = self.article_file(1)
        incoming.write_text(incoming.read_text(encoding="utf-8") + "\nLocal note.\n", encoding="utf-8")
        self.records.append(post(2))
        self.detail_calls.clear()
        original_render = sync.render_views

        def render_new_article(*args, **kwargs):
            self.assertEqual([record["id"] for record in args[3]], [2])
            return original_render(*args, **kwargs)

        with patch("sync.render_views", side_effect=render_new_article):
            report = self.run_sync()
        text = incoming.read_text(encoding="utf-8")
        self.assertIn("[Next](2011-01-01%2000.00%20Title%202.md#part)", text)
        self.assertNotIn("https://kiszamolo.hu/p2/embed/", text)
        self.assertIn("Local note.", text)
        self.assertEqual(self.detail_calls, [("posts", [2])])
        self.assertEqual(report["links_repaired"], 1)

    def test_renamed_target_repairs_existing_linked_image(self):
        self.records[0] = post(body='<a href="/p2/"><img src="/a.png" alt="cover"></a>')
        self.run_sync()
        old = self.article_file(2)
        self.records[1]["title"]["rendered"] = "Renamed target"
        self.records[1]["modified"] = self.records[1]["modified_gmt"] = "2026-10-08T12:00:00"
        self.run_sync()
        text = self.article_file(1).read_text(encoding="utf-8")
        self.assertIn("[![cover](https://kiszamolo.hu/a.png", text)
        self.assertIn("](2011-01-01%2000.00%20Renamed%20target.md)", text)
        self.assertFalse(old.exists())

    def test_renamed_target_repairs_existing_link_with_inline_code_label(self):
        self.records[0] = post(body='<a href="/p2/">Some <code>code</code> link</a>')
        self.run_sync()
        old = self.article_file(2)
        self.records[1]["title"]["rendered"] = "Renamed target"
        self.records[1]["modified"] = self.records[1]["modified_gmt"] = "2026-10-08T12:00:00"
        self.run_sync()
        self.assertIn(
            "[Some `code` link](2011-01-01%2000.00%20Renamed%20target.md)",
            self.article_file(1).read_text(encoding="utf-8"))
        self.assertFalse(old.exists())

    def test_title_date_changes_move_markdown_and_update_incoming_links(self):
        self.run_sync()
        old = self.article_file(2)
        self.records[1]["title"]["rendered"] = "Changed title?"
        self.records[1]["date"] = "2020-03-04T12:34:56"
        self.records[1]["date_gmt"] = "2020-03-04T11:34:56"
        self.records[1]["modified"] = "2020-03-04T12:34:56"
        self.records[1]["modified_gmt"] = "2020-03-04T11:34:56"
        self.run_sync()
        new = self.article_file(2)
        self.assertEqual(new.relative_to(self.articles).as_posix(), "2020/03/2020-03-04 12.34 Changed title.md")
        self.assertTrue(new.is_file())
        self.assertFalse(old.exists())
        self.assertIn("../../2020/03/2020-03-04%2012.34%20Changed%20title.md#part", self.article_file(1).read_text(encoding="utf-8"))

    def test_renamed_target_repairs_manually_edited_incoming_markdown(self):
        self.run_sync()
        incoming = self.article_file(1)
        incoming.write_text(incoming.read_text(encoding="utf-8") + "\nLocal note.\n", encoding="utf-8")
        self.records[1]["title"]["rendered"] = "Renamed target"
        self.records[1]["modified"] = self.records[1]["modified_gmt"] = "2026-10-08T12:00:00"
        report = self.run_sync()
        text = incoming.read_text(encoding="utf-8")
        self.assertIn("Local note.", text)
        self.assertIn("Renamed%20target.md#part", text)
        self.assertEqual(report["links_repaired"], 1)

    def test_renaming_preserves_locally_modified_old_file(self):
        self.run_sync()
        old = self.article_file(2)
        old.write_text("User edit", encoding="utf-8")
        self.records[1]["title"]["rendered"] = "New title"
        self.records[1]["modified"] = self.records[1]["modified_gmt"] = "2026-10-08T12:00:00"
        report = self.run_sync()
        self.assertEqual(old.read_text(encoding="utf-8"), "User edit")
        self.assertEqual(report["obsolete_locally_modified_files_preserved"], ["2011/01/2011-01-01 00.00 Title 2.md"])

    def test_missing_record_collision_keeps_both_files_and_local_links(self):
        self.run_sync()
        self.records = [post(3)]
        self.records[0]["title"]["rendered"] = "Title 2"
        self.run_sync()
        state = self.state()
        self.assertIn("[2]", state["records"]["post:2"]["path"])
        self.assertIn("[3]", state["records"]["post:3"]["path"])
        self.assertTrue(self.article_file(2).is_file())
        self.assertTrue(self.article_file(3).is_file())

    def test_title_swap_preserves_modified_previous_owner_before_overwrite(self):
        self.run_sync()
        old = self.article_file(1)
        old.write_bytes(b"User edit")
        self.records[0]["title"]["rendered"] = "Title 2"
        self.records[1]["title"]["rendered"] = "Title 1"
        for record in self.records:
            record["modified"] = record["modified_gmt"] = "2026-10-08T12:00:00"
        report = self.run_sync()
        saved = report["reassigned_locally_modified_files_preserved"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["previous_key"], "post:1")
        self.assertEqual(saved[0]["new_key"], "post:2")
        self.assertEqual((self.root/saved[0]["preserved_source_path"]).read_bytes(), b"User edit")
        self.assertIn("id: 2", old.read_text(encoding="utf-8"))

    def test_shared_markdown_root_is_locked_across_different_source_roots(self):
        with sync.exclusive_run(self.articles):
            with self.assertRaises(OSError):
                self.run_sync()
            with self.assertRaises(OSError):
                sync.run_sync(Path(self.temp.name)/"other-source", articles_root=self.articles,
                              client=self.client, inventory_loader=self.inventory,
                              metadata_loader=lambda *a, **k: Inventory([], []), progress=lambda _: None)
        self.assertFalse(self.client.calls)

    def test_old_modified_record_is_fetched_selectively(self):
        self.run_sync()
        self.detail_calls.clear()
        self.records[0] = post(modified="2026-10-08T12:00:00", body="<p>Changed</p>")
        report = self.run_sync()
        self.assertEqual(self.detail_calls, [("posts", [1])])
        self.assertEqual(report["cached_records_reused"], 1)
        self.assertIn("Changed", self.article_file(1).read_text())

    def test_recent_unchanged_record_reuses_source_and_does_not_redownload_page_or_media(self):
        self.records[0] = post(date="2099-01-01T00:00:00")
        self.run_sync()
        self.detail_calls.clear()
        self.client.calls.clear()
        state = self.state()
        protected = {
            self.root / item["directory"] / name
            for item in state["records"].values()
            for name in ("source.json", "source.html", "view-source.html", "source-page.html")
        }
        protected.update(self.articles / item["path"] for item in state["records"].values())
        original_read_bytes = Path.read_bytes

        def guarded_read_bytes(path):
            if path in protected:
                raise AssertionError(f"Unchanged content was read: {path}")
            return original_read_bytes(path)

        with patch.object(Path, "read_bytes", guarded_read_bytes), \
             patch("sync.discover_record", side_effect=AssertionError("Unchanged article was parsed")), \
             patch("sync.load_cached_view", side_effect=AssertionError("Unchanged source was loaded")):
            report = self.run_sync()
        self.assertEqual(self.detail_calls, [])
        self.assertEqual(self.client.calls, [])
        self.assertEqual(report["processed_records"], 0)
        self.assertEqual(report["rendered_records"], 0)
        self.assertEqual(self.state()["records"]["post:1"]["source_sha256"],
                         sync.digest(sync.json_bytes(self.records[0])))

    def test_missing_markdown_repairs_only_that_record_from_cache(self):
        self.run_sync()
        (self.article_file(1)).unlink()
        self.detail_calls.clear()
        self.client.calls.clear()
        report = self.run_sync()
        self.assertEqual(self.detail_calls, [])
        self.assertEqual(self.client.calls, [])
        self.assertEqual(report["processed_records"], 1)
        self.assertEqual(report["rendered_records"], 1)
        self.assertTrue(self.article_file(1).is_file())

    def test_missing_raw_source_is_refetched(self):
        self.run_sync()
        (self.root/"records/posts/1/source.json").unlink()
        self.detail_calls.clear()
        self.run_sync()
        self.assertEqual(self.detail_calls, [("posts", [1])])
        self.assertEqual(json.loads((self.root/"records/posts/1/source.json").read_text())["id"], 1)

    def test_offline_rebuild_rejects_corrupt_cached_sources(self):
        self.run_sync()
        self.records = [post()]
        before_state = (self.root / "state.json").read_bytes()
        before_index = (self.articles / "index.jsonl").read_bytes()
        before_markdown = self.article_file(2).read_bytes()
        directory = self.root / "records/posts/2"
        for filename in ("source.json", "source.html", "view-source.html"):
            with self.subTest(filename=filename):
                path = directory / filename
                original = path.read_bytes()
                if filename == "source.json":
                    record = json.loads(original)
                    record["title"]["rendered"] = "Corrupted title"
                    corrupt = sync.json_bytes(record)
                else:
                    corrupt = original + b"<p>Corrupted content</p>"
                path.write_bytes(corrupt)
                try:
                    with self.assertRaisesRegex(ValueError, "Cached .* mismatch"):
                        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
                    self.assertEqual((self.root / "state.json").read_bytes(), before_state)
                    self.assertEqual((self.articles / "index.jsonl").read_bytes(), before_index)
                    self.assertEqual(self.article_file(2).read_bytes(), before_markdown)
                finally:
                    path.write_bytes(original)

    def test_census_detail_race_does_not_advance_state(self):
        self.run_sync()
        before = (self.root/"state.json").read_bytes()
        self.records[0]["modified"] = "2026-10-08T12:00:00"
        def racing_details(*args, **kwargs):
            inventory = self.details(*args, **kwargs)
            inventory.records[0]["modified"] = "2026-10-08T12:01:00"
            return inventory
        with self.assertRaisesRegex(ValueError, "changed during census"):
            sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                          record_loader=racing_details, progress=lambda _: None)
        self.assertEqual((self.root/"state.json").read_bytes(), before)

    def test_normal_metadata_is_limited_and_verified_featured_cache_is_reused(self):
        self.records[0]["featured_media"] = 100
        calls = []
        def metadata(client, kind, **kwargs):
            calls.append((kind, kwargs))
            if kind == "media" and kwargs["ids"]:
                return Inventory([{"id":100,"media_type":"image","source_url":"https://kiszamolo.hu/hero.png"}], [])
            return Inventory([], [])
        for _ in range(2):
            calls.clear()
            report = sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                                   record_loader=self.details, metadata_loader=metadata, progress=lambda _: None)
            self.assertEqual(report["page_failures"], {})
            self.assertEqual(report["incomplete_records"], 0)
        self.assertNotIn("media", [kind for kind, _ in calls])
        self.assertTrue(all(kwargs["fields"] == ("id","name") for kind,kwargs in calls if kind!="media"))
        self.assertIn("hero.png", json.dumps(self.state()["records"]["post:1"]["external_assets"]))

    def test_legacy_state_needs_no_modified_gmt_migration(self):
        self.run_sync()
        state = self.state()
        for item in state["records"].values():
            item.pop("modified_at_gmt", None)
        (self.root/"state.json").write_bytes(sync.json_bytes(state))
        self.detail_calls.clear()
        self.run_sync()
        self.assertEqual(self.detail_calls, [])

    def test_failed_page_refetches_source_and_missing_details_abort(self):
        self.client.fail.update(record["link"] for record in self.records)
        self.run_sync()
        self.detail_calls.clear()
        self.run_sync()
        self.assertEqual(self.detail_calls, [("posts", [1, 2])])
        before = (self.root/"state.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "Incomplete targeted"):
            sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                          record_loader=lambda *a, **k: Inventory([], []), progress=lambda _: None)
        self.assertEqual((self.root/"state.json").read_bytes(), before)

    def test_manifest_distinguishes_census_from_targeted_full_records(self):
        def inventory(client, kind, **kwargs):
            value = self.inventory(client, kind, **kwargs)
            return Inventory(value.records, [(f"https://kiszamolo.hu/wp-json/wp/v2/{kind}?_fields=id",
                                              Response(sync.json_bytes(value.records), 200, {}, ""))])
        def details(client, kind, ids, **kwargs):
            value = self.details(client, kind, ids, **kwargs)
            return Inventory(value.records, [(f"https://kiszamolo.hu/wp-json/wp/v2/{kind}?include=1,2",
                                              Response(sync.json_bytes(value.records), 200, {}, ""))])
        report = sync.run_sync(self.root, client=self.client, inventory_loader=inventory,
                               record_loader=details, metadata_loader=lambda *a, **k: Inventory([], []),
                               progress=lambda _: None)
        manifest = json.loads((self.root/"sources/api/manifest.json").read_text())
        self.assertEqual({r["path"].split("/")[2] for r in manifest},
                         {"inventory-posts", "inventory-pages", "details-posts"})
        self.assertEqual(report["api_response_count"], 3)
        self.assertEqual(report["api_response_bytes"], sum(r["size_bytes"] for r in manifest))

    def test_default_roots_are_allowed_but_tool_targets_are_rejected(self):
        self.assertEqual(sync.validate_roots(sync.DEFAULT_SOURCE_OUTPUT, sync.DEFAULT_OUTPUT),
                         (sync.DEFAULT_SOURCE_OUTPUT.resolve(), sync.DEFAULT_OUTPUT.resolve()))
        with self.assertRaises(ValueError):
            sync.validate_roots(self.root, sync.ROOT/"blog/content-mirror/automation"/"output")

    def test_changing_output_while_old_articles_are_locked_leaves_old_output_unchanged(self):
        self.run_sync()
        previous_files = {path: path.read_bytes() for path in self.articles.rglob("*") if path.is_file()}
        other_articles = Path(self.temp.name) / "other-articles"
        with sync.exclusive_run(self.articles):
            sync.run_sync(self.root, articles_root=other_articles, client=self.client,
                          inventory_loader=self.inventory, record_loader=self.details,
                          metadata_loader=lambda *a, **k: Inventory([], []), progress=lambda _: None)
        self.assertEqual({path: path.read_bytes() for path in previous_files}, previous_files)
        self.assertTrue((other_articles / "INDEX.md").is_file())
        self.assertEqual(self.state()["articles_root"], str(other_articles.resolve()))

    def test_offline_rebuild_preserves_crlf_view_source_input(self):
        self.records[0] = post(body='<p>First</p>\r\n<p>Second</p><img src="/a.png">')
        self.run_sync()
        before = self.article_file(1).read_bytes()
        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        self.assertEqual(self.article_file(1).read_bytes(),before)
        self.assertIn("https://kiszamolo.hu/a.png", self.article_file(1).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
