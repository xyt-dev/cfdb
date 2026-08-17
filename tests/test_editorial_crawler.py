import hashlib
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from cfcrawl import (
    EditorialBuildResult,
    TutorialBatch,
    build_editorial_document,
    fetch_editorial_v2,
    localize_editorial_assets,
    _fetch_problem_tutorial_fragments,
)
from content_cache import (  # pyright: ignore[reportMissingImports]
    ContentStatus as ContestStatus,
    ContentStore,
)
from editorial_model import EditorialDocument, Node, validate_document
from content_codecs import EDITORIAL_CODEC  # pyright: ignore[reportMissingImports]


FIXTURES = Path(__file__).parent / "fixtures" / "editorials"
SOURCE_URL = "https://codeforces.com/blog/entry/103978"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


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
    def test_tutorial_bootstrap_uses_problem_from_requested_contest(self):
        commands = []

        def run(command, **_kwargs):
            commands.append(command)
            if command[-1].startswith("https://codeforces.com/contest/"):
                return SimpleNamespace(
                    returncode=0,
                    stdout=b"<html data-csrf='0123456789abcdef0123456789abcdef'>",
                )
            return SimpleNamespace(returncode=0, stdout=b'{"success": false}')

        with patch("cfcrawl.subprocess.run", side_effect=run), patch(
            "cfcrawl.time.sleep"
        ):
            batch = _fetch_problem_tutorial_fragments(
                "1129",
                ["1130A", "1129A2"],
            )

        self.assertEqual(
            commands[0][-1],
            "https://codeforces.com/contest/1129/problem/A2",
        )
        self.assertEqual(batch.missing_codes, {"1130A", "1129A2"})
        self.assertEqual(batch.transient_errors, [])

    def test_tutorial_bootstrap_falls_back_to_contest_page_for_alias_codes(self):
        commands = []

        def run(command, **_kwargs):
            commands.append(command)
            if command[-1].startswith("https://codeforces.com/contest/"):
                return SimpleNamespace(
                    returncode=0,
                    stdout=b"<html data-csrf='0123456789abcdef0123456789abcdef'>",
                )
            return SimpleNamespace(returncode=0, stdout=b'{"success": false}')

        with patch("cfcrawl.subprocess.run", side_effect=run), patch(
            "cfcrawl.time.sleep"
        ):
            batch = _fetch_problem_tutorial_fragments(
                "1784",
                ["1786A2", "1785A"],
            )

        self.assertEqual(
            commands[0][-1],
            "https://codeforces.com/contest/1784",
        )
        self.assertEqual(batch.missing_codes, {"1786A2", "1785A"})
        self.assertEqual(batch.transient_errors, [])
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

    def test_asset_download_does_not_change_document_until_atomic_publish(self):
        source_url = "https://codeforces.com/images/shared-slot.png"
        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory) / "v2"
            store = ContentStore.initialize(cache_root, EDITORIAL_CODEC)
            initial_result = localize_editorial_assets(
                document_with_image(source_url),
                image_dir=str(store.assets_path),
                image_fetcher=lambda _url: PNG_MAGIC + b"INITIAL_IMAGE_A",
            )
            self.assertIs(initial_result.status, ContestStatus.READY)
            assert initial_result.document is not None
            initial_route = initial_result.document.root.children[0].attrs["src"]
            initial_asset = store.assets_path / Path(initial_route).name
            store.publish(initial_result.document)

            replacement_result = localize_editorial_assets(
                document_with_image(source_url),
                image_dir=str(store.assets_path),
                image_fetcher=lambda _url: PNG_MAGIC + b"REPLACEMENT_IMAGE_B",
            )
            self.assertIs(replacement_result.status, ContestStatus.READY)
            assert replacement_result.document is not None
            replacement_route = replacement_result.document.root.children[0].attrs["src"]
            replacement_asset = store.assets_path / Path(replacement_route).name

            still_published = store.load_document("1700")
            self.assertEqual(
                still_published.root.children[0].attrs["src"],
                initial_route,
            )
            self.assertNotEqual(replacement_route, initial_route)
            self.assertEqual(initial_asset.read_bytes(), PNG_MAGIC + b"INITIAL_IMAGE_A")
            self.assertEqual(
                replacement_asset.read_bytes(),
                PNG_MAGIC + b"REPLACEMENT_IMAGE_B",
            )

            store.publish(replacement_result.document)
            published = store.load_document("1700")
            self.assertEqual(
                published.root.children[0].attrs["src"],
                replacement_route,
            )
    def test_localize_assets_rewrites_only_after_atomic_download(self):
        document = document_with_image("https://codeforces.com/images/diagram.png")

        def fetch_image(url: str) -> bytes:
            self.assertEqual(url, "https://codeforces.com/images/diagram.png")
            self.assertEqual(
                document.root.children[0].attrs["src"],
                "https://codeforces.com/images/diagram.png",
            )
            return PNG_MAGIC + b"PNG_ASSET_SENTINEL"

        with tempfile.TemporaryDirectory() as directory:
            result = localize_editorial_assets(
                document,
                image_dir=directory,
                image_fetcher=fetch_image,
            )
            digest = hashlib.sha256(PNG_MAGIC + b"PNG_ASSET_SENTINEL").hexdigest()
            route = f"/editorial-assets/{digest}.png"
            target = Path(directory) / Path(route).name

            self.assertEqual(target.read_bytes(), PNG_MAGIC + b"PNG_ASSET_SENTINEL")
            self.assertIs(result.status, ContestStatus.READY)
            assert result.document is not None
            self.assertEqual(result.document.root.children[0].attrs["src"], route)
            self.assertEqual(document.root.children[0].attrs["src"], "https://codeforces.com/images/diagram.png")
            self.assertEqual(result.document.assets, [route])

            blocked_directory = Path(directory) / "not-a-directory"
            blocked_directory.write_bytes(b"existing-file")
            blocked_document = document_with_image(
                "https://codeforces.com/images/blocked.png"
            )
            blocked = localize_editorial_assets(
                blocked_document,
                image_dir=str(blocked_directory),
                image_fetcher=lambda _url: PNG_MAGIC + b"NEVER_VISIBLE",
            )

            self.assertIs(blocked.status, ContestStatus.TRANSIENT_FAILURE)
            self.assertIsNone(blocked.document)
            self.assertEqual(blocked_directory.read_bytes(), b"existing-file")
            self.assertEqual(
                blocked_document.root.children[0].attrs["src"],
                "https://codeforces.com/images/blocked.png",
            )

    def test_preexisting_digest_mismatch_is_not_replaced(self):
        document = document_with_image("https://codeforces.com/images/diagram.png")

        with tempfile.TemporaryDirectory() as directory:
            payload = PNG_MAGIC + b"VERIFIED_REPLACEMENT_ASSET"
            digest = hashlib.sha256(payload).hexdigest()
            name = f"{digest}.png"
            target = Path(directory) / name
            target.write_bytes(b"CORRUPT_PREEXISTING_ASSET")

            result = localize_editorial_assets(
                document,
                image_dir=directory,
                image_fetcher=lambda _url: payload,
            )

            self.assertIs(result.status, ContestStatus.TRANSIENT_FAILURE)
            self.assertIsNone(result.document)
            self.assertEqual(target.read_bytes(), b"CORRUPT_PREEXISTING_ASSET")
            self.assertEqual(
                sorted(path.name for path in Path(directory).iterdir()),
                [name],
            )


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
                    image_fetcher=lambda _url: PNG_MAGIC + b"ASSET_SENTINEL",
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
            digest = hashlib.sha256(PNG_MAGIC + b"ASSET_SENTINEL").hexdigest()
            self.assertEqual(
                [node.attrs["src"] for node in all_images],
                [
                    f"/editorial-assets/{digest}.png",
                    f"/editorial-assets/{digest}.png",
                ],
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
