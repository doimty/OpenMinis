# Minis Automation：iOS 15 真机验收

这份指南用于采集新构建的真实设备证据。仓库内测试生成的 JSON、图片和 DEB 都是夹具，不能作为验收结果。目前没有与本轮完整实现对应的 iOS 15 自动化报告，也没有 iOS 16+ 实机证据。

目标链路是：用户明确授权 → MCP 发现工具 → 启动一个独立测试 App → 观察实际屏幕并用 `read_image` 查看 → 根据实际 UI 点击输入框 → 输入公开测试标记 → 独立读取 UI 验证 → 再截图查看 → 手动停止及自然到期后，CLI 拒绝继续操作。

## 安装与准备

需要已有的 Rootless 或 RootHide 越狱环境。使用当前环境的包管理器安装同一次 Actions、同一提交生成的两个实际 DEB：

- Minis Root 主 App：iOS 15 使用 `15.0` 档，scheme 为当前环境对应的 `rootless` 或 `roothide`。
- Minis Automation companion：相同 scheme，最低 iOS 15.0，只注入 SpringBoard。

保留两个包及各自相邻的 `manifest.json`，不要混用历史产物。核对 `source_commit`、`actions_run_id`、scheme 和实际 DEB SHA-256。普通版 App、TrollStore IPA 或旧 helper 身份报告不能代替这两个 DEB 的安装验证。

安装 companion 后，通过设备已有越狱工具的用户界面重启 SpringBoard，使 tweak 加载。本指南不执行或提供自动重启系统进程的命令。之后手动唤醒、解锁设备，打开 Minis Root。

选一个没有私人数据、支持编辑文本且其输入框能由辅助功能树读回的独立测试 App。测试 App 不能是 Minis 或 SpringBoard。先人工准备好一个空输入区，记录它的实际 bundle ID。不能假设某个系统 App、按钮名称或坐标一定存在。

在 Minis 选择一个已存在且可写的测试目录作为 agent shell 的当前目录，使用一个新的 `receipts.jsonl` 文件名。截图会保存在此目录下的 `.minis-automation-captures/` 中。下列命令应由当前 Minis agent 的现有 shell 工具按顺序执行；跨 App 期间不要为了手工输入下一条命令而切回 Minis 终端，这会把前台目标切换掉。交给 agent 实际 UI 结果后，控件选择和后续命令也由它继续；iOS 暂停 agent 时保留失败记录，不能写成后台链路通过。

下列重定向只写 CLI 的原始标准输出；不要加 `--pretty`，也不要把 shell 提示、说明文字或 stderr 混入 JSONL。

```sh
minis-mcp-cli refresh minis-automation > receipts.jsonl
```

上面的首条命令需要先完成下一节的授权再执行。每次成功调用应输出一行 JSON；失败也保留原始单行 JSON。追加回执使用 `>> receipts.jsonl`。已有文件不要覆盖；重新采集时更换文件名，并在没有活动会话时清除旧报告。

## 第一段会话：观察、点击、输入与独立读回

进入“设置 → MCP 集成 → 跨 App 自动化”，在没有活动会话时点“清除自动化会话报告”。点“开始自动化会话”，阅读原生确认中关于整个屏幕、其他 App、点击、输入和 3 分钟有效期的说明，明确点“启用 3 分钟”。开启普通的“允许设备工具请求”不会代替这项跨 App 授权。

跨 App 会话不会因为 Minis 离开前台就撤销，但这不保证 Minis agent 在后台持续运行。iOS 可能提前结束后台执行预算，此时会话报告记为 `background-budget-ended`。这种结果保留作诊断，不能当作本节完整验收通过；不要静默开启后台音频或定位来延长运行。

授权后按顺序采集：

```sh
minis-mcp-cli refresh minis-automation > receipts.jsonl
minis-mcp-cli call minis-automation get_device_info '{}' >> receipts.jsonl
minis-mcp-cli call minis-automation get_screen_info '{}' >> receipts.jsonl
```

接着让 agent 启动事先选定的测试 App。以下 `<TARGET_BUNDLE_ID>` 是占位符，要替换成实际值；JSON 参数使用上游真实声明的 `bundle_id`。

```sh
minis-mcp-cli call minis-automation launch_app '{"bundle_id":"<TARGET_BUNDLE_ID>"}' >> receipts.jsonl
minis-mcp-cli call minis-automation get_frontmost_app '{}' >> receipts.jsonl
minis-mcp-cli call minis-automation screenshot '{}' >> receipts.jsonl
minis-mcp-cli call minis-automation get_ui_elements '{"visible_only":true,"clickable_only":false}' >> receipts.jsonl
```

查看 screenshot 回执顶层 `captures[0]` 中的 `path`、`sha256`、`width`、`height`。必须使用 Minis 当前 agent 的 `read_image` 工具读取这个**实际文件路径**，并在聊天中留下真实观察，例如当前是哪个 App、哪个区域可见。`read_image` 是 agent 工具，不是需要在 shell 里运行的命令。保存该次真实工具调用 ID、截图请求的 `_meta.openminis.request_id` 和观察文字。不要把“截图已保存”的文本提示当作模型已经看过图像。

重定向后的返回值在 JSONL 文件里，agent 应用现有文件读取或 shell 工具查看对应原始行；这些读取动作不追加到回执文件。比如刚执行一条查询后可用 `tail -n 1 receipts.jsonl` 查看最后的 JSON 行，然后据此选择 `read_image` 路径或下一步 UI 参数。

先检查 `get_frontmost_app` 的 `structuredContent.bundleId`，以及 UI 树中的 `bundleId`、`elements`，确认读到独立测试 App。依据实际返回的文本、标签或位置选择输入框：

- 有稳定文本或标识符时，根据工具列表的真实 `inputSchema` 调用 `tap_element`。
- 若只能定位坐标，先补一张截图并实际 `read_image`，再把该图中观察到的屏幕点坐标传给 `tap_screen`。截图像素已对应屏幕点，不要除以 Retina scale。

只选择上述一种点击方式，不要照抄猜测坐标。例如下面仅展示参数形状，`<ACTUAL_LABEL>` 必须来自刚才的实际 UI 结果：

```sh
minis-mcp-cli call minis-automation tap_element '{"text":"<ACTUAL_LABEL>","match":"exact"}' >> receipts.jsonl
```

生成一个本次独有、公开无隐私的标记，格式为 `MINIS-AUTOMATION-` 加 12–32 个小写十六进制字符。例如可以在设备终端用下列 Python 命令**生成标记本身**，它不会调用设备工具或获取 token：

```sh
python3 -c 'import uuid; print("MINIS-AUTOMATION-" + uuid.uuid4().hex[:16])'
```

把真实生成的标记放进 `input_text` 的 `text`，然后单独调用 UI 读取和截图：

```sh
minis-mcp-cli call minis-automation input_text '{"text":"<PUBLIC_MARKER>"}' >> receipts.jsonl
minis-mcp-cli call minis-automation get_ui_elements '{"visible_only":true,"clickable_only":false}' >> receipts.jsonl
minis-mcp-cli call minis-automation screenshot '{}' >> receipts.jsonl
```

输入请求失败、超时、断线或返回结果不确定时，先独立观察，不要自动发送相同文字。输入命令的成功字符串或请求参数回显不算读回证据。验收要求后一个 `get_ui_elements` 的同一测试 App `elements[*].text` 中出现标记，输入前的 UI 树没有该标记。

再次用 `read_image` 读取最后截图的实际路径，记录真实调用 ID 与观察文字。检查标记所在的输入区、输入后的界面变化。截图文件、UI 树和真实视觉观察是三份不同证据；保存 JSON 并不能替代视觉工具调用。

回到设置页点“停止自动化会话”，随后只做一次无副作用查询，保留拒绝输出：

```sh
minis-mcp-cli call minis-automation get_screen_info '{}' >> receipts.jsonl
```

预期 CLI 退出码为 1、`code` 为 `AUTOMATION_UNAVAILABLE`。不要把 `AUTOMATION_OUTCOME_UNKNOWN` 当作拒绝成功；它表示调用可能已经发送，结果未知。不要重试交互动作。

## 第二段会话：自然到期

再次在设置页明确开始新的 3 分钟会话，保留第一段报告。新会话 UUID 必须与前一段不同。采集新的工具发现和基线查询：

```sh
minis-mcp-cli refresh minis-automation >> receipts.jsonl
minis-mcp-cli call minis-automation get_screen_info '{}' >> receipts.jsonl
```

让 Minis 保持前台，人工等待倒计时自然归零，不要点停止，不要提前清除报告。达到 180 秒后，再运行一次相同查询并保留拒绝输出：

```sh
minis-mcp-cli call minis-automation get_screen_info '{}' >> receipts.jsonl
```

报告中应有同一第二会话的 `started` 与 `expired`，`observed >= expires`。提前因系统后台预算结束记为 `background-budget-ended` 时，不能算自然 TTL 验收。

点“复制自动化会话报告”，将剪贴板 JSON 原样保存为 `session-report.json`。报告含主 App 构建、运行环境、实际 UID/EUID 和最多 64 条生命周期事件，不含 token。活动会话期间“清除自动化会话报告”不可用，清除报告也不会作为停止动作。

停止与到期的 CLI 拒绝发生在发送 HTTP 前，只证明 native 和客户端门禁阻止了继续调用。它们不能证明 companion 使用旧凭据收到 HTTP 请求后实际返回了 401。服务端 STOP/TTL/generation 门禁另有生产原生测试；若没有独立设备负例，真机服务端拒绝仍写为未验证。不要为补这项证据而把 bearer 复制到聊天、回执、shell 历史或导出文件。

## 导出与索引

导出以下真实文件，保留原始内容：

1. 主 App 与 companion 的实际 DEB 及各自 manifest。
2. `session-report.json`、原始 `receipts.jsonl`。
3. 第一段选定的输入前、输入后两张截图；从回执的真实 `captures[].path` 导出。可在本地重命名，字节和 SHA-256 必须相同。
4. 两次真实 `read_image` 调用的聊天记录或工具日志引用，以及观察文字。

新增 `evidence.json` 作为**人工采集索引**，只引用上面实际取得的内容，不把它当作设备自动签名的报告。字段：

| 字段 | 填写内容 |
| --- | --- |
| `schema` / `kind` | `1` / `minis-automation-device-evidence` |
| `main_manifest_sha256` / `companion_manifest_sha256` | 两份原始 manifest 文件的实际 SHA-256 |
| `source_commit` / `actions_run_id` | 对应真实 Actions 构建 |
| `target_bundle_id` / `public_marker` | 本次测试 App 与真实输入标记 |
| `primary_session_id` / `expiry_session_id` | 原生报告中第一段、第二段 UUID |
| `steps` | 下表步骤到 JSONL **从 0 开始的行索引**映射 |
| `captures` | 两项：`receipt_index`、导出目录内的相对 `local_file`、真实 `sha256` |
| `observations` | 两项：`receipt_index`、该截图真实 `request_id`、`capture_sha256`、真实 `read_image_call_id`、真实 `observation` |

`steps` 必须包含以下键，索引严格递增：

```text
discovery, device_info, screen_info, launch, frontmost, screen_before,
ui_before, tap, input, ui_after, screen_after, stop_rejected,
expiry_discovery, expiry_baseline, expiry_rejected
```

可包含实际发生的额外成功观察调用，因此不要机械照抄固定行号。`screen_before` 是首次观察截图；`screen_after` 是标记输入后截图。`ui_before`、`ui_after` 必须是两个独立 `get_ui_elements` 请求，不能引用输入响应。`request_id` 来自对应截图 `result._meta.openminis.request_id`，不是自己新建的 UUID。

索引中的截图相对路径不能包含 `..`、绝对路径、盘符或反斜杠。不要在任何材料中加入 token、headers、Authorization 或配置 bridge 输出。

## Windows 离线关联检查

在仓库根目录执行，替换所有路径。iOS 15.1.1 示例 OS 字符串来自真实系统报告；其他设备按实际值填写。

```powershell
py -3 scripts/verify_automation_device_evidence.py `
  --main-manifest "C:\path\to\main\manifest.json" `
  --main-deb "C:\path\to\main\<MAIN_DEB>.deb" `
  --companion-manifest "C:\path\to\companion\manifest.json" `
  --companion-deb "C:\path\to\companion\<COMPANION_DEB>.deb" `
  --report "C:\path\to\evidence\session-report.json" `
  --receipts "C:\path\to\evidence\receipts.jsonl" `
  --evidence "C:\path\to\evidence\evidence.json" `
  --capture-dir "C:\path\to\evidence\images" `
  --expected-os "Version 15.1.1 (Build 19B81)"
```

检查器拒绝重复 JSON 字段、非有限数、超限文件、secret、构建或 manifest SHA 不匹配、非实机 runtime、重复 trace UUID、跨 grant/generation 的操作链、缺少独立标记读回、截图替换、未知调用结果冒充拒绝，以及缺少自然到期记录。它会核对实际 DEB 文件 hash 与原始 manifest；manifest 的来源仍需由操作者从可信 Actions 下载确认。

通过只表示这组用户采集文件互相对应。JSON 校验器不能证明图片已被模型读取，不能验证人工观察是否真实，也不提供密码学设备证明；两次真实 `read_image` 日志必须由人或对应工具记录另行复核。iOS 16+ 的实际启动、截图、AX/HID、输入、后台预算和 RootHide 运行行为仍未验证。

本流程不要求补做旧 helper 的取消或后台实验。旧固定样本的 root 身份报告和这次跨 App 自动化验收分别保留。
