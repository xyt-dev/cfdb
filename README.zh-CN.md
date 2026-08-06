# CF Database (cfdb)

[English](README.md) · **中文**

全本地化的 Codeforces 题目数据库 + 内置网页。离线浏览全部 11335 道题、题面与题解——零 CDN、无需账号、局域网可用。

## 功能

- **全量题目元数据** — Codeforces API 抓取 11335 题（rating 800–3500、标签、解题数）→ `problems.json`
- **题面/题解全本地化** — 爬取为 Markdown 存入 `statements/` 与 `editorials/`；图片本地化；公式由本地 MathJax 渲染（SVG，离线）
- **动态 per-problem tutorial 补全** — 新版 editorial（2024+）的 JS 动态题解通过 `problemTutorial` API 自动补全
- **PDF 题面支持** — 无 HTML 题面的比赛（老 ACM 赛）经 `pdftotext` 提取
- **爬取智能** — 增量爬取 + 失败记忆（`failed_statements.json` / `failed_editorials.json`）、403 封禁感知、1s 限速
- **本地网页**（`index.html` 单文件）：
  - 按 rating / 解题数 / 标签 / 名称筛选；6 种排序（含 ID 降序）
  - 详情页「题面 / 题解」tab；题号标题链接到原题；右上角「打开 ↗」
  - **可折叠小节**（Hint / Solution / Tutorial / …）— 无框、主题联动
  - **公式渲染**：原始 LaTeX 四层保护链 + MathJax
  - **i18n**：默认英文，「文/En」按钮切中文——布局像素级稳定
  - **双主题**：Catppuccin Mocha / Gruvbox Dark（CSS 变量驱动）
  - 自己的解题代码展示在题面底部（`solutions/{题号}.{ext}`）
- **零 CDN** — marked、highlight.js、MathJax、CodeNewRoman Nerd Font 全部本地
- **局域网就绪** — 默认绑定 `0.0.0.0`，无鉴权

## 快速开始

```bash
python3 server.py
```

打开 <http://localhost:8765>（启动时打印局域网地址）。启动后自动刷新元数据并增量爬取缺失题面/题解（顶部进度条）。

端口覆盖：`CFDB_PORT=9000 python3 server.py`

## 手动爬取 / 更新

```bash
python3 update.py               # 刷新元数据 problems.json
python3 update.py --statements  # 全量预爬题面
python3 update.py --editorials  # 全量预爬题解
```

## 数据布局

```text
cfdb/
├── problems.json          # 元数据（git 忽略，可再生成）
├── statements/            # 题面 md + images/（git 忽略）
├── editorials/            # 题解 md + images/（git 忽略）
├── solutions/             # 自己的解题代码，{题号}.{ext}
├── server.py              # HTTP 服务 + 启动自动增量爬取
├── cfcrawl.py             # 爬取库（curl 反反爬 + md 生成）
├── html2md.py             # HTML → Markdown 转换器
├── update.py              # 数据更新 / 全量预爬
├── index.html             # 单页前端
├── vendor/                # 本地依赖：marked、highlight.js、MathJax、Nerd Font
├── CHANGELOG.md
└── failed_statements.json / failed_editorials.json   # 爬取失败记忆
```

## 说明

- 若 CF 临时封禁你的 IP（所有请求 403）：本地数据完全可用，服务器跳过爬取，下次启动自动重试
- Git 只跟踪代码；数据（`statements/`、`editorials/`、`problems.json`、失败记忆）在 .gitignore
- 完整历史见 [CHANGELOG.md](CHANGELOG.md)
