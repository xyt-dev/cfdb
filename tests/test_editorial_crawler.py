import errno
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cfcrawl import (
    _fsync_asset_directory,
    EditorialBuildResult,
    TutorialBatch,
    build_editorial_document,
    fetch_editorial_v2,
    localize_editorial_assets,
)
from editorial_cache import ContestStatus
from editorial_model import EditorialDocument, Node, validate_document


FIXTURES = Path(__file__).parent / "fixtures" / "editorials"
SOURCE_URL = "https://codeforces.com/blog/entry/103978"


def fixture(relative: str) -> str:
    return (FIXTURES / relative).read_text(encoding="utf-8")


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def plain_text(node) -> str:
    return (node.text or "") + "".join(plain_text(child) for child in node.children)


def ready_localizer(document: EditorialDocument) -> EditorialBuildResult:
    return EditorialBuildResult(ContestStatus.READY, document, {})


def tutorial_batch(*, missing_codes=(), transient_errors=()) -> TutorialBatch:
    expected = json.loads(fixture("1700/expected.json"))
    missing = set(missing_codes)
    return TutorialBatch(
        html_by_code={
            code: fixture(f"1700/tutorial-{code[-1]}.html")
            for code in expected["problemCodes"]
            if code not in missing
        },
        missing_codes=missing,
        transient_errors=list(transient_errors),
    )


def document_with_image(source: str) -> EditorialDocument:
    return EditorialDocument(
        contest_id="1700",
        source_url=SOURCE_URL,
        root=Node(
            kind="document",
            children=[Node(kind="image", attrs={"src": source, "alt": "diagram"})],
        ),
    )


class EditorialCrawlerTests(unittest.TestCase):
    def test_build_editorial_document_composes_1700_a_through_f(self):
        expected = json.loads(fixture("1700/expected.json"))

        def fetch_page(url: str) -> str:
            if url == "https://codeforces.com/contest/1700":
                return '<a href="/blog/entry/103978" title="Editorial">Editorial</a>'
            if url == SOURCE_URL:
                return fixture("1700/base.html")
            raise AssertionError(f"unexpected URL: {url}")

        def fetch_tutorial(code: str) -> dict[str, str]:
            letter = code.removeprefix("1700")
            return {"success": "true", "html": fixture(f"1700/tutorial-{letter}.html")}

        result = fetch_editorial_v2(
            "1700",
            fetch_page=fetch_page,
            fetch_tutorial=fetch_tutorial,
            asset_localizer=ready_localizer,
        )

        self.assertIs(result.status, ContestStatus.READY)
        self.assertIsNotNone(result.document)
        assert result.document is not None
        sections = [
            node for node in result.document.root.children if node.kind == "problem_section"
        ]
        self.assertEqual(
            [node.attrs["problemCode"] for node in sections],
            expected["problemCodes"],
        )
        for section, sentinel in zip(sections, expected["bodySentinels"]):
            self.assertIn(sentinel, plain_text(section))
        self.assertFalse(
            any(node.kind == "tutorial_slot" for node in walk(result.document.root))
        )

    def test_transient_tutorial_failure_returns_no_document(self):
        def fetch_page(url: str) -> str:
            if url.endswith("/contest/1700"):
                return '<a href="/blog/entry/103978" title="Tutorial">Tutorial</a>'
            return fixture("1700/base.html")

        def fetch_tutorial(code: str) -> dict[str, str]:
            if code == "1700C":
                raise OSError("temporary API failure")
            return {
                "success": "true",
                "html": fixture(f"1700/tutorial-{code[-1]}.html"),
            }

        result = fetch_editorial_v2(
            "1700",
            fetch_page=fetch_page,
            fetch_tutorial=fetch_tutorial,
            asset_localizer=ready_localizer,
        )

        self.assertIs(result.status, ContestStatus.TRANSIENT_FAILURE)
        self.assertIsNone(result.document)
        self.assertEqual(result.evidence["errors"], ["1700C:tutorial-request-failed"])

    def test_success_false_removes_only_exact_slot(self):
        responses = {
            code: (
                {"success": "false"}
                if code == "1700C"
                else {
                    "success": "true",
                    "html": fixture(f"1700/tutorial-{code[-1]}.html"),
                }
            )
            for code in ("1700A", "1700B", "1700C", "1700D", "1700E", "1700F")
        }

        def fetch_page(url: str) -> str:
            if url.endswith("/contest/1700"):
                return '<a href="/blog/entry/103978" title="Tutorial">Tutorial</a>'
            return fixture("1700/base.html")

        result = fetch_editorial_v2(
            "1700",
            fetch_page=fetch_page,
            fetch_tutorial=responses.__getitem__,
            asset_localizer=ready_localizer,
        )

        self.assertIs(result.status, ContestStatus.READY)
        assert result.document is not None
        sections = [
            node for node in result.document.root.children if node.kind == "problem_section"
        ]
        self.assertEqual(
            [node.attrs["problemCode"] for node in sections],
            ["1700A", "1700B", "1700D", "1700E", "1700F"],
        )
        self.assertEqual(
            [
                sentinel
                for node in sections
                for sentinel in [plain_text(node)]
            ],
            [
                "1700A - Optimal PathA_BODY_SENTINEL",
                "1700B - Palindromic NumbersB_BODY_SENTINEL",
                "1700D - River LocksD_BODY_SENTINEL",
                "1700E - Serega the PirateE_BODY_SENTINEL",
                "1700F - PuzzleF_BODY_SENTINEL",
            ],
        )
        self.assertIn(
            "tutorial-known-absent",
            [item.code for item in result.document.diagnostics],
        )

    def test_wrong_fragment_problem_code_is_invalid_structure(self):
        batch = tutorial_batch()
        batch.html_by_code["1700B"] = fixture("1700/tutorial-A.html")

        result = build_editorial_document(
            "1700",
            SOURCE_URL,
            fixture("1700/base.html"),
            batch,
            ready_localizer,
        )

        self.assertIs(result.status, ContestStatus.INVALID_STRUCTURE)
        self.assertIsNone(result.document)
        self.assertEqual(result.evidence["error"], "problem-code-mismatch:1700A:1700B")

    def test_localize_assets_rewrites_only_after_atomic_download(self):
        document = document_with_image("https://codeforces.com/images/diagram.png")

        def fetch_image(url: str) -> bytes:
            self.assertEqual(url, "https://codeforces.com/images/diagram.png")
            self.assertEqual(
                document.root.children[0].attrs["src"],
                "https://codeforces.com/images/diagram.png",
            )
            return b"PNG_ASSET_SENTINEL"

        with tempfile.TemporaryDirectory() as directory:
            result = localize_editorial_assets(
                document,
                image_dir=directory,
                image_fetcher=fetch_image,
            )
            target = Path(directory) / "1700_1.png"

            self.assertEqual(target.read_bytes(), b"PNG_ASSET_SENTINEL")
            self.assertIs(result.status, ContestStatus.READY)
            assert result.document is not None
            self.assertEqual(result.document.root.children[0].attrs["src"], "/eimages/1700_1.png")
            self.assertEqual(document.root.children[0].attrs["src"], "https://codeforces.com/images/diagram.png")
            self.assertEqual(result.document.assets, ["/eimages/1700_1.png"])

            blocked_directory = Path(directory) / "not-a-directory"
            blocked_directory.write_bytes(b"existing-file")
            blocked_document = document_with_image(
                "https://codeforces.com/images/blocked.png"
            )
            blocked = localize_editorial_assets(
                blocked_document,
                image_dir=str(blocked_directory),
                image_fetcher=lambda _url: b"NEVER_VISIBLE",
            )

            self.assertIs(blocked.status, ContestStatus.TRANSIENT_FAILURE)
            self.assertIsNone(blocked.document)
            self.assertEqual(blocked_directory.read_bytes(), b"existing-file")
            self.assertEqual(
                blocked_document.root.children[0].attrs["src"],
                "https://codeforces.com/images/blocked.png",
            )

    def test_preexisting_asset_is_refetched_and_atomically_replaced_before_rewrite(self):
        document = document_with_image("https://codeforces.com/images/diagram.png")

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "1700_1.png"
            target.write_bytes(b"CORRUPT_PREEXISTING_ASSET")

            def fetch_image(url: str) -> bytes:
                self.assertEqual(url, "https://codeforces.com/images/diagram.png")
                self.assertEqual(target.read_bytes(), b"CORRUPT_PREEXISTING_ASSET")
                self.assertEqual(
                    document.root.children[0].attrs["src"],
                    "https://codeforces.com/images/diagram.png",
                )
                return b"VERIFIED_REPLACEMENT_ASSET"

            result = localize_editorial_assets(
                document,
                image_dir=directory,
                image_fetcher=fetch_image,
            )

            self.assertIs(result.status, ContestStatus.READY)
            assert result.document is not None
            self.assertEqual(target.read_bytes(), b"VERIFIED_REPLACEMENT_ASSET")
            self.assertEqual(
                result.document.root.children[0].attrs["src"],
                "/eimages/1700_1.png",
            )
            self.assertEqual(
                sorted(path.name for path in Path(directory).iterdir()),
                ["1700_1.png"],
            )

    def test_directory_fsync_ignores_only_explicitly_unsupported_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            for error_number in (errno.EINVAL, errno.ENOTSUP):
                with self.subTest(operation="fsync", errno=error_number):
                    with patch(
                        "cfcrawl.os.fsync",
                        side_effect=OSError(error_number, "unsupported"),
                    ):
                        _fsync_asset_directory(directory)

            with patch(
                "cfcrawl.os.open",
                side_effect=OSError(errno.ENOTSUP, "unsupported"),
            ):
                _fsync_asset_directory(directory)

    def test_directory_fsync_propagates_real_durability_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            for error_number in (errno.EIO, errno.ENOSPC):
                with self.subTest(operation="fsync", errno=error_number):
                    with patch(
                        "cfcrawl.os.fsync",
                        side_effect=OSError(error_number, "durability failure"),
                    ):
                        with self.assertRaises(OSError) as raised:
                            _fsync_asset_directory(directory)
                    self.assertEqual(raised.exception.errno, error_number)

            with patch(
                "cfcrawl.os.open",
                side_effect=OSError(errno.EIO, "directory open failure"),
            ):
                with self.assertRaises(OSError) as raised:
                    _fsync_asset_directory(directory)
            self.assertEqual(raised.exception.errno, errno.EIO)

    def test_transient_image_failure_prevents_ready_document(self):
        document = document_with_image("https://codeforces.com/images/diagram.png")

        with tempfile.TemporaryDirectory() as directory:
            result = localize_editorial_assets(
                document,
                image_dir=directory,
                image_fetcher=lambda _url: None,
            )

            self.assertIs(result.status, ContestStatus.TRANSIENT_FAILURE)
            self.assertIsNone(result.document)
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertEqual(document.root.children[0].attrs["src"], "https://codeforces.com/images/diagram.png")
            errors = result.evidence["errors"]
            assert isinstance(errors, list) and isinstance(errors[0], dict)
            self.assertEqual(
                errors[0]["code"],
                "editorial-asset-transient-failure",
            )

    def test_confirmed_missing_image_becomes_missing_asset(self):
        document = document_with_image("https://codeforces.com/images/diagram.svg")

        with tempfile.TemporaryDirectory() as directory:
            result = localize_editorial_assets(
                document,
                image_dir=directory,
                image_fetcher=lambda _url: (_ for _ in ()).throw(
                    AssertionError("SVG must not be downloaded")
                ),
            )

            self.assertIs(result.status, ContestStatus.READY)
            assert result.document is not None
            self.assertEqual(result.document.root.children[0].kind, "missing_asset")
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertIn(
                "editorial-asset-unsupported",
                [item.code for item in result.document.diagnostics],
            )

    def test_encoded_and_mixed_case_svg_paths_are_confirmed_missing(self):
        sources = [
            "https://codeforces.com/images/direct.SvG",
            "https://codeforces.com/images/encoded%2eSvG",
            "https://codeforces.com/images/encoded-dot.%53%76%47",
        ]
        document = EditorialDocument(
            contest_id="1700",
            source_url=SOURCE_URL,
            root=Node(
                kind="document",
                children=[
                    Node(kind="image", attrs={"src": source, "alt": "diagram"})
                    for source in sources
                ],
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            result = localize_editorial_assets(
                document,
                image_dir=directory,
                image_fetcher=lambda _url: (_ for _ in ()).throw(
                    AssertionError("SVG must not be downloaded")
                ),
            )

            self.assertIs(result.status, ContestStatus.READY)
            assert result.document is not None
            self.assertEqual(
                [node.kind for node in result.document.root.children],
                ["missing_asset", "missing_asset", "missing_asset"],
            )
            self.assertEqual(
                [item.code for item in result.document.diagnostics],
                [
                    "editorial-asset-unsupported",
                    "editorial-asset-unsupported",
                    "editorial-asset-unsupported",
                ],
            )
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_ready_document_has_no_remote_image_source(self):
        source = (
            '<div class="ttypography">'
            '<div class="spoiler">'
            '<div class="spoiler-title">Proof '
            '<img src="https://codeforces.com/images/title.png" alt="title"></div>'
            '<div class="spoiler-content"><p>body</p></div>'
            '</div>'
            '<img src="https://codeforces.com/images/diagram.webp" alt="diagram">'
            '</div>'
        )

        with tempfile.TemporaryDirectory() as directory:
            result = build_editorial_document(
                "1700",
                SOURCE_URL,
                source,
                TutorialBatch({}, set(), []),
                lambda document: localize_editorial_assets(
                    document,
                    image_dir=directory,
                    image_fetcher=lambda _url: b"ASSET_SENTINEL",
                ),
            )

            self.assertIs(result.status, ContestStatus.READY)
            assert result.document is not None
            images = [node for node in walk(result.document.root) if node.kind == "image"]
            spoiler = next(node for node in walk(result.document.root) if node.kind == "spoiler")
            title_values = spoiler.attrs["title"]
            assert isinstance(title_values, list)
            title_nodes = []
            for value in title_values:
                assert isinstance(value, dict)
                title_nodes.append(Node.from_dict(value))
            title_images = [node for title in title_nodes for node in walk(title) if node.kind == "image"]
            all_images = [*title_images, *images]
            self.assertEqual(
                [node.attrs["src"] for node in all_images],
                ["/eimages/1700_1.png", "/eimages/1700_2.webp"],
            )
            self.assertEqual(validate_document(result.document, ready=True), [])
            self.assertFalse(
                any(
                    str(node.attrs.get("src", "")).startswith(("http://", "https://"))
                    for node in all_images
                )
            )


if __name__ == "__main__":
    unittest.main()
