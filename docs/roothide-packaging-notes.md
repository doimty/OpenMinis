# RootHide 打包依据与验收范围

本文记录 Minis Root 的 RootHide 安装契约。当前工作流交付 Rootless、RootHide 两种 DEB，各有 iOS 15.0、16.0、16.2 三档安装下限。包结构、签名读回、原生检查和设备运行是分别记录的证据；本文没有 RootHide 真机通过结论。

## 固定版本的上游依据

以下链接固定到实际审查的提交，避免使用后续变化的 `main` 或 `master` 推断当前实现：

| 上游源码 | 本次使用的事实 |
| --- | --- |
| [Developer / README](https://github.com/roothide/Developer/blob/c53cc199f1bab7d7ea2fd70f0a14e215b1f3461b/README.md) 与 [entitlements](https://github.com/roothide/Developer/blob/c53cc199f1bab7d7ea2fd70f0a14e215b1f3461b/entitlements.md) | 原生 App 访问越狱文件时使用 `jbroot` 转换；越狱 App、工具的基础权限包含 `platform-application`、`no-sandbox`、`storage.AppBundles` 和 `storage.AppDataContainers`。 |
| [Developer / roothide](https://github.com/roothide/Developer/blob/c53cc199f1bab7d7ea2fd70f0a14e215b1f3461b/roothide.md) 与 [interface](https://github.com/roothide/Developer/blob/c53cc199f1bab7d7ea2fd70f0a14e215b1f3461b/interface.md) | RootHide 使用随机的 jbroot，bootstrap 工具以它为默认根；dpkg 为含 Mach-O 的目录建立 `.jbroot` 链接，原生 API 和 bootstrap 命令的路径语义不同。 |
| [Theos / RootHide DEB 配置](https://github.com/roothide/theos/blob/958fea4f779943505b15b93c88bb4db8d97b346d/vendor/mod/roothide/package/deb.mk) | RootHide 的 Debian `Architecture` 为 `iphoneos-arm64e`。这是包管理器架构标识，不要求 Minis 的每个 Mach-O 都改成 arm64e 指令集。 |
| [libroothide / 头文件](https://github.com/roothide/libroothide/blob/cb236f9cf1c5e2ac932611328998a62b4b133e33/roothide.h)、[jbroot 实现](https://github.com/roothide/libroothide/blob/cb236f9cf1c5e2ac932611328998a62b4b133e33/jbroot.c) 与 [Makefile](https://github.com/roothide/libroothide/blob/cb236f9cf1c5e2ac932611328998a62b4b133e33/Makefile) | `jbroot` 是外部 C ABI：`const char *jbroot(const char *)`。返回值由库缓存管理，调用方不能释放。库的安装名为 `@loader_path/.jbroot/usr/lib/libroothide.dylib`，并依赖同样通过 `.jbroot` 解析的 `roothideinit.dylib`。 |
| [Procursus / roothide.control](https://github.com/roothide/Procursus-roothide/blob/7dfa18ef09d18028f4e2bca01ce28c3e7e0db227/build_info/roothide.control) 与 [roothide.mk](https://github.com/roothide/Procursus-roothide/blob/7dfa18ef09d18028f4e2bca01ce28c3e7e0db227/makefiles/roothide.mk) | 安装上述运行库的核心包名称为 `roothide`，它把 `libroothide.dylib`、`roothideinit.dylib` 放入 bootstrap 的 `/usr/lib`。 |
| [uikittools-ng / uicache](https://github.com/roothide/uikittools-ng/blob/627e1eebea4d49244f8065b09d9ad93a2eef4c1e/uicache.m) | `constructContainerizationForEntitlements` 优先接受 `uicache.app-data-container-required: true`。否则 RootHide 会把 `no-sandbox: true` 的 App 注册为没有独立数据容器的应用；`registerPath` 使用这个判断决定容器与 HOME。 |

## Minis 的实现

RootHide DEB 采用 `iphoneos-arm64e`，在 bootstrap 的逻辑 `/Applications/MinisRoot.app` 安装。注册脚本调用逻辑 `/usr/bin/uicache -p /Applications/MinisRoot.app`，卸载脚本对同一固定目录执行 `-u`。RootHide 的 `uicache` 会把参数转换到真实 jbroot；脚本不扫描随机目录，也不修改系统分区。依赖为对应系统下限的 `firmware`、`uikittools` 和 `roothide`。Rootless 包继续使用 `/var/jb/Applications/MinisRoot.app`，不依赖 RootHide 核心包。

主 App 与 helper 的 Root 专用签名配置都包含四项基础权限：

```text
platform-application = true
com.apple.private.security.no-sandbox = true
com.apple.private.security.storage.AppBundles = true
com.apple.private.security.storage.AppDataContainers = true
```

主 App 另带 `uicache.app-data-container-required = true`，让 RootHide 注册时仍为 Minis 创建独立数据容器，保持 Library、Documents 和容器回退存储的归属。helper 不需要 App 数据容器标记；它仍只有固定样本操作。分享、File Provider 和 Widget 扩展继续使用各自的沙盒签名配置。普通版的签名配置不因此改变。

`RootManagementClient.c` 的 helper 解析先比较固定 Rootless／系统应用目录解析后的真实路径。若均不匹配，Root 专用分支才从当前 App 包内的固定路径 `/.jbroot/usr/lib/libroothide.dylib` 加载官方库，并查询 `jbroot`。只把固定参数 `/Applications/MinisRoot.app` 传给该 API，再用 `realpath` 与当前 App 的真实目录严格比较。缺库、缺符号、无效映射或映射到其他目录均拒绝执行。

该分支依赖包管理器维护的 `.jbroot` 和可信的 RootHide 核心库。它不把调用者传入的路径交给 API，不用随机目录前缀或任意 `MinisRoot.app` 同名目录代替匹配。比较在 `dlclose` 之前完成，不保留或释放库拥有的返回字符串。选定 App 包后仍只追加固定 helper 名，并由 `lstat` 拒绝 helper 符号链接、不安全权限和多硬链接。

## 已覆盖的检查与未验证项

`package_ios15_root_ipa.py` 读回签名权限，要求它们与 Root 专用配置精确匹配。DEB 打包器再检查来源 IPA 的身份、哈希、签名证据、扩展集合和实际 Mach-O 下限，写出归档后读回文件、权限及注册脚本。夹具包含缺失 RootHide 基础权限或独立容器标记时必须拒绝交付的反例。

`RootManagementHelperPathHarness.c` 在 Actions 使用生产路径函数，模拟 Rootless 的真实符号链接关系及 RootHide 的加载接口。RootHide 用例替换 `dlopen`、`dlsym`、`jbroot`，检查加载失败、缺符号、空返回、错误映射、正确映射及 helper 符号链接。这些用例不启动特权进程，也不加载设备上的真实 RootHide 核心库。

三档打包检查分别要求分享扩展（15.0）、File Provider（16.0）和 Widget（16.2）存在，并保留各角色的原生版本下限。普通版 Release 审计检查四个 target 的身份、扩展、签名配置、编译宏与最终链接。它们证明相应构建和包结构，不证明 iOS 16+ 的所有运行功能。

仍需目标设备直接验证：RootHide 实际加载库及 `.jbroot` 解析、App 独立数据容器、helper UID/EUID、逐次 MCP 操作、扩展注册，以及 iOS 16+ 设备行为。设备报告必须对应实际安装的提交、Actions run 和包清单。iOS 15 的历史 TrollStore helper 报告不能替代这些 RootHide 或 iOS 16+ 结果。
