# Minis Automation：跨 App 自动化接续方案

状态：2026-11-21 的实现契约与源码依据。主要服务端、Minis 授权界面、MCP 接线、图像落盘和验收校验器已进入源码，仍在 GitHub Actions 验证与修复；没有本轮真机成功结论。下文保留设计取舍，当前具体实现以 `jailbreak/automation/`、`AutomationMCPController.swift` 和 Python MCP 源码为准。

## 目标与现状

独立越狱版 Minis 需要在 MCP 工作流中执行真正的设备观察和交互。当前 `minis-device` 的四个 UIKit 工具，以及设置页中操作固定样本的 Root helper，提供了权限和授权基础；它们不能证明任意 App 的截图、点击、滑动、输入或前后台切换已经可用。

用户指定 DEB 只交付 Rootless、RootHide。主 App 继续使用 15.0、16.0、16.2 三档，保留对应分享、File Provider、Widget；本文的 companion 无 App 扩展，只需两份最低 iOS 15.0 的 tweak DEB。iOS 16+ 的实际运行仍需单独记录，不能由安装下限或上游报告代替。

最短实现路径是在 SpringBoard 中运行独立的 `Minis Automation` companion，复用钉定版本 iOS MCP 的设备实现，增加本项目的有限授权层。Minis 通过现有 HTTP MCP 客户端访问本机服务；授权有效期不依赖 Minis 是否始终处于前台。保持当前四工具的逐次前台确认，并为跨 App 会话使用独立的开始、停止和到期机制。

已实现的原生会话固定为 180 秒，reader 最多接受 600 秒的协议上限，但 UI 不提供延长或自动续期。companion 使用纯 Xcode/Clang 构造函数注册控制入口，无 Logos hook、PreferenceLoader 或 Theos 构建依赖；只依赖现有 tweak 注入环境，RootHide 另依赖官方 `roothide` 核心库。`prepare_sources.py` 校验原始上游每文件哈希，生成独立命名空间与受限执行源码；`build.sh`、`verify_build.py` 和 `package_automation_deb.py` 检查两架构最低系统、签名、链接、过滤器及归档。

同一 boot 内已停止的授权 ID 和时间高水位不能被旧 START 恢复；旧请求 ticket 不会借新会话继续。HID、Unicode 输入和 App 切换在执行前重查有效会话与锁屏状态，长按等待按不超过 10ms 的片段检查撤销，释放触点/按键始终放行。这些是生产代码和门禁覆盖的契约；真实 SpringBoard 的私有 API 行为仍需设备证据。

## 已核对的上游

固定源码：[`witchan/ios-mcp` 的 `62c4464f688c8f269419924b1a19c996de75f175`](https://github.com/witchan/ios-mcp/tree/62c4464f688c8f269419924b1a19c996de75f175)，对应 [`v1.2.8`](https://github.com/witchan/ios-mcp/releases/tag/v1.2.8)。该版本公布 46 个 MCP 工具。

| 项目 | 已核对的实际源码 |
| --- | --- |
| 运行宿主 | `ios-mcp.plist` 只注入 `com.apple.springboard`；`Tweak.x` 的构造函数和 SpringBoard 生命周期 hook 启动服务。它不是一个独立 launchd daemon。 |
| HTTP | `MCPServer.m` 的 `handleClient:listenerPort:` 接收 `POST /mcp`，`routeMCPRequest:` 实现 `initialize`、`notifications/initialized`、`tools/list`、`tools/call`、`ping`；回包是 JSON-RPC JSON。 |
| MCP 兼容 | 默认 `2025-11-25`，兼容 `2025-06-18` 与 `2025-03-26`；回包包含 `Mcp-Session-Id`、`MCP-Protocol-Version`。Minis 的 `transport/http.py` 使用 `2025-06-18`，接口形状相符。 |
| 上游默认值 | `IOSMCPPreferences.h` 默认端口 8090；`Tweak.x` 的 `ios_mcp_enabled_preference()` 在 key 不存在时返回 YES；`MCPServer.m:985` 绑定 `INADDR_ANY`。README 明确没有内置认证。 |
| 工具分发 | `MCPServer.m` 的 `handleToolsList:` 枚举工具，`handleToolsCall:params:` 分发；另有 `/upload_file`、`/download_file` HTTP 路由，不能只过滤 `tools/list`。 |
| 锁屏保护 | `MCPDeviceStateRequiresWakeOrUnlock()`、`lockedScreenGuardResponseForTool:reqId:` 与 `ScreenManager.deviceInteractionState` 可复用；上游对锁屏状态未知采用尽力判断，需要在实际设备确认。 |
| 打包 | `Makefile` 支持 `THEOS_PACKAGE_SCHEME=rootless`、`roothide`，这两种 target 最低 15.0；RootHide 额外链接 `roothide`。 |

固定引用：

- [MCPServer.m](https://github.com/witchan/ios-mcp/blob/62c4464f688c8f269419924b1a19c996de75f175/MCPServer.m)
- [Tweak.x](https://github.com/witchan/ios-mcp/blob/62c4464f688c8f269419924b1a19c996de75f175/Tweak.x)
- [IOSMCPPreferences.h](https://github.com/witchan/ios-mcp/blob/62c4464f688c8f269419924b1a19c996de75f175/IOSMCPPreferences.h)
- [Makefile](https://github.com/witchan/ios-mcp/blob/62c4464f688c8f269419924b1a19c996de75f175/Makefile)

上游发布的 Rootless DEB 为 `com.witchan.ios-mcp_1.2.8_iphoneos-arm64.deb`，GitHub release digest 为 `2b1321c8b2723ae2bf0bfa15425f800275651d34a67f6b4d4d25c2c670aae336`；RootHide DEB 为 `com.witchan.ios-mcp_1.2.8_iphoneos-arm64e.deb`，digest 为 `9cce934fe76ab421cb29f14c09be2b44251fda4cbc87c2e3559be9df8a580f33`。这仅能定位上游原包；原包的全接口监听、默认启用、无认证行为不满足下面的 Minis 会话契约，不能直接换名作为本项目 companion。

## Companion 的固定契约

包名建议 `com.openminis.automation`，tweak 名 `MinisAutomation`，只注入 SpringBoard，最低 iOS 15.0。使用 `127.0.0.1:8091/mcp`，避免与上游 8090 混淆。不接收可配置远端 URL，不开放 LAN 地址。

授权初始状态关闭。安装、重启 SpringBoard、缺失或损坏的会话文件均不得自动开启自动化。上游原有 `enabled` 偏好及 START/STOP 通知不能继续控制此 companion，需要改为本项目独立的域和通知名。

建议控制契约如下，具体字段在实现时由一个共享协议文件统一定义：

| 项目 | 建议契约 |
| --- | --- |
| 固定控制文件 | `/private/var/mobile/Library/OpenMinisAutomation/session.json`，仅当前 mobile 用户读写，目录 0700、文件 0600；拒绝符号链接、硬链接、错误 owner、额外字段及超大文件。 |
| 会话内容 | schema、随机会话 UUID、256 位随机 bearer token、连续单调时钟截止值、固定工具集版本。若增加目标 App 白名单，应在此记录，并在前台确认时显示。 |
| 有效期 | 用户在前台选择，建议默认 180 秒、上限 600 秒；使用 `mach_continuous_time()` 计时并跨睡眠过期，修改系统时间不能延长。不得自动续期。 |
| 启用 | Minis Root 设置页前台明确确认后，原生层原子写入新文件并发送本项目 START 通知。companion 只因新的控制通知加载授权，不在启动时恢复遗留会话。 |
| 停止 | Minis 删除控制文件并发送 STOP；companion 立即使当前 generation 失效、关闭 listener、取消尚未执行的事件。到期由 companion 自己执行同样撤销。 |
| 认证 | 每个 `/mcp` 请求都要求当前会话的 `Authorization: Bearer <token>`。固定时间比较 token；MCP session ID 不是授权凭据。缺失、错误、到期 token 在工具执行前失败。 |
| 重启 | companion 进程重新加载后默认停用，旧文件不恢复授权；用户必须重新开始会话。 |

原生确认文案必须说明有效期、可观察当前屏幕及可操作其他 App，提供“开始自动化会话”“停止自动化会话”和剩余时间。新界面提供英语、简体中文、繁体中文。token 不展示在 UI、聊天、日志、导出和 iCloud 同步中。

这里的会话可以跨越 Minis 退到后台，但不能延长 Minis 自身的系统执行预算。已有 `BackgroundKeepAliveManager` 有用户可选的后台运行路径；未启用时，agent 仍可能被 iOS 暂停。不得为了让自动化继续而静默替用户开启定位或后台音频。验收应分别记录 companion 在 Minis 后台可接收调用，以及 Minis agent 在目标设备后台能继续多少时间。

## 首批固定工具白名单

目标是能完成“观察一个已授权 App → 点击/输入 → 再观察结果”的真实链路。初始白名单为：

| 范围 | 工具 |
| --- | --- |
| 屏幕与 App 状态 | `get_device_info`、`get_screen_info`、`screenshot`、`get_frontmost_app`、`list_apps`、`list_running_apps` |
| UI 元素 | `get_ui_elements`、`get_element_at_point`、`wait_for_element`、`wait_for_disappear` |
| 触控 | `tap_screen`、`tap_element`、`swipe_screen`、`long_press`、`double_tap`、`drag_and_drop` |
| 输入 | `input_text`、`type_text`、`press_key` |
| 前后台切换 | `launch_app`、`kill_app`、`press_home` |

在 `handleToolsList:` 和 `handleToolsCall:params:` 两处使用同一份固定白名单；伪造调用不在列表中的名称也必须被拒绝。禁用所有 upload/download 路由，不挂载额外文件访问端点。

本阶段不开放 `run_command`、`list_dir`、`read_file`、`write_file`、`install_app`、`uninstall_app`、日志读取、剪贴板、任意 URL scheme 或修改音量等其他工具。这些属于另外的用户授权与交付范围，不能通过透传 upstream 名称绕过。

初始白名单不依赖 PaddleOCR；截图和 UI 树已经能支持可观察的操作链。若之后加入 OCR，先对系统 Vision 的中英文与最低版本做真机验证，再明确新增引擎及其依赖；不要悄悄依赖上游默认的 Paddle 模型/worker。

所有事件在执行前检查会话有效性。长手势、分段文字输入和等待元素不能只在 HTTP 入口查一次：每个后续事件应携带原始 session generation，在发送 HID 或文本前再次确认，撤销后释放按下中的触点并停止后续事件。新会话不能让旧会话排队的事件继续执行。已经发生的点击或输入没有事务回滚语义。

锁屏、屏幕关闭时拦截交互；若无法可靠确定交互状态，应返回明确的不可用状态。首版可以要求用户手动唤醒和解锁后重新开始会话，不能通过工具尝试绕过锁屏认证。

## 要改动的上游文件与函数

| 源码 | 实现点 |
| --- | --- |
| `IOSMCPPreferences.h` | 替换端口、preferences domain、通知名；不能沿用上游自动启用偏好。建议 companion 改用独立 `MinisAutomationSession.h/.m` 共享状态。 |
| `Tweak.x` | 替换构造/START/STOP/生命周期逻辑；删除 `ios_mcp_autostart_if_needed()` 与延时自动恢复路径的授权作用。只注册控制入口，初始无活动会话。 |
| `MCPServer.m` 的 `startListenerLockedOnPort:` | `INADDR_ANY` 改为 `htonl(INADDR_LOOPBACK)`，固定端口，并保证 stop/TTL 能撤销 listener 的重试意图。 |
| `MCPServer.m` 的 `handleClient:listenerPort:` | 解析完 headers 后检查 bearer、会话和到期；只放行 `/mcp` 及不含敏感信息的版本/状态健康检查。禁止 upload/download 路由。不记录 authorization header。 |
| `MCPServer.m` 的 `handleInitialize:` | serverInfo 名称改为 `minis-automation`，保留兼容的协议协商；instructions 仅说明实际白名单和撤销/坐标语义，不提示不存在的 shell、安装、文件或日志能力。 |
| `MCPServer.m` 的 `handleToolsList:`、`handleToolsCall:params:` | 同源白名单、同一会话检查、单个交互在途限制；拒绝未知字段和非白名单工具。观察与交互回执携带 request ID、会话 generation 和来源提交。 |
| `HIDManager.m` 的 `postHIDEvent:error:` 及手势循环 | 增加会话取消检查；撤销时结束已按下触点，阻止继续发事件。 |
| `TextInputManager.m` 的分段 Unicode/HID 输入路径 | 捕获调用时的 generation，逐段检查；不得因错误自动重放已发送文字。 |
| `AppManager.m` 的 `launchApp:error:`、`killApp:error:` | 对 target 做明确限制；至少拒绝终止 SpringBoard 等系统宿主及 companion 自身的控制宿主。若使用目标 App 白名单，这里和触控/输入前台检查共用它。 |
| `ios-mcp.plist`、`control` | 改为本项目身份，保留 SpringBoard filter；只构建 Rootless/RootHide，依赖实际 tweak injection runtime。UI 在 Minis 内时无需 PreferenceLoader。 |
| `Makefile` | 新建 companion 专用入口，避免执行上游 `after-stage`，不要打入 AppSync、签名绕过、setuid root helper、ldid、Paddle worker 或模型。 |

与系统原生类重名之外，还要避免和设备可能已安装的上游 iOS MCP 自有 ObjC 类重名。构建前为复用的所有项目自有 ObjC 类/C 全局符号增加 Minis 前缀，或明确设计互斥安装。不能只改 dylib 文件名和端口：同一 SpringBoard 中仍会发生 `MCPServer`、`AppManager` 等类名冲突。优先使用类名映射/源补丁并核对 `NSClassFromString` 字面量，保留与上游并存能力。

## 最小源码与 CI 依赖

直接复用 MIT 的 HTTP/router 与设备模块时，可以先保留上游主 tweak 的完整项目自有 `.m/.h` 文件列表以避免大量路由裁剪，但必须在入口拒绝不交付的功能，并使用专用打包规则排除其外部工具。名单包括 `MCPServer`、`MCPLogger`、`HIDManager`、`ScreenManager`、`ClipboardManager`、`AppManager`、`AccessibilityManager`、`TextInputManager`、`FileSystemManager`、`LogManager`、`OCRManager`、`MCPProcessUtil`、`MCPAXQueryContext`、`MCPAXRemoteContextResolver`、`MCPUIElementSerializer`、`MCPUIElementsFacade`、`MCPAXAttributeBridge`、`MCPAXNodeSource`、`VisionOCREngine`、`PaddleOCRManager`、`MCPOCRRequestContext`，以及它们的私有接口头。这里保留文件仅用于已核对的可链接构建，不等于开放文件中的全部工具。

设备实现中，与当前白名单直接相关的入口是 `ScreenManager.takeScreenshotPayload`、`screenInfo`、`deviceInteractionState`，`AccessibilityManager` 的 UI 树/命中点接口，`IOSMCPHIDManager` 的手势接口，`TextInputManager` 的三种输入接口，以及 `AppManager` 的 App 查询和前后台接口。裁剪源码时，要先删除 MCP router 对被裁剪 class 的引用，不能把链接失败改成静默空实现。

CI 构建全部在 GitHub Actions macOS 进行。最小环境是 Xcode/iphoneos SDK、固定版本 Theos/Logos、make、ldid、dpkg-deb（或项目独立归档器）及 Theos 的 jailbreak headers/stubs；RootHide 使用固定版本的官方 roothide 库/header。可用的官方 Theos fork 固定点为 `roothide/theos@958fea4f779943505b15b93c88bb4db8d97b346d`，取用前仍应校验 submodule 和 SDK 的实际解析。普通 App 的 Xcode target 不加入这些编译配置或依赖。

系统框架依据上游 Makefile 为 IOKit、UIKit、CoreGraphics、QuartzCore、MobileCoreServices、AVFoundation、Security、Vision；实际使用 ImageIO 的保留代码也需要链接 ImageIO。独立 companion 不编译 Settings preference bundle，因此不需要上游专为 prefs 设置的 `iPhoneOS16.5.sdk/System/Library/PrivateFrameworks` 路径。

完整上游 `build.sh` 另外构建 AppSync、appinst、roothelper、ldid、setuid mcp-root、logreader 和 Paddle worker，并要求预先生成 ONNX Runtime/OpenCV 静态库、模型和字典。这些不是本白名单 companion 的必要依赖。不要直接执行该脚本再删除校验来取得出包结果；使用专用的可审计 Makefile/打包入口，并验证实际包内只包含需要的 tweak、filter、许可证和 provenance。

## Minis 侧需要接上的文件

1. 新增 Root-only 原生 controller/协议，负责前台确认、token 生成、固定文件原子发布、Darwin START/STOP、剩余时间和报告。它独立于 `DeviceMCPController` 的逐次前台门禁；普通版不编入功能。
2. `MCPStore.swift`、`utils/config.py` 为 `minis-automation` 增加与 `minis-device` 同等的保留名称规则。只在本机有效会话存在时注入 HTTP 配置，不从 `servers.json`、导入或同步接受同名项。bearer 只经本机临时 bridge 交给 transport，不写同步配置。
3. `main.py`、`daemon.py` 及 `transport/http.py` 一起禁止自动重放自动化 `tools/call`。现有 transport 的 `_call_with_reconnect()` 会因 session 错误重试，仅在外层设置 `replay=False` 不够；发现/握手可以重新建立，已发送的交互必须返回明确的未知/失败结果。
4. 截图结果必须到达视觉输入链。当前 `cmd_call()` 只 `_emit(result)`，MCP image Base64 会变成 shell 文本。对 `minis-automation` 的 screenshot 结果校验 MIME/字节上限，保存到固定会话截图目录，回传文件路径和 SHA-256，随后使用现有 `read_image` 工具。不得把 Base64 大串当作模型已经看到了图像。
5. `MCPIntegrationsView.swift` 增加单独中文会话 UI。系统提示/会话工具列表要明确当前白名单、有效期和屏幕点坐标；截图是 point-sized，不能重复按 Retina scale 缩放点击坐标。
6. 复用 `AIChatViewModel+ConcurrentTools.swift` 的 `read_image` 与已有后台执行能力，但分别验证真实图像输入、后台 agent 存活与 companion 跨 App 接收操作，不由任一项代替其他项。

## 许可证与来源材料

上游项目自有代码使用 MIT，需保留 copyright、完整 `LICENSE` 和 `NOTICE`，在 companion 中标明上游仓库、固定提交、本地补丁与构建提交。独立记录保留文件列表和 hashes，方便审计与更新。

上游 [`THIRD_PARTY_NOTICES.md`](https://github.com/witchan/ios-mcp/blob/62c4464f688c8f269419924b1a19c996de75f175/THIRD_PARTY_NOTICES.md) 另列 AppSync/appinst 的 GPL-3.0、ldid 的 AGPL-3.0、静态 libplist 的 LGPL 等要求。专用 companion 不打入这些外部组件，仍保留来源说明并在归档检查中证明它们未被混入。若未来重新加入其任何组件，必须同时提供对应许可证、修改源码和适用的重链接材料；不能以项目主许可证是 MIT 覆盖第三方要求。

## 完成所需证据

CI 必须执行生产授权/TTL代码的单元测试，验证默认关闭、错误 bearer、到期、撤销、重启、并发、非白名单名称、隐藏 HTTP 文件路由、循环中止、旧 generation 不能借新会话继续、无交互重放，以及截图正确落盘和 `read_image` 能读取。静态字符串搜索只能补充这些测试。

Rootless/RootHide 包分别读回 architecture、安装根、依赖、Mach-O 架构/最低系统、签名、filter、对象命名、许可证、上游 pin、本地 patch hash。不得把包归档合法当作 SpringBoard 已成功加载 tweak。

至少使用现有 iOS 15 真机完成同提交、同 Actions run 的：用户开始会话 → MCP initialize/tools/list → 取得独立 App 的屏幕信息/截图并实际显示给模型 → 启动测试 App → 点击与输入一个公开测试标记 → 再截图或 UI 树独立读回标记 → 停止/到期后再调用被拒绝。记录 App UID/EUID 与 companion 宿主身份、请求 ID、会话 ID、截图 hash、可观察结果和原始 MCP 回执。helper 的旧固定样本报告不能替代这组证据。

没有 iOS 16+ 真机时，交付说明明确：对应扩展、编译与打包检查已覆盖的内容，以及实际设备上尚未验证的启动、截图、AX/HID、输入、后台和 RootHide 运行行为。只有新证据通过后，才能把“跨 App 设备自动化”记为已实现并在目标设备验收。
