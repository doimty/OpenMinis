# Minis Automation companion

本目录提供独立的 SpringBoard tweak。它将钉定 iOS MCP 项目的 MIT 源码编入本机 HTTP MCP 服务，供 Minis Root 已确认的短期会话使用。主 App 与普通版的 Xcode target 不引用本目录。

运行接口固定为 `http://127.0.0.1:8091/mcp`。服务默认关闭，重启不恢复会话。只有前台确认后写入的固定会话文件和 `com.openminis.automation.start` 通知能开启 listener；每个请求要求 bearer，逐次读取控制文件并核对编译提交、run ID、权限和到期时间。STOP、到期、断线和旧请求 generation 失效都会阻止后续事件，已发送的事件没有回滚语义。

`MinisAutomationSession.m` 直接使用 Darwin `open/openat` 访问 rootfs 下 `/private/var/mobile/Library/OpenMinisAutomation/session.json`。本构建不使用 vroot、libiosexec、符号重定向或全局路径重写。RootHide 仅通过官方 C `jbroot/rootfs` ABI 包装上游设备模块需要的 bootstrap 路径，固定控制文件不转换。

源码固定为 `witchan/ios-mcp@62c4464f688c8f269419924b1a19c996de75f175`。`vendor/ios-mcp/source.json` 记录所有原始源及许可证的 SHA-256；这些文件保持原始字节。`prepare_sources.py` 在临时构建目录验证原始字节、增加自有 ObjC/C 符号前缀，并应用明确补丁：loopback、认证、22 工具白名单、参数界限、受保护宿主、事件取消检查、无输入回退、输出 provenance。

生产源码保留上游部分未开放功能的类以保持 HTTP/router 的完整链接，但 HTTP 分发在入口拒绝非白名单和额外路由。安装包不包含任何 AppSync、appinst、ldid、setuid helper、Paddle 模型、OCR worker、GPL/AGPL 外部组件或相应安装脚本。

RootHide ABI 的来源为 `roothide/libroothide@cb236f9cf1c5e2ac932611328998a62b4b133e33`。`vendor/libroothide` 保留官方 C 头、rootless stub、声明安装名的 Makefile、MIT License 及逐文件 hash。构建脚本只为实际使用的 `_jbroot/_rootfs` 生成 link-only TAPI stub，安装名保持官方 `@loader_path/.jbroot/usr/lib/libroothide.dylib`；实际库由设备的 `roothide` 核心包提供，不在此包内重新分发。

所有原生编译仅在 GitHub Actions macOS 执行：

```sh
python3 jailbreak/automation/test_native.py --native
bash jailbreak/automation/build.sh rootless "$RUNNER_TEMP/automation-rootless"
bash jailbreak/automation/build.sh roothide "$RUNNER_TEMP/automation-roothide"
```

只需要 Xcode、系统 SDK、clang、lipo、codesign 和 Python；不用 Theos/Logos，也不会执行设备安装或 respring。`build.sh` 读取 `GITHUB_SHA` 和 `GITHUB_RUN_ID`，输出已签名的通用 arm64/arm64e dylib、SpringBoard filter、`build.json`、`source-lock.json`、日志及许可证。Rootless 运行依赖 tweak injection runtime；RootHide 另依赖 `roothide` 核心。重启 SpringBoard 后才加载新 tweak，应由用户在安装工具中手动执行。

arm64e slice 单独使用 `-fno-ptrauth-objc-class-ro`，输出 iOS 15 可消费的 legacy class-RO 布局；其它 arm64e PAC 保持开启。`verify_build.py` 与 DEB 打包器都会从最终二进制读取每个 fat slice 的 UUID、`__objc_imageinfo` 和 chained-fixups 元数据，并要求 `SignedClassRO=false`。较新的 objc4 会用进程级 class-RO 兼容模式接受该镜像，完整 SignedClassRO 不能由单个 arm64e slice 同时兼容 iOS 15 与 OS 新语义。

```sh
python3 jailbreak/automation/test_native.py --static
```

该命令可在 Windows 执行，验证原始哈希、生成补丁与名称隔离，完全不调用编译器。`--native` 则在 Actions 测试实际生产 Foundation parser、文件策略、默认关闭、时间/字段/重复 JSON 拒绝、旧 generation、dispatch 传播、并发、断线、trace、白名单和输入参数。这些测试不调用 HID、截图或其他设备操作，不能替代真机验收。

完整目标、授权说明、许可证范围和真机证据要求见仓库的 `docs/minis-automation-design.md`。当前是否能在目标设备工作必须以同提交、同 run 的真实 App/companion manifests 与 MCP 回执为准。
