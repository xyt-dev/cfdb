#!/usr/bin/env python3
"""Update cfdb metadata, statements, and editorial caches."""

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import subprocess
import sys

import cfcrawl
from editorial_rebuild import rebuild_editorials, update_editorials, validate_editorial
from statement_rebuild import rebuild_statements, update_statements, validate_statement


ROOT = os.path.dirname(os.path.abspath(__file__))
API_URL = "https://codeforces.com/api/problemset.problems"
UA = "Mozilla/5.0 (X11; Linux x86_64) cfdb-update/0.1"


def fetch() -> bytes:
    """Fetch the Codeforces API with the existing retry policy."""
    for attempt in range(3):
        try:
            result = subprocess.run(
                [
                    "curl", "-sL", "--max-time", "120",
                    "-H", f"User-Agent: {UA}", API_URL,
                ],
                capture_output=True,
                timeout=150,
            )
            if result.returncode == 0 and result.stdout:
                head = result.stdout[:256].lstrip()
                if head.startswith(b"<") or b"403 Forbidden" in head:
                    print(
                        "❌ CF 拒绝访问（403，IP 可能被临时封禁/限流）。"
                        "本地数据不受影响；请稍后（数小时）再试。",
                        file=sys.stderr,
                    )
                    raise RuntimeError("Codeforces rejected the metadata request")
                return result.stdout
        except RuntimeError:
            raise
        except Exception:
            pass
        print(f"⚠️ 第 {attempt + 1} 次抓取失败，重试...", file=sys.stderr)
        import time
        time.sleep(3 * (attempt + 1))
    raise RuntimeError("metadata fetch failed")


def update_metadata() -> int:
    print("▸ 抓取 Codeforces API...")
    try:
        raw = fetch()
        data = json.loads(raw)
    except (RuntimeError, json.JSONDecodeError) as error:
        print(f"❌ 元数据更新失败: {error}", file=sys.stderr)
        return 1
    if data.get("status") != "OK":
        print(
            f"❌ API 返回异常: {data.get('status')} {data.get('comment', '')}",
            file=sys.stderr,
        )
        return 1

    result = data["result"]
    stats = {
        f"{problem['contestId']}{problem['index']}": problem["solvedCount"]
        for problem in result.get("problemStatistics", [])
    }
    output = []
    for problem in result.get("problems", []):
        key = f"{problem['contestId']}{problem['index']}"
        output.append(
            {
                "id": key,
                "contestId": problem["contestId"],
                "index": problem["index"],
                "name": problem["name"],
                "rating": problem.get("rating"),
                "tags": problem.get("tags", []),
                "solvedCount": stats.get(key, 0),
                "url": (
                    f"https://codeforces.com/contest/{problem['contestId']}"
                    f"/problem/{problem['index']}"
                ),
            }
        )

    output_path = os.path.join(ROOT, "problems.json")
    temporary = output_path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as output_file:
            json.dump(output, output_file)
        os.replace(temporary, output_path)
    except OSError as error:
        try:
            os.remove(temporary)
        except OSError:
            pass
        print(f"❌ 写入 problems.json 失败: {error}", file=sys.stderr)
        return 1

    rated = sum(1 for problem in output if problem["rating"])
    size = os.path.getsize(output_path) / 1024 / 1024
    print(
        f"✅ 更新完成: {len(output)} 题（有 rating: {rated}）"
        f"| problems.json {size:.1f} MB"
    )
    print("   服务器运行中会自动读取新数据（下次刷新页面生效）")
    return 0


def main_crawl() -> int:
    """Run the existing full statement pre-crawl."""
    delay = 0.4
    print(f"▸ 全量预爬题面（间隔 {delay}s，可 Ctrl+C 中断续跑）...")
    total, cached, fetched = cfcrawl.fetch_all_statements(delay=delay)
    print(f"✅ 题面: 共 {total} | 缓存 {cached} | 新爬 {fetched}")
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh cfdb metadata or update v2 content generations.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--statements",
        action="store_true",
        help="incrementally update statements, or rebuild with --rebuild",
    )
    modes.add_argument(
        "--editorials",
        action="store_true",
        help="incrementally update editorials, or rebuild with --rebuild",
    )
    modes.add_argument(
        "--validate-statement",
        metavar="PROBLEM_CODE",
        help="build and validate one statement without activation",
    )
    modes.add_argument(
        "--validate-editorial",
        metavar="CONTEST_ID",
        help="build and validate one editorial without activation",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="with --statements or --editorials, build a full v2 generation",
    )
    return parser


def _report_exit(report: Mapping[str, object], success_key: str) -> int:
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    success = report.get(success_key)
    return 0 if isinstance(success, bool) and success else 1


def _require_pointer(root: Path, content_kind: str, rebuild_command: str) -> bool:
    if (root / "current.json").is_file():
        return True
    print(
        f"❌ {content_kind} v2 is not initialized; run: {rebuild_command}",
        file=sys.stderr,
    )
    return False


def main(argv=None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    if args.rebuild and not (args.statements or args.editorials):
        parser.error("--rebuild requires --statements or --editorials")

    if args.validate_statement is not None:
        return _report_exit(validate_statement(args.validate_statement), "ok")
    if args.validate_editorial is not None:
        return _report_exit(validate_editorial(args.validate_editorial), "ok")
    if args.statements:
        if args.rebuild:
            return _report_exit(rebuild_statements(), "activated")
        root = Path(cfcrawl.STATEMENT_DIR) / "v2"
        if not _require_pointer(
            root,
            "statement",
            "python3 update.py --statements --rebuild",
        ):
            return 1
        return _report_exit(update_statements(), "activated")
    if args.editorials:
        if args.rebuild:
            return _report_exit(rebuild_editorials(), "activated")
        root = Path(cfcrawl.EDITORIAL_DIR) / "v2"
        if not _require_pointer(
            root,
            "editorial",
            "python3 update.py --editorials --rebuild",
        ):
            return 1
        return _report_exit(update_editorials(), "activated")
    return update_metadata()


if __name__ == "__main__":
    raise SystemExit(main())
