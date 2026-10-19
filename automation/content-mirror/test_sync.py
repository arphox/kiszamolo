"""Deterministic sync lifecycle tests with no remote network access."""

from datetime import datetime, timezone
import hashlib
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from remote import Download, Inventory, Response
from urllib.error import HTTPError
import sync


def post(number=1, *, modified="2011-01-01T00:00:00", date="2011-01-01T00:00:00", body=None):
    return dict(id=number, type="post", status="publish", date=date, date_gmt=date,
                modified=modified, modified_gmt=modified, link=f"https://kiszamolo.hu/p{number}/",
                title={"rendered": f"Title {number}"},
                content={"rendered": body or '<p>A <a href="/p2/#part">link</a></p><img src="/a.png"><img src="https://other.test/b.png">',
                         "protected": False}, categories=[], tags=[])


class FakeClient:
    def __init__(self):
        self.calls = []
        self.fail = set()
        self.payloads = {}
        self.live_absent_candidate = False

    def request_bytes(self, url, **kwargs):
        if self.live_absent_candidate:
            return Response(b"{}", 200, {}, url)
        raise HTTPError(url, 404, "Not found", {}, None)

    def download(self, url, target, conditional_headers=None, **kwargs):
        self.calls.append((url, conditional_headers))
        if url in self.fail:
            raise OSError("Simulated network failure")
        body = self.payloads.get(url, b"data")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        return Download(200, {"ETag": '"tag"', "Content-Type": "text/html" if url.endswith("/") else "image/png"}, url, len(body), hashlib.sha256(body).hexdigest())


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "mirror"
        self.articles = Path(self.temp.name) / "articles"
        self.client = FakeClient()
        self.records = [post(), post(2)]
        self.detail_calls = []

    def tearDown(self):
        self.temp.cleanup()

    def inventory(self, client, kind, **kwargs):
        fields = ("id", "type", "status", "modified", "modified_gmt", "date_gmt")
        return Inventory([{k:r[k] for k in fields} for r in self.records] if kind == "posts" else [], [])

    def details(self, client, kind, ids, **kwargs):
        self.detail_calls.append((kind, list(ids)))
        return Inventory([json.loads(json.dumps(r)) for r in self.records
                          if r["id"] in ids and r["type"] + "s" == kind], [])

    def run_sync(self, **kwargs):
        return sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                             record_loader=self.details,
                             metadata_loader=lambda *a, **k: Inventory([], []),
                             progress=lambda _: None, **kwargs)

    def state(self):
        return json.loads((self.root / "state.json").read_text(encoding="utf-8"))

    def article_file(self, number, kind="post"):
        return self.articles / self.state()["records"][f"{kind}:{number}"]["path"]

    def test_full_download_local_links_and_external_policy(self):
        report = self.run_sync()
        self.assertEqual(report["incomplete_records"], 0)
        self.assertTrue(all("other.test" not in url for url, _ in self.client.calls))
        text = self.article_file(1).read_text(encoding="utf-8")
        self.assertIn("2011-01-01%2000.00%20Title%202.md#part", text)
        self.assertIn("../../assets/", text)
        self.assertFalse(list(self.articles.rglob("*.html")))
        self.assertTrue((self.root / "records/posts/1/source.html").exists())
        self.assertIn("https://other.test/b.png", text)
        self.assertIn("other.test", report["media_hosts"])

    def test_unchanged_timestamp_reuses_cache_and_full_detects_content_change(self):
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
        report = self.run_sync(full=True)
        self.assertEqual(report["full_records_fetched"], 2)
        self.assertIn("Changed without modified timestamp", self.article_file(1).read_text())

    def test_recent_window_is_relative_to_last_completed_run(self):
        cutoff = sync.recent_cutoff({"last_completed_sync_at": "2026-10-01T00:00:00+00:00"}, datetime(2026, 10, 8, tzinfo=timezone.utc))
        self.assertEqual(cutoff.isoformat(), "2026-09-24T00:00:00+00:00")
        p = post(date="2026-09-25T00:00:00")
        previous = {"status": "active", "source_sha256": sync.digest(sync.json_bytes(p)), "page": {"status": "downloaded"}}
        self.assertTrue(sync.should_refresh(p, previous, cutoff))

    def test_failed_asset_is_explicit_and_retried(self):
        self.client.fail.add("https://kiszamolo.hu/a.png")
        self.assertEqual(self.run_sync()["incomplete_records"], 2)
        self.assertEqual(self.state()["records"]["post:1"]["acquisition_status"], "incomplete")
        self.client.fail.clear()
        self.client.calls.clear()
        self.assertEqual(self.run_sync()["incomplete_records"], 0)
        self.assertIn("https://kiszamolo.hu/a.png", [u for u, _ in self.client.calls])
        self.assertIn("https://kiszamolo.hu/p1/", [u for u, _ in self.client.calls])
        self.assertIn("https://kiszamolo.hu/p2/", [u for u, _ in self.client.calls])

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

    def test_corrupt_cache_does_not_send_conditionals(self):
        self.run_sync()
        state = self.state()
        asset = state["assets"]["https://kiszamolo.hu/a.png"]
        (self.articles / asset["path"]).write_bytes(b"corrupt")
        self.client.calls.clear()
        self.run_sync()
        call = next(c for c in self.client.calls if c[0] == "https://kiszamolo.hu/a.png")
        self.assertIsNone(call[1])

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
        self.assertIn("https://kiszamolo.hu/hero.png", state["records"]["post:1"]["asset_urls"])
        self.assertTrue((self.root / "records/posts/1/source-featured-media.json").is_file())
        text = self.article_file(1).read_text(encoding="utf-8")
        self.assertIn('category_names: ["Finance"]', text)
        self.assertIn("Hero", text)
        self.assertIn("Caption", text)
        before = self.article_file(1).read_bytes()
        def failed_metadata(client, kind, **kwargs):
            if kind == "media":
                raise OSError("Temporary media API outage")
            return metadata(client, kind, **kwargs)
        sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                      record_loader=self.details, full=True, metadata_loader=failed_metadata, progress=lambda _: None)
        self.assertEqual(self.article_file(1).read_bytes(), before)
        self.assertIn("https://kiszamolo.hu/hero.png", self.state()["records"]["post:1"]["asset_urls"])
        self.assertEqual(self.state()["records"]["post:1"]["acquisition_status"], "incomplete")

    def test_empty_page_api_uses_public_content_and_downloads_its_images(self):
        page = post()
        page["type"] = "page"
        page["content"]["rendered"] = ""
        def inventory(client, kind, **kwargs):
            fields = ("id", "type", "status", "modified", "modified_gmt", "date_gmt")
            return Inventory([{k:page[k] for k in fields}] if kind == "pages" else [], [])
        self.client.payloads[page["link"]] = b'<nav>Navigation</nav><div class="ct-inner-content"><h1>Title 1</h1><p>Actual page</p><img src="/builder.png"></div><footer>Footer</footer>'
        report = sync.run_sync(self.root, client=self.client, inventory_loader=inventory,
                              record_loader=lambda *a, **k: Inventory([page], []),
                              metadata_loader=lambda *a, **k: Inventory([], []), progress=lambda _: None)
        self.assertEqual(report["incomplete_records"], 0)
        text = self.article_file(1, "page").read_text(encoding="utf-8")
        self.assertIn("Actual page", text)
        self.assertNotIn("Navigation", text)
        self.assertNotIn("Footer", text)
        self.assertIn("../../assets/", text)
        self.assertEqual(self.state()["records"]["page:1"]["derived_source"], "public_page_content")

    def test_title_date_changes_move_markdown_and_update_incoming_links(self):
        self.run_sync()
        old = self.article_file(2)
        self.records[1]["title"]["rendered"] = "Changed title?"
        self.records[1]["date"] = "2020-03-04T12:34:56"
        self.records[1]["date_gmt"] = "2020-03-04T11:34:56"
        self.run_sync()
        new = self.article_file(2)
        self.assertEqual(new.relative_to(self.articles).as_posix(), "2020/03/2020-03-04 12.34 Changed title.md")
        self.assertTrue(new.is_file())
        self.assertFalse(old.exists())
        self.assertIn("../../2020/03/2020-03-04%2012.34%20Changed%20title.md#part", self.article_file(1).read_text(encoding="utf-8"))

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

    def test_recent_record_full_source_is_refetched_despite_unchanged_timestamp(self):
        self.records[0] = post(date="2099-01-01T00:00:00")
        self.run_sync()
        self.detail_calls.clear()
        self.run_sync()
        self.assertEqual(self.detail_calls, [("posts", [1])])

    def test_corrupt_full_source_is_refetched(self):
        self.run_sync()
        (self.root/"records/posts/1/source.json").write_bytes(b"corrupt")
        self.detail_calls.clear()
        self.run_sync()
        self.assertEqual(self.detail_calls, [("posts", [1])])
        self.assertEqual(json.loads((self.root/"records/posts/1/source.json").read_text())["id"], 1)

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
            sync.run_sync(self.root, client=self.client, inventory_loader=self.inventory,
                          record_loader=self.details, metadata_loader=metadata, progress=lambda _: None)
        self.assertEqual(next(kwargs["ids"] for kind,kwargs in calls if kind=="media"), [])
        self.assertTrue(all(kwargs["fields"] == ("id","name") for kind,kwargs in calls if kind!="media"))
        self.assertIn("hero.png", json.dumps(self.state()["records"]["post:1"]["asset_urls"]))

    def test_legacy_state_needs_no_modified_gmt_migration(self):
        self.run_sync()
        state = self.state()
        for item in state["records"].values():
            item.pop("modified_at_gmt", None)
        (self.root/"state.json").write_bytes(sync.json_bytes(state))
        self.detail_calls.clear()
        self.run_sync()
        self.assertEqual(self.detail_calls, [])

    def test_incomplete_source_is_fetched_and_missing_details_abort(self):
        self.client.fail.add("https://kiszamolo.hu/a.png")
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

    def legacy_asset_layout(self):
        state = self.state()
        state.pop("assets_location", None)
        for item in state["assets"].values():
            if item.get("sha256"):
                old = self.root/item["path"]
                old.parent.mkdir(parents=True, exist_ok=True)
                (self.articles/item["path"]).replace(old)
        (self.root/"state.json").write_bytes(sync.json_bytes(state))
        return state

    def test_offline_rebuild_moves_legacy_media_and_preserves_sync_status(self):
        self.run_sync()
        before = self.legacy_asset_layout()
        report_before = (self.articles/"last-report.json").read_bytes()
        with patch("sync.HttpClient", side_effect=AssertionError("Network forbidden")):
            sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        state = self.state()
        self.assertEqual(state["assets_location"], "articles")
        self.assertEqual(state["last_completed_sync_at"], before["last_completed_sync_at"])
        self.assertEqual(state["last_inventory_at"], before["last_inventory_at"])
        self.assertEqual((self.articles/"last-report.json").read_bytes(), report_before)
        self.assertFalse((self.root/"assets").exists())
        self.assertIn("../../assets/", self.article_file(1).read_text())
        entries = [json.loads(line) for line in (self.articles/"index.jsonl").read_text().splitlines()]
        self.assertEqual(entries[0]["source_record"], "records/posts/1")
        self.assertNotIn("source_directory", entries[0])
        self.assertNotIn("directory", entries[0])
        self.assertNotIn("path", entries[0]["page"])
        first = self.article_file(1).read_bytes()
        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        self.assertEqual(self.article_file(1).read_bytes(), first)

    def test_normal_sync_migrates_old_assets_without_refetch(self):
        self.run_sync()
        self.legacy_asset_layout()
        self.client.calls.clear()
        self.run_sync()
        self.assertFalse(self.client.calls)
        self.assertTrue((self.articles/sync.asset_filename("https://kiszamolo.hu/a.png")).is_file())
        self.assertFalse((self.root/"assets").exists())

    def test_conflicting_migration_target_aborts_without_overwrite(self):
        self.run_sync()
        state = self.legacy_asset_layout()
        asset = state["assets"]["https://kiszamolo.hu/a.png"]
        (self.articles/asset["path"]).write_bytes(b"different")
        before = (self.root/"state.json").read_bytes()
        with self.assertRaises(ValueError):
            sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        self.assertEqual((self.root/asset["path"]).read_bytes(), b"data")
        self.assertEqual((self.articles/asset["path"]).read_bytes(), b"different")
        self.assertEqual((self.root/"state.json").read_bytes(), before)

    def test_partial_asset_migration_can_resume(self):
        self.records[0] = post(body='<p>Body</p><img src="/a.png"><img src="/c.png">')
        self.run_sync()
        state = self.legacy_asset_layout()
        first = next(item for item in state["assets"].values() if item.get("sha256"))
        (self.root/first["path"]).replace(self.articles/first["path"])
        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        self.assertFalse((self.root/"assets").exists())
        self.assertEqual(len(list((self.articles/"assets").iterdir())), 2)

    def test_offline_rebuild_preserves_failed_asset_with_valid_cached_copy(self):
        self.run_sync()
        self.client.fail.add("https://kiszamolo.hu/a.png")
        self.run_sync(full=True)
        before = self.legacy_asset_layout()
        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        after = self.state()
        self.assertEqual(after["assets"], before["assets"])
        self.assertEqual(after["records"]["post:1"]["acquisition_status"], "incomplete")
        self.assertIn("../../assets/", self.article_file(1).read_text())

    def test_rebuild_rejects_corrupt_downloaded_asset(self):
        self.run_sync()
        asset = self.state()["assets"]["https://kiszamolo.hu/a.png"]
        (self.articles/asset["path"]).write_bytes(b"corrupt")
        with self.assertRaises(ValueError):
            sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)

    def test_default_roots_are_allowed_but_tool_targets_are_rejected(self):
        self.assertEqual(sync.validate_roots(sync.DEFAULT_SOURCE_OUTPUT, sync.DEFAULT_OUTPUT),
                         (sync.DEFAULT_SOURCE_OUTPUT.resolve(), sync.DEFAULT_OUTPUT.resolve()))
        with self.assertRaises(ValueError):
            sync.validate_roots(self.root, sync.ROOT/"automation"/"output")

    def test_migration_target_directory_aborts_before_any_move(self):
        self.run_sync()
        state = self.legacy_asset_layout()
        asset = state["assets"]["https://kiszamolo.hu/a.png"]
        (self.articles/asset["path"]).mkdir()
        with self.assertRaises(ValueError):
            sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        self.assertTrue((self.root/asset["path"]).is_file())

    def test_cross_volume_move_verifies_copy_before_removing_source(self):
        source = Path(self.temp.name)/"source.bin"
        destination = Path(self.temp.name)/"destination.bin"
        source.write_bytes(b"media")
        replace = sync.os.replace
        calls = []
        def cross_volume_first(old, new):
            calls.append((old,new))
            if len(calls)==1:
                raise OSError(errno.EXDEV, "Different devices")
            return replace(old,new)
        with patch("sync.os.replace", side_effect=cross_volume_first):
            sync.move_asset(source,destination)
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_bytes(),b"media")

    def test_failed_cross_volume_verification_keeps_source_and_cleans_temporary(self):
        source = Path(self.temp.name)/"source.bin"
        destination = Path(self.temp.name)/"destination.bin"
        source.write_bytes(b"media")
        with patch("sync.os.replace", side_effect=OSError(errno.EXDEV,"Different devices")):
            with patch("sync.files_equal", return_value=False):
                with self.assertRaises(OSError):
                    sync.move_asset(source,destination)
        self.assertEqual(source.read_bytes(),b"media")
        self.assertFalse(destination.exists())
        self.assertFalse(list(Path(self.temp.name).glob("*.tmp")))

    def test_changing_output_requires_previous_articles_lock(self):
        self.run_sync()
        asset = self.state()["assets"]["https://kiszamolo.hu/a.png"]
        before = (self.root/"state.json").read_bytes()
        with sync.exclusive_run(self.articles):
            with self.assertRaises(OSError):
                sync.run_sync(self.root, articles_root=Path(self.temp.name)/"other-articles",
                              client=self.client, inventory_loader=self.inventory,
                              record_loader=self.details, metadata_loader=lambda *a,**k: Inventory([],[]),
                              progress=lambda _: None)
        self.assertTrue((self.articles/asset["path"]).is_file())
        self.assertEqual((self.root/"state.json").read_bytes(),before)

    def test_offline_rebuild_preserves_crlf_view_source_input(self):
        self.records[0] = post(body="<p>First</p>\r\n<p>Second</p>")
        self.run_sync()
        before = self.article_file(1).read_bytes()
        sync.offline_rebuild(self.root, self.articles, progress=lambda _: None)
        self.assertEqual(self.article_file(1).read_bytes(),before)


if __name__ == "__main__":
    unittest.main()
