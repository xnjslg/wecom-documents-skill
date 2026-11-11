# Windows 客户端传输

执行核心随包放在 `scripts/runtime/`，包含原生文档通信、CEF 文档连接、全局互斥和校验布局。通过运行进程发现实际安装位置及身份；企微版本号与程序哈希仅用于诊断，不限制原生文档接口调用。

## 运行环境

使用已有的 64 位 Windows Python 3.11+ 虚拟环境；当前环境没有可用虚拟环境时再创建。下面命令在技能目录中运行，默认环境位置在用户的 miniconda3 目录下：

```powershell
$venv = Join-Path $HOME 'miniconda3\envs\wecom-documents'
$python = Join-Path $venv 'Scripts\python.exe'
if (!(Test-Path $python)) { py -3 -m venv $venv }
& $python -m pip install -r .\scripts\runtime\requirements.txt
$env:WECOM_DOCUMENT_PYTHON = $python
.\scripts\document.ps1 probe
```

已有虚拟环境可直接把 `WECOM_DOCUMENT_PYTHON` 指向其 `python.exe`。入口优先使用这个变量，其次使用已激活的 venv/Conda 环境，最后检查上述默认位置。核心和 Python 环境都在 Windows 执行，所有参数文件、表达式和输出使用 Windows 路径。

`document.ps1` 与 `& $python scripts/document.py` 调用同一份执行核心。包内不含 Python、企微程序、登录会话或 Office 组件；从当前用户已登录的企微会话工作。

默认运行产物位于 `%LOCALAPPDATA%\WeComDocumentTools\runs`，每次创建新的独立目录；`--output` 可指定尚不存在的绝对目录。产物含文档快照，不放回技能目录或发布包。

入口通过包内 `query_lock.ps1` 的 `Global\CodexWeComReadonly-<Windows SID>` 互斥串行执行。PowerShell 根据真实进程路径和父子关系定位主程序、伴随 Agent 和 CEF browser；存在多个候选时报告歧义，不猜选实例。

## 能力检查与错误处理

`probe` 读取本机 MCP 工具清单和文档服务描述，分别报告原生桥接、文件上传与文档路由是否存在。它不修改文档；路由存在也不代表某份文档已授权，目标文档仍以实际读取结果为准。

原生文档通道通过本机协议通信，不依赖固定二进制地址，也不要求目标文档已在编辑器中打开。登录过期、权限不足与接口变更按原始错误排查。`state` 等编辑器操作返回 `DocumentNotOpenError` 时，只说明没有找到对应的已打开编辑器，不据此否定原生读取。不要仅因版本号与历史测试环境不同就拒绝执行。

`10004 操作失败` 是通用接口错误，不能仅据此判断需要打开文档、登录已失效或客户端不兼容。保留原始响应，先区分服务发现、正文读取、OOXML 读取和异步轮询。正文读取已在脚本内有界重试；`probe` 的服务发现目前不自动重试，可补做一次探测。只需正文时，也可直接尝试只读 Markdown 请求，以实际返回判断目标是否可读。确认某种格式已读取成功后，按 [部分读取失败](document-io.md#部分读取失败)利用已取得内容。

局部排版需要连接内嵌编辑器。此通道包含底层函数地址与结构布局，因此在使用布局前核对实际加载的 CEF 模块指纹；不能对未知模块直接使用旧地址。这里校验的是接口布局，不是企微版本号。接口无法匹配时只停止相应编辑器操作，原生读取等通道仍按各自实际能力执行。

发生接口兼容错误时，可附上历史测试环境企微 `5.0.12.6018`、CEF `129.0.12+gf09539f` 作为排查参考，不要求用户事先换版本，也不将参考版本描述为所有功能的唯一支持版本。

## 原生文档接口

客户端伴随 Agent 内的请求转发到本机 MCP，再调用文档服务。普通外部进程直接访问同一 MCP 端口不等价。原始 HTTP 与工具响应保留在运行目录，正文和媒体不自动加入模型输入。

异步调用保持初始 trace ID；轮询 `/task/query` 使用原始 `taskid`，请求不另套 CLI 的方法包装。图片上传用专用 `upload_doc_media`，文件导入先 `upload_media`。

## 编辑器内部调用

CEF 库按目标文档的精确 URL 匹配浏览器实例，再在正确线程注册调用与结果回调。每次连接都先读取新的 CAPI 对象清单，保留引用后验证 URL，防止地址被释放或导航到其他文档。

关键 ABI 已经通过机器码与活体表核对：

- 64 位 `cef_browser_t`：208 字节。
- 腾讯 `cef_browser_host_t`：584 字节，72 个回调；与标准头的 568 字节不同。
- `cef_frame_t`：240 字节；task / observer：48 / 80 字节。
- host 的发送、执行和 observer 偏移为 232 / 240 / 248。

`cef_post_task` 和 observer 注册都会消费传入的一份引用。适配器分别保留本地引用与转交给 CEF 的引用，退出前等待注册、task、observer 和回调全部释放，再 unpin、unload、detach。清理未完成时维持会话托管，不能用杀企微或强制卸载解决。

五种 observer 回调传入的 browser 引用由接收方在 `finally` 中释放；`self` 为借用引用。所有权依据该版本生成源码及实际 DLL 调用链核对，详见包内 `scripts/runtime/documents/cef/OWNERSHIP.md`。

`evaluate()` 是拒绝潜在副作用的纯属性读取；`evaluate_document()` 是经过审查的文档 SDK 表达式，执行时再次检查目标 URL。SDK 方法会操作选区与数据模型，不能把表达式模式当成只读权限证明。`apply` 负责快照、版本检查和写后回读。

多个实例返回候选供显式选择；根据实际内容、版本与目标范围判断，不沿用旧实例编号。编辑器未打开时先通过原生文档读取建立所需背景；仍无法取得时报告实际状态，继续可完成的读取和离线准备。

需打开文档时，可从企微“文档”列表按准确标题进入；以 `state --doc-id` 成功匹配且可编辑为准。仅有窗口或链接唤起成功不代表编辑器已加载。页面刷新使用目标明确的 `refresh --doc-id`，待同步队列空闲后执行，并重新读取版本。

修改使用 `window.pad.editor` 的现有文档、选区、图片和内部复制服务。普通企微 domain 不自动挂载 `WebApiHandler`，不能因为缓存有该脚本就假定 `rpc.call` 可用。

本机编辑器指令有长度限制，结果也有体积限制。拆成明确的局部操作，超限时保留输入与快照，不截断表达式或自动扩大修改范围。
