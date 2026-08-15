import hashlib
from importlib import import_module
import json
from pathlib import Path
import unittest

from editorial_model import canonical_json


statement_parser = import_module("statement_parser")
ParseError = statement_parser.ParseError
parse_statement_html = statement_parser.parse_statement_html

FIXTURES = Path(__file__).parent / "fixtures" / "statements"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _roles(root) -> list[str]:
    return [str(node.attrs["role"]) for node in _walk(root) if "role" in node.attrs]


def _nodes_with_role(root, role: str):
    return [node for node in _walk(root) if node.attrs.get("role") == role]


def _plain_text(node) -> str:
    return (node.text or "") + "".join(_plain_text(child) for child in node.children)


class StatementParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = json.loads(_fixture("expected.json"))

    def parse_fixture(
        self,
        name: str,
        *,
        problem_code: str,
        contest_id: str,
        index: str,
    ):
        return parse_statement_html(
            _fixture(name),
            problem_code=problem_code,
            contest_id=contest_id,
            index=index,
            source_url=f"https://codeforces.com/contest/{contest_id}/problem/{index}",
        )

    def test_fixture_checksums(self):
        manifest = json.loads(_fixture("manifest.json"))
        recorded = {entry["path"] for entry in manifest["fixtures"]}
        actual = {
            path.name
            for path in FIXTURES.iterdir()
            if path.is_file() and path.name != "manifest.json"
        }

        self.assertEqual(recorded, actual)
        for entry in manifest["fixtures"]:
            payload = (FIXTURES / entry["path"]).read_bytes()
            self.assertEqual(entry["encoding"], "utf-8")
            self.assertEqual(entry["newline"], "lf")
            self.assertNotIn(b"\r\n", payload)
            payload.decode("utf-8")
            self.assertEqual(hashlib.sha256(payload).hexdigest(), entry["sha256"])

    def test_preserves_metadata_sections_and_sample_pairs(self):
        document = self.parse_fixture(
            "normal.html",
            problem_code="1700A",
            contest_id="1700",
            index="A",
        )
        expected = self.expected["normal"]

        self.assertEqual(document.problem_code, expected["problemCode"])
        self.assertEqual(_roles(document.root), expected["roles"])
        samples = _nodes_with_role(document.root, "sample")
        self.assertEqual(len(samples), 2)
        for sample in samples:
            self.assertEqual(
                [child.attrs["role"] for child in sample.children],
                ["sample_input", "sample_output"],
            )
        self.assertEqual(
            [_plain_text(node) for node in _nodes_with_role(document.root, "sample_input")],
            expected["sampleInputText"],
        )
        self.assertEqual(
            [_plain_text(node) for node in _nodes_with_role(document.root, "sample_output")],
            expected["sampleOutputText"],
        )

    def test_localized_labels_do_not_determine_section_identity(self):
        document = self.parse_fixture(
            "localized.html",
            problem_code="1970A1",
            contest_id="1970",
            index="A1",
        )

        self.assertEqual(document.problem_code, self.expected["localized"]["problemCode"])
        self.assertEqual(_roles(document.root), self.expected["localized"]["roles"])
        input_section = _nodes_with_role(document.root, "input_specification")[0]
        self.assertIn("Входные данные", _plain_text(input_section))


    def test_live_preformatted_lines_titles_and_hidden_standard_channels(self):
        document = parse_statement_html(
            """
            <div class="problem-statement">
              <div class="header">
                <div class="title">A. Live Shapes</div>
                <div class="input-file input-standard"><div class="property-title">input</div>standard input</div>
                <div class="output-file output-standard"><div class="property-title">output</div>standard output</div>
              </div>
              <div class="problem-description">
                <pre>namespace Solution {<br>    operation Solve () : ()<br><br>}</pre>
              </div>
              <div class="sample-tests">
                <div class="section-title">Example</div>
                <div class="sample-test">
                  <div class="input">
                    <div class="title">Input</div>
                    <pre><div class="test-example-line">7</div><div class="test-example-line">1 1</div><div class="test-example-line">2 3</div></pre>
                  </div>
                  <div class="output">
                    <div class="title">Output</div>
                    <pre>1<br>12<br>13</pre>
                  </div>
                </div>
              </div>
            </div>
            """,
            problem_code="1700A",
            contest_id="1700",
            index="A",
            source_url="https://codeforces.com/contest/1700/problem/A",
        )

        roles = _roles(document.root)
        self.assertNotIn("input_channel", roles)
        self.assertNotIn("output_channel", roles)
        code_blocks = [node for node in _walk(document.root) if node.kind == "code_block"]
        self.assertEqual(
            [node.text for node in code_blocks],
            [
                "namespace Solution {\n    operation Solve () : ()\n\n}",
                "7\n1 1\n2 3\n",
                "1\n12\n13",
            ],
        )

        sample_input = _nodes_with_role(document.root, "sample_input")[0]
        sample_output = _nodes_with_role(document.root, "sample_output")[0]
        self.assertEqual(sample_input.children[0].kind, "heading")
        self.assertEqual(sample_input.children[0].attrs, {"level": 3})
        self.assertEqual(_plain_text(sample_input), "Input7\n1 1\n2 3\n")
        self.assertEqual(sample_output.children[0].kind, "heading")
        self.assertEqual(sample_output.children[0].attrs, {"level": 3})
        self.assertEqual(_plain_text(sample_output), "Output1\n12\n13")

    def test_interaction_scoring_and_custom_sections_keep_source_order(self):
        document = self.parse_fixture(
            "interaction.html",
            problem_code="1000B",
            contest_id="1000",
            index="B",
        )

        self.assertEqual(_roles(document.root), self.expected["interaction"]["roles"])
        custom = _nodes_with_role(document.root, "custom")[0]
        self.assertEqual(custom.attrs["title"], self.expected["interaction"]["customTitle"])

    def test_unsafe_elements_urls_and_event_attributes_are_discarded(self):
        document = self.parse_fixture(
            "unsafe.html",
            problem_code="1000D",
            contest_id="1000",
            index="D",
        )
        serialized = canonical_json(document)

        self.assertNotIn("onclick", serialized)
        self.assertNotIn("onmouseover", serialized)
        self.assertNotIn("onerror", serialized)
        self.assertNotIn("javascript:", serialized)
        self.assertNotIn("SCRIPT_TEXT_MUST_NOT_SURVIVE", serialized)
        self.assertNotIn("STYLE_TEXT_MUST_NOT_SURVIVE", serialized)
        self.assertNotIn("FRAME_TEXT_MUST_NOT_SURVIVE", serialized)
        self.assertIn("unsafe link text", serialized)
        self.assertIn("https://codeforces.com/", serialized)

    def test_malformed_dom_is_recovered_without_losing_sections(self):
        document = self.parse_fixture(
            "malformed.html",
            problem_code="1000C",
            contest_id="1000",
            index="C",
        )

        self.assertEqual(
            _roles(document.root),
            ["title", "body", "input_specification", "output_specification"],
        )
        self.assertIn("First paragraph", _plain_text(document.root))
        self.assertIn("two", _plain_text(document.root))

    def test_requires_exactly_one_problem_statement_island(self):
        with self.assertRaisesRegex(ParseError, "missing-problem-statement"):
            parse_statement_html(
                "<div>none</div>",
                problem_code="1A",
                contest_id="1",
                index="A",
                source_url="https://codeforces.com/contest/1/problem/A",
            )
        with self.assertRaisesRegex(ParseError, "ambiguous-problem-statement"):
            parse_statement_html(
                '<div class="problem-statement"></div><div class="problem-statement"></div>',
                problem_code="1A",
                contest_id="1",
                index="A",
                source_url="https://codeforces.com/contest/1/problem/A",
            )

    def test_rejects_mismatched_exact_problem_identity(self):
        with self.assertRaisesRegex(ParseError, "invalid-problem-identity"):
            self.parse_fixture(
                "normal.html",
                problem_code="1700A2",
                contest_id="1700",
                index="A1",
            )


if __name__ == "__main__":
    unittest.main()
