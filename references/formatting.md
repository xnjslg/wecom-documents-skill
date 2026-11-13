# 标题、排版与原生代码块

## 使用范围

以下操作已在真实文档实测写入并刷新回读：一级和二级标题、粗体、斜体、单下划线、删除线、字体和字号、文字颜色和底色、上下标、段落居中与左缩进及间距、引用、数字/符号/任务列表及完成状态、2×2 表格与表头格式、原生代码块及语言/主题/换行属性。

脚本沿用这些方法，参数可指定本次文档的内容与值。未对所有字体、字号、表格尺寸和代码语言组合逐一验收；参数被接受不替代写后检查。代码块语言、主题和换行已验证属性持久化，语法着色颜色和长行换行效果需额外视觉核验。

多级列表调整、合并拆分单元格、页面设置、行内代码、行号、整篇自动美化尚未提供已验证模板。用户需要时先在独立文档验证对应操作。

已有普通矩形表格的整体居中、单元格对齐和列宽调整，以及局部段落合并、嵌套列表转一级列表，也已通过真实文档写入与原生回读核验。这些操作需按当前 SDK 审查表达式，不是 `make_edit.py` 的新增参数；方法与范围校验见 [表格布局与内容整合](reflow.md)。

## 定位与执行

1. 读取原生快照与当前编辑器状态，确认目标文档及编辑权限。用户说“重新排版”时，根据实际内容确定标题层级、正文、列表、表格和代码块，保留原文事实与文字；内容改写按用户另行授权的范围处理。
2. 将本次明确的目标及样式写为 UTF-8 JSON。文本选择器须唯一，标题、段落、引用、任务状态及列表端点使用完整段落文字。多个同文段落先用原生节点和 GCP 明确目标，不能自动取首个匹配。
3. 生成表达式后审查实际目标和参数，再沿 SKILL.md 的 `apply` 流程执行。同一文档多批修改在一次 `apply` 中重复传入 `--script`，复用编辑器连接；每批核对版本，整项任务前后各取一次完整快照。独立运行的 `apply` 各自重新读取版本与快照。

```powershell
$skill = (Get-Location).Path
& $python "$skill/scripts/make_edit.py" `
  --kind format --options C:/任务目录/options.json --output C:/任务目录/edit.js
```

所有新增模式必须提供当前 `docid`，表达式再次核对精确 URL。SDK 从当前编辑器已加载的模块中获取，不下载其他代码。表达式长度受现有 CEF 限制，生成器超限时拆分批次。

模板预检目标，遇到运行期错误即停止并记录已完成步骤；返回 `stopped_needs_audit` 时核对 `completed` 与 `failed`，多批 `apply` 会停止后续批次，单批的写入回读成功也不代表全部步骤成功。多个操作不构成原子事务；无论返回错误还是中断，都先回读确认部分执行结果，再生成剩余动作。选区变化不等于文字修改。

## 连续批次与失败恢复

所有表达式准备并审查完后，按执行顺序提交。各表达式分别受长度限制，包含版本检查包装后的长度不超过 16384 个 UTF-16 单元；超限时按明确目标拆分。批次间通过现有编辑器检查状态，不重新连接，也不逐批请求完整原生快照。

```powershell
.\scripts\document.ps1 apply --doc-id '<docid>' --browser-id <实际编号> `
  --expected-version <当前版本> --before-sha256 '<快照正文SHA-256>' `
  --script 'C:/任务目录/第一批.js' `
  --script 'C:/任务目录/第二批.js'
```

每批开始前检查版本与同步状态，批后等待本地队列稳定，并使用新版本检查下一批。每批仍须核对目标文字和范围，前序插入删除引起的位置变化由后续表达式重新定位。一次连接不意味着原子事务，也不意味着只有一次网络请求。

多批结果保存在 `batch-NNN-editor-response.json`、`batch-NNN-result.json` 和对应的状态记录中，`attempt.json` 保存当前进度；成功时 `result.json` 汇总各批返回值。失败摘要中的 `current_batch` 指示当前批次，`completed_batches` 表示已返回且本地队列已稳定的批数，不代表云端已核验，也不保证失败批没有部分写入。

- 写前读取或版本检查失败，且 `write_attempted=false`：修正实际原因，重新核对快照和版本后执行。
- 已提交编辑：先检查失败批的回执、结果及最新文档，明确已执行的步骤，再为剩余修改生成表达式。使用新的版本与正文摘要提交，保留此前运行目录；不按批次编号自动重放失败批。
- 所有批次完成但写后读取失败：保留修改回执，只补读缺少的格式并完成目标和外围内容核验，不重新执行修改。

任一批抛出异常或报告 `stopped_needs_audit`，多批任务立即停止后续批次；已完成修改不会自动回滚。最终仍以原生回读确认保存结果。

## format 参数

顶层为 `{"docid":"当前文档ID","operations":[操作对象]}`。每项 `type` 的字段如下，实际取值从用户要求及当前文档获得。

| type | 字段 |
| --- | --- |
| `heading` | `text`：完整段落；`level`：1 或 2 |
| `font` | `text`：唯一文字；可选 `bold`、`italic`、`strike` 布尔值，`underline="single"`，`color`、`shading` 为不带 # 的六位十六进制，`sizePt` 为字号，`family` 为字体映射，`verticalAlign="superscript"` 或 `"subscript"` |
| `paragraph` | `text`：完整段落；可选 `alignment="center"`、`leftIndentTwips`、`spaceBeforeTwips`、`spaceAfterTwips`、`lineSpacing=360` |
| `quote` | `text`：完整段落；应用原生引用与左边线 |
| `list` | `fromText`、`toText`：起止完整段落；`kind` 为 `numbered`、`bullet` 或 `task`；两端之间的所有完整段落属于目标范围 |
| `task-state` | `text`：完整任务段落；`state` 为 `todo` 或 `done`；先确认已是任务列表，或在同批前序创建任务列表 |

`family` 使用 `{ascii,hAnsi,eastAsia,cs}` 四个字体名称。字号以 pt 输入，模板转换为 twips；缩进和段间距直接使用 twips，20 twips = 1 pt。`lineSpacing=360` 是本轮验证过的编辑器行距值，不是 360 pt。

底层标题名称来自 `sdk.BuiltinStyleName.Heading1/Heading2`，当前真实值为 `heading 1/heading 2`。字体设置需要字体映射对象；上下标使用 `setVerticalAlignRun`。

## insert-table 参数

顶层字段：`docid`、`anchorText`、`rows`、`cols`、`cells`、可选 `header`。

- `anchorText` 指当前文档主正文中唯一的插入锚点，表格插在锚点之后，原锚点保留；不在现有表格或子故事内插入。
- `cells` 为 rows×cols 的单行字符串二维数组，不含控制字符。用空字符串表示空单元格，不把 Markdown 表格当作单元格数组。
- `header=true` 将首行设为粗体、`E8F0FE` 底色及垂直居中；省略时不额外设置表头。

生成时使用 `--kind insert-table`。底层 `insertTable(rows,cols)` 后重新获取表格服务，按单元格 GCP 从后往前写入内容，避免插入导致后续位置错位。它插入新表格，不用于覆盖已有表格。

## codeblock 参数

创建使用 `--kind codeblock`，参数包含：

- `docid`、`action="create"`、主正文中的唯一 `anchorText`；不在表格或其他子故事内创建。
- `contentId`：为此次对象生成的新标识符；同一对象的后续操作沿用该 ID。
- `code`：含真实换行的代码字符串，不包含 Markdown 围栏。
- 可选 `language`、`theme`、`wordWrap`。默认 `plain text`、`normal`、true。

当前 SDK 的语言使用别名，例如 `javascript`；语言与主题以当前 SDK 属性工厂接受的值为准。实测创建 plain text/normal 后更新为 javascript/dark，且 wordWrap 能从 true 改为 false。

更新使用 `action="update"`，提供当前原生 Drawing 的 `gcp`、`contentId`，以及要更新的 `language`、`theme`、`wordWrap` 中至少一项。`gcp` 从本次快照取得，不沿用历史坐标。模板核对 U+0008 占位符及 Drawing 的内容引用后更新子故事属性，保持代码正文不变。

代码块是正文 Drawing 加独立 `TextBox` 子故事。普通 Markdown 下标不能定位对象；代码块中的 Mermaid 文本也不会自动渲染为流程图。此模板不提供代码正文替换或删除。

## 回读核验

检查原生 OOXML，不只看命令回执或 Markdown：

- 标题：Paragraph 的 `pStyle.val` 解析到 `styles.style` 中对应 heading 名称。
- 字符：目标 Run 的 `b/i/u/strike/color/shd/sz/rFonts/vertAlign`；被分割的 Run 需合并检查文本，不依赖原有 Run 数量。
- 内容整合：逐一核对保留的标签、术语及其他强调片段对应的新文字范围；字体、字号或全文文字相等不能证明段内加粗等样式仍保留。具体方法见 [行内样式](reflow.md#行内样式)。
- 段落：`jc`、`ind`、`spacing`；引用还有 `blockQuote` 与左边框。
- 列表：Paragraph 的 `numPr` 关联 numbering，核对 `ilvl`、实际缩进、decimal/bullet/task 类型及任务 `taskPr.taskState`；不仅比较 numId 是否相等。
- 表格：实际 Table/Row/Cell 结构、四周文本、每格文字、`tblPr.jc`、列宽总和，以及单元格段落 `jc`、`shd`、`vAlign`、粗体；分别核对整体位置与表内对齐。
- 代码块：Drawing 的 `txbx.contentId` 对应唯一 `TextBox.property.id`，`isCodeBlock=true`，逐项检查 language/theme/wordWrap；保留代码行、字符及顺序。

格式修改前后核对全部原文文字。新增表格和代码块时分别核对新增内容，以及原文的保留与顺序。对明确目标之外使用 `audit_outside.py`；排除目标段落后仍需检查段内非目标文字和属性。

新增标题、列表和代码块可能增加全局 styles/numbering。完整树审计会报告这种差异，须核对新增定义、引用关系及旧定义是否保持，不能把整份 styles/numbering 一概忽略，也不能仅因出现预期差异就宣称审计通过。

队列空闲后可对明确目标执行 `refresh`，重新查询状态和快照，确认持久化。报告实际完成项目；视觉未验收的效果单独标明。
