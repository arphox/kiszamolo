"""Update the local Markdown mirror from the public WordPress API."""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from article_links import repair_markdown_links
from catalog import (ARTICLES_PATH, article_path, atomic_write,
                     exclusive_run, list_article_paths, load_index,
                     read_article, read_metadata, rebuild_index, save_index, split_article,
                     write_article)
from paths import plan_article_paths
from remote import HttpClient, MissingMediaError, fetch_census, fetch_metadata, fetch_records
from render import extract_public_content, make_markdown, plain_title, rewrite_html


ROOT = Path(__file__).resolve().parents[3]
ALLOWED_HOSTS = {"kiszamolo.hu", "www.kiszamolo.hu"}
EXCLUSIONS_PATH = ARTICLES_PATH / "exclusions.json"


def record_key(record):
    return f"{record['type']}:{record['id']}"


def _relative_url(source: Path, target: Path) -> str:
    return quote(os.path.relpath(target, source).replace("\\", "/"), safe="/")


class RelativePaths:
    def __init__(self, directory, targets):
        self.directory = directory
        self.targets = targets

    def get(self, key, default=None):
        target = self.targets.get(key)
        return _relative_url(self.directory, target) if target is not None else default


def _aliases(url):
    parts = urlsplit(url)
    if parts.hostname not in ALLOWED_HOSTS:
        return [url]
    paths = {parts.path, parts.path.rstrip("/"), parts.path.rstrip("/") + "/"}
    return [urlunsplit((scheme, host, path, "", ""))
            for scheme in ("http", "https") for host in ALLOWED_HOSTS for path in paths]


def _render_record(client, record, featured, targets, embeds, article_dir):
    content = record.get("content") or {}
    if content.get("protected"):
        raise ValueError("Protected article content is not publicly available")
    source = content.get("rendered") or ""
    derived_available = bool(source.strip())
    if not source.strip():
        try:
            response = client.request_bytes(record["link"])
            extracted = extract_public_content(response.body, record)
            if extracted is None:
                raise ValueError("Could not identify article content in the public page")
            source = extracted
            derived_available = bool(source.strip())
        except HTTPError as error:
            if error.code not in {404, 410}:
                raise
            error.close()
            source = ""
            derived_available = False

    media = featured.get(record.get("featured_media"))
    if (media and media.get("media_type") == "image" and media.get("source_url")
            and media["source_url"] not in source):
        image_url = html.escape(media["source_url"], quote=True)
        alt = html.escape(media.get("alt_text", ""), quote=True)
        caption = (media.get("caption") or {}).get("rendered", "")
        source = f'<figure><img src="{image_url}" alt="{alt}"><figcaption>{caption}</figcaption></figure>\n' + source

    rewritten = rewrite_html(source, record["link"], RelativePaths(article_dir, targets),
                             RelativePaths(article_dir, embeds))
    retained_anchors = {str(tag["id"]) for tag in BeautifulSoup(rewritten, "html.parser").find_all(True)
                        if tag.has_attr("id")}
    metadata, body = split_article(make_markdown(record, rewritten, retained_anchors=retained_anchors))
    metadata["derived_view_available"] = derived_available
    return metadata, body

def _needs_detail(item, previous_modified, local_path):
    return (previous_modified != item.get("modified_gmt") or local_path is None or
            not local_path.is_file())


def _census_paths(census, local_paths, article_root):
    targets, embeds = {}, {}
    for item in census:
        path = local_paths.get(str(item["id"]))
        if path is None:
            continue
        target = path if path.is_absolute() else article_root / path
        for alias in _aliases(item["link"]):
            targets[alias] = target
            embeds[alias.rstrip("/") + "/embed/"] = target
    return targets, embeds


def run_sync(root=ROOT, *, client=None, inventory_loader=fetch_census,
             record_loader=fetch_records, metadata_loader=fetch_metadata,
             progress=print):
    """Fetch the lightweight census and render only new or changed articles."""
    root = Path(root).resolve()
    article_root = root / ARTICLES_PATH
    client = client or HttpClient(allowed_hosts=ALLOWED_HOSTS)
    report = {"incomplete_records": 0, "changed_article_ids": [], "errors": [],
              "excluded_records": [], "post_ids": []}

    with exclusive_run(root):
        exclusions = json.loads((root / EXCLUSIONS_PATH).read_text(encoding="utf-8"))
        modified_by_id = load_index(root)
        local_paths = list_article_paths(root)
        inventories = [inventory_loader(client, kind, progress=progress)
                       for kind in ("posts", "pages")]
        census = [record for inventory in inventories for record in inventory.records]
        census_by_id = {record["id"]: record for record in census}
        if len(census_by_id) != len(census):
            raise ValueError("Duplicate WordPress IDs across posts and pages")

        known_ids = {str(item_id) for item_id in census_by_id} | set(modified_by_id) | set(local_paths)
        excluded = {identity: reason for identity, reason in exclusions.items()
                    if identity in known_ids}
        report["excluded_records"] = excluded

        deleted_targets = {}
        for identity in excluded:
            modified_by_id.pop(identity, None)
            path = local_paths.pop(identity, None)
            if path is not None:
                if not path.is_absolute():
                    path = article_root / path
                metadata = read_metadata(path)
                if metadata.get("url"):
                    deleted_targets[path.relative_to(article_root).as_posix()] = metadata["url"]
                path.unlink(missing_ok=True)
        if excluded:
            save_index(root, modified_by_id)

        excluded_ids = {int(identity) for identity in excluded}
        census = [item for item in census if item["id"] not in excluded_ids]
        census_by_id = {record["id"]: record for record in census}

        post_ids = {item["id"] for item in census if item["type"] == "post"}
        link_records = list(census)
        for item_id, path in local_paths.items():
            numeric_id = int(item_id)
            if numeric_id in census_by_id or numeric_id in excluded_ids or not path.is_file():
                continue
            metadata = read_metadata(path)
            link_records.append({"id": numeric_id, "link": metadata["url"]})
            if metadata.get("type") == "post":
                post_ids.add(numeric_id)
        report["post_ids"] = sorted(post_ids)

        changed = [item for item in census
                   if _needs_detail(item, modified_by_id.get(str(item["id"])),
                                    local_paths.get(str(item["id"])))]
        requested = {"posts": [], "pages": []}
        for item in changed:
            requested[item["type"] + "s"].append(item["id"])

        records, errors_by_id = {}, {}
        for kind, ids in requested.items():
            if not ids:
                continue
            try:
                result = record_loader(client, kind, ids=ids, progress=progress)
                fetched = {record["id"]: record for record in result.records}
                if set(fetched) != set(ids):
                    raise ValueError(f"Incomplete targeted {kind} response")
                for item_id, record in fetched.items():
                    summary = census_by_id.get(item_id)
                    if summary is None or any(record.get(field) != summary.get(field)
                                              for field in ("modified", "modified_gmt", "date_gmt")):
                        errors_by_id[item_id] = "Record changed during census; retry the update"
                    else:
                        records[item_id] = record
            except Exception as error:
                for item_id in ids:
                    errors_by_id[item_id] = f"{type(error).__name__}: {error}"

        media_ids = sorted({record.get("featured_media") for record in records.values()
                            if record.get("featured_media")})
        media = {}
        if media_ids:
            try:
                media_inventory = metadata_loader(client, "media", ids=media_ids, progress=progress)
                media = {item["id"]: item for item in media_inventory.records}
            except Exception as error:
                failed_media_ids = set(media_ids)
                if isinstance(error, MissingMediaError):
                    media = {item["id"]: item for item in error.partial_inventory.records}
                    failed_media_ids = error.missing_ids
                for item_id, record in records.items():
                    if record.get("featured_media") in failed_media_ids:
                        errors_by_id.setdefault(
                            item_id, f"Featured media: {type(error).__name__}: {error}")

        processable = {item_id: record for item_id, record in records.items()
                       if item_id not in errors_by_id}
        path_records = {item_id: record for item_id, record in processable.items()
                        if str(item_id) not in local_paths}
        path_plan = plan_article_paths(list(path_records.values()), (), article_root)
        planned_paths = {item_id: local_paths[str(item_id)].relative_to(article_root).as_posix()
                         for item_id in processable
                         if str(item_id) in local_paths}
        planned_paths.update({item_id: path_plan[record_key(record)]
                              for item_id, record in path_records.items()})
        targets, embeds = _census_paths(link_records, local_paths, article_root)

        written_ids = set()
        for item_id, record in processable.items():
            target_path = article_path(root, planned_paths[item_id])
            try:
                metadata, body = _render_record(
                    client, record, media, targets, embeds, target_path.parent)
                identity = str(item_id)
                old_path = local_paths.get(identity)
                old_article_path = (old_path if old_path and old_path.is_absolute()
                                    else article_root / old_path if old_path else None)
                article_exists = bool(old_article_path and old_article_path.is_file())
                prior_metadata, prior_body = (read_article(old_article_path) if article_exists
                                               else ({}, None))
                missing_but_stale = bool(
                    old_path and not article_exists and
                    modified_by_id.get(identity) != metadata.get("modified_at_gmt"))
                changed_content = (old_path is None or missing_but_stale or
                                   (article_exists and (prior_metadata.get("title") != metadata["title"] or
                                                        prior_body != body)))
                write_article(target_path, metadata, body)
                previous_stamp = modified_by_id.get(identity)
                modified_by_id[identity] = metadata["modified_at_gmt"]
                try:
                    save_index(root, modified_by_id)
                except Exception:
                    if previous_stamp is None:
                        modified_by_id.pop(identity, None)
                    else:
                        modified_by_id[identity] = previous_stamp
                    raise
                local_paths[identity] = target_path
                written_ids.add(item_id)
                if record["type"] == "post" and changed_content:
                    report["changed_article_ids"].append(item_id)
                if not metadata.get("derived_view_available", True):
                    report["incomplete_records"] += 1
            except Exception as error:
                errors_by_id[item_id] = f"{type(error).__name__}: {error}"

        report["errors"] = [{"record": str(item_id), "error": message}
                            for item_id, message in sorted(errors_by_id.items())]
        report["incomplete_records"] += len(errors_by_id)

        new_targets = {}
        for item_id in written_ids:
            identity = str(item_id)
            for alias in _aliases(census_by_id[item_id]["link"]):
                new_targets[alias] = local_paths[identity].relative_to(article_root).as_posix()

        known_paths = {path.relative_to(article_root).as_posix()
                       if path.is_absolute() else Path(path).as_posix()
                       for path in local_paths.values()}
        if deleted_targets:
            repair_paths = [(path, path.relative_to(article_root).as_posix())
                            for path in article_root.rglob("*.md")]
        else:
            repair_paths = [(local_paths[str(item_id)],
                            local_paths[str(item_id)].relative_to(article_root).as_posix())
                            for item_id in written_ids]
        if new_targets or deleted_targets:
            for path, relative in repair_paths:
                if relative not in known_paths:
                    continue
                original = path.read_text(encoding="utf-8")
                repaired = repair_markdown_links(original, relative, {},
                                                 remote_targets=new_targets,
                                                 deleted_targets=deleted_targets)
                if repaired != original:
                    atomic_write(path, repaired.encode("utf-8"))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild-index", action="store_true",
                        help="Recreate the disposable ID-to-modified-time lookup")
    args = parser.parse_args()
    try:
        if args.rebuild_index:
            with exclusive_run(ROOT):
                rebuild_index(ROOT)
            return 0
        report = run_sync(ROOT)
        for error in report["errors"]:
            print(f"{error['record']}: {error['error']}", file=sys.stderr)
        return 2 if report["incomplete_records"] else 0
    except KeyboardInterrupt:
        print("Interrupted; rerun to continue.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Sync failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
