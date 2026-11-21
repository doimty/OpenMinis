# Minis Root / iOS 15 与 16+ 候选及验收记录（2026-11-21）

目标是交付越狱版 Minis Root，兼容 iOS 15，并保留 iOS 16 及更高系统对应的扩展和打包路径。所有 C、Swift、原生依赖和 App 编译均在 GitHub Actions 执行；Windows 本机运行 Python 夹具和源码检查。打包下限、编译成功和真机运行分别记录，不互相替代。

回退固定为 `ff1f00fffdeee9d167253eb128134dcc33a2dca8`。本候选使用 `feat/ios15-trollstore-root-20261121`，普通版、Minis Files 和 Root 的验收分别记录。

## 旧构建的已核实结果

| 范围 | Actions / 提交 | 实际结果 |
| --- | --- | --- |
| 文件探针 | `44862733539` / `dd2222af2ee4b5a28d6567b850ee3a7b0d8ad6d9` | 失败。完整 App 首错是 `HostFileAccessProbeView.swift:63` 找不到 `OMHostProbeMatch`，随后所有探针 C 声明不可见。 |
| 输入框独立修复 | `44859280729` / `0640bc90be6f1d67bc9758f462f9c11f64e54e21` | 成功构建并上传 IPA；未收到真机界面验收。 |

输入框 IPA 已下载并读回：`Minis-1.15-ios15-trollstore.ipa`，大小 `102990722` 字节，SHA-256 `85b8ce37a82fba3bca6da559a95859e4f1eaeaabed271cb5a85d43794acb1665`。主 App 与分享扩展的 `MinimumOSVersion` 均为 `15.0`。

探针的单独类型检查带有 `-Xcc -DMINIS_HOST_FILE_PROBE=1`，完整 `xcodebuild` 之前只有 C 编译开关和 Swift 编译开关。Swift 的 Clang importer 未得到该宏，桥接头隐藏了声明。本候选给探针与 Root 完整构建均补齐 importer 宏，并加入对应回归检查。

## 候选实现

| 对象 | Root 候选值 |
| --- | --- |
| 主 App | `com.openminis.app.root` / `Minis Root` |
| App Group | `group.com.openminis.app.root` |
| 分享扩展 | `com.openminis.app.root.ShareExtension` |
| 分享入口 | `minis-root://share` |
| Keychain group | `com.openminis.app.root` |
| helper | `Payload/Minis.app/minis-rootprobe`，Unix 普通文件 `0755` |
| 特权启动 | 运行时解析 persona API，persona `99`、override `1`、UID/GID `0` |
| 主 App 身份 | 不将主 App 改成 UID 0；报告同时记录 App 与 helper 的 UID/EUID |

`RootManagementClient.c` 只接受 `identity`、`prepare`、`check`、`cleanup` 四个枚举操作，只执行当前 App 包内固定 helper。helper 只操作 `/private/var/mobile/Library/OpenMinisRootProbe/sample-v1.txt` 这一份固定、无隐私的样本。

管理页面默认关闭。每次操作需要独立确认，确认在执行前消耗；取消、离开页面和进入后台会撤销未使用的授权。已经开始的文件操作有可能完成，撤销不表示回滚。

生产解析器校验 helper 报告的提交、schema、操作、UID/EUID、errno 和必要元数据。源码提交与 Actions run ID 在打包时写入 Info.plist；helper 编译时写入同一提交。原生文件探针也使用当前包的提交和 run ID。

打包器使用真实 Mach-O 符号检查 Root 客户端已编译，按角色审计包内全部 Mach-O 的最低版本，在 macOS 签名并读回保留的主 App、扩展和 helper 的 entitlements。ZIP 读回检查 helper 唯一、Unix/普通文件/0755，以及归档 helper 的 SHA-256 等于签名后文件。manifest 保留实际运行能力为未验证。

Root 候选包含输入框提交 `7c45140702c2f9a824d329bb015c69610988a6cd` 的两个文件补丁。iOS 15 子代理输入框保留一层原生底色与一物理像素描边；Root 能力与输入框外观仍分别验收。

## 跨版本 Root 与本机设备 MCP

Root 打包器现在接受 `--minimum-os 15.0|16.0|16.2`。15.0 档保留分享扩展，16.0 档另外保留 File Provider，16.2 档保留 Widget；主 App、helper、分享扩展和共享框架仍分别按自己的原生下限审计。File Provider 与 Widget 使用 Root 专用 App Group、domain 和签名配置，避免与普通版并存时共享容器串线。

越狱安装包由 `scripts/package_jailbreak_deb.py` 从同一份经过打包审计的 IPA 生成 Rootless、RootHide 两种档案，每档系统版本各出一份，共六份 DEB。安装路径和 `uicache` 脚本固定，包不会把主 App 伪装成 UID 0，也不会给 helper 添加 setuid；manifest 保持 `persona_support: "unverified"`、`device_runtime_verified: false`。

Rootless 将当前 App 和固定安装根都解析为真实路径，以覆盖 `/var/jb` 指向 preboot 的情况。RootHide 使用当前 App 下由 bootstrap 管理的 `.jbroot/usr/lib/libroothide.dylib`，通过官方 `jbroot("/Applications/MinisRoot.app")` 接口转换固定安装根，再与当前 App 的真实路径严格比较；未找到库或路径不匹配时拒绝启动 helper。helper 文件本身仍用 `lstat` 拒绝符号链接。RootHide DEB 依赖 `roothide`，Root App 与 helper 带齐官方四项基础权限，主 App 另外要求独立数据容器。上述解析及权限配置有源码和夹具门禁，真实 bootstrap 运行仍待验收；实现依据见 [RootHide 打包说明](roothide-packaging-notes.md)。

Root 版的 `minis-device` 是内置、保留名称的 STDIO MCP server，在“设置 → MCP 集成 → 本机”（Settings → MCP Integrations → This device）中开启“允许设备工具请求”后出现。无需填写 URL、token 或手动配置 MCP JSON。它只提供 `get_device_info`、`get_screen_info`、`get_brightness`、`set_brightness` 四个工具，最后一个接受 0–100 的整数 `percent`。每次操作都由前台原生确认；离开 App 会关闭开关，取消、超时和断线使本次请求失效，调用不会自动重放。普通版不会注入这个 server，`servers.json` 也不能伪造或覆盖保留名称。

这四项是当前已实现的基础设备能力。截图、跨 App 点击/滑动、UI 观察和输入仍未接入；其授权会话需要覆盖 Minis 离开前台后的执行，不能直接沿用这四项工具的前台逐次确认模型。跨 App 能力仍属于本目标待完成部分，不能以本候选的构建或亮度验收宣布整体自动化目标完成。

设备验收使用 `scripts/verify_jailbreak_device_report.py`，要求新构建的 commit/run、`runtime: physical-device` 和完整 MCP JSONL 回执匹配；旧 Root helper 报告不能作为新设备 MCP 证据。当前仍没有新构建的 iOS 15 MCP 真机报告，也没有 iOS 16+ 真机报告。

## 下载与选包

已发起的原生 gate 修复构建为 `5021a58fa743dcf0e3f9c6911b8c0974016aaa4c` / [Actions 44900128255](https://github.com/doimty/OpenMinis/actions/runs/44900128255)。本文补写时该轮构建尚在进行，尚未记录最终构建结果或新候选包校验值。此前 `44899653106` 在 Root 原生检查阶段失败，没有可安装的本轮候选 IPA/DEB；该失败不能记为编译通过。后续源码修复必须使用对应新 run 的产物与 manifest，不能继续沿用本段的构建身份。

在对应新 run 成功上传后，从 Actions 页面底部 Artifacts 下载 `minis-ios15-root-candidate`。该 artifact 保留 14 天，目标内容为三份 IPA、六份 DEB 及各自相邻的 `manifest.json`。前述旧 run 不包含本轮 Rootless 路径与汉化收尾修改，不能作为最终候选。下面的路径、版本、run 和文件名均为占位符，必须替换为实际下载内容；测试夹具生成的 IPA、DEB、manifest 或报告不能充当交付产物。

```powershell
gh run download "<RUN_ID>" --repo doimty/OpenMinis `
  --name minis-ios15-root-candidate `
  --dir "C:\path\to\candidate"
```

解压后保留目录层级，并按实际系统选择一档：

| 实际系统 | 安装下限 floor | IPA 相对目录与文件名 | 保留扩展 |
| --- | --- | --- | --- |
| iOS 15.x | `15.0` | `root-ipa/Minis-<VERSION>-ios15-root-<COMMIT8>.ipa` | 分享 |
| iOS 16.0–16.1 | `16.0` | `root-ipa-ios16.0/Minis-<VERSION>-ios16.0-root-<COMMIT8>.ipa` | 分享、File Provider |
| iOS 16.2 及更高 | `16.2` | `root-ipa-ios16.2/Minis-<VERSION>-ios16.2-root-<COMMIT8>.ipa` | 分享、File Provider、Widget |

floor 是安装和打包下限，不代表表内每个系统版本均已通过运行验证。当前 Root 候选按 Debug 配置构建。另一个 `minis-standard-release-audit` artifact 只包含普通版 Release 的 `xcodebuild.log` 和 `audit.json`；`m1-ios15-probe` 是诊断证据，它们都不是这里的 Root 安装包。

越狱版再按**现有越狱/bootstrap 的包架构**选择 scheme；不能只根据设备 CPU、iOS 版本或某个越狱品牌名称推断。两种 DEB 均位于 `deb-<FLOOR>-<SCHEME>/`，例如 15.0 档 rootless 是 `deb-15.0-rootless/`。

| scheme | Debian architecture | App 安装路径 | 固定 `uicache` 路径 |
| --- | --- | --- | --- |
| `rootless` | `iphoneos-arm64` | `/var/jb/Applications/MinisRoot.app` | `/var/jb/usr/bin/uicache` |
| `roothide` | `iphoneos-arm64e` | 对应 bootstrap 内的逻辑路径 `/Applications/MinisRoot.app` | 对应 bootstrap 内的 `/usr/bin/uicache` |

DEB 文件名格式为 `com.openminis.app.root_<VERSION>+ci<RUN_ID>+ios<FLOOR>_<ARCH>.deb`。每个 DEB 目录都有自己的 `manifest.json`。本文未验证任何越狱品牌、bootstrap 或版本的支持矩阵，包架构和固定路径也不证明该环境支持 persona 启动。rootless 的 `/var/jb` 可能是指向其他实际安装根的符号链接；表中描述的是逻辑安装路径，实际路径解析、helper 启动和 persona 权限仍需在目标 rootless 设备验证。

安装前核对所选文件旁边的真实 manifest：`source_commit`、`actions_run_id` 必须对应下载的 run，`bundle_id` 必须为 `com.openminis.app.root`，`minimum_os` 必须对应选中的 floor；IPA 另有 `package_floor`。DEB 还应核对 `scheme`、`architecture`、`source_ipa_sha256`。将文件的实际 SHA-256 与 manifest 的 `ipa_sha256` 或 `deb_sha256` 比较，例如：

```powershell
Get-FileHash -LiteralPath "C:\path\to\candidate\deb-15.0-<SCHEME>\<DEB_NAME>.deb" -Algorithm SHA256
```

## 安装到已有越狱环境

1. 保留普通版和回退包，按上表选定一个 floor 和 scheme。所有 Root IPA/DEB 使用同一个 bundle ID，不将 TrollStore IPA 与 DEB 当作可并存的两个 Root 变体；切换安装方式时，先用原安装工具移除原 Root 安装。
2. 优先用设备现有包管理器安装选中的本地 `.deb`，并让它处理 `firmware (>= <FLOOR>)`、`uikittools` 和 RootHide 档额外的 `roothide` 依赖。若已有该 bootstrap 的 root 终端且依赖齐全，也可执行下面的命令；路径替换为实际传入设备的包。

   ```sh
   dpkg -i "/path/to/selected.deb"
   ```

3. 安装脚本在 configure 时仅对该包固定 App 路径执行 `uicache -p`。从主屏打开 `Minis Root`，在“设置 → 关于 Minis → Root 管理”（Root Management）核对“源码提交”和“Actions 运行编号”。诊断页的按钮、确认说明和报告状态均提供简体与繁体中文。
4. 若使用 TrollStore 路径，则安装所选 floor 的 `.ipa`，同样核对 Root 名称和构建身份。该安装方式是独立验收对象；成功安装 IPA 不能证明 DEB 的安装脚本或 bootstrap 路径可用。

## 构建与验收证据

| 断言 | 证据与状态 | 最便宜的证伪方式 / 若假设错 |
| --- | --- | --- |
| 原有未提交工作已保存 | 接手时 23 个文件复制到本机恢复目录并记录 SHA-256；已验证。 | 对比恢复副本和接手后的 diff。遗失修改会使后续结果无法复现。 |
| Windows ZIP 可执行位夹具可通过 | 历史记录中 `py -3 scripts/test_package_ios15_root_ipa.py -v` 为 18/18；这是当时的夹具计数，不是本轮完整测试数量，也不是真实产物。 | 把 ZIP helper 改成 0644、4755、0777、符号链接或 DOS 元数据，必须拒绝。删除检查会掩盖安装后无法执行。 |
| 归档 helper 对应签名后的文件 | 同一套测试包含合法 CRC 下替换 helper 的负例；已验证，仅夹具。 | 对比 manifest 的 signed/archive helper SHA-256 和实际归档字节。若不一致，设备运行的文件可能不是审计的文件。 |
| 本轮 Root App 能编译、签名并生成三档 IPA/DEB | `5021a58` 的 Actions 已启动；本文未记录最终结果。 | 读取完整构建首错、签名读回及包内 native floor，并检查真实 artifact 与 manifest；静态测试不能替代。 |
| 历史 helper 在目标 iOS 15 上取得 UID/EUID 0 | 用户提供的 `2bb94a2c353ec1b162aafa0cf18a839842cec50f` / `44872743232` 报告中 identity、prepare、check、cleanup 均为 `0/0`，transport 正常；仅证明旧包。 | 新包先执行 `identity`。若 helper 非 0/0、commit 不符或启动失败，不能沿用旧包结论。 |
| 历史原生 App 能读取固定容器外样本 | 同一旧包以 UID/EUID `501/501` 在 `sample.compare` 读到匹配的 56 字节样本；不代表任意路径读写。 | 新包需对应的新报告；缺失样本或只有 bundle 正对照不能判容器外权限通过。 |
| 新设备 MCP 的授权、拒绝、亮度修改及恢复完整链路 | 尚无本轮 iOS 15 真机报告与配套 JSONL；未验证。 | 按下节采集九次操作，使用所装包的 manifest 运行离线验证器；仅 native 报告不够。 |
| iOS 16+ 扩展和设备运行 | 保留三档打包路径；尚无 iOS 16+ 真机报告。 | 在对应 floor 的真实设备上独立验收安装、扩展、MCP 和 helper；15.0 报告不能替代。 |
| 粗灰边已在真机消失 | 独立 UI 包已构建，Root 带同一源码补丁；真机外观未验证。 | 深浅色、空输入、四行、键盘、发送清空和箭头布局截图。源码相同不能证明真机外观。 |

最可能的三个静默失败点是安装了旧包、签名权限未被安装环境接受，以及确认到 helper 的实际调用失败。分别用报告的 commit/run、真实 App/helper UID/EUID、client stage/errno/exit/signal 排除。异常时保留原始标量报告，不把超时或未完成误写成失败操作没有副作用。

## 新设备 MCP 的 iOS 15 真机验收

本轮优先采集新增 MCP 链路，不要求补做此前已暂缓的 helper 取消/后台实验。以下命令在**设备内 Minis 的终端**运行，不能用 Windows 本机 Python 代替设备端调用。

1. 安装并打开上节所选 Root 包，先打开一次聊天或终端，使内置环境初始化。
2. 进入“设置 → MCP 集成 → 本机”。若已有记录，先点“清除设备活动报告”；再开启“允许设备工具请求”。“显示设备工具”应列出上述四个工具。
3. 返回 Minis 终端，保持 App 在前台。选一个已存在且可写目录中的**新文件名**；采集器拒绝覆盖已有文件。以下输出路径是占位示例：

   ```sh
   python3 /usr/local/lib/minis-mcp-cli/device_acceptance.py \
     --output "/var/minis/shared/<NEW_RECEIPTS_NAME>.jsonl"
   ```

4. 正常会出现九次原生确认。第 4 次必须明确点“拒绝”（Deny）；其余八次点“允许一次”（Allow once）。采集器先保存工具发现结果，再保存每次 CLI 的原始单行 JSON，不修改回执内容。

| 次序 | 操作 | 本次决定与检查 |
| --- | --- | --- |
| 1 | 设备信息 | 允许一次 |
| 2 | 屏幕尺寸与 scale | 允许一次 |
| 3 | 读取原亮度 | 允许一次，记录原值和四舍五入后的恢复百分比 |
| 4 | 请求测试亮度 | **拒绝**；测试值由原亮度决定，为 30% 或 70% |
| 5 | 独立读取亮度 | 允许一次，确认拒绝后未改变 |
| 6 | 设置测试亮度 | 允许一次 |
| 7 | 独立读取测试结果 | 允许一次 |
| 8 | 恢复原亮度百分比 | 允许一次 |
| 9 | 独立读取恢复结果 | 允许一次 |

采集期间不要切到后台或打开控制中心，以免前台授权失效。误点、超时或中断时采集器停止，保留已有回执且不重放操作；若未确认亮度恢复，按终端显示的恢复百分比手动恢复。重新采集需要再次清空原生活动报告、开启开关，并使用另一个新 JSONL 文件名。

完成后回到“设置 → MCP 集成 → 本机”，点“复制设备活动报告”，将剪贴板中的原始 JSON 单独保存为 `device.json`，并导出采集到的 JSONL。两份文件共同保留，通过请求 UUID 关联；只复制终端最后一行摘要不够。

## Windows 离线核对

在仓库根目录运行以下命令。`--manifest` 必须指向**实际安装包旁边的那份 manifest**，`--report` 是本次原生复制的 JSON，`--receipts` 是本次未编辑的设备端 JSONL；所有路径和 `<EXPECTED_OS_BUILD>` 都替换为实际值。目标设备的 OS 字符串格式如 `Version 15.1.1 (Build 19B81)`。

```powershell
py -3 scripts/verify_jailbreak_device_report.py `
  --manifest "C:\path\to\candidate\deb-15.0-<SCHEME>\manifest.json" `
  --report "C:\path\to\evidence\device.json" `
  --receipts "C:\path\to\evidence\<NEW_RECEIPTS_NAME>.jsonl" `
  --expected-os "<EXPECTED_OS_BUILD>"
```

若实际使用 15.0 档 IPA 安装，则将 `--manifest` 改为下载目录的 `root-ipa/manifest.json`。当前验证器有意只接受 floor 为 `15.0`、OS 字符串为 iOS 15、`runtime: physical-device` 的报告，并要求 commit/run/bundle、九次原生记录和 MCP 回执完整一致。不要把 16.0/16.2 manifest 传给此验收器来声称 iOS 16+ 已验收。

通过时输出的证据范围为 `native-plus-mcp`，覆盖设备与屏幕读取、亮度拒绝、修改和恢复。`--native-only` 是较弱的独立检查，不能作为 MCP 全链路通过的证据。验证结果也不证明 helper 取得 root、DEB 在另一 bootstrap 可运行或 iOS 16+ 真机兼容；本地 JSON 是用户采集的记录，不是密码学设备证明。

## helper 与界面的独立回归

需要核对新包的 helper 时，进入“设置 → 关于 Minis → Root 管理”，开启“仅本次启用”，先点“读取辅助程序身份”（Read helper identity），单次确认后点“复制标量报告”。只有同提交 helper 实际 UID/EUID 为 `0/0` 且 transport 正常，才能记录本包 Root 身份成功。随后可分别确认“创建固定 Root 样本” → “读回固定 Root 样本” → “删除固定 Root 样本”，每步保留报告，check 应匹配固定 marker、大小和文件身份。已开始的文件操作不属于可撤销事务。

`Host File Access` 的原生文件正对照与固定样本是另一组独立证据；Root 管理样本和 Host File Access 样本是不同文件。子代理输入框的深浅色、空输入、四行、键盘和箭头布局也需独立验收。上述历史结果不能替代新包的设备 MCP 验收。

## 我现在不该说的话

- “本轮 Root 已在设备生效”或“新设备 MCP 真机通过”，除非有匹配本包的真实设备报告和所需回执。
- “原生文件全盘读写成功”，因为现有探针只验证固定样本。
- “签名、包结构或绿色 Actions 等于真机通过”。
- “取消操作能撤销已经开始的写入”。
- “输入框真机灰边已消失”，除非有对应版本的界面证据。
