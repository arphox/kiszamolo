"""Focused tests for remote HTTP and WordPress inventory handling."""

import json
import http.client
import threading
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, HTTPServer

from remote import (HttpClient, MissingMediaError, Response, fetch_census,
                    fetch_metadata, fetch_records, TransientDownloadError)


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None, url="http://localhost/"):
        self._body = body
        self._offset = 0
        self._status = status
        self.headers = headers or {}
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def getcode(self):
        return self._status

    def geturl(self):
        return self._url

    def read(self, amount=-1):
        if amount < 0:
            amount = len(self._body) - self._offset
        value = self._body[self._offset:self._offset + amount]
        self._offset += len(value)
        return value


class RemoteTests(unittest.TestCase):
    def test_census_uses_minimal_fields_pages_and_count_probe(self):
        census = [
            {"id": 2, "type": "post", "status": "publish", "modified": "2025-01-02T03:04:05",
             "modified_gmt": "2025-01-02T02:04:05", "date_gmt": "2025-01-01T02:04:05"},
            {"id": 5, "type": "post", "status": "publish", "modified": "2025-02-02T03:04:05",
             "modified_gmt": "2025-02-02T02:04:05", "date_gmt": None},
            {"id": 7, "type": "post", "status": "publish", "modified": "2025-03-02T03:04:05",
             "modified_gmt": "2025-03-02T02:04:05", "date_gmt": None},
        ]

        class Client:
            def __init__(self):
                self.calls = []

            def request_bytes(self, url, max_bytes):
                query = parse_qs(urlsplit(url).query)
                self.calls.append(query)
                if query["_fields"] == ["id"]:
                    batch = [{"id": 2}]
                    total, pages = 3, 3
                else:
                    page = int(query["page"][0])
                    batch = census[(page - 1) * 2:page * 2]
                    total, pages = 3, 2
                return Response(json.dumps(batch).encode(), 200, {
                    "X-WP-Total": str(total), "X-WP-TotalPages": str(pages),
                }, url)

        client = Client()
        inventory = fetch_census(client, "posts", api_base="https://example.test/wp-json/wp/v2",
                                 page_size=2)
        self.assertEqual(inventory.records, census)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(client.calls[0]["_fields"], ["id,type,status,modified,modified_gmt,date_gmt"])
        self.assertEqual(client.calls[1]["_fields"], ["id,type,status,modified,modified_gmt,date_gmt"])
        self.assertEqual(client.calls[2]["_fields"], ["id"])

    def test_census_rejects_bad_timestamps_duplicate_ids_and_count_races(self):
        valid = {"id": 1, "type": "page", "status": "publish", "modified": "2025-01-01T00:00:00",
                 "modified_gmt": "2025-01-01T00:00:00", "date_gmt": None}

        class Client:
            def __init__(self, pages):
                self.pages = iter(pages)

            def request_bytes(self, url, max_bytes):
                body, total, total_pages = next(self.pages)
                return Response(json.dumps(body).encode(), 200, {
                    "X-WP-Total": str(total), "X-WP-TotalPages": str(total_pages),
                }, url)

        malformed = dict(valid, modified="2025-02-30T00:00:00")
        with self.assertRaisesRegex(ValueError, "modified timestamp"):
            fetch_census(Client([([malformed], 1, 1)]), "pages",
                         api_base="https://example.test/wp-json/wp/v2")

        malformed_date = dict(valid, date_gmt="")
        with self.assertRaisesRegex(ValueError, "date_gmt timestamp"):
            fetch_census(Client([([malformed_date], 1, 1)]), "pages",
                         api_base="https://example.test/wp-json/wp/v2")

        with self.assertRaisesRegex(ValueError, "duplicate or not in ascending order"):
            fetch_census(Client([([valid, valid], 2, 1)]), "pages",
                         api_base="https://example.test/wp-json/wp/v2")

        changed = Client([([valid], 1, 1), ([{"id": 1}], 2, 2)])
        with self.assertRaisesRegex(ValueError, "count changed"):
            fetch_census(changed, "pages", api_base="https://example.test/wp-json/wp/v2")

    def test_targeted_record_fetch_batches_and_validates_each_batch(self):
        requested = list(range(1, 102))

        def full_record(item_id):
            return {"id": item_id, "date": "2025-01-01T12:00:00", "link": f"https://example.test/{item_id}/",
                    "title": {"rendered": f"Title {item_id}"}, "status": "publish", "type": "post",
                    "content": {"rendered": f"Body {item_id}", "protected": False}}

        class Client:
            def __init__(self):
                self.calls = []

            def request_bytes(self, url, max_bytes):
                query = parse_qs(urlsplit(url).query)
                self.calls.append(query)
                ids = [int(item_id) for item_id in query["include"][0].split(",")]
                return Response(json.dumps([full_record(item_id) for item_id in ids]).encode(), 200, {}, url)

        client = Client()
        inventory = fetch_records(client, "posts", requested,
                                  api_base="https://example.test/wp-json/wp/v2")
        self.assertEqual([record["id"] for record in inventory.records], requested)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(client.calls[0]["include"][0].split(",")), 100)
        self.assertEqual(client.calls[0]["status"], ["publish"])
        self.assertEqual(client.calls[0]["orderby"], ["id"])
        self.assertEqual(len(client.calls[1]["include"][0].split(",")), 1)

        class MissingClient:
            def request_bytes(self, url, max_bytes):
                return Response(json.dumps([full_record(1)]).encode(), 200, {}, url)

        with self.assertRaisesRegex(ValueError, "omitted requested posts IDs"):
            fetch_records(MissingClient(), "posts", [1, 2],
                          api_base="https://example.test/wp-json/wp/v2")

    def test_targeted_empty_ids_make_no_request(self):
        class Client:
            def request_bytes(self, *args, **kwargs):
                raise AssertionError("empty record list should not make a request")

        self.assertEqual(fetch_records(Client(), "pages", []).records, [])

    def test_retries_transient_status_and_sends_user_agent(self):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            if len(calls) == 1:
                raise HTTPError(request.full_url, 503, "busy", {"Retry-After": "0"}, None)
            return FakeResponse(b"ok", headers={"Content-Length": "2"}, url=request.full_url)

        response = HttpClient(opener=opener, retry_delay=0).request_bytes("https://example.test/data")
        self.assertEqual(response.body, b"ok")
        self.assertEqual(response.status, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0].get_header("User-agent"), "KiszamoloContentMirror/1.0")

    def test_conditional_304_is_returned(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 304, "not modified", {"ETag": '"x"'}, None)

        response = HttpClient(opener=opener).request_bytes(
            "https://example.test/data", {"If-None-Match": '"x"'})
        self.assertEqual(response.status, 304)
        self.assertEqual(response.body, b"")

    def test_rejects_non_http_and_userinfo(self):
        client = HttpClient()
        for url in ("file:///tmp/a", "https://name:secret@example.test/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                client.request_bytes(url)

    def test_allowed_hosts_rejects_initial_and_fake_redirect_hosts(self):
        calls = []
        client = HttpClient(allowed_hosts={"kiszamolo.hu"}, opener=lambda request, timeout: (
            calls.append(request.full_url) or FakeResponse(
                b"ok", headers={"Content-Length": "2"}, url="https://outside.test/file")))
        with self.assertRaisesRegex(ValueError, "outside the allowed host list"):
            client.request_bytes("https://outside.test/file")
        self.assertEqual(calls, [])
        with self.assertRaisesRegex(ValueError, "outside the allowed host list"):
            client.request_bytes("https://kiszamolo.hu/file")
        self.assertEqual(len(calls), 1)

    def test_request_url_quotes_unicode_without_double_encoding_escapes(self):
        seen = []

        def opener(request, timeout):
            seen.append(request.full_url)
            return FakeResponse(b"ok", headers={"Content-Length": "2"}, url=request.full_url)

        client = HttpClient(opener=opener, allowed_hosts={"kiszamolo.hu"})
        client.request_bytes("https://kiszamolo.hu/wp-content/életkor.jpg?név=árvíz&keep=a%20b")
        self.assertEqual(seen, [
            "https://kiszamolo.hu/wp-content/%C3%A9letkor.jpg?n%C3%A9v=%C3%A1rv%C3%ADz&keep=a%20b"
        ])

    def test_request_url_converts_idna_hostname(self):
        seen = []

        def opener(request, timeout):
            seen.append(request.full_url)
            return FakeResponse(b"ok", headers={"Content-Length": "2"}, url=request.full_url)

        client = HttpClient(opener=opener, allowed_hosts={"xn--bcher-kva.example"})
        client.request_bytes("https://bücher.example/é")
        self.assertEqual(seen, ["https://xn--bcher-kva.example/%C3%A9"])

    def test_expected_media_rejects_html_error_document_before_replace(self):
        client = HttpClient(opener=lambda request, timeout: FakeResponse(
            b"<html>missing</html>", headers={
                "Content-Length": "20", "Content-Type": "text/html; charset=utf-8",
            }, url=request.full_url))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "media.jpg"
            target.write_bytes(b"old media")
            with self.assertRaisesRegex(ValueError, "Expected media, received text/html"):
                client.download("https://example.test/media.jpg", target, expected_media=True)
            self.assertEqual(target.read_bytes(), b"old media")

    def test_allowed_hosts_blocks_real_off_host_redirect_before_following(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/target")
                self.end_headers()

            def log_message(self, *_):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                client = HttpClient(allowed_hosts={"127.0.0.1"}, retry_delay=0)
                with self.assertRaisesRegex(ValueError, "outside the allowed host list"):
                    client.request_bytes(f"http://127.0.0.1:{server.server_port}/start")
            finally:
                server.shutdown()
                worker.join(timeout=2)
        self.assertEqual(requests, ["/start"])

    def test_partial_http_response_is_rejected(self):
        client = HttpClient(opener=lambda request, timeout: FakeResponse(
            b"part", status=206, headers={"Content-Length": "4"}, url=request.full_url))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Unexpected HTTP 206"):
                client.download("https://example.test/file", Path(directory) / "file")

    def test_incomplete_read_retries_without_publishing_partial_file(self):
        attempts = []

        class Truncated(FakeResponse):
            def read(self, amount=-1):
                raise http.client.IncompleteRead(b"partial", 4)

        def opener(request, timeout):
            attempts.append(request.full_url)
            if len(attempts) == 1:
                return Truncated(url=request.full_url)
            body = b"complete"
            return FakeResponse(body, headers={"Content-Length": str(len(body))}, url=request.full_url)

        client = HttpClient(opener=opener, retries=2, retry_delay=0)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "file"
            target.write_bytes(b"old")
            result = client.download("https://example.test/file", target)
            self.assertEqual(target.read_bytes(), b"complete")
            self.assertEqual(result.size, len(b"complete"))
            self.assertEqual(len(attempts), 2)

    def test_download_hashes_and_enforces_size(self):
        body = b"small body"
        client = HttpClient(opener=lambda request, timeout: FakeResponse(
            body, headers={"Content-Length": str(len(body))}, url=request.full_url))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "file.bin"
            result = client.download("https://example.test/file", target)
            self.assertEqual(target.read_bytes(), body)
            self.assertEqual(result.size, len(body))
            import hashlib
            self.assertEqual(result.sha256, hashlib.sha256(body).hexdigest())

            too_large = HttpClient(opener=lambda request, timeout: FakeResponse(
                b"1234", headers={"Content-Length": "4"}, url=request.full_url))
            with self.assertRaises(ValueError):
                too_large.download("https://example.test/file", target, max_bytes=3)
            self.assertEqual(target.read_bytes(), body)

    def test_truncated_http_response_retries(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "file.bin"
                target.write_bytes(b"old")
                requests = []
                existing_copies = []

                class Handler(BaseHTTPRequestHandler):
                    def do_GET(self):
                        requests.append(self.path)
                        existing_copies.append(target.read_bytes())
                        self.send_response(200)
                        self.send_header("Content-Length", "8")
                        self.end_headers()
                        self.wfile.write(b"short" if len(requests) == 1 else b"complete")

                    def log_message(self, *_):
                        pass

                with HTTPServer(("127.0.0.1", 0), Handler) as server:
                    worker = threading.Thread(target=server.serve_forever, daemon=True)
                    worker.start()
                    try:
                        client = HttpClient(retries=3, retry_delay=0)
                        url = f"http://127.0.0.1:{server.server_port}/file"
                        if streaming:
                            result = client.download(url, target)
                            self.assertEqual(result.size, 8)
                            self.assertEqual(target.read_bytes(), b"complete")
                        else:
                            self.assertEqual(client.request_bytes(url).body, b"complete")
                    finally:
                        server.shutdown()
                        worker.join(timeout=2)
                self.assertEqual(requests, ["/file", "/file"])
                self.assertEqual(existing_copies, [b"old", b"old"])
                self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_short_body_retry_exhaustion_preserves_existing_file(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming), tempfile.TemporaryDirectory() as directory:
                calls = []

                def opener(request, timeout):
                    calls.append(request.full_url)
                    return FakeResponse(b"short", headers={"Content-Length": "8"}, url=request.full_url)

                client = HttpClient(opener=opener, retries=3, retry_delay=0)
                target = Path(directory) / "file.bin"
                target.write_bytes(b"old")
                expected_error = TransientDownloadError if streaming else ValueError
                with self.assertRaisesRegex(expected_error, "Content-Length mismatch"):
                    if streaming:
                        client.download("https://example.test/file", target)
                    else:
                        client.request_bytes("https://example.test/file")
                self.assertEqual(len(calls), 3)
                self.assertEqual(target.read_bytes(), b"old")
                self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_invalid_lengths_and_size_limits_are_not_retried(self):
        for streaming in (False, True):
            for length, max_bytes in (("invalid", 10), ("-1", 10), ("2", 10), ("3", 2)):
                with self.subTest(streaming=streaming, length=length, max_bytes=max_bytes):
                    calls = []

                    def opener(request, timeout):
                        calls.append(request.full_url)
                        return FakeResponse(b"new", headers={"Content-Length": length}, url=request.full_url)

                    client = HttpClient(opener=opener, retries=3, retry_delay=0)
                    with tempfile.TemporaryDirectory() as directory:
                        target = Path(directory) / "file.bin"
                        target.write_bytes(b"old")
                        with self.assertRaises(ValueError):
                            if streaming:
                                client.download("https://example.test/file", target, max_bytes=max_bytes)
                            else:
                                client.request_bytes("https://example.test/file", max_bytes=max_bytes)
                        self.assertEqual(target.read_bytes(), b"old")
                        self.assertEqual(list(Path(directory).glob(".*.tmp")), [])
                    self.assertEqual(len(calls), 1)

    def test_length_mismatch_preserves_existing_file(self):
        client = HttpClient(opener=lambda request, timeout: FakeResponse(
            b"new", headers={"Content-Length": "4"}, url=request.full_url), retries=1)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "file.bin"
            target.write_bytes(b"old")
            with self.assertRaises(TransientDownloadError):
                client.download("https://example.test/file", target)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_interrupted_download_preserves_existing_file(self):
        class Interrupted(FakeResponse):
            def read(self, amount=-1):
                if self._offset:
                    raise URLError("connection interrupted")
                self._offset = 3
                return b"new"

        client = HttpClient(opener=lambda request, timeout: Interrupted(
            b"newbody", headers={"Content-Length": "7"}, url=request.full_url), retries=1)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "file.bin"
            target.write_bytes(b"old")
            with self.assertRaises(TransientDownloadError):
                client.download("https://example.test/file", target)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_targeted_records_keep_full_records_and_reject_duplicate_ids(self):
        record = {
            "id": 2, "date": "2025-01-01T12:00:00", "link": "https://example.test/post/",
            "title": {"rendered": "Title"}, "status": "publish", "type": "post",
            "content": {"rendered": "<p>Text</p>", "protected": False}, "custom": {"x": 1},
        }

        class Client:
            def __init__(self, batch):
                self.batch = batch

            def request_bytes(self, url, max_bytes):
                return Response(json.dumps(self.batch).encode(), 200, {}, url)

        inventory = fetch_records(Client([record]), "posts", [2],
                                  api_base="https://example.test/wp-json/wp/v2")
        self.assertEqual(inventory.records, [record])
        self.assertEqual(inventory.records[0]["custom"], {"x": 1})
        self.assertEqual(len(inventory.responses), 1)

        duplicate = dict(record, id=2)
        with self.assertRaisesRegex(ValueError, "duplicate record IDs"):
            fetch_records(Client([record, duplicate]), "posts", [2],
                          api_base="https://example.test/wp-json/wp/v2")

    def test_targeted_pages_use_singular_api_type(self):
        record = {
            "id": 7, "date": "2025-01-01T12:00:00", "link": "https://example.test/about/",
            "title": {"rendered": "About"}, "status": "publish", "type": "page",
            "content": {"rendered": "Page body", "protected": False},
        }

        class Client:
            def request_bytes(self, url, max_bytes):
                return Response(json.dumps([record]).encode(), 200, {}, url)

        inventory = fetch_records(Client(), "pages", [7], api_base="https://example.test/wp-json/wp/v2")
        self.assertEqual(inventory.records, [record])

    def test_metadata_fetches_all_taxonomy_pages_and_rechecks_count(self):
        terms = [{"id": 4, "name": "One", "extra": {"kept": True}},
                 {"id": 9, "name": "Two"}]

        class Client:
            def __init__(self):
                self.calls = []

            def request_bytes(self, url, max_bytes):
                self.calls.append(url)
                batch = terms if len(self.calls) == 1 else []
                return Response(json.dumps(batch).encode(), 200, {
                    "X-WP-Total": "2", "X-WP-TotalPages": "1" if "per_page=100" in url else "2",
                }, url)

        client = Client()
        inventory = fetch_metadata(client, "categories", api_base="https://example.test/wp-json/wp/v2")
        self.assertEqual(inventory.records, terms)
        self.assertEqual(inventory.records[0]["extra"], {"kept": True})
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(inventory.responses[0][1].body, json.dumps(terms).encode())

    def test_taxonomy_fields_limit_payload_and_count_probe_to_id(self):
        terms = [{"id": 4, "name": "One"}]

        class Client:
            def __init__(self):
                self.queries = []

            def request_bytes(self, url, max_bytes):
                query = parse_qs(urlsplit(url).query)
                self.queries.append(query)
                batch = terms if query["per_page"] == ["100"] else [{"id": 4}]
                return Response(json.dumps(batch).encode(), 200, {
                    "X-WP-Total": "1", "X-WP-TotalPages": "1",
                }, url)

        client = Client()
        inventory = fetch_metadata(client, "categories", fields=("id", "name"),
                                   api_base="https://example.test/wp-json/wp/v2")
        self.assertEqual(inventory.records, terms)
        self.assertEqual(client.queries[0]["_fields"], ["id,name"])
        self.assertEqual(client.queries[1]["_fields"], ["id"])

    def test_media_metadata_batches_include_ids_in_groups_of_100(self):
        requested = list(range(1, 102))

        class Client:
            def __init__(self):
                self.calls = []

            def request_bytes(self, url, max_bytes):
                self.calls.append(url)
                query = parse_qs(urlsplit(url).query)
                batch_ids = [int(value) for value in query["include"][0].split(",")]
                records = [{"id": item_id, "source": "preserved"} for item_id in batch_ids]
                return Response(json.dumps(records).encode(), 200, {}, url)

        client = Client()
        inventory = fetch_metadata(client, "media", requested,
                                   api_base="https://example.test/wp-json/wp/v2")
        self.assertEqual([record["id"] for record in inventory.records], requested)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(parse_qs(urlsplit(client.calls[0]).query)["include"][0].split(",")), 100)
        self.assertEqual(len(parse_qs(urlsplit(client.calls[1]).query)["include"][0].split(",")), 1)

    def test_media_metadata_reports_missing_requested_ids(self):
        class Client:
            def request_bytes(self, url, max_bytes):
                return Response(json.dumps([{"id": 1}]).encode(), 200, {}, url)

        with self.assertRaisesRegex(MissingMediaError, "omitted 1 requested media") as caught:
            fetch_metadata(Client(), "media", [1, 2],
                           api_base="https://example.test/wp-json/wp/v2")
        self.assertEqual(caught.exception.missing_ids, {2})
        self.assertEqual(caught.exception.partial_inventory.records, [{"id": 1}])
        self.assertEqual(len(caught.exception.partial_inventory.responses), 1)


if __name__ == "__main__":
    unittest.main()
