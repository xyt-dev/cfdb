# <img src="vendor/cf-favicon.png" width="28" height="28" align="center" alt="Codeforces"> Codeforces Database (cfdb)

[English](README.md) · **中文**

![](vendor/screenshot.png)

全本地化的 Codeforces 题目数据库与内置网页。离线浏览 **11k+ 道题**、题面与题解——零 CDN、无需账号、局域网可用。

## 功能

- **全量题目元数据** — Codeforces API 提供的 11k+ 道题（rating 800–3500、标签、解题数）存入 `problems.json`。
- **题面路径不变** — 题面继续以 Markdown 存入 `statements/`，图片本地化并由本地 MathJax 渲染公式。
- **结构化题解 v2** — 题解解析为带类型的语义树，以规范 JSON 保存，再渲染为经过净化的 HTML。源码顺序、标题层级、嵌套 spoiler/列表/引用/表格、代码空白、图片和 TeX 与 Codeforces 保持语义一致。
- **按完整题号精确组合动态 tutorial** — 每个完整 `problemTutorial` 片段（含官方标题）只替换完整 Codeforces 题号完全相同的 slot。1700 按 A 标题/正文到 F 标题/正文组合，不使用首字母、顺序或文本推断兜底。
- **原子重建** — 文档和 manifest 先写入非活动代际；只有每场比赛均为 `ready` 或 `known_absent` 时，全量重建才通过一次原子指针替换激活。旧代际和 v1 Markdown 保留用于回滚。
- **只读题解 API** — `GET /api/editorial` 不抓取 Codeforces，也不修改缓存或失败记忆。v2 激活前返回旧 Markdown；激活后以 v2 manifest 为唯一依据，ready 文档返回净化 HTML。
- **本地网页**（`index.html` 单文件）— 筛选、排序、题面/题解 tab、本地解题代码、中英切换、双主题、离线 MathJax/高亮与复制按钮。
- **纵深防御** — 源 HTML 经过允许列表解析和渲染；结构化阅读器使用 `sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"`，禁止 `allow-same-origin`。
- **零 CDN、局域网就绪** — 浏览器资源全部 vendored，本地服务默认绑定 `0.0.0.0`，无鉴权。

## 快速开始

```bash
# 仅代码（无数据）——数据位于 snapshot 分支
git clone https://github.com/xyt-dev/cfdb.git
cd cfdb
python3 server.py          # 启动服务与后台增量更新

# 带完整数据快照：
git clone -b snapshot https://github.com/xyt-dev/cfdb.git
```

打开 <http://localhost:8765>（启动时会打印局域网地址）。

**运行依赖：** Python 3.10+、`curl`、`poppler-utils`（`pdftotext`，仅 PDF 题面需要）。运行时无需 pip、npm 或 Node.js；浏览器资源全部在 `vendor/`。

端口覆盖：`CFDB_PORT=9000 python3 server.py`

## 更新与题解运维

```bash
python3 update.py                              # 刷新 problems.json 元数据
python3 update.py --statements                 # 全量预爬 Markdown 题面
python3 update.py --validate-editorial 1700    # 在线构建/渲染验证，不激活
python3 update.py --editorials                 # v2 前爬旧版；v2 后建增量后继代际
python3 update.py --editorials --rebuild       # 可续跑的 v2 全量重建
```

`--validate-editorial` 使用临时本地图片目录，不改变活动代际。全量重建忽略 v1 缓存命中和失败记忆，按有界批次写入非活动代际，可续跑兼容的未完成全量代际；只要有比赛尚未达到终态，就不激活并以非零状态退出。v2 激活后，单独运行 `--editorials` 会从当前活动代际生成新后继，重新检查已知无题解和新增比赛，并且仅在完整时激活。

### 状态含义

- `ready` — 语义文档已验证，不含未解析 tutorial slot 或远程图片依赖。
- `known_absent` — 两次成功且可识别的比赛页面检查都未发现 editorial/tutorial 链接；每次新重建都会复查。
- `transient_failure` — 可重试的网络、限流、CSRF、响应或图片失败；阻止激活。
- `invalid_structure` — 解析、完整题号组合或语义验证失败；阻止激活。

### 缓存布局与回滚

```text
editorials/
├── *.md                              # 保留的旧版 v1 Markdown
├── images/                           # 共享的本地题解图片
└── v2/
    ├── current.json                  # 原子活动代际指针
    └── generations/<generation-id>/
        ├── manifest.json             # 比赛集合、状态、回执与摘要
        └── documents/<contestId>.json # 规范 schema-2 语义树
```

渲染 HTML 从规范 JSON 派生并由 `/api/editorial` 返回，不把源 HTML 写入缓存。当 `current.json` 记录了上一代际时，可原子重新激活它以回滚：

```bash
python3 - <<'PY'
import json
from editorial_cache import activate_generation
with open("editorials/v2/current.json", encoding="utf-8") as source:
    previous = json.load(source)["previousGenerationId"]
activate_generation("editorials/v2", previous)
PY
```

替代代际完成验证且回滚窗口关闭前，不要删除旧代际。

## API 与阅读器行为

活动 v2 内容的 `GET /api/editorial?contestId=1700` 返回 `format: "html"`、`schema: 2`、净化后的 `html`、Codeforces `url`、`known: true` 与 `status: "ready"`。确认无题解时返回空正文和 `status: "known_absent"`。没有任何 v2 指针前继续提供原有 `format: "markdown"` 响应；激活后不会逐比赛回退到 v1。

题面和旧版题解继续使用 Markdown 阅读路径。v2 HTML 绕过 Markdown 解析与标题归一化，再复用本地 MathJax、语法高亮、复制按钮、图片诊断、外链处理和 iframe 高度同步。

## 开发验证

Node.js 仅为可选的无依赖阅读器开发测试工具，不是运行依赖：

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/reader_payload.test.js
python3 -m py_compile editorial_model.py editorial_parser.py editorial_render.py editorial_cache.py editorial_rebuild.py cfcrawl.py server.py update.py
```

## 说明

- Codeforces 临时封禁 IP 时，当前活动本地数据仍可用；网络更新失败不会替换活动代际。
- 生成数据（`problems.json`、题面、题解、图片和失败记忆）属于快照/数据工作流，不应混入普通代码提交。
- 完整历史见 [CHANGELOG.md](CHANGELOG.md)。
