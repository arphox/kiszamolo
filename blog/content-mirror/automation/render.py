"""Helpers for turning WordPress HTML into local mirror references and views.

The JSON returned by WordPress remains the authoritative preserved source. These
functions only create convenient derived views and never write files.
"""

from __future__ import annotations

import html
import json
import copy
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment, Tag
from markdownify import MarkdownConverter, should_remove_whitespace_outside

from tables import table_markdown


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
    # BeautifulSoup has already decoded HTML attribute values.
    value = value.strip()
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
        absolute = urljoin(base_url, value.strip())
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
    original = urlsplit(urljoin(base_url, value.strip()))
    fragment = f"#{original.fragment}" if original.fragment else ""
    return f"{path}{fragment}"


def _rewrite_srcset(value: str, base_url: str) -> str:
    candidates: list[str] = []
    for url, descriptor in _srcset_parts(value):
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
    article_paths: dict[str, str],
    embed_paths: dict[str, str] | None = None,
) -> str:
    """Keep media URLs absolute and rewrite known internal article references."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(True):
        for attribute in _RESOURCE_ATTRIBUTES.get(tag.name, ()):
            value = _tag_text(tag, attribute)
            if not value:
                continue
            tag[attribute] = _absolute_http_reference(value, base_url) or value
        for attribute in (("srcset", "imagesrcset", "data-srcset") if tag.name in {"img", "source"} else ("srcset", "imagesrcset")):
            value = _tag_text(tag, attribute)
            if value:
                tag[attribute] = _rewrite_srcset(value, base_url)
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
                replacement = _mapped_url(src, base_url, embed_paths or {})
                tag["src"] = replacement or _absolute_http_reference(src, base_url) or src
        if tag.name == "a":
            href = _tag_text(tag, "href")
            if href:
                replacement = _mapped_url(href, base_url, article_paths)
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


def _punctuation(character: str) -> bool:
    return unicodedata.category(character).startswith(("P", "S"))


def _neighbor_character(node, before: bool) -> str:
    """Find visible inline context through wrappers, stopping at block edges."""
    while node is not None:
        sibling = node.previous_sibling if before else node.next_sibling
        if sibling is not None:
            if isinstance(sibling, Tag):
                if sibling.name == "br" or should_remove_whitespace_outside(sibling):
                    return "\n"
                text = sibling.get_text()
            else:
                text = "" if isinstance(sibling, Comment) else str(sibling)
            if text:
                return text[-1] if before else text[0]
            node = sibling
        else:
            node = node.parent
            if isinstance(node, Tag) and node.name == "a" and node.has_attr("href"):
                return "[" if before else "]"
            if node is None or should_remove_whitespace_outside(node):
                return "\n"
    return "\n"


class _MirrorMarkdownConverter(MarkdownConverter):
    """Markdown converter that keeps structures Markdown cannot represent."""

    def process_tag(self, node: Tag, parent_tags: set[str] | None = None) -> str:
        # Adjacent equivalent emphasis must share delimiters: *E**bben* is not
        # equivalent to *Ebben*. Normalize a copy of the immediate container so
        # retained HTML still sees the original DOM, including inside tables.
        formats = {"b": "strong", "strong": "strong", "i": "em", "em": "em",
                   "s": "del", "del": "del"}

        if "_noformat" not in (parent_tags or set()) and node.name in formats:
            link = node
            while isinstance(link, Tag) and link.name in formats:
                children = [child for child in link.contents
                            if isinstance(child, Tag) or str(child).strip()]
                if len(children) != 1:
                    break
                link = children[0]
            if isinstance(link, Tag) and link.name == "a" and link.has_attr("href"):
                # ***[label](url)***suffix cannot close its emphasis after the
                # punctuation of a link. [***label***](url)suffix can.
                original = node
                node = copy.deepcopy(node)
                inner_link = node.find("a", href=True)
                outer_link = copy.copy(inner_link)
                outer_link.clear()
                inner_link.unwrap()
                outer_link.append(node)
                node = outer_link
                node.parent = original.parent
                node.previous_sibling = original.previous_sibling
                node.next_sibling = original.next_sibling

        def mergeable(tag, sibling):
            return (isinstance(tag, Tag) and tag.name in formats and
                    not tag.has_attr("id") and not tag.has_attr("name") and
                    isinstance(sibling, Tag) and formats.get(sibling.name) == formats[tag.name] and
                    not sibling.has_attr("id") and not sibling.has_attr("name"))

        if ("_noformat" not in (parent_tags or set()) and node.name not in {"pre", "code", "kbd", "samp"}
                and (any(mergeable(tag, tag.next_sibling) for tag in node.children) or
                     any(isinstance(tag, Tag) and tag.name == "span" and
                         not tag.has_attr("id") and not tag.has_attr("name") for tag in node.children))):
            original = node
            node = copy.deepcopy(node)
            # List numbering and inline/block decisions also consult DOM context.
            node.parent = original.parent
            node.previous_sibling = original.previous_sibling
            node.next_sibling = original.next_sibling
            # Spans and presentation attributes do not survive Markdown conversion.
            # Remove only immediate transparent wrappers on this converter copy.
            while spans := [tag for tag in node.children if isinstance(tag, Tag) and
                            tag.name == "span" and not tag.has_attr("id") and not tag.has_attr("name")]:
                for tag in spans:
                    tag.unwrap()
            for tag in list(node.children):
                sibling = tag.next_sibling
                while mergeable(tag, sibling):
                    for child in list(sibling.contents):
                        tag.append(child.extract())
                    sibling.decompose()
                    sibling = tag.next_sibling
        return super().process_tag(node, parent_tags)

    def _spaced_inline(self, convert, el: Tag, text: str, parent_tags: set[str]) -> str:
        # markdownify's chomp() strips all whitespace but restores only ASCII
        # spaces, and discards whitespace-only tags. Keep boundaries outside markup.
        if "_noformat" in parent_tags or not text.strip():
            return text
        start = len(text) - len(text.lstrip())
        end = len(text.rstrip())
        return text[:start] + convert(el, text[start:end], parent_tags) + text[end:]

    def convert_b(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return self._emphasis(el, text, parent_tags, "**", "strong")

    convert_strong = convert_b

    def convert_i(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return self._emphasis(el, text, parent_tags, "*", "em")

    convert_em = convert_i

    def convert_del(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return self._emphasis(el, text, parent_tags, "~~", "del")

    convert_s = convert_del

    def _emphasis(self, el: Tag, text: str, parent_tags: set[str], marker: str, tag: str) -> str:
        if "_noformat" in parent_tags or not text.strip():
            return text
        # Emphasis cannot span Markdown paragraphs. Preserve each paragraph's
        # boundary whitespace, including hard breaks, outside its delimiters.
        parts = re.split(r"(\n[ \t]*\n)", text)
        for index in range(0, len(parts), 2):
            part = parts[index]
            if not part.strip():
                continue
            start, end = len(part) - len(part.lstrip()), len(part.rstrip())
            content = part[start:end]
            before = part[start - 1] if start else ("\n" if index else _neighbor_character(el, True))
            after = part[end] if end < len(part) else (
                "\n" if index < len(parts) - 1 else _neighbor_character(el, False))
            # A punctuation edge beside a word cannot open/close '*' emphasis.
            # Inline HTML preserves the exact styled range and still allows
            # Markdown links and code inside it.
            first = content.lstrip("*")[:1] or content[0]
            last = content.rstrip("*")[-1:] or content[-1]
            safe_start = not _punctuation(first) or before.isspace() or _punctuation(before)
            safe_end = not _punctuation(last) or after.isspace() or _punctuation(after)
            if safe_start and safe_end:
                wrapped = marker + content + marker
            else:
                wrapped = f"<{tag}>{content}</{tag}>"
            parts[index] = part[:start] + wrapped + part[end:]
        return "".join(parts)

    def process_text(self, el, parent_tags=None):
        text = super().process_text(el, parent_tags)
        if "_noformat" not in (parent_tags or set()) and _neighbor_character(el, True) == "\n":
            # Escape source numbers before generated list markers exist. This
            # also covers divs and transparent inline wrappers at block starts.
            text = re.sub(r"^([ \t]*\d{1,9})([.)])(?=[ \t])", r"\1\\\2", text)
        return text

    def convert_p(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        if "_noformat" not in parent_tags:
            # Paragraph numbers are literal text, not Markdown list markers.
            text = re.sub(r"(?m)^([ \t]*\d{1,9})([.)])(?=[ \t])", r"\1\\\2", text)
        return super().convert_p(el, text, parent_tags)

    def escape(self, text: str, parent_tags: set[str]) -> str:
        # Markdownify skips this hook for code; retained HTML uses the original DOM.
        return html.escape(super().escape(text, parent_tags), quote=False)

    def convert_table(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        converted = table_markdown(el, lambda cell: self.convert_soup(
            BeautifulSoup(cell.decode_contents(), "html.parser")).strip())
        if converted is not None:
            return f"\n\n{converted}\n\n"
        if (el.find("table") or el.find(attrs={"rowspan": True}) or
                el.find(attrs={"colspan": True}) or el.find(attrs={"bgcolor": True}) or
                el.find(["pre", "ul", "ol"]) or el.find("a", id=True) or
                any(not cell.get_text(strip=True) and re.search(
                    r"(?:^|;)\s*background(?:-color)?\s*:", str(cell.get("style", "")), re.I)
                    for cell in el.find_all(["td", "th"]))):
            return f"\n\n{str(el)}\n\n"
        return super().convert_table(el, text, parent_tags)

    def convert_iframe(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        src = str(el.get("src", ""))
        title = str(el.get("title", "")).strip()
        try:
            parts = urlsplit(src)
            host = parts.hostname or ""
        except ValueError:
            return self._media_links(el, text, [src], title or "Beágyazott tartalom")
        label = "Beágyazott tartalom"
        if host in {"youtube.com", "www.youtube.com", "www.youtube-nocookie.com", "youtu.be"}:
            label = "YouTube-videó"
        elif host in {"vimeo.com", "player.vimeo.com"}:
            label = "Vimeo-videó"
        elif host in {"facebook.com", "www.facebook.com"}:
            label = "Facebook-videó"
            target = parse_qs(parts.query).get("href", [""])[0]
            if (_absolute_http_reference(target, src) and
                    urlsplit(target).hostname in {"facebook.com", "www.facebook.com", "fb.watch"}):
                src = target
        elif parts.path.lower().endswith(tuple(_ASSET_EXTENSIONS)):
            label = "Beágyazott fájl"
        if title and title.lower() not in {"youtube video player", "video player"}:
            label = title
        return self._media_links(el, text, [src], label)

    def convert_video(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        urls = [el.get("src", "")] + [source.get("src", "") for source in el.find_all("source")]
        result = self._media_links(el, text, urls, "Videó")
        if el.get("poster"):
            poster = _markdown_destination(str(el["poster"]))
            result += f"\n\n![Videó előnézeti képe]({poster})\n\n"
        return result

    def convert_audio(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        urls = [el.get("src", "")] + [source.get("src", "") for source in el.find_all("source")]
        return self._media_links(el, text, urls, "Hangfelvétel")

    def convert_object(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return self._media_links(el, text, [el.get("data", "")], "Beágyazott fájl")

    def convert_embed(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return self._media_links(el, text, [el.get("src", "")], "Beágyazott fájl")

    def convert_picture(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return f"\n\n{text.strip()}\n\n" if el.find("img") else f"\n\n{str(el)}\n\n"

    def _media_links(self, el: Tag, text: str, urls: list[str], label: str) -> str:
        links = [f"[{_markdown_label(label)}]({_markdown_destination(str(url))})"
                 for url in dict.fromkeys(urls) if url]
        fallback = text.strip()
        if el.name == "iframe":
            fallback = el.get_text()
            for _ in range(5):
                decoded = html.unescape(fallback)
                if decoded == fallback:
                    break
                fallback = decoded
            cleaned = re.sub(
                r'<span\b(?=[^>]*(?:data-mce-type=["\']bookmark["\']|mce_SELRES))[^>]*>'
                r'[\s\ufeff]*</span>', "", fallback, flags=re.I)
            if cleaned != fallback or re.search(r'</?p\b|<br\b', cleaned, re.I):
                fragment = BeautifulSoup(cleaned, "html.parser")
                for tag in fragment.find_all(["script", "style"]):
                    tag.decompose()
                for comment in fragment.find_all(string=lambda node: isinstance(node, Comment)):
                    comment.extract()
                fallback = self.convert_soup(fragment).strip()
            else:
                fallback = text.strip()
        if fallback:
            links.append(fallback)
        return "\n\n" + "\n\n".join(links) + "\n\n"

    def convert_sup(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        if ("_noformat" in parent_tags or
                re.fullmatch(r"(?:\*+|\[\d+\]|st|nd|rd|th)", el.get_text().strip())):
            return text
        return _positioned_text(text, "^")

    def convert_sub(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return text if "_noformat" in parent_tags else _positioned_text(text, r"\_")

    def convert_dt(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return f"\n\n{text.strip()}\n\n" if el.find("table") else super().convert_dt(el, text, parent_tags)

    def convert_dd(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        return f"\n\n{text.strip()}\n\n"

    def convert_img(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        image = copy.copy(el)
        image.parent = el.parent
        if image.has_attr("src"):
            image["src"] = _markdown_destination(str(image["src"]))
        return super().convert_img(image, text, parent_tags)

    def convert_a(self, el: Tag, text: str, parent_tags: set[str]) -> str:
        if el.has_attr("id") and not text.strip() and not el.has_attr("href"):
            return str(el)
        link = copy.copy(el)
        if link.has_attr("href"):
            link["href"] = _markdown_destination(str(link["href"]))
        return self._spaced_inline(super().convert_a, link, text, parent_tags)


def _markdown_destination(value: str) -> str:
    """Encode Markdown delimiters without decoding existing URL escapes."""
    safe = ":/?#[]@!$&'*+,;=%"
    try:
        parts = urlsplit(value)
    except ValueError:
        return quote(value, safe=safe)
    if parts.netloc:
        # Keep international hostnames for the Markdown reader's IDNA handling.
        start = value.find("//") + 2
        end = start + len(parts.netloc)
        if value[start:end] == parts.netloc:
            return quote(value[:start], safe=safe) + parts.netloc + quote(value[end:], safe=safe)
    return quote(value, safe=safe)


def _markdown_label(value: str) -> str:
    value = html.escape(value, quote=False).replace("\\", "\\\\")
    return re.sub(r"([\[\]*_`])", r"\\\1", value)


def _positioned_text(text: str, marker: str) -> str:
    content = text.strip()
    return text.replace(content, f"{marker}({content})", 1) if content else text


_TECHNICAL_ANCHOR = re.compile(
    r"(?:(?:section|new_columns|inner_content|div_block|headline|text_block|_posts_grid|"
    r"link_text|link|image|span|code_block|video)-\d+-\d+|more-\d+|zone\d+|bubble-\d+|"
    r"attachment_\d+|caption-attachment-\d+|gtx-host|wrfi|pr\d+id)\Z")


def referenced_anchors(content_html: str) -> set[str]:
    """Conservatively retain fragment targets, including cross-article links."""
    result = set()
    for tag in BeautifulSoup(content_html or "", "html.parser").find_all("a", href=True):
        try:
            fragment = urlsplit(str(tag["href"])).fragment
        except ValueError:
            continue
        if fragment:
            result.add(unquote(fragment))
    return result


def _markdown_body(content_html: str, retained_anchors: set[str] | None = None) -> str:
    soup = BeautifulSoup(content_html or "", "html.parser")
    for comment in soup.find_all(string=lambda node: isinstance(node, Comment)):
        comment.extract()
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    for tag in soup.select('span[data-mce-type="bookmark"], span.mce_SELRES_start, span.mce_SELRES_end'):
        if not tag.get_text().strip(" \t\r\n\ufeff"):
            tag.decompose()
    keep = referenced_anchors(content_html) | (retained_anchors or set())
    for tag in list(soup.find_all(True)):
        if tag.has_attr("id") and _TECHNICAL_ANCHOR.fullmatch(str(tag["id"])) and tag["id"] not in keep:
            del tag["id"]
            if (tag.name == "a" and not tag.has_attr("href") and
                    not tag.get_text(strip=True) and not tag.find(True)):
                tag.decompose()
                continue
        if tag.has_attr("id") and not (
            tag.name == "a" and not tag.has_attr("href") and not tag.get_text(strip=True)
        ):
            anchor = soup.new_tag("a", id=tag["id"])
            tag.insert_before(anchor)
            del tag["id"]
    converter = _MirrorMarkdownConverter(
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    )
    return converter.convert_soup(soup).strip()


def make_markdown(record: dict[str, Any], content_html: str, *, retained_anchors: set[str] | None = None) -> str:
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
    body = _markdown_body(content_html, retained_anchors)
    return f"---\n{front_matter}\n---\n\n# {title}\n\n{body}\n"


_CONVERSION_NOTES = {
    "Complex tables are retained as HTML in Markdown.",
    "Embedded media is retained as HTML in Markdown.",
    "Merged table cells are repeated in Markdown; unsupported tables remain HTML.",
    "Embedded media is represented by links or images in Markdown.",
    "Script or style content is omitted from the Markdown derivative.",
}


def conversion_warnings(content_html: str, *, previous: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Return deterministic notes for structures retained as HTML or omitted."""
    soup = BeautifulSoup(content_html or "", "html.parser")
    warnings = [note for note in previous if note not in _CONVERSION_NOTES]
    if soup.find("table") and any(table.find("table") or table.find(attrs={"rowspan": True}) or table.find(attrs={"colspan": True}) for table in soup.find_all("table")):
        warnings.append("Merged table cells are repeated in Markdown; unsupported tables remain HTML.")
    if soup.find(["iframe", "video", "audio", "object", "embed", "picture"]):
        warnings.append("Embedded media is represented by links or images in Markdown.")
    if soup.find(["script", "style"]):
        warnings.append("Script or style content is omitted from the Markdown derivative.")
    return warnings
