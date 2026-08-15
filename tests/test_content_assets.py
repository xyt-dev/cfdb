import hashlib
from importlib import import_module
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zlib

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


def _png_chunk(chunk_type: bytes, body: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + body) & 0xFFFFFFFF
    return (
        len(body).to_bytes(4, "big")
        + chunk_type
        + body
        + checksum.to_bytes(4, "big")
    )


def _grayscale_alpha_png(pixels: list[tuple[int, int]]) -> bytes:
    header = (
        len(pixels).to_bytes(4, "big")
        + (1).to_bytes(4, "big")
        + b"\x08\x04\x00\x00\x00"
    )
    scanline = b"\x00" + bytes(component for pixel in pixels for component in pixel)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanline))
        + _png_chunk(b"IEND", b"")
    )


def _png_from_scanline(
    *,
    width: int,
    bit_depth: int,
    color_type: int,
    scanline: bytes,
    chunks_before_idat: tuple[tuple[bytes, bytes], ...] = (),
) -> bytes:
    header = (
        width.to_bytes(4, "big")
        + (1).to_bytes(4, "big")
        + bytes((bit_depth, color_type, 0, 0, 0))
    )
    chunks = b"".join(
        _png_chunk(chunk_type, body)
        for chunk_type, body in chunks_before_idat
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + chunks
        + _png_chunk(b"IDAT", zlib.compress(scanline))
        + _png_chunk(b"IEND", b"")
    )


def _decoded_png_scanlines(payload: bytes) -> bytes:
    position = 8
    compressed = bytearray()
    while position + 12 <= len(payload):
        size = int.from_bytes(payload[position:position + 4], "big")
        chunk_type = payload[position + 4:position + 8]
        body = payload[position + 8:position + 8 + size]
        if chunk_type == b"IDAT":
            compressed.extend(body)
        position += size + 12
    return zlib.decompress(bytes(compressed))


def _rewrite_png_idat(payload: bytes, transform) -> bytes:
    position = 8
    rewritten = bytearray(payload[:8])
    while position + 12 <= len(payload):
        size = int.from_bytes(payload[position:position + 4], "big")
        chunk_type = payload[position + 4:position + 8]
        body = payload[position + 8:position + 8 + size]
        if chunk_type == b"IDAT":
            body = transform(body)
        rewritten.extend(_png_chunk(chunk_type, body))
        position += size + 12
    return bytes(rewritten)


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


def make_statement_image(url: str):
    return StatementDocument(
        problem_code="1700A",
        contest_id="1700",
        index="A",
        source_url="https://codeforces.com/contest/1700/problem/A",
        source_kind="html",
        root=Node(
            kind="document",
            children=[Node(kind="image", attrs={"src": url, "alt": "diagram"})],
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


    def test_transparent_png_is_flattened_before_digest_for_all_content_kinds(self):
        payload = _grayscale_alpha_png([(0, 0), (0, 128), (9, 255)])
        policy = AssetPolicy(
            allow_raster=True,
            allow_pdf_attachment=False,
            max_bytes=1024,
        )
        cases = [
            (
                make_statement_image("https://codeforces.com/transparent.png"),
                "/statement-assets",
            ),
            (
                make_editorial_image("https://codeforces.com/transparent.png"),
                "/editorial-assets",
            ),
        ]

        with tempfile.TemporaryDirectory() as directory:
            basenames = []
            for source, route_prefix in cases:
                localized = localize_content_assets(
                    source,
                    generation_asset_dir=directory,
                    route_prefix=route_prefix,
                    fetcher=lambda _url: AssetFetchResult(payload, "image/png"),
                    policy=policy,
                )
                route = first_resource(localized).attrs["src"]
                basename = str(route).rsplit("/", 1)[-1]
                flattened = (Path(directory) / basename).read_bytes()

                self.assertEqual(flattened[24:26], b"\x08\x02")
                self.assertEqual(
                    _decoded_png_scanlines(flattened),
                    b"\x00\xff\xff\xff\x7f\x7f\x7f\x09\x09\x09",
                )
                self.assertEqual(
                    basename,
                    f"{hashlib.sha256(flattened).hexdigest()}.png",
                )
                self.assertNotEqual(
                    basename,
                    f"{hashlib.sha256(payload).hexdigest()}.png",
                )
                basenames.append(basename)

            self.assertEqual(basenames[0], basenames[1])


    def test_malformed_transparent_png_is_preserved_byte_for_byte(self):
        payload = _grayscale_alpha_png([(0, 0), (0, 128)])
        bad_crc = bytearray(payload)
        bad_crc[29] ^= 1
        cases = {
            "bad-crc": bytes(bad_crc),
            "missing-iend": payload[:-12],
            "unknown-critical-chunk": (
                payload[:-12] + _png_chunk(b"ABCD", b"") + payload[-12:]
            ),
            "trailing-zlib-data": _rewrite_png_idat(
                payload,
                lambda body: body + b"trailing",
            ),
        }
        policy = AssetPolicy(
            allow_raster=True,
            allow_pdf_attachment=False,
            max_bytes=1024,
        )

        for name, malformed in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                localized = localize_content_assets(
                    make_editorial_image("https://codeforces.com/transparent.png"),
                    generation_asset_dir=directory,
                    route_prefix="/editorial-assets",
                    fetcher=lambda _url, value=malformed: AssetFetchResult(
                        value,
                        "image/png",
                    ),
                    policy=policy,
                )
                expected_name = f"{hashlib.sha256(malformed).hexdigest()}.png"

                self.assertEqual(
                    first_resource(localized).attrs["src"],
                    f"/editorial-assets/{expected_name}",
                )
                self.assertEqual(
                    (Path(directory) / expected_name).read_bytes(),
                    malformed,
                )


    def test_16bit_partial_alpha_uses_full_sample_precision(self):
        expected_scanline = b"\x00\x00\x00\x00\xfe\xfe\xfe"
        payloads = {
            "rgba": _png_from_scanline(
                width=2,
                bit_depth=16,
                color_type=6,
                scanline=(
                    b"\x00"
                    + (0).to_bytes(2, "big") * 3
                    + (0xFFFE).to_bytes(2, "big")
                    + (0).to_bytes(2, "big") * 3
                    + (0x00FF).to_bytes(2, "big")
                ),
            ),
            "grayscale-alpha": _png_from_scanline(
                width=2,
                bit_depth=16,
                color_type=4,
                scanline=(
                    b"\x00"
                    + (0).to_bytes(2, "big")
                    + (0xFFFE).to_bytes(2, "big")
                    + (0).to_bytes(2, "big")
                    + (0x00FF).to_bytes(2, "big")
                ),
            ),
        }
        policy = AssetPolicy(
            allow_raster=True,
            allow_pdf_attachment=False,
            max_bytes=2048,
        )

        for name, payload in payloads.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                localized = localize_content_assets(
                    make_editorial_image("https://codeforces.com/transparent.png"),
                    generation_asset_dir=directory,
                    route_prefix="/editorial-assets",
                    fetcher=lambda _url, value=payload: AssetFetchResult(
                        value,
                        "image/png",
                    ),
                    policy=policy,
                )
                route = str(first_resource(localized).attrs["src"])
                flattened = Path(directory, route.rsplit("/", 1)[-1]).read_bytes()

                self.assertEqual(flattened[24:26], b"\x08\x02")
                self.assertEqual(_decoded_png_scanlines(flattened), expected_scanline)
                self.assertNotEqual(flattened, payload)
                self.assertEqual(
                    route,
                    "/editorial-assets/"
                    + hashlib.sha256(flattened).hexdigest()
                    + ".png",
                )

    def test_semantically_invalid_png_chunks_are_preserved(self):
        cases = {
            "trns-with-grayscale-alpha": _png_from_scanline(
                width=1,
                bit_depth=8,
                color_type=4,
                scanline=b"\x00\x00\x00",
                chunks_before_idat=((b"tRNS", b"\x00\x00"),),
            ),
            "trns-with-rgba": _png_from_scanline(
                width=1,
                bit_depth=8,
                color_type=6,
                scanline=b"\x00\x00\x00\x00\x00",
                chunks_before_idat=((b"tRNS", b"\x00\x00"),),
            ),
            "plte-with-grayscale": _png_from_scanline(
                width=1,
                bit_depth=8,
                color_type=0,
                scanline=b"\x00\x00",
                chunks_before_idat=(
                    (b"PLTE", b"\x00\x00\x00"),
                    (b"tRNS", b"\x00\x00"),
                ),
            ),
            "plte-with-grayscale-alpha": _png_from_scanline(
                width=1,
                bit_depth=8,
                color_type=4,
                scanline=b"\x00\x00\x00",
                chunks_before_idat=((b"PLTE", b"\x00\x00\x00"),),
            ),
            "invalid-plte-length": _png_from_scanline(
                width=1,
                bit_depth=8,
                color_type=6,
                scanline=b"\x00\x00\x00\x00\x00",
                chunks_before_idat=((b"PLTE", b"\x00\x00"),),
            ),
            "invalid-palette-entry-count": _png_from_scanline(
                width=1,
                bit_depth=1,
                color_type=3,
                scanline=b"\x00\x00",
                chunks_before_idat=(
                    (b"PLTE", b"\x00\x00\x00" * 3),
                    (b"tRNS", b"\x00"),
                ),
            ),
            "oversized-palette-trns": _png_from_scanline(
                width=1,
                bit_depth=1,
                color_type=3,
                scanline=b"\x00\x00",
                chunks_before_idat=(
                    (b"PLTE", b"\x00\x00\x00"),
                    (b"tRNS", b"\x00\xff"),
                ),
            ),
            "invalid-gray-trns-length": _png_from_scanline(
                width=1,
                bit_depth=8,
                color_type=0,
                scanline=b"\x00\x00",
                chunks_before_idat=((b"tRNS", b"\x00\x00\x00"),),
            ),
            "out-of-range-gray-trns": _png_from_scanline(
                width=1,
                bit_depth=8,
                color_type=0,
                scanline=b"\x00\x00",
                chunks_before_idat=((b"tRNS", b"\x01\x00"),),
            ),
            "invalid-rgb-trns-length": _png_from_scanline(
                width=1,
                bit_depth=8,
                color_type=2,
                scanline=b"\x00\x00\x00\x00",
                chunks_before_idat=((b"tRNS", b"\x00" * 7),),
            ),
            "out-of-range-rgb-trns": _png_from_scanline(
                width=1,
                bit_depth=8,
                color_type=2,
                scanline=b"\x00\x00\x00\x00",
                chunks_before_idat=((b"tRNS", b"\x01\x00\x00\x00\x00\x00"),),
            ),
        }
        policy = AssetPolicy(
            allow_raster=True,
            allow_pdf_attachment=False,
            max_bytes=2048,
        )

        for name, malformed in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                localized = localize_content_assets(
                    make_editorial_image("https://codeforces.com/transparent.png"),
                    generation_asset_dir=directory,
                    route_prefix="/editorial-assets",
                    fetcher=lambda _url, value=malformed: AssetFetchResult(
                        value,
                        "image/png",
                    ),
                    policy=policy,
                )
                expected_name = f"{hashlib.sha256(malformed).hexdigest()}.png"

                self.assertEqual(
                    first_resource(localized).attrs["src"],
                    f"/editorial-assets/{expected_name}",
                )
                self.assertEqual(
                    Path(directory, expected_name).read_bytes(),
                    malformed,
                )

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
