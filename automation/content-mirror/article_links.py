"""Repair links in generated article Markdown after paths change."""

from __future__ import annotations

import html
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit, urlunsplit


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LIST_ITEM = re.compile(r"^( *)(?:[-+*]|[0-9]+[.)])[ \t]+")
_MARKDOWN_LINK = re.compile(
    r"\]\([ \t]*(?P<destination><[^>\n]*>|[^\s)]+)"
)
_MARKDOWN_BRACKET = re.compile(r"\\.|[\[\]]")
_INLINE_CODE = re.compile(r"(?<![\\`])(?P<ticks>`+).*?(?<![\\`])(?P=ticks)(?!`)", re.DOTALL)
_HTML_TAG = re.compile(
    r"<(?:a|area|base|link|img|source|video|audio|iframe|frame|embed|object|script|input|track)\b[^<>]*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_ATTRIBUTE = re.compile(r"(?<![\w:-])(href|src)([ \t]*=[ \t]*)([\"'])(.*?)\3", re.IGNORECASE | re.DOTALL)


def _normalized(path: str) -> str:
    return posixpath.normpath(path)  # Map paths are raw filesystem names, not URLs.


def _protected_blocks(markdown: str) -> list[tuple[int, int]]:
    spans = []
    offset = 0
    fence = None
    fence_start = 0
    indented_start = None
    prior_list_indent = None
    for line in markdown.splitlines(keepends=True):
        end = offset + len(line)
        text = line.rstrip("\r\n")
        if fence:
            close = re.match(r"^ {0,3}([`~]+)[ \t]*$", text)
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= len(fence):
                spans.append((fence_start, end))
                fence = None
            offset = end
            continue

        opening = _FENCE.match(text)
        if opening and (opening.group(1)[0] != "`" or "`" not in text[opening.end():]):
            if indented_start is not None:
                spans.append((indented_start, offset))
                indented_start = None
            fence = opening.group(1)
            fence_start = offset
            offset = end
            continue

        list_item = _LIST_ITEM.match(text)
        if indented_start is not None:
            if not text.strip() or re.match(r"^( {4,}|\t+)", text):
                offset = end
                continue
            spans.append((indented_start, offset))
            indented_start = None

        if list_item:
            indent = len(list_item.group(1))
            if indent < 4 or (prior_list_indent is not None and indent >= prior_list_indent):
                prior_list_indent = indent
                offset = end
                continue

        if re.match(r"^( {4,}|\t+)", text):
            indented_start = offset
        elif text.strip() and not list_item:
            prior_list_indent = None
        offset = end

    if fence:
        spans.append((fence_start, len(markdown)))
    if indented_start is not None:
        spans.append((indented_start, len(markdown)))
    return spans


def _code_spans(markdown: str) -> list[tuple[int, int]]:
    """Find fenced/indented blocks and Markdown backtick spans, including multiline spans."""
    blocks = _protected_blocks(markdown)
    spans = blocks.copy()
    cursor = 0
    for start, end in blocks:
        if start > cursor:
            spans.extend(match.span() for match in _INLINE_CODE.finditer(markdown, cursor, start))
        cursor = max(cursor, end)
    if cursor < len(markdown):
        spans.extend(match.span() for match in _INLINE_CODE.finditer(markdown, cursor))
    return sorted(spans)


def _rewrite_target(
    destination: str,
    source_path: str,
    moves: dict[str, str],
    remote_targets: dict[str, str],
    deleted_targets: dict[str, str],
) -> str:
    try:
        parts = urlsplit(destination)
    except ValueError:
        return destination
    source_dir = posixpath.dirname(source_path) or "."
    if parts.scheme or parts.netloc:
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        local_path = remote_targets.get(url)
        if local_path is None:
            return destination
        relative = posixpath.relpath(local_path, source_dir)
        return urlunsplit(("", "", quote(relative, safe="/"), "", parts.fragment))
    if not parts.path:
        return destination

    target = posixpath.normpath(posixpath.join(source_dir, unquote(parts.path)))
    if target in deleted_targets:
        remote = urlsplit(deleted_targets[target])
        return urlunsplit((remote.scheme, remote.netloc, remote.path,
                           remote.query or parts.query, parts.fragment or remote.fragment))
    new_path = moves.get(target)
    if new_path is None:
        return destination
    relative = posixpath.relpath(new_path, source_dir)
    return urlunsplit(("", "", quote(relative, safe="/"), parts.query, parts.fragment))


def _rewrite_segment(
    text: str,
    source_path: str,
    moves: dict[str, str],
    remote_targets: dict[str, str],
    deleted_targets: dict[str, str],
    brackets: list[int],
) -> str:
    def replace_url(url: str) -> str:
        return _rewrite_target(url, source_path, moves, remote_targets, deleted_targets)

    def html_tag(match: re.Match[str]) -> str:
        tag = match.group(0)

        def html_attribute(attribute: re.Match[str]) -> str:
            old = html.unescape(attribute.group(4))
            new = replace_url(old)
            if new == old:
                return attribute.group(0)
            return (attribute.group(1) + attribute.group(2) + attribute.group(3)
                    + html.escape(new, quote=False) + attribute.group(3))

        return _HTML_ATTRIBUTE.sub(html_attribute, tag)

    replacements = []
    destination_end = 0
    for bracket in _MARKDOWN_BRACKET.finditer(text):
        if bracket.start() < destination_end:
            continue
        if bracket.group() == "[":
            brackets.append(bracket.start())
        elif bracket.group() == "]" and brackets:
            brackets.pop()
            match = _MARKDOWN_LINK.match(text, bracket.start())
            if match is None:
                continue
            destination = match.group("destination")
            angle = destination.startswith("<") and destination.endswith(">")
            url = destination[1:-1] if angle else destination
            replacement = replace_url(url)
            if replacement != url:
                replacements.append((*match.span("destination"),
                                     f"<{replacement}>" if angle else replacement))
            destination_end = match.end()
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return _HTML_TAG.sub(html_tag, text)


def repair_markdown_links(
    markdown: str,
    source_path: str,
    moves: dict[str, str],
    *,
    remote_targets: dict[str, str] | None = None,
    deleted_targets: dict[str, str] | None = None,
) -> str:
    """Rewrite generated inline links to moved, downloaded, or excluded articles/assets."""
    remote_targets = remote_targets or {}
    deleted_targets = deleted_targets or {}
    if not moves and not remote_targets and not deleted_targets:
        return markdown
    move_map = {_normalized(old): _normalized(new) for old, new in moves.items()}
    deleted_map = {_normalized(path): url for path, url in deleted_targets.items()}
    remote_map = {}
    for url, path in remote_targets.items():
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        remote_map[urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))] = _normalized(path)

    source_path = _normalized(source_path)
    output = []
    cursor = 0
    brackets = []  # Link labels may contain inline code spans.
    for start, end in _code_spans(markdown):
        if start > cursor:
            output.append(_rewrite_segment(
                markdown[cursor:start], source_path, move_map, remote_map, deleted_map, brackets))
        output.append(markdown[start:end])
        cursor = end
    if cursor < len(markdown):
        output.append(_rewrite_segment(markdown[cursor:], source_path, move_map, remote_map, deleted_map, brackets))
    return "".join(output)
