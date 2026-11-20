# 第 2、3 层实现记录（本地源码与测试，未构建/未装机）

基线 `ff1f00f`，独立工作树 `/root/staging/openminis-20261120/host-permission-probe`。
交付的两个 IPA 和修复分支均未改动。

## 第 2 层：原生沙盒外读取探针

| 文件 | 作用 |
|---|---|
| `src/ios/Diagnostics/HostFileAccessProbe.h` | 无路径入参的 C 接口 |
| `src/ios/Diagnostics/HostFileAccessProbe.c` | 固定样本的只读核心 |
| `src/ios/Views/Settings/HostFileAccessProbeView.swift` | 设置 › 关于 里的手动诊断页 |
| `src/ios/Views/Settings/AboutView.swift` | 仅开关下出现的入口 |
| `src/ios/MinisApp-Bridging-Header.h` | 仅开关下导入头文件 |
| `src/ios/Minis.xcodeproj/project.pbxproj` | 登记 2 个编译单元和分组 |

设计约束：

- 只读固定路径 `/private/var/mobile/Media/OpenMinisPermissionProbe/sample-v1.txt`，无路径输入、无目录遍历、无写入。
- 从 `/` 起逐级 `openat + O_NOFOLLOW + O_DIRECTORY` 走到样本目录，祖先目录里的符号链接直接拒绝。
- 样本要求：普通文件、单硬链接、精确长度、标记字节逐字节匹配、读前读后 inode/size/mtime/ctime 一致。
- 失败只回报阶段名和真实 `errno`，不把 `ENOENT` 当权限拒绝，不把 `EACCES` 当文件不存在。
- 整页由 `DEBUG && MINIS_HOST_FILE_PROBE && os(iOS)` 包住，普通构建里编译产物为零；入口隐藏 iOS App on Mac。
- 每次点击跑一次，后台线程，无自动触发、无网络、无日志落盘、无 AI/Shell/MCP 入口。

本地已验证（Linux clang 19.1.4，真实执行）：

```
python3 tests/test_host_file_access_probe.py
PASS host C probe: exact marker, missing, symlink, ancestor symlink, FIFO, directory, size, marker, hardlink, permission denial
```

覆盖：精确匹配、缺失、文件软链接、祖先目录软链接（ELOOP）、FIFO、目录、超长/超短、错标记、硬链接、降权到 nobody 的 EACCES。
Linux 结果不等于 iOS 沙盒/TCC 行为，这条结论必须靠真机 A/B 才能给出。

Swift 侧只做了语法解析（tree_sitter_swift，0 错误；`AboutView.swift` 里 1 处 ERROR 是基线同样存在，与本改动无关）。**本机没有 swiftc/Xcode，未编译。**

## 第 3 层：root 助手源码

`roothelper/main.c`，单文件、无依赖，三个子命令 `prepare / check / cleanup`，其余一律拒绝。

安全规则：

- 只允许 root 运行；进入特权路径后 `setgid(0)+setuid(0)`。
- 只操作 `/private/var/mobile/Library/OpenMinisRootProbe/`，目录必须 root 所有且 0700，样本必须 root 所有、0600、单硬链接、精确长度、标记匹配。
- 全部用 `*at()` + `O_NOFOLLOW`，目录 fd 锚定，防止替换目录/软链接/竞态。
- 无 `posix_spawn`、无任意命令、无任意路径、无通配符；参数只有三个固定词。
- `cleanup` 只删文件，保留目录，避免删掉被替换的目录。

本地已验证（容器内是 root，可实际编译运行）：

```
python3 tests/test_root_probe_helper.py
PASS root probe helper: prepare/check/cleanup, dir policy, symlink, mode, hardlink, op allowlist, non-root refusal
```

未验证：**iOS 上 setuid 是否真正生效、rootless/rootHide 路径下 `/var/jb` 前缀处理、entitlement 需求、amfid/代码签名**。这些只能在真机上做，且必须先有 deb 和安装授权。

## 两者共同缺失的环节

1. 云构建未运行：这两份改动没有经过 xcodebuild，Swift 编译结果未知。
2. 未打包：第 2 层要产出带 `MINIS_HOST_FILE_PROBE=1` 的独立 IPA；第 3 层要产出 iOS 15 arm64e deb，编译时用 iOS SDK + `ldid` 签名 + `chmod 4755`。
3. 未装机：用户尚未授权装 IPA/deb 或重启 SpringBoard。

## 明确的非目标

不接 `task_for_pid`、不接内核提权、不接 TCC 全绕过、不把 root 能力暴露给 AI 工具/iSH/MCP、不默认开启任何一项。