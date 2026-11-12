# ONNX Runtime Extensions for IPA Board

`IPA_Engine` 的独立 `ranking` 分支，为 IPA Board 现有 Multilingual E5 语义排序构建 Android ONNX Runtime Extensions。分支从无父提交开始，保存构建资料、必要源码补丁、许可证、验证记录及已编译产物。本地目录为 `D:\IPA_Engine\ONNX`。

## 范围与产物

**目标与验收平台为 16KB 页设备。** 按维护者明确要求，4KB 不属于支持或验收范围；已有 4KB 运行记录只保留为本次迁移的新旧输出对照，后续不默认追加该平台回归。

固定微软官方 Extensions **v0.13.0**，保留现有 SentencePiece／XLM-R 分词行为；使用 Android NDK r29，从源码重新链接两个 ABI 的动态库，显式设置 **16KB** 的最大页和公共页对齐。没有通过修改 ELF 头或只调整 ZIP 来伪装兼容。

本包仅启用 SentencePiece 分词／解码算子，满足 IPA Board 当前 `tokenizer.onnx`；它不是包含所有图像、音频和其他分词算子的完整 Extensions 发行版。

- `artifacts/onnxruntime-extensions-android-0.13.0-ipa-e5-16kb.aar`：建议接入的完整 Android 库。
- `artifacts/{arm64-v8a,x86_64}/libortextensions.so`。
- `artifacts/{arm64-v8a,x86_64}/libonnxruntime_extensions4j_jni.so`。
- `artifacts/manifest.json`：SHA-256、文件大小、实际 ELF 段、动态依赖、导出和 AAR 内容核验。

ONNX Runtime 主库 **1.23.2**、E5 权重、分词图和校准向量保持 IPA Board 原版，不在此分支重复分发。确切版本与摘要见 [兼容资源锁定文件](integration/ipa-board-resources.lock.json)。分词模型内嵌在现有 `tokenizer.onnx`，无需为本次重编译另制词典。

## 重建

需要 Linux x86_64、Git、Python 3.11+、GNU Make、tar、unzip，以及 JDK **21.0.13**（实际使用 Temurin 21.0.13+9，输出 Java 8 字节码）。源码、依赖提交和工具摘要均在 [sources.lock.json](sources.lock.json)。NDK r29 与 CMake 3.31.6 可通过校验下载入口准备：

```sh
python3 scripts/bootstrap_tools.py
export JAVA_HOME=/path/to/jdk-21.0.13
export ANDROID_NDK_HOME="$PWD/.cache/tools/android-ndk-r29"
bash scripts/build.sh
```

已有经过校验的 NDK 可通过 `ANDROID_NDK_HOME` 复用；`ORTX_CMAKE` 可以指定同版 CMake。构建不会安装工具到系统目录或修改个人 Git 配置。首次准备需要网络，源码与工具齐备后的 CMake 编译不联网。

源码和工具缓存位于 `.work/`、`.cache/`，构建日志在 `logs/`，均不提交。WSL 可设置 `ORTX_BUILD_DIR=/var/tmp/ipa-onnx-build` 使用 Linux 临时编译目录；已验证的最终产物仍保存到本仓库。`ORTX_BUILD_JOBS` 默认为 4。编译前后都会验证源码提交和完整补丁差异，拒绝未批准的源码改动、隐藏索引标记和未跟踪源码。

原生库使用 API 26 和静态 C++ 运行库编译；AAR 明确声明 minSdk 26。此链接基线不代表已在 API 26 设备验证完整应用。只提供 arm64-v8a 和 x86_64。

构建先在暂存目录生成整套产物，通过 ELF、导出、依赖和 AAR 一致性检查后再发布到 `artifacts/`；失败不会覆盖已有产物。发布库使用同版 NDK 的 `llvm-strip --strip-unneeded`。本地诊断副本位于 `.work/symbols/`；上游 Release 构建本身已对 `libortextensions.so` 去除部分符号，因此这些副本不承诺包含完整调试信息。

```sh
python3 scripts/verify_artifacts.py --artifacts artifacts --output artifacts/manifest.json
python3 -B -m unittest discover -s tests -v
```

## 与上游的差异

算子及分词实现不改动。依赖使用上游所选的 SentencePiece 0.1.96、protobuf 21.12、GSL 4.0.0，并保留上游对 SentencePiece／protobuf 的构建补丁。

[本地补丁](patches/java-jni-symbol.patch) 只修正 Java 原生方法的符号名称：上游 Java 声明位于 `OrtxLibrary`，C 导出却误写为 `Utils`。IPA Board 原来只调用 `OrtxPackage.getLibraryPath()`，不触发此缺陷；本次同时验证注册函数地址接口有效。

[构建包装](integration/CMakeLists.txt) 继续使用上游原生 CMake target，保留 v0.13.0 JNI 静态链接算子的方式；Java 源码原样用 `javac` 编译并生成 JNI 头，绕开旧 Gradle／AGP 打包流程及其源码格式化副作用。AAR 包装补充 minSdk 26 与许可材料，Java 公共类名、方法签名和动态库名不变。两份 `.so` 都保留，不把文件重命名冒充 JNI 包装库。

## 接入与验证

接入说明见 [integration/README.md](integration/README.md)，已执行检查与边界见 [VALIDATION.md](VALIDATION.md)。验证使用独立 IPA Board 副本和专用模拟器，经项目保护流程完成备份、测试、恢复和验证；日常测试手机以及正式 IPA Board 工作树均未修改。

ELF 静态检查、真实 16KB 进程运行、ARM64 真机验证是不同层级，具体已执行范围以验证记录为准。

第三方原始许可、实际链接组件与来源摘要见 [licenses/README.md](licenses/README.md)。项目级新许可证仍未选定；上游材料遵循各自原许可。
