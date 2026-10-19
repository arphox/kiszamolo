"""Split the export, trimming boundary blank lines without overwriting files."""

import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "raw_source" / "kiszamolo_cikkek-1.txt"
DESTINATION = ROOT / "articles"
SEPARATOR = re.compile(rb"(?m)^={80}\n")
HEADER = re.compile(
    r"\A\n*#(?P<id>\d+) (?P<title>[^\n]+)\n"
    r"Dátum: (?P<date>[^\n]+)\nHivatkozás: (?P<url>[^\n]+)\n"
)


def trim_boundary_lines(content):
    lines = re.findall(rb"[^\n]*\n|[^\n]+", content)
    start, end = 0, len(lines)
    while start < end and not lines[start].decode("utf-8").strip():
        start += 1
    while end > start and not lines[end - 1].decode("utf-8").strip():
        end -= 1
    return b"".join(lines[start:end])


def normalize_title(title):
    title = re.sub(r'[?"]', "", title).replace(":", " - ")
    title = re.sub(r'[<>/\\|*\x00-\x1f]', " ", title)
    return re.sub(r"\s+", " ", title).strip(" .-")


def plan_archive():
    """Validate the entire export and all paths before creating anything."""
    source = SOURCE.read_bytes()
    source.decode("utf-8")  # Reject invalid UTF-8 without replacing characters.
    if source.startswith(b"\xef\xbb\xbf") or b"\r" in source:
        raise ValueError("Expected the original BOM-free UTF-8 export with LF newlines")
    separators = list(SEPARATOR.finditer(source))
    if not separators:
        raise ValueError("No article separators found")
    if source[separators[-1].end():].strip():
        raise ValueError("Unexpected content after the final separator")

    planned = []
    records = []
    names = set()
    urls = set()
    start = 0
    for expected_id, separator in enumerate(separators, 1):
        content = source[start:separator.start()]
        start = separator.end()
        text = content.decode("utf-8")
        header = HEADER.match(text)
        if not header or int(header["id"]) != expected_id:
            raise ValueError(f"Invalid article header or sequence at record {expected_id}")
        if len(re.findall(r"(?m)^#\d+ ", text)) != 1:
            raise ValueError(f"Unexpected article boundary at record {expected_id}")
        title, date, url = header["title"], header["date"], header["url"]
        timestamp = datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
        if timestamp.strftime("%Y-%m-%d %H:%M:%S") != date:
            raise ValueError(f"Noncanonical date at record {expected_id}")
        if not url.startswith("https://kiszamolo.hu/") or url in urls:
            raise ValueError(f"Invalid or duplicate URL at record {expected_id}")
        urls.add(url)
        safe_title = normalize_title(title)
        if not safe_title:
            raise ValueError(f"Empty normalized title at record {expected_id}")
        filename = f"{timestamp:%Y-%m-%d %H.%M} {safe_title}.txt"
        relative = Path(f"{timestamp:%Y}") / f"{timestamp:%m}" / filename
        target = DESTINATION / relative
        # Windows measures filename/path lengths in UTF-16 code units.
        if len(filename.encode("utf-16-le")) // 2 > 255:
            raise ValueError(f"Filename exceeds 255 characters: {filename}")
        if len(str(target).encode("utf-16-le")) // 2 >= 260:
            raise ValueError(f"Path exceeds the legacy Windows limit: {target}")
        key = relative.as_posix().casefold()
        if key in names:
            raise ValueError(f"Filename collision: {relative}")
        names.add(key)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"Article target is not a regular file: {target}")
        parent = target.parent
        while parent != ROOT:
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise ValueError(f"Output parent is not a regular directory: {parent}")
            parent = parent.parent
        planned.append((target, trim_boundary_lines(content)))
        records.append(dict(title=title, published_at=timestamp.isoformat(),
                            url=url, path=relative.as_posix()))

    index = b"".join(
        (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        for record in records
    )
    index_path = DESTINATION / "index.jsonl"
    if index_path.is_symlink() or (index_path.exists() and not index_path.is_file()):
        raise ValueError(f"Index target is not a regular file: {index_path}")
    if index_path.exists() and index_path.read_bytes() != index:
        raise ValueError("Existing index.jsonl differs; no files were written")
    return planned, index_path, index


def main():
    planned, index_path, index = plan_archive()
    created = skipped = 0
    for target, content in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as article:
                article.write(content)
        except FileExistsError:
            skipped += 1
            continue
        if target.read_bytes() != content:
            raise OSError(f"Written article differs from planned content: {target}")
        created += 1
    try:
        with index_path.open("xb") as output:
            output.write(index)
    except FileExistsError:
        if index_path.read_bytes() != index:
            raise ValueError("Index changed during processing; it was not overwritten")
    if index_path.read_bytes() != index:
        raise OSError("Written index differs from the planned index")
    print(f"Articles: {len(planned)}; created: {created}; skipped: {skipped}")


if __name__ == "__main__":
    main()
