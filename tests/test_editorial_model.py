import json
import unittest

from editorial_model import (
    Diagnostic,
    EditorialDocument,
    Node,
    canonical_json,
    validate_document,
)


class EditorialModelTests(unittest.TestCase):
    def test_canonical_json_is_stable_and_sorted(self):
        document = EditorialDocument(
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
            root=Node(
                kind="document",
                children=[
                    Node(
                        kind="heading",
                        attrs={"level": 3},
                        children=[Node(kind="text", text="1700A - Optimal Path")],
                    )
                ],
            ),
            diagnostics=[Diagnostic("warning", "recovered-close", "closed p")],
            assets=[],
        )

        first = canonical_json(document)
        second = canonical_json(EditorialDocument.from_dict(json.loads(first)))

        self.assertEqual(first, second)
        self.assertEqual(json.loads(first)["schema"], 2)
        self.assertNotIn(": ", first)
        self.assertNotIn(", ", first)

    def test_validation_rejects_slot_in_ready_document(self):
        document = EditorialDocument(
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
            root=Node(
                kind="document",
                children=[Node(kind="tutorial_slot", attrs={"problemCode": "1700A"})],
            ),
        )

        errors = validate_document(document, ready=True)

        self.assertEqual([e.code for e in errors], ["unresolved-tutorial-slot"])

    def test_validation_rejects_invalid_heading_level(self):
        document = EditorialDocument(
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
            root=Node(
                kind="document",
                children=[Node(kind="heading", attrs={"level": 7})],
            ),
        )

        errors = validate_document(document, ready=False)

        self.assertEqual([e.code for e in errors], ["invalid-heading-level"])

    def test_validation_rejects_remote_image_in_ready_document(self):
        document = EditorialDocument(
            contest_id="1700",
            source_url="https://codeforces.com/blog/entry/103978",
            root=Node(
                kind="document",
                children=[Node(kind="image", attrs={"src": "https://codeforces.com/image.png", "alt": "x"})],
            ),
        )

        errors = validate_document(document, ready=True)

        self.assertEqual([e.code for e in errors], ["remote-image-in-ready-document"])


if __name__ == "__main__":
    unittest.main()
