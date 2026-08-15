import hashlib
from importlib import import_module
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from editorial_model import EditorialDocument, Node


content_assets = import_module("content_assets")
AssetError = content_assets.AssetError
AssetFetchResult = content_assets.AssetFetchResult
AssetPolicy = content_assets.AssetPolicy
localize_content_assets = content_assets.localize_content_assets
StatementDocument = import_module("statement_model").StatementDocument

PDF_POLICY = AssetPolicy(allow_raster=False, allow_pdf_attachment=True, max_bytes=64)
RASTER_POLICY = AssetPolicy(allow_raster=True, allow_pdf_attachment=False, max_bytes=64)
PNG_PAYLOAD = b"\x89PNG\r\n\x1a\nsynthetic"
PDF_PAYLOAD = b"%PDF-1.7\nsynthetic"


def make_pdf_statement(url: str):
    return StatementDocument(
        problem_code="1700A",
        contest_id="1700",
        index="A",
        source_url="https://codeforces.com/contest/1700/problem/A",
        source_kind="pdf",
        root=Node(
            kind="document",
            children=[
                Node(
                    kind="attachment",
                    attrs={
                        "href": url,
                        "mediaType": "application/pdf",
                        "label": "Open PDF",
                    },
                )
            ],
        ),
    )


def make_editorial_image(url: str):
    return EditorialDocument(
        contest_id="1700",
        source_url="https://codeforces.com/blog/entry/1",
        root=Node(
            kind="document",
            children=[Node(kind="image", attrs={"src": url, "alt": "diagram"})],
        ),
    )


def first_resource(document):
    return document.root.children[0]


class ContentAssetsTests(unittest.TestCase):
    def test_pdf_is_written_by_full_digest_and_linked_locally(self):
        source = make_pdf_statement("https://codeforces.com/a.pdf")
        with tempfile.TemporaryDirectory() as directory:
            localized = localize_content_assets(
                source,
                generation_asset_dir=directory,
                route_prefix="/statement-assets",
                fetcher=lambda url: AssetFetchResult(PDF_PAYLOAD, "application/pdf"),
                policy=PDF_POLICY,
            )
            digest = hashlib.sha256(PDF_PAYLOAD).hexdigest()
            route = f"/statement-assets/{digest}.pdf"

            self.assertEqual(first_resource(localized).attrs["href"], route)
            self.assertEqual((Path(directory) / f"{digest}.pdf").read_bytes(), PDF_PAYLOAD)
            self.assertEqual(localized.assets, [route])
            self.assertEqual(
                first_resource(source).attrs["href"],
                "https://codeforces.com/a.pdf",
            )

    def test_pdf_rejects_interstitial_mime_magic_and_oversize(self):
        cases = [
            (AssetFetchResult(b"<html>challenge</html>", "application/pdf"), "invalid-pdf-magic"),
            (AssetFetchResult(PDF_PAYLOAD, "text/html"), "invalid-pdf-media-type"),
            (AssetFetchResult(b"not-a-pdf", "application/pdf"), "invalid-pdf-magic"),
            (AssetFetchResult(b"%PDF-" + b"x" * 64, "application/pdf"), "asset-too-large"),
        ]
        for fetched, error in cases:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(AssetError, error):
                    localize_content_assets(
                        make_pdf_statement("https://codeforces.com/a.pdf"),
                        generation_asset_dir=directory,
                        route_prefix="/statement-assets",
                        fetcher=lambda _url, value=fetched: value,
                        policy=PDF_POLICY,
                    )
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_raster_magic_controls_extension_and_route(self):
        source = make_editorial_image("https://codeforces.com/image.unknown")
        with tempfile.TemporaryDirectory() as directory:
            localized = localize_content_assets(
                source,
                generation_asset_dir=directory,
                route_prefix="/editorial-assets",
                fetcher=lambda _url: AssetFetchResult(PNG_PAYLOAD, "image/png"),
                policy=RASTER_POLICY,
            )
            digest = hashlib.sha256(PNG_PAYLOAD).hexdigest()
            route = f"/editorial-assets/{digest}.png"

            self.assertEqual(first_resource(localized).attrs["src"], route)
            self.assertEqual((Path(directory) / f"{digest}.png").read_bytes(), PNG_PAYLOAD)

    def test_existing_digest_mismatch_is_never_replaced(self):
        digest = hashlib.sha256(PDF_PAYLOAD).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / f"{digest}.pdf"
            target.write_bytes(b"corrupt")

            with self.assertRaisesRegex(AssetError, "existing-asset-mismatch"):
                localize_content_assets(
                    make_pdf_statement("https://codeforces.com/a.pdf"),
                    generation_asset_dir=directory,
                    route_prefix="/statement-assets",
                    fetcher=lambda _url: AssetFetchResult(PDF_PAYLOAD, "application/pdf"),
                    policy=PDF_POLICY,
                )

            self.assertEqual(target.read_bytes(), b"corrupt")

    def test_interrupted_atomic_write_leaves_no_visible_asset(self):
        digest = hashlib.sha256(PDF_PAYLOAD).hexdigest()
        with tempfile.TemporaryDirectory() as directory, patch(
            "content_assets.os.replace",
            side_effect=OSError("interrupted"),
        ):
            with self.assertRaisesRegex(AssetError, "asset-write-failed"):
                localize_content_assets(
                    make_pdf_statement("https://codeforces.com/a.pdf"),
                    generation_asset_dir=directory,
                    route_prefix="/statement-assets",
                    fetcher=lambda _url: AssetFetchResult(PDF_PAYLOAD, "application/pdf"),
                    policy=PDF_POLICY,
                )

            self.assertFalse((Path(directory) / f"{digest}.pdf").exists())
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
