import unittest

from bs4 import BeautifulSoup

from render import (
    conversion_warnings,
    discover_assets,
    discover_embeds,
    extract_public_content,
    make_markdown,
    plain_title,
    rewrite_html,
)


BASE = "https://kiszamolo.hu/cikk/pelda/"


class RenderTests(unittest.TestCase):
    def test_extract_public_content_selects_page_container_without_site_chrome(self):
        page = b"""
        <html><body><nav>navigation</nav><div class="ct-inner-content" id="inner_content-3-228">
          <h1>Calculator</h1><p>Public page content.</p></div><footer>footer links</footer></body></html>
        """
        extracted = extract_public_content(page, {"type": "page", "title": {"rendered": "Calculator"}})
        self.assertIsNotNone(extracted)
        soup = BeautifulSoup(extracted, "html.parser")
        self.assertEqual(soup.select_one(".ct-inner-content")["id"], "inner_content-3-228")
        self.assertIsNone(soup.find("h1"))
        self.assertIn("Public page content.", extracted)
        self.assertNotIn("navigation", extracted)
        self.assertNotIn("footer links", extracted)

    def test_extract_public_content_returns_none_for_unknown_layout(self):
        self.assertIsNone(
            extract_public_content(
                "<html><body><nav>navigation</nav><div class='random-widget'>unknown</div></body></html>",
                {"type": "page", "title": "Unknown"},
            )
        )

    def test_asset_discovery_covers_media_srcset_and_documents(self):
        content = """
        <picture><source srcset="/wp/a,one.jpg 1x, /wp/a2.jpg 2x">
          <img src="//cdn.example/image.webp#preview" alt="ábra"></picture>
        <video poster="poster.png"><source src="clip.mp4"></video>
        <audio src="sound.ogg"></audio><object data="report.pdf"></object>
        <embed src="archive.zip"><a href="/files/table.xlsx?download=1">tábla</a>
        <a href="/another-post/">cikk</a><iframe src="https://video.example/watch"></iframe>
        <img src="javascript:alert(1)"><img src="data:image/png;base64,AAAA">
        """
        self.assertEqual(
            discover_assets(content, BASE),
            {
                "https://kiszamolo.hu/wp/a,one.jpg",
                "https://kiszamolo.hu/wp/a2.jpg",
                "https://cdn.example/image.webp",
                "https://kiszamolo.hu/cikk/pelda/poster.png",
                "https://kiszamolo.hu/cikk/pelda/clip.mp4",
                "https://kiszamolo.hu/cikk/pelda/sound.ogg",
                "https://kiszamolo.hu/cikk/pelda/report.pdf",
                "https://kiszamolo.hu/cikk/pelda/archive.zip",
                "https://kiszamolo.hu/files/table.xlsx?download=1",
            },
        )
        self.assertEqual(
            discover_embeds(content, BASE),
            {"https://video.example/watch"},
        )

    def test_rewrite_uses_local_paths_and_preserves_fragments_and_unknowns(self):
        content = (
            '<p><a href="https://kiszamolo.hu/masik-cikk/#resz">belső</a> '
            '<a href="https://example.com/kulso#frag">külső</a></p>'
            '<img src="/wp-content/a.png?size=large" srcset="/wp/a.jpg 1x, /wp/b.jpg 2x">'
            '<iframe src="https://video.example/watch"></iframe>'
        )
        rendered = rewrite_html(
            content,
            BASE,
            {
                "https://kiszamolo.hu/wp-content/a.png?size=large": "../../media/a.png",
                "https://kiszamolo.hu/wp/a.jpg": "../../media/a.jpg",
                "https://kiszamolo.hu/wp/b.jpg": "../../media/b.jpg",
            },
            {"https://kiszamolo.hu/masik-cikk/": "../masik-cikk.md"},
        )
        self.assertIn('href="../masik-cikk.md#resz"', rendered)
        self.assertIn('href="https://example.com/kulso#frag"', rendered)
        self.assertIn('src="../../media/a.png"', rendered)
        self.assertIn('srcset="../../media/a.jpg 1x, ../../media/b.jpg 2x"', rendered)
        self.assertIn('src="https://video.example/watch"', rendered)

    def test_iframe_assets_embeds_and_lazy_image_references_are_rewritten(self):
        content = (
            '<iframe src="/files/report.pdf"></iframe>'
            '<iframe src="https://kiszamolo.hu/cikk/pelda/embed/"></iframe>'
            '<img data-src="/images/lazy.jpg" data-srcset="/images/small.jpg 1x, /images/large.jpg 2x">'
        )
        self.assertEqual(
            discover_assets(content, BASE),
            {
                "https://kiszamolo.hu/files/report.pdf",
                "https://kiszamolo.hu/images/lazy.jpg",
                "https://kiszamolo.hu/images/small.jpg",
                "https://kiszamolo.hu/images/large.jpg",
            },
        )
        rendered = rewrite_html(
            content,
            BASE,
            {
                "https://kiszamolo.hu/files/report.pdf": "../../media/report.pdf",
                "https://kiszamolo.hu/images/lazy.jpg": "../../media/lazy.jpg",
                "https://kiszamolo.hu/images/small.jpg": "../../media/small.jpg",
                "https://kiszamolo.hu/images/large.jpg": "../../media/large.jpg",
            },
            {},
            {"https://kiszamolo.hu/cikk/pelda/embed/": "../cikk.html"},
        )
        self.assertIn('src="../../media/report.pdf"', rendered)
        self.assertIn('src="../cikk.html"', rendered)
        self.assertIn('data-src="../../media/lazy.jpg"', rendered)
        self.assertIn('data-srcset="../../media/small.jpg 1x, ../../media/large.jpg 2x"', rendered)

    def test_lazy_only_image_is_promoted_for_markdown(self):
        source = '<img alt="Ábra" data-src="/images/figure.png" data-srcset="/images/figure.png 1x">'
        rewritten = rewrite_html(
            source,
            BASE,
            {"https://kiszamolo.hu/images/figure.png": "../../media/figure.png"},
            {},
        )
        self.assertIn('src="../../media/figure.png"', rewritten)
        self.assertIn('srcset="../../media/figure.png 1x"', rewritten)
        self.assertIn('data-src="../../media/figure.png"', rewritten)
        markdown = make_markdown({"title": "Cikk"}, rewritten)
        self.assertIn("![Ábra](../../media/figure.png)", markdown)

    def test_markdown_omits_html_and_word_conditional_comments(self):
        content = """
        <p>Megmaradó bekezdés.</p><!--more-->
        <!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View></w:WordDocument></xml>
        <style>body { display: none; } .hidden { behavior: url(evil.css); }</style><![endif]-->
        <!--nextpage--><!-- /wp:pagebreak -->
        """
        markdown = make_markdown({"title": "Minta"}, content)
        self.assertIn("Megmaradó bekezdés.", markdown)
        for hidden_marker in ("more", "mso 9", "WordDocument", "display: none", "nextpage", "pagebreak"):
            self.assertNotIn(hidden_marker, markdown)

    def test_markdown_retains_readable_structure_links_nbsp_and_complex_tables(self):
        record = {
            "id": 55,
            "type": "post",
            "title": {"rendered": "Pénzügy <em>egyszerűen</em>"},
            "link": "https://kiszamolo.hu/pelda/",
            "date": "2026-01-02T03:04:05",
            "date_gmt": "2026-01-02T02:04:05",
            "modified": "2026-01-03T03:04:05",
            "category_names": ["Megtakarítás", "Kezdőknek"],
            "tag_names": ["adózás", "tervezés"],
        }
        body = """
        <p>Első&nbsp;bekezdés <strong>kiemelten</strong>, <a href="helyi.md">hivatkozással</a>.</p>
        <h2 id="resz">Lista</h2><p><a id="ures"></a><a href="#resz">ugrás</a></p>
        <p>Szó szerinti *csillag* és _aláhúzás_, kódban <code>a_b</code>.</p>
        <ol><li>egy</li><li>kettő</li></ol>
        <ol start="4"><li>négy</li><li>öt</li></ol>
        <table><thead><tr><th>Név</th><th>Érték</th></tr></thead>
          <tbody><tr><td>Alma</td><td>42</td></tr></tbody></table>
        <table><tr><th colspan="2">Összevont</th></tr><tr><td>A</td><td>B</td></tr></table>
        <p>&lt;p&gt;szó szerinti jelölés&lt;/p&gt;</p>
        <script>alert('ignored')</script>
        """
        markdown = make_markdown(record, body)
        self.assertIn('title: "Pénzügy egyszerűen"', markdown)
        self.assertIn('category_names: ["Megtakarítás", "Kezdőknek"]', markdown)
        self.assertIn('tag_names: ["adózás", "tervezés"]', markdown)
        self.assertIn("Első bekezdés", markdown)
        self.assertIn("**kiemelten**", markdown)
        self.assertIn("[hivatkozással](helyi.md)", markdown)
        self.assertIn("## Lista", markdown)
        self.assertIn('<a id="resz"></a>', markdown)
        self.assertIn('<a id="ures"></a>', markdown)
        self.assertIn("[ugrás](#resz)", markdown)
        self.assertIn(r"\*csillag\*", markdown)
        self.assertIn(r"\_aláhúzás\_", markdown)
        self.assertIn("`a_b`", markdown)
        self.assertRegex(markdown, r"1\. egy\s+2\. kettő")
        self.assertRegex(markdown, r"4\. négy\s+5\. öt")
        self.assertIn("| Név | Érték |", markdown)
        self.assertIn("| Alma | 42 |", markdown)
        self.assertIn('<th colspan="2">Összevont</th>', markdown)
        self.assertIn("&lt;p&gt;szó szerinti jelölés&lt;/p&gt;", markdown)
        self.assertNotIn("alert(", markdown)
        warnings = conversion_warnings(body)
        self.assertTrue(any("Complex tables" in warning for warning in warnings))
        self.assertTrue(any("omitted" in warning for warning in warnings))

    def test_plain_title_strips_markup_and_decodes_entities(self):
        self.assertEqual(plain_title("<span>A &amp; B</span>"), "A & B")


if __name__ == "__main__":
    unittest.main()
