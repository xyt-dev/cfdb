from importlib import import_module
import unittest

from editorial_model import Node


StatementDocument = import_module("statement_model").StatementDocument
statement_render = import_module("statement_render")
RenderError = statement_render.RenderError
render_statement_html = statement_render.render_statement_html


def document_with(*children):
    return StatementDocument(
        problem_code="1700A",
        contest_id="1700",
        index="A",
        source_url="https://codeforces.com/contest/1700/problem/A",
        source_kind="html",
        root=Node(kind="document", children=list(children)),
    )


def sample(input_text: str, output_text: str):
    return Node(
        kind="section",
        attrs={"role": "sample"},
        children=[
            Node(
                kind="section",
                attrs={"role": "sample_input"},
                children=[
                    Node(
                        kind="heading",
                        attrs={"level": 3},
                        children=[Node(kind="text", text="Input")],
                    ),
                    Node(kind="code_block", text=input_text),
                ],
            ),
            Node(
                kind="section",
                attrs={"role": "sample_output"},
                children=[
                    Node(
                        kind="heading",
                        attrs={"level": 3},
                        children=[Node(kind="text", text="Output")],
                    ),
                    Node(kind="code_block", text=output_text),
                ],
            ),
        ],
    )


class StatementRenderTests(unittest.TestCase):
    def test_statement_samples_render_as_paired_semantic_sections(self):
        document = document_with(
            Node(
                kind="section",
                attrs={"role": "samples"},
                children=[sample("1\n", "2\n"), sample("3\n", "4\n")],
            )
        )

        html = render_statement_html(document)

        self.assertEqual(html.count('class="cf-sample"'), 2)
        self.assertEqual(html.count('class="cf-sample-input"'), 2)
        self.assertEqual(html.count('class="cf-sample-output"'), 2)
        self.assertLess(html.index('class="cf-sample-input"'), html.index('class="cf-sample-output"'))
        self.assertEqual(html.count("<h3>Input</h3>"), 2)
        self.assertEqual(html.count("<h3>Output</h3>"), 2)

    def test_pdf_attachment_renders_as_escaped_local_link_only(self):
        href = "/statement-assets/" + "a" * 64 + ".pdf"
        document = document_with(
            Node(
                kind="attachment",
                attrs={
                    "href": href,
                    "mediaType": "application/pdf",
                    "label": 'Open "PDF"',
                },
            )
        )

        html = render_statement_html(document)

        self.assertIn('href="' + href + '"', html)
        self.assertIn("Open &quot;PDF&quot;", html)
        self.assertNotIn("<iframe", html)
        self.assertNotIn("<object", html)
        self.assertNotIn("<embed", html)

    def test_remote_pdf_attachment_fails_closed(self):
        document = document_with(
            Node(
                kind="attachment",
                attrs={
                    "href": "https://evil.test/a.pdf",
                    "mediaType": "application/pdf",
                    "label": "Open PDF",
                },
            )
        )

        with self.assertRaisesRegex(RenderError, "remote-attachment-in-ready-document"):
            render_statement_html(document)

    def test_statement_roles_render_only_controlled_classes(self):
        document = document_with(
            Node(
                kind="heading",
                attrs={"role": "title", "level": 1, "class": 'x" onclick="attack'},
                children=[Node(kind="text", text="A < B")],
            ),
            Node(
                kind="section",
                attrs={"role": "input_specification", "class": "source-class"},
                children=[Node(kind="paragraph", children=[Node(kind="text", text="n")])],
            ),
        )

        html = render_statement_html(document)

        self.assertEqual(
            html,
            '<h1 class="cf-statement-title">A &lt; B</h1>'
            '<section class="cf-input-specification"><p>n</p></section>',
        )
        self.assertNotIn("onclick", html)
        self.assertNotIn("source-class", html)


if __name__ == "__main__":
    unittest.main()
