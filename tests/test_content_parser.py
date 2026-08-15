import unittest

from content_parser import (
    ParseError,
    ParseLimits,
    class_tokens,
    parse_source_html,
    find_first_by_class,
    text_content,
)


class ContentParserTests(unittest.TestCase):
    def test_source_tree_preserves_parent_child_and_sibling_order(self):
        root = parse_source_html("<section><h2>A</h2><div><p>B</p></div></section>")
        section = root.children[0]

        self.assertEqual([child.tag for child in section.children], ["h2", "div"])
        self.assertEqual(section.children[1].children[0].tag, "p")

    def test_source_tree_preserves_interleaved_text_order(self):
        root = parse_source_html("<p>left<strong>middle</strong>right</p>")
        paragraph = root.children[0]

        self.assertEqual([child.tag for child in paragraph.children], ["#text", "strong", "#text"])
        self.assertEqual(text_content(paragraph), "leftmiddleright")

    def test_source_tree_applies_optional_list_item_closing(self):
        root = parse_source_html("<ul><li>first<li>second</ul>")
        unordered = root.children[0]

        self.assertEqual([child.tag for child in unordered.children], ["li", "li"])
        self.assertEqual([text_content(child) for child in unordered.children], ["first", "second"])

    def test_source_tree_rejects_depth_over_limit(self):
        with self.assertRaisesRegex(ParseError, "max-depth-exceeded"):
            parse_source_html(
                "<div><div><div>x</div></div></div>",
                limits=ParseLimits(max_depth=2),
            )

    def test_class_helpers_find_exact_class_token(self):
        root = parse_source_html('<div class="x ttypography y"><p>body</p></div>')

        match = find_first_by_class(root, "ttypography")

        self.assertIsNotNone(match)
        self.assertEqual(class_tokens(match), {"x", "ttypography", "y"})
        self.assertEqual(text_content(match), "body")


if __name__ == "__main__":
    unittest.main()
