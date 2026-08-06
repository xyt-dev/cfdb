#!/usr/bin/env python3
"""cfdb 数据更新脚本：重新抓取 CF 全量题目元数据并合并为 problems.json

用法:
  python3 update.py                     # 刷新元数据 problems.json
  python3 update.py --statements        # 全量预爬题面 → statements/*.md
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
API_URL = "https://codeforces.com/api/problemset.problems"
UA = "Mozilla/5.0 (X11; Linux x86_64) cfdb-update/0.1"

def fetch() -> bytes:
    """调 curl 抓 API（静默），带重试"""
    for attempt in range(3):
        try:
            r = subprocess.run(
                ["curl", "-sL", "--max-time", "120",
                 "-H", f"User-Agent: {UA}",
                 API_URL],
                capture_output=True, timeout=150)
            if r.returncode == 0 and r.stdout:
                return r.stdout
        except Exception:
            pass
        print(f"⚠️ 第 {attempt + 1} 次抓取失败，重试...", file=sys.stderr)
        import time
        time.sleep(3 * (attempt + 1))
    print("❌ 抓取失败（网络问题或 CF 限流，稍后重试）", file=sys.stderr)
    sys.exit(1)

def main():
    print("▸ 抓取 Codeforces API...")
    raw = fetch()
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"❌ API 返回的不是合法 JSON: {e}", file=sys.stderr)
        sys.exit(1)
    if d.get("status") != "OK":
        print(f"❌ API 返回异常: {d.get('status')} {d.get('comment', '')}", file=sys.stderr)
        sys.exit(1)

    result = d["result"]
    stats = {f"{p['contestId']}{p['index']}": p["solvedCount"]
             for p in result.get("problemStatistics", [])}
    out = []
    for p in result.get("problems", []):
        key = f"{p['contestId']}{p['index']}"
        out.append({
            "id": key,
            "contestId": p["contestId"],
            "index": p["index"],
            "name": p["name"],
            "rating": p.get("rating"),
            "tags": p.get("tags", []),
            "solvedCount": stats.get(key, 0),
            "url": f"https://codeforces.com/contest/{p['contestId']}/problem/{p['index']}",
        })

    out_path = os.path.join(ROOT, "problems.json")
    tmp = out_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f)
    except OSError as e:
        print(f"❌ 写入 problems.json 失败: {e}", file=sys.stderr)
        sys.exit(1)
    os.replace(tmp, out_path)  # 原子替换，避免写一半

    rated = sum(1 for p in out if p["rating"])
    size = os.path.getsize(out_path) / 1024 / 1024
    print(f"✅ 更新完成: {len(out)} 题（有 rating: {rated}）| problems.json {size:.1f} MB")
    print("   服务器运行中会自动读取新数据（下次刷新页面生效）")

def main_crawl():
    """预爬模式: --statements 全量题面"""
    import cfcrawl
    delay = 0.4  # 请求间隔（秒），避免触发反爬
    print(f"▸ 全量预爬题面（间隔 {delay}s，可 Ctrl+C 中断续跑）...")
    total, cached, fetched = cfcrawl.fetch_all_statements(delay=delay)
    print(f"✅ 题面: 共 {total} | 缓存 {cached} | 新爬 {fetched}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--statements" in args:
        main_crawl()
    elif "--help" in args or "-h" in args:
        print(__doc__)
    else:
        main()
