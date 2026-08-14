import copy
import json
import unittest
from pathlib import Path

from editorial_model import validate_document
from editorial_parser import (
    ParseError,
    compose_tutorials,
    parse_blog_html,
    parse_tutorial_fragment,
)


FIXTURES = Path(__file__).parent / "fixtures" / "editorials"


def fixture(relative: str) -> str:
    return (FIXTURES / relative).read_text(encoding="utf-8")


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def plain_text(node) -> str:
    return (node.text or "") + "".join(plain_text(child) for child in node.children)


class EditorialComposerTests(unittest.TestCase):
    def setUp(self):
        self.expected = json.loads(fixture("1700/expected.json"))
        self.document = parse_blog_html(
            fixture("1700/base.html"),
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
        )
        self.tutorials = {
            code: parse_tutorial_fragment(
                fixture(f"1700/tutorial-{code[-1]}.html"),
                expected_code=code,
            )
            for code in self.expected["problemCodes"]
        }

    def test_composes_contest_1700_in_exact_source_order(self):
        composed = compose_tutorials(self.document, tutorials=self.tutorials)

        problem_sections = [node for node in composed.root.children if node.kind == "problem_section"]
        self.assertEqual([node.attrs["problemCode"] for node in problem_sections], self.expected["problemCodes"])
        for problem, sentinel in zip(problem_sections, self.expected["bodySentinels"]):
            self.assertIn(sentinel, plain_text(problem))
        self.assertEqual(composed.root.children[1].kind, "paragraph")
        self.assertIn(self.expected["creditText"], plain_text(composed.root.children[1]))
        self.assertFalse(any(node.kind == "tutorial_slot" for node in walk(composed.root)))

    def test_fragment_code_must_match_full_expected_code(self):
        with self.assertRaisesRegex(ParseError, "problem-code-mismatch:1700A:1700B"):
            parse_tutorial_fragment(fixture("1700/tutorial-A.html"), expected_code="1700B")

    def test_missing_code_removes_only_its_exact_slot(self):
        tutorials = {code: node for code, node in self.tutorials.items() if code != "1700C"}
        composed = compose_tutorials(self.document, tutorials=tutorials, missing_codes={"1700C"})
        problem_sections = [node for node in composed.root.children if node.kind == "problem_section"]

        self.assertEqual(
            [node.attrs["problemCode"] for node in problem_sections],
            ["1700A", "1700B", "1700D", "1700E", "1700F"],
        )
        self.assertEqual(
            [next(sentinel for sentinel in self.expected["bodySentinels"] if sentinel in plain_text(node)) for node in problem_sections],
            ["A_BODY_SENTINEL", "B_BODY_SENTINEL", "D_BODY_SENTINEL", "E_BODY_SENTINEL", "F_BODY_SENTINEL"],
        )
        self.assertIn("tutorial-known-absent", [item.code for item in composed.diagnostics])

    def test_transient_omission_leaves_slot_and_fails_ready_validation(self):
        tutorials = {code: node for code, node in self.tutorials.items() if code != "1700C"}
        composed = compose_tutorials(self.document, tutorials=tutorials)

        slots = [node for node in walk(composed.root) if node.kind == "tutorial_slot"]
        self.assertEqual([node.attrs["problemCode"] for node in slots], ["1700C"])
        self.assertIn("unresolved-tutorial-slot", [item.code for item in validate_document(composed, ready=True)])

    def test_duplicate_fragment_code_is_rejected(self):
        tutorials = dict(self.tutorials)
        tutorials["1700B"] = copy.deepcopy(tutorials["1700A"])

        with self.assertRaisesRegex(ParseError, "duplicate-tutorial-fragment:1700A"):
            compose_tutorials(self.document, tutorials=tutorials)


if __name__ == "__main__":
    unittest.main()
