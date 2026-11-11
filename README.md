# WeCom Documents Skill｜企业微信在线文档读取、编辑与排版

A local AI skill for reading, creating, editing, and formatting WeCom online Word documents through an existing Windows WeCom session. Supports native code blocks, tables, images, and editable equations.

`wecom-documents` 复用当前 Windows 企业微信的登录会话，读取或新建在线 Word 文档，在原文档中局部修改内容和格式。写入前保存快照，写入后回读并核对目标及其他内容。

**适用于能够加载 Skill 并执行 Windows 本机脚本的 AI 客户端，不限于 Codex。** 执行脚本随包提供，运行需要 Windows、已登录的企业微信和 Python 虚拟环境。

## 功能

| 功能 | 支持范围 |
| --- | --- |
| 文档读写 | 读取、新建、追加、导入文件；按明确范围局部编辑 |
| 标题与文字 | 一级、二级标题；粗体、斜体、下划线、删除线、字体、字号、颜色、底色、上下标 |
| 段落与列表 | 居中、左缩进、段间距、已验证行距；引用、数字列表、符号列表、任务列表及完成状态 |
| 表格 | 插入原生表格、填写单元格、设置表头格式 |
| 原生代码块 | 创建代码块，设置或更新语言、主题和自动换行属性 |
| 图片与流程图 | 上传文档图片；将 Word 中的代码框替换为图片；独立流程图可导入 Mermaid |
| 数学公式 | 生成和插入可编辑的原生 OMath 公式 |

重新排版根据实际文档确定标题、正文、列表、表格和代码块的样式，再分批修改。原文事实与文字默认保留，改写内容按用户指定的范围执行。

## 环境要求

- Windows 上已安装并登录企业微信，当前账号具备目标文档的相应权限。
- 64 位 Windows Python 3.11+ 虚拟环境，以及 [scripts/runtime/requirements.txt](scripts/runtime/requirements.txt) 中的依赖。目前核心依赖为 `frida==17.25.2`。
- AI 客户端能读取本地技能、执行 PowerShell 或 Windows Python，并访问任务文件。
- 局部排版需要目标文档在企业微信内嵌编辑器中打开，并能匹配当前编辑器接口。
- 数学公式按需安装 [scripts/requirements-math.txt](scripts/requirements-math.txt)，另需 Office 提供的 `MML2OMML.XSL`，详见 [公式说明](references/math.md)。

运行核心在 Windows 执行，兼容性根据实际接口能力判断。内嵌编辑器使用底层接口，模块布局不匹配时会停止对应排版操作；读取等原生接口仍按各自的实际结果判断。其他企微版本尚未逐一实测，兼容性不能仅凭版本号推断。

## 安装

将仓库放入所用客户端的 Skill 目录，保留 `SKILL.md`、`scripts/`、`references/` 的相对位置。下面以 Codex 的 Windows 技能目录为例；其他客户端使用其自己的技能加载路径。目标目录应尚不存在：

```powershell
$skillDir = Join-Path $HOME '.codex\skills\wecom-documents'
git clone https://github.com/xnjslg/wecom-documents-skill.git $skillDir
Set-Location $skillDir
```

优先复用已有的 64 位 Windows Python 3.11+ 虚拟环境。没有合适环境时再创建：

```powershell
$venv = Join-Path $HOME 'miniconda3\envs\wecom-documents'
$python = Join-Path $venv 'Scripts\python.exe'
if (!(Test-Path $python)) { py -3 -m venv $venv }
& $python -m pip install -r .\scripts\runtime\requirements.txt
$env:WECOM_DOCUMENT_PYTHON = $python
.\scripts\document.ps1 probe
```

已有环境可直接把 `WECOM_DOCUMENT_PYTHON` 指向其 `python.exe`。入口也会查找已激活的 venv/Conda 环境和上述默认目录。`probe` 只检查本机接口，不修改文档；接口存在不等于目标文档已授权，后续读取会验证实际权限。

本包不含 Python、企业微信、Office、登录凭据或登录会话。安装与连接细节见 [运行环境说明](references/client-transport.md)。

## 使用示例

在支持 `$技能名` 的客户端中使用 `$wecom-documents`；其他客户端按其方式加载 [SKILL.md](SKILL.md)。提供目标文档链接和明确的操作范围：

```text
用 $wecom-documents 读取这份企微在线文档：<文档链接>。
列出其中的标题、结论和待办，保留原文中的数字与限定条件。
```

```text
用 $wecom-documents 重新排版这份技术方案：<文档链接>。
章节使用一级标题，子章节使用二级标题，步骤改为编号列表，
命令示例整理成原生代码块。保留原文内容、现有表格和图片，
在同一份文档中修改，完成后核对文字和格式。
```

```text
用 $wecom-documents 在这份文档的“请求示例”段落后插入原生代码块：<文档链接>。
代码使用下面提供的内容，语言为 javascript，主题为 dark，开启自动换行。
```

```text
用 $wecom-documents 把指定公式插入这份文档：<文档链接>。
使用可编辑公式，保留上下标和独立段落，插入位置为“计算公式”段落之后。
公式：<LaTeX 内容>
```

只需正文时，优先使用 `io --operation read` 请求 Markdown，无需先在企微编辑器中打开文档；请求示例见 [只读正文](references/document-io.md#只读正文)。

需要原生结构或准备编辑时，使用快照和编辑器状态命令。将 `<docid>` 替换为 `https://doc.weixin.qq.com/doc/<docid>` 中的文档 ID；`state` 要求目标文档已在编辑器中打开：

```powershell
.\scripts\document.ps1 snapshot --doc-id '<docid>'
.\scripts\document.ps1 state --doc-id '<docid>'
```

局部编辑需要使用当前快照、编辑器状态和实际目标位置生成表达式，再按 [SKILL.md](SKILL.md) 的流程执行与核验。编辑位置不是 Markdown 字符下标。

快照返回 `snapshot_partial`、`complete=false` 时，表示正文已核验保存，但后续 OOXML 请求失败；已取得正文可用于只读审阅，结果同时提供缺失格式和错误记录。局部编辑仍要求完整快照。详见 [部分读取失败](references/document-io.md#部分读取失败)。

## 结果与数据存放

最终结果保存在原在线文档中，交付原链接及核验结果；新建操作返回新文档。格式修改通过文档原生结构完成，标题、表格、代码块及原生公式保持对应对象的可编辑性。

本地运行目录默认位于 `%LOCALAPPDATA%\WeComDocumentTools\runs`，保存原始快照、编辑表达式及回读结果，其中可能包含文档正文。每次操作使用独立目录；发布或分享技能时只分享技能代码，不包含这些文档产物。

写入回执不代替成功核验。发生超时、中断或部分执行时，先回读确认已执行内容，再继续剩余操作。技能不自动发送群消息或创建定时任务。

## 已验证范围与限制

- 已在真实文档验证标题、文字样式、段落、列表、表格和原生代码块的写入及刷新回读；详细范围见 [排版说明](references/formatting.md)。未逐一验收所有字体、字号、表格尺寸、代码语言及客户端版本组合。
- 代码块语言、主题、换行属性已验证可持久化，具体语法着色及长行显示仍需视觉检查。当前模板不提供代码正文替换或删除。
- 多级列表、合并拆分单元格、页面设置、行内代码、行号没有已验证模板；需要时先在独立文档验证。
- 现有模板支持按范围重新排版，不提供对任意文档一键套用并保证正确的自动美化功能。
- Word 代码块里的 Mermaid 文本不会自行变成流程图；可按需求生成图片或使用独立流程图接口。
- 本技能聚焦企微在线 Word 文档，不覆盖在线表格和演示文稿的完整编辑能力。
- 企业微信内部接口可能变更。发生兼容错误时保留原始错误，先区分登录、权限、文档状态和接口问题，再参考历史测试环境排查。

## 文件与实现

| 文件 | 作用 |
| --- | --- |
| [SKILL.md](SKILL.md) | 文档读取、编辑与核验流程 |
| [scripts/document.ps1](scripts/document.ps1) / [document.py](scripts/document.py) | 统一调用入口 |
| [scripts/make_edit.py](scripts/make_edit.py) | 根据明确参数生成编辑表达式 |
| [scripts/audit_outside.py](scripts/audit_outside.py) | 核对目标范围以外的文档结构 |
| [references/formatting.md](references/formatting.md) | 排版、表格、代码块参数与验证范围 |
| [references/document-io.md](references/document-io.md) | 读取、新建、导入与图片上传 |
| [references/math.md](references/math.md) | 可编辑数学公式 |

## 许可证

本项目以 [MIT License](LICENSE) 开源。企业微信、Office 等外部软件仍适用各自的许可与服务条款。
