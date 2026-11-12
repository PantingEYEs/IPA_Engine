# Mozc for IPA Board

`IPA_Engine` 的独立 `japanese` 分支，从 Google 官方 Mozc 源码构建 Android JNI 引擎与配套 OSS 词库。此分支以无父提交的初始提交开始，不包含 `develop` 的源码或历史。

本分支经维护者明确同意，例外提交已验证的二进制与词库；`develop` 原有的源码白名单和 Releases 分发约定不变。本地工作目录为 `D:\IPA_Engine\Mozc`。

## 构建输入与产物

固定上游提交、依赖工具链和补丁哈希见 [sources.lock.json](sources.lock.json)。上游模块及词典依赖继续采用该提交自带的固定版本与完整性校验。使用 Linux x86_64、Python 3.12+、C++20 编译器、Git、curl，以及 bsdtar 或 unzip。

```sh
bash scripts/build.sh
```

源码与工具缓存放在 `.work/` 和 `.cache/`；日志在 `logs/`，这些内容不提交。WSL 可用 `MOZC_BAZEL_OUTPUT_ROOT` 指定 Linux 临时编译缓存，以避免跨文件系统的大量随机访问。`MOZC_BUILD_JOBS` 默认 4。证书受管理的构建环境可通过 `MOZC_JAVA_TRUSTSTORE` 指定已有 PKCS12 信任库，脚本不禁用 TLS 校验。

产物按同一次源码构建配套使用：

- `artifacts/arm64-v8a/libmozc.so`
- `artifacts/x86_64/libmozc.so`
- `artifacts/mozc.data`
- `artifacts/manifest.json`：文件大小、SHA-256、ELF 段对齐、动态依赖与 JNI 入口。

发布库用同一 NDK 的 `llvm-strip --strip-unneeded` 移除不参与加载的调试与静态符号信息；未剥离版本保存在 `.work/symbols/`，供本地排错。两个 JNI 导出、加载段与动态依赖在剥离后重新校验。`build.sh` 先在临时目录收齐并验证整套产物，再替换 `artifacts/`；编译或验证失败不会覆盖已有的一套。

重新检查静态产物：

```sh
python3 scripts/verify_artifacts.py --artifacts artifacts --output artifacts/manifest.json
python3 -m unittest discover -s tests
```

NDK 使用 r29，显式以 API 26 为链接基线。最低链接 API 不代表已经验证 API 26 上的完整应用行为。只构建当前 IPA Board 使用的 arm64-v8a 与 x86_64；没有 32 位产物。Google 的 Android 输入法前端已经停止维护，本分支只使用仍保留的原生引擎构建入口。

## IPA Board 接入边界

[兼容补丁](patches/ipa-board-compat.patch) 保留官方 `MozcJni.initialize(): boolean`，同时提供现有 `MozcJNI.initialize(): void` 入口；动态注册仍针对实际调用的 Java 类。旧入口在注册失败时保留异常或抛出明确异常。`evalCommand`、`onPostLoad`、`getDataVersion` 的签名与协议不另行包装。

接入时需同步更新两种 ABI 的库、词库、资源锁定哈希，以及词库部署目录的代际。现有 IPA Board 使用 `mozc-v0.1.2`，并在文件存在时跳过复制，不能直接让新库继续读取该目录中的旧词库。建议按本次词库 SHA-256 选择新目录，先校验再切换，不覆盖仍被 mmap 使用的文件。

新版还要求在零输入预测请求中显式设置 `mixed_conversion=true`；旧适配器只传 `zero_query_suggestion=true`，会失去词典续写。该参数只需加到 `predict()` 的临时会话，普通转换及隐身、禁历史设置保持原样。[接入说明及最小补丁](integration/README.md) 同时包含此项和词库部署代际更新，未应用到当前 IPA Board 工作树。

`onPostLoad` 返回成功不等于完整词库已加载：官方实现可能回退到最小引擎。接入验收需核对数据版本，并实际验证日文转换、分段与预测。候选排名可能随官方词库版本更新而变化，不应将旧版本固定排名当成兼容合同。

上游与第三方许可说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [licenses/](licenses/)。本仓库尚未选定项目级许可证；各上游材料继续遵循原有许可，不将其重新许可为本项目协议。

## 验证记录

实际构建、6 项严格 16KB 运行专项及验证边界见 [VALIDATION.md](VALIDATION.md)。ELF 对齐检查、16KB 模拟器运行与 arm64 真机运行是不同层级，不能互相替代。
