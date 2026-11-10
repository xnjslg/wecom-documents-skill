# 可编辑原生公式

公式使用企微原生 `Math` / OMath 结构。原生 Math 的开始、结束字符为 U+0016 / U+0017；图片 Drawing 或复制得到的“[数学公式]”占位文本不能证明公式结构正确。

## 确认范围

从最新原生树及 `locate --text-file` 找实际 LaTeX 文本、行内 `$...$`、独立公式和多余分隔标记。按本次真实值构造替换源串，不对全文反斜杠或美元符号做通用替换。GCP 是 UTF-16 单位；Python 计算前缀长度时使用 `len(prefix.encode('utf-16-le')) // 2`，优先采用接口给出的范围。

保留独立公式的原有段落与排版，行内公式保持周围文字、标点、粗体和列表编号。需要删除相邻段落或分隔标记时，单独列出精确范围并验证段尾字符。

## 取得原生片段

1. 将本次实际公式整理为 JSON 数组，每项明确给出 `latex` 字符串和 `display` 布尔值；表达式不包含外围 `$` 标记。
2. 在已有环境生成单独的素材 DOCX：

```powershell
$skill = (Get-Location).Path
& $python "$skill/scripts/math_docx.py" `
  --input C:/任务目录/equations.json --output C:/任务目录/formulas.docx
```

公式转换另需 `& $python -m pip install -r ./scripts/requirements-math.txt` 安装 `latex2mathml`、`python-docx`、`lxml`，以及本机 Office 的 `MML2OMML.XSL`。脚本从系统 Program Files 下发现该文件；也可用 `--xsl` 或 `WECOM_MATH_XSL` 指定实际路径。转换结果需与实际表达式核对，复杂语法以本次转换与回读为准。

3. `upload --kind media` 后，将 DOCX 导入单独的临时文档。读取其 OOXML，使用 `inspect_snapshot.py` 取得外层 Math 的真实范围。导入覆盖临时素材后若当前编辑器仍是旧内容，确认队列为空，再显式 `refresh --doc-id` 刷新该素材文档。
4. 构造 `{"ranges":[{"start":整数,"end":整数}]}`，范围包含完整 Math 起止字符，不带段尾。用 `make_edit.py --kind copy-math` 生成表达式，通过素材文档的 `apply` 执行，结果 JSON 即 `{samples:[...],version:...}`。

`copy-math` 使用 `clipboardManager.storeSelectionInnerData(真实GCPRange,false).innerData`，只读取原生片段并更新客户端内存复制状态，不写系统剪贴板。`copyParserManager.doCopy()` 没有 `innerData` 字段。

5. 规范化原生片段中的正体文字开关：

```powershell
& $python "$skill/scripts/math_fragments.py" `
  --input C:/任务目录/copied-result.json --output C:/任务目录/native-fragments.json
```

Office 的空 `<m:nor/>` 表示正体文字，但当前客户端 `CTOnOffShdM.val` 默认 false。已实测空 `nor:{}` 经复制粘贴会丢失正体语义，需仅将已有的空值显式写成 `nor:{val:true}`。脚本只处理 `ModifyRunProp.property.mRPr.nor`，其余操作保持不变。

## 写回目标

对每个明确目标构造 `native-math` 参数：

| 字段 | 来源 |
| --- | --- |
| `start`, `end` | 目标文档当前 GCP 范围 |
| `source` | 该范围的原始文本，含待移除的外围标记 |
| `innerData` | 规范化后的实际原生片段字符串 |
| `rawUtf16` | 素材中该公式的完整 UTF-16 代码单元数组 |

通过 `make_edit.py --kind native-math` 生成并审查表达式，随后执行目标文档的 `apply`。脚本精确删除源串，调用既有 `pasteTemplate(innerData字符串)`，核对 MathStream、完整起止字符和同步队列，再取服务器快照。

只复制 Math、不复制段尾时，素材的 `isOMathPara` 不会自动移植。此流程让原来独立成行的公式继续处于原段落，保留原段落属性；两个行内公式也不新增段落。需要额外改变公式段落布局时，按用户要求另行验证。

分隔标记使用 `delete-text`，参数是 `start`, `end`, `source`, `after`；`after` 是删除后应出现的已知紧邻文本。按范围从后往前操作，或每次重新定位，检查结果后处理下一项。

## 验证

将目标 `Math` 与素材的语义 signature 比较：文字、下标/上标角色、每层相对位置及 `mRPr` 均需一致；作者和修改时间单列为修改元数据。核对 `\text` 对应正体开关、乘号、常数及标签，不以节点总数代替语义核验。

对允许修改的段落之外做完整树比较；段内还要核对非公式文字、粗体、标点、编号和原有段落属性。纯 Math 的已验证复制数据只有 `InsertText`、`ModifyRunProp`、`ModifyMathProp`，`subStory=[]`、`builtinStyleName=''`；不改 `srcGlobalPadId` 伪装来源，也不把含附件等其他操作的数据当成同一已验证情形。
