# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式。

## [Unreleased]

### 新增

- **筛选状态持久化到 URL 参数**：`?rmin=1400&rmax=1900&tags=dp,graphs&page=3`——刷新/滚动/快速刷新天然保留（无写入竞态），且筛选条件可分享链接
- **「↺ 恢复默认」按钮**：迷你样式，悬浮于筛选栏右上角，一键清除全部筛选并重置页码
- **浏览器前进/后退支持**：详情页使用 `pushState` + `popstate`，「← 返回」按钮与浏览器后退行为完全一致（含无历史时兜底）
- **题面图片爬取**：题面内 `<img>` 识别（过滤头像/国旗/站点装饰图），按题号命名保存至 `statements/images/{题号}_{n}.png`，md 内正确位置渲染（`/images/` 路由）
- **题解图片爬取**：editorial 博客配图同样本地化（`editorials/images/{比赛号}_{n}.png`，`/eimages/` 路由）

### 修复

- **题解代码高亮**：md 代码块语言标注（html2md 启发式检测：`#include`/`cin`/`cout`→cpp、`def`/`print(`→python、`fn`+`let`→rust 等；含弱特征：`long long`/`for (int`/`1ll`/C 风格 `){`）
- **围栏配对错乱**：正则无法区分开/闭围栏 → 改为逐行状态机识别，已爬 md 批量补救（含前导空格围栏）
- **iframe 内代码颜色不显示**：srcdoc 是独立文档不继承父页 CSS/CSS 变量 → hljs 配色按当前主题色值内联注入 srcdoc
- **公式渲染延迟**：MathJax 由 CDN + CHTML（需下载字体）改为**本地 tex-svg**（离线、SVG 输出无字体依赖，渲染即完成）
- **solutions 语言识别失败**：`ext` 字段带点（`.cpp`）与语言映射键不匹配 → 去点处理（`.cpp`→`cpp`）
- **爬取性能**：已预爬题秒跳过（不再每道 sleep 0.2s）；失败题记忆到 `failed_statements.json`（避免反复重试拖慢遍历）；批量模式单次尝试 + 短超时

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
