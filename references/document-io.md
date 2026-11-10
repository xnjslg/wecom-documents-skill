# 原生文档接口

入口：`scripts/document.ps1`。每次创建新的私有运行目录，标准输出只给元数据与结果文件路径。

`snapshot --doc-id ID` 保存 Markdown 与 OOXML 原始响应，返回 Markdown 正文的 SHA-256。源文件中内容字段为 `content` 或 `file_path`，只能有一个。OOXML 字段值还需 JSON 解析一次，得到 `Document` 树；不用这个字段值访问文件系统。

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

当前链路调用 DOCX 导出曾返回 `48002`，要求客户端沙箱 Python 环境。没有可用沙箱时直接采用已验证局部编辑链，不伪造运行环境或认证字段。
