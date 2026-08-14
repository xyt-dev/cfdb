import unittest

from editorial_parser import parse_blog_html


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
