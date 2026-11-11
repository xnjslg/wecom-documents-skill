# 原生文档接口

入口：`scripts/document.ps1`。每次创建新的私有运行目录，标准输出只给元数据与结果文件路径。

## 只读正文

仅提取正文时请求 Markdown，不要求文档已在企微编辑器中打开。需求审阅和文档对照使用完整快照，结合 OOXML 核对可能影响含义的文字样式，图片下载另按用户选择执行。以下示例用无 BOM 的 UTF-8 保存请求，兼容 Windows PowerShell 5.1：

```powershell
$doc = Join-Path (Get-Location) 'scripts\document.ps1'
$requestPath = Join-Path $env:TEMP ('wecom-read-' + [guid]::NewGuid().ToString('N') + '.json')
$request = @{docid = '<docid>'; content_type = 'markdown'} | ConvertTo-Json -Compress
[IO.File]::WriteAllText($requestPath, $request, [Text.UTF8Encoding]::new($false))
& $doc io --operation read --request $requestPath
```

从回执的 `result_json` 读取实际响应，核对 `errcode=0`，并确认响应 `url` 的主机为 `doc.weixin.qq.com`、路径为目标 `/doc/<docid>`。查询参数可以与输入链接不同。响应中的 `name` 可用于核对标题。

正文取自 `content` 或 `file_path`，两字段互斥且值为文本；本适配链的 `file_path` 值不是磁盘路径。按需分段读取长正文，避免终端输出截断造成漏读。Markdown 会展开部分表格、不能完整保留样式，涉及这些结构时再补读 OOXML。

## 完整快照与其他操作

`snapshot --doc-id ID` 依次保存 Markdown 与 OOXML 原始响应，返回 Markdown 正文的 SHA-256。OOXML 字段值还需 JSON 解析一次，得到 `Document` 树，不用这个字段值访问文件系统。局部编辑和原生结构审计使用完整快照。

```powershell
$doc = Join-Path (Get-Location) "scripts\document.ps1"
& $doc io --operation read --request C:/任务目录/read.json
& $doc io --operation create --request C:/任务目录/create.json
& $doc io --operation append --request C:/任务目录/append.json
& $doc io --operation import --request C:/任务目录/import.json
```

请求文件使用原接口字段，按实际需求构造：

| 操作 | 已验证的请求字段 | 用途 |
| --- | --- | --- |
| read | `docid`, `content_type` | `markdown` / `ooxml` 读取 |
| create | `doc_type=doc`, `doc_name`, `content_type`, `content` | 新建独立 Word 文档 |
| append | `docid`, `content` | 末尾追加纯文本 |
| overwrite | `docid`, `content_type`, `content` | 用户要求整篇替换时使用 |
| import | `doc_type`, `file_name`, `media_id` 或 `file_content`, 可选 `overwrite_doc_id` | 导入新文档，或明确覆盖指定文档 |
| flowchart_pages | `docid` | 获取独立流程图画布 `sub_id` |

`overwrite_doc_id` 是全文覆盖。公式素材导入用单独的临时 docid；原文局部修改走编辑器 SDK。DTO `OaUpdateRequest` 在已验证 schema 中只属于新建文档的 `doc_requests`，不能据此编造现有文档的局部更新路径。

## 文档图片下载

先完成正文读取，不等待用户选择图片选项。任务已包含图片下载或图文审阅时直接下载，否则先保存图片引用，完成后说明下载与审图状态，再按未覆盖的内容询问是否需要补做。使用已有 `snapshot/markdown.json` 或 `io` 读取 Markdown 后返回的 `result_json`，不为下载图片重复抓取整份文档。

```powershell
# 不带下载参数时只建立图片索引，不发起图片网络请求。
& $python .\scripts\document_images.py --input C:/任务目录/markdown.json --output C:/任务目录/image-index

# 任务包含图片下载时，显式添加参数；输出目录须为新目录。
& $python .\scripts\document_images.py --input C:/任务目录/markdown.json --output C:/任务目录/image-download --download-images
```

脚本使用 Python 标准库，可在 Windows 虚拟环境或能够读取已保存响应的其他 Python 环境中执行，无需连接企微编辑器。当前支持原生正文中的 Markdown 图片和 HTML `img` 引用，以及企微 `qpic.cn` HTTPS CDN 上的 PNG、JPEG、GIF 和 WebP 原图。其他地址或格式记录为失败，不猜测凭据或改用无关下载通道。

输出包含 `document.original.md`、使用本地图片引用的 `document.local.md`、原图目录和 `images.json`。索引按 URL 去重下载，保留每次引用的行号、说明和附近文字，并记录字节数、SHA-256、可识别的尺寸及失败原因。原生 Markdown 与 OOXML 快照保持原样，局部编辑仍使用原始快照。

回执分别给出引用数、独立图片数、下载成功数和失败数。`visual_reviewed=false` 表示下载工具尚未阅读图片；不能将下载成功表述为已核对图文。不下载时说明本次未包含图片内容，样式核验则仍按 OOXML 执行，两者分别报告。

## 文件与图片上传

```powershell
& $doc upload --kind media --path C:/任务目录/source.docx
& $doc upload --kind document-image --doc-id ID --path C:/任务目录/figure.png
```

- `media` 使用 `upload_media`，从结果 JSON 读取真实 `media_id`，再传给导入操作。Word 源文件的 `file_name` 与实际文件名、扩展名一致；已实测 DOCX 和 TXT 导入。
- `document-image` 使用 `upload_doc_media`，自动设置 `open_id=docid`；从结果 JSON 取得图片 URL。已实测 PNG，资源先上传，插入后再回读图片节点。
- 上传成功不等于内容已经插入原文。`upload_attachment` 属于 Agent 附件登记，不能当成 Word 文档附件块插入；文件附件块尚无本适配器的已验证入口。

原图和文件原件保存在磁盘，按任务需要读取。回执中的 CDN URL、media ID 与原始响应留在私有目录，向用户交付在线文档链接或按机器人协议发送文件。

## 异步与不确定响应

适配器自动保持同一 trace ID 查询 `/task/query` 的 `taskid`，直到 `long_task_poll.done=true`。只读操作的 `10004` 有界重试；写入不自动重放。写入失败或超时时先取新快照，确认已执行情况再安排剩余动作。

### 部分读取失败

`snapshot` 依次读取两种格式。Markdown 已核验并保存后，若 OOXML 请求返回接口错误或超时，命令返回 `ok=true`、`status=snapshot_partial`、`complete=false`，进程退出码为 0。结果中的 `markdown_content` 与 `markdown_sha256` 指向已取得的正文，`available_formats`、`missing_formats` 列出格式状态，`format_errors` 返回错误类型、接口错误码及错误记录路径。

- 部分成功可继续用于只读审阅，报告正文读取成功及缺失格式，不把 OOXML 请求失败报告成“文档无法读取”。旧运行记录只显示整次失败时，检查 `failure.json`、原始 HTTP 和 `snapshot/` 下的产物；已保存的 `markdown.json` 与 `markdown.txt` 须按上述响应规则重新核对。
- 需要补读时，用 `io --operation read` 只请求缺少的格式，保留已成功取得的内容，避免重复抓取完整快照。写入前仍按局部编辑流程重新取得完整快照和最新版本。
- Markdown 请求失败、响应文档不符或内容校验失败时，仍返回失败。原始错误响应、异步任务未完成或文件存在本身均不构成正文读取成功。`apply` 的写前与写后快照保持严格模式，不能使用部分成功结果跳过结构校验。

当前链路调用 DOCX 导出曾返回 `48002`，要求客户端沙箱 Python 环境。没有可用沙箱时直接采用已验证局部编辑链，不伪造运行环境或认证字段。
