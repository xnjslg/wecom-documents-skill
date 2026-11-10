# CEF observer 入参引用

已支持的 libcef SHA256：`305967f575bc139b13975d080f620103993b65a6bbef9672c07d3f036743c59f`。

五种 observer 回调各自接收一份 `browser` CAPI 引用，由接收方释放。`self` 是调用对象，不转移引用。统一的 `observerCallback` 在 `finally` 中释放 `browser`，覆盖正常返回、未知请求 ID、校验失败和 JSON 解析异常；释放期间保持 observer 的 active 计数。

精确上游版本 f09539f 的[生成回调实现](https://github.com/chromiumembedded/cef/blob/f09539f/libcef_dll/ctocpp/dev_tools_message_observer_ctocpp.cc)在五处传入 `CefBrowserCppToC::Wrap(browser)`，调用后没有释放该 CAPI 包装。[CppToC 引用实现](https://github.com/chromiumembedded/cef/blob/f09539f/libcef_dll/cpptoc/cpptoc_ref_counted.h)的 `Wrap` 新建包装并增加一份跨边界引用；正常 C++ 接收方通过 [CToCpp Wrap](https://github.com/chromiumembedded/cef/blob/f09539f/libcef_dll/ctocpp/ctocpp_ref_counted.h)接收并消费该引用。

当前 Tencent 二进制的静态调用链为 `add_dev_tools_message_observer` RVA `0x2a73430` → CToCpp 构造器 `0x2a87860` → 方法表 RVA `0xc8b8ae0`。方法表五项如下：

| 回调 | CAPI 偏移 | 原生方法 RVA | browser 包装构造调用 RVA | 包装 ref++ RVA | 外部回调调用 RVA |
| --- | --- | --- | --- | --- | --- |
| message | 40 | `0x2a873f0` | `0x2a87489` | `0x2a874af` | `0x2a874e8` |
| result | 48 | `0x2a87500` | `0x2a875bc` | `0x2a875e1` | `0x2a87623` |
| event | 56 | `0x2a87630` | `0x2a876ec` | `0x2a87712` | `0x2a87750` |
| attached | 64 | `0x2a6d230` | `0x2a6d2c0` | `0x2a6d2e5` | `0x2a6d317` |
| detached | 72 | `0x2a87760` | `0x2a877f0` | `0x2a87815` | `0x2a87847` |

五处均分配 248 字节的 browser CppToC 包装，调用构造器 RVA `0x2a6f080`，保存原 C++ browser 到包装 `+0x10`，增加包装 `+0xf0` 的引用计数，再把包装 `+0x20` 作为 CAPI 参数传给外部回调。回调后的尾路径只释放原 C++ browser 参数，不接触新包装的引用计数。attached 采用与其它相同签名实现折叠共享的函数地址，仍从 observer 方法表明确引用。

离线模型为每次回调生成独立 browser 包装，转移一份引用，不在调用方自动回收。测试验证五种回调及 result 的正常、早退、错误 browser ID、超限结果、错误 JSON、无效 self 路径均准确消费该引用，observer self 仍由 registration 和本地所有者管理。历史扫描候选不做补偿性释放。
