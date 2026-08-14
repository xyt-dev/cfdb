import unittest
from pathlib import Path

from editorial_model import EditorialDocument, Node
from editorial_parser import parse_blog_html
from editorial_render import (
    RenderError,
    render_editorial_html,
    sanitize_image_url,
    sanitize_link_url,
)


def fixture_document(*children: Node) -> EditorialDocument:
    return EditorialDocument(
        contest_id="1",
        source_url="https://codeforces.com/blog/entry/1",
        root=Node(kind="document", children=list(children)),
    )


class EditorialRenderTests(unittest.TestCase):
    def test_renders_nested_spoiler_and_preserves_heading_level(self):
        document = fixture_document(
            Node(
                kind="spoiler",
                attrs={"title": [Node(kind="text", text="Proof").to_dict()]},
                children=[Node(kind="heading", attrs={"level": 4}, children=[Node(kind="text", text="Case 1")])],
            )
        )
        self.assertEqual(
            render_editorial_html(document),
            '<details class="cf-spoiler"><summary>Proof</summary><h4>Case 1</h4></details>',
        )

    def test_spoiler_title_cannot_bypass_heading_validation(self):
        document = fixture_document(
            Node(
                kind="spoiler",
                attrs={"title": [Node(kind="heading", attrs={"level": 7}).to_dict()]},
            )
        )
        with self.assertRaisesRegex(RenderError, "^invalid-heading-level$"):
            render_editorial_html(document)

    def test_spoiler_title_remote_image_fails_ready_validation(self):
        document = fixture_document(
            Node(
                kind="spoiler",
                attrs={
                    "title": [
                        Node(
                            kind="image",
                            attrs={"src": "https://codeforces.com/remote.png", "alt": "remote"},
                        ).to_dict()
                    ]
                },
            )
        )
        with self.assertRaisesRegex(RenderError, "^remote-image-in-ready-document$"):
            render_editorial_html(document)

    def test_escapes_text_and_rejects_unsafe_link(self):
        document = fixture_document(
            Node(kind="paragraph", children=[
                Node(kind="link", attrs={"href": "javascript:alert(1)"}, children=[Node(kind="text", text="<click>")])
            ])
        )
        self.assertEqual(render_editorial_html(document), "<p>&lt;click&gt;</p>")

    def test_code_whitespace_and_math_text_survive(self):
        document = fixture_document(
            Node(kind="code_block", attrs={"language": "cpp"}, text="#include <x>\n  return 0;"),
            Node(kind="paragraph", children=[Node(kind="text", text="$x_1 < y$")]),
        )
        self.assertEqual(
            render_editorial_html(document),
            '<pre><code class="language-cpp">#include &lt;x&gt;\n  return 0;</code></pre><p>$x_1 &lt; y$</p>',
        )

    def test_math_normalization_does_not_rewrite_literal_backslash_text(self):
        document = fixture_document(
            Node(kind="paragraph", children=[Node(kind="text", text=r"literal \lt, math $$$x \lt y$$$")])
        )
        self.assertEqual(
            render_editorial_html(document),
            r"<p>literal \lt, math $x &lt; y$</p>",
        )

    def test_code_and_math_fixture_round_trips_without_whitespace_loss(self):
        source = (Path(__file__).parent / "fixtures/editorials/synthetic/code-and-math.html").read_text(encoding="utf-8")
        document = parse_blog_html(source, contest_id="1", source_url="u")
        html = render_editorial_html(document)
        self.assertIn("#include &lt;bits/stdc++.h&gt;\n  return 0;", html)
        self.assertIn("Formula: $x_1 &lt; y$", html)

    def test_external_http_links_get_new_tab_protections(self):
        for url in ("http://example.com/a?x=1&y=2", "https://example.com/"):
            with self.subTest(url=url):
                document = fixture_document(
                    Node(kind="link", attrs={"href": url}, children=[Node(kind="text", text="site")])
                )
                escaped = url.replace("&", "&amp;")
                self.assertEqual(
                    render_editorial_html(document),
                    f'<a href="{escaped}" target="_blank" rel="noopener noreferrer">site</a>',
                )

    def test_safe_local_link_does_not_get_external_attributes(self):
        document = fixture_document(
            Node(kind="link", attrs={"href": "/contest/1/problem/A"}, children=[Node(kind="text", text="A")])
        )
        self.assertEqual(render_editorial_html(document), '<a href="/contest/1/problem/A">A</a>')

    def test_link_sanitizer_is_allowlist_based(self):
        cases = {
            "https://example.com/x": "https://example.com/x",
            "http://example.com/x": "http://example.com/x",
            "/contest/1/problem/A": "/contest/1/problem/A",
            "#proof": "#proof",
            "javascript:alert(1)": None,
            "data:text/html,boom": None,
            "mailto:a@example.com": None,
            "//example.com/x": None,
            " https://example.com/x": None,
            "https://exa\tmple.com/x": None,
            "http://[::1": None,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(sanitize_link_url(value), expected)

    def test_retains_only_safe_local_raster_images_and_escapes_alt(self):
        document = fixture_document(
            Node(kind="image", attrs={"src": "/eimages/1.png", "alt": 'x" <y>'})
        )
        self.assertEqual(
            render_editorial_html(document),
            '<img src="/eimages/1.png" alt="x&quot; &lt;y&gt;">',
        )
        self.assertEqual(sanitize_image_url("/eimages/1.png"), "/eimages/1.png")

    def test_image_sanitizer_allows_only_approved_raster_extensions(self):
        cases = {
            "/eimages/a.png": "/eimages/a.png",
            "/eimages/a.JPG": "/eimages/a.JPG",
            "/eimages/a.jpeg": "/eimages/a.jpeg",
            "/eimages/a.GIF": "/eimages/a.GIF",
            "/eimages/a.webp": "/eimages/a.webp",
            "/eimages/extensionless": None,
            "/eimages/file.html": None,
            "/eimages/file.svg": None,
            "/eimages/file.bmp": None,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(sanitize_image_url(value), expected)

    def test_image_sanitizer_rejects_remote_non_asset_and_svg_urls(self):
        for value in (
            "https://codeforces.com/1.png",
            "/images/1.png",
            "/eimages/vector.svg",
            "/eimages/vector.SVG?x=1",
            "/eimages/vector%2esvg",
            "/eimages/%2e%2e/secret.png",
            "//codeforces.com/eimages/1.png",
            " /eimages/1.png",
        ):
            with self.subTest(value=value):
                self.assertIsNone(sanitize_image_url(value))

    def test_missing_asset_and_rejected_svg_emit_placeholder(self):
        self.assertEqual(
            render_editorial_html(fixture_document(Node(kind="missing_asset"))),
            '<span class="img-missing">Image unavailable</span>',
        )
        self.assertEqual(
            render_editorial_html(
                fixture_document(Node(kind="image", attrs={"src": "/eimages/vector.svg", "alt": "vector"}))
            ),
            '<span class="img-missing">Image unavailable</span>',
        )

    def test_ready_validation_rejects_remote_image(self):
        document = fixture_document(
            Node(kind="image", attrs={"src": "https://codeforces.com/1.png", "alt": "remote"})
        )
        with self.assertRaisesRegex(RenderError, "^remote-image-in-ready-document$"):
            render_editorial_html(document)

    def test_unknown_language_emits_no_class(self):
        document = fixture_document(
            Node(kind="code_block", attrs={"language": 'cpp\" onmouseover="boom'}, text="x < y")
        )
        self.assertEqual(render_editorial_html(document), "<pre><code>x &lt; y</code></pre>")

    def test_two_cell_table_renders_semantic_structure(self):
        document = fixture_document(
            Node(
                kind="table",
                children=[
                    Node(
                        kind="table_row",
                        children=[
                            Node(kind="table_cell", children=[Node(kind="text", text="A")]),
                            Node(kind="table_cell", children=[Node(kind="text", text="B")]),
                        ],
                    )
                ],
            )
        )
        self.assertEqual(render_editorial_html(document), "<table><tr><td>A</td><td>B</td></tr></table>")

    def test_subscript_and_superscript_are_preserved(self):
        document = fixture_document(
            Node(kind="paragraph", children=[
                Node(kind="text", text="x"),
                Node(kind="subscript", children=[Node(kind="text", text="1")]),
                Node(kind="superscript", children=[Node(kind="text", text="2")]),
            ])
        )
        self.assertEqual(render_editorial_html(document), "<p>x<sub>1</sub><sup>2</sup></p>")

    def test_problem_section_and_owned_block_tags_render_exactly(self):
        document = fixture_document(
            Node(
                kind="problem_section",
                attrs={"problemCode": "1700A"},
                children=[
                    Node(kind="heading", attrs={"level": 3}, children=[Node(kind="text", text="1700A - Optimal Path")]),
                    Node(kind="paragraph", children=[Node(kind="text", text="A body")]),
                ],
            )
        )
        self.assertEqual(
            render_editorial_html(document),
            '<section data-problem-code="1700A"><h3>1700A - Optimal Path</h3><p>A body</p></section>',
        )

    def test_renderer_escapes_owned_attributes_and_ignores_source_classes(self):
        document = fixture_document(
            Node(
                kind="problem_section",
                attrs={"problemCode": 'A\" onmouseover="boom', "class": "source-class"},
                children=[Node(kind="container", attrs={"class": "ttypography"}, children=[Node(kind="text", text="ok")])],
            )
        )
        self.assertEqual(
            render_editorial_html(document),
            '<section data-problem-code="A&quot; onmouseover=&quot;boom">ok</section>',
        )

    def test_lists_quotes_inline_markup_and_breaks_use_allowlisted_tags(self):
        document = fixture_document(
            Node(kind="list", attrs={"ordered": True}, children=[
                Node(kind="list_item", children=[
                    Node(kind="strong", children=[Node(kind="text", text="bold")]),
                    Node(kind="emphasis", children=[Node(kind="text", text="em")]),
                    Node(kind="inline_code", children=[Node(kind="text", text="a < b")]),
                    Node(kind="line_break"),
                ])
            ]),
            Node(kind="blockquote", children=[Node(kind="text", text="quote")]),
            Node(kind="horizontal_rule"),
        )
        self.assertEqual(
            render_editorial_html(document),
            "<ol><li><strong>bold</strong><em>em</em><code>a &lt; b</code><br></li></ol><blockquote>quote</blockquote><hr>",
        )

    def test_unresolved_slots_are_rejected(self):
        document = fixture_document(Node(kind="tutorial_slot", attrs={"problemCode": "1700A"}))
        with self.assertRaisesRegex(RenderError, "^unresolved-tutorial-slot$"):
            render_editorial_html(document)

    def test_two_renders_of_same_document_are_equal(self):
        document = fixture_document(
            Node(kind="paragraph", children=[Node(kind="text", text="deterministic")])
        )
        self.assertEqual(render_editorial_html(document), render_editorial_html(document))


if __name__ == "__main__":
    unittest.main()
