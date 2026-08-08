# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/) 格式。

## [Unreleased]

### 新增

- **favicon 与标题 CF 图标**：标签页 favicon 使用本地 CF 图标（`vendor/cf-favicon.png`，零 CDN）；页面顶部标题（h1）前加 CF 图标 22×22
- **`/vendor/` 图片 content-type**：png/jpg/gif/svg/webp 正确返回（此前 octet-stream 导致 favicon 不显示）

### 修复

- **题号标题链路（根本解决）**：① API 动态 tutorial 的 `<h3>题号标题</h3>` 在 ttypography 容器外被转换器丢弃（1004E 等纯占位博客整场无题号标题）→ 补全时从 API html 提取补 `## 题号 - 名称`；② 博客原文标题与补全标题双份（1000 类每题标题重复 + 每题第一个总是 Editorial）→ 替换时去重 + 转换管线末尾**全局去重**兜底（同题号 h2 只留第一个，内容不丢）；③ CF 原文 `spoiler-title` 的 `**Editorial**` 折叠标题（题号标题后第一个元素总是 Editorial）→ 转换时跳过（Solution 折叠保留，正文粗体 Editorial 不误伤）
- **题号标题格式全覆盖**：链接/粗体/裸文本/h1-h4（`### 1004E - Name`、`### [1004E - Name](<url>)`、`#### 2044A - Easy Problem`）/点号（`1000B. Light It Up`，3+ 位数字 + 大写开头防 `1) Sum` 列表误判）→ 统一转 h2；题面不再转（题目名称唯一 h1 保留，居中大标题不受影响）；editorial 转换补入归一化（此前只加在题面转换，重爬不生效）
- **前端折叠边界**：题号标题作为 Solution 折叠收束边界（isProblemTitleText 双保险）——折叠块不再吞掉下一题描述；`###` 层级题号标题也能正确 wrap 原题链接
- **部分题缺题解不再整场丢弃**：动态 tutorial API 的 `success` 是字符串（`"false"` 不能当真值）——个别题官方未发布题解（819A 等）此前导致占位残留 → 整场 @temp 死循环（每场重试都失败，其他题的题解永远拿不到）→ 区分**确认无题解**（`success=="false"`）与网络失败：确认缺失的题删除占位（该题无题解，标题保留），其他题照常写入；819（6 题恢复）等 18 场历史误判全部恢复爬取，failed 记录同步清理

- **样例渲染修复**：① 公式保护正则错位——`$a$$b$` 连写公式的闭 `$` 被当开 `$` 配对，长文本/样例围栏被吞进"公式段"转成实体（`&#96;`），marked 不再识别代码块（样例变普通段落）→ 正则加 `(?<!\$)` 前向断言 + 行内内容非换行（换行天然挡住样例块）；② 样例输入单行——CF 样例行 `<div class="test-example-line">` 每行一个 div，html2md 忽略 div 行结构拼成一行（2234A 等）→ pre 内 div 结束补换行（3010 个已爬题面需重爬恢复，新爬自动正确）；③ srcdoc 缺 h4 样式（Input/Output 小节标题与正文无区分）→ 补 h4（accent 色加粗）

- **返回列表后筛选丢失**：showList 的 `replaceState(location.pathname)` 清掉 URL 的 query（filter 参数）——从题目详情返回列表后刷新，筛选/排序/页码全部重置 → 保留 `location.search`

- **透明图片白底优化**：CF 题面/题解图片很多是透明 PNG（内容为白底设计）——深色主题下透明区与黑底糊成一片 → 新增 `_flatten_transparent_png`（**纯标准库 zlib/struct，零外部依赖**）把透明像素按 alpha 合成到白色背景；支持 RGBA / gray+alpha / palette+tRNS（1/2/4/8-bit 拆位）/ RGB+tRNS 透明色键，不透明图零开销跳过；图片下载后自动处理，已爬 140 个透明图全部白底化（残留 0）

- **题解不再折叠**：删除 srcdoc 折叠逻辑（`details.fold` + KEYS 小节检测）——Solution/Hint/Tutorial 等小节直接展开显示（无需点击），题面不受影响
- **透明图片白底增强**：`_flatten_transparent_png` 扩展——16-bit RGBA/gray+alpha、gray+tRNS（1/2/4/8/16-bit 拆位）、palette 低位尾字节越界修复（stride ceil 多出像素→写入越界）、RGB+tRNS 16-bit 解析；题解图存量 6260 个 + 题面图全部白底化（12612/12614，2 个损坏文件跳过）

- **下标渲染为删除线修复**：marked 把单 `~` 当删除线（GFM）——html2md 的 `<sub>` 转 `~` 后下标（`~0~`、`~*t*-1~` 等）渲染成删除线 → 渲染前把单波浪 `~内容~` 还原为 `<sub>`（内容内 markdown 斜体 `*x*` 一并转 `<em>`；真删除线 `~~x~~` 不受影响）；html2md 的 `<sub>` 直接输出 raw HTML（新爬数据源头正确）

- **上标渲染修复**：marked 不解析 `^...^`（显示字面）——html2md 的 `<sup>` 转 `^` 后上标（`10^6^` 等）不渲染 → 渲染前把单波浪 `^内容^` 还原为 `<sup>`（与下标同一机制）
- **代码块保护**：下标/上标还原前先把代码块（``` 围栏）与行内代码（`...`）占位保护——代码里的 `~`/`^`（位运算等）不被误替换；占位符含 NUL 不与正文冲突，代码块原样往返

## [v2.4.0] - 2026-08-06

### 修复

- **空壳阈值修正（200→50）**：短题面（愚人节题等 51-200B 正常内容）被误判为空壳 → 阈值与写前保护一致（≤50B 才算爬取异常产物）；1331A 等 6 个短题面恢复显示
- **滚动条主题化**：filter 标签区/代码框/页面滚动条统一 WebKit 样式（surface2 thumb + 透明 track），Firefox 用原生 `scrollbar-color`；修复 Chrome 中 thumb `border` 渲染成一圈黑色轮廓（去 border）
- **数字输入框去增减按钮**：filter 栏 Rating/解题数输入框的 spin button 隐藏（WebKit `appearance:none` + Firefox `appearance:textfield`）
- **返回按钮箭头居中**：Chrome 中 `←` 字体字形基线偏移（Firefox 正常）→ 改用 **SVG 箭头**（几何渲染零字体依赖）+ i18n 文本去箭头（避免双箭头）
- **iframe 高度动态同步**：浏览器 resize（宽屏→窄屏）时图片等比缩放、内容重排——srcdoc 补 `window resize` 监听自动防抖同步高度（此前高度停留在旧值，题面图片下文字与本地解题代码错位/被覆盖）
- **默认主题改为 gruvbox**：`data-theme` 默认值 catppuccin → gruvbox（无 localStorage 记忆时生效；已记忆的主题保持用户选择；srcdoc 内联色值随之）



### 修复

- **iframe 高度同步根本解决**：折叠块展开后代码框超出底部不可见（事件驱动 syncH 有遗漏）→ srcdoc 改用 **MutationObserver 监听内容变化**（折叠/公式 SVG/代码高亮/图片 load/字体就绪一律自动防抖同步高度）——不再依赖逐个事件



### 新增

- **样例/代码框复制按钮**：hover 显示，Nerd Font 图标（复制 U+F0C5 / 完成 U+F00C）正方形按钮、主题 accent 色、透明背景（hover 才 surface2）；兼容所有浏览器（clipboard API 安全上下文 → execCommand fallback 局域网 http）；`themeColors` 补 surface2（缺失导致按钮白底）；**本地解题代码块复用同一复制逻辑**（`attachCopyButton`）

### 修复

- **题面标题层级**：题目名称唯一 h1（24px 居中）；Input/Output/Note/Examples 小节与样例标记从 `##`/`#` 降级为 `####`（不再是大标题）；属性行（time limit per test: / input: / output: 带冒号）居中（CF 风格）
- **parseHash 支持数字 index**：`#/problem/2164F2` 等链接可直接打开（正则 `[A-Za-z]+` → `[A-Za-z0-9]+`）



### 修复

- **假题解防线（根本解决）**：题解 403 错误页/nginx 页检测丢弃（@temp 可重试）；动态 tutorial 占位符替换加无标题博客 fallback（1300 等格式）；写前校验（占位残留/错误页/过短 <100 字符 → 不写）；清理 12 个 403 污染 + 102 个占位残留假题解并重爬恢复 407 场
- **题面空壳保护**：转换结果 <50 字符不写文件（1 字节空壳根因修复）；≤200B 空壳视为未爬（解封后自动补爬）；清理 5354 个空壳并重爬恢复

### 性能

- **批量爬取框架复用**：题面/题解共用 `_run_batch_crawler`（8 并发 batch + 403 自适应暂停 + 批间限速 + 进度回调）——消除重复实现，行为一致；`_save_failed` 加并发写锁



### 性能

- **editorial 探测并发化**：未确认的比赛按批并发探测（BATCH 8 + 批间限速 1.5s），已爬/已确认无题解的比赛秒跳过；整批网络异常自动暂停（403 自适应），下次启动续跑——首轮 2000 场从串行 ~50 分钟提速数倍，且记忆持续积累、中断不丢

### 修复

- **failed_editorials 并发写安全**：记忆写入加线程锁（HTTP 线程与后台探测并发写不再互相覆盖丢条目）


### 修复

- **editorial 批量爬取 int contestId 崩溃**：`fetch_all_editorials` 遍历 problems.json 传 int，动态 tutorial 补全的 `len(cid)` 抛 TypeError（批量爬到 655 场卡死）→ `fetch_editorial_md` 统一 `str(cid)`

## [v2.2.0] - 2026-08-06

### 新增

- **双语 README**：英文默认（`README.md`）+ 中文（`README.zh-CN.md`），顶部语言互链；覆盖功能/快速开始/手动爬取/数据布局/说明
- **强制使用 CodeNewRoman Nerd Font**：本地 otf（vendor/，零 CDN）——正文比例版 + 代码等宽 Mono 版（Regular/Bold/Italic 6 个 @font-face）；主页面、srcdoc 阅读区、solutions 代码块全覆盖；`/vendor/` 路由补 font/otf content-type
- **i18n 全页面中英切换**：默认英文；「文/En」按钮位于主题按钮左侧（localStorage 记忆）；覆盖静态文本（筛选标签/排序/表头/tab/按钮）、动态文本（进度条/筛选统计/hint/详情元数据）与 srcdoc 内容（打开 ↗/图片占位）；详情页内切换即时重建当前 tab
- **恢复默认按钮**：从迷你图标 `↺` 改为文字「Default/默认」（随语言切换）
- **排序新增「ID 降序」**：contestId + index 双字段降序（`ID ↓` / `题号 降序`，中英双语）
- **Rating i18n**：中文界面 Rating 译为「分数」（表头/筛选标签/统计/详情元数据/排序选项），英文保留 Rating（`colRating` 双字典同文本曾误替换，已修）
- **布局稳定性**：filter 单元冗余高度（min-height 58px + label 固定高 16px 防换行）、filter 组件固定尺寸（input/select 高 34px、reset 按钮 24×88px）、链接列文字固定宽度 72px（打开 ↗/Open ↗ 切换整行文字位置稳定）、列表行冗余行高（tr 44px + td line-height 24px——字符垂直位置完全稳定）、标签列改 flex 容器（align-items: center——垂直中心对齐零依赖字体基线/x-height，字体 fallback 与中英切换均零抖动）；详情栏稳定（meta 单行省略、back 按钮 34×116px、tab 按钮 34px 固定）；「共多少题」与「筛选结果」行冗余高度（min-height 20px）；爬取进度框冗余高度（min-height 40px）；表头行固定高度 38px（table-cell 的 min-height 无效，用 height 语义 + 垂直居中）；ID 列 96px、分数列 80px、链接列 88px、主题按钮 118px 均固定宽度——中英切换高度/宽度零跳动；主题按钮双语均带 🎨 图标

## [v2.1.0] - 2026-08-06

### 新增

- **PDF 题面爬取**：CF 部分比赛（ACM 老赛 1089/1090/1181 等）无 HTML 题面、只提供 PDF → 探测 content-type 后下载 + `pdftotext` 提取转 md（标注「仅 PDF 题面，公式可能失真」，标题从 `Problem X. Name` 行提取）

### 修复

- **公告博客误当题解**：editorial 未发布时 contest 页 tutorial 链接指向 Announcement 公告 → 检测 `Announcement of Codeforces Round` 丢弃并标记 `@announcement`（不阻止重试，题解发布后自动爬取）；6 个历史污染文件（3/15/16/118/168 等）已清理重爬
- **孤立 `**` 残留**：CF 老博客用 `<b><br/></b>` 当分隔（空粗体标签残缺）→ 转换后出现行尾/单独行/行首孤立 `**`（如 2.md 的 `Lets start from the end.**`）→ **双层加固**：① 转换器 `<b>` 延迟输出（空粗体直接丢弃，新数据根治）；② `_fix_broken_bold` 行级修复（奇数 `**`：单独行删除、行尾删除、行首删开标记；**公式 `$...$` 与行内代码 `` `...` `` 中的 `**` 受保护不动、跨行粗体通过 next_line 识别保留**、`2**n`/`(**)` 字面记号不动）；12 个文件 64 行已清洗；9 项单测覆盖
- **无 Editorial 确认态**：按需点击题解 tab 时探测结果也记忆；提示区分「官方未发布题解」（已确认）与「尚未爬取成功」（网络问题，可重试）——以 921（AIM Tech Mini Marathon，无官方题解）为例验证；**确认过的比赛点击立即秒回（known 检查在探测之前，零 CF 请求 ~6ms），不再先转「加载中」再等探测**
- **纯数字题号 index 被拒**：921 等比赛的 index 是 `01`-`14`（纯数字）——参数校验要求字母开头 → 全部失败 → 统一放宽为 `isalnum`（A / A1 / 01 均可）；14 个 Labyrinth 题面全部恢复，failed_statements 归零
- **无 Editorial 比赛记忆**：2000 场比赛仅 ~200 场有公开题解——之前每次启动对 ~1800 场无题解比赛逐个发起网络请求（"从 0 慢慢解析"且加剧封禁）→ `failed_editorials.json` 记忆无链接比赛，启动秒跳过；按需点击题解 tab 也秒回「无 Editorial」
- **CF 403 封禁感知**：CF 反爬会临时封禁 IP（所有请求 403）→ `update.py` 检测 403/HTML 响应立即退出并明确提示（不再无意义重试 3 次）；`auto_update` 元数据失败时跳过题面/题解爬取（避免继续触发反爬）；爬取限速从 0.2s 调至 1.0s 防再次封禁；本地数据（11335 题元数据 + 11321 题面 + 全部 editorial）不受影响
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
- **「打开 ↗」顶部独立行右对齐**：内容框顶部单独一行、右对齐（题面=原题，题解=editorial 博客）；链接为行内元素只占文字宽度，不铺满整行可点击
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










