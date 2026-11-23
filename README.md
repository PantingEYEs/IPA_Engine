# IPA Engine

供 Android 输入法接入的 **IPA → 中英日俄阿韩文字候选** 引擎，采用 Rust DAFSA 图查询、
顺序省略匹配与词频排序。键盘 UI、编辑器提交、资源下载和更新切换由宿主实现。

`main` / `develop` 仅提供白名单源码、合成测试和接入文档，不承担客户端更新分发。
运行时词典、DEX、JNI 及安装包全部存放在 [GitHub Releases](https://github.com/PantingEYEs/IPA_Engine/releases)。
本 README 是输入法 UI 开发者的接入手册；不会要求客户端读取某个源码分支。

本文新增的语言包、按需加载及引擎反查接口属于 **v0.0.5-dev 开发版本**。
当前 Latest 正式版本仍为 **v0.0.4**，其 Release 附件不会被开发构建替换。
新增接口须使用本次配套 DEX/JNI，不能对旧 v0.0.4 类直接调用；安装能力以选中 Release 的 `client.json` 为准。

## 1. 从 Release 获取引擎与词典

选择符合用户通道偏好的非 Draft Release，下载该 Release 顶层 `client.json`。
清单为 `schemaVersion=1`、`manifestType=ipa-engine.release`。
`vX.Y.Z-dev` 与 Pre-release 标记一致；正式版本为 `vX.Y.Z`。
排除预发布后没有正式版本就显示“暂无正式版本”，不回退选择开发版。

| 字段 | 用途 |
| --- | --- |
| releaseVersion / engineVersion / dictionaryVersion | 发布、引擎、词典各自的版本。相同词典哈希可复用。 |
| abi / minSdk / entryPoint | ABI、最低 Android API、Java 入口类。当前 arm64-v8a / 26 / com.ipaengine.graph.GraphEngine。 |
| dictionaryFormat / dictionaryIndexBits / dictionaryKeys | 本批词典格式、位宽及 IPA 键数。v0.0.4 是 IPADWG01 v2 / 24 位；本次开发包为 IPAPACK1 容器、格式号 4，内部各图仍为 24 位。 |
| assets.enginePackage | 仅含 classes.dex、libipa_graph_android.so 和 engine.json 的引擎 ZIP。 |
| assets.dex / assets.native | 配套引擎裸文件，可分别下载，DEX/JNI 必须一起切换。 |
| assets.dictionary / assets.dictionaryManifest | 独立裸词典及编译/来源 manifest。 |
| assets.clientPackage | 可选的完整客户端 ZIP，包含词典；新版流程固定提供。 |
| documentationUrl / provenance | 固定提交的接口说明及构建来源。 |

每个 assets 对象都有精确的 `name`、`bytes`、`sha256`、完整 `url`。
按名获取同一 Release 的 Asset 并核验，不能猜文件名或拼接旧仓库路径。
引擎 ZIP 内的 `engine.json` 记录文件路径、字节数、SHA-256 及词典格式要求。
完整 ZIP 内的 client.json 不引用该 ZIP 自身，避免循环哈希。
`ASSET_SHA256SUMS` 覆盖所有其他附件；`SHA256SUMS` 校验裸文件及构建元数据。
保留词典 manifest 和 THIRD_PARTY_NOTICES.json，按逐来源声明使用。

不再新增 AAR、board-update.json 或分支 VERSION。前三版已确认的历史 Release 和
原包保持不变，其中旧路径说明仅作历史记录，新获取合同以顶层 client.json 为准。
下载后先验证并暂存，确认引擎和词典兼容后切换；不要覆盖仍在 mmap 使用中的词典。

## 2. Android / Gradle 要求

要求 **Android API 26+、arm64-v8a、Java 8 语言支持或更高**。原生 ELF 支持 16 KiB 页对齐。JNI 共享库包含 Rust 图核心与 UTF-16 转换薄层；宿主使用 Android 平台提供的 `org.json`，无需额外运行时依赖，也不需要在手机安装 Python、SQLite、PyTorch、OpenFst 或联网。

宿主按 Release 清单加载配套 DEX 和 SO，并将已校验词典放在应用私有目录。
若静态集成，可从源码构建 classes.jar/JNI，并自行打包到宿主工程；不再提供新的 AAR。
`openBundled` 示例要求宿主自行打包对应 assets；独立下载词典可使用 `new GraphEngine(path)`。
JNI 类和方法名称必须由宿主混淆规则保留。

## 3. 初始化、复用和关闭

在工作线程初始化一次，然后复用同一实例：

~~~kotlin
import com.ipaengine.graph.GraphEngine
import java.io.IOException
import java.util.concurrent.Executor

// ready / failed 都在传入的工作线程执行；需要更新 UI 时由宿主切回主线程。
fun openEngine(
    dictionaryAbsolutePath: String,
    worker: Executor,
    ready: (GraphEngine) -> Unit,
    failed: (IOException) -> Unit
) {
    worker.execute {
        val engine = try {
            GraphEngine(dictionaryAbsolutePath)
        } catch (failure: IOException) {
            failed(failure)
            return@execute
        }
        ready(engine)
    }
}
~~~

若宿主自行打包资产，也可调用 `openBundled(context)`，它从宿主 assets 读取 `ipa/lexicon.manifest.json` 与 `ipa/lexicon.ipad`，检查完整标记、字节数和 SHA-256，复制到：

~~~
context.noBackupFilesDir/ipa-graph/lexicon-<sha256>.ipad
~~~

复制经过临时文件和原子替换，随后只读映射；已有相同哈希缓存会复用，但每次初始化仍核验缓存。损坏缓存会从资产重新提取。初始化包含文件读取与哈希成本，应避免逐键重新创建实例。安装后的 APK 资产和私有映射缓存各保存一份编译文件，存储预算需同时考虑这两份；查询不会展开整个词形池。

清单或资产缺失、哈希错误、格式错误、原生库不可用等初始化失败抛出 `IOException`。宿主应报告自己登记的初始化错误码并保留原文输入路径。异常文字不能作为候选或直接作为 Messenger 错误码。

也可用 `new GraphEngine(dictionaryAbsolutePath)` 打开宿主准备好的独立编译文件。客户端更新批次没有 AAR 资产，宿主在加载配套 DEX/JNI 并校验下载的词典后使用这个入口，不调用 `openBundled`。该入口验证二进制格式；文件来源、实际大小和 SHA-256 核验由调用方负责。使用旧 AAR 时默认已带词典；两种接入均无需在手机编译资料库。

`GraphEngine` 实现 `AutoCloseable`。实例方法和 `close()` 同步保护句柄，`close()` 可重复调用；关闭后的查询、配置、判定和属性访问会抛出 `IllegalStateException`。宿主仍应在同一工作队列中安排生命周期：停止接收请求、等待已提交工作结束，再关闭；不要在 UI 线程并发关闭或靠关闭句柄取消查询。服务解绑不等于实例已销毁。

映射期间保持对应文件不可变。新词典使用新的哈希文件名；确认旧实例不再使用后才能清理旧文件。库不持有编辑器、剪贴板或输入法 UI。

本次默认只需一个 `lexicon.ipad`：容器内含 `core`、`ru`、`ar`、`ko` 四个独立语言包及反查索引，`GraphEngine(path)` 默认全部加载并启用。当前完整开发词典为 **65,466,387 字节**；其中原中英日核心图仍为 **28,986,769 字节，字节内容未变**，新增体积包括俄阿韩图与各包反查索引。单图必须小于 40,000,000 字节，容器及一个实例挂入的词典总量必须小于 96,000,000 字节；30 MB 是原核心图的推荐目标，不再用于拒绝这份完整多语言交付。旧 IPADWG01 v2 单图仍可直接打开，自动作为 `core` 包处理。

## 4. 地区和查询选项

`Policy`、`Options` 都是不可变配置快照：

~~~kotlin
// 八参数顺序：en-US、en-GB、zh-CN、zh-TW、ja-JP、ru-RU、ar-001、ko-KR。
engine.configure(
    GraphEngine.Policy(true, true, true, true, true, true, true, true),
    // 保留四参数构造器，默认开启省略匹配。
    GraphEngine.Options(30, false, 60, 2048)
)
~~~

| Policy 字段 | 控制的候选 | 默认 |
| --- | --- | --- |
| `enUS` | 美国英语，标签 `EN` | true |
| `enGB` | 英国英语，标签 `EN` | true |
| `zhCN` | 大陆普通话/简体，标签 `简` | true |
| `zhTW` | 台湾国语/繁体，标签 `繁` | true |
| `jaJP` | 日语标准语，标签 `日` | true |
| `ruRU` | 俄语，地区 `ru-RU`，标签 `RU` | true |
| `arStandard` | 阿语规范参考层，地区 `ar-001`，标签 `AR` | true |
| `koKR` | 韩国标准语参考，地区 `ko-KR`，标签 `KO` | true |

`new GraphEngine.Policy()` 开启全部八个地区。旧五参数构造 `Policy(enUS, enGB, zhCN, zhTW, jaJP)` 保留，新增俄阿韩默认均为 true；要关闭全部地区必须传八个 false。关闭某地区后不产生该地区候选／反查读音；Policy 只过滤输出，不卸载图、反查索引或省略搜索辅助数组。资源开关使用下一节的 PackMode。

阿语暂使用现有来源默认规范音位资料，保留来源与变体标记，不宣称整包是纯净、逐条认证的 MSA；以后找到更干净的 MSA 来源再评估替换。`ar-001` 只是地区标识，不构成 MSA 认证。派生发音暂时没有独立禁用开关。

| Options 字段 | 范围 | 默认及含义 |
| --- | --- | --- |
| `candidateLimit` | 1–180 | 30；去重后的输出上限。 |
| `prefix` | Boolean | false；显式开启 IPA 前缀补全。 |
| `deadlineMs` | 1–60 | 60；所有正向图共用的原生查询截止预算。省略搜索已有结果时保留候选并标记截断，否则超时为空。 |
| `prefixKeyBudget` | 1–65,536 | 2,048；各正向图显式前缀补全最多访问的接受键数。 |
| `allowOmissions` | Boolean | true；允许漏输词典符号，仍消费全部输入。 |
| `omissionStateBudget` | 1–200,000 | 20,000；每个正向图的省略搜索状态预算，所有图仍共用同一个截止时间。 |

六参数构造器 `Options(30, false, 60, 2048, true, 20000)` 可控制省略匹配。对照严格模式使用 `Options(30, false, 60, 2048, false, 20000)`；数字调值转换在两种模式下都生效。四参数构造器与 `Options()` 默认开启省略。

越界选项抛出 `IllegalArgumentException`。`configure(policy)` 只更新地区，保留当前 Options；`configure(policy, options)` 同时更新二者。`getPolicy()`、`getOptions()` 返回当前快照。查询预算与诊断耗时针对原生核心，不是整个 Java 调用或 UI 调度的硬性墙钟上限。

### 4.1 语言包模式与真正按需加载

| Pack ID | 包含内容 |
| --- | --- |
| `core` | 原有美国／英国英语、大陆普通话／台湾国语、日语共享图。 |
| `ru` | 俄语图和反查索引。 |
| `ar` | 阿语规范参考图和反查索引。 |
| `ko` | 韩语图和反查索引。 |

| PackMode | 正向输入 | 反查 | 资源 |
| --- | --- | --- | --- |
| `INPUT` | 参与 | 可用 | 映射该包并建立正向搜索辅助数组；本次四包默认模式。 |
| `REVERSE_ONLY` | 不参与 | 可用 | 映射图和反查索引，不建立正向省略搜索辅助数组。 |
| `OFF` | 不参与 | 不可用 | 释放该包映射和辅助数组；在途原生快照结束后完成释放。 |

可以直接在现有实例的工作线程上切换：

~~~kotlin
engine.setPackMode("ru", GraphEngine.PackMode.REVERSE_ONLY)
engine.setPackMode("ar", GraphEngine.PackMode.OFF)
engine.setPackMode("ko", GraphEngine.PackMode.INPUT)
val packStateJson: String = engine.listPacksJson()
// 模式改变后重新读取字符表正则。
val segmentRegex = Regex(engine.segmentPattern())
~~~

如果希望初始化时也不加载暂未使用的包，调用双参数构造器 `GraphEngine(path, false)`。
下面是可直接调用的初始化函数，仅读取目录，然后启用中英日输入和俄语反查；阿语和韩语保持 OFF：

~~~kotlin
fun openCoreWithRussianLookup(dictionaryPath: String): GraphEngine {
    val engine = GraphEngine(dictionaryPath, false)
    try {
        engine.setPackMode("core", GraphEngine.PackMode.INPUT)
        engine.setPackMode("ru", GraphEngine.PackMode.REVERSE_ONLY)
        return engine
    } catch (failure: RuntimeException) {
        engine.close()
        throw failure
    }
}
// 在工作线程调用；返回的实例由宿主持有并在不再使用时 close()。
~~~

`listPacksJson()` 返回 `{format_version, packs}`；各项有 `id`、`region_mask`、`mode`（`off/reverse/input`）、`loaded`、`forward`、`reverse`、`graph_bytes`、`reverse_bytes`、`search_index_bytes`。`forward/reverse` 表示包的能力，实际是否使用看 `mode/loaded`；图与索引字节数是资源尺寸，`search_index_bytes` 是当前正向辅助数组占用，不是整个进程 PSS 或一次查询临时内存。关闭所有正向图时 `segmentPattern()` 返回 `(?!)`，`acceptsInput` 不接受非空输入，反查专用包仍可返回读音。

Policy 和 PackMode 独立生效：允许俄语地区但把 `ru` 设 OFF，仍不会产生俄语结果；只关闭 Policy 不会释放包资源。`core` 是三语言共享图，不能靠单个地区开关卸载其中某一语言；只启用 `core` 时保留原单图查询路径。默认四包一起启用会增加实际查询和常驻资源成本，不能套用旧三语言的耗时。

### 4.2 词汇反查 IPA

反查现在属于同一个 Android 引擎，支持 INPUT 和 REVERSE_ONLY 包，使用词形索引定位读音，不需要读取原始资料库：

~~~kotlin
import org.json.JSONObject

val result = JSONObject(engine.reverseLookupJson("кот"))
val readings = result.getJSONArray("readings")
for (i in 0 until readings.length()) {
    val reading = readings.getJSONObject(i)
    val ipa = reading.getString("ipa")
    val region = reading.getString("region")
    val packId = reading.getString("pack_id")
    // 将 ipa / region / packId 显示在宿主的读音查询界面。
}
val incomplete = result.getBoolean("truncated")
~~~

`reverseLookupJson(word)` 先做 NFC，按完整词形精确匹配，最多允许 1,000 个 UTF-16 单元，不受正向 IPA 的 64 单元限制；不自动改大小写、简繁或生成未知读音。空串、孤立代理和超长词形抛出 `IllegalArgumentException`。响应为 `{word, readings, truncated}`；每项含 `ipa, region, frequency_score, frequency_zipf, derived, pack_id`。不存在的词、OFF 包及 Policy 禁用的读音不返回，结果为 `readings=[]`；最多返回 512 条，通过 `truncated` 标记未完整返回。旧 v2 图没有反查索引时采用兼容扫描，新格式使用预编译反查索引。

### 4.3 未来独立交付语言包

本次一个容器已经包含四包，宿主无需添加第二个文件。未来单独发布时，各文件仍采用相同容器格式，只包含自己的语言包。新 API 可在原实例挂入另一个不重复的包：

~~~kotlin
// 前提：当前实例尚未包含 ru；文件已经由宿主校验大小、SHA-256 和来源。
engine.addDictionary(russianPackAbsolutePath, false)
engine.setPackMode("ru", GraphEngine.PackMode.REVERSE_ONLY)
~~~

第二参数为 true 则加载附加文件的默认包，false 仅读目录。重复 Pack ID／地区、超过八个包或总量达到 96 MB 会拒绝，失败不改变当前实例；加载失败抛出 `IOException`。更新已挂入的包时创建新实例并切换，不能覆盖仍在映射的文件。模式切换和挂入均在工作线程执行。以上能力已在引擎中保留，应用侧语言包入口可后续再接。

## 5. 输入、判定与分段

每次 `raw` 是**当前完整待处理预输入**，不是新增的字符。已输入后退格、改地区或改上下文时，仍重新传完整当前串。

| 输入 | 规则 |
| --- | --- |
| `raw` | Java/Kotlin UTF-16 长度最多 64；原串先校验长度和代理配对，再做 NFC，规范化后也不得超长。 |
| `beforeCursor` / `afterCursor` | 按宿主合同各最多 256 个 UTF-16 单元；可为空，当前不参与排序。Java 包装把 null 上下文视为空串，长度限制应由宿主保证。 |
| 空 raw | 返回空候选，详细状态为 `ok`；本版没有预测功能。 |
| null raw、孤立代理、超长或未知字符 | 返回空候选，详细状态为 `unsupported_input`。 |
| 字符受支持但没有符合当前匹配规则的路径 | 返回空候选，详细状态为 `ok`。 |

`acceptsInput(raw)` 使用相同的 UTF-16/NFC 规则及词典字符表。它表示能处理整串，不保证有词典命中；关闭所有地区也不会使字符判定变成命中判定。空串和纯空白不接受。

`segmentPattern()` 与 `getSegmentPattern()` 返回由当前 INPUT 包字符表生成的 Java 正则，使用 Unicode 码点字符类与 `+` 匹配非空字符段；没有正向图时为 `(?!)`。宿主用 `Pattern.compile(engine.segmentPattern())` 或 Kotlin `Regex` 编译，并覆盖原先只识别拉丁字母/撇号的分段规则，切换包后重新读取。IPA 音段、组合符号、重音、调值和内部界限应作为完整段处理，不能把有效 IPA 拆断。

输入先做 NFC。数字 1–5 分别转换为 `˩˨˧˦˥`，表示 Chao 音高值，不是普通话声调类别；0、6–9、全角数字不转换。完整匹配保留已经输入的声调、重音、长音、分节和空格。

默认还接受顺序省略：比较视图对 IPA 预组字符做规范分解，完整输入必须能依序出现在词典 IPA 中，词典中其余符号可省略，包括音段、调线、重音、附加符、长音、分节与空格。不会改动、忽略或重排已经输入的字符，不进行替换纠错。输出及 `matchedIpa` 仍使用词典原记录，附加符采用字符级匹配，不额外强制与原音段绑定。Java/Python 入口先 NFC；直接 Rust/C 调用者也应先 NFC，逐字符分解不承诺全部跨字符组合符重排。

例如：

| raw / 地区 | 已收录的输出示例 |
| --- | --- |
| `həˈloʊ` / en-US | hello |
| `həˈləʊ` / en-GB | hello |
| `tu` / en-US | too、two，按词频排序 |
| `maɪ` / en-US | my，省略重音 |
| `nixɑʊ` / zh-CN | 你好，省略声调、附加符和边界 |
| `ni35 xɑʊ̯21` / zh-CN | 你好，数字调值对应已收录完整读音 |
| `ni˨˩˦ xɑʊ̯˨˩˦` / zh-CN | 你好 |
| `tʰaɪ˧˥.wan˥˥` / zh-TW | 臺灣 |
| `ɡakːoɯ` / ja-JP | 学校 |

`tu` 与 `tuː` 是不同完整键；严格模式中删除声调、重音或空格会得到另一个键；默认省略模式则继续匹配包含这些省略符号的词典路径。日语例子的 `ɡ` 是 IPA 字符 U+0261，不会在 Java 入口把普通拉丁 `g` 自动替换成它。数字音高值已支持；拼音、注音、假名及普通话声调类别数字没有自动转 IPA 输入方案。

非空候选替换**本次查询的全部 raw**。查询 `həˈloʊ123` 不会只转换前面再丢掉 `123`；查询字符合法的 `həˈloʊzzz` 也不会偷偷返回 hello。如果宿主先分段，外部未查询文字由宿主保留，候选仅替换该段；分段不是允许丢弃其他文字。

## 6. 查询与候选

~~~kotlin
val first = engine.query("həˈloʊ")
val second = engine.query("həˈloʊ", "左侧上下文")
val third = engine.query("həˈloʊ", "左侧上下文", "右侧上下文")

val detail = engine.queryDetailed("həˈloʊ")
val withContext = engine.queryDetailed("həˈloʊ", "", "")
~~~

三个 `query` 重载都返回非 null、不可变的 `List<GraphEngine.Candidate>`，未提供的上下文按空串处理。`queryDetailed` 提供单参数和三参数形式。无匹配、禁用、空预测、不支持输入、timeout 或 error 状态的候选列表为空；宽松搜索触及截止但已有候选时返回 ok + truncated 并保留候选；需要区分原因时使用详细结果。

| 六个宿主字段 | 内容与约束 |
| --- | --- |
| `text` | 实际上屏文字，非空白、完整 Unicode、最多 1,000 个 UTF-16 单元。不能附加读音、标签、解释或省略号。 |
| `language` | `EN`、`简`、`繁`、`日`、`RU`、`AR`、`KO`；同文来源可能合并为斜线分隔标签。 |
| `rank` | 从 0 开始，越小越靠前。 |
| `kind` | 0=`KIND_CONVERSION`，2=`KIND_COMPLETION`。命中精确 IPA 键的转换仍是 CONVERSION，不是宿主原文 EXACT。 |
| `nativeScore` | 整数参考排序分，公式见下一节；不是概率，也未与其他引擎的分数尺度校准。 |
| `contextAffinity` | 当前为 0f；有限值，宿主允许范围为 [0,1]。 |

观察字段 `matchedIpa`、`frequencyScore`、`region`、`derived`、`omittedSymbols` 分别表示胜出记录的完整 IPA、量化词频、地区、派生属性及漏输的比较符号数。它们不能拼进 `text`。同一文字跨地区去重后，标签会合并，而这些观察字段对应用于评分的胜出记录。

| QueryResult 字段 | 含义 |
| --- | --- |
| `candidates` | 同样的不可变候选列表。 |
| `status` | `ok`、`unsupported_input`、`timeout`、`error`；这两种状态没有候选。宽松截止若已有结果则返回 `ok` 并标记截断。 |
| `elapsedMs` | 原生查询耗时；不包括 Java JSON 解析、候选对象构造和宿主调度。 |
| `exactFound` | 完整 IPA 键存在；地区禁用后仍可能为 true。 |
| `truncated` | 候选上限、前缀键预算、省略状态预算或宽松搜索截止导致未完整返回；部分候选仍有效。 |
| `exploredStates` | 各正向图省略搜索已处理的路径状态数之和。 |
| `matchedKeys` | 已访问候选表的接受键数。 |
| `matchedRecords` | 去重前、通过地区开关的候选记录数。 |

库返回完成后不修改候选。UI、其他引擎混排或以后模型重排需要创建新的宿主列表与 rank，不能修改库的快照。

## 7. 词频排序与补全

离线编译器使用可比较的同地区或 `mixed` 词频证据。Zipf 或每百万词频可用时，参考值为：

~~~
z = clamp(max(可用 Zipf, log10(per_million) + 3), 0, 9)
frequencyScore = floor(z * 20 + 0.5)
nativeScore = (完整匹配 ? 10000 : 显式前缀补全 ? 1000 : 5000 - omittedSymbols * 20)
              + frequencyScore
              - (派生发音 ? 5 : 0)
~~~

只取可用、有限且合法的频率值；词形存在多个兼容来源时取最大参考值，不相加各语料计数。没有可用词频时评分项为 0，原始缺失信息仍在离线资料中。新增俄阿韩当前没有接入已验证的可分发词频，参考分为 0。量化步长为 0.05 Zipf；派生罚分 5 相当于 0.25 Zipf 的参考差。单图同分按词形排序；跨图同分保持包次序及各图原排名，相同文字合并语言标签后只返回一次。

词频属于词形或词汇素，不能把这套参考分当成某条读音的真实概率。完整匹配优先，省略匹配扣除漏输罚分；显式前缀补全使用较低基分；首选质量、同音词选择仍需按产品场景评估。

启用 `prefix=true` 后，例如 `hə` 可返回精确转换和以该 IPA 开头的完整词候选；补全项的 `matchedIpa` 显示完整匹配路径，`kind=2`。前缀搜索按键预算运行，省略搜索优先扩展较少音段遗漏、较少注记遗漏的路径，并受状态预算和截止时间限制。`truncated=true` 时只对**已访问范围**排序，不能承诺所有扩展中的全局最高频前 N 项。候选数量上限本身不表示所有源记录已经展示。

本版支持漏符号，但没有任意连续 IPA 的分词拼接、替换/换序纠错、上下文语言模型或预测。已经收录为完整键的多音节词/短语可以转换；这不等于具备句子解码能力。

## 8. 映射 IPA Board 宿主字段

以下 Kotlin 例子放在宿主定义 `Candidate`、`CandidateKind` 的接入代码中，显式转换枚举：

~~~kotlin
import com.ipaengine.graph.GraphEngine

fun GraphEngine.Candidate.toHostCandidate(): Candidate {
    val hostKind = when (kind) {
        GraphEngine.KIND_CONVERSION -> CandidateKind.CONVERSION
        GraphEngine.KIND_COMPLETION -> CandidateKind.COMPLETION
        else -> throw IllegalArgumentException("Unsupported graph candidate kind")
    }
    return Candidate(
        text = text,
        language = language,
        rank = rank,
        kind = hostKind,
        nativeScore = nativeScore,
        contextAffinity = contextAffinity
    )
}

val hostCandidates: List<Candidate> =
    engine.query(raw, beforeCursor, afterCursor).map { it.toHostCandidate() }
~~~

当前宿主枚举 ordinal 合同为：0 CONVERSION、1 EXACT、2 COMPLETION、3 CORRECTION、4 PREDICTION。接入时先转换为宿主枚举，发 Messenger 回复时再取宿主 `ordinal`；不要通过“所有非零都是同一种候选”或直接套另一个枚举解释编号。非空 raw 不返回 PREDICTION；本版本空 raw 也返回空列表。

图候选的 EN 标签表示英文输出，保留宿主英文提交行为。宿主按“文字 + 是否预测”去重，合并标签含 EN 时，空格选择可能追加空格；UI 接入必须理解这个语义。组件路由则需要单独注册 IPA。若未来还返回 IPA 原文候选，应使用双方约定的 IPA 标签，不能借用 EN 或 ∑。

输入法仍负责预输入更新、候选点击、全部 raw 的替换范围、原文上屏、光标和最终文字变换。库的查询不会调用编辑器提交接口。故障、禁用或无候选时保留原文提交路径。

## 9. 工作线程、请求标识和服务回复

初始化、配置和查询在宿主工作线程进行，建议单一串行执行队列。快速输入、退格、上下文和开关更新可能使在途结果过期。库没有运行中取消接口；取消协程、收到新请求或服务解绑都不代表原生调用已经停止。

每个任务捕获**该请求自己的** `revision`、`requestId`、`replyTo`、raw 和配置快照。回复必须原样带回这两个标识，不能替换为最新请求标识；UI 协调器比较当前标识，丢弃过期回复。老查询即使完成，也不能覆盖较新的预输入。

IPA Board 当前 Messenger 合同：

| 消息 | Bundle / 行为 |
| --- | --- |
| 查询请求 `what=1` | 带 `replyTo`；`revision: Long`、`requestId: Long`、`raw: String`、`beforeCursor: String`、`afterCursor: String`、宿主定义的功能布尔开关、`debug_diagnostics_enabled: Boolean`。 |
| 查询回复 `what=1` | 原样回传 revision/requestId；`text`、`language` 为 `ArrayList<String>`，`rank`、`kind`、`score` 为 `IntArray`，`affinity` 为 `FloatArray`。同一下标是一条候选，所有长度一致；空结果使用空数组。 |
| 故障回复 | 候选为空，附宿主认可的 `error: String` 固定码，格式为 `COMPONENT/STAGE/KIND`。 |
| 诊断控制 `what=2` | 只更新诊断开关；不发起查询、不清空输入、不取消正在运行的调用。 |

可以用下面的构造器保证候选数组一致：

~~~kotlin
import android.os.Bundle

// acceptedErrorCode 必须已经过宿主现有错误码注册表映射和确认。
fun makeReply(
    revision: Long,
    requestId: Long,
    candidates: List<Candidate>,
    acceptedErrorCode: String? = null
): Bundle {
    val rows = if (acceptedErrorCode == null) candidates else emptyList()
    return Bundle().apply {
        putLong("revision", revision)
        putLong("requestId", requestId)
        putStringArrayList("text", ArrayList(rows.map { it.text }))
        putStringArrayList("language", ArrayList(rows.map { it.language }))
        putIntArray("rank", rows.map { it.rank }.toIntArray())
        putIntArray("kind", rows.map { it.kind.ordinal }.toIntArray())
        putIntArray("score", rows.map { it.nativeScore }.toIntArray())
        putFloatArray("affinity", rows.map { it.contextAffinity }.toFloatArray())
        if (acceptedErrorCode != null) putString("error", acceptedErrorCode)
    }
}
~~~

正常无匹配、地区关闭、空预测和 `unsupported_input` 是空结果，不应触发故障熔断。`timeout` 清空候选并可记诊断；如果宿主需要把它作为故障回复，使用已登记的超时代码。`error`、初始化 IOException 和意外生命周期异常由宿主捕获并映射固定码。不要把库的状态字符串、异常文字或自行拼出的 IPA 错误码直接放进 `error`；本文未提供宿主的注册表，因此不声称任何新码已获宿主认可。

宿主负责服务路由、功能开关和诊断映射；返回语言标签不能自动注册组件。地区选择映射到本库八个 Policy 布尔值，资源模式选择映射到 PackMode。原生运行建议按既有合同放入独立引擎服务进程，初始化/查询故障不会终止输入法主进程或改变原文提交。以上为宿主接入所需合同；合同原文留在本地开发项目的 `engine_android/docs/引擎开发规范.md`。本次只实现引擎 API，应用侧入口后续接入。

## 10. 在终端观察输入和输出

终端工具和合成测试随 [引擎源码](source/README.md) 公开。在远程仓库的 `source/` 内运行以下脚本，或在完整本地项目根目录运行。需要 Python 3 与 Rust 1.89+；首次调用自动构建与 Android 同源的 Rust CLI，无须原始资料库即可查询已编译词典。UI 接入仍使用前九节的 Java API。

从选定 Release 的 client.json 获取词典和 manifest，校验大小与 SHA-256，
放入 `data/dictionary/compiled/lexicon.ipad` 和 `lexicon.manifest.json`。
公开源码不包含运行时词典；合成回归测试无需下载词典。

~~~sh
python3 cli_tests/scripts/engine_cli.py query 'həˈloʊ' --region en-US
python3 cli_tests/scripts/engine_cli.py query 'tu' --region en-US --json
python3 cli_tests/scripts/engine_cli.py query 'ni˨˩˦ xɑʊ̯˨˩˦' --region zh-CN
python3 cli_tests/scripts/engine_cli.py query 'ɡakːoɯ' --region ja-JP
python3 cli_tests/scripts/engine_cli.py query 'maɪ'
python3 cli_tests/scripts/engine_cli.py query 'nixɑʊ'
python3 cli_tests/scripts/engine_cli.py query 'ni35 xɑʊ̯21'
python3 cli_tests/scripts/engine_cli.py query 'maɪ' --strict
python3 cli_tests/scripts/engine_cli.py query 'hə' --prefix --prefix-budget 2048 --limit 10
python3 cli_tests/scripts/engine_cli.py repl
python3 cli_tests/scripts/engine_cli.py info
python3 cli_tests/scripts/engine_cli.py cases cli_tests/cases/graph_cases.jsonl --verbose
python3 cli_tests/scripts/engine_cli.py test
python3 cli_tests/scripts/engine_cli.py reverse 'hello' --region en-US
python3 cli_tests/scripts/engine_cli.py reverse '学校' --json
python3 cli_tests/scripts/engine_cli.py packs --json
python3 cli_tests/scripts/engine_cli.py reverse '한국' --json
python3 cli_tests/scripts/engine_cli.py repl --no-default-packs --pack-mode core:input --pack-mode ru:reverse
~~~

可重复 `--region` 开启多个地区；不指定则全部开启。全局 `--dictionary PATH`、`--cli PATH` 放在子命令前。文本输出显示匹配、耗时、扫描量、截断和每条候选的 IPA、地区、词频、类型、派生属性；`--json` 用于程序读取。原生 Rust/C 入口要求调用方提供 NFC，Python 包装负责规范化。

交互模式 `repl` 支持以下输入：

| 输入 | 行为 |
| --- | --- |
| `test` | 从 JSONL 用例文件随机选一条，展示完整输入、配置、预期、实际候选与 PASS/FAIL。使用该用例自己的地区/补全/上限配置。 |
| `hello`、`学校` 等词汇 | 没有正向 IPA 命中时，反查词典中实际收录的 IPA，附地区、派生标记和词频参考值。 |
| `reverse 词汇` | 强制词汇反查；用于同串也是 IPA 键或词汇本身为 `test` 等有歧义的情况。 |
| `ipa 输入` | 强制正向查询，并保留无匹配、不支持等详细诊断。 |
| `packs` | 查看包 ID、模式、加载状态与资源尺寸。 |
| `pack ru off` / `pack ru reverse` / `pack ru input` | 立即更改当前会话的俄语包模式；其他包使用对应 ID。 |

普通输入优先显示正向 IPA 命中或补全，没有完整 IPA 命中时优先反查实际存在的词汇；其他输入展示省略匹配候选；两者都没有结果时不输出内容。强制反查不存在的词汇也没有输出，JSON 模式同样不输出一行空结果。反查与 Android 使用同一个 Rust 引擎，遵守 `--region` 与语言包模式，最多 512 条，截断时明确标记，不受正向候选 limit 限制。容器格式使用预编译反查索引，旧单图兼容扫描；两者都不读取原始 SQLite、不猜测读音。

`--no-default-packs` 实现真正的惰性初始化，可重复 `--pack-mode ID:off|reverse|input` 指定模式；`--extra-dictionary PATH` 可附加未来独立交付的包容器，同样遵守默认加载选项。当前四包合并文件不需要再附加同名包。反查和 pack 切换在同一 REPL 实例执行，修改后立即生效。

随机用例默认来自 `cli_tests/cases/graph_cases.jsonl`，包含正常命中与拒绝/无结果边界；可通过 `repl --cases PATH` 或 `test --cases PATH` 改用其他用例文件。`test --json` 返回用例、实际输出和断言结果，`reverse WORD --json` 返回 `word`、`pronunciations` 和 `truncated`；相同 IPA 的不同地区记录分别展示。合成测试用例与 CLI 源码也在公开的 `source/` 中提供；从完整资料库抽样用例属于本地数据准备工作，不包含在公开源码快照。

用例为 JSONL，每行一条。例如：

~~~json
{"id":"us-homophones","raw":"tu","regions":["en-US"],"limit":180,"expected_all":["too","two"]}
{"id":"reject-suffix","raw":"həˈloʊ0","expected_status":"unsupported_input","expected_empty":true}
{"id":"prefix-budget","raw":"hə","regions":["en-US"],"prefix":true,"prefix_budget":1,"expected_truncated":true}
~~~

支持 `expected_any`、`expected_all`、`expected_none`、`expected_exact_found`、`expected_empty`、`expected_nonempty`、`expected_status`、`expected_truncated`。用 `cases PATH --output REPORT.json` 保存输入、输出、断言结果；用例中的地区、补全和候选上限均按配置执行。桌面 CLI 允许 limit=0 检查空结果；Android Options 的范围是 1–180，运行时关闭能力使用 Policy。

source 快照不含来源数据库和抽样/预处理工具。普通 query/repl/cases 只需要随 source 提供的编译词典与手写测试用例；来源观测检查属于完整本地项目工作流，不能替代音系核验或同音词首选准确率。

## 11. 已完成验证与实际边界

本次完整词典在开发机固定抽样中，限定原五地区的 1,023 个输入与加包前本地基线（3ee3135）的目标召回及目标排序一致；1 个查询在旧基线触发预算截断而多返回结果存在差异，单项复查结果一致；默认六语言开启后，前 30 目标召回由 77.61% 变为 77.52%（1 个原目标降出前 30），p95 由 6.58 ms 变为 11.13 ms。该样本含大量人工省略压力输入，不能当作真实使用准确率或手机延迟。新增三语言各 100 个完整 IPA 查询及反查全部回收目标；20% 音段省略压力样本前 30 回收率为 91.33%。新增语言尚无可靠词频时使用 0 分参考值，完整 IPA 可减少跨语言歧义。

本次 v0.0.5-dev 已在桌面合成四包上验证 CLI 查询／反查、默认加载、OFF／REVERSE_ONLY／INPUT 切换、真正惰性初始化及独立文件挂入；桌面 JVM 的真实 JNI 验证覆盖 Unicode/NFC、旧五参数配置保持新增语言、反查独立长度限制、动态分段正则与关闭保护。**这些不是 Android 实机执行或新多语言包的耗电验证。** 当前开发包没有新版 S21 Ultra 性能结论。

以下迁移与手机数字均为历史版本记录，不代表本次六语言、容器及反查索引的内存或延迟：

v0.0.3-dev 的词典校验、精确/顺序省略/补全查询、排序、C ABI、原生 CLI 和图最小化器已迁移到 Rust；Python 离线数据处理、Java 接口及 C++ JNI 的 UTF-16 转换薄层保留。24/32 位格式和 Android 类名、方法、候选字段保持兼容。迁移期间曾以旧 C++ 引擎作逐字段对照，公开源码中的旧引擎和比较程序现已移除。完整24位词典的五地区5,000条来源用例，严格与省略两种模式当时均与旧实现零差异、预期词全部保留；部分查询有预算截断。图最小化器24/32位输出逐字节一致，Unicode、损坏文件、并发、JNI及不带原始数据的独立源码构建检查通过。

开发机交替运行3轮、每轮500个相同输入，省略查询p50/p95/p99中位数：C++为1.9095/4.9526/5.3180 ms，Rust为1.4662/3.8534/4.4230 ms。严格查询p95为0.001250/0.001292 ms；加载为44.32/54.74 ms，Rust一次加载多约10 ms。测试含预热、全地区、默认30候选与20,000状态预算，不含Java/JNI或UI，也不代表S21 Ultra表现。

Rust省略搜索用路径编号排序，仅在接受键命中时重建IPA；状态由最初移植的48 B减为24 B。后缀摘要仍约每节点9字节、完整图合计约10.29 MB，另有有界搜索临时内存。词典字节未改动，三个要求的输入继续召回目标词。最终arm64-v8a AAR已交叉编译；新版实机与耗电未复测，release.json 的 validation.android_device_execution 为 false。

如果忽略候选上限和计算预算，顺序删除更多输入符号只会扩大合法匹配集合。本库每次最多返回180条且预算有界，实际可见列表不能保证单调扩大或包含所有合法词。提高 candidateLimit/omissionStateBudget 可观察更多结果；应查看 truncated，不把截断结果当成完整集合。

以下手机数据是 **v0.0.1-dev 严格查询** 的历史结果，不代表新版省略搜索的手机表现。设备为三星 S21 Ultra **SM-G998U1 / Android 15 / API 35**：

| 范围 | 输入与次数 | p50 / p95 / p99 |
| --- | --- | --- |
| 手机原生核心 | 五地区 5,000 输入，5 轮共 500,000 查询；各轮中位数 | 0.002761 / 0.006979 / 0.030781 ms |
| 完整 Java/JNI queryDetailed | 五地区交替输入，预热 1,000，测量 10,000；上限 180 | 0.046615 / 0.286407 / 1.783125 ms |
| 10 Hz 持续查询 | 180 秒、1,800 次 | 1.198073 / 3.500052 / 19.387187 ms |

上述查询没有错误或超时；完整包装和持续查询每次均有候选。Java/JNI 紧密循环最大 6.427 ms；10 Hz 持续模式最大 40.957 ms，低频唤醒条件下延迟更高。首次 `openBundled` 263.523 ms，缓存初始化 149.458 ms，包含资产和 SHA 开销。

v0.0.1-dev 的24位 AAR 有17项手机检查通过，涵盖五地区、Unicode/NFC、配置、候选与补全、关闭、资产提取、哈希、缓存重用/损坏修复、完整包装基准和短持续测试。另有 14 个人工边界用例、5,000 条来源观测、独立格式/候选参照，以及损坏文件、并发、ASan/UBSan、JNI 验证。完整报告留在本地 `data/cli_tests/reports/graph-s21-comparison.json`、`android-wrapper-probe.json` 和 `graph-cases.json`；[公开发布摘要](https://github.com/PantingEYEs/IPA_Engine/blob/707823a2aac3e848ddee0a7b66e232aa94de88e3/pre-release/v0.0.1-dev/release.json) 固定在历史提交中。

3 分钟内原生已分配堆为 7,320,048 → 7,320,096 B，整体进程 PSS 为 52,722 → 66,202 KiB，Java 已用堆为 5,011,408 → 19,171,280 B。未强制 GC；这段观察既不证明长期无泄漏，也不能单凭 Java 堆增长判定泄漏。报告的 PSS 含测试应用及映射页，并非单独词典的内存上界。

基准未覆盖输入法 UI、Messenger 调度、真实编辑器提交、15–30 分钟使用及耗电验收，未控制手机调频、GC 或全部缓存条件。默认 24 位已经确定；32 位对照资料继续保留，预发布仅提供所选 24 位包。当前尚无句子解码、替换/换序纠错或上下文模型，图关系正确不等于同音词首选永远正确。

## 12. 源码与分支

[source/](source/README.md) 含 Rust 核心、Java/JNI、构建工具、CLI 和合成测试。
不含原始数据、运行时词典、词典编译器、GRU、发布工具或完整私有开发历史。
构建需要 Python 3.10+、Rust 1.89+；Android 另需 SDK、NDK、JDK 及 ARM64 Rust target。

在 source/ 内运行：

```sh
cargo test --offline --manifest-path engine_android/ipa_graph/Cargo.toml --target-dir data/engine_android/builds/rust-test
python3 -m unittest cli_tests.tests.test_engine_cli cli_tests.tests.test_graph_omissions
```

开发源码位于 develop；main 通过 no-ff 合并更新。客户端不读取任一分支寻找版本。
当前未选定项目级软件许可证；数据许可由每版附件中的来源声明分别说明。

语言包生命周期：OFF 会保留一个共享的只读载体文件句柄，以便再次启用时仍使用同一版本；不会保留该包的图映射和搜索数组。更新可原子替换路径，旧实例仍固定旧文件，新实例读取新文件。
