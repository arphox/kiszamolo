"""One-way mirror of current public Kiszamolo posts and pages."""

from __future__ import annotations

import argparse
import concurrent.futures
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import html
import http.client
import json
import os
from pathlib import Path
import re
import errno
import ssl
import shutil
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit, quote
from urllib.error import HTTPError, URLError

from bs4 import BeautifulSoup

from remote import (HttpClient, Inventory, TRANSIENT_STATUSES, TransientDownloadError,
                    fetch_census, fetch_records, fetch_metadata)
from paths import plan_article_paths
from article_links import repair_markdown_links
from render import (conversion_warnings, discover_assets, discover_embeds,
                    extract_public_content, make_markdown, plain_title, referenced_anchors, rewrite_html)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "articles"
DEFAULT_SOURCE_OUTPUT = ROOT / "raw_source" / "content-mirror"
ALLOWED_HOSTS = {"kiszamolo.hu", "www.kiszamolo.hu"}
SCHEMA_VERSION = 1


def load_exclusions(path):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate exclusion key: {key!r}")
            result[key] = value
        return result

    records = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    if not isinstance(records, dict):
        raise ValueError("Exclusions must be an object mapping record keys to reasons")
    for key, reason in records.items():
        if not re.fullmatch(r"(?:post|page):[1-9][0-9]*", key):
            raise ValueError(f"Invalid exclusion key: {key!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"Exclusion reason must be a nonempty string: {key!r}")
    return records


EXCLUDED_RECORDS = load_exclusions(Path(__file__).with_name("exclusions.json"))


def utc_now():
    return datetime.now(timezone.utc)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def files_equal(first, second):
    with Path(first).open("rb") as left, Path(second).open("rb") as right:
        while True:
            a, b = left.read(1024 * 1024), right.read(1024 * 1024)
            if a != b:
                return False
            if not a:
                return True


def move_asset(source, destination):
    """Move one asset atomically, copying through a verified temp on other volumes."""
    try:
        os.replace(source, destination)
        return
    except OSError as error:
        if error.errno != errno.EXDEV and getattr(error, "winerror", None) != 17:
            raise
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp",
                                     dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as target, Path(source).open("rb") as original:
            shutil.copyfileobj(original, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        if not files_equal(source, temporary):
            raise OSError(f"Copied asset verification failed: {source}")
        os.replace(temporary, destination)
        Path(source).unlink()
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write(path, data):
    """Preserve existing bytes when equal, otherwise replace one file atomically."""
    path = Path(path)
    if path.exists() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def safe_path(root, relative):
    """Reject state paths escaping the dedicated mirror directory."""
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError(f"Unsafe mirror path: {relative!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"Path escapes mirror: {relative!r}")
    return path


def validate_roots(root, articles_root):
    """Validate roots for both online sync and offline rebuild entry points."""
    root, articles_root = Path(root).resolve(), Path(articles_root).resolve()
    if articles_root == root or articles_root.is_relative_to(root) or root.is_relative_to(articles_root):
        raise ValueError("Source and Markdown directories must be separate, non-overlapping roots")
    forbidden = [ROOT / "automation"]
    for path in (root, articles_root):
        for item in forbidden:
            if path.is_relative_to(item.resolve()) or item.resolve().is_relative_to(path):
                raise ValueError("Output roots must not touch the tool directory")
    return root, articles_root


def _tracked_asset_path(root, relative):
    """Only move flat asset files recorded in state; never traverse arbitrary paths."""
    path = safe_path(root, relative)
    if Path(relative).parent.as_posix() != "assets" or Path(relative).name in ("", ".", ".."):
        raise ValueError(f"Tracked media path is not a flat assets file: {relative!r}")
    if (root / relative).is_symlink() or (root / "assets").is_symlink():
        raise ValueError(f"Tracked media must not be a symlink: {relative!r}")
    if (root / "assets").exists() and not (root / "assets").is_dir():
        raise ValueError("Asset parent must be a directory")
    if path.exists() and not path.is_file():
        raise ValueError(f"Tracked media must be a regular file: {relative!r}")
    return path


def migrate_assets(root, articles_root, state):
    """Move tracked media into the portable article tree, then commit its location marker."""
    root, articles_root = validate_roots(root, articles_root)
    location = state.get("assets_location", "raw")
    if location not in {"raw", "articles"}:
        raise ValueError(f"Unsupported assets location: {location!r}")
    old_articles_root = Path(state.get("articles_root") or articles_root).resolve()
    source_root = root if location == "raw" else old_articles_root
    if location == "articles" and source_root == articles_root:
        return 0
    if location == "articles":
        validate_roots(root, source_root)
        if source_root != articles_root and (source_root.is_relative_to(articles_root) or articles_root.is_relative_to(source_root)):
            raise ValueError("Previous and current Markdown directories must not overlap")
    with ExitStack() as locks:
        if location == "articles" and source_root != articles_root:
            locks.enter_context(exclusive_run(source_root))
        moves = []
        # Validate every known path and every collision before moving any file.
        for info in state.get("assets", {}).values():
            relative = info.get("path")
            if not relative:
                continue
            _tracked_asset_path(root, relative)
            source = _tracked_asset_path(source_root, relative)
            destination = _tracked_asset_path(articles_root, relative)
            if source == destination:
                continue
            if source.is_file() and destination.is_file():
                if not files_equal(source, destination):
                    raise ValueError(f"Conflicting source and article media files: {relative}")
                moves.append((source, destination, "duplicate"))
            elif source.is_file():
                moves.append((source, destination, "move"))
            elif not destination.is_file():
                # Missing media remains an acquisition retry target during online sync.
                continue
        moved = 0
        for source, destination, action in moves:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if action == "duplicate":
                source.unlink()
            else:
                move_asset(source, destination)
                moved += 1
        old_assets = source_root / "assets"
        if old_assets.is_dir() and not any(old_assets.iterdir()):
            old_assets.rmdir()
        state["assets_location"] = "articles"
        state["articles_root"] = str(articles_root)
        atomic_write(root / "state.json", json_bytes(state))
        return moved


@contextmanager
def exclusive_run(root):
    """OS-held lock is released even if a process dies; the lock file may remain."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".sync.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def load_state(root):
    path = root / "state.json"
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "records": {}, "assets": {},
                "last_completed_sync_at": None}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported mirror state schema")
    if not isinstance(state.get("records"), dict) or not isinstance(state.get("assets"), dict):
        raise ValueError("Invalid mirror state")
    for item in state["records"].values():
        safe_path(root, item["directory"])
    for item in state["assets"].values():
        if item.get("path"):
            safe_path(root, item["path"])
    return state


def header(headers, name):
    return next((v for k, v in headers.items() if k.lower() == name.lower()), None)


def conditional_headers(info):
    result = {}
    if info.get("etag"):
        result["If-None-Match"] = info["etag"]
    if info.get("last_modified"):
        result["If-Modified-Since"] = info["last_modified"]
    return result


def verified_file(root, info):
    if not info.get("path") or not info.get("sha256"):
        return False
    path = safe_path(root, info["path"])
    if not path.is_file():
        return False
    hasher = hashlib.sha256()
    with path.open("rb") as inp:
        for chunk in iter(lambda: inp.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest() == info["sha256"]


def _certificate_failure(error):
    """Find certificate verification failures inside urllib's transport wrappers."""
    while True:
        if isinstance(error, (ssl.SSLCertVerificationError, ssl.CertificateError)):
            return True
        if isinstance(error, URLError):
            error = error.reason
            continue
        original = getattr(error, "original", None)
        if original is not None:
            error = original
            continue
        return False


def fetch_file(client, url, root, relative, previous, now, *, max_bytes=None, expected_media=False):
    """Keep old bytes, retry transient failures later, and remember permanent misses."""
    old_valid = verified_file(root, previous)
    try:
        result = client.download(url, safe_path(root, relative),
                                 conditional_headers(previous) if old_valid else None,
                                 max_bytes=max_bytes, expected_media=expected_media)
        if result.status == 304 and not old_valid:
            raise ValueError("Unexpected 304 without a verified cached file")
        result_info = dict(previous) if result.status == 304 else {}
        result_info.update(path=relative, status="downloaded", checked_at=now,
                           requested_url=url, resolved_url=result.url,
                           sha256=previous["sha256"] if result.status == 304 else result.sha256,
                           size=previous.get("size") if result.status == 304 else result.size)
        for source, target in [("ETag", "etag"), ("Last-Modified", "last_modified"),
                               ("Content-Type", "content_type")]:
            value = header(result.headers, source)
            if value is not None:
                result_info[target] = value
        result_info.pop("error", None)
        result_info.pop("http_status", None)
        return result_info
    except HTTPError as error:
        transient = error.code in TRANSIENT_STATUSES
        result_info = dict(previous) if old_valid else {"path": relative}
        result_info.update(status="failed" if transient else "unavailable",
                           checked_at=now, requested_url=url,
                           error=f"{type(error).__name__}: {error}",
                           http_status=error.code, cached_copy=old_valid)
        error.close()
        return result_info
    except (TransientDownloadError, URLError, TimeoutError, ConnectionError,
            http.client.IncompleteRead, ValueError) as error:
        transient = (isinstance(error, (TransientDownloadError, URLError, TimeoutError,
                                        ConnectionError, http.client.IncompleteRead))
                     and not _certificate_failure(error))
        result_info = dict(previous) if old_valid else {"path": relative}
        result_info.update(status="failed" if transient else "unavailable",
                           checked_at=now, requested_url=url,
                           error=f"{type(error).__name__}: {error}", cached_copy=old_valid)
        result_info.pop("http_status", None)
        return result_info


def record_key(record):
    return f"{record['type']}:{record['id']}"


def record_directory(record):
    return f"records/{record['type']}s/{record['id']}"


def tagging_content(record):
    """Fields whose changes can affect a post's tag assignment."""
    content = record.get("content") or {}
    return (plain_title(record.get("title", "")), content.get("rendered", ""),
            bool(content.get("protected")), record.get("featured_media") or 0)


def load_saved_record(root, key, previous):
    """Return the prior API record only when its saved bytes match the state hash."""
    if not previous.get("source_sha256") or not previous.get("directory"):
        return None
    path = safe_path(root, previous["directory"] + "/source.json")
    try:
        raw = path.read_bytes()
        record = json.loads(raw.decode("utf-8"))
        if digest(raw) == previous["source_sha256"] and record_key(record) == key:
            return record
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def load_saved_featured_media(root, previous):
    """Return verified prior featured-media metadata, if present."""
    if not previous.get("featured_media_sha256") or not previous.get("directory"):
        return None
    relative = previous["directory"] + "/source-featured-media.json"
    try:
        raw = safe_path(root, relative).read_bytes()
        media = json.loads(raw.decode("utf-8"))
        if digest(raw) == previous["featured_media_sha256"]:
            return media
    except (OSError, UnicodeError, ValueError):
        pass
    return None


def featured_tagging_content(media):
    """Only metadata rendered into the article can affect its tag assignment."""
    if media is None:
        return None
    return (media.get("id"), media.get("source_url"), media.get("media_type"),
            media.get("alt_text"), (media.get("caption") or {}).get("rendered", ""))


def prune_excluded(root, articles_root, state):
    """Remove explicitly excluded records and their unshared local files."""
    excluded = {key: item for key, item in state["records"].items() if key in EXCLUDED_RECORDS}
    if not excluded:
        return []
    kept = [item for key, item in state["records"].items() if key not in excluded]
    shared_assets = {url for item in kept for url in item.get("asset_urls", [])}
    removed_assets = {url for item in excluded.values() for url in item.get("asset_urls", [])} - shared_assets
    shared_paths = {asset["path"] for url, asset in state["assets"].items()
                    if url not in removed_assets and asset.get("path")}
    files, directories = set(), []
    for key, item in excluded.items():
        if record_key(item) != key or item["directory"] != record_directory(item):
            raise ValueError(f"Excluded record identity or directory mismatch: {key}")
        directory = safe_path(root, item["directory"])
        if directory != root / item["directory"]:
            raise ValueError(f"Excluded source directory must not use symlinks: {key}")
        directories.append(directory)
        if item.get("path"):
            path = safe_path(articles_root, item["path"])
            if path != articles_root / item["path"] or any(other.get("path") == item["path"] for other in kept):
                raise ValueError(f"Excluded Markdown path is shared or uses symlinks: {key}")
            files.add(path)
    for url in removed_assets:
        asset = state["assets"].get(url, {})
        if asset.get("path") and asset["path"] not in shared_paths:
            files.add(_tracked_asset_path(articles_root, asset["path"]))
    # All deletion targets have been resolved and checked before any removal.
    for path in files:
        path.unlink(missing_ok=True)
    for directory in directories:
        if directory.exists():
            shutil.rmtree(directory)
    for key, item in excluded.items():
        state["records"].pop(key)
        state.get("obsolete_markdown", {}).pop(item.get("path"), None)
    for url in removed_assets:
        state["assets"].pop(url, None)
    atomic_write(root / "state.json", json_bytes(state))
    return sorted(excluded)


def load_cached_view(root, key, item):
    """Read a retained source and view only after checking their preserved evidence."""
    source_path = safe_path(root, item["directory"] + "/source.json")
    if not source_path.is_file():
        raise ValueError(f"Cached source is missing: {key}")
    source_bytes = source_path.read_bytes()
    if digest(source_bytes) != item.get("source_sha256"):
        raise ValueError(f"Cached source hash mismatch: {key}")
    record = json.loads(source_bytes.decode("utf-8"))
    if record_key(record) != key:
        raise ValueError(f"Cached source identity mismatch: {key}")
    html_path = safe_path(root, item["directory"] + "/source.html")
    expected_html = record.get("content", {}).get("rendered", "").encode("utf-8")
    if not html_path.is_file() or html_path.read_bytes() != expected_html:
        raise ValueError(f"Cached source HTML mismatch: {key}")
    view_path = safe_path(root, item["directory"] + "/view-source.html")
    if not view_path.is_file():
        raise ValueError(f"Cached view source is missing: {key}")
    body = view_path.read_bytes().decode("utf-8")
    if item.get("derived_source") == "api_content":
        if body != record.get("content", {}).get("rendered", ""):
            raise ValueError(f"Cached view source mismatch: {key}")
    elif item.get("derived_source") == "public_page_content":
        page_path = safe_path(root, item["page"]["path"])
        if not verified_file(root, item["page"]):
            raise ValueError(f"Cached public page hash mismatch: {key}")
        expected_view = extract_public_content(page_path.read_bytes(), record)
        if expected_view is None or body != expected_view:
            raise ValueError(f"Cached derived view source mismatch: {key}")
    elif item.get("derived_source") == "source_page_only" and body:
        raise ValueError(f"Unexpected cached view source for unavailable content: {key}")
    if item.get("page", {}).get("status") == "downloaded" and not verified_file(root, item["page"]):
        raise ValueError(f"Cached public page hash mismatch: {key}")
    return record, body


def hydrate_inventory(root, articles_root, census, state, client, *, sample_ids, record_loader, progress):
    """Fetch only new/modified records or records whose required source files are missing."""
    requested = {"posts": [], "pages": []}
    repair_keys = set()
    for number, summary in enumerate(census, 1):
        key = record_key(summary)
        previous = state["records"].get(key, {})
        selected = sample_ids is None or summary["id"] in sample_ids
        directory = previous.get("directory") or record_directory(summary)
        raw_files_present = all(safe_path(root, f"{directory}/{name}").is_file()
                                for name in ("source.json", "source.html", "view-source.html"))
        page = previous.get("page", {})
        page_url_matches = page.get("requested_url") == previous.get("url")
        page_downloaded = (page.get("status") == "downloaded" and
                           (page.get("requested_url") is None or page_url_matches))
        page_unavailable = page.get("status") == "unavailable" and page_url_matches
        page_present = bool(page.get("path") and safe_path(root, page["path"]).is_file())
        page_settled = page_downloaded or page_unavailable
        featured_path = f"{directory}/source-featured-media.json"
        featured_present = (not previous.get("featured_media_sha256") or
                            safe_path(root, featured_path).is_file())
        modified = (summary.get("modified") != previous.get("modified_at") or
                    (previous.get("modified_at_gmt") is not None and
                     summary.get("modified_gmt") != previous.get("modified_at_gmt")))
        # Older state did not record protection; recheck its incomplete sources once.
        legacy_incomplete = ("protected_content" not in previous and
                             previous.get("acquisition_status") == "incomplete")
        needs_refresh = (not previous or previous.get("status") != "active" or modified or
                         not raw_files_present or not page_settled or
                         (page_downloaded and not page_present) or not featured_present or
                         not previous.get("path") or
                         previous.get("featured_media_error") or
                         (previous.get("derived_view_available") is False and not page_unavailable) or
                         legacy_incomplete or
                         previous.get("protected_content", False))
        if needs_refresh:
            if selected:
                requested[summary["type"] + "s"].append(summary["id"])
        elif selected and not safe_path(articles_root, previous["path"]).is_file():
            repair_keys.add(key)
        if number % 250 == 0 or number == len(census):
            progress(f"Local sources checked: {number}/{len(census)}")
    summaries = {record_key(r): r for r in census}
    detail_inventories = {}
    fetched_keys = set()
    fetched_records = {}
    for kind, ids in requested.items():
        inventory = record_loader(client, kind, ids=ids, progress=progress) if ids else Inventory([], [])
        if {r["id"] for r in inventory.records} != set(ids) or len(inventory.records) != len(ids):
            raise ValueError(f"Incomplete targeted {kind} response")
        for record in inventory.records:
            key = record_key(record)
            summary = summaries.get(key)
            if summary is None or record.get("status") != "publish" or any(
                    record.get(field) != summary.get(field)
                    for field in ("modified", "modified_gmt", "date_gmt")):
                raise ValueError(f"Record changed during census: {key}; rerun sync")
            fetched_records[key] = record
            fetched_keys.add(key)
        detail_inventories["details-" + kind] = inventory
    return fetched_records, detail_inventories, fetched_keys, repair_keys


def discover_record(record, featured=None):
    body, base = record["content"]["rendered"], record["link"]
    assets = discover_assets(body, base)
    images = set()
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup.find_all(["img", "picture"]):
        images.update(discover_assets(str(tag), base))
    for tag in soup.find_all("video", poster=True):
        images.update(discover_assets(f'<img src="{tag["poster"]}">', base))
    images.update(u for u in assets if re.search(r"\.(?:jpe?g|png|gif|svg|webp|avif|bmp|tiff?)$",
                                                urlsplit(u).path, re.I))
    if featured and featured.get("source_url"):
        urls = discover_assets(f'<img src="{html.escape(featured["source_url"], quote=True)}">', base)
        assets.update(urls)
        if featured.get("media_type") == "image":
            images.update(urls)
    return assets, images, discover_embeds(body, base)


def host_statistics(records, discovered):
    hosts = {}
    for record in records:
        key = record_key(record)
        assets, images, embeds = discovered[key]
        for category, urls in [("assets", assets), ("images", images), ("embeds", embeds)]:
            for url in urls:
                host = urlsplit(url).hostname.lower()
                item = hosts.setdefault(host, {"download_allowed": host in ALLOWED_HOSTS,
                    "assets": set(), "asset_documents": set(), "images": set(),
                    "image_documents": set(), "embeds": set(), "embed_documents": set()})
                item[category].add(url)
                item[{"assets": "asset_documents", "images": "image_documents",
                      "embeds": "embed_documents"}[category]].add(key)
    return {host: {key: len(value) if isinstance(value, set) else value
                   for key, value in item.items()} for host, item in sorted(hosts.items())}


def asset_filename(url):
    suffix = Path(urlsplit(url).path).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".bin"
    return f"assets/{digest(url.encode('utf-8'))}{suffix}"


def relative_url(directory, target):
    return quote(os.path.relpath(target, directory).replace("\\", "/"), safe="/")


class RelativePaths:
    """Resolve only referenced links, without rebuilding the whole index per article."""

    def __init__(self, directory, targets):
        self.directory, self.targets = directory, targets

    def get(self, key, default=None):
        target = self.targets.get(key)
        return relative_url(self.directory, target) if target is not None else default


def internal_aliases(url):
    parts = urlsplit(url)
    if parts.hostname not in ALLOWED_HOSTS:
        return [url]
    paths = {parts.path, parts.path.rstrip("/"), parts.path.rstrip("/") + "/"}
    return [urlunsplit((scheme, host, path, "", ""))
            for scheme in ("http", "https") for host in ALLOWED_HOSTS for path in paths]


def confirm_missing(client, state, remote_records):
    """Pagination is not a snapshot: require an individual public 404 for absence."""
    found = {record_key(r) for r in remote_records}
    for key, item in state["records"].items():
        if key in found:
            continue
        endpoint = f"https://kiszamolo.hu/wp-json/wp/v2/{item['type']}s/{item['id']}"
        try:
            response = client.request_bytes(endpoint)
        except HTTPError as error:
            if error.code == 404:
                error.close()
                continue
            raise
        # A live record or unusual response makes the census inconsistent.
        raise ValueError(f"Remote absence could not be confirmed for {key} (HTTP {response.status}); rerun inventory")


def publish_article_index(articles_root, state):
    """Publish the machine-readable article registry derived from mirror state."""
    records = sorted(state["records"].values(), key=lambda r: (r["published_at"], r["key"]))
    entries = []
    for record in records:
        entry = dict(record)
        entry["source_record"] = entry.pop("directory")
        page = dict(entry.get("page", {}))
        page.pop("path", None)
        if "page" in entry:
            entry["page"] = page
        entries.append(entry)
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in entries]
    atomic_write(articles_root / "index.jsonl", "".join(lines).encode("utf-8"))


def publish_indexes(root, state, stats, report, articles_root=None, *, update_media_sources=True):
    articles_root = articles_root or root
    records = sorted(state["records"].values(), key=lambda r: (r["published_at"], r["key"]))
    publish_article_index(articles_root, state)
    md = ["# Kiszámoló helyi tartalomarchívum", "", "Az eltűnt távoli tartalmak is szerepelnek; státuszuk `remote_missing`.", ""]
    if update_media_sources or (articles_root / "media-sources.json").is_file():
        md.extend(["[Médiaforrások statisztikája](MEDIA-SOURCES.md)", ""])
    year = None
    for item in records:
        current = item["published_at"][:4]
        if current != year:
            md.extend([f"## {current}", ""])
            year = current
        title = item["title"].replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
        md.append(f"- {item['published_at'][:10]} [{title}]({quote(item['path'], safe='/')})"
                  f" · {item['type']} · {item['status']} · {item['acquisition_status']}")
    md.extend(["", "## Kategóriák", ""])
    categories = {}
    for item in records:
        for category in item.get("category_names", []):
            categories.setdefault(category, []).append(item)
    for category, items in sorted(categories.items()):
        md.extend([f"### {category}", ""])
        for item in items:
            title = item["title"].replace("[", "\\[").replace("]", "\\]").replace("\n", " ")
            md.append(f"- [{title}]({quote(item['path'], safe='/')}) · {item['status']}")
        md.append("")
    atomic_write(articles_root / "INDEX.md", ("\n".join(md) + "\n").encode("utf-8"))
    if update_media_sources:
        smd = ["# Médiaforrások", "", "Az aktuális teljes ID-leltárhoz tartozó, legutóbb letöltött cikk- és oldaltörzsekben, valamint elérhető kiemelt képekben felismert URL-ek. A srcset-változatok külön URL-nek számítanak; ez nem különálló ábrák száma.",
               "", "| Host | Egyedi média-/fájl-URL | Érintett tartalom | Kép-URL | Képes tartalom | Embed-URL | Letölthető |",
               "|---|---:|---:|---:|---:|---:|---:|"]
        for host, item in sorted(stats.items(), key=lambda x: (-x[1]["images"], x[0])):
            smd.append(f"| {host} | {item['assets']} | {item['asset_documents']} | {item['images']} | {item['image_documents']} | {item['embeds']} | {'igen' if item['download_allowed'] else 'nem'} |")
        atomic_write(articles_root / "MEDIA-SOURCES.md", ("\n".join(smd) + "\n").encode("utf-8"))
        atomic_write(articles_root / "media-sources.json", json_bytes(stats))
    atomic_write(articles_root / "last-report.json", json_bytes(report))
    # State is the commit marker and source of truth if publishing is interrupted.
    atomic_write(root / "state.json", json_bytes(state))


def update_acquisition_status(item, assets, *, protected_content, derived_view_available):
    """Refresh acquisition bookkeeping without changing the caller's source flags."""
    urls = item.get("asset_urls", [])
    failures = [url for url in urls if assets.get(url, {}).get("status") == "failed"]
    item["failed_assets"] = failures
    item["external_assets"] = [url for url in urls
                               if assets.get(url, {}).get("status") == "external_reference"]
    item["acquisition_status"] = "incomplete" if (
        failures or item.get("page", {}).get("status") not in {"downloaded", "unavailable"} or
        protected_content or item.get("featured_media_error") or not derived_view_available) else "complete"


def render_views(root, articles_root, state, render_records, view_bodies, featured, *,
                 prior_records=None, previous_articles_root=None, update_status=True):
    """Render shared Markdown views and preserve locally edited reassigned paths."""
    existing_documents = state["records"]
    targets = {alias: safe_path(articles_root, other["path"])
               for other in existing_documents.values() for alias in internal_aliases(other["url"])}
    embed_targets = {alias.rstrip("/") + "/embed/": safe_path(articles_root, other["path"])
                     for other in existing_documents.values() for alias in internal_aliases(other["url"])}
    retained_anchors = set().union(*(referenced_anchors(body) for body in view_bodies.values()))
    reassigned_preserved = []
    if prior_records is not None and previous_articles_root == str(articles_root):
        previous_owners = {item["path"].casefold(): (key, item)
                           for key, item in prior_records.items() if item.get("path")}
        for key, item in state["records"].items():
            previous_owner = previous_owners.get(item["path"].casefold())
            if previous_owner is None or previous_owner[0] == key:
                continue
            old_key, old_item = previous_owner
            old_path = safe_path(articles_root, old_item["path"])
            if not old_path.is_file() or not old_item.get("markdown_sha256"):
                continue
            old_bytes = old_path.read_bytes()
            old_hash = digest(old_bytes)
            if old_hash == old_item["markdown_sha256"]:
                continue
            backup = f"preserved-markdown/{old_hash}.md"
            atomic_write(safe_path(root, backup), old_bytes)
            reassigned_preserved.append(dict(path=old_item["path"], previous_key=old_key,
                                            new_key=key, preserved_source_path=backup))
    for record in render_records:
        key = record_key(record)
        item = state["records"][key]
        output_path = safe_path(articles_root, item["path"])
        directory = output_path.parent
        asset_paths = {}
        for url in item.get("asset_urls", []):
            asset = state["assets"].get(url, {})
            if (asset.get("path") and asset.get("sha256") and
                    (asset.get("status") == "downloaded" or
                     (asset.get("status") == "failed" and asset.get("cached_copy")))):
                asset_paths[url] = relative_url(directory, safe_path(articles_root, asset["path"]))
        body = view_bodies[key]
        featured_record = featured.get(record.get("featured_media"))
        if featured_record is None and record.get("featured_media"):
            featured_path = item["directory"] + "/source-featured-media.json"
            cached_featured = safe_path(root, featured_path)
            if verified_file(root, dict(path=featured_path, sha256=item.get("featured_media_sha256"))):
                candidate = json.loads(cached_featured.read_text(encoding="utf-8"))
                if candidate.get("id") == record["featured_media"]:
                    featured_record = candidate
        if (featured_record and featured_record.get("media_type") == "image" and
                featured_record.get("source_url") and featured_record["source_url"] not in body):
            image_url = html.escape(featured_record["source_url"], quote=True)
            alt = html.escape(featured_record.get("alt_text", ""), quote=True)
            caption = featured_record.get("caption", {}).get("rendered", "")
            body = f'<figure><img src="{image_url}" alt="{alt}"><figcaption>{caption}</figcaption></figure>\n' + body
        display_record = dict(record, category_names=item["category_names"], tag_names=item["tag_names"])
        rewritten = rewrite_html(body, record["link"], asset_paths, RelativePaths(directory, targets),
                                 RelativePaths(directory, embed_targets))
        soup = BeautifulSoup(rewritten, "html.parser")
        for frame in soup.find_all("iframe", src=True):
            if str(frame["src"]).split("#", 1)[0].endswith(".md"):
                link = soup.new_tag("a", href=frame["src"])
                link.string = "Beágyazott cikk"
                frame.replace_with(link)
        source_anchors = {str(tag["id"]) for tag in soup.find_all(True) if tag.has_attr("id")}
        previous_anchors = set()
        if output_path.is_file():
            previous_markdown = output_path.read_text(encoding="utf-8")
            previous_anchors = set(re.findall(r'<a id="([^"]+)"></a>', previous_markdown))
        markdown_text = make_markdown(display_record, str(soup),
            retained_anchors=retained_anchors | source_anchors | previous_anchors)
        if previous_anchors:
            missing_anchors = sorted(previous_anchors -
                                     set(re.findall(r'<a id="([^"]+)"></a>', markdown_text)))
            if missing_anchors:
                markdown_text += "\n" + "\n".join(
                    f'<a id="{html.escape(anchor, quote=True)}"></a>' for anchor in missing_anchors) + "\n"
        markdown = markdown_text.encode("utf-8")
        atomic_write(output_path, markdown)
        item["markdown_sha256"] = digest(markdown)
        if update_status:
            update_acquisition_status(item, state.get("assets", {}),
                protected_content=record["content"].get("protected"),
                derived_view_available=item.get("derived_view_available"))
            if record["content"].get("protected"):
                item.setdefault("warnings", []).append("Protected content is not available through the public API.")
    return reassigned_preserved


def run_sync(root, *, articles_root=None, client=None, sample_ids=None, workers=4,
             max_asset_bytes=None, progress=print, inventory_loader=fetch_census,
             metadata_loader=fetch_metadata, record_loader=fetch_records):
    started = utc_now()
    root = Path(root).resolve()
    articles_root = Path(articles_root).resolve() if articles_root else root.parent / "articles"
    root, articles_root = validate_roots(root, articles_root)
    stamp = started.isoformat()
    client = client or HttpClient(allowed_hosts=ALLOWED_HOSTS)
    with exclusive_run(root), exclusive_run(articles_root):
        state = load_state(root)
        previous_articles_root = state.get("articles_root")
        migrate_assets(root, articles_root, state)
        before_exclusions = {key: dict(item) for key, item in state["records"].items()}
        excluded_keys = sorted(key for key in state["records"] if key in EXCLUDED_RECORDS)
        deleted_targets = {
            before_exclusions[key]["path"]: before_exclusions[key]["url"]
            for key in excluded_keys
            if before_exclusions.get(key, {}).get("path") and before_exclusions.get(key, {}).get("url")
        }
        exclusion_links_repaired = 0
        if deleted_targets:
            for key, item in state["records"].items():
                if key in excluded_keys or not item.get("path"):
                    continue
                path = safe_path(articles_root, item["path"])
                if not path.is_file():
                    continue
                original = path.read_bytes()
                text = original.decode("utf-8")
                repaired = repair_markdown_links(text, item["path"], {}, deleted_targets=deleted_targets)
                if repaired == text:
                    continue
                atomic_write(path, repaired.encode("utf-8"))
                exclusion_links_repaired += 1
                if item.get("markdown_sha256") == digest(original):
                    item["markdown_sha256"] = digest(repaired.encode("utf-8"))
        if exclusion_links_repaired:
            atomic_write(root / "state.json", json_bytes(state))
        prune_excluded(root, articles_root, state)
        deleted_targets = {}
        prior_records = {key: dict(item) for key, item in state["records"].items()}
        for item in state["records"].values():
            if item.get("type") == "post":
                item.setdefault("tagging_pending", False)
        inventories = [inventory_loader(client, kind, progress=progress) for kind in ("posts", "pages")]
        census = [record for inv in inventories for record in inv.records]
        if len({record["id"] for record in census}) != len(census):
            raise ValueError("Duplicate WordPress IDs across posts/pages")
        excluded = {record_key(r): EXCLUDED_RECORDS[record_key(r)]
                    for r in census if record_key(r) in EXCLUDED_RECORDS}
        census = [r for r in census if record_key(r) not in EXCLUDED_RECORDS]
        if sample_ids is None:
            confirm_missing(client, state, census)
        if sample_ids is not None and not set(sample_ids).issubset({r["id"] for r in census}):
            raise ValueError("Sample includes IDs absent from the complete public inventory")
        summaries = {record_key(record): record for record in census}
        scope_summaries = [record for record in census
                           if sample_ids is None or record["id"] in sample_ids]
        scope_keys = {record_key(record) for record in scope_summaries}
        legacy_stats_path = articles_root / "media-sources.json"
        stats = (json.loads(legacy_stats_path.read_text(encoding="utf-8"))
                 if legacy_stats_path.is_file() else {})
        fetched_records, detail_inventories, fetched_keys, repair_keys = hydrate_inventory(
            root, articles_root, census, state, client, sample_ids=sample_ids,
            record_loader=record_loader, progress=progress)
        fetched_keys &= scope_keys
        progress(f"Full records fetched: {len(fetched_keys)}; unchanged sources reused: {len(scope_keys) - len(fetched_keys)}")
        need_taxonomies = bool(fetched_keys)
        taxonomies = ({kind: metadata_loader(client, kind, progress=progress,
                                            fields=("id", "name"))
                       for kind in ("categories", "tags")} if need_taxonomies else {})
        names = {kind: {term["id"]: plain_title(term["name"]) for term in inv.records}
                 for kind, inv in taxonomies.items()}
        process_keys = ((fetched_keys | repair_keys) & scope_keys)
        if sample_ids is not None:
            process_keys.update(scope_keys)
        process_records = {key: fetched_records[key] for key in process_keys if key in fetched_records}
        view_bodies = {}
        for key in sorted(process_keys - process_records.keys()):
            cached_record, body = load_cached_view(root, key, state["records"][key])
            process_records[key] = cached_record
            view_bodies[key] = body
        featured_ids = {record["featured_media"] for key, record in process_records.items()
                        if key in fetched_keys and record.get("featured_media")}
        media_error = None
        failed_media_ids = set()
        try:
            media_inventory = (metadata_loader(client, "media", ids=sorted(featured_ids), progress=progress)
                               if featured_ids else Inventory([], []))
        except Exception as error:
            media_inventory = getattr(error, "partial_inventory", None)
            failed_media_ids = set(getattr(error, "missing_ids", featured_ids))
            media_error = f"{type(error).__name__}: {error}"
            progress(f"Featured media metadata failed: {media_error}")
        featured = {m["id"]: m for m in media_inventory.records} if media_inventory else {}
        # Preserve cached featured records for targeted renders during an API outage.
        for key, record in process_records.items():
            media_id = record.get("featured_media")
            previous = state["records"].get(key, {})
            if media_id and media_id not in featured and previous.get("directory"):
                cached_path = safe_path(root, previous["directory"] + "/source-featured-media.json")
                if cached_path.is_file() and previous.get("featured_media_sha256"):
                    try:
                        raw = cached_path.read_bytes()
                        cached = json.loads(raw.decode("utf-8"))
                        if cached.get("id") == media_id and digest(raw) == previous.get("featured_media_sha256"):
                            featured[media_id] = cached
                    except (ValueError, OSError):
                        pass
        api_sources = []
        source_inventories = dict(zip(("inventory-posts", "inventory-pages"), inventories)) | detail_inventories | taxonomies
        if media_inventory:
            source_inventories["media"] = media_inventory
        for kind, inv in source_inventories.items():
            for n, (url, response) in enumerate(inv.responses, 1):
                relative = f"sources/api/{kind}/{n:04}.json"
                atomic_write(root / relative, response.body)
                api_sources.append(dict(path=relative, url=url, headers=response.headers,
                                        fetched_at=stamp, sha256=digest(response.body), size_bytes=len(response.body)))
        atomic_write(root / "sources/api/manifest.json", json_bytes(api_sources))
        for record in process_records.values():
            if urlsplit(record["link"]).hostname not in ALLOWED_HOSTS:
                raise ValueError(f"Unexpected article host: {record['link']}")
        process_records_list = list(process_records.values())
        retained = [item for key, item in state["records"].items() if key not in process_keys]
        planned_paths = plan_article_paths(process_records_list, retained, articles_root=articles_root)
        path_changed_keys = {key for key, path in planned_paths.items()
                             if key in state["records"] and
                             state["records"][key].get("path") != path and key not in process_keys}
        for key in sorted(path_changed_keys):
            cached_record, body = load_cached_view(root, key, state["records"][key])
            process_records[key] = cached_record
            view_bodies[key] = body
        if path_changed_keys:
            process_keys.update(path_changed_keys)
            process_records_list = list(process_records.values())
            retained = [item for key, item in state["records"].items() if key not in process_keys]
            planned_paths = plan_article_paths(process_records_list, retained, articles_root=articles_root)
        progress(f"Inventory complete: {len(census)} records; processing: {len(process_keys)}")
        for summary in scope_summaries:
            key = record_key(summary)
            item = state["records"].get(key)
            if item is None:
                continue
            item.update(modified_at=summary.get("modified"),
                        modified_at_gmt=summary.get("modified_gmt"),
                        last_seen_at=stamp, status="active")
            item.pop("remote_missing_since", None)
        page_refresh_keys = {
            record_key(record) for record in process_records_list
            if record_key(record) in fetched_keys and not (
                state["records"].get(record_key(record), {}).get("page", {}).get("status") == "unavailable"
                and state["records"].get(record_key(record), {}).get("page", {}).get("requested_url") == record["link"])
        }
        discovered = {}
        for record in process_records_list:
            key = record_key(record)
            previous = state["records"].get(key, {})
            directory = record_directory(record)
            featured_record = featured.get(record.get("featured_media"))
            media_hash = digest(json_bytes(featured_record)) if featured_record else None
            newly_fetched = key in fetched_keys
            if newly_fetched:
                source_hash = digest(json_bytes(record))
                item = dict(previous, key=key, id=record["id"], type=record["type"],
                    title=plain_title(record["title"]), published_at=record["date"],
                    published_at_gmt=record.get("date_gmt"), modified_at=record.get("modified"),
                    modified_at_gmt=record.get("modified_gmt"), url=record["link"],
                    directory=directory, status="active", last_seen_at=stamp,
                    source_sha256=source_hash, categories=record.get("categories", []),
                    tags=record.get("tags", []),
                    category_names=[names["categories"].get(n, f"ID:{n}") for n in record.get("categories", [])],
                    tag_names=[names["tags"].get(n, f"ID:{n}") for n in record.get("tags", [])],
                    featured_media_sha256=media_hash,
                    featured_media_error=media_error if record.get("featured_media") in failed_media_ids else None,
                    protected_content=bool(record.get("content", {}).get("protected")),
                    warnings=conversion_warnings(record["content"]["rendered"]))
                if record["type"] == "post":
                    old_record = load_saved_record(root, key, previous)
                    source_changed = bool(previous) and (
                        old_record is None or tagging_content(old_record) != tagging_content(record))
                    old_featured = load_saved_featured_media(root, previous)
                    featured_changed = bool(record.get("featured_media") and featured_record) and (
                        old_featured is None or
                        featured_tagging_content(old_featured) != featured_tagging_content(featured_record))
                    item["tagging_pending"] = bool(
                        previous.get("tagging_pending") or not previous or source_changed or featured_changed)
                else:
                    item.pop("tagging_pending", None)
                item.pop("remote_missing_since", None)
                atomic_write(root / directory / "source.json", json_bytes(record))
                atomic_write(root / directory / "source.html", record["content"]["rendered"].encode("utf-8"))
                if featured_record:
                    atomic_write(root / directory / "source-featured-media.json", json_bytes(featured_record))
            else:
                item = dict(previous)
                if taxonomies:
                    item["category_names"] = [names["categories"].get(n, f"ID:{n}")
                                              for n in item.get("categories", [])]
                    item["tag_names"] = [names["tags"].get(n, f"ID:{n}")
                                         for n in item.get("tags", [])]
            item["path"] = planned_paths[key]
            state["records"][key] = item
            if newly_fetched:
                discovered[key] = discover_record(record, featured_record)
                item["asset_urls"] = sorted(discovered[key][0])
                item["embed_urls"] = sorted(discovered[key][2])
        progress(f"Public page refresh: {len(page_refresh_keys)}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for record in process_records_list:
                key = record_key(record)
                if key in page_refresh_keys:
                    item = state["records"][key]
                    futures[pool.submit(fetch_file, client, record["link"], root,
                        item["directory"] + "/source-page.html", item.get("page", {}),
                        stamp, max_bytes=32 * 1024 * 1024)] = key
            for n, future in enumerate(concurrent.futures.as_completed(futures), 1):
                key = futures[future]
                state["records"][key]["page"] = future.result()
                if n % 25 == 0 or n == len(futures):
                    progress(f"Public pages: {n}/{len(futures)}")
        for record in process_records_list:
            key = record_key(record)
            item = state["records"][key]
            body = view_bodies.get(key, record["content"]["rendered"])
            if key in fetched_keys:
                item["derived_source"] = "api_content"
                item["derived_view_available"] = True
            if key in fetched_keys and record["type"] == "page" and not body.strip():
                page_path = safe_path(root, item["directory"] + "/source-page.html")
                extracted = extract_public_content(page_path.read_bytes(), record) if page_path.is_file() else None
                if extracted is None:
                    item["derived_source"] = "source_page_only"
                    item["derived_view_available"] = False
                    item["warnings"].append("Empty API content; public page content container was not recognized. Read source-page.html.")
                else:
                    body = extracted
                    item["derived_source"] = "public_page_content"
                    item["warnings"].append("Empty API content; derived view extracted from the preserved public page content container.")
                    extra_assets, extra_images, extra_embeds = discover_record(dict(record, content={"rendered": body}))
                    old_assets, old_images, old_embeds = discovered[key]
                    discovered[key] = old_assets | extra_assets, old_images | extra_images, old_embeds | extra_embeds
                    item["asset_urls"] = sorted(discovered[key][0])
                    item["embed_urls"] = sorted(discovered[key][2])
            view_bodies[key] = body
            if key in fetched_keys:
                atomic_write(root / item["directory"] / "view-source.html", body.encode("utf-8"))
        all_assets = {url for key in scope_keys for url in state["records"].get(key, {}).get("asset_urls", [])}
        assets_refresh = {url for key in fetched_keys if key in discovered for url in discovered[key][0]}
        jobs = {}
        previous_asset_hashes = {url: state["assets"].get(url, {}).get("sha256") for url in all_assets}
        previous_asset_statuses = {url: state["assets"].get(url, {}).get("status") for url in all_assets}
        previous_asset_paths = {url: state["assets"].get(url, {}).get("path") for url in all_assets}
        previously_available = {
            url: state["assets"].get(url, {}).get("status") == "downloaded" and
                 bool(state["assets"].get(url, {}).get("path")) and
                 safe_path(articles_root, state["assets"][url]["path"]).is_file()
            for url in all_assets
        }
        # Avoid racing Windows path resolution with creation of the shared parent.
        safe_path(articles_root, "assets").mkdir(exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for url in sorted(all_assets):
                previous = state["assets"].get(url, {})
                if urlsplit(url).hostname not in ALLOWED_HOSTS:
                    state["assets"][url] = dict(status="external_reference", url=url)
                    continue
                asset_path = previous.get("path") or asset_filename(url)
                present = safe_path(articles_root, asset_path).is_file()
                if previous.get("status") == "unavailable":
                    continue
                if url in assets_refresh or not present or previous.get("status") != "downloaded":
                    jobs[pool.submit(fetch_file, client, url, articles_root, asset_filename(url),
                        previous, stamp, max_bytes=max_asset_bytes, expected_media=True)] = url
            progress(f"Media refresh: {len(jobs)}; external references: {sum(urlsplit(u).hostname not in ALLOWED_HOSTS for u in all_assets)}")
            for n, future in enumerate(concurrent.futures.as_completed(jobs), 1):
                url = jobs[future]
                state["assets"][url] = dict(future.result(), url=url)
                if n % 50 == 0 or n == len(jobs):
                    progress(f"Media: {n}/{len(jobs)}")
        changed_asset_urls = {
            url for url in all_assets
            if previous_asset_hashes.get(url) is not None
            and state["assets"].get(url, {}).get("status") == "downloaded"
            and state["assets"].get(url, {}).get("sha256") is not None
            and previous_asset_hashes[url] != state["assets"][url]["sha256"]
        }
        unavailable_targets = {
            previous_asset_paths[url]: url
            for url in all_assets
            if previous_asset_statuses.get(url) in {"downloaded", "failed"}
            and state["assets"].get(url, {}).get("status") == "unavailable"
            and previous_asset_paths.get(url)
        }
        remote_targets = {
            url: state["assets"][url]["path"]
            for url in all_assets
            if not previously_available[url] and
            state["assets"].get(url, {}).get("status") == "downloaded" and
            state["assets"].get(url, {}).get("path")
        }
        article_targets = {}
        for key in fetched_keys - prior_records.keys():
            item = state["records"][key]
            for alias in internal_aliases(item["url"]):
                article_targets[alias] = item["path"]
                article_targets[alias.rstrip("/") + "/embed/"] = item["path"]
        remote_targets.update(article_targets)
        if changed_asset_urls:
            for key, item in state["records"].items():
                if item.get("type") == "post" and changed_asset_urls.intersection(item.get("asset_urls", [])):
                    item["tagging_pending"] = True
        for key, item in state["records"].items():
            if key in planned_paths:
                item["path"] = planned_paths[key]
        for key in scope_keys:
            item = state["records"].get(key)
            if not item:
                continue
            update_acquisition_status(item, state.get("assets", {}),
                protected_content=item.get("protected_content"),
                derived_view_available=item.get("derived_view_available", True))
        render_keys = process_keys
        render_records = [process_records[key] for key in render_keys]
        reassigned_preserved = (render_views(root, articles_root, state, render_records, view_bodies, featured,
            prior_records=prior_records, previous_articles_root=previous_articles_root) if render_records else [])
        path_moves = {previous["path"]: state["records"][key]["path"]
                      for key, previous in prior_records.items()
                      if key in state["records"] and previous.get("path") and
                      previous["path"] != state["records"][key].get("path")}
        rendered_keys = set(render_keys)
        links_repaired = exclusion_links_repaired
        if path_moves or deleted_targets or remote_targets or unavailable_targets:
            for key, item in state["records"].items():
                if key in rendered_keys or not item.get("path"):
                    continue
                if (not path_moves and not deleted_targets and not article_targets and
                        not unavailable_targets and
                        remote_targets.keys().isdisjoint(item.get("asset_urls", []))):
                    continue
                path = safe_path(articles_root, item["path"])
                if not path.is_file():
                    continue
                original = path.read_bytes()
                text = original.decode("utf-8")
                repaired = repair_markdown_links(text, item["path"], path_moves,
                    remote_targets=remote_targets,
                    deleted_targets=deleted_targets | unavailable_targets)
                if repaired == text:
                    continue
                atomic_write(path, repaired.encode("utf-8"))
                links_repaired += 1
                old_hash = digest(original)
                if item.get("markdown_sha256") == old_hash:
                    item["markdown_sha256"] = digest(repaired.encode("utf-8"))
        remote_keys = set(summaries)
        if sample_ids is None:
            for key, item in state["records"].items():
                if key not in remote_keys:
                    item["status"] = "remote_missing"
                    item.setdefault("remote_missing_since", stamp)
            # A completed traversal advances the watermark even with explicitly recorded
            # acquisition errors. Such errors remain retry targets on the next run.
            state["last_completed_sync_at"] = utc_now().isoformat()
        state["last_inventory_at"] = stamp
        state["articles_root"] = str(articles_root)
        failures = {u: state["assets"][u]["error"] for u in all_assets
                    if state["assets"][u]["status"] == "failed"}
        report = dict(started_at=stamp, finished_at=utc_now().isoformat(),
            mode="sample" if sample_ids is not None else "full_inventory_sync",
            excluded_records=excluded,
            remote_posts=len(inventories[0].records), remote_pages=len(inventories[1].records),
            selected_records=len(scope_keys), processed_records=len(process_keys),
            rendered_records=len(render_keys), links_repaired=links_repaired,
            refreshed_pages=len(page_refresh_keys), refreshed_assets=len(jobs),
            tagging_pending_count=sum(item.get("type") == "post" and item.get("tagging_pending", False)
                                      for item in state["records"].values()),
            full_records_fetched=len(fetched_keys), cached_records_reused=len(scope_keys) - len(fetched_keys),
            content_check="modified_timestamps",
            api_response_count=len(api_sources), api_response_bytes=sum(r["size_bytes"] for r in api_sources),
            remote_missing=sum(r["status"] == "remote_missing" for r in state["records"].values()),
            incomplete_records=sum(state["records"][key]["acquisition_status"] != "complete"
                                   for key in scope_keys if key in state["records"]),
            asset_failures=failures, page_failures={record_key(r):state["records"][record_key(r)].get("page", {}).get("error")
                for r in scope_summaries if state["records"].get(record_key(r), {}).get("page", {}).get("status") == "failed"},
            media_hosts=stats)
        report["featured_media_error"] = media_error
        report["reassigned_locally_modified_files_preserved"] = reassigned_preserved
        # Commit new locations before pruning a previous generated filename.
        pending = dict(state.get("obsolete_markdown", {})) if previous_articles_root == str(articles_root) else {}
        current_paths = {item["path"] for item in state["records"].values()}
        if previous_articles_root == str(articles_root):
            for item in prior_records.values():
                if item.get("path") and item["path"] not in current_paths and item.get("markdown_sha256"):
                    pending[item["path"]] = item["markdown_sha256"]
        state["obsolete_markdown"] = pending
        publish_indexes(root, state, stats, report, articles_root, update_media_sources=False)
        preserved = []
        for relative, expected in list(pending.items()):
            if relative in current_paths:
                pending.pop(relative)
                continue
            old_path = safe_path(articles_root, relative)
            if old_path.is_file():
                if digest(old_path.read_bytes()) != expected:
                    preserved.append(relative)
                    pending.pop(relative)
                    continue
                old_path.unlink()
            pending.pop(relative)
        report["obsolete_locally_modified_files_preserved"] = preserved
        atomic_write(root / "state.json", json_bytes(state))
        atomic_write(articles_root / "last-report.json", json_bytes(report))
        progress(f"Done: {len(scope_keys)} records; rendered: {len(render_keys)}; incomplete: {report['incomplete_records']}; missing remotely: {report['remote_missing']}")
        return report


def offline_rebuild(root, articles_root, progress=print):
    """Regenerate Markdown and indexes only from verified local mirror files."""
    root, articles_root = validate_roots(root, articles_root)
    with exclusive_run(root), exclusive_run(articles_root):
        state = load_state(root)
        if not state["records"]:
            raise ValueError("Cannot rebuild without cached records")
        if state.get("articles_root") != str(articles_root):
            raise ValueError("Offline rebuild requires the articles root recorded in state")
        moved = migrate_assets(root, articles_root, state)
        excluded = prune_excluded(root, articles_root, state)
        records = []
        view_bodies = {}
        featured = {}
        discovered = {}
        for key, item in state["records"].items():
            record, body = load_cached_view(root, key, item)
            item["warnings"] = conversion_warnings(body, previous=item.get("warnings", []))
            view_bodies[key] = body
            media_id = record.get("featured_media")
            if media_id and item.get("featured_media_sha256"):
                featured_path = safe_path(root, item["directory"] + "/source-featured-media.json")
                if featured_path.is_file():
                    raw = featured_path.read_bytes()
                    candidate = json.loads(raw.decode("utf-8"))
                    if candidate.get("id") != media_id or digest(raw) != item.get("featured_media_sha256"):
                        raise ValueError(f"Cached featured-media metadata mismatch: {key}")
                    featured[media_id] = candidate
                else:
                    raise ValueError(f"Cached featured-media metadata is missing: {key}")
            records.append(record)
        for record in records:
            key = record_key(record)
            featured_record = featured.get(record.get("featured_media"))
            found = discover_record(record, featured_record)
            body = view_bodies[key]
            if body != record.get("content", {}).get("rendered", ""):
                extra = discover_record(dict(record, content={"rendered": body}), featured_record)
                found = found[0] | extra[0], found[1] | extra[1], found[2] | extra[2]
            discovered[key] = found
        for url, asset in state.get("assets", {}).items():
            if (asset.get("status") == "downloaded" or
                    (asset.get("status") == "failed" and asset.get("cached_copy"))):
                if not verified_file(articles_root, asset):
                    raise ValueError(f"Cached media hash mismatch or missing file: {url}")
        stats = host_statistics(records, discovered)
        preserved = render_views(root, articles_root, state, records, view_bodies, featured,
                                 update_status=False)
        old_report_path = articles_root / "last-report.json"
        old_report = json.loads(old_report_path.read_text(encoding="utf-8")) if old_report_path.is_file() else {}
        publish_indexes(root, state, stats, old_report, articles_root)
        report = dict(rebuilt_at=utc_now().isoformat(), moved_assets=moved,
                      excluded_records=excluded,
                      records=len(records), locally_modified_reassigned_files_preserved=preserved)
        atomic_write(articles_root / "rebuild-report.json", json_bytes(report))
        progress(f"Offline rebuild complete: {len(records)} records; moved assets: {moved}")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-output", type=Path, default=DEFAULT_SOURCE_OUTPUT)
    parser.add_argument("--sample", type=int, nargs="+", metavar="ID", help="Partial prototype; never marks remote_missing or advances sync watermark")
    parser.add_argument("--rebuild", action="store_true", help="Regenerate Markdown and indexes from verified local files only")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-asset-mib", type=float, help="Optional per-asset limit; oversized files are marked unavailable")
    args = parser.parse_args()
    output = args.output.resolve()
    source_output = args.source_output.resolve()
    forbidden = [ROOT / "automation"]
    if output in {ROOT.resolve(), (ROOT / "raw_source").resolve()} or source_output in {ROOT.resolve(), (ROOT / "raw_source").resolve(), DEFAULT_OUTPUT.resolve()}:
        parser.error("Use articles output and a dedicated raw_source subdirectory")
    if any(path.is_relative_to(p.resolve()) or p.resolve().is_relative_to(path)
           for path in [output, source_output] for p in forbidden):
        parser.error("Output roots must not touch the tool directory")
    if source_output.is_relative_to(output) or output.is_relative_to(source_output):
        parser.error("Source and Markdown output roots must not overlap")
    if args.workers < 1 or args.workers > 8:
        parser.error("workers must be between 1 and 8")
    if args.max_asset_mib is not None and args.max_asset_mib <= 0:
        parser.error("max-asset-mib must be positive")
    if args.rebuild and (args.sample or args.max_asset_mib is not None):
        parser.error("--rebuild cannot be combined with --sample or --max-asset-mib")
    if args.sample and (output == DEFAULT_OUTPUT.resolve() or source_output == DEFAULT_SOURCE_OUTPUT.resolve()):
        parser.error("Sample requires separate --output and --source-output directories")
    try:
        if args.rebuild:
            offline_rebuild(source_output, output)
            return 0
        report = run_sync(source_output, articles_root=output, sample_ids=set(args.sample) if args.sample else None,
            workers=args.workers,
            max_asset_bytes=int(args.max_asset_mib * 1024 * 1024) if args.max_asset_mib else None)
        return 2 if report["incomplete_records"] else 0
    except KeyboardInterrupt:
        print("Interrupted; existing files remain, rerun to resume.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Sync failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
