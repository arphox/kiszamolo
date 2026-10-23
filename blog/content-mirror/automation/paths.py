"""Deterministic Markdown archive paths for WordPress posts and pages."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any


class _PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_title(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("rendered", "")
    parser = _PlainText()
    parser.feed(str(value or ""))
    parser.close()
    return " ".join(parser.parts)


def normalize_title(title: str) -> str:
    """Match the existing article exporter title normalization exactly."""
    title = re.sub(r'[?"]', "", title).replace(":", " - ")
    title = re.sub(r'[<>/\\|*\x00-\x1f]', " ", title)
    return re.sub(r"\s+", " ", title).strip(" .-")


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO timestamp string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field}: {value!r}") from exc
    if parsed.tzinfo is not None or parsed.isoformat(timespec="seconds") != value:
        raise ValueError(f"{field} must be canonical timezone-free YYYY-MM-DDTHH:MM:SS")
    return parsed


def _identity(record: dict[str, Any]) -> tuple[str, int, str]:
    record_type = record.get("type")
    record_id = record.get("id")
    if record_type not in {"post", "page"}:
        raise ValueError(f"Unsupported WordPress record type: {record_type!r}")
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id <= 0:
        raise ValueError(f"Invalid WordPress record ID: {record_id!r}")
    key = f"{record_type}:{record_id}"
    provided_key = record.get("key")
    if provided_key is not None and provided_key != key:
        raise ValueError(f"Record key {provided_key!r} does not match {key!r}")
    return key, record_id, record_type


def _generated_path(title: str, timestamp: datetime, record_id: int | None = None) -> str:
    normalized = normalize_title(title)
    if not normalized:
        raise ValueError("Title is empty after filename normalization")
    suffix = f" [{record_id}]" if record_id is not None else ""
    return f"{timestamp:%Y}/{timestamp:%m}/{timestamp:%Y-%m-%d %H.%M} {normalized}{suffix}.md"


def _retained_path(record: dict[str, Any], timestamp: datetime) -> str | None:
    value = record.get("path")
    if value is None:
        return None
    if not isinstance(value, str) or "\\" in value:
        raise ValueError(f"Unsafe retained path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 3 or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe retained path: {value!r}")
    if path.suffix.casefold() != ".md":
        raise ValueError(f"Retained archive path must end in .md: {value!r}")
    if path.parts[:2] != (f"{timestamp:%Y}", f"{timestamp:%m}"):
        raise ValueError(f"Retained path date folders do not match its publication date: {value!r}")
    if not path.name.startswith(f"{timestamp:%Y-%m-%d %H.%M} "):
        raise ValueError(f"Retained filename date does not match its publication date: {value!r}")
    return path.as_posix()


def _check_path_lengths(relative: str, articles_root: Path | None) -> None:
    filename = PurePosixPath(relative).name
    if len(filename.encode("utf-16-le")) // 2 > 255:
        raise ValueError(f"Filename exceeds 255 UTF-16 code units: {relative}")
    if articles_root is not None:
        absolute = articles_root.joinpath(*PurePosixPath(relative).parts)
        if len(str(absolute).encode("utf-16-le")) // 2 >= 260:
            raise ValueError(f"Path exceeds the legacy Windows limit: {absolute}")


def plan_article_paths(
    records: list[dict[str, Any]],
    retained: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
    articles_root: Path | None = None,
) -> dict[str, str]:
    """Plan active and retained article paths without writing or renaming files.

    A non-colliding retained record keeps its validated existing path. If that
    path collides with another active or retained record, every record in the
    collision group receives the deterministic ``[WordPress ID]`` suffix.
    """
    root = Path(articles_root) if articles_root is not None else None
    entries: dict[str, dict[str, Any]] = {}
    seen_ids: set[int] = set()
    for is_retained, group in ((False, records), (True, retained)):
        for record in group:
            key, record_id, _ = _identity(record)
            if key in entries:
                raise ValueError(f"Duplicate WordPress record key: {key}")
            if record_id in seen_ids:
                raise ValueError(f"WordPress IDs must be globally unique: {record_id}")
            seen_ids.add(record_id)

            title_value = record.get("title", "")
            title = _plain_title(title_value)
            timestamp_value = record.get("published_at") if is_retained else record.get("date")
            timestamp = _timestamp(timestamp_value, "published_at" if is_retained else "date")
            generated = _generated_path(title, timestamp)
            existing = _retained_path(record, timestamp) if is_retained else None
            entries[key] = {
                "timestamp": timestamp,
                "generated": generated,
                "generated_with_id": _generated_path(title, timestamp, record_id),
                "candidate": existing or generated,
            }

    if root is not None:
        # Also check path length before collision resolution, then validate the
        # final suffixed names below.
        for entry in entries.values():
            _check_path_lengths(entry["candidate"], root)

    suffixed: set[str] = set()
    while True:
        paths: dict[str, str] = {}
        for key, entry in entries.items():
            path = entry["generated"]
            if key in suffixed:
                path = entry["generated_with_id"]
            elif entry["candidate"] != entry["generated"]:
                path = entry["candidate"]
            paths[key] = path

        groups: dict[str, list[str]] = defaultdict(list)
        for key, path in paths.items():
            groups[path.casefold()].append(key)
        collisions = [keys for keys in groups.values() if len(keys) > 1]
        if not collisions:
            break
        added = False
        for keys in collisions:
            for key in keys:
                if key not in suffixed:
                    suffixed.add(key)
                    added = True
        if not added:
            raise ValueError("Path collision remains after adding stable ID suffixes")

    for path in paths.values():
        _check_path_lengths(path, root)
    return paths
