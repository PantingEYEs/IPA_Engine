# Android IPA 图引擎依赖库

`com.ipaengine.graph.GraphEngine` 是 Rust 压缩词典核心的 Java/JNI 接入层，目标为 Android API 26+、`arm64-v8a`。三星 S21 Ultra 使用这个 ABI。运行时使用只读映射的编译词典，不依赖 SQLite、Python、PyTorch、OpenFst 或联网；词频排序不读取编辑器或外部上下文。

发布部分为依赖库。`engine_android/android/tests/GraphProbe.java` 是本阶段的独立手机探针，用于验证 Android/JNI 包装；最终引擎端到端设备 CLI 测试由 `cli_tests/` 负责。IPA Board 宿主工程、输入法服务、Messenger 注册与键盘界面仍由宿主提供。

## 构建产物

仓库的 `engine_android/scripts/build_android_library.py` 使用 Rust 1.89+、JDK、Android SDK 和 NDK，分别生成 Java `classes.jar`、原生 JNI `.so`、设备 CLI，以及完整词典和 manifest；脚本不再打包 AAR。默认输出到 `data/engine_android/builds/24/`，`--core-only` 会跳过词典和 manifest。宿主可直接消费这些独立文件，或按自己的构建方式集成；脚本参数以 `--help` 为准，生成的二进制和构建目录不进入 Git。

构建过程、JNI 包装验证和本阶段的测试实现保存在 `engine_android/`；生成的组件、探针输出与构建报告分别写入 `data/engine_android/` 下对应目录，不要将构建产物放回源码目录。

```sh
rustup target add aarch64-linux-android
python3 engine_android/scripts/build_android_library.py --output-dir data/engine_android/builds/24
# 32 位对照须另行提供对应词典和 manifest；公开快照只含 24 位。
python3 engine_android/scripts/build_android_library.py --dictionary data/dictionary/compiled/lexicon-32.ipad \
  --manifest data/dictionary/compiled/lexicon-32.manifest.json --output-dir data/engine_android/builds/32
```

若宿主需要 Gradle 依赖包，应在宿主工程中自行将 Java 类、JNI 库和词典资源按其构建方式打包。仓库构建脚本不生成 AAR。

Java API 需要 Java 8 语言支持，Android `minSdk` 至少 26。宿主须保留 JNI 类与方法名称；库本身不提供输入法服务或 Activity。

也可以用 `engine_android/android/CMakeLists.txt` 把核心与 JNI 包装构建成共享库。工具链必须为 NDK 的 `android.toolchain.cmake`，目标 `ANDROID_ABI=arm64-v8a`、`ANDROID_PLATFORM=android-26`，构建时先用 Cargo 生成 Rust 静态库，再链接 JNI 薄层；共享库名称为 `ipa_graph_android`。[NDK CMake 文档](https://developer.android.com/ndk/guides/cmake)、[Android 依赖库文档](https://developer.android.com/studio/projects/android-library)

## 初始化与资源生命周期

在宿主工作线程初始化一次并复用：

```java
GraphEngine engine = GraphEngine.openBundled(context);
// 也可加载宿主已经准备好的独立编译文件：
// GraphEngine engine = new GraphEngine(dictionaryAbsolutePath);
```

`openBundled` 读取完整编译 manifest，要求 `complete=true`、`status=complete`、合法 SHA-256 和小于 40,000,000 字节的绝对体积上限。推荐目标仍为 30,000,000 字节；用户已允许略超该目标的 32 位索引比较包，包装层不因推荐值直接拒绝它。它复制到 `Context.getNoBackupFilesDir()/ipa-graph/lexicon-<sha256>.ipad`，校验字节数和哈希，然后原子替换临时文件并加载。相同哈希文件会复用，但每次初始化仍校验缓存，故首次和再次初始化有文件读取成本；该成本不在每次输入查询中发生。清单和词典缺失、校验失败或原生库不可用时抛出 `IOException`，由宿主转换成自己的初始化故障。

映射期间不要修改或覆盖对应词典文件。新版本采用新的哈希文件名；旧文件的清理应由宿主在确认没有使用者后完成。包装层不因服务解绑自动释放实例。所有实例操作和 `close()` 都是同步的，确保关闭不会释放正在查询的句柄；`close()` 可重复调用，关闭后的查询或配置抛出 `IllegalStateException`。宿主确定资源不再使用时调用 `close()`，可用 `try`/`finally` 或 `AutoCloseable`。

## 配置与查询

`Policy` 与 `Options` 均为不可变快照：

```java
// 地区顺序：en-US、en-GB、zh-CN、zh-TW、ja-JP。
engine.configure(
    new GraphEngine.Policy(true, true, true, true, true),
    new GraphEngine.Options(30, false, 60, 2048, true, 20000)
);
// Options 顺序：候选上限、前缀补全、单次预算毫秒、前缀键预算、省略开关、省略状态预算。
List<GraphEngine.Candidate> candidates = engine.query("həˈloʊ", "", "");
GraphEngine.QueryResult diagnostic = engine.queryDetailed("hə", "", "");
```

候选上限为 1–180，预算为 1–60 ms，前缀键预算为 1–65,536；默认分别为 30、60、2,048，补全关闭，默认顺序省略匹配开启（allowOmissions=true，omissionStateBudget=20000，范围1–200000）。四参数构造器保留并启用省略；六参数构造器可关闭省略用于严格对照。`configure(policy)` 只改变地区，`configure(policy, options)` 同时更新查询选项。不要在 UI 线程运行初始化或同步查询。

每次 `raw` 是当前完整预输入，不是增量。Java 检查原串的 UTF-16 长度不超过 64，拒绝孤立代理，并做 NFC 规范化后再调用原生核心。`acceptsInput` 使用同一规则与词典字符表；`getSegmentPattern()` / `segmentPattern()` 返回动态字符表对应的非空分段正则，宿主应覆盖原先只接受拉丁字母的模式。

`query(raw)`、`query(raw, beforeCursor)` 和 `query(raw, beforeCursor, afterCursor)` 均返回不可变 `List<Candidate>`。空请求返回空列表，本版本不提供预测；无匹配与不支持输入也返回空列表。左右上下文可为空，当前不参与评分。所有候选替换整个查询串；本版本不会返回截断的部分词，也没有连续句子解码。

`queryDetailed` 同时返回 `status`（`ok`、`unsupported_input`、`timeout`、`error`）、`elapsedMs`、`exactFound`、`truncated`、`matchedKeys` 和 `matchedRecords`。其中耗时来自原生核心，不包括 Java JSON 转换和宿主线程调度。error/timeout状态返回空候选；宽松搜索触及截止且已有候选时返回ok+truncated并保留候选。候选上限、前缀键预算或省略状态预算触发时 `truncated=true`，此时不是全局词频最优的前 N 项。空请求的状态为 `ok`。

候选六个宿主字段是 `text`、`language`、`rank`、`kind`、`nativeScore`、`contextAffinity`。`kind` 为 0（转换）或 2（补全），与现有宿主枚举一致；`contextAffinity` 当前为 0。`matchedIpa`、`frequencyScore`、`region`、`derived` 是单独的观察字段，不能拼进上屏文本。`frequencyScore` 为 Zipf × 20 的单字节量化参考值。状态字符串也不能原样作为 Messenger 的固定故障码，宿主需明确映射。

当前包装只提供五地区开关。派生发音随编译词典加载，尚未提供运行时独立禁用开关。

## 宿主接入边界与后续评分

宿主负责请求的 `requestId/revision`、候选数组和枚举转换、固定错误码、路由/功能开关、过期回复丢弃与进程隔离，具体见 [引擎开发规范](../docs/引擎开发规范.md)。宿主加载本批独立 Java/JNI 文件后，仍须在 IPA Board 工程中注册 IPA 路由。

保留的 `gru/` 训练/推理工具不作为默认 Android 评分组件，旧权重已删除，当前依赖库不附带预训练模型。新模型训练工具仅在完整本地项目中保留，说明见本地 `gru/README.md`。未来可对图候选执行 teacher-forcing GRU 打分并校准词频组合，模型只应重排合法图候选；未覆盖的字符不能成为删除词典候选的理由。候选列表是不可变的，接入层需要创建新的宿主候选和 rank，而不能修改本次结果。没有上下文时，先用确定词频公式建立基线。

## 验证

`engine_android/android/tests/verify_desktop_jni.py` 编译真正的 JNI 共享库，在桌面 JVM 的 `-Xcheck:jni` 下检查 Unicode 文件名、非 BMP 候选、JSON 引号/反斜杠、Java NFC、孤立代理、地区配置和关闭后的句柄保护：

```sh
python3 engine_android/android/tests/verify_desktop_jni.py \
  --android-jar /path/to/android.jar \
  --java-home /path/to/jdk
```

桌面 SDK 的 `org.json` 是 stub，因此此工具针对原生 JSON 传输使用反射读取私有 JNI 返回值，不宣称完成 Android 的 JSON 解析、asset 提取或设备运行测试。JNI 的字符串转换明确采用 `GetStringChars` / `NewString`，自行完成 UTF-16 与标准 UTF-8 转换，以避免 `GetStringUTFChars` / `NewStringUTF` 的 modified UTF-8 损坏补充平面字符。[JNI 字符串接口](https://docs.oracle.com/en/java/javase/21/docs/specs/jni/functions.html#string-operations)

以下为迁移前 C++ v0.0.1-dev 的历史结果，不代表 Rust v0.0.3-dev；Rust 本次以桌面与 Android 交叉编译验证为准。

已在 S21 Ultra `SM-G998U1`（Android 15 / API 35）执行原生双格式对照和实际 24 位 AAR 包装测试。两格式的 5,014 个输入输出一致，每格式 500,000 次原生查询无超时/错误。p95 各轮中位数为 24 位 0.006979 ms、32 位 0.007292 ms；采样峰值进程 RSS 为 35,260/40,328 KiB。32 位未带来显著查询收益，默认仍为 24 位。指标含预热、不清空文件缓存、RSS 为抽样；原生耗时不包括 Java/JNI 或输入法 UI。

历史独立测试 APK 真实调用 `openBundled`、Android `JSONObject` 与 JNI，检查五地区查询、地区开关、前缀预算、候选限制/不可变性、空/完整/非法 UTF-16 输入、NFC、regex、初始化故障、缓存复用/损坏修复、关闭后的句柄保护。它使用当时 AAR 的 `classes.jar`、`.so` 和完整资产，而不是桌面 stub。

完整本地项目中的 `engine_android/android/tests/test_android_wrapper.py` 只用于已有完整历史 AAR，需显式提供对应 AAR 和来源用例；该脚本未包含在公开快照中，也不支持当前 standalone 构建目录。当前接入可先执行上面的桌面 JNI 验证，再把公开的 `engine_android/android/tests/GraphProbe.java` 集成到宿主 Android 测试工程，配合本批 Java/JNI 与词典资产验证设备上的 JSON、初始化和生命周期。

历史包装测试还测量 10,000 次完整 `GraphEngine.queryDetailed`（预热 1,000 次，五地区各 1,000 个输入交替排列，全地区、180 候选），包括 Java NFC、JNI、原生查询/JSON 和 Android JSON/对象构造。输出独立的 p50/p95/p99/max、状态、PSS 和原生/Java 堆起止数据；不包含输入法 UI 和资产加载。

standalone native CLI 在 Android 设备上的测试归 [CLI 与最终引擎测试](../../cli_tests/README.md) 阶段。

报告为 `data/cli_tests/reports/graph-s21-comparison.json` 与 `data/engine_android/reports/android-wrapper-probe.json`。实际系统输入法接入、15–30 分钟持续输入/退格/服务生命周期，以及温度与耗电仍应由宿主场景验收；短基准不等于长期耗电测量。

## v0.0.2-dev 顺序省略

完整输入优先；默认允许漏音段、声调、重音、附加符、长音和分节/空格，输入必须依原顺序全部出现在词典IPA的规范分解比较视图中，不做替换或换序。普通数字1–5映射˩˨˧˦˥，表示音高值而非声调类别。`maɪ`可查my，`nixɑʊ`及`ni35 xɑʊ̯21`可查你好。`Candidate.omittedSymbols`显示遗漏数，`QueryResult.exploredStates`显示搜索状态数；`truncated`表示结果受候选/搜索预算限制，不能保证包含所有合法候选。JNI参数已扩展，须使用同一客户端批次的Java类与.so，勿混搭旧包装。新版未追加实机性能测试。
