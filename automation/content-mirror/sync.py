"""One-way mirror of current public Kiszamolo posts and pages."""

from __future__ import annotations

import argparse
import concurrent.futures
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import errno
import shutil
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit, quote
from urllib.error import HTTPError

from bs4 import BeautifulSoup

from remote import HttpClient, Inventory, fetch_census, fetch_records, fetch_metadata
from paths import plan_article_paths
from render import (conversion_warnings, discover_assets, discover_embeds,
                    extract_public_content, make_markdown, plain_title, referenced_anchors, rewrite_html)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "articles"
DEFAULT_SOURCE_OUTPUT = ROOT / "raw_source" / "content-mirror"
ALLOWED_HOSTS = {"kiszamolo.hu", "www.kiszamolo.hu"}
SCHEMA_VERSION = 1
EXCLUDED_RECORDS = {
    "post:104048": "Password-protected insurance policy; excluded by user request.",
}


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


def fetch_file(client, url, root, relative, previous, now, *, max_bytes=None, expected_media=False):
    """Keep old bytes on acquisition failure, but label their currentness explicitly."""
    old_valid = verified_file(root, previous)
    try:
        result = client.download(url, safe_path(root, relative),
                                 conditional_headers(previous) if old_valid else None,
                                 max_bytes=max_bytes, expected_media=expected_media)
        if result.status == 304 and not old_valid:
            raise ValueError("Unexpected 304 without a verified cached file")
        result_info = dict(previous) if result.status == 304 else {}
        result_info.update(path=relative, status="downloaded", checked_at=now,
                           resolved_url=result.url,
                           sha256=previous["sha256"] if result.status == 304 else result.sha256,
                           size=previous.get("size") if result.status == 304 else result.size)
        for source, target in [("ETag", "etag"), ("Last-Modified", "last_modified"),
                               ("Content-Type", "content_type")]:
            value = header(result.headers, source)
            if value is not None:
                result_info[target] = value
        result_info.pop("error", None)
        return result_info
    except Exception as error:
        # Never publish stale bytes as a successful remote check.
        result_info = dict(previous) if old_valid else {"path": relative}
        result_info.update(status="failed", checked_at=now,
                           error=f"{type(error).__name__}: {error}", cached_copy=old_valid)
        return result_info


def record_key(record):
    return f"{record['type']}:{record['id']}"


def record_directory(record):
    return f"records/{record['type']}s/{record['id']}"


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


def recent_cutoff(state, started):
    previous = state.get("last_completed_sync_at")
    return (datetime.fromisoformat(previous) if previous else started) - timedelta(days=7)


def should_refresh(record, previous, cutoff, full=False):
    """Refresh article sources independently of failed asset downloads."""
    if full or not previous or previous.get("status") != "active":
        return True
    if previous.get("source_sha256") != digest(json_bytes(record)):
        return True
    if previous.get("page", {}).get("status") != "downloaded":
        return True
    if previous.get("featured_media_error") or previous.get("derived_view_available") is False:
        return True
    if record.get("content", {}).get("protected"):
        return True
    published = record.get("date_gmt")
    if published:
        return datetime.fromisoformat(published).replace(tzinfo=timezone.utc) >= cutoff
    # Explicit fallback for missing GMT metadata: refresh instead of guessing timezone.
    return True


def hydrate_inventory(root, census, state, client, cutoff, *, full, record_loader, progress):
    """Reuse hash-verified full sources; fetch only records due for refresh."""
    records = {}
    requested = {"posts": [], "pages": []}
    for summary in census:
        key = record_key(summary)
        previous = state["records"].get(key, {})
        cached = None
        if previous.get("directory"):
            source = safe_path(root, previous["directory"] + "/source.json")
            if source.is_file():
                data = source.read_bytes()
                if digest(data) == previous.get("source_sha256"):
                    candidate = json.loads(data.decode("utf-8"))
                    if record_key(candidate) != key:
                        raise ValueError(f"Cached source identity mismatch: {key}")
                    cached = candidate
        changed = cached is None or any(summary.get(field) != cached.get(field)
                                       for field in ("modified", "modified_gmt", "date_gmt"))
        needs_refresh = cached is None or full or changed
        if cached is not None and should_refresh(cached, previous, cutoff):
            needs_refresh = True
        if not verified_file(root, previous.get("page", {})):
            needs_refresh = True
        if cached and cached.get("featured_media"):
            featured_path = previous["directory"] + "/source-featured-media.json"
            if not verified_file(root, dict(path=featured_path, sha256=previous.get("featured_media_sha256"))):
                needs_refresh = True
        if needs_refresh:
            requested[summary["type"] + "s"].append(summary["id"])
        else:
            records[key] = cached
    summaries = {record_key(r): r for r in census}
    detail_inventories = {}
    fetched_keys = set()
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
            records[key] = record
            fetched_keys.add(key)
        detail_inventories["details-" + kind] = inventory
    return [records[record_key(r)] for r in census], detail_inventories, fetched_keys


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


def publish_indexes(root, state, stats, report, articles_root=None):
    articles_root = articles_root or root
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
    md = ["# Kiszámoló helyi tartalomarchívum", "", "Az eltűnt távoli tartalmak is szerepelnek; státuszuk `remote_missing`.",
          "", "[Médiaforrások statisztikája](MEDIA-SOURCES.md)", ""]
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
    smd = ["# Médiaforrások", "", "Az aktuális teljes ID-leltárhoz tartozó, legutóbb letöltött cikk- és oldaltörzsekben, valamint elérhető kiemelt képekben felismert URL-ek. A srcset-változatok külön URL-nek számítanak; ez nem különálló ábrák száma.",
           "", "| Host | Egyedi média-/fájl-URL | Érintett tartalom | Kép-URL | Képes tartalom | Embed-URL | Letölthető |",
           "|---|---:|---:|---:|---:|---:|---|"]
    for host, item in sorted(stats.items(), key=lambda x: (-x[1]["images"], x[0])):
        smd.append(f"| {host} | {item['assets']} | {item['asset_documents']} | {item['images']} | {item['image_documents']} | {item['embeds']} | {'igen' if item['download_allowed'] else 'nem'} |")
    atomic_write(articles_root / "MEDIA-SOURCES.md", ("\n".join(smd) + "\n").encode("utf-8"))
    atomic_write(articles_root / "media-sources.json", json_bytes(stats))
    atomic_write(articles_root / "last-report.json", json_bytes(report))
    # State is the commit marker and source of truth if publishing is interrupted.
    atomic_write(root / "state.json", json_bytes(state))


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
                    (asset.get("status") == "downloaded" or asset.get("cached_copy"))):
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
        markdown = make_markdown(display_record, str(soup), retained_anchors=retained_anchors).encode("utf-8")
        atomic_write(output_path, markdown)
        item["markdown_sha256"] = digest(markdown)
        if update_status:
            failures = [u for u in item.get("asset_urls", [])
                        if state.get("assets", {}).get(u, {}).get("status") == "failed"]
            item["failed_assets"] = failures
            item["external_assets"] = [u for u in item.get("asset_urls", [])
                                       if state.get("assets", {}).get(u, {}).get("status") == "external_reference"]
            item["acquisition_status"] = "incomplete" if failures or item.get("page", {}).get("status") != "downloaded" or record["content"].get("protected") or item.get("featured_media_error") or not item.get("derived_view_available") else "complete"
            if record["content"].get("protected"):
                item.setdefault("warnings", []).append("Protected content is not available through the public API.")
    return reassigned_preserved


def run_sync(root, *, articles_root=None, client=None, sample_ids=None, full=False, workers=4,
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
        prune_excluded(root, articles_root, state)
        prior_records = {key: dict(item) for key, item in state["records"].items()}
        cutoff = recent_cutoff(state, started)
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
        remote_records, detail_inventories, fetched_keys = hydrate_inventory(
            root, census, state, client, cutoff, full=full, record_loader=record_loader, progress=progress)
        progress(f"Full records fetched: {len(fetched_keys)}; cached sources reused: {len(remote_records) - len(fetched_keys)}")
        for record in remote_records:
            if urlsplit(record["link"]).hostname not in ALLOWED_HOSTS:
                raise ValueError(f"Unexpected article host: {record['link']}")
        selected = [r for r in remote_records if sample_ids is None or r["id"] in sample_ids]
        if sample_ids is not None and {r["id"] for r in selected} != set(sample_ids):
            raise ValueError("Sample includes IDs absent from the complete public inventory")
        selected_keys = {record_key(r) for r in selected}
        retained = [item for key, item in state["records"].items() if key not in selected_keys]
        planned_paths = plan_article_paths(selected, retained, articles_root=articles_root)
        progress(f"Inventory complete: {len(remote_records)} records; selected: {len(selected)}")
        taxonomies = {kind: metadata_loader(client, kind, progress=progress,
                                           fields=None if full else ("id", "name"))
                      for kind in ("categories", "tags")}
        names = {kind: {term["id"]: plain_title(term["name"]) for term in inv.records}
                 for kind, inv in taxonomies.items()}
        featured_ids = {r["featured_media"] for r in remote_records
                        if r.get("featured_media") and record_key(r) in fetched_keys}
        media_error = None
        failed_media_ids = set()
        try:
            media_inventory = metadata_loader(client, "media", ids=sorted(featured_ids), progress=progress)
        except Exception as error:
            media_inventory = getattr(error, "partial_inventory", None)
            failed_media_ids = set(getattr(error, "missing_ids", featured_ids))
            media_error = f"{type(error).__name__}: {error}"
            progress(f"Featured media metadata failed: {media_error}")
        featured = {m["id"]: m for m in media_inventory.records} if media_inventory else {}
        # Preserve the existing derived figure during a temporary metadata outage.
        # Only a verified cached record for the same current featured ID is eligible.
        for record in remote_records:
            media_id = record.get("featured_media")
            previous = state["records"].get(record_key(record), {})
            if media_id and media_id not in featured and previous.get("directory"):
                cached_path = safe_path(root, previous["directory"] + "/source-featured-media.json")
                if cached_path.is_file():
                    try:
                        cached = json.loads(cached_path.read_text(encoding="utf-8"))
                        if cached.get("id") == media_id and digest(json_bytes(cached)) == previous.get("featured_media_sha256"):
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
        discovered = {record_key(r): discover_record(r, featured.get(r.get("featured_media"))) for r in remote_records}
        refresh = {record_key(r) for r in selected
                   if record_key(r) in fetched_keys or should_refresh(r, state["records"].get(record_key(r)), cutoff, full)}
        for record in selected:
            key = record_key(record)
            previous = state["records"].get(key, {})
            directory = record_directory(record)
            featured_record = featured.get(record.get("featured_media"))
            media_hash = digest(json_bytes(featured_record)) if featured_record else None
            if previous.get("featured_media_sha256") != media_hash:
                refresh.add(key)
            if not verified_file(root, previous.get("page", {})):
                refresh.add(key)
            state["records"][key] = dict(previous, key=key, id=record["id"], type=record["type"],
                title=plain_title(record["title"]), published_at=record["date"],
                published_at_gmt=record.get("date_gmt"), modified_at=record.get("modified"),
                modified_at_gmt=record.get("modified_gmt"),
                url=record["link"], directory=directory, status="active", last_seen_at=stamp,
                path=planned_paths[key],
                source_sha256=digest(json_bytes(record)),
                asset_urls=sorted(discovered[key][0]), embed_urls=sorted(discovered[key][2]),
                categories=record.get("categories", []), tags=record.get("tags", []),
                category_names=[names["categories"].get(n, f"ID:{n}") for n in record.get("categories", [])],
                tag_names=[names["tags"].get(n, f"ID:{n}") for n in record.get("tags", [])],
                featured_media_sha256=media_hash,
                featured_media_error=media_error if record.get("featured_media") in failed_media_ids else None,
                warnings=conversion_warnings(record["content"]["rendered"]))
            state["records"][key].pop("remote_missing_since", None)
            atomic_write(root / directory / "source.json", json_bytes(record))
            atomic_write(root / directory / "source.html", record["content"]["rendered"].encode("utf-8"))
            if featured_record:
                atomic_write(root / directory / "source-featured-media.json", json_bytes(featured_record))
        progress(f"Public page refresh: {len(refresh)}; overlap starts {cutoff.isoformat()}")
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for record in selected:
                key = record_key(record)
                if key in refresh:
                    item = state["records"][key]
                    futures[pool.submit(fetch_file, client, record["link"], root,
                        item["directory"] + "/source-page.html", item.get("page", {}),
                        stamp, max_bytes=32 * 1024 * 1024)] = key
            for n, future in enumerate(concurrent.futures.as_completed(futures), 1):
                key = futures[future]
                state["records"][key]["page"] = future.result()
                if n % 25 == 0 or n == len(futures):
                    progress(f"Public pages: {n}/{len(futures)}")
        view_bodies = {}
        for record in selected:
            key = record_key(record)
            item = state["records"][key]
            body = record["content"]["rendered"]
            item["derived_source"] = "api_content"
            item["derived_view_available"] = True
            if record["type"] == "page" and not body.strip():
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
            # Exact intermediate used for the AI/readable derivatives.
            atomic_write(root / item["directory"] / "view-source.html", body.encode("utf-8"))
        stats = host_statistics(remote_records, discovered)
        all_assets = {u for record in selected for u in discovered[record_key(record)][0]}
        assets_refresh = {u for record in selected if record_key(record) in refresh
                          for u in discovered[record_key(record)][0]}
        jobs = {}
        # Avoid racing Windows path resolution with creation of the shared parent.
        safe_path(articles_root, "assets").mkdir(exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for url in sorted(all_assets):
                previous = state["assets"].get(url, {})
                if urlsplit(url).hostname not in ALLOWED_HOSTS:
                    state["assets"][url] = dict(status="external_reference", url=url)
                    continue
                valid = verified_file(articles_root, previous)
                if url in assets_refresh or not valid or previous.get("status") != "downloaded":
                    jobs[pool.submit(fetch_file, client, url, articles_root, asset_filename(url),
                        previous, stamp, max_bytes=max_asset_bytes, expected_media=True)] = url
            progress(f"Media refresh: {len(jobs)}; external references: {sum(urlsplit(u).hostname not in ALLOWED_HOSTS for u in all_assets)}")
            for n, future in enumerate(concurrent.futures.as_completed(jobs), 1):
                url = jobs[future]
                state["assets"][url] = dict(future.result(), url=url)
                if n % 50 == 0 or n == len(jobs):
                    progress(f"Media: {n}/{len(jobs)}")
        for key, item in state["records"].items():
            if key not in selected_keys:
                item["path"] = planned_paths[key]
        render_records = list(selected)
        for key, item in state["records"].items():
            if key not in selected_keys:
                cached_record, body = load_cached_view(root, key, item)
                render_records.append(cached_record)
                view_bodies[key] = body
        reassigned_preserved = render_views(root, articles_root, state, render_records, view_bodies, featured,
            prior_records=prior_records, previous_articles_root=previous_articles_root)
        remote_keys = {record_key(r) for r in remote_records}
        if sample_ids is None:
            for key, item in state["records"].items():
                if key not in remote_keys:
                    item["status"] = "remote_missing"
                    item.setdefault("remote_missing_since", stamp)
            # A completed traversal advances the overlap even with explicitly recorded
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
            selected_records=len(selected), refreshed_pages=len(refresh), refreshed_assets=len(jobs),
            full_records_fetched=len(fetched_keys), cached_records_reused=len(remote_records) - len(fetched_keys),
            content_check="full" if full else "modified_timestamps",
            api_response_count=len(api_sources), api_response_bytes=sum(r["size_bytes"] for r in api_sources),
            remote_missing=sum(r["status"] == "remote_missing" for r in state["records"].values()),
            incomplete_records=sum(state["records"][record_key(r)]["acquisition_status"] != "complete" for r in selected),
            asset_failures=failures, page_failures={record_key(r):state["records"][record_key(r)].get("page", {}).get("error")
                for r in selected if state["records"][record_key(r)].get("page", {}).get("status") != "downloaded"},
            media_hosts=stats, overlap_cutoff=cutoff.isoformat())
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
        publish_indexes(root, state, stats, report, articles_root)
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
        progress(f"Done: {len(selected)} records; incomplete: {report['incomplete_records']}; missing remotely: {report['remote_missing']}")
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
            if asset.get("status") == "downloaded" or asset.get("cached_copy"):
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
    parser.add_argument("--full", action="store_true", help="Refresh every public page and all same-host assets")
    parser.add_argument("--rebuild", action="store_true", help="Regenerate Markdown and indexes from verified local files only")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-asset-mib", type=float, help="Optional per-asset limit; over-limit assets are explicitly failed")
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
    if args.rebuild and (args.sample or args.full or args.max_asset_mib is not None):
        parser.error("--rebuild cannot be combined with --sample, --full, or --max-asset-mib")
    if args.sample and (output == DEFAULT_OUTPUT.resolve() or source_output == DEFAULT_SOURCE_OUTPUT.resolve()):
        parser.error("Sample requires separate --output and --source-output directories")
    try:
        if args.rebuild:
            offline_rebuild(source_output, output)
            return 0
        report = run_sync(source_output, articles_root=output, sample_ids=set(args.sample) if args.sample else None,
            full=args.full, workers=args.workers,
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
