"""Offline tests for repairing links to moved local articles."""

import unittest

from article_links import repair_markdown_links
from render import make_markdown


class RepairMarkdownLinksTests(unittest.TestCase):
    def test_rewrites_relative_target_across_directories(self):
        source = "2025/04/ref.md"
        markdown = "[Régi cikk](../../2024/12/old.md)"
        result = repair_markdown_links(
            markdown, source, {"2024/12/old.md": "2026/05/new.md"})
        self.assertEqual(result, "[Régi cikk](../../2026/05/new.md)")

    def test_matches_decoded_hungarian_and_space_paths_preserving_query_fragment(self):
        source = "2025/01/ref.md"
        markdown = "[Rész](../../2024/%C3%A1rv%C3%ADz%20t%C5%B1r%C5%91.md?old=1#r%C3%A9sz)"
        result = repair_markdown_links(
            markdown, source,
            {"2024/árvíz tűrő.md": "2024/új cikk.md"})
        self.assertEqual(
            result,
            "[Rész](../../2024/%C3%BAj%20cikk.md?old=1#r%C3%A9sz)")

    def test_applies_name_swaps_simultaneously(self):
        source = "2025/01/ref.md"
        markdown = "[A](../../2024/a.md) [B](../../2024/b.md)"
        moves = {"2024/a.md": "2024/b.md", "2024/b.md": "2024/a.md"}
        self.assertEqual(
            repair_markdown_links(markdown, source, moves),
            "[A](../../2024/b.md) [B](../../2024/a.md)")

    def test_encodes_markdown_delimiters_in_moved_filename(self):
        source = "2025/04/ref.md"
        markdown = "[Cikk](../../2024/old.md)"
        result = repair_markdown_links(
            markdown, source, {"2024/old.md": "2026/new (round) [1].md"})
        self.assertEqual(result, "[Cikk](../../2026/new%20%28round%29%20%5B1%5D.md)")

    def test_literal_percent_escape_in_filename_is_not_decoded_in_moves(self):
        source = "2025/04/ref.md"
        markdown = "[Cikk](../../2024/literal%2520name.md)"
        result = repair_markdown_links(
            markdown, source,
            {"2024/literal%20name.md": "2026/literal%20new.md"})
        self.assertEqual(result, "[Cikk](../../2026/literal%2520new.md)")

    def test_preserves_angle_destination_and_title(self):
        source = "2025/04/ref.md"
        markdown = '[Cikk](<../../2024/old page.md> "cím")\n'
        result = repair_markdown_links(
            markdown, source, {"2024/old page.md": "2026/new page.md"})
        self.assertEqual(
            result,
            '[Cikk](<../../2026/new%20page.md> "cím")\n')

    def test_repairs_article_destination_of_a_linked_remote_image(self):
        markdown = '[![cover](https://media.test/cover.png)](../../2024/old.md "title")'
        result = repair_markdown_links(
            markdown, "2025/04/ref.md",
            {"2024/old.md": "2026/new.md"})
        self.assertEqual(
            result, '[![cover](https://media.test/cover.png)](../../2026/new.md "title")')

    def test_repairs_link_with_inline_code_in_label_preserving_code_contents(self):
        markdown = '[Some `code [example](../../2024/old.md)` link](../../2024/old.md)'
        self.assertEqual(
            repair_markdown_links(markdown, "2025/04/ref.md",
                                  {"2024/old.md": "2026/new.md"}),
            '[Some `code [example](../../2024/old.md)` link](../../2026/new.md)')

    def test_preserves_escaped_brackets_and_unmatched_destination_text(self):
        markdown = r'\[example](../../2024/old.md) ](../../2024/old.md)'
        self.assertEqual(
            repair_markdown_links(markdown, "2025/04/ref.md",
                                  {"2024/old.md": "2026/new.md"}),
            markdown)

    def test_rewrites_inline_html_href_and_src_without_touching_other_attributes(self):
        source = "2025/04/ref.md"
        markdown = ('Manual prose href="../../2024/old.md"\n'
                    '<a href="../../2024/old.md?x=1&amp;y=2#top">link</a>\n'
                    '<img src="../../2024/old.md"><img data-src="../../2024/old.md">')
        result = repair_markdown_links(
            markdown, source, {"2024/old.md": "2026/new.md"})
        self.assertEqual(
            result,
            'Manual prose href="../../2024/old.md"\n'
            '<a href="../../2026/new.md?x=1&amp;y=2#top">link</a>\n'
            '<img src="../../2026/new.md"><img data-src="../../2024/old.md">')

    def test_ignores_external_anchors_unmapped_media_and_unrelated_text(self):
        source = "2025/04/ref.md"
        markdown = (
            "Manual mention: ../../2024/old.md\n"
            "[external](https://example.test/2024/old.md) [anchor](#part) "
            "[media](../../2024/image.png) [other](../../2024/other.md)\n")
        result = repair_markdown_links(markdown, source, {"2024/old.md": "2026/new.md"})
        self.assertEqual(
            result,
            "Manual mention: ../../2024/old.md\n"
            "[external](https://example.test/2024/old.md) [anchor](#part) "
            "[media](../../2024/image.png) [other](../../2024/other.md)\n")

    def test_maps_only_explicit_remote_article_urls_to_local_articles(self):
        markdown = (
            "[Now local](https://kiszamolo.hu/new-article/?ref=abc#part) "
            "[Still external](https://kiszamolo.hu/other-article/?ref=abc#part)")
        result = repair_markdown_links(
            markdown,
            "2025/04/ref.md",
            {},
            remote_targets={"https://kiszamolo.hu/new-article/?ref=abc": "2026/new article.md"},
        )
        self.assertEqual(
            result,
            "[Now local](../../2026/new%20article.md#part) "
            "[Still external](https://kiszamolo.hu/other-article/?ref=abc#part)")

    def test_maps_deleted_article_links_back_to_canonical_url(self):
        markdown = (
            "[Removed](../../2024/gone.md#part) "
            "[With query](../../2024/gone.md?ref=local#section) "
            "[Other](../../2024/stays.md) "
            "[External](https://example.test/2024/gone.md)")
        result = repair_markdown_links(
            markdown,
            "2025/04/ref.md",
            {},
            deleted_targets={"2024/gone.md": "https://kiszamolo.hu/canonical/"},
        )
        self.assertEqual(
            result,
            "[Removed](https://kiszamolo.hu/canonical/#part) "
            "[With query](https://kiszamolo.hu/canonical/?ref=local#section) "
            "[Other](../../2024/stays.md) "
            "[External](https://example.test/2024/gone.md)")

    def test_preserves_inline_and_fenced_code_examples(self):
        source = "2025/04/ref.md"
        markdown = (
            "Inline ` [example](../../2024/old.md) ` stays.\n\n"
            "```markdown\n[example](../../2024/old.md)\n```\n\n"
            "[real](../../2024/old.md)\n")
        result = repair_markdown_links(markdown, source, {"2024/old.md": "2026/new.md"})
        self.assertEqual(
            result,
            "Inline ` [example](../../2024/old.md) ` stays.\n\n"
            "```markdown\n[example](../../2024/old.md)\n```\n\n"
            "[real](../../2026/new.md)\n")

    def test_preserves_indented_code_blocks_and_blank_continuations(self):
        source = "2025/04/ref.md"
        markdown = (
            "    [example](../../2024/old.md)\n\n"
            "    [continued](../../2024/old.md)\n"
            "After the code: [real](../../2024/old.md)\n")
        result = repair_markdown_links(markdown, source, {"2024/old.md": "2026/new.md"})
        self.assertEqual(
            result,
            "    [example](../../2024/old.md)\n\n"
            "    [continued](../../2024/old.md)\n"
            "After the code: [real](../../2026/new.md)\n")

    def test_rewrites_nested_list_links_with_indentation(self):
        source = "2025/04/ref.md"
        markdown = "- top\n  - middle\n    - [nested](../../2024/old.md)\n"
        result = repair_markdown_links(markdown, source, {"2024/old.md": "2026/new.md"})
        self.assertEqual(result, "- top\n  - middle\n    - [nested](../../2026/new.md)\n")

    def test_repairs_generated_nested_list_paragraphs_preserving_code(self):
        for tag in ("ul", "ol"):
            with self.subTest(tag=tag):
                markdown = make_markdown({"title": "Test"},
                    f'<{tag}><li>Top<{tag}><li>Nested'
                    '<pre><code>[example](../../2024/old.md)</code></pre>'
                    '<p>Continued <a href="../../2024/old.md">target</a></p>'
                    f'</li></{tag}></li></{tag}>')
                result = repair_markdown_links(markdown, "2025/01/ref.md",
                                               {"2024/old.md": "2026/new.md"})
                self.assertEqual(result, markdown.replace(
                    "[target](../../2024/old.md)", "[target](../../2026/new.md)"))

    def test_repairs_list_paragraph_after_indented_code(self):
        markdown = ("- top\n  - nested\n\n"
                    "        [example](../../2024/old.md)\n\n"
                    "    Continued [real](../../2024/old.md)\n")
        result = repair_markdown_links(markdown, "2025/01/ref.md",
                                       {"2024/old.md": "2026/new.md"})
        self.assertEqual(result, markdown.replace(
            "[real](../../2024/old.md)", "[real](../../2026/new.md)"))

    def test_preserves_parent_list_code_after_leaving_nested_list(self):
        for indent in (2, 3):
            with self.subTest(indent=indent):
                markdown = ("- top\n  - nested\n\n"
                            + " " * indent + "Parent again\n\n"
                            "      [example](../../2024/old.md)\n\n"
                            "  [real](../../2024/old.md)\n")
                result = repair_markdown_links(markdown, "2025/01/ref.md",
                                               {"2024/old.md": "2026/new.md"})
                self.assertEqual(result, markdown.replace(
                    "[real](../../2024/old.md)", "[real](../../2026/new.md)"))

    def test_preserves_fenced_code_in_nested_list(self):
        for fence in ("```", "~~~"):
            with self.subTest(fence=fence):
                markdown = (f"- top\n  - nested\n\n    {fence}markdown\n"
                            f"    [example](../../2024/old.md)\n    {fence}{fence[0]}\n\n"
                            "    Continued [real](../../2024/old.md)\n")
                result = repair_markdown_links(markdown, "2025/01/ref.md",
                                               {"2024/old.md": "2026/new.md"})
                self.assertEqual(result, markdown.replace(
                    "[real](../../2024/old.md)", "[real](../../2026/new.md)"))

    def test_preserves_code_after_list_marker_with_excess_padding(self):
        markdown = ("-     example\n\n      [example](../../2024/old.md)\n\n"
                    "  [real](../../2024/old.md)\n")
        result = repair_markdown_links(markdown, "2025/01/ref.md",
                                       {"2024/old.md": "2026/new.md"})
        self.assertEqual(result, markdown.replace(
            "[real](../../2024/old.md)", "[real](../../2026/new.md)"))

    def test_preserves_multiline_inline_code_with_exact_backtick_run(self):
        source = "2025/04/ref.md"
        markdown = "`example\n[x](../../2024/old.md)\n`\n[real](../../2024/old.md)\n"
        result = repair_markdown_links(markdown, source, {"2024/old.md": "2026/new.md"})
        self.assertEqual(
            result,
            "`example\n[x](../../2024/old.md)\n`\n[real](../../2026/new.md)\n")

    def test_empty_moves_returns_the_original_string(self):
        markdown = "[link](../../2024/old.md)"
        self.assertIs(repair_markdown_links(markdown, "2025/04/ref.md", {}), markdown)


if __name__ == "__main__":
    unittest.main()
