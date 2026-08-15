from importlib import import_module
import unittest


content_render = import_module("content_render")
RenderError = content_render.RenderError
sanitize_attachment_url = content_render.sanitize_attachment_url
sanitize_image_url = content_render.sanitize_image_url
sanitize_link_url = content_render.sanitize_link_url


class ContentRenderTests(unittest.TestCase):
    def test_pdf_attachment_requires_local_content_address(self):
        valid = "/statement-assets/" + "a" * 64 + ".pdf"

        self.assertEqual(sanitize_attachment_url(valid), valid)
        for href in (
            "https://evil.test/a.pdf",
            "javascript:alert(1)",
            "/statement-assets/a.pdf",
            "/statement-assets/" + "A" * 64 + ".pdf",
            "/statement-assets/" + "a" * 64 + ".pdf?download=1",
        ):
            with self.subTest(href=href):
                with self.assertRaisesRegex(RenderError, "unsafe-attachment-url"):
                    sanitize_attachment_url(href)

    def test_link_and_image_sanitizers_are_route_allowlists(self):
        self.assertEqual(sanitize_link_url("/contest/1/problem/A"), "/contest/1/problem/A")
        self.assertIsNone(sanitize_link_url("javascript:alert(1)"))
        editorial_image = "/editorial-assets/" + "b" * 64 + ".webp"
        statement_image = "/statement-assets/" + "c" * 64 + ".png"
        self.assertEqual(
            sanitize_image_url(editorial_image, content_kind="editorial"),
            editorial_image,
        )
        self.assertEqual(
            sanitize_image_url(statement_image, content_kind="statement"),
            statement_image,
        )
        self.assertIsNone(sanitize_image_url(statement_image, content_kind="editorial"))
        self.assertIsNone(sanitize_image_url("/statement-assets/vector.svg", content_kind="statement"))


if __name__ == "__main__":
    unittest.main()
