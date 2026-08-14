import unittest
from pathlib import Path

from editorial_model import Node
from editorial_parser import ParseError, ParseLimits, parse_blog_html


FIXTURES = Path(__file__).parent / "fixtures" / "editorials"


def _fixture(relative: str) -> str:
    return (FIXTURES / relative).read_text(encoding="utf-8")


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _plain_text(node) -> str:
    return (node.text or "") + "".join(_plain_text(child) for child in node.children)


class EditorialParserTests(unittest.TestCase):
    def test_preserves_heading_levels_nested_lists_quotes_and_code(self):
        source = """
        <div class="ttypography">
          <h4>Problem A</h4>
          <ol><li>first<ul><li>nested</li></ul></li><li>second</li></ol>
          <blockquote><p>proof</p></blockquote>
          <pre><code>#include &lt;bits/stdc++.h&gt;\n  return 0;</code></pre>
        </div>
        """

        result = parse_blog_html(source, contest_id="1", source_url="https://codeforces.com/blog/entry/1")
        root = result.root

        self.assertEqual([node.kind for node in root.children], ["heading", "list", "blockquote", "code_block"])
        self.assertEqual(root.children[0].attrs, {"level": 4})
        self.assertTrue(root.children[1].attrs["ordered"])
        self.assertEqual(root.children[1].children[0].children[1].kind, "list")
        self.assertEqual(root.children[3].text, "#include <bits/stdc++.h>\n  return 0;")

    def test_author_credit_link_stays_a_paragraph(self):
        source = """
        <div class="ttypography">
          <p><a href="/contest/1700/problem/A">1700A - Optimal Path</a> was prepared by Alice.</p>
        </div>
        """

        result = parse_blog_html(source, contest_id="1700", source_url="https://codeforces.com/blog/entry/103978")

        self.assertEqual(result.root.children[0].kind, "paragraph")
        self.assertEqual(result.root.children[0].children[0].kind, "link")
        self.assertFalse(any(node.kind == "heading" for node in result.root.children))

    def test_optional_list_item_close_skips_intervening_inline_frames(self):
        source = """
        <div class="ttypography">
          <ul><li><strong>first<li>second</ul>
        </div>
        """

        result = parse_blog_html(source, contest_id="1", source_url="https://codeforces.com/blog/entry/1")
        items = result.root.children[0].children

        self.assertEqual([node.kind for node in items], ["list_item", "list_item"])
        self.assertEqual(items[0].children[0].kind, "strong")
        self.assertEqual(items[1].children[0].text, "second")

    def test_discards_inter_block_whitespace_but_preserves_inline_space(self):
        source = """
        <div class="ttypography"><p>one</p> <p>two</p>
          <p><strong>left</strong> <em>right</em></p></div>
        """

        result = parse_blog_html(source, contest_id="1", source_url="https://codeforces.com/blog/entry/1")
        root = result.root

        self.assertEqual([node.kind for node in root.children], ["paragraph", "paragraph", "paragraph"])
        self.assertEqual([node.kind for node in root.children[2].children], ["strong", "text", "emphasis"])
        self.assertEqual(root.children[2].children[1].text, " ")


class EditorialParserSemanticTests(unittest.TestCase):
    def test_spoiler_keeps_title_and_body_together(self):
        result = parse_blog_html(_fixture("1369/base.html"), contest_id="1369", source_url="u")
        spoiler = next(node for node in _walk(result.root) if node.kind == "spoiler")
        self.assertEqual(spoiler.attrs["title"][0]["text"], "Brief Solution")
        self.assertIn("A_BRIEF_SENTINEL", _plain_text(spoiler))

    def test_nested_spoilers_remain_nested(self):
        result = parse_blog_html(_fixture("synthetic/nested-structure.html"), contest_id="1700", source_url="u")
        spoilers = [node for node in _walk(result.root) if node.kind == "spoiler"]
        self.assertEqual([node.attrs["title"][0]["text"] for node in spoilers], ["Outer", "Inner"])
        self.assertIn("INNER_BODY", _plain_text(spoilers[0]))

    def test_problem_tutorial_becomes_identity_slot(self):
        result = parse_blog_html(_fixture("synthetic/nested-structure.html"), contest_id="1700", source_url="u")
        slots = [node for node in _walk(result.root) if node.kind == "tutorial_slot"]
        self.assertEqual([node.attrs["problemCode"] for node in slots], ["1700A"])

    def test_malformed_second_typography_island_is_excluded(self):
        result = parse_blog_html(_fixture("synthetic/malformed.html"), contest_id="1", source_url="u")
        self.assertIn("before", _plain_text(result.root))
        self.assertNotIn("comment must not enter editorial", _plain_text(result.root))
        self.assertIn("recovered-close", [item.code for item in result.diagnostics])

    def test_official_mixed_heading_levels_are_unchanged(self):
        result = parse_blog_html(_fixture("1706/base.html"), contest_id="1706", source_url="u")
        levels = [node.attrs["level"] for node in result.root.children if node.kind == "heading"]
        self.assertEqual(levels, [2, 4, 2, 4])

    def test_dangerous_subtrees_are_dropped_with_diagnostics(self):
        result = parse_blog_html(_fixture("synthetic/unsafe.html"), contest_id="1", source_url="u")
        text = _plain_text(result.root)
        self.assertNotIn("SCRIPT_TEXT_MUST_NOT_SURVIVE", text)
        self.assertNotIn("FORM_TEXT_MUST_NOT_SURVIVE", text)
        self.assertIn("unsafe link text", text)
        self.assertIn("safe text", text)
        self.assertIn("dropped-dangerous-subtree", [item.code for item in result.diagnostics])

    def test_depth_limit_raises_parse_error(self):
        source = '<div class="ttypography">' + "<div>" * 5 + "x" + "</div>" * 5 + "</div>"
        with self.assertRaisesRegex(ParseError, "max-depth-exceeded"):
            parse_blog_html(source, contest_id="1", source_url="u", limits=ParseLimits(max_depth=3))

    def test_malformed_link_url_is_dropped_without_uncontrolled_error(self):
        source = '<div class="ttypography"><p><a href="http://[::1">kept text</a></p></div>'

        result = parse_blog_html(source, contest_id="1", source_url="https://codeforces.com/blog/entry/1")
        link = next(node for node in _walk(result.root) if node.kind == "link")

        self.assertNotIn("href", link.attrs)
        self.assertEqual(_plain_text(link), "kept text")

    def test_image_preserves_only_normalized_source_and_decoded_alt(self):
        source = (
            '<div class="ttypography">'
            '<img src="/images/diagram.png" alt="A &amp; B" width="800" onerror="attack()">'
            '</div>'
        )

        result = parse_blog_html(
            source,
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
        )
        image = next(node for node in _walk(result.root) if node.kind == "image")

        self.assertEqual(
            image.attrs,
            {"src": "https://codeforces.com/images/diagram.png", "alt": "A & B"},
        )

    def test_missing_malformed_and_unsafe_image_sources_become_missing_assets(self):
        source = (
            '<div class="ttypography">'
            '<img alt="missing">'
            '<img src="http://[::1" alt="malformed">'
            '<img src="javascript:attack()" alt="unsafe">'
            '<img src="http:/no-host.png" alt="hostless">'
            '</div>'
        )

        result = parse_blog_html(
            source,
            contest_id="1",
            source_url="https://codeforces.com/blog/entry/1",
        )
        missing = [node for node in _walk(result.root) if node.kind == "missing_asset"]

        self.assertEqual(
            [node.attrs for node in missing],
            [{"alt": "missing"}, {"alt": "malformed"}, {"alt": "unsafe"}, {"alt": "hostless"}],
        )
        self.assertEqual(
            [item.code for item in result.diagnostics].count("unsupported-image-source"),
            4,
        )
