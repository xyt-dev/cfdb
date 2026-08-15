import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
FORBIDDEN_CONTENT_SYMBOLS = {
    "FAILED_EDITORIALS",
    "_embed_images",
    "_flatten_transparent_png",
    "_fsync_asset_directory",
    "_prepare_editorial_asset_payload",
    "_atomic_write_editorial_asset",
    "_mix",
    "main_crawl",
    "_fetch_statement_pdf",
    "_load_failed_editorials",
    "_remember_failed_editorial",
    "_replace_tutorial",
    "editorial_to_md",
    "fetch_all_editorials",
    "fetch_all_statements",
    "fetch_editorial_md",
    "fetch_statement_md",
    "problem_statement_to_md",
    "read_editorial_md",
    "read_statement_md",
}


def project_identifiers(names: set[str]) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {}
    for path in ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                candidate = node.name
            elif isinstance(node, ast.Name):
                candidate = node.id
            elif isinstance(node, ast.Attribute):
                candidate = node.attr
            else:
                continue
            if candidate in names:
                found.add(candidate)
        if found:
            matches[path.name] = sorted(found)
    return matches


class V2OnlySourceTests(unittest.TestCase):
    def test_v2_source_has_no_legacy_content_symbols(self):
        self.assertEqual(project_identifiers(FORBIDDEN_CONTENT_SYMBOLS), {})


    def test_runtime_source_has_no_legacy_modules_routes_or_reader_keys(self):
        paths = [*ROOT.glob("*.py"), ROOT / "index.html", ROOT / "reader_payload.js"]
        forbidden = (
            "editorial_cache",
            "html2md",
            "/eimages/",
            "/images/",
            "marked.min.js",
            "preCrawling",
        )
        matches = {}
        for path in paths:
            source = path.read_text(encoding="utf-8")
            found = [value for value in forbidden if value in source]
            if found:
                matches[path.name] = found
        self.assertEqual(matches, {})

    def test_legacy_content_modules_and_regression_test_are_deleted(self):
        for relative_path in (
            "html2md.py",
            "editorial_cache.py",
            "tests/test_legacy_editorial_crawler.py",
        ):
            with self.subTest(path=relative_path):
                self.assertFalse((ROOT / relative_path).exists())


if __name__ == "__main__":
    unittest.main()
