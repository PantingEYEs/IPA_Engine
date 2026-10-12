# IPA Engine

这是供 Android 输入法接入的 **IPA → 中英日文字候选** 依赖库。默认使用完整 24 位压缩词典、最小 DAFSA 图、顺序省略匹配与词频排序，运行时只读映射二进制文件。它提供候选查询核心；键盘界面、预输入、选择候选、提交文字和服务通信由宿主实现。

本 README 是 UI/输入法开发者的独立接入手册。GitHub 仓库只发布本地项目的 `release/remote/` 子目录，内容在远程仓库根目录展示；`source/` 是含编译词典的精简可构建源码快照，`pre-release/` 是客户端直接消费的发布资源，两者职责不同且允许重复包含词典。原始资料、SQLite 资料库、训练权重和完整开发历史留在本地。

## 1. 取得引擎：客户端更新与 Gradle 依赖

IPA Board 固定读取 `codex/ipa-engine-v0.0.1-dev` 分支的根 [board-update.json](board-update.json)；分支名不随版本变化。客户端仅由这个入口发现更新，按清单下载配套 DEX/JNI 与裸二进制词典，不解包 AAR、不编译源词库，也不扫描 `VERSION`、GitHub Releases 或其他分支。只发布 AAR 或更新版本文件不会触发客户端升级。

当前客户端批次和资源地址见 [最新产物索引](pre-release/README.md)。历史版本提供的 AAR 可继续用于 Gradle 集成；新的发布流程只生成独立客户端资源，不再打包 AAR。客户端资源包含：

| 文件 | 用途 |
| --- | --- |
| `classes.dex` | Android DEX 格式的 GraphEngine Java 代码，由独立 Java `classes.jar` 经 D8 转换。 |
| `libipa_graph_android.so` | 与该 DEX 配套的 ARM64 JNI 和原生图核心。 |
| `lexicon.ipad` | 可直接打开的完整 24 位 IPADWG01 v2 文件，没有 ZIP/GZIP 外包装。 |
| `client.json` / `SHA256SUMS` | 批次资源字段与实际校验值；客户端发现仍以根 board-update.json 为准。 |
| `lexicon.manifest.json` / `THIRD_PARTY_NOTICES.json` | 词典编译记录、来源及逐来源许可。 |

引擎与词典版本独立，以根入口的实际值为准。v0.0.3-dev 发布时词典内容与哈希未变，词典版本保持 v0.0.1-dev-lexicon。更新入口固定二进制所在的已发布公开提交 `artifactCommit`，下载与校验细节见本文“IPA Board 独立资源更新”。最新目录只保留当前产物，旧版本从 Git 历史提交下载或回滚。

需要在宿主工程构建时集成 Java/JNI 和词典的开发者，可从历史发布提交取得对应版本的 AAR；新版本可将独立 Java/JNI/词典组件按宿主工程方式集成。预发布目录只包含发布结果，没有源码或测试 APK：

| 文件 | 用途 |
| --- | --- |
| `ipa-engine-<版本>-android-arm64-v8a.aar` | 仅历史发布提供的 Gradle 依赖，含 Java/JNI、原生核心、完整 24 位词典与编译 manifest。 |
| `release.json` | 版本、ABI、体积、哈希、数据规模和已有验证摘要。 |
| `SHA256SUMS` | 发布文件的校验值。 |
| `THIRD_PARTY_NOTICES.json` | 实际采用的数据来源与逐来源许可记录。 |

v0.0.3-dev 的 Rust AAR 约为 **29.22 MB**；词典为 **28,986,769 B（28.99 MB）**，含 1,024,289 个 IPA 键、768,387 个独立输出词形、1,281,101 条地区候选映射。当前版本的精确字节数及 SHA-256 见产物索引中的 release.json。MB 使用十进制。完整性指编译规则允许收录的关系完整保留；原始资料中的待核验记录、非词汇类别和超长记录有明确筛选规则。

消费历史 AAR 时只引入该 AAR，避免重复打包同名 Java 类或原生库。入口为 `com.ipaengine.graph.GraphEngine`，原生库名为 `ipa_graph_android`。独立客户端资源采用相同的调用接口；由宿主处理 DEX/JNI 加载、文件校验与版本切换。

## 2. Android / Gradle 要求

要求 **Android API 26+、arm64-v8a、Java 8 语言支持或更高**。原生 ELF 支持 16 KiB 页对齐。AAR 已包含 Rust 图核心与 JNI 薄层；宿主使用 Android 平台提供的 `org.json`，无需额外运行时依赖，也不需要在手机安装 Python、SQLite、PyTorch、OpenFst 或联网。

本节及下一节的 `openBundled` 示例用于 AAR 集成。将主 AAR 复制到输入法工程 `app/libs/`；下面以 v0.0.3-dev 文件名为例，实际名称取产物索引：

~~~kotlin
// app/build.gradle.kts；保留宿主原有 compileSdk 和其他配置。
android {
    defaultConfig {
        minSdk = 26
        // 此版本提供 arm64-v8a；本示例宿主仅分发这个 ABI。
        ndk { abiFilters += "arm64-v8a" }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_1_8
        targetCompatibility = JavaVersion.VERSION_1_8
    }
}
dependencies {
    implementation(files("libs/ipa-engine-v0.0.3-dev-android-arm64-v8a.aar"))
}
~~~

库清单没有权限、Service 或 Activity。AAR 内的 consumer ProGuard 规则保留 JNI 类和方法名。只消费 AAR 时不需要安装 NDK；从源码构建才需要。未提供的 ABI 不能加载原生库，初始化会交给宿主作为故障处理。

## 3. 初始化、复用和关闭

在工作线程初始化一次，然后复用同一实例：

~~~kotlin
import android.content.Context
import com.ipaengine.graph.GraphEngine
import java.io.IOException
import java.util.concurrent.Executor

// ready / failed 都在传入的工作线程执行；需要更新 UI 时由宿主切回主线程。
fun openEngine(
    context: Context,
    worker: Executor,
    ready: (GraphEngine) -> Unit,
    failed: (IOException) -> Unit
) {
    worker.execute {
        val engine = try {
            GraphEngine.openBundled(context.applicationContext ?: context)
        } catch (failure: IOException) {
            failed(failure)
            return@execute
        }
        ready(engine)
    }
}
~~~

`openBundled(context)` 从 AAR 资产读取 `ipa/lexicon.manifest.json` 与 `ipa/lexicon.ipad`，检查完整标记、字节数和 SHA-256，复制到：

~~~
context.noBackupFilesDir/ipa-graph/lexicon-<sha256>.ipad
~~~

复制经过临时文件和原子替换，随后只读映射；已有相同哈希缓存会复用，但每次初始化仍核验缓存。损坏缓存会从资产重新提取。初始化包含文件读取与哈希成本，应避免逐键重新创建实例。安装后的 APK 资产和私有映射缓存各保存一份编译文件，存储预算需同时考虑这两份；查询不会展开整个词形池。

清单或资产缺失、哈希错误、格式错误、原生库不可用等初始化失败抛出 `IOException`。宿主应报告自己登记的初始化错误码并保留原文输入路径。异常文字不能作为候选或直接作为 Messenger 错误码。

也可用 `new GraphEngine(dictionaryAbsolutePath)` 打开宿主准备好的独立编译文件。客户端更新批次没有 AAR 资产，宿主在加载配套 DEX/JNI 并校验下载的词典后使用这个入口，不调用 `openBundled`。该入口验证二进制格式；文件来源、实际大小和 SHA-256 核验由调用方负责。AAR 集成默认已带词典；两种接入均无需在手机编译资料库。

`GraphEngine` 实现 `AutoCloseable`。实例方法和 `close()` 同步保护句柄，`close()` 可重复调用；关闭后的查询、配置、判定和属性访问会抛出 `IllegalStateException`。宿主仍应在同一工作队列中安排生命周期：停止接收请求、等待已提交工作结束，再关闭；不要在 UI 线程并发关闭或靠关闭句柄取消查询。服务解绑不等于实例已销毁。

映射期间保持对应文件不可变。新词典使用新的哈希文件名；确认旧实例不再使用后才能清理旧文件。库不持有编辑器、剪贴板或输入法 UI。

## 4. 地区和查询选项

`Policy`、`Options` 都是不可变配置快照：

~~~kotlin
// 顺序：en-US、en-GB、zh-CN、zh-TW、ja-JP。
engine.configure(
    GraphEngine.Policy(true, true, true, true, true),
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

`new GraphEngine.Policy()` 开启全部地区。关闭某地区后不产生该地区候选；全部关闭则返回空列表。派生发音随词典加载，目前没有单独禁用派生层的运行开关。

| Options 字段 | 范围 | 默认及含义 |
| --- | --- | --- |
| `candidateLimit` | 1–180 | 30；去重后的输出上限。 |
| `prefix` | Boolean | false；显式开启 IPA 前缀补全。 |
| `deadlineMs` | 1–60 | 60；原生查询预算。省略搜索已有结果时保留候选并标记截断，否则超时为空。 |
| `prefixKeyBudget` | 1–65,536 | 2,048；显式前缀补全最多访问的接受键数。 |
| `allowOmissions` | Boolean | true；允许漏输词典符号，仍消费全部输入。 |
| `omissionStateBudget` | 1–200,000 | 20,000；省略搜索最多创建的路径状态数。 |

六参数构造器 `Options(30, false, 60, 2048, true, 20000)` 可控制省略匹配。对照严格模式使用 `Options(30, false, 60, 2048, false, 20000)`；数字调值转换在两种模式下都生效。四参数构造器与 `Options()` 默认开启省略。

越界选项抛出 `IllegalArgumentException`。`configure(policy)` 只更新地区，保留当前 Options；`configure(policy, options)` 同时更新二者。`getPolicy()`、`getOptions()` 返回当前快照。查询预算与诊断耗时针对原生核心，不是整个 Java 调用或 UI 调度的硬性墙钟上限。

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

`segmentPattern()` 与 `getSegmentPattern()` 返回由词典字符表生成的 Java 正则，使用 Unicode 码点字符类与 `+` 匹配非空字符段。宿主用 `Pattern.compile(engine.segmentPattern())` 或 Kotlin `Regex` 编译，并覆盖原先只识别拉丁字母/撇号的分段规则。IPA 音段、组合符号、重音、调值和内部界限应作为完整段处理，不能把有效 IPA 拆断。

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
| `language` | `EN`、`简`、`繁`、`日`；同文来源可能合并为斜线分隔标签。 |
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
| `exploredStates` | 本次省略搜索已处理的路径状态数。 |
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

只取可用、有限且合法的频率值；词形存在多个兼容来源时取最大参考值，不相加各语料计数。没有可用词频时评分项为 0，原始缺失信息仍在离线资料中。量化步长为 0.05 Zipf；派生罚分 5 相当于 0.25 Zipf 的参考差。同分按词形确定排序。

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

宿主目前的服务路由、功能开关和诊断只识别 ZH/JA/EN，须增加 IPA 路由及对应映射；返回语言标签不能自动注册组件。地区选择映射到本库五个 Policy 布尔值。原生运行建议按既有合同放入独立引擎服务进程，初始化/查询故障不会终止输入法主进程或改变原文提交。以上为宿主接入所需合同；合同原文留在本地开发项目的 `engine_android/docs/引擎开发规范.md`。

## 10. 在终端观察输入和输出

终端工具和合成测试随 [引擎源码](source/README.md) 公开。在远程仓库的 `source/` 内运行以下脚本，或在完整本地项目根目录运行。需要 Python 3 与 Rust 1.89+；首次调用自动构建与 Android 同源的 Rust CLI，无须原始资料库即可查询已编译词典。UI 接入仍使用前九节的 Java API。

在完整本地开发项目中、尚无编译词典时，可在项目根从现有客户端批次复制词典和 manifest。已有 `data/dictionary/compiled/` 产物时直接使用下方查询命令；提取代码拒绝覆盖已有文件：

~~~sh
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile
import shutil
output = Path("data/dictionary/compiled")
output.mkdir(parents=True, exist_ok=True)
names = ("lexicon.ipad", "lexicon.manifest.json")
if any((output / name).exists() for name in names):
    raise SystemExit("已有本地词典或 manifest，请直接查询或另选提取目录")
import json
public = Path("release/remote") if Path("release/remote/VERSION").is_file() else Path(".")
update = json.loads((public / "board-update.json").read_text(encoding="utf-8"))
batch_dir = public / Path(update["dictionaryPath"]).parent
for name in names:
    shutil.copyfile(batch_dir / name, output / name)
PY
~~~

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
~~~

可重复 `--region` 开启多个地区；不指定则全部开启。全局 `--dictionary PATH`、`--cli PATH` 放在子命令前。文本输出显示匹配、耗时、扫描量、截断和每条候选的 IPA、地区、词频、类型、派生属性；`--json` 用于程序读取。原生 Rust/C 入口要求调用方提供 NFC，Python 包装负责规范化。

交互模式 `repl` 支持以下输入：

| 输入 | 行为 |
| --- | --- |
| `test` | 从 JSONL 用例文件随机选一条，展示完整输入、配置、预期、实际候选与 PASS/FAIL。使用该用例自己的地区/补全/上限配置。 |
| `hello`、`学校` 等词汇 | 没有正向 IPA 命中时，反查词典中实际收录的 IPA，附地区、派生标记和词频参考值。 |
| `reverse 词汇` | 强制词汇反查；用于同串也是 IPA 键或词汇本身为 `test` 等有歧义的情况。 |
| `ipa 输入` | 强制正向查询，并保留无匹配、不支持等详细诊断。 |

普通输入优先显示正向 IPA 命中或补全，没有完整 IPA 命中时优先反查实际存在的词汇；其他输入展示省略匹配候选；两者都没有结果时不输出内容。强制反查不存在的词汇也没有输出，JSON 模式同样不输出一行空结果。反查按 NFC 后的完整词形精确匹配，不自动改大小写或转换简繁；返回全部已收录读音，遵守 `--region`，不受正向候选 limit 限制。反查直接读取所选 `.ipad` 的词形池和候选映射，不使用原始 SQLite、不猜测读音。找到词形后需要扫描候选映射，速度可能慢于正向查询；该功能只用于桌面 CLI。

随机用例默认来自 `cli_tests/cases/graph_cases.jsonl`，包含正常命中与拒绝/无结果边界；可通过 `repl --cases PATH` 或 `test --cases PATH` 改用其他用例文件。`test --json` 返回用例、实际输出和断言结果，`reverse WORD --json` 返回 `word` 与 `pronunciations`；相同 IPA 的不同地区记录分别展示。合成测试用例与 CLI 源码也在公开的 `source/` 中提供；从完整资料库抽样用例属于本地数据准备工作，不包含在公开源码快照。

用例为 JSONL，每行一条。例如：

~~~json
{"id":"us-homophones","raw":"tu","regions":["en-US"],"limit":180,"expected_all":["too","two"]}
{"id":"reject-suffix","raw":"həˈloʊ0","expected_status":"unsupported_input","expected_empty":true}
{"id":"prefix-budget","raw":"hə","regions":["en-US"],"prefix":true,"prefix_budget":1,"expected_truncated":true}
~~~

支持 `expected_any`、`expected_all`、`expected_none`、`expected_exact_found`、`expected_empty`、`expected_nonempty`、`expected_status`、`expected_truncated`。用 `cases PATH --output REPORT.json` 保存输入、输出、断言结果；用例中的地区、补全和候选上限均按配置执行。桌面 CLI 允许 limit=0 检查空结果；Android Options 的范围是 1–180，运行时关闭能力使用 Policy。

source 快照不含来源数据库和抽样/预处理工具。普通 query/repl/cases 只需要随 source 提供的编译词典与手写测试用例；来源观测检查属于完整本地项目工作流，不能替代音系核验或同音词首选准确率。

## 11. 已完成验证与实际边界

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

## 12. 本地开发目录、重建与训练工具

下表是完整本地开发项目的结构，不是 GitHub 发布仓库的文件清单。所有开发命令从本地项目根目录执行。

| 目录/文件 | 职责 |
| --- | --- |
| `engine_android/ipa_graph/` | Rust 图查询核心、C ABI、原生 CLI 与 DAFSA 最小化器。 |
| `cli_tests/scripts/compiled_lexicon.py` | 桌面 CLI 的编译词典反查读取器。 |
| `cli_tests/tests/fixtures/` | CLI、JNI 和发布测试使用的小型独立词典生成器。 |
| `engine_android/android/` | Java/JNI 包装、CMake 与 Android/JNI 验证工具。 |
| `gru/` | GRU 训练、量化、导出、评估及独立参数推理运行时。 |
| `data_pipeline/scripts/collection/` | 数据来源采集、下载与采集专用依赖。 |
| `data_pipeline/scripts/preprocessing/` | 清洗、规范化、词频处理、索引和审查工具。 |
| `dictionary/` | 图词典编译、位宽转换、格式说明及编译测试。 |
| `cli_tests/scripts/` | 桌面 CLI 和编译词典反查读取器。 |
| `gru/scripts/` | GRU 语料准备、原生 parity 与基准工具。 |
| `engine_android/scripts/` | Rust 引擎和 Android 原生组件构建工具。 |
| `release/scripts/release/` | 客户端批次打包、公开源码同步和发布。 |
| `cli_tests/tests/` | 主机 CLI 和最终引擎测试。 |
| `cli_tests/cases/` | 手工维护的终端 JSONL 用例。 |
| `data_pipeline/catalog/` | 来源、许可、原始快照 manifest。 |
| `data/` | 完整本地项目的数据工作区；公开 `source/data/` 仅含运行所需的编译后词典及 manifest，原始/规范化资料、SQLite、权重和构建报告不随 source 发布。 |
| `engine_android/docs/` | 引擎接入接口与运行时设计说明。 |
| `release/docs/` | 发布工作流说明。 |
| `release/remote/source/` | 自动同步的公开引擎源码、构建工具和合成测试，不含原始资料和训练权重。 |
| `release/remote/pre-release/` | 当前版本元数据和客户端批次；旧产物保存在 Git 历史中，不含源代码/测试 APK。 |
| `release/remote/board-update.json` | IPA Board 唯一更新入口，指向已发布公开提交的分离资源。 |
| `.tools/` | 被 Git 忽略的本地编译器、训练环境与可重建工具缓存。 |
| `release/remote/VERSION` | 当前发布版本，由发布脚本更新。 |

离线编译读取已有 SQLite，只在开发机运行。默认完整 24 位产物超出预算会失败，不会静默删词。完整本地项目可重新编译词典并生成独立 Android 组件：

~~~sh
python3 dictionary/scripts/compile_graph_dictionary.py --index-bits 24
rustup target add aarch64-linux-android
python3 engine_android/scripts/build_android_library.py --output-dir data/engine_android/builds/24-v0.0.3-dev
~~~

图编译需要 Python 3 与 Rust 1.89+；Android 构建还需要 JDK、Android SDK、NDK 和 Rust `aarch64-linux-android` 目标标准库。JNI 的薄层使用 NDK C++ 编译器。本项目已有构建使用 NDK r30；构建脚本可用 `--ndk /path/to/android-ndk-r30`、`--sdk /path/to/Android/sdk` 显式定位。构建输出独立 `classes.jar`、`libipa_graph_android.so`、设备 CLI、词典和 manifest，不打包 AAR。

准备好并验证独立组件后，在完整本地项目运行一次发布命令：

~~~sh
python3 release/scripts/release/publish_release.py \
  --classes-jar data/engine_android/builds/24-v0.0.3-dev/classes.jar \
  --native data/engine_android/builds/24-v0.0.3-dev/libipa_graph_android.so \
  --dictionary data/engine_android/builds/24-v0.0.3-dev/lexicon.ipad \
  --dictionary-manifest data/engine_android/builds/24-v0.0.3-dev/lexicon.manifest.json \
  --build-report data/engine_android/builds/24-v0.0.3-dev/build-report.json \
  --version v0.0.3-dev \
  --message 'Release v0.0.3-dev'
~~~

脚本自动同步公开源码并生成默认 `{version}-client.1` 批次，清理其他旧产物目录，更新当前版本、索引和 `board-update.json`，校验并推送 `release/remote/` 的公开内容。源码等非 `release/remote/` 改动须在调用前自行提交。需要另发批次时增加 `--batch`；Git 默认读取 macOS 系统代理，也可用 `--git-proxy` 指定。维护约定见 [CONTRIBUTING](CONTRIBUTING.md)。

`release/remote/pre-release/` 结果纳入本地与远程版本管理；大型 `data/` 数据、权重/词典缓存和 `.tools/` 运行环境被忽略，按阶段归档的 JSON 报告保留。来源许可按 `THIRD_PARTY_NOTICES.json` 和目录逐条使用，混合数据没有统一替代许可证。

通用 GRU 训练工具、可复用语料和参数推理库继续保留。旧权重未证明图候选首选排序有稳定收益，已删除；当前 Android 图引擎继续按词频排序，不附带预训练模型。新模型可按以下命令训练和评估，权重路径表示新训练的输出：

~~~sh
python3 -m venv .tools/train
.tools/train/bin/pip install -r gru/requirements.txt
.tools/train/bin/python gru/scripts/prepare_model_data.py --max-per-region 6000
.tools/train/bin/python -m gru.ipa_model.train --epochs 20
.tools/train/bin/python -m gru.ipa_model.evaluate data/gru/runs/best.pt --quantized
~~~

未来模型可对已经生成的合法图候选打分、校准词频组合并重排；需要在独立测试集验证首选改善和手机成本。模型词表缺字不应删除合法字典候选。训练、导出和运行库用法留在本地 `gru/README.md`、`gru/model_runtime/README.md`；图设计说明见本地 `engine_android/docs/图词典引擎.md` 和 `dictionary/docs/图结构选型.md`；词典编译说明见 `dictionary/README.md`。

## 13. 发布范围与分支约定

远程仓库根目录包含 `README.md`、`VERSION`、`CONTRIBUTING.md`、`board-update.json`、`source/` 和 `pre-release/`。这些文件在本地位于 `release/remote/`；`source/` 按逐文件白名单同步引擎源码及构建、测试工具。原始下载、资料库、训练样本与权重不进入公开树或公开历史。发布包及来源许可记录的相对路径在两种布局中一致。

客户端固定读取的开发分支为 `codex/ipa-engine-v0.0.1-dev`，分支名不随版本变化。发布脚本只推送本地 `codex/remote-v0.0.1-dev` 的公开历史到 `public/codex/ipa-engine-v0.0.1-dev`；当前导出树及所有新增祖先提交须符合现行白名单，已发布历史按原记录保留。禁止推送完整项目分支或强推。开发发布不创建或更新 `main`，未来向远程 main 合并采用 `git merge --no-ff` 或 GitHub **Create a merge commit** 保留记录；不使用 fast-forward、squash 或 rebase。维护规则见 [CONTRIBUTING](CONTRIBUTING.md)，完整本地项目另有 `release/docs/远程发布流程.md`。

## IPA Board 独立资源更新

客户端更新使用同版 AAR 拆分的配套 DEX/JNI 与裸词典。当前批次与版本见 [最新产物索引](pre-release/README.md)，旧资源可从各自固定的 Git 历史提交取得。产物打包与发布不表示新增手机性能验证。

客户端读取 [board-update.json](board-update.json)，该入口必须是 UTF-8 JSON 对象，无注释，大小不超过 1,048,576 字节。字段合同如下，客户端无需自行推断缺失值：

| 必需字段 | 值与范围 |
| --- | --- |
| `schemaVersion` / `entryPoint` | 整数 `1` / `com.ipaengine.graph.GraphEngine`。 |
| `abi` / `minSdk` | `arm64-v8a` / 实际最低 API，整数 26–36，当前为 26。 |
| `engineVersion` / `dictionaryVersion` | 独立版本字符串，各 1–100 字符；以入口实际值为准，词典字节未变时保持原词典版本。 |
| `dictionaryFormat` / `dictionaryKeys` | 整数 `2`（IPADWG01 v2）/ 实际 IPA 编码数，整数 1–2,000,000；当前词典为 1,024,289。 |
| `dexPath` / `nativePath` / `dictionaryPath` | 各资源以远程仓库根为基准的相对路径；只用字母、数字、`_`、`-`、`.`、`/`，禁止绝对路径、外部 URL、空路径段及 `..`。 |
| `dexBytes` / `nativeBytes` / `dictionaryBytes` | 真实正整数字节数，上限依次为 4,000,000 / 8,000,000 / 40,000,000。 |
| `dexSha256` / `nativeSha256` / `dictionarySha256` | 各文件真实 SHA-256，64 位小写十六进制。 |
| `artifactCommit` | 已发布公开产物提交，40 位小写十六进制 SHA；该提交必须包含上述路径的全部资源。 |

下载路径采用 `https://raw.githubusercontent.com/PantingEYEs/IPA_Engine/<artifactCommit>/<资源Path>`，其中两个占位项取清单的实际值。入口读取固定开发分支，但二进制读取固定提交，避免两次下载期间分支变化造成 DEX/JNI 混用；不依赖 GitHub REST API。资源文件的实际大小和 SHA-256 必须全部匹配清单。`lexicon.ipad` 已是开发侧编译好的压缩词典，客户端不再解压 ZIP/GZIP 或编译原始词库。

宿主先下载到临时位置并验证资源，再一起启用匹配的 DEX/JNI，加载新词典并调用现有 GraphEngine 接口。独立资源通过 `GraphEngine(dictionaryAbsolutePath)` 打开；它们没有 bundled 资产。宿主处理动态代码加载、工作线程、校验、切换及旧文件生命周期；更新失败继续使用旧资源，不把不完整批次设为当前引擎。输入、候选和服务回复合同仍采用本文前述接口。

一次 `release/scripts/release/publish_release.py` 调用自动完成内部两阶段发布：先推送完整产物，取得真实公开提交 SHA，再将它写入 `artifactCommit` 并发布根入口。该值必须指向包含全部资源的已发布公开提交；批次 `client.json` 仅记录资源字段，客户端唯一更新入口始终是根 `board-update.json`。

引擎与词典可独立更新。词典单独更新时保持引擎字段不变，更新词典字段与 `artifactCommit`；新的产物提交仍必须包含清单引用的旧引擎文件。新词典必须兼容已安装旧引擎，客户端在切换前验证。引擎更新必须提供配套 DEX/JNI，保持调用接口及候选合同，并兼容已安装词典；不能把新版 Java 类与旧 JNI 交叉组合。
