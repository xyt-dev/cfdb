import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cfcrawl


class LegacyEditorialCrawlerTests(unittest.TestCase):
    def _crawl(self, contest_id, blog_html, tutorials):
        with tempfile.TemporaryDirectory() as temp_dir:
            editorial_dir = Path(temp_dir) / "editorials"
            image_dir = editorial_dir / "images"
            blog_url = "https://codeforces.com/blog/entry/999999"

            def fetch_url(url, **_kwargs):
                if url == f"https://codeforces.com/contest/{contest_id}":
                    return '<a href="/blog/entry/999999" title="Editorial">Editorial</a>'
                if url == blog_url:
                    return blog_html
                self.fail(f"unexpected URL: {url}")

            with (
                patch.object(cfcrawl, "EDITORIAL_DIR", str(editorial_dir)),
                patch.object(cfcrawl, "EDITORIAL_IMAGE_DIR", str(image_dir)),
                patch.object(cfcrawl, "fetch_url", side_effect=fetch_url),
                patch.object(
                    cfcrawl,
                    "_fetch_problem_tutorials",
                    return_value=(tutorials, []),
                ),
                patch.object(cfcrawl, "_embed_images", side_effect=lambda md, *_args: md),
            ):
                result = cfcrawl.fetch_editorial_md(contest_id)
                stored = (editorial_dir / f"{contest_id}.md").read_text(encoding="utf-8")

        self.assertIsNotNone(result)
        return stored

    def test_recrawl_keeps_1700_heading_with_exact_body(self):
        blog_html = """
        <div class="ttypography">
          <p><a href="/contest/1700/problem/A">1700A - Optimal Path</a></p>
          <p><a href="/contest/1700/problem/B">1700B - Palindromic Numbers</a></p>
          <div class="problemTutorial" problemcode="1700A">Tutorial is loading...</div>
          <div class="problemTutorial" problemcode="1700B">Tutorial is loading...</div>
        </div>
        """
        stored = self._crawl(
            "1700",
            blog_html,
            {
                "1700A": "## 1700A - Optimal Path\n\nA_BODY_SENTINEL " + "explanation " * 20,
                "1700B": "## 1700B - Palindromic Numbers\n\nB_BODY_SENTINEL " + "explanation " * 20,
            },
        )

        positions = [
            stored.index("## 1700A - Optimal Path"),
            stored.index("A_BODY_SENTINEL"),
            stored.index("## 1700B - Palindromic Numbers"),
            stored.index("B_BODY_SENTINEL"),
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(stored.count("## 1700A - Optimal Path"), 1)
        self.assertEqual(stored.count("## 1700B - Palindromic Numbers"), 1)

    def test_recrawl_omits_1369_redundant_nested_api_heading(self):
        blog_html = """
        <div class="ttypography">
          <h4><a href="/contest/1369/problem/A">A. FashionabLee :</a></h4>
          <p><b>Complete Proof</b></p>
          <div class="problemTutorial" problemcode="1369A">Tutorial is loading...</div>
        </div>
        """
        stored = self._crawl(
            "1369",
            blog_html,
            {
                "1369A": "## 1369A - FashionabLee\n\n" + r"$\mathcal Complete\;Proof$" + "\n\nA_PROOF_SENTINEL",
            },
        )

        self.assertIn("#### [A. FashionabLee :]", stored)
        self.assertIn("**Complete Proof**", stored)
        self.assertNotIn("## 1369A - FashionabLee", stored)
        self.assertLess(stored.index("**Complete Proof**"), stored.index("A_PROOF_SENTINEL"))

    def test_exact_slot_replacement_never_falls_back_to_first_letter(self):
        markdown = "\n".join(
            [
                "CFDB_TUTORIAL_SLOT_1970A1",
                "",
                "CFDB_TUTORIAL_SLOT_1970A2",
            ]
        )

        replaced = cfcrawl._replace_tutorial(
            markdown,
            "1970A2",
            "## 1970A2 - Example\n\nA2_BODY_SENTINEL",
        )

        self.assertIn("CFDB_TUTORIAL_SLOT_1970A1", replaced)
        self.assertNotIn("CFDB_TUTORIAL_SLOT_1970A2", replaced)
        self.assertIn("A2_BODY_SENTINEL", replaced)


if __name__ == "__main__":
    unittest.main()
