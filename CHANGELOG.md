# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式。

## [Unreleased]

### 新增

- **PDF 题面爬取**：CF 部分比赛（ACM 老赛 1089/1090/1181 等）无 HTML 题面、只提供 PDF → 探测 content-type 后下载 + `pdftotext` 提取转 md（标注「仅 PDF 题面，公式可能失真」，标题从 `Problem X. Name` 行提取）

### 修复

- **cfcrawl 参数校验拒绝数字题号**：`fetch_statement_md` 的 `idx.isalpha()` 与 server 同 bug（A1/B2/F1 → 直接失败）→ 改字母开头 + alnum；**264 个 failed_statements 全部重爬恢复（0 失败）**：171 个 isalpha bug + 73 个 PDF 题面 + 2 个限流/超时

## [v2.0.0] - 2026-08-06

### 新增

- **筛选状态持久化到 URL 参数**：`?rmin=1400&rmax=1900&tags=dp,graphs&page=3`——刷新/滚动/快速刷新天然保留（无写入竞态），且筛选条件可分享链接
- **「↺ 恢复默认」按钮**：迷你样式，悬浮于筛选栏右上角，一键清除全部筛选并重置页码
- **浏览器前进/后退支持**：详情页使用 `pushState` + `popstate`，「← 返回」按钮与浏览器后退行为完全一致（含无历史时兜底）
- **题面图片爬取**：题面内 `<img>` 识别（过滤头像/国旗/站点装饰图），按题号命名保存至 `statements/images/{题号}_{n}.png`，md 内正确位置渲染（`/images/` 路由）
- **题解图片爬取**：editorial 博客配图同样本地化（`editorials/images/{比赛号}_{n}.png`，`/eimages/` 路由）
- **图片等比缩放**：`max-width:100% + height:auto`，超宽图片不溢出容器、保持纵横比
- **图片加载失败占位提示**：显示「🖼️ 图片未加载」而非隐藏/破图图标
- **题解题号统一渲染为标题 + 分割线**：识别裸文本/粗体/h1-h4/D2A 等全部题号格式（`1900A - Cover in Water`、`**A1 - ...**`、`#### 2044A - ...`、`D2A Submission`），统一渲染为 h2 醒目标题，每题前后加 `<hr>` 分割线，首个标题附「打开 ↗」链接；`1) Sum` 列表项、公式段落等不误判
- **Hint/Solution/Tutorial 等小节可折叠**：`**Hint #1**`/`**Solution (C++)**`/`**Tutorial**` 等粗体小节渲染为 details 折叠块（默认折叠、点击展开，▸/▾ 指示，**无框简易样式**——标题行+内容缩进 22px，无边框/背景；识别 Hint/Solution/Tutorial/Code/Feedback/Approach/Proof/Observation/Note/Complexity/Implementation/Explanation/Idea/Alternative/Official，长度 ≤50 防误伤长句；遇题目标题分隔线自动收束；展开时重新 typeset 公式解决隐藏态布局问题）
- **Editorial 不再爬取 Feedback 投票**：CF 标准 6 选项投票组件（Didn't attempt/Great problem/...）在转换层过滤（`_FEEDBACK_RE`），已爬 md 同步清理
- **「打开 ↗」统一到内容框右上角**：题面/题解 tab 的打开链接均为内容框顶部右对齐普通链接（题面=原题，题解=editorial 博客），随文档流不悬浮遮挡
- **题号标题链接到原题**：每个题解标题（`1900A - Cover in Water` / `A1 - ...` / `D2A Submission` / `D1C1 ...`）本身可点击直达 CF 原题页；链接构建三级：problems.json 名称匹配（处理 Div1 题在独立 contest 如 2129 的场景）→ 题号数字解析 → 当前比赛兜底
- **动态 per-problem tutorial 补全**：CF 新版 editorial（2024+）题解是 JS 动态加载（博客只有 `Tutorial is loading...` 占位壳）→ 检测 `problemTutorial` 容器后模拟前端调 `/data/problemTutorial` API（POST problemCode + 会话 cookie + X-Csrf-Token）逐题补全；占位替换三级匹配：精确题号 → 首字母（problemCode=F3 但标题是 F）→ `**Tutorial**` 小节（2032 格式）

### 修复

- **含数字题号被拒**：`_valid_ref` 要求 index 纯字母（`isalpha()`），1970A1/A2/B2 等题面请求全部 400 → 改「字母开头 + 字母数字」（A1/B2/F1 放行，纯数字仍拒）
- **快速切换 题面⇄题解 竞态**：editorial/题面/解题代码的异步响应迟到时会无条件 `replaceChildren()` 覆盖当前视图（题面消失、显示"没有 Editorial"）→ 加载请求序列号（`loadSeq`），切 tab/切题/返回列表后旧响应直接丢弃
- **图片路由恢复**：移除 shiki 时误删 `/images/` `/eimages/` 路由 → 已恢复（content-type 按扩展名）
- **题解代码高亮**：md 代码块语言标注（html2md 启发式检测：`#include`/`cin`/`cout`→cpp、`def`/`print(`→python、`fn`+`let`→rust 等；含弱特征：`long long`/`for (int`/`1ll`/C 风格 `){`）
- **围栏配对错乱**：正则无法区分开/闭围栏 → 改为逐行状态机识别，已爬 md 批量补救（含前导空格围栏）
- **iframe 内代码颜色不显示**：srcdoc 是独立文档不继承父页 CSS/CSS 变量 → hljs 配色按当前主题色值内联注入 srcdoc
- **公式渲染延迟**：MathJax 由 CDN + CHTML（需下载字体）改为**本地 tex-svg**（离线、SVG 输出无字体依赖，渲染即完成）
- **solutions 语言识别失败**：`ext` 字段带点（`.cpp`）与语言映射键不匹配 → 去点处理（`.cpp`→`cpp`）
- **爬取性能**：已预爬题秒跳过（不再每道 sleep 0.2s）；失败题记忆到 `failed_statements.json`（避免反复重试拖慢遍历）；批量模式单次尝试 + 短超时

### 公式保护链（渲染层防线）

CF 题面/题解公式是原始 LaTeX，需穿越 markdown 渲染（marked）不被破坏，再到 MathJax。共四层防线：

```
md（原始 LaTeX，含 \\ 换行、* 乘法、[] 艾弗森括号）
  ↓ ① protectMathContent：公式段内 markdown 敏感字符实体化
  │    \ → &#92;   * → &#42;   _ → &#95;
  │    [ → &#91;    ] → &#93;   ` → &#96;
  │    （marked 不解析实体 → LaTeX 原样通过）
  ↓ ② marked：markdown → HTML（实体保持）
  ↓ ③ HTML 解析：实体解码还原原始 LaTeX 字符
  ↓ ④ MathJax tex-svg-full：完整 TeX 渲染为 SVG
```

| 修复 | 根因 |
| --- | --- |
| **`\\` 换行被破坏** | marked 把 LaTeX 的 `\\`（多行换行命令）当 markdown 转义 → 输出单 `\` → MathJax 收到残缺 `\k`、`\=` → **红色未知命令乱码** |
| **`*` 乘法被拆断** | marked 把公式内成对 `*`（如 `(n-i)*\sum...*`）解析为强调 → `<em>` 标签**拆断公式** → MathJax 无法识别 |
| **`[]` 被当链接** | 艾弗森括号 `[gcd(a_i,a_j)=k]` 被 marked 当链接文本处理 |
| **`_` 被当强调** | LaTeX 下标 `a_i` 的 `_` 被 marked 解析为斜体标记 |
| **`\color` 扩展缺失** | 精简版 tex-svg.js 缺扩展 → typeset 整体失败 → 换 tex-svg-full.js（含全部扩展） |
| **自动+手动重复 typeset** | MathJax 默认自动渲染 + 手动 `typesetPromise` 双重处理 → `splitText` 越界 → 换 `startup:{typeset:false}` 单次渲染 |

## [v1.0.0] - 2026-08-06

首个完整版本（对应 4 次提交）。

### 核心功能

- **全量题目数据库**：Codeforces API 抓取 11335 题元数据（题号/名称/rating 800-3500/标签/解题数/链接）→ `problems.json`
- **题面自动爬取**：服务器启动自动增量爬取缺失题面 → `statements/{题号}.md`（markdown 格式，含样例、公式、图片）
- **本地网页**：Rating 区间/解题数/标签多选/名称搜索筛选，6 种排序，分页浏览，题面/题解弹窗阅读
- **双主题**：Catppuccin Mocha / Gruvbox Dark 一键切换（CSS 变量驱动，localStorage 记忆）
- **解题代码管理**：`solutions/{题号}.{ext}` 目录渲染自己的解题代码（多语言支持，题面底部展示）

### 修复

- **局域网访问**：服务器绑定 `0.0.0.0`（默认开放局域网，启动时提示局域网地址），CORS 放开
- **链接在 iframe 内导航**：iframe 内拦截所有链接点击 → 强制新标签打开
- **iframe 高度为 0**：srcdoc 中 `</script>` 双反斜杠转义导致 script 未闭合吞掉 body → 修正转义 + `min-height` 兜底
- **公式空格丢失/竖排**：html2md 保留文本节点空格、折叠行内换行（防止 marked 转 `<br>` 拆断公式）
- **公式前导 `$`**：CF 的 `$$$x$$$` 被 MathJax 误解析 → 统一归一化为单 `$` 内联公式
- **题面页脚垃圾**：转换后裁剪 Copyright/Server time/Privacy 等页脚标记

### 性能

- 爬取进度实时显示（前端轮询 + 进度条），列表标记自动刷新
- 题面/题解缓存于本地文件，点击秒开

## 技术架构

```text
cfcrawl.py   爬取库（curl 反反爬 + 题面/题解/图片下载 + md 生成）
html2md.py   HTML→Markdown 转换器（div 深度提取 + 代码语言检测 + 页脚裁剪）
server.py    本地服务器（静态页 + API + 启动自动增量爬取）
index.html   单页前端（筛选/排序/分页/详情/双主题/代码高亮）
update.py    数据更新/全量预爬（--statements）
vendor/      本地依赖：marked、highlight.js、MathJax tex-svg（零 CDN）
```
