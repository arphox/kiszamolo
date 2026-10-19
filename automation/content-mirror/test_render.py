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

    def test_srcset_discovery_and_rewriting_handle_the_same_candidates(self):
        first_url = "https://kiszamolo.hu/wp/a,one.jpg"
        second_url = "https://kiszamolo.hu/wp/b.jpg"
        asset_paths = {first_url: "../../media/a.jpg", second_url: "../../media/b.jpg"}
        cases = [
            ("", set(), ""),
            (" \t, , ", set(), ""),
            (
                "/wp/a,one.jpg 1x, /wp/b.jpg 2x",
                {first_url, second_url},
                "../../media/a.jpg 1x, ../../media/b.jpg 2x",
            ),
            (
                "/wp/a,one.jpg, /wp/b.jpg,",
                {first_url, second_url},
                "../../media/a.jpg, ../../media/b.jpg",
            ),
            (
                "/wp/a,one.jpg 320w,\t/wp/b.jpg 640w,",
                {first_url, second_url},
                "../../media/a.jpg 320w, ../../media/b.jpg 640w",
            ),
            (
                "data:image/png;base64,AAAA 1x, /wp/b.jpg 2x",
                {second_url},
                "data:image/png;base64,AAAA 1x, ../../media/b.jpg 2x",
            ),
        ]
        for value, expected_assets, expected_srcset in cases:
            with self.subTest(srcset=value):
                content = f'<source srcset="{value}">'
                self.assertEqual(discover_assets(content, BASE), expected_assets)
                rewritten = rewrite_html(content, BASE, asset_paths, {})
                source = BeautifulSoup(rewritten, "html.parser").find("source")
                self.assertEqual(source["srcset"], expected_srcset)

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
        self.assertIn("| Összevont | Összevont |", markdown)
        self.assertIn("&lt;p&gt;szó szerinti jelölés&lt;/p&gt;", markdown)
        self.assertNotIn("alert(", markdown)
        warnings = conversion_warnings(body)
        self.assertTrue(any("Merged table cells" in warning for warning in warnings))
        self.assertTrue(any("omitted" in warning for warning in warnings))

    def test_plain_title_strips_markup_and_decodes_entities(self):
        self.assertEqual(plain_title("<span>A &amp; B</span>"), "A & B")

    def test_markdown_preserves_literal_characters_in_code(self):
        content = "x &lt; 3 &amp;&amp; a_b * c &amp;lt;"
        expected = "x < 3 && a_b * c &lt;"
        for source, snippet in (
            (f"<p><code>{content}</code></p>", f"`{expected}`"),
            (f"<pre><code>{content}</code></pre>", f"```\n{expected}\n```"),
            (f"<pre>{content}</pre>", f"```\n{expected}\n```"),
        ):
            with self.subTest(source=source):
                self.assertIn(snippet, make_markdown({"title": "Test"}, source))

    def test_markdown_preserves_text_and_attributes_in_retained_html(self):
        content = "a_b * c &lt;b&gt; &amp;copy;"
        cases = (
            ("table", f"<table><tr><td><table><tr><td>{content}</td></tr></table></td></tr></table>"),
        )
        for tag, source in cases:
            with self.subTest(source=source):
                markdown = make_markdown({"title": "Test"}, source)
                retained = BeautifulSoup(markdown, "html.parser").find(tag)
                self.assertIsNotNone(retained)
                self.assertEqual(str(retained), str(BeautifulSoup(source, "html.parser").find(tag)))

    def test_simplified_tables_and_media_preserve_literal_text(self):
        content = "a_b * c &lt;b&gt; &amp;copy;"
        expected = r"a\_b \* c &lt;b&gt; &amp;copy;"
        for source in (
            f'<table><tr><td colspan="2">{content}</td></tr></table>',
            f'<picture><img src="a_b.png" alt="Ábra">{content}</picture>',
            f'<video src="a_b.mp4">{content}</video>',
        ):
            with self.subTest(source=source):
                markdown = make_markdown({"title": "Test"}, source)
                self.assertIn(expected, markdown)
        self.assertIn("![Ábra](a_b.png)", make_markdown({"title": "Test"},
            '<picture><source srcset="a-large.png 2x"><img src="a_b.png" alt="Ábra"></picture>'))

    def test_superscripts_preserve_formulas_without_rewriting_footnotes(self):
        source = ('<p>(1,06<sup>4</sup>), (1+g)<sup>n</sup>, m<sup>2</sup>, '
                  'szöveg<sup>*</sup>, <a href="#note"><sup>[1]</sup></a>, '
                  '3<sup>rd </sup>party</p><pre>a<sup>2</sup></pre>')
        markdown = make_markdown({"title": "Test"}, source)
        for expected in ("(1,06^(4))", "(1+g)^(n)", "m^(2)", r"szöveg\*", "[[1]](#note)",
                         "3rd party", "```\na2\n```"):
            self.assertIn(expected, markdown)

    def test_subscripts_remain_distinct_from_baseline_text(self):
        markdown = make_markdown({"title": "Test"}, '<p>CO<sub>2</sub> és x<sub>n+1</sub></p>')
        self.assertIn(r"CO\_(2) és x\_(n+1)", markdown)

    def test_markdown_encodes_destinations_without_double_encoding(self):
        source = ('<a href="mailto:person@example.test?subject=Jelentkezés tanácsadásra">Levél</a>'
                  '<a href="../../C%C3%ADm%20%28friss%29.md#resz">Helyi</a>'
                  '<a href="https://example.test/a)b?value=%2F">Külső</a>'
                  '<a href="https://münich.example/út (1)">Nemzetközi</a>'
                  '<a href="https://[broken/path">Hibás</a>'
                  '<a href="HTTPS://example.test/path?#">Üres URL-részek</a>'
                  '<img src="képek/ábra (1).png" alt="Ábra">')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn('[Levél](mailto:person@example.test?subject=Jelentkez%C3%A9s%20tan%C3%A1csad%C3%A1sra)', markdown)
        self.assertIn('[Helyi](../../C%C3%ADm%20%28friss%29.md#resz)', markdown)
        self.assertIn('[Külső](https://example.test/a%29b?value=%2F)', markdown)
        self.assertIn('[Nemzetközi](https://münich.example/%C3%BAt%20%281%29)', markdown)
        self.assertIn('[Hibás](https://[broken/path)', markdown)
        self.assertIn('[Üres URL-részek](HTTPS://example.test/path?#)', markdown)
        self.assertIn('![Ábra](k%C3%A9pek/%C3%A1bra%20%281%29.png)', markdown)

    def test_video_embeds_become_links_without_editor_bookmarks(self):
        source = ('<iframe src="https://www.youtube.com/embed/abc?start=30" title="YouTube video player">'
                  '&amp;lt;span data-mce-type="bookmark" class="mce_SELRES_start"&amp;gt;'
                  '\ufeff&amp;lt;/span&amp;gt;</iframe><p>A szerző képaláírása.</p>'
                  '<span data-mce-type="bookmark">\ufeff</span>'
                  '<span data-mce-type="bookmark">Megőrzendő szöveg.</span>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn('[YouTube-videó](https://www.youtube.com/embed/abc?start=30)', markdown)
        self.assertIn('A szerző képaláírása.', markdown)
        self.assertIn('Megőrzendő szöveg.', markdown)
        self.assertNotIn('\ufeff', markdown)
        for noise in ('<iframe', 'mce_SELRES', 'bookmark', 'width='):
            self.assertNotIn(noise, markdown)

    def test_iframe_preserves_title_fallback_and_facebook_destination(self):
        source = ('<iframe src="https://example.test/calculator" title="Hitel [2024]">'
                  'Ha nem látszik, keresd fel az oldalt.</iframe>'
                  '<iframe src="https://[broken/path">Hibás beágyazás.</iframe>'
                  '<iframe src="https://www.facebook.com/plugins/video.php?href=https%3A%2F%2Fwww.facebook.com%2Fpage%2Fvideos%2F123%2F&amp;width=560"></iframe>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn(r'[Hitel \[2024\]](https://example.test/calculator)', markdown)
        self.assertIn('Ha nem látszik, keresd fel az oldalt.', markdown)
        self.assertIn('[Beágyazott tartalom](https://[broken/path)', markdown)
        self.assertIn('Hibás beágyazás.', markdown)
        self.assertIn('[Facebook-videó](https://www.facebook.com/page/videos/123/)', markdown)

    def test_iframe_fallback_removes_encoded_editor_paragraph_debris(self):
        source = ('<iframe src="https://example.test/calculator">'
                  'Nem sikerült megjeleníteni.&amp;amp;nbsp;&amp;lt;/p&amp;gt;'
                  '\n&amp;lt;p&amp;gt;&amp;amp;nbsp;&amp;lt;/p&amp;gt;'
                  '\n&amp;lt;p&amp;gt;</iframe>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn('Nem sikerült megjeleníteni.', markdown)
        self.assertIn('[Beágyazott tartalom](https://example.test/calculator)', markdown)
        for noise in ('nbsp;', '&amp;', '&lt;', '<p>'):
            self.assertNotIn(noise, markdown)

    def test_media_links_keep_all_sources_poster_and_fallback(self):
        source = ('<video poster="poster.png"><source src="clip.mp4"><source src="clip.webm">'
                  'Videó leírása.</video><audio src="sound.mp3">Hang leírása.</audio>'
                  '<object data="report.pdf">Dokumentum leírása.</object>')
        markdown = make_markdown({"title": "Test"}, source)
        for expected in ('[Videó](clip.mp4)', '[Videó](clip.webm)', '![Videó előnézeti képe](poster.png)',
                         '[Hangfelvétel](sound.mp3)', '[Beágyazott fájl](report.pdf)',
                         'Videó leírása.', 'Hang leírása.', 'Dokumentum leírása.'):
            self.assertIn(expected, markdown)

    def test_technical_anchors_are_removed_unless_referenced(self):
        source = ('<h2 id="headline-38-120338">Cím</h2><span id="span-39-120338">Szöveg</span>'
                  '<a id="more-12"></a><a id="image-2-3"><img src="a.png"></a>'
                  '<a id="custom-section"></a><h2 id="headline-40-120338">Másik cím</h2>'
                  '<a href="#headline-40-120338">Ugrás</a>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertNotIn('headline-38-120338', markdown)
        self.assertNotIn('span-39-120338', markdown)
        self.assertNotIn('more-12', markdown)
        self.assertIn('![](a.png)', markdown)
        self.assertIn('<a id="custom-section"></a>', markdown)
        self.assertEqual(markdown.count('<a id="headline-40-120338"></a>'), 1)
        self.assertIn('[Ugrás](#headline-40-120338)', markdown)
        referenced = make_markdown({"title": "Test"}, source, retained_anchors={'headline-38-120338'})
        self.assertIn('<a id="headline-38-120338"></a>', referenced)

    def test_complex_table_and_color_legend_keep_values_and_meaning(self):
        source = ('<table><tr><th rowspan="2">Gyermek</th><th colspan="2">Támogatás</th></tr>'
                  '<tr><th>Vásárlás</th><th>Bővítés</th></tr><tr><td>1</td><td>500.000 Ft</td>'
                  '<td>500.000 Ft</td></tr></table><dl><dt><table><tr>'
                  '<td bgcolor="#c45911"></td></tr></table></dt><dd>Kedvezőbb árazás</dd></dl>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertNotIn('<table', markdown)
        self.assertIn('| Gyermek | Támogatás | Támogatás |', markdown)
        self.assertIn('| Gyermek | Vásárlás | Bővítés |', markdown)
        self.assertIn('| 1 | 500.000 Ft | 500.000 Ft |', markdown)
        self.assertIn('[szín: #c45911]', markdown)
        self.assertIn('Kedvezőbb árazás', markdown)

    def test_unsupported_tables_preserve_referenced_anchors_and_code(self):
        source = ('<table><tr><td colspan="2"><a href="#cell">Ugrás</a></td></tr>'
                  '<tr><td id="cell">Célpont</td><td>X</td></tr></table>'
                  '<table><tr><td colspan="2"><pre>foo\nbar</pre></td></tr></table>'
                  '<table><tr><td style="background: rgb(255, 0, 0)"></td></tr></table>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn('<a id="cell"></a>', markdown)
        self.assertIn('href="#cell"', markdown)
        self.assertIn('<pre>foo\nbar</pre>', markdown)
        self.assertIn('background: rgb(255, 0, 0)', markdown)

    def test_rebuild_refreshes_conversion_notes_and_preserves_acquisition_notes(self):
        notes = conversion_warnings('<iframe src="video"></iframe>', previous=[
            'Embedded media is retained as HTML in Markdown.',
            'Complex tables are retained as HTML in Markdown.',
            'Protected content is not available through the public API.',
        ])
        self.assertEqual(notes, [
            'Protected content is not available through the public API.',
            'Embedded media is represented by links or images in Markdown.',
        ])

    def test_markdown_escapes_literal_markup_and_entities_in_normal_text(self):
        content = "&lt;b&gt;literal&lt;/b&gt; *star* _underscore_ &amp;copy; &amp;lt; &amp;#60;"
        expected = r"&lt;b&gt;literal&lt;/b&gt; \*star\* \_underscore\_ &amp;copy; &amp;lt; &amp;#60;"
        for source in (
            f"<p>{content}</p>",
            f"<table><tr><th>Text</th></tr><tr><td>{content}</td></tr></table>",
            f'<a href="https://example.test/?a=1&amp;b=2">{content}</a>',
        ):
            with self.subTest(source=source):
                markdown = make_markdown({"title": "Test"}, source)
                self.assertIn(expected, markdown)
                if source.startswith("<table"):
                    self.assertIn(f"| {expected} |", markdown)
                elif source.startswith("<a"):
                    self.assertIn(f"[{expected}](https://example.test/?a=1&b=2)", markdown)

    def test_markdown_preserves_private_use_characters_in_source(self):
        text = "\ue000A\ue001 \ue000B\ue001 \ue000C\ue001 \ue000D\ue001"
        self.assertIn(text, make_markdown({"title": "Test"}, f"<p>{text}</p>"))

    def test_url_attributes_are_decoded_only_by_the_html_parser(self):
        url = "https://example.test/report.pdf?docid=A&timeshift=20160101&lt1=x&REGION=y#part&copy;"
        encoded = url.replace("&", "&amp;")
        source = f'<a href="{encoded}">Report</a><img src="{encoded}"><iframe src="{encoded}"></iframe>'
        self.assertEqual(discover_assets(source, BASE), {url.split("#")[0]})
        self.assertEqual(discover_embeds(source, BASE), {url.split("#")[0]})
        rewritten = rewrite_html(source, BASE, {}, {})
        soup = BeautifulSoup(rewritten, "html.parser")
        self.assertEqual(soup.a["href"], url)
        self.assertEqual(soup.img["src"], url)
        self.assertEqual(soup.iframe["src"], url)
        self.assertIn(f"[Report]({url})", make_markdown({"title": "Test"}, rewritten))
        mapped = BeautifulSoup(rewrite_html(source, BASE,
            {url.split("#")[0]: "assets/report.pdf"}, {}), "html.parser")
        self.assertEqual(mapped.a["href"], "assets/report.pdf#part&copy;")
        self.assertEqual(mapped.img["src"], "assets/report.pdf#part&copy;")

    def test_inline_formatting_preserves_whitespace_only_and_nbsp_boundaries(self):
        for tag, marker in (("strong", "**"), ("b", "**"), ("em", "*"),
                            ("i", "*"), ("del", "~~"), ("s", "~~")):
            for space in (" ", "\u00a0", "\u202f"):
                with self.subTest(tag=tag, space=repr(space)):
                    source = (f'<p>a<{tag}>{space}</{tag}>b; '
                              f'a<{tag}>{space}b{space}</{tag}>c</p>')
                    markdown = make_markdown({"title": "Test"}, source)
                    self.assertIn(f"a{space}b; a{space}{marker}b{marker}{space}c", markdown)

    def test_link_boundaries_preserve_spaces_and_line_breaks(self):
        source = ('<p>az<a href="a">&nbsp;<strong>Akadémiára</strong></a>; '
                  'a<a href="b">&nbsp;</a>b; '
                  '<a href="c">Első<br></a><a href="d">Második</a></p>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn('az\u00a0[**Akadémiára**](a)', markdown)
        self.assertIn('a\u00a0b', markdown)
        self.assertIn('[Első](c)  \n[Második](d)', markdown)

    def test_paragraph_numbers_stay_literal_and_real_lists_keep_numbering(self):
        source = ('<p>20. Kanada</p><p>19. Egyiptom<br>18) Anglia</p>'
                  '<ol start="4"><li>Valódi lista</li><li>Folytatás</li></ol>'
                  '<pre>20. Kód</pre>')
        markdown = make_markdown({"title": "Test"}, source)
        for literal in (r"20\. Kanada", r"19\. Egyiptom", r"18\) Anglia"):
            self.assertIn(literal, markdown)
        self.assertIn("4. Valódi lista\n5. Folytatás", markdown)
        self.assertIn("```\n20. Kód\n```", markdown)

    def test_adjacent_equivalent_emphasis_shares_delimiters(self):
        for first, second, marker in (("em", "em", "*"), ("i", "em", "*"),
                                      ("strong", "b", "**"), ("del", "s", "~~")):
            with self.subTest(first=first, second=second):
                markdown = make_markdown({"title": "Test"},
                    f"<p>(<{first}>E</{first}><{second}>bben</{second}>)</p>")
                self.assertIn(f"({marker}Ebben{marker})", markdown)
        nested = make_markdown({"title": "Test"}, '<p><em><b>E</b></em><em><b>bben</b></em></p>')
        self.assertIn("***Ebben***", nested)
        for span in ("", ' colspan="2"'):
            with self.subTest(span=span):
                table = make_markdown({"title": "Test"},
                    f'<table><tr><td{span}><em>E</em><em>bben</em></td></tr></table>')
                self.assertIn("| *Ebben* |", table)

    def test_inline_normalization_keeps_code_anchors_and_retained_html(self):
        source = ('<pre><em>E</em><em>bben</em> a<strong>&nbsp;</strong>b</pre>'
                  '<p><em id="one">Első</em><em id="two">Második</em></p>'
                  '<table><tr><td><table><tr><td><em>E</em><em>bben</em>'
                  '</td></tr></table></td></tr></table>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn("```\nEbben a\u00a0b\n```", markdown)
        self.assertIn('<a id="one"></a>', markdown)
        self.assertIn('<a id="two"></a>', markdown)
        retained = BeautifulSoup(markdown, "html.parser").find("table")
        self.assertEqual(str(retained), str(BeautifulSoup(source, "html.parser").find("table")))

    def test_adjacent_emphasis_keeps_ordered_list_and_nested_list_context(self):
        source = ('<ol start="4"><li><em>E</em><em>bben</em></li>'
                  '<li><strong>Má</strong><b>sodik</b>'
                  '<ul><li><i>Bel</i><em>ső</em></li></ul></li></ol>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn("4. *Ebben*\n5. **Második**", markdown)
        self.assertIn("\n   - *Belső*", markdown)

    def test_link_only_emphasis_stays_inside_the_label_at_word_boundaries(self):
        cases = (
            ('<em><strong><a href="a">tanulmány</a></strong></em>ok', '[***tanulmány***](a)ok'),
            ('elő<strong><em><a href="a">tag</a></em></strong>utó', 'elő[***tag***](a)utó'),
            ('i<em><a href="a">tt</a></em>', 'i[*tt*](a)'),
            ('<b><a href="a">kiemelt</a></b>en', '[**kiemelt**](a)en'),
            ('<s><a href="a">törölt</a></s>szöveg', '[~~törölt~~](a)szöveg'),
            ('<em> <b><a href="a">szó</a></b> </em>utó', ' [***szó***](a) utó'),
            ('<em><a href="a">vegyes <strong>címke</strong></a></em>utó',
             '[*vegyes **címke***](a)utó'),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                markdown = make_markdown({"title": "Test"}, f'<p>X {source}.</p>')
                self.assertIn(expected, markdown)

    def test_link_only_emphasis_preserves_destinations_titles_and_list_context(self):
        source = ('<ol start="4"><li>Első</li><li><em id="label"><b>'
                  '<a href="képek/ábra (1).md#resz" title="Cím">Második</a>'
                  '</b></em>szó</li></ol>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn('5. <a id="label"></a>[***Második***]('
                      'k%C3%A9pek/%C3%A1bra%20%281%29.md#resz "Cím")szó', markdown)

    def test_link_emphasis_does_not_capture_neighboring_unlinked_text(self):
        source = ('<p><em>előtte <a href="a">link</a> utána</em>; '
                  '<strong><a href="a">első</a> és <a href="b">második</a></strong></p>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn('*előtte [link](a) utána*', markdown)
        self.assertIn('**[első](a) és [második](b)**', markdown)

    def test_link_emphasis_normalization_keeps_code_and_retained_html(self):
        source = ('<pre><em><b><a href="a">szó</a></b></em>utó</pre>'
                  '<table><tr><td><table><tr><td><em><strong>'
                  '<a href="a">szó</a></strong></em>utó'
                  '</td></tr></table></td></tr></table>')
        markdown = make_markdown({"title": "Test"}, source)
        self.assertIn('```\nszóutó\n```', markdown)
        retained = BeautifulSoup(markdown, "html.parser").find("table")
        self.assertEqual(str(retained), str(BeautifulSoup(source, "html.parser").find("table")))
        for span in ("", ' colspan="2"'):
            table = make_markdown({"title": "Test"},
                f'<table><tr><td{span}><em><a href="a">szó</a></em>utó</td></tr></table>')
            self.assertIn('| [*szó*](a)utó |', table)


if __name__ == "__main__":
    unittest.main()
