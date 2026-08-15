import json
import unittest

from content_model import ContentNode, canonical_json, validate_content_tree  # pyright: ignore[reportMissingImports]


class ContentModelTests(unittest.TestCase):
    def test_content_node_canonical_json_is_stable(self):
        node = ContentNode(
            kind="paragraph",
            children=[ContentNode(kind="text", text="x")],
        )

        encoded = canonical_json({"root": node.to_dict(), "schema": 2})

        self.assertEqual(
            encoded,
            '{"root":{"children":[{"kind":"text","text":"x"}],"kind":"paragraph"},"schema":2}',
        )
        self.assertEqual(json.loads(encoded)["root"]["kind"], "paragraph")

    def test_ready_attachment_requires_local_pdf_route(self):
        root = ContentNode(
            kind="document",
            children=[
                ContentNode(
                    kind="attachment",
                    attrs={
                        "href": "https://example.com/a.pdf",
                        "mediaType": "application/pdf",
                    },
                )
            ],
        )

        errors = validate_content_tree(
            root,
            diagnostics=[],
            assets=[],
            ready=True,
            content_kind="statement",
        )

        self.assertEqual([error.code for error in errors], ["remote-attachment-in-ready-document"])

    def test_attachment_is_not_valid_editorial_content(self):
        root = ContentNode(
            kind="document",
            children=[
                ContentNode(
                    kind="attachment",
                    attrs={
                        "href": "/statement-assets/" + "a" * 64 + ".pdf",
                        "mediaType": "application/pdf",
                    },
                )
            ],
        )

        errors = validate_content_tree(
            root,
            diagnostics=[],
            assets=[],
            ready=True,
            content_kind="editorial",
        )

        self.assertEqual([error.code for error in errors], ["invalid-attachment-content-kind"])


    def test_ready_images_require_content_addressed_kind_routes(self):
        digest = "a" * 64
        for content_kind, source in (
            ("editorial", f"/editorial-assets/{digest}.png"),
            ("statement", f"/statement-assets/{digest}.webp"),
        ):
            with self.subTest(content_kind=content_kind, source=source):
                root = ContentNode(kind="document", children=[ContentNode(kind="image", attrs={"src": source})])
                self.assertEqual(
                    validate_content_tree(
                        root,
                        diagnostics=[],
                        assets=[],
                        ready=True,
                        content_kind=content_kind,
                    ),
                    [],
                )

        for content_kind, source in (
            ("editorial", "/eimages/legacy.png"),
            ("statement", "/images/legacy.png"),
        ):
            with self.subTest(content_kind=content_kind, source=source):
                root = ContentNode(kind="document", children=[ContentNode(kind="image", attrs={"src": source})])
                errors = validate_content_tree(
                    root,
                    diagnostics=[],
                    assets=[],
                    ready=True,
                    content_kind=content_kind,
                )
                self.assertEqual(
                    [error.code for error in errors],
                    ["remote-image-in-ready-document"],
                )


if __name__ == "__main__":
    unittest.main()
