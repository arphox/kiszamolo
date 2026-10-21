"""HTTP and WordPress inventory primitives for the content mirror."""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


API_BASE = "https://kiszamolo.hu/wp-json/wp/v2"
DEFAULT_MAX_BYTES = 32 * 1024 * 1024
PAGE_SIZE = 100
TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class Response:
    body: bytes
    status: int
    headers: dict[str, str]
    url: str


@dataclass(frozen=True)
class Download:
    status: int
    headers: dict[str, str]
    url: str
    size: int | None
    sha256: str | None


@dataclass(frozen=True)
class Inventory:
    records: list[dict]
    responses: list[tuple[str, Response]]


class MissingMediaError(ValueError):
    """A media batch completed, but some requested IDs were absent."""

    def __init__(self, missing_ids: set[int], partial_inventory: Inventory):
        self.missing_ids = set(missing_ids)
        self.partial_inventory = partial_inventory
        sample = ", ".join(str(item_id) for item_id in sorted(self.missing_ids)[:10])
        super().__init__(f"WordPress omitted {len(self.missing_ids)} requested media item(s): {sample}")


class _NetworkError(Exception):
    """Distinguishes transport read failures from local filesystem failures."""

    def __init__(self, original):
        self.original = original
        super().__init__(str(original))


class TransientDownloadError(Exception):
    """A download exhausted retries after a remote transport interruption."""

    def __init__(self, original):
        self.original = original
        super().__init__(str(original))


def _validate_url(url: str) -> None:
    if not isinstance(url, str) or not url:
        raise ValueError("URL must be a non-empty string")
    try:
        parts = urlsplit(url)
        # Accessing .port also validates malformed ports.
        _ = parts.port
    except ValueError as error:
        raise ValueError(f"Invalid URL: {url!r}") from error
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"Only absolute HTTP(S) URLs are allowed: {url!r}")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URLs containing user information are not allowed")


def _idna_hostname(hostname: str) -> str:
    try:
        return hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as error:
        raise ValueError(f"Invalid internationalized hostname: {hostname!r}") from error


def _normalize_request_url(url: str) -> str:
    """Encode Unicode URL components for urllib while preserving existing escapes."""
    _validate_url(url)
    parts = urlsplit(url)
    host = _idna_hostname(parts.hostname)
    if ":" in host:  # IPv6 literals must remain bracketed in the authority.
        host = f"[{host}]"
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    path = quote(parts.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/?%:@!$&'()*+,;=-._~")
    return urlunsplit((parts.scheme.lower(), host, path, query, parts.fragment))


def _headers(source) -> dict[str, str]:
    if hasattr(source, "items"):
        return {str(key): str(value) for key, value in source.items()}
    return {}


def _header(headers: dict[str, str], name: str) -> str | None:
    name = name.lower()
    return next((value for key, value in headers.items() if key.lower() == name), None)


def _check_content_length(headers: dict[str, str], actual: int) -> None:
    value = _header(headers, "Content-Length")
    if value is None:
        return
    try:
        expected = int(value)
    except ValueError as error:
        raise ValueError(f"Invalid Content-Length header: {value!r}") from error
    if actual < expected:
        # Sized urllib reads can return EOF without raising IncompleteRead.
        raise _NetworkError(ValueError(f"Content-Length mismatch: expected {expected}, received {actual}"))
    if expected < 0 or expected != actual:
        raise ValueError(f"Content-Length mismatch: expected {expected}, received {actual}")


class HttpClient:
    """Small stdlib-only HTTP client with bounded retries and download limits."""

    def __init__(self, *, user_agent="KiszamoloContentMirror/1.0", timeout=30,
                 retries=3, retry_delay=1.0, opener=None, allowed_hosts=None):
        if retries < 1:
            raise ValueError("retries must be at least 1")
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.allowed_hosts = (None if allowed_hosts is None else
                              {_idna_hostname(host) for host in allowed_hosts})
        if self.allowed_hosts is not None and not self.allowed_hosts:
            raise ValueError("allowed_hosts cannot be empty")
        if opener is None and self.allowed_hosts is not None:
            self._opener = build_opener(_AllowlistRedirectHandler(self)).open
        else:
            self._opener = opener or urlopen

    def _validate_allowed_host(self, url: str) -> None:
        _validate_url(url)
        if self.allowed_hosts is None:
            return
        host = _idna_hostname(urlsplit(url).hostname)
        if host not in self.allowed_hosts:
            raise ValueError(f"URL host {host!r} is outside the allowed host list")

    def _request(self, url: str, conditional_headers=None):
        self._validate_allowed_host(url)
        headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
        if conditional_headers:
            headers.update(conditional_headers)
        return Request(_normalize_request_url(url), headers=headers)

    def _retry_wait(self, attempt: int, response_headers=None):
        delay = min(60.0, max(0.0, self.retry_delay * (2 ** attempt)))
        retry_after = _header(response_headers or {}, "Retry-After")
        if retry_after:
            try:
                delay = max(delay, min(60.0, max(0.0, float(retry_after))))
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(retry_after).timestamp() - time.time())
                    delay = max(delay, min(60.0, max(0.0, seconds)))
                except (TypeError, ValueError, OverflowError):
                    pass
        time.sleep(delay)

    def _open(self, request):
        try:
            return self._opener(request, timeout=self.timeout)
        except HTTPError as error:
            # urllib represents conditional GETs returning 304 as HTTPError.
            if error.code == 304:
                return error
            raise
        except (URLError, TimeoutError, ConnectionError, OSError) as error:
            raise _NetworkError(error) from error

    @staticmethod
    def _read(response, amount=-1):
        try:
            return response.read(amount)
        except (http.client.IncompleteRead, URLError, TimeoutError, ConnectionError, OSError) as error:
            raise _NetworkError(error) from error

    def request_bytes(self, url: str, conditional_headers=None, *, max_bytes=DEFAULT_MAX_BYTES) -> Response:
        """GET a bounded response body, including a bodyless 304 response."""
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
        request = self._request(url, conditional_headers)
        for attempt in range(self.retries):
            try:
                with self._open(request) as response:
                    status = response.getcode()
                    headers = _headers(response.headers)
                    final_url = response.geturl()
                    self._validate_allowed_host(final_url)
                    if status == 304:
                        return Response(b"", status, headers, final_url)
                    if status != 200:
                        raise ValueError(f"Unexpected HTTP {status} response")
                    body = self._read(response, (max_bytes + 1) if max_bytes is not None else -1)
                    if max_bytes is not None and len(body) > max_bytes:
                        raise ValueError(f"Response exceeds maximum size of {max_bytes} bytes")
                    _check_content_length(headers, len(body))
                    return Response(body, status, headers, final_url)
            except HTTPError as error:
                if error.code not in TRANSIENT_STATUSES or attempt + 1 == self.retries:
                    raise
                error.close()
                self._retry_wait(attempt, _headers(error.headers))
            except _NetworkError as error:
                if attempt + 1 == self.retries:
                    raise error.original
                self._retry_wait(attempt)
        raise RuntimeError("Unreachable retry state")

    def download(self, url: str, target: Path, conditional_headers=None, *,
                 max_bytes=DEFAULT_MAX_BYTES, expected_media=False) -> Download:
        """Stream to a same-directory temporary file and atomically replace target."""
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes cannot be negative")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        request = self._request(url, conditional_headers)
        last_error = None
        for attempt in range(self.retries):
            temporary = None
            try:
                with self._open(request) as response:
                    status = response.getcode()
                    headers = _headers(response.headers)
                    final_url = response.geturl()
                    self._validate_allowed_host(final_url)
                    if status == 304:
                        return Download(status, headers, final_url, None, None)
                    if status != 200:
                        raise ValueError(f"Unexpected HTTP {status} response")
                    if expected_media:
                        content_type = (_header(headers, "Content-Type") or "").split(";", 1)[0].strip().lower()
                        if content_type in {"text/html", "application/xhtml+xml"}:
                            raise ValueError(f"Expected media, received {content_type}")
                    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.",
                                                              suffix=".tmp", dir=target.parent)
                    size = 0
                    digest = hashlib.sha256()
                    with os.fdopen(descriptor, "wb") as output:
                        while True:
                            chunk = self._read(response, 64 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            if max_bytes is not None and size > max_bytes:
                                raise ValueError(f"Download exceeds maximum size of {max_bytes} bytes")
                            digest.update(chunk)
                            output.write(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    _check_content_length(headers, size)
                    os.replace(temporary, target)
                    temporary = None
                    return Download(status, headers, final_url, size, digest.hexdigest())
            except HTTPError as error:
                if error.code not in TRANSIENT_STATUSES or attempt + 1 == self.retries:
                    raise
                last_error = error
                error.close()
                self._retry_wait(attempt, _headers(error.headers))
            except _NetworkError as error:
                if attempt + 1 == self.retries:
                    raise TransientDownloadError(error.original) from error.original
                last_error = error
                self._retry_wait(attempt)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        if last_error:
            raise last_error
        raise RuntimeError("Unreachable retry state")


class _AllowlistRedirectHandler(HTTPRedirectHandler):
    """Reject off-host redirects before urllib sends the redirected request."""

    def __init__(self, client):
        super().__init__()
        self.client = client

    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        normalized_url = _normalize_request_url(new_url)
        self.client._validate_allowed_host(normalized_url)
        return super().redirect_request(request, file_pointer, code, message, headers, normalized_url)


def _response_header(response: Response, name: str) -> int:
    value = _header(response.headers, name)
    if value is None:
        raise ValueError(f"Missing {name} response header")
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"Invalid {name} response header: {value!r}") from error
    if parsed < 0:
        raise ValueError(f"Invalid {name} response header: {value!r}")
    return parsed


def _validate_record(record, kind, previous_id):
    if not isinstance(record, dict):
        raise ValueError("WordPress inventory item must be an object")
    expected_type = {"posts": "post", "pages": "page"}[kind]
    if record.get("status") != "publish" or record.get("type") != expected_type:
        raise ValueError(f"Unexpected WordPress status/type for {kind}")
    item_id = record.get("id")
    if type(item_id) is not int or item_id <= previous_id:
        raise ValueError("WordPress IDs are duplicate or not in ascending order")
    if not isinstance(record.get("date"), str) or not record["date"].strip():
        raise ValueError("WordPress item has no valid date")
    link = record.get("link")
    _validate_url(link)
    title = record.get("title")
    if not isinstance(title, dict) or not isinstance(title.get("rendered"), str):
        raise ValueError("WordPress item has no rendered title")
    content = record.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("rendered"), str):
        raise ValueError("WordPress item has no rendered content")
    if "protected" in content and type(content["protected"]) is not bool:
        raise ValueError("WordPress content protected flag must be boolean")
    return item_id


_WP_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\Z")


def _validate_census_record(record, kind, previous_id):
    if not isinstance(record, dict):
        raise ValueError("WordPress census item must be an object")
    expected_type = {"posts": "post", "pages": "page"}[kind]
    if record.get("status") != "publish" or record.get("type") != expected_type:
        raise ValueError(f"Unexpected WordPress status/type for {kind} census")
    item_id = record.get("id")
    if type(item_id) is not int or item_id <= previous_id:
        raise ValueError("WordPress census IDs are duplicate or not in ascending order")
    for field in ("modified", "modified_gmt"):
        value = record.get(field)
        if not isinstance(value, str) or not _WP_TIMESTAMP.fullmatch(value):
            raise ValueError(f"WordPress census item has invalid {field} timestamp")
        try:
            datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
        except ValueError as error:
            raise ValueError(f"WordPress census item has invalid {field} timestamp") from error
    date_gmt = record.get("date_gmt")
    if date_gmt is not None:
        if not isinstance(date_gmt, str) or not _WP_TIMESTAMP.fullmatch(date_gmt):
            raise ValueError("WordPress census item has invalid date_gmt timestamp")
        try:
            datetime.strptime(date_gmt, "%Y-%m-%dT%H:%M:%S")
        except ValueError as error:
            raise ValueError("WordPress census item has invalid date_gmt timestamp") from error
    return item_id


def fetch_census(client: HttpClient, kind: str, *, api_base=API_BASE,
                 page_size=PAGE_SIZE, progress=lambda message: None) -> Inventory:
    """Fetch the full published ID/modified-time census with minimal REST fields."""
    if kind not in {"posts", "pages"}:
        raise ValueError("kind must be 'posts' or 'pages'")
    if page_size < 1:
        raise ValueError("page_size must be positive")
    endpoint = api_base.rstrip("/") + "/" + kind
    _validate_url(endpoint)
    fields = "id,type,status,modified,modified_gmt,date_gmt"
    records, responses = [], []
    expected_total = expected_pages = None
    previous_id = 0
    page = 1
    while True:
        query = urlencode({"status": "publish", "per_page": page_size,
                           "page": page, "orderby": "id", "order": "asc",
                           "_fields": fields})
        url = f"{endpoint}?{query}"
        response = client.request_bytes(url, max_bytes=DEFAULT_MAX_BYTES)
        if response.status != 200:
            raise ValueError(f"Unexpected HTTP {response.status} while fetching {kind} census")
        total = _response_header(response, "X-WP-Total")
        total_pages = _response_header(response, "X-WP-TotalPages")
        if total_pages != math.ceil(total / page_size):
            raise ValueError("Invalid WordPress pagination headers")
        if expected_total is None:
            expected_total, expected_pages = total, total_pages
        elif (total, total_pages) != (expected_total, expected_pages):
            raise ValueError("WordPress count changed during census pagination")
        try:
            batch = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"Invalid JSON in WordPress {kind} census") from error
        expected_length = min(page_size, max(0, total - (page - 1) * page_size))
        if not isinstance(batch, list) or len(batch) != expected_length:
            raise ValueError(f"Incomplete WordPress {kind} census page {page}")
        for record in batch:
            previous_id = _validate_census_record(record, kind, previous_id)
            records.append(record)
        responses.append((url, response))
        progress(f"{kind} census: page {page}/{max(1, total_pages)}, {len(records)}/{total}")
        if page >= total_pages:
            break
        page += 1

    # A small second request catches inventory changes during this non-atomic read.
    probe_query = urlencode({"status": "publish", "per_page": 1, "page": 1,
                             "orderby": "id", "order": "asc", "_fields": "id"})
    probe_url = f"{endpoint}?{probe_query}"
    probe = client.request_bytes(probe_url, max_bytes=DEFAULT_MAX_BYTES)
    if probe.status != 200:
        raise ValueError(f"Unexpected HTTP {probe.status} while rechecking {kind} census count")
    final_total = _response_header(probe, "X-WP-Total")
    final_pages = _response_header(probe, "X-WP-TotalPages")
    if final_total != expected_total or final_pages != final_total:
        raise ValueError("WordPress count changed before census completion")
    if len(records) != expected_total:
        raise ValueError(f"Incomplete WordPress {kind} census")
    responses.append((probe_url, probe))
    return Inventory(records, responses)


def fetch_records(client: HttpClient, kind: str, ids, *, api_base=API_BASE,
                  progress=lambda message: None) -> Inventory:
    """Fetch complete published records for the requested post/page IDs only."""
    if kind not in {"posts", "pages"}:
        raise ValueError("kind must be 'posts' or 'pages'")
    requested = list(ids)
    if any(type(item_id) is not int or item_id <= 0 for item_id in requested):
        raise ValueError("record IDs must be positive integers")
    if len(set(requested)) != len(requested):
        raise ValueError("record IDs must be unique")
    if not requested:
        return Inventory([], [])

    endpoint = api_base.rstrip("/") + "/" + kind
    _validate_url(endpoint)
    records, responses = [], []
    for offset in range(0, len(requested), PAGE_SIZE):
        batch_ids = requested[offset:offset + PAGE_SIZE]
        requested_batch = set(batch_ids)
        query = urlencode({"include": ",".join(str(item_id) for item_id in batch_ids),
                           "status": "publish", "per_page": PAGE_SIZE,
                           "orderby": "id", "order": "asc"})
        url = f"{endpoint}?{query}"
        response = client.request_bytes(url, max_bytes=DEFAULT_MAX_BYTES)
        batch = _decode_record_batch(response, kind)
        seen_batch = set()
        previous_id = 0
        for record in batch:
            item_id = record.get("id")
            if type(item_id) is not int or item_id not in requested_batch:
                raise ValueError("WordPress returned an ID outside the requested batch")
            if item_id in seen_batch:
                raise ValueError("WordPress returned duplicate record IDs")
            previous_id = _validate_record(record, kind, previous_id)
            seen_batch.add(item_id)
            records.append(record)
        if seen_batch != requested_batch:
            missing = sorted(requested_batch - seen_batch)
            raise ValueError(f"WordPress omitted requested {kind} IDs: {missing[:10]}")
        responses.append((url, response))
        progress(f"{kind}: fetched batch {offset // PAGE_SIZE + 1}, "
                 f"{min(offset + len(batch_ids), len(requested))}/{len(requested)}")
    return Inventory(records, responses)


def _decode_record_batch(response: Response, kind: str) -> list[dict]:
    if response.status != 200:
        raise ValueError(f"Unexpected HTTP {response.status} while fetching {kind}")
    try:
        records = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Invalid JSON in WordPress {kind} inventory") from error
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError(f"Invalid WordPress {kind} response shape")
    return records


def fetch_metadata(client: HttpClient, kind: str, ids=None, *, api_base=API_BASE,
                   fields=None,
                   progress=lambda message: None) -> Inventory:
    """Fetch public taxonomy metadata or selected media, retaining complete records."""
    if kind not in {"categories", "tags", "media"}:
        raise ValueError("kind must be 'categories', 'tags', or 'media'")
    endpoint = api_base.rstrip("/") + "/" + kind
    _validate_url(endpoint)
    responses, records = [], []

    if kind in {"categories", "tags"}:
        if fields is not None:
            fields = tuple(fields)
            if not fields or any(not isinstance(field, str) or not field for field in fields):
                raise ValueError("taxonomy fields must be non-empty strings")
            fields = ",".join(fields)
        expected_total = expected_pages = None
        previous_id = 0
        page = 1
        while True:
            params = {"per_page": PAGE_SIZE, "page": page,
                      "orderby": "id", "order": "asc"}
            if fields is not None:
                params["_fields"] = fields
            query = urlencode(params)
            url = f"{endpoint}?{query}"
            response = client.request_bytes(url, max_bytes=DEFAULT_MAX_BYTES)
            batch = _decode_record_batch(response, kind)
            total = _response_header(response, "X-WP-Total")
            total_pages = _response_header(response, "X-WP-TotalPages")
            if total_pages != math.ceil(total / PAGE_SIZE):
                raise ValueError(f"Invalid WordPress {kind} pagination headers")
            if expected_total is None:
                expected_total, expected_pages = total, total_pages
            elif (total, total_pages) != (expected_total, expected_pages):
                raise ValueError(f"WordPress {kind} count changed during pagination")
            expected_length = min(PAGE_SIZE, max(0, total - (page - 1) * PAGE_SIZE))
            if len(batch) != expected_length:
                raise ValueError(f"Incomplete WordPress {kind} page {page}")
            for record in batch:
                item_id = record.get("id")
                if type(item_id) is not int or item_id <= previous_id:
                    raise ValueError(f"WordPress {kind} IDs are duplicate or unordered")
                previous_id = item_id
                records.append(record)
            responses.append((url, response))
            progress(f"{kind}: page {page}/{max(1, total_pages)}, {len(records)}/{total}")
            if page >= total_pages:
                break
            page += 1

        probe_params = {"per_page": 1, "page": 1, "orderby": "id", "order": "asc"}
        if fields is not None:
            probe_params["_fields"] = "id"
        probe_query = urlencode(probe_params)
        probe_url = f"{endpoint}?{probe_query}"
        probe = client.request_bytes(probe_url, max_bytes=DEFAULT_MAX_BYTES)
        if probe.status != 200:
            raise ValueError(f"Unexpected HTTP {probe.status} while rechecking {kind} count")
        final_total = _response_header(probe, "X-WP-Total")
        final_pages = _response_header(probe, "X-WP-TotalPages")
        if final_total != expected_total or final_pages != final_total:
            raise ValueError(f"WordPress {kind} count changed before inventory completion")
        if len(records) != expected_total:
            raise ValueError(f"Incomplete WordPress {kind} inventory")
        responses.append((probe_url, probe))
        return Inventory(records, responses)

    if ids is None:
        raise ValueError("ids are required when fetching media")
    requested = list(ids)
    if any(type(item_id) is not int or item_id <= 0 for item_id in requested):
        raise ValueError("media IDs must be positive integers")
    if len(set(requested)) != len(requested):
        raise ValueError("media IDs must be unique")
    requested_ids = set(requested)
    returned_ids = set()
    for offset in range(0, len(requested), PAGE_SIZE):
        batch_ids = requested[offset:offset + PAGE_SIZE]
        query = urlencode({"include": ",".join(str(item_id) for item_id in batch_ids),
                           "per_page": PAGE_SIZE})
        url = f"{endpoint}?{query}"
        response = client.request_bytes(url, max_bytes=DEFAULT_MAX_BYTES)
        batch = _decode_record_batch(response, kind)
        for record in batch:
            item_id = record.get("id")
            if type(item_id) is not int or item_id not in requested_ids:
                raise ValueError("WordPress returned an unrequested media ID")
            if item_id in returned_ids:
                raise ValueError("WordPress returned duplicate media IDs")
            returned_ids.add(item_id)
            records.append(record)
        responses.append((url, response))
        progress(f"media: batch {offset // PAGE_SIZE + 1}, {len(returned_ids)}/{len(requested)}")
    missing = requested_ids - returned_ids
    if missing:
        raise MissingMediaError(missing, Inventory(records, responses))
    return Inventory(records, responses)
