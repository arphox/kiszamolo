"""Persist which article input each enrichment assignment has processed."""

import hashlib
import json
import re
from pathlib import Path

from blog.enrichment.automation.common import ClassificationError, catalog


PROCESSING_PATH = "blog/enrichment/processing.json"
DIMENSIONS = ("topics", "content_nature")
HASH_RE = re.compile(r"[0-9a-f]{64}")


def article_fingerprint(metadata, body):
    """Hash only canonical classifier input, excluding unrelated WordPress metadata."""
    title = metadata.get("title")
    if not isinstance(title, str):
        raise ClassificationError("A cikk címe nem olvasható a metaadatokból.")
    content = json.dumps([title, body], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_processing(value):
    if not isinstance(value, dict) or set(value) != {"articles"} or not isinstance(value["articles"], dict):
        raise ClassificationError("Hibás blog/enrichment/processing.json szerkezet.")
    for article_id, dimensions in value["articles"].items():
        if not re.fullmatch(r"[1-9][0-9]*", article_id) or not isinstance(dimensions, dict):
            raise ClassificationError(f"Hibás feldolgozási állapot: {article_id}")
        if set(dimensions) - set(DIMENSIONS):
            raise ClassificationError(f"Ismeretlen feldolgozási szempont: {article_id}")
        for dimension, marker in dimensions.items():
            if (not isinstance(marker, dict) or set(marker) != {"modified_gmt", "content_sha256"}
                    or marker["modified_gmt"] is not None and not isinstance(marker["modified_gmt"], str)
                    or not isinstance(marker["content_sha256"], str)
                    or not HASH_RE.fullmatch(marker["content_sha256"])):
                raise ClassificationError(f"Hibás feldolgozási jelölő: {article_id}/{dimension}")
    return value


def load_processing(root):
    path = Path(root) / PROCESSING_PATH
    if not path.is_file():
        return {"articles": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClassificationError(f"A feldolgozási nyilvántartás nem olvasható: {error}") from error
    return validate_processing(value)


def save_processing(root, processing):
    validate_processing(processing)
    output = (json.dumps(processing, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    catalog.atomic_write(Path(root) / PROCESSING_PATH, output)


def save_marker(root, processing, article_id, dimension, marker):
    article_id = str(article_id)
    articles = processing["articles"]
    previous_article = articles.get(article_id)
    article = dict(previous_article or {})
    previous_marker = article.get(dimension)
    article[dimension] = marker
    articles[article_id] = article
    try:
        save_processing(root, processing)
    except BaseException:
        if previous_marker is None:
            article.pop(dimension, None)
        else:
            article[dimension] = previous_marker
        if article:
            articles[article_id] = article
        else:
            articles.pop(article_id, None)
        raise


def pending_dimensions(article_id, modified_gmt, assignment, processing, dimensions=DIMENSIONS, *, force=False):
    article = processing["articles"].get(str(article_id), {})
    return [dimension for dimension in dimensions
            if (force or dimension not in assignment
                or (marker := article.get(dimension)) is None
                or marker["modified_gmt"] != modified_gmt)]
