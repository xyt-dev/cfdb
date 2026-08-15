import json
import unittest

from content_model import ContentNode
from statement_model import StatementDocument, validate_statement_document


class StatementModelTests(unittest.TestCase):
    def make_document(self, **changes):
        values = {
            "problem_code": "1970A1",
            "contest_id": "1970",
            "index": "A1",
            "source_url": "https://codeforces.com/contest/1970/problem/A1",
            "source_kind": "html",
            "root": ContentNode(kind="document"),
        }
        values.update(changes)
        return StatementDocument(**values)

    def test_statement_identity_round_trips_exact_compound_index(self):
        document = self.make_document()

        encoded = document.to_dict()
        restored = StatementDocument.from_dict(json.loads(json.dumps(encoded)))

        self.assertEqual(restored, document)
        self.assertEqual(document.content_kind, "statement")
        self.assertEqual(document.content_id, "1970A1")
        self.assertEqual(validate_statement_document(document, ready=False), [])

    def test_statement_rejects_mismatched_problem_code(self):
        document = self.make_document(problem_code="1970A2")

        errors = validate_statement_document(document, ready=False)

        self.assertEqual([error.code for error in errors], ["invalid-problem-identity"])

    def test_statement_rejects_unknown_source_kind(self):
        document = self.make_document(source_kind="markdown")

        errors = validate_statement_document(document, ready=False)

        self.assertEqual([error.code for error in errors], ["invalid-statement-source-kind"])


if __name__ == "__main__":
    unittest.main()
