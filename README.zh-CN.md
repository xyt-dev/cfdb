# <img src="vendor/cf-favicon.png" width="28" height="28" align="center" alt="Codeforces"> Codeforces Database (cfdb)

[English](README.md) · **中文**

![](vendor/screenshot.png)

全本地化的 Codeforces 题目数据库 + 内置网页。离线浏览 **11k+ 道题**、题面与题解——零 CDN、无需账号、局域网可用。

## 功能

- **全量题目元数据** — Codeforces API 抓取 11k+ 题（rating 800–3500、标签、解题数）→ `problems.json`
- **题面/题解全本地化** — 爬取为 Markdown 存入 `statements/` 与 `editorials/`；图片本地化；公式由本地 MathJax 渲染（SVG，离线）
- **动态 per-problem tutorial 补全** — 新版 editorial（2024+）的 JS 动态题解通过 `problemTutorial` API 自动补全
- **PDF 题面支持** — 无 HTML 题面的比赛（老 ACM 赛）经 `pdftotext` 提取
- **爬取智能** — 增量爬取 + 失败记忆（`failed_statements.json` / `failed_editorials.json`）、假题解防线（403 页/占位残留绝不写入）、403 封禁感知、分批并发爬取（8 并发）
- **本地网页**（`index.html` 单文件）：
  - 按 rating / 解题数 / 标签 / 名称筛选；6 种排序（含 ID 降序）
  - 详情页「题面 / 题解」tab；题号标题链接到原题；「打开 ↗」链接
  - **可折叠小节**（Hint / Solution / Tutorial / …）— 无框、主题联动
  - **一键复制按钮**（样例框/代码块，Nerd Font 图标，任何浏览器可用）
  - **公式渲染**：原始 LaTeX 四层保护链 + MathJax
  - **i18n**：默认英文，「文/En」按钮切中文——布局像素级稳定
  - **双主题**：Catppuccin Mocha / Gruvbox Dark（CSS 变量驱动，默认 gruvbox）
  - 自己的解题代码展示在题面底部（`solutions/{题号}.{ext}`）
- **零 CDN** — marked、highlight.js、MathJax、CodeNewRoman Nerd Font 全部本地
- **局域网就绪** — 默认绑定 `0.0.0.0`，无鉴权

## 快速开始

```bash
# 仅代码（无数据）——数据在 snapshot 分支
git clone https://github.com/xyt-dev/cfdb.git
cd cfdb
python3 server.py          # 启动（启动时自动增量爬取）

# 带完整数据快照（statements/editorials/images，tag snapshot2026.8）：
git clone -b snapshot https://github.com/xyt-dev/cfdb.git
```

打开 <http://localhost:8765>（启动时打印局域网地址）。

**依赖**：Python 3.10+、`curl`、`poppler-utils`（`pdftotext`，仅 PDF 题面需要）。前端资源全部在 `vendor/`——无需 npm / pip install。

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
