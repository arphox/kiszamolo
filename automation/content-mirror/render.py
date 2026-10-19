"""Helpers for turning WordPress HTML into local mirror references and views.

The JSON returned by WordPress remains the authoritative preserved source. These
functions only create convenient derived views and never write files.
"""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from markdownify import MarkdownConverter


_ASSET_EXTENSIONS = frozenset(
    ".avif .bmp .csv .doc .docx .gif .jpeg .jpg .m4a .m4v .mp3 .mp4 .odt .ogg .pdf "
    ".mkv .mov .png .ppt .pptx .rar .rtf .svg .tif .tiff .txt .wav .webm .webp .xls .xlsx .zip .7z".split()
)
_RESOURCE_ATTRIBUTES = {
    "img": ("src", "data-src"),
    "source": ("src", "data-src"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "object": ("data",),
    "embed": ("src",),
}


def _absolute_http_url(value: str, base_url: str) -> str | None:
    """Resolve a URL and retain only safe, credential-free HTTP(S) URLs."""
    value = html.unescape(value.strip())
    if not value:
        return None
    try:
        absolute = urljoin(base_url, value)
        parts = urlsplit(absolute)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        # Fragments identify a location inside a document, not a separate asset.
        return urlunsplit((parts.scheme.lower(), parts.netloc, parts.path, parts.query, ""))
    except ValueError:
        return None


def _absolute_http_reference(value: str, base_url: str) -> str | None:
    """Resolve a safe HTTP(S) reference without removing its fragment."""
    try:
        absolute = urljoin(base_url, html.unescape(value.strip()))
    except ValueError:
        return None
    if _absolute_http_url(absolute, base_url) is None:
        return None
    return absolute


def _tag_text(tag: Tag, attribute: str) -> str | None:
    value = tag.get(attribute)
    if isinstance(value, list):
        value = " ".join(str(part) for part in value)
    return str(value) if value is not None else None


def _srcset_parts(value: str) -> list[tuple[str, str]]:
    """Parse srcset into (URL, descriptor) pairs, allowing commas inside URLs."""
    result: list[tuple[str, str]] = []
    i = 0
    length = len(value)
    while i < length:
        while i < length and (value[i].isspace() or value[i] == ","):
            i += 1
        if i >= length:
            break
        start = i
        while i < length and not value[i].isspace():
            i += 1
        token = value[start:i]
        trailing_comma = len(token) - len(token.rstrip(","))
        url = token.rstrip(",")
        descriptor_start = i
        if trailing_comma:
            descriptor = ""
        else:
            while i < length and value[i] != ",":
                i += 1
            descriptor = value[descriptor_start:i].strip()
        if url:
            result.append((url, descriptor))
        if i < length:
            i += 1
    return result


def discover_assets(html: str, base_url: str) -> set[str]:
    """Find downloadable media/document URLs referenced by content HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    found: set[str] = set()

    def add(value: str | None) -> None:
        if not value:
            return
        absolute = _absolute_http_url(value, base_url)
        if absolute:
            found.add(absolute)

    for tag in soup.find_all(True):
        for attribute in _RESOURCE_ATTRIBUTES.get(tag.name, ()):
            add(_tag_text(tag, attribute))
        for attribute in (("srcset", "imagesrcset", "data-srcset") if tag.name in {"img", "source"} else ("srcset", "imagesrcset")):
            value = _tag_text(tag, attribute)
            if value:
                for candidate, _ in _srcset_parts(value):
                    add(candidate)
        if tag.name == "a":
            href = _tag_text(tag, "href")
            if href:
                absolute = _absolute_http_url(href, base_url)
                if absolute and urlsplit(absolute).path.lower().endswith(tuple(_ASSET_EXTENSIONS)):
                    found.add(absolute)
        if tag.name == "iframe":
            src = _tag_text(tag, "src")
            absolute = _absolute_http_url(src, base_url) if src else None
            if absolute and urlsplit(absolute).path.lower().endswith(tuple(_ASSET_EXTENSIONS)):
                found.add(absolute)
    return found


def discover_embeds(html: str, base_url: str) -> set[str]:
    """Find iframe destinations for reporting; these are not downloaded."""
    soup = BeautifulSoup(html or "", "html.parser")
    result: set[str] = set()
    for iframe in soup.find_all("iframe"):
        src = _tag_text(iframe, "src")
        absolute = _absolute_http_url(src, base_url) if src else None
        if absolute:
            result.add(absolute)
    return result


def _url_key(value: str, base_url: str) -> str | None:
    return _absolute_http_url(value, base_url)


def _mapped_url(value: str, base_url: str, paths: dict[str, str]) -> str | None:
    absolute = _url_key(value, base_url)
    if absolute is None:
        return None
    path = paths.get(absolute)
    if path is None:
        # Accommodate maps keyed by an unescaped or original relative URL.
        path = paths.get(value)
    if path is None:
        return None
    original = urlsplit(urljoin(base_url, html.unescape(value.strip())))
    fragment = f"#{original.fragment}" if original.fragment else ""
    return f"{path}{fragment}"


def _rewrite_srcset(value: str, base_url: str, asset_paths: dict[str, str]) -> str:
    candidates: list[str] = []
    for url, descriptor in _srcset_parts(value):
        replacement = _mapped_url(url, base_url, asset_paths)
        if replacement is None:
            replacement = _absolute_http_reference(url, base_url) or url
        candidates.append(replacement + (f" {descriptor}" if descriptor else ""))
    return ", ".join(candidates)


def _is_lazy_placeholder(value: str | None) -> bool:
    if not value:
        return True
    lowered = value.strip().lower()
    return lowered.startswith("data:image/") or lowered == "about:blank"


def rewrite_html(
    html: str,
    base_url: str,
    asset_paths: dict[str, str],
    article_paths: dict[str, str],
    embed_paths: dict[str, str] | None = None,
) -> str:
    """Rewrite known media and internal article references to local paths."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(True):
        for attribute in _RESOURCE_ATTRIBUTES.get(tag.name, ()):
            value = _tag_text(tag, attribute)
            if not value:
                continue
            replacement = _mapped_url(value, base_url, asset_paths)
            if replacement is None:
                replacement = _absolute_http_reference(value, base_url) or value
            tag[attribute] = replacement
        for attribute in (("srcset", "imagesrcset", "data-srcset") if tag.name in {"img", "source"} else ("srcset", "imagesrcset")):
            value = _tag_text(tag, attribute)
            if value:
                tag[attribute] = _rewrite_srcset(value, base_url, asset_paths)
        if tag.name in {"img", "source"}:
            lazy_src = _tag_text(tag, "data-src")
            current_src = _tag_text(tag, "src")
            if lazy_src and _is_lazy_placeholder(current_src):
                if _absolute_http_reference(lazy_src, base_url):
                    tag["src"] = _tag_text(tag, "data-src")
            lazy_srcset = _tag_text(tag, "data-srcset")
            current_srcset = _tag_text(tag, "srcset")
            if lazy_srcset and _is_lazy_placeholder(current_srcset):
                tag["srcset"] = _tag_text(tag, "data-srcset")
        if tag.name == "iframe":
            src = _tag_text(tag, "src")
            if src:
                replacement = _mapped_url(src, base_url, asset_paths)
                if replacement is None and embed_paths:
                    replacement = _mapped_url(src, base_url, embed_paths)
                tag["src"] = replacement or _absolute_http_reference(src, base_url) or src
        if tag.name == "a":
            href = _tag_text(tag, "href")
            if href:
                replacement = _mapped_url(href, base_url, article_paths)
                if replacement is None:
                    replacement = _mapped_url(href, base_url, asset_paths)
                if replacement is None:
                    replacement = _absolute_http_reference(href, base_url) or href
                tag["href"] = replacement
    return str(soup)


def plain_title(rendered: Any) -> str:
    """Convert a WordPress rendered title value to plain text."""
    if isinstance(rendered, dict):
        rendered = rendered.get("rendered", "")
    soup = BeautifulSoup(str(rendered or ""), "html.parser")
    return soup.get_text(" ", strip=True)


def extract_public_content(page_html: bytes | str, record: dict[str, Any]) -> str | None:
    """Extract a known public page content container when API content is empty.

    This intentionally returns ``None`` for unknown layouts instead of treating
    the whole document body as article content, which would include navigation
    and other site chrome.
    """
    soup = BeautifulSoup(page_html or b"", "html.parser")
    candidate = None
    for selector in (".ct-inner-content", "main", '[role="main"]', ".entry-content", "article"):
        candidate = soup.select_one(selector)
        if candidate is not None:
            break
    if candidate is None:
        return None

    title = plain_title(record.get("title", ""))
    first_heading = candidate.find("h1")
    if first_heading is not None and plain_title(str(first_heading)) == title:
        first_heading.decompose()
    return str(candidate)


class _MirrorMarkdownConverter(MarkdownConverter):
    """Markdown converter that keeps structures Markdown cannot represent."""

    def convert_table(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        if el.find("table") or el.find(attrs={"rowspan": True}) or el.find(attrs={"colspan": True}):
            return f"\n\n{str(el)}\n\n"
        return super().convert_table(el, text, parent_tags)

    def convert_iframe(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return f"\n\n{str(el)}\n\n"

    def convert_video(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return f"\n\n{str(el)}\n\n"

    def convert_audio(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return f"\n\n{str(el)}\n\n"

    def convert_object(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return f"\n\n{str(el)}\n\n"

    def convert_embed(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return f"\n\n{str(el)}\n\n"

    def convert_picture(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return f"\n\n{str(el)}\n\n"

    def convert_a(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        if el.has_attr("id") and not text.strip() and not el.has_attr("href"):
            return str(el)
        return super().convert_a(el, text, parent_tags)


def _markdown_body(content_html: str) -> str:
    soup = BeautifulSoup(content_html or "", "html.parser")
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    # Keep text such as an escaped ``<p>`` visibly literal in the Markdown file.
    # Markdownify otherwise emits it as an actual HTML tag.
    lt_token = "\ue000A\ue001"
    gt_token = "\ue000B\ue001"
    star_token = "\ue000C\ue001"
    underscore_token = "\ue000D\ue001"
    for tag in list(soup.find_all(True)):
        if tag.has_attr("id") and not (
            tag.name == "a" and not tag.has_attr("href") and not tag.get_text(strip=True)
        ):
            anchor = soup.new_tag("a", id=tag["id"])
            tag.insert_before(anchor)
    for node in list(soup.find_all(string=True)):
        if node.parent and node.parent.name not in {"script", "style"}:
            value = str(node).replace("<", lt_token).replace(">", gt_token)
            if node.find_parent(["code", "pre"]) is None:
                value = value.replace("*", star_token).replace("_", underscore_token)
            node.replace_with(NavigableString(value))
    converter = _MirrorMarkdownConverter(
        heading_style="ATX",
        bullets="-",
        escape_underscores=False,
        escape_asterisks=False,
        strip=["script", "style"],
    )
    converted = converter.convert(str(soup)).strip()
    return (
        converted.replace(lt_token, "&lt;")
        .replace(gt_token, "&gt;")
        .replace(star_token, r"\*")
        .replace(underscore_token, r"\_")
    )


def make_markdown(record: dict[str, Any], content_html: str) -> str:
    """Create an AI-readable Markdown derivative with machine-readable metadata."""
    title = plain_title(record.get("title", ""))
    metadata = {
        "id": record.get("id"),
        "type": record.get("type", "post"),
        "title": title,
        "url": record.get("link"),
        "published_at": record.get("date"),
        "published_at_gmt": record.get("date_gmt"),
        "modified_at": record.get("modified"),
        "category_names": record.get("category_names"),
        "tag_names": record.get("tag_names"),
    }
    metadata = {key: value for key, value in metadata.items() if value is not None}
    front_matter = "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in metadata.items())
    body = _markdown_body(content_html)
    return f"---\n{front_matter}\n---\n\n# {title}\n\n{body}\n"


def conversion_warnings(content_html: str) -> list[str]:
    """Return deterministic notes for structures retained as HTML or omitted."""
    soup = BeautifulSoup(content_html or "", "html.parser")
    warnings: list[str] = []
    if soup.find("table") and any(table.find("table") or table.find(attrs={"rowspan": True}) or table.find(attrs={"colspan": True}) for table in soup.find_all("table")):
        warnings.append("Complex tables are retained as HTML in Markdown.")
    if soup.find(["iframe", "video", "audio", "object", "embed", "picture"]):
        warnings.append("Embedded media is retained as HTML in Markdown.")
    if soup.find(["script", "style"]):
        warnings.append("Script or style content is omitted from the Markdown derivative.")
    return warnings
