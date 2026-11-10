# 流程图与原文图片

企微独立流程图支持 Mermaid 导入。Word 文档中的普通代码框不会因此自动变成图形；其源码在原生树里可能是 `TextBox`，正文只用一个 `Drawing` 引用它。Markdown 读取还会把同一代码框重复展开，计数以原生对象为准。

## 生成图形

从当前文档提取实际源码。节点、边、标签和方向保持原意，使用本地渲染器输出清晰 PNG，检查中文字体、连线和边界。渲染工具以本机已安装工具为准；需要变换绘图语言时逐项核对结构，不重新编造流程。

独立可编辑流程图可用 `io --operation import`：`doc_type=flowchart`、`file_name` 为名称、`file_content` 为保留真实换行的 Mermaid 源码。源码不包 Markdown 围栏，不做 Base64。获取 SVG 时先用 `flowchart_pages` 取真实 `sub_id`，再 `read`，请求 `content_type=svg` 与该 `sub_id`。

## 替换 Word 里的代码框

1. 用最新 OOXML 定位 `Drawing` 及其 `inlineKeyword.graphic.graphicData.wsp.txbx.contentId`，找到对应根 `TextBox`。读取实际 `begin/end` 和代码内容。
2. 用 `state --range START:END` 核对编辑器中的同一范围。Drawing 在当前内核占 U+0008；单个对象范围是一单位，但仍以本次返回为准。
3. `upload --kind document-image --doc-id ID --path figure.png`，将 PNG 绑定目标文档。
4. 构造 `codebox-image` 参数文件：`start`, `end`, `contentId`, `imageUrl`, `width`, `height`。宽高是显示像素，保持原图比例与页面宽度。
5. 生成表达式并通过 `apply` 执行：

```powershell
$skill = (Get-Location).Path
& $python "$skill/scripts/make_edit.py" `
  --kind codebox-image --options C:/任务目录/options.json --output C:/任务目录/edit.js
```

脚本先加载图片，再核对目标引用、精确范围和可编辑性；删除该代码框，调用已有 `onSelection().insertImage` 插入图片。SDK 使用 twips，脚本按已验证的 `15 × 像素` 转换。附加 ID 按客户端同一算法生成。

普通 HTML 粘贴不作为此对象替换方法：已观察到它可能保留旧对象并增加段落。上述 SDK 路径已通过真实替换回读验证。

## 回读

目标变为含 `graphicData.pic.blipFill.blip.embed` 的 `Drawing`，图片 URL 与本次上传回执一致；旧代码框及其正文引用被移除。核对目标外全文、表格、其他图和全局样式。

修改会更新目标所属 Run 的 `modifyUnix`。审计时可以把只含该对象的直接父 Run 作为明确允许修改的范围，单独检查它的其他属性和唯一 Drawing；不忽略全文所有 Run 的修改时间。
