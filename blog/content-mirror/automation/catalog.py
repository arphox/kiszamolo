"""Article files and the disposable ID-to-source-version update index."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile


ROOT = Path(__file__).resolve().parents[3]
ARTICLES_PATH = Path("blog/content-mirror/articles")
INDEX_PATH = ARTICLES_PATH / "wordpress-modified.json"
METADATA_FIELDS = (
    "id", "type", "title", "url", "published_at", "published_at_gmt",
    "modified_at", "modified_at_gmt", "derived_view_available",
)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def atomic_write(path, data):
    path = Path(path)
    if path.is_file() and path.read_bytes() == data:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def article_path(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError(f"Invalid article path: {relative!r}")
    boundary = (Path(root) / ARTICLES_PATH).resolve()
    path = (boundary / relative).resolve()
    if not path.is_relative_to(boundary) or path == boundary or path.suffix.lower() != ".md":
        raise ValueError(f"Article path escapes the collection or is not Markdown: {relative!r}")
    return path


def record_key(metadata):
    identity = metadata.get("id")
    kind = metadata.get("type")
    if type(identity) is not int or identity <= 0 or kind not in {"post", "page"}:
        raise ValueError("Article metadata requires a positive WordPress ID and post/page type")
    return f"{kind}:{identity}"


def metadata_from_row(row):
    return {field: row[field] for field in METADATA_FIELDS if field in row}


def list_article_paths(root=ROOT):
    """Locate IDs from filenames without opening any article."""
    paths = {}
    for path in (Path(root) / ARTICLES_PATH).rglob("*.md"):
        match = re.search(r" \[([1-9][0-9]*)\]\.md$", path.name)
        if not match:
            raise ValueError(f"Article filename must end with its WordPress ID: {path}")
        identity = match.group(1)
        if identity in paths:
            raise ValueError(f"Multiple article files have ID {identity}")
        paths[identity] = path
    return paths


def find_article(root, article_id):
    try:
        return list_article_paths(root)[str(article_id)]
    except KeyError:
        raise FileNotFoundError(f"Article {article_id} is not present in the collection") from None


def _parse_metadata(lines):
    metadata = {}
    for line in lines:
        key, separator, value = line.rstrip("\r\n").partition(": ")
        if not separator or not re.fullmatch(r"[a-z_]+", key) or key in metadata:
            raise ValueError(f"Invalid or duplicate article metadata field: {key!r}")
        metadata[key] = json.loads(value)
    record_key(metadata)
    return metadata


def split_article(text):
    text = text.removeprefix("\ufeff")
    if not text.startswith("---\n"):
        raise ValueError("Article is missing its metadata header")
    header, separator, body = text[4:].partition("\n---\n")
    if not separator:
        raise ValueError("Article metadata header is not closed")
    return _parse_metadata(header.splitlines()), body.lstrip("\n")


def read_article(path):
    return split_article(Path(path).read_text(encoding="utf-8"))


def read_metadata(path):
    """Read only the header; rebuilding the index never loads article bodies."""
    with Path(path).open(encoding="utf-8-sig") as source:
        if source.readline().rstrip("\r\n") != "---":
            raise ValueError(f"Article is missing its metadata header: {path}")
        lines = []
        for line in source:
            if line.rstrip("\r\n") == "---":
                return _parse_metadata(lines)
            lines.append(line)
    raise ValueError(f"Article metadata header is not closed: {path}")


def write_article(path, metadata, body):
    record_key(metadata)
    header = "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in metadata_from_row(metadata).items()
    )
    body = body.lstrip("\n")
    atomic_write(path, f"---\n{header}\n---\n\n{body}".encode("utf-8"))


def _validate_index(index):
    if not isinstance(index, dict):
        raise ValueError("Article index must map WordPress IDs to modified_gmt timestamps")
    for identity, modified in index.items():
        if not isinstance(identity, str) or not re.fullmatch(r"[1-9][0-9]*", identity):
            raise ValueError(f"Invalid WordPress ID in update index: {identity!r}")
        if not isinstance(modified, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", modified):
            raise ValueError(f"Invalid modified_gmt in update index: {identity}")


def save_index(root, index):
    _validate_index(index)
    ordered = dict(sorted(index.items(), key=lambda item: int(item[0])))
    atomic_write(Path(root) / INDEX_PATH, (json.dumps(ordered, indent=2) + "\n").encode("utf-8"))


def load_index(root=ROOT):
    path = Path(root) / INDEX_PATH
    if not path.is_file():
        return rebuild_index(root)
    with path.open(encoding="utf-8") as source:
        index = json.load(source)
    _validate_index(index)
    return index


def rebuild_index(root=ROOT):
    root = Path(root)
    index = {}
    for identity, path in list_article_paths(root).items():
        metadata = read_metadata(path)
        if str(metadata["id"]) != identity:
            raise ValueError(f"Article header and filename IDs differ: {path}")
        index[identity] = metadata["modified_at_gmt"]
    save_index(root, index)
    return index


@contextmanager
def exclusive_run(root=ROOT):
    """Use one process-held lock outside the article collection."""
    path = Path(root) / "blog/content-mirror/.update.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.seek(0, 2) == 0:
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
