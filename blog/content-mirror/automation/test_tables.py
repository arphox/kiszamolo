"""Focused tests for the span/color-aware Markdown table renderer."""

import unittest

from bs4 import BeautifulSoup, Tag

from tables import table_markdown


def plain_cell(tag: Tag) -> str:
    return tag.get_text(" ", strip=True)


class TableMarkdownTests(unittest.TestCase):
    def render(self, source: str) -> tuple[str | None, Tag]:
        soup = BeautifulSoup(source, "html.parser")
        table = soup.find("table")
        assert isinstance(table, Tag)
        return table_markdown(table, plain_cell), table

    def test_expands_colspan_without_losing_header_values(self) -> None:
        result, _ = self.render(
            """<table><tr><td rowspan="2">Gyermek</td><td colspan="2">Hitelösszeg</td></tr>
            <tr><td>CSOK</td><td>Piaci</td></tr><tr><td>1</td><td>15M</td><td>10M</td></tr></table>"""
        )
        self.assertEqual(
            result,
            "| Gyermek | Hitelösszeg | Hitelösszeg |\n| --- | --- | --- |\n"
            "| Gyermek | CSOK | Piaci |\n| 1 | 15M | 10M |",
        )

    def test_expands_rowspan_and_colspan_into_rectangular_grid(self) -> None:
        result, _ = self.render(
            """<table><tr><td rowspan="2">A</td><td>B</td><td colspan="2">C</td></tr>
            <tr><td>D</td><td>E</td></tr><tr><td>F</td><td>G</td><td>H</td><td>I</td></tr></table>"""
        )
        self.assertEqual(
            result,
            "| A | B | C | C |\n| --- | --- | --- | --- |\n"
            "| A | D | E |  |\n| F | G | H | I |",
        )

    def test_color_only_cells_are_labeled_and_cell_fill_wins(self) -> None:
        result, _ = self.render(
            """<table bgcolor="#aaaaaa"><tr><th>Hónap</th><th>Érték</th></tr>
            <tr><td>Jan</td><td bgcolor="#c45911" style="background: #FDF3ED"></td></tr>
            <tr><td>Feb</td><td style="background-color:#b4c6e7"></td></tr></table>"""
        )
        self.assertIn("[szín: #FDF3ED]", result or "")
        self.assertIn("[szín: #b4c6e7]", result or "")
        self.assertNotIn("#aaaaaa", result or "")

    def test_nonempty_colored_values_keep_their_color_when_table_is_converted(self) -> None:
        result, _ = self.render(
            '<table><tr><th>Hónap</th><th>Érték</th></tr>'
            '<tr><td>Jan</td><td bgcolor="#c45911"></td></tr>'
            '<tr><td>Feb</td><td bgcolor="#c45911">80%</td></tr></table>'
        )
        self.assertIn("[szín: #c45911]", result or "")
        self.assertIn("80% [szín: #c45911]", result or "")

        decorative, _ = self.render(
            '<table><tr><td bgcolor="#c45911">Kiemelt</td><td>Érték</td></tr></table>'
        )
        self.assertIsNone(decorative)

    def test_color_shorthand_respects_declaration_order(self) -> None:
        first, _ = self.render(
            '<table><tr><td bgcolor="#111">'
            '<span style="display:none"></span></td></tr></table>'
        )
        # The blank cell is colored by its own bgcolor, so this is a color table.
        self.assertIn("[szín: #111]", first or "")

        result, _ = self.render(
            '<table><tr><td bgcolor="#111" style="background: #222; background-color: #333"></td></tr></table>'
        )
        self.assertIn("[szín: #333]", result or "")

    def test_unsupported_effective_background_falls_back_to_html(self) -> None:
        rgb, _ = self.render(
            '<table><tr><td colspan="2" style="background: rgb(255, 0, 0)"></td></tr></table>'
        )
        child_rgb_over_table_hex, _ = self.render(
            '<table bgcolor="#aaaaaa"><tr><td colspan="2" style="background-color: rgb(255, 0, 0)"></td></tr></table>'
        )
        self.assertIsNone(rgb)
        self.assertIsNone(child_rgb_over_table_hex)

    def test_preserves_caption_and_escapes_pipes_and_multiline_text(self) -> None:
        result, _ = self.render(
            """<table><caption>Összehasonlítás</caption><tr><td rowspan="2">A |
            B</td><td>C</td></tr><tr><td>D</td></tr></table>"""
        )
        self.assertTrue((result or "").startswith("**Összehasonlítás**\n\n"))
        self.assertIn(r"A \| B", result or "")
        self.assertNotIn("\n            ", result or "")

    def test_nested_or_malformed_tables_fall_back(self) -> None:
        nested, _ = self.render(
            '<table><tr><td rowspan="2">A<table><tr><td>B</td></tr></table></td></tr></table>'
        )
        bad_span, _ = self.render('<table><tr><td colspan="nope">A</td></tr></table>')
        overrun, _ = self.render('<table><tr><td rowspan="3">A</td></tr><tr><td>B</td></tr></table>')
        overlap, _ = self.render(
            '<table><tr><td rowspan="2">A</td><td>B</td><td rowspan="2">C</td></tr>'
            '<tr><td colspan="2">Ambiguous</td></tr></table>'
        )
        self.assertIsNone(nested)
        self.assertIsNone(bad_span)
        self.assertIsNone(overrun)
        self.assertIsNone(overlap)

    def test_structural_anchors_outside_cells_fall_back(self) -> None:
        result, _ = self.render(
            '<table><tr><td colspan="2"><a href="#cell">Go</a></td></tr>'
            '<tr><a id="cell"></a><td>Target</td><td>X</td></tr></table>'
        )
        self.assertIsNone(result)

    def test_preformatted_code_and_lists_fall_back(self) -> None:
        preformatted, _ = self.render(
            '<table><tr><td rowspan="2"><pre>x &lt; 3\ny = 4</pre></td></tr><tr><td>Value</td></tr></table>'
        )
        listed, _ = self.render(
            '<table><tr><td colspan="2"><ul><li>First</li><li>Second</li></ul></td></tr>'
            '<tr><td>A</td><td>B</td></tr></table>'
        )
        self.assertIsNone(preformatted)
        self.assertIsNone(listed)

    def test_simple_table_stays_on_existing_conversion_path(self) -> None:
        result, _ = self.render("<table><tr><td>A</td><td>B</td></tr></table>")
        self.assertIsNone(result)

    def test_original_dom_is_not_mutated(self) -> None:
        result, table = self.render(
            '<table><tr><td rowspan="2">A</td><td colspan="2">B</td></tr>'
            '<tr><td bgcolor="#abc"></td><td>C</td></tr></table>'
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(table.find_all("td")), 4)
        self.assertEqual(table.find("td").get("rowspan"), "2")

    def test_color_only_legend_like_table_is_not_blank(self) -> None:
        result, _ = self.render(
            '<table bgcolor="#f7caac"><tr><td bgcolor="#c45911" style="background: #c45911"></td></tr></table>'
        )
        self.assertEqual(result, "| [szín: #c45911] |\n| --- |")


if __name__ == "__main__":
    unittest.main()
