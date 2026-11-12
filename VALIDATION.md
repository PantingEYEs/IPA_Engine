# ONNX Runtime Extensions 构建与验证

日期：2026-11-11。目标为 **16KB 页设备**；4KB 不是维护者要求的支持或验收平台，已执行的相关记录仅作为此次迁移的历史输出对照。

## 交付组合

- Extensions 官方 v0.13.0，提交 `9d5638b0b1f28ed2ca9ef589d5ec3ea86f859432`。
- SPM-only 算子配置，NDK r29 / Clang 21、CMake 3.31.6、Temurin JDK 21.0.13+9、GNU Make 4.4.1；Java 8 字节码，Android API 26 链接基线。
- arm64-v8a 与 x86_64，各有 `libortextensions.so` 和 `libonnxruntime_extensions4j_jni.so`。
- 最终 AAR：`onnxruntime-extensions-android-0.13.0-ipa-e5-16kb.aar`。
- AAR SHA-256：`20620659f2695dc5e36cca7f339e64c606f36413b9a42c144eac0aace73cecc6`。
- 配套 ONNX Runtime 1.23.2 及原 E5 模型／分词图／校准数据保持不变。
- IPA Board 测试基线：`a2e3bc84cc9471831e0dd049c570edd8f4d96d1f`，仅使用独立检出副本。

## 静态与主机检查

[产物清单](artifacts/manifest.json)记录四个库的实际 ELF LOAD 对齐、offset／vaddr 同余、RELRO、系统动态依赖和有效导出。四个库全部满足 16KB 要求；JNI 包装导出与原 Java 声明对应。AAR 的四个库逐字节等于独立产物，Java 类名、签名、native 方法和库名通过检查。

构建工具的 **31 项专项测试通过**，包含损坏 ELF／依赖／Java／AAR、隐藏或注入源码、补丁篡改、漏库、许可缺失／路径与确定性打包的负例。见 [build-tool-tests.json](validation/build-tool-tests.json)。

隔离应用的 **34 项相关主机单元测试通过**；应用与测试 APK 编译、资源摘要、套件登记及 lint 通过。未跑应用全量测试。16KB 包装调整后只重做必要的编译、资源检查与 lint；296 条 lint 警告及 129 条由既有 baseline 过滤的错误不属于本次解决范围。

[实际图算子清单](validation/graph-operator-inventory.md)确认分词图只有一个 `ai.onnx.contrib::SentencepieceTokenizer`；完整 E5 图的 1,630 个节点、21 类算子均属于 ONNX 标准域。没有其他 Extensions 算子依赖。当前裁剪不改变 ONNX Runtime 主库的算子集合；未来更换模型／分词器仍需重新核验。

[许可证与构建输入审计](licenses/evidence/dependency-audit.json)核对两个 ABI 各 57 个编译源文件、105 个项目及依赖头文件、实际静态运行库贡献和全部许可文本摘要。

## 严格 16KB 运行验收

实际环境：Android **API 36 / x86_64 / 64-bit**，测试进程的 `Os.sysconf(_SC_PAGESIZE)` 返回 **16384**。隔离应用声明 `android:pageSizeCompat="disabled"`，编译后的 manifest 属性值已核验为 64。没有排除任何其他原生库来规避装载问题。

应用与测试 APK 合计 **20 个原生库条目**全部通过 ELF LOAD／RELRO 检查，两包均通过 `zipalign -c -P 16 -v 4`。见 [APK 检查](validation/apk-16kb/apk-16kb-audit.json)与[环境记录](validation/strict16-environment.json)。

通过项目备份／恢复入口执行 **12/12 项专项**：

- 实际 JNI 加载、原生注册入口、完整旧分词图和 118,346,824 字节 E5 模型加载与推理。
- 17 组中英日、Unicode、空白、全角、IPA、Emoji 和长输入分词序列与原库完全一致。
- 22 个真实 E5 分数均在 `0.0001` 容差内；最大绝对差值为 **3.78955841706663e-09**。
- 前后文、多语言候选保留、默认关闭、实时关闭、过期结果拒绝、取消与后续恢复、缓存、空上下文。
- 服务绑定失败保留原候选、测试自有资源部署失败／模型损坏的正确阶段报告，以及失败后的正常推理恢复。

完整结果与 APK 摘要见 [compare-16k-runtime-results.json](validation/compare-16k-runtime-results.json)。原 APK 与私有应用数据已恢复并校验，备份已删除，待恢复列表为空。专用模拟器和独立测试连接已关闭；日常手机未操作。

## 对照记录与边界

首次原库对照中，8 项 E5 专项与 2 项集成通过，另 2 项等待原生引擎就绪超时。原因是旧测试默认启用了 x86_64 不支持的 ARM64 专用 IPA 引擎。隔离夹具仅在非 ARM64 进程关闭该 backend，并在测试后恢复偏好；Rime、LatinIME、Mozc、断言和超时均保持。四项集成定点重跑通过。相关失败记录和最小测试补丁保留在 [validation](validation/README.md)。

此前完成的原库／新库 4KB 对照均有恢复验证记录，新库为 12/12 通过。它们只用于历史差分；**不构成 4KB 支持承诺，也不是后续默认回归要求**。

ARM64 已编译并完成静态核验，**未进行 ARM64 设备运行**。没有验证 API 26 的完整应用、其他 Android／ABI 组合、所有编辑器、内存压力或未编入的 Extensions 算子。此结果不等于 IPA Board 全量回归或全部第三方组件的发布验收。

当前 IPA Board 正式工作树、手机安装包和用户配置均未改动。本分支不自动关闭 IPA Board Issue；后续产品接入仍须按其工作流处理。
