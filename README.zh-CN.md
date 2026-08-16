# <img src="vendor/cf-favicon.png" width="28" height="28" align="center" alt="Codeforces"> Codeforces Database (cfdb)

[English](README.md) · **中文**

![](vendor/screenshot.png)

全本地化的 Codeforces 题目数据库与内置网页。无需 CDN 或账号，即可浏览 11k+ 道题、题面、题解和自己的解题代码。

## 功能

- **带类型的题面 IR** — 当前 Codeforces 题面 DOM 或权威 PDF 原始字节转换为规范 `StatementDocument` JSON；PDF 保持为 SHA-256 寻址的不可变本地附件。
- **带类型的题解 IR** — 当前 blog/tutorial DOM 转换为规范 `EditorialDocument` JSON 与确定性净化 HTML，并保留层级、spoiler、列表、表格、引用、代码、TeX 和图片。
- **按完整题号精确组合 tutorial** — 每个完整 `problemTutorial` 片段只替换完整 `problemCode` 完全相同的 slot，不使用首字母、标题文本、顺序或占位符兜底。
- **逐项即时发布** — 单道题面或单场题解完成验证并原子写入后立即可读，不存在全库发布门槛。
- **自动从空目录启动** — 服务器启动后刷新元数据，并发启动题面和题解爬虫；即使两个存储均为空也不会跳过。每爬好一个即可立即查看，无需重启。
- **只读内容 API** — GET 请求不爬取、不联网、不写文件，运行时绝不读取旧版 Markdown。
- **安全渲染** — 源 HTML 经过允许列表解析和渲染；阅读器 iframe 严格使用 `sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"`，不允许 `allow-same-origin`。
- **本地网页** — 支持筛选、排序、题面/题解 tab、本地解题代码、中英切换、主题、离线 MathJax/高亮和复制控件。

## 快速开始

```bash
git clone https://github.com/xyt-dev/cfdb.git
cd cfdb
python3 server.py
```

打开 <http://localhost:8765>。服务器会打印局域网地址，并在后台同时启动两个缺失内容爬虫；每个完成项都会立即可用。

运行依赖为 Python 3.10+ 与 `curl`。无需 pip、npm、Node.js 运行时、`pdftotext` 或 CDN；浏览器资源均位于 `vendor/`。

端口覆盖：`CFDB_PORT=9000 python3 server.py`。

## 内容操作

```bash
python3 update.py                              # 刷新 problems.json 元数据
python3 update.py --statements                 # 爬取缺失/失败题面
python3 update.py --editorials                 # 爬取缺失/失败题解
python3 update.py --statements --rebuild       # 强制刷新每道题面
python3 update.py --editorials --rebuild       # 强制刷新每场题解
python3 update.py --validate-statement 1700A  # 在临时存储验证单道题面
python3 update.py --validate-editorial 1700    # 在临时存储验证单场题解
```

普通更新命令可以直接初始化空存储。`--rebuild` 表示“尝试元数据中的每一项”，不是“等待全库一起发布”：每个成功替代项独立即时发布。刷新期间，旧的有效文档会一直可读，直到新文档成功写入；确认不存在时只删除对应的过期单项。

网页端 **Rebuild** 明确定义为重试当前跳过的单项：确认对话框用两个可滚动 Tab 分别列出题面和题解、显示数量，并且每一项都可点击跳转。点击确认后，完整预览快照会重新加入对应的内存队列，因此列表会清空；已就绪和已确认不存在的单项保持不变，正在运行的启动爬取会合并而不会重复启动。CLI `--rebuild` 仍是显式强制刷新命令。

阅读器打开可重试但尚不可用的内容时，会单独发送严格的 `POST /api/prioritize` 提示；题面和题解 GET API 仍保持只读。最近一次点击会移到对应内存队列前端：题面在下一次抓取前选中，题解在下一批 8 项形成时选中；已就绪和已确认不存在的内容不会发送提示。

### 单项状态

- `ready` — 规范文档已验证，且所有引用资源均为本地、摘要有效。
- `pending` — 该单项尚未爬取；API 返回 HTTP 202，阅读器会自动重试。
- `known_absent` — 按对应来源策略确认该单项内容不存在。
- `transient_failure` — 可重试的网络、限流、响应或资源失败；下次更新会重试。
- `invalid_structure` — 解析、精确身份组合、规范验证或本地资源验证失败。

## 存储布局

```text
statements/v2/                         editorials/v2/
├── documents/<problemCode>.json       ├── documents/<contestId>.json
├── assets/<sha256>.<ext>              ├── assets/<sha256>.<ext>
└── status/<problemCode>.json          └── status/<contestId>.json
```

文档是否存在就是可见性的唯一依据。先写资源，再 fsync 规范文档 JSON，最后原子重命名到稳定路径。状态 sidecar 只记录不存在或失败尝试，绝不阻止有效文档读取。爬取结束后会删除未被引用的内容寻址资源。临时 `crawl.lock` 只用于排除同一根目录的并发写者。

## API 与阅读器行为

- `GET /api/statement?contestId=1700&index=A`
- `GET /api/editorial?contestId=1700`
- `GET /statement-assets/<sha256>.<ext>`
- `GET /editorial-assets/<sha256>.<ext>`
- `GET /api/progress`

就绪响应包含 `format: "html"`、`contentKind`、`schema: 2`、确定性净化后的 `html`、精确 Codeforces `url` 与 `status: "ready"`。确认不存在时正文为空且状态为 `known_absent`。尚未爬取时状态为 `pending`；不存在全局初始化错误，也不存在 Markdown 回退。

已运行的服务器会立即看到每个原子发布的文档。仅 PDF 的题面以不可变本地附件链接在新浏览上下文中打开。

## 开发验证

Node.js 仅用于可选的无依赖阅读器测试：

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/reader_payload.test.js
python3 -m py_compile content_model.py content_parser.py content_render.py content_cache.py content_codecs.py crawl_priority.py statement_model.py statement_parser.py statement_crawl.py statement_rebuild.py editorial_model.py editorial_parser.py editorial_rebuild.py cfcrawl.py server.py update.py
```

## 说明

- Codeforces 或网络临时失败不会隐藏已经发布的有效单项；失败项仍会重试。
- 生成的直接存储文档与资源属于数据工作流；`problems.json` 与 `solutions/` 相互独立。
- 完整历史见 [CHANGELOG.md](CHANGELOG.md)。
