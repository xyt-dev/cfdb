# <img src="vendor/cf-favicon.png" width="28" height="28" align="center" alt="Codeforces"> Codeforces Database (cfdb)

[English](README.md) · **中文**

![](vendor/screenshot.png)

全本地化的 Codeforces 题目数据库与内置网页。离线浏览 **11k+ 道题**、题面与题解——零 CDN、无需账号、局域网可用。

## 功能

- **全量题目元数据** — Codeforces API 提供的 11k+ 道题（rating 800–3500、标签、解题数）存入 `problems.json`。
- **结构化题面 v2** — 当前题面 DOM 或权威 PDF 转为带类型的规范 JSON；HTML 题面按语义渲染，PDF 保持为不可变本地附件。
- **结构化题解 v2** — 题解转为带类型的规范 JSON 与确定性净化 HTML，同时保留源码顺序、标题层级、嵌套 spoiler/列表/引用/表格、代码空白、图片和 TeX。
- **按完整题号精确组合动态 tutorial** — 每个完整 `problemTutorial` 片段（含官方标题）只替换完整 Codeforces 题号完全相同的 slot。1700 按 A 标题/正文到 F 标题/正文组合，不使用首字母、顺序或文本推断兜底。
- **独立原子代际** — 题面与题解分别拥有 manifest、指针、重建、激活、回滚和历史；只有完整且验证通过的代际会激活。
- **只读 v2 API** — 题面与题解 GET 请求均无网络、无写入；缺少指针时返回 HTTP 503，运行时绝不读取或提供旧版 Markdown。
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

## 更新与内容运维

```bash
python3 update.py                              # 刷新 problems.json 元数据
python3 update.py --validate-statement 1700A  # 验证单道题面，不激活
python3 update.py --validate-editorial 1700    # 验证单场题解，不激活
python3 update.py --statements --rebuild       # 显式全量重建题面代际
python3 update.py --editorials --rebuild       # 显式全量重建题解代际
python3 update.py --statements                 # 生成题面增量后继代际
python3 update.py --editorials                 # 生成题解增量后继代际
```

只有显式 `--rebuild` 可以创建首个代际。普通题面或题解增量命令要求各自存在活动 `current.json` 指针；对应内容根尚未初始化时以非零状态退出。服务器启动遵循同一规则：两个内容根独立增量更新，缺少指针的根会被跳过，不会隐式启动内容爬取。

两种验证模式都使用临时本地资源且绝不激活代际。全量重建按有界批次写入非活动代际，只在完整且验证通过时激活。增量命令从各自活动代际播种后继；题面与题解独立激活、回滚并保留历史。

### 状态含义

- `ready` — 语义文档已验证，且所有资源均为本地资源。
- `known_absent` — 按对应来源策略完成权威检查并确认内容不存在。
- `transient_failure` — 可重试的网络、限流、响应或资源失败；阻止激活。
- `invalid_structure` — 解析、精确身份组合或语义验证失败；阻止激活。

### 缓存布局与回滚

```text
statements/v2/                         editorials/v2/
├── current.json                       ├── current.json
└── generations/<generation-id>/       └── generations/<generation-id>/
    ├── manifest.json                      ├── manifest.json
    ├── documents/<problemCode>.json       ├── documents/<contestId>.json
    └── assets/<sha256>.<ext>               └── assets/<sha256>.<ext>
```

规范 JSON 是缓存的唯一内容真源；渲染 HTML 在读取时派生，不作为规范内容存储。已完成代际和内容寻址资源不可变。当 `current.json` 记录上一代际时，可用通用缓存 API 原子重新激活对应根：

```bash
python3 - <<'PY'
import json
from content_cache import activate_generation
root = "editorials/v2"
with open(f"{root}/current.json", encoding="utf-8") as source:
    previous = json.load(source)["previousGenerationId"]
activate_generation(root, previous)
PY
```

替代代际完成验证且回滚窗口关闭前，不要删除旧代际。

## API 与阅读器行为

`GET /api/statement?contestId=1700&index=A` 与 `GET /api/editorial?contestId=1700` 只提供 v2 内容。就绪响应包含 `format: "html"`、`contentKind`、`schema: 2`、净化后的 `html`、精确 Codeforces `url` 与 `status: "ready"`。确认不存在时返回空正文和 `status: "known_absent"`。

某个内容根没有活动指针时，对应接口返回 HTTP 503 与 `status: "v2_not_initialized"`。运行时不存在 Markdown、请求期爬取或逐项旧版回退。阅读器把服务器渲染的 HTML 直接送入沙箱 iframe，在保留语义层级的同时复用本地 MathJax、语法高亮、复制控件、图片诊断和高度同步。仅 PDF 的题面以不可变本地附件链接在新浏览上下文中打开。

## 开发验证

Node.js 仅为可选的无依赖阅读器开发测试工具：

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/reader_payload.test.js
python3 -m py_compile content_model.py content_parser.py content_render.py content_cache.py content_codecs.py statement_model.py statement_parser.py statement_crawl.py statement_rebuild.py editorial_model.py editorial_parser.py editorial_rebuild.py cfcrawl.py server.py update.py
```

## 说明

- Codeforces 临时封禁 IP 时，当前活动本地数据仍可用；网络更新失败不会替换活动代际。
- 生成的元数据与 v2 代际/资源属于快照数据工作流，不应混入普通代码提交。旧版 v1 文件可作为惰性历史数据保留在快照中，但绝不是运行时输入。
- 完整历史见 [CHANGELOG.md](CHANGELOG.md)。
