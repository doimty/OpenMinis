# Minis Root / iOS 15 候选与验收记录（2026-11-21）

目标是完成兼容 iOS 15 的 Minis Root。所有 C、Swift、原生依赖和 App 编译均在 GitHub Actions 执行；本机只运行 Python 夹具和源码检查。

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

打包器使用真实 Mach-O 符号检查 Root 客户端已编译，审计包内全部 Mach-O 的 iOS 15 最低版本，在 macOS 签名并读回主 App、分享扩展和 helper 的 entitlements。ZIP 读回检查 helper 唯一、Unix/普通文件/0755，以及归档 helper 的 SHA-256 等于签名后文件。manifest 保留实际运行能力为未验证。

Root 候选包含输入框提交 `7c45140702c2f9a824d329bb015c69610988a6cd` 的两个文件补丁。iOS 15 子代理输入框保留一层原生底色与一物理像素描边；Root 能力与输入框外观仍分别验收。

## 构建与验收证据

| 断言 | 证据与状态 | 最便宜的证伪方式 / 若假设错 |
| --- | --- | --- |
| 原有未提交工作已保存 | 接手时 23 个文件复制到本机恢复目录并记录 SHA-256；已验证。 | 对比恢复副本和接手后的 diff。遗失修改会使后续结果无法复现。 |
| Windows ZIP 可执行位夹具可通过 | `py -3 scripts/test_package_ios15_root_ipa.py -v`：18/18；已验证，仅夹具。 | 把 ZIP helper 改成 0644、4755、0777、符号链接或 DOS 元数据，必须拒绝。删除检查会掩盖安装后无法执行。 |
| 归档 helper 对应签名后的文件 | 同一套测试包含合法 CRC 下替换 helper 的负例；已验证，仅夹具。 | 对比 manifest 的 signed/archive helper SHA-256 和实际归档字节。若不一致，设备运行的文件可能不是审计的文件。 |
| 完整 Root App 能为 iOS 15 编译并签名 | 等待本候选 Actions；未知。 | 读取完整构建首错、签名读回及包内 native floor；静态测试不能替代。 |
| helper 能在目标 iOS 15 上取得 UID/EUID 0 | 尚无目标设备报告；未知。 | 安装同一候选，先只执行 `identity`。若 helper 非 0/0、commit 不符或启动失败，就不能判 Root 生效。 |
| 原生 App 能访问容器外文件 | 尚无本候选设备报告；未知。 | 在 Host File Access 先确认自身 Info.plist 正对照通过，再读已预置固定样本；缺失样本不能判权限通过。 |
| 逐次受限操作已在真机跑通 | 尚无设备报告；未知。 | 逐次确认执行 prepare → check → cleanup，并独立测试取消与后台撤销。若报告不匹配或文件结果不同，保持未通过。 |
| 粗灰边已在真机消失 | 独立 UI 包已构建，Root 带同一源码补丁；真机外观未验证。 | 深浅色、空输入、四行、键盘、发送清空和箭头布局截图。源码相同不能证明真机外观。 |

最可能的三个静默失败点是安装了旧包、签名权限未被安装环境接受，以及确认到 helper 的实际调用失败。分别用报告的 commit/run、真实 App/helper UID/EUID、client stage/errno/exit/signal 排除。异常时保留原始标量报告，不把超时或未完成误写成失败操作没有副作用。

## 真机操作次序

1. 通过 TrollStore 安装产物中的独立 `Minis Root`，核对名称与原版并存。保留普通版和 `ff1f00f` 回退。
2. 打开“设置 → 关于 → Root Management”，核对完整提交和 Actions run。
3. 开启本次页面授权，先点 `Read helper identity`，单次确认后复制标量报告。只有同提交 helper 实际 UID/EUID 为 `0/0` 且 transport 正常，才能记录本次 Root 身份成功。
4. 分别确认 `Create fixed root sample`、`Read back fixed root sample`、`Remove fixed root sample`。每步各留报告，check 应匹配固定 marker、大小和文件身份。
5. 单独测试取消和后台撤销；不把已经开始的操作当作可撤销事务。
6. 在 `Host File Access` 独立记录原生文件正对照和固定样本结果；Root 管理样本与 Host File Access 样本是不同文件。
7. 最后独立验收子代理输入框的深浅色与键盘布局。

## 我现在不该说的话

- “Root 已在设备生效”或“受限管理真机通过”，除非有匹配本包的真实设备报告。
- “原生文件全盘读写成功”，因为现有探针只验证固定样本。
- “签名、包结构或绿色 Actions 等于真机通过”。
- “取消操作能撤销已经开始的写入”。
- “输入框真机灰边已消失”，除非有对应版本的界面证据。
