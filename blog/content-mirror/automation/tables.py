"""Small Markdown renderer for tables whose spans or fills carry meaning."""

from __future__ import annotations

import re
from typing import Callable

from bs4 import Tag


_MAX_ROWS = 512
_MAX_COLUMNS = 128
_MAX_GRID_CELLS = 10_000
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _positive_span(cell: Tag, name: str) -> int | None:
    value = cell.get(name)
    if value is None:
        return 1
    try:
        span = int(str(value).strip())
    except ValueError:
        return None
    return span if span > 0 else None


def _style_background(style: object) -> tuple[bool, str | None, bool]:
    """Return whether inline CSS sets a background, its hex color, and support status."""
    if not isinstance(style, str):
        return False, None, False
    winner: tuple[bool, str | None, bool] = (False, None, False)
    found = False
    for declaration in style.split(";"):
        property_name, separator, value = declaration.partition(":")
        if not separator:
            continue
        property_name = property_name.strip().lower()
        if property_name not in {"background", "background-color"}:
            continue
        found = True
        value = value.strip()
        important = bool(re.search(r"\s*!\s*important\s*$", value, re.IGNORECASE))
        value = re.sub(r"\s*!\s*important\s*$", "", value, flags=re.IGNORECASE).strip()
        if winner[0] and not important:
            continue
        # A later shorthand resets background-color unless it contains a color.
        match = re.match(r"#[0-9a-fA-F]{3,8}(?![0-9a-fA-F])", value)
        color = match.group(0) if match and _HEX_COLOR.fullmatch(match.group(0)) else None
        lowered = value.lower()
        no_color = {"", "transparent", "none", "initial", "inherit", "unset", "revert"}
        if color:
            unsupported = False
        elif property_name == "background-color":
            unsupported = lowered not in no_color
        elif re.search(r"(?:rgb|hsl|hwb|lab|lch|color)\s*\(", lowered):
            unsupported = True
        else:
            # Named colors, images, gradients, and other forms are retained through HTML.
            unsupported = lowered not in no_color
        winner = (important, color, unsupported)
    return found, winner[1], winner[2]


def _background(tag: Tag) -> tuple[bool, str | None, bool]:
    # Inline CSS outranks the legacy bgcolor presentation attribute.
    has_style_background, style_color, unsupported = _style_background(tag.get("style"))
    if has_style_background:
        return True, style_color, unsupported
    bgcolor = tag.get("bgcolor")
    if isinstance(bgcolor, str):
        color = bgcolor.strip()
        if _HEX_COLOR.fullmatch(color):
            return True, color, False
        return True, None, bool(color)
    return False, None, False


def _effective_background(cell: Tag, row: Tag, table: Tag) -> tuple[str | None, bool]:
    for tag in (cell, row, table):
        present, color, unsupported = _background(tag)
        if unsupported:
            return None, True
        if color is not None:
            return color, False
        if present:
            # A style-level shorthand with no color resets the presentation
            # bgcolor on this element, but does not prevent the table showing through.
            continue
    return None, False


def _flatten(value: str) -> str:
    # Markdown table cells cannot contain physical rows. Collapse whitespace so
    # words from paragraph and line-break conversions remain separated.
    value = re.sub(r"\s+", " ", value).strip()
    return value.replace("|", r"\|")


def _row_cells(row: Tag) -> list[Tag]:
    return [child for child in row.find_all(["td", "th"], recursive=False) if isinstance(child, Tag)]


def _rule(width: int) -> str:
    return "| " + " | ".join("---" for _ in range(width)) + " |"


def table_markdown(table: Tag, convert_cell: Callable[[Tag], str]) -> str | None:
    """Render a spanned or color-coded table as readable Markdown.

    Repeating a spanning cell in the expanded grid denotes a merged cell.
    ``None`` means this table is simple or unsupported and should use the
    caller's existing conversion/fallback path.
    """
    if table.name != "table" or table.find("table") or table.find("pre"):
        return None
    # The renderer emits only cells; structural anchors outside a cell would
    # otherwise disappear. Let the caller retain the original HTML in that case.
    for anchor in table.find_all("a", id=True):
        container = anchor.find_parent(["td", "th", "caption"])
        if container is None or container.find_parent("table") is not table:
            return None
    # Flattening Markdown list output would collapse list items into one cell line.
    if table.find(["ul", "ol"]):
        return None
    rows = table.find_all("tr")
    if not rows or len(rows) > _MAX_ROWS:
        return None
    if any(row.find_parent("table") is not table for row in rows):
        return None

    grid: list[dict[int, str]] = [{} for _ in rows]
    needs_conversion = False
    meaningful_color = False
    total_slots = 0

    for row_index, row in enumerate(rows):
        cells = _row_cells(row)
        for cell in cells:
            colspan = _positive_span(cell, "colspan")
            rowspan = _positive_span(cell, "rowspan")
            if colspan is None or rowspan is None or row_index + rowspan > len(rows):
                return None
            if colspan > _MAX_COLUMNS or rowspan > _MAX_ROWS:
                return None
            needs_conversion |= colspan > 1 or rowspan > 1

            # Start at the first free slot; if the declared span overlaps an
            # earlier rowspan, the source grid is ambiguous, so preserve HTML.
            column = 0
            while column in grid[row_index]:
                column += 1
            if column + colspan > _MAX_COLUMNS:
                return None
            if any(column + offset in grid[row_index] for offset in range(colspan)):
                return None
            value = _flatten(convert_cell(cell) or "")
            effective_color, unsupported_color = _effective_background(cell, row, table)
            if unsupported_color:
                return None
            cell_color = next(
                (color for tag in (cell, row) if (color := _background(tag)[1]) is not None),
                None,
            )
            color = cell_color if value else effective_color
            if color:
                if value:
                    value += f" [szín: {color}]"
                else:
                    value = f"[szín: {color}]"
                    meaningful_color = True
            for target_row in range(row_index, row_index + rowspan):
                for target_column in range(column, column + colspan):
                    if target_column in grid[target_row]:
                        return None
                    grid[target_row][target_column] = value
            total_slots += colspan * rowspan
            if total_slots > _MAX_GRID_CELLS:
                return None

    if not needs_conversion and not meaningful_color:
        return None
    width = max((max(row.keys(), default=-1) + 1 for row in grid), default=0)
    if width == 0 or width > _MAX_COLUMNS or len(rows) * width > _MAX_GRID_CELLS:
        return None
    matrix = [[row.get(column, "") for column in range(width)] for row in grid]

    caption = table.find("caption", recursive=False)
    caption_text = _flatten(convert_cell(caption) or "") if isinstance(caption, Tag) else ""
    lines: list[str] = []
    if caption_text:
        lines.extend((f"**{caption_text}**", ""))
    # The first physical row is kept as the Markdown header. Any multi-level
    # header rows remain present below it, with expanded values still visible.
    lines.append("| " + " | ".join(matrix[0]) + " |")
    lines.append(_rule(width))
    for row in matrix[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)
