# IPA Engine v0.0.1-dev

这是供 Android 输入法接入的 **IPA → 中英日文字候选** 依赖库。默认使用完整 24 位压缩词典、最小 DAFSA 图与词频排序，运行时只读映射二进制文件。它提供候选查询核心；键盘界面、预输入、选择候选、提交文字和服务通信由宿主实现。

本 README 是 UI/输入法开发者的独立接入手册。GitHub 仓库只发布本地项目的 `remote/` 子目录，内容在远程仓库根目录展示；源码、原始资料、训练工具和完整开发历史由本地仓库管理。资料收集与早期实验记录留在本地 `docs/数据资料库.md`。

## 1. 取得依赖包

预发布目录只包含结果，没有源码或测试 APK：

| 文件 | 用途 |
| --- | --- |
| [ipa-engine-v0.0.1-dev-android-arm64-v8a.aar](pre-release/v0.0.1-dev/ipa-engine-v0.0.1-dev-android-arm64-v8a.aar) | 主依赖包，含 Java/JNI、原生核心、完整 24 位词典与编译 manifest。 |
| [release.json](pre-release/v0.0.1-dev/release.json) | 版本、ABI、体积、哈希、数据规模和已有验证摘要。 |
| [SHA256SUMS](pre-release/v0.0.1-dev/SHA256SUMS) | 发布文件的校验值。 |
| [THIRD_PARTY_NOTICES.json](pre-release/v0.0.1-dev/THIRD_PARTY_NOTICES.json) | 实际采用的数据来源与逐来源许可记录。 |

本版 AAR 为 **29,239,937 B**；词典为 **28,986,769 B（28.99 MB）**，含 1,024,289 个 IPA 键、768,387 个独立输出词形、1,281,101 条地区候选映射。MB 使用十进制。完整性指编译规则允许收录的关系完整保留；原始资料中的待核验记录、非词汇类别和超长记录有明确筛选规则。

只引入这个主 AAR，避免重复打包同名 Java 类或原生库。入口为 `com.ipaengine.graph.GraphEngine`，原生库名为 `ipa_graph_android`。

## 2. Android / Gradle 要求

要求 **Android API 26+、arm64-v8a、Java 8 语言支持或更高**。原生 ELF 支持 16 KiB 页对齐。AAR 已包含 C++ 运行代码；宿主使用 Android 平台提供的 `org.json`，无需额外运行时依赖，也不需要在手机安装 Python、SQLite、PyTorch、OpenFst 或联网。

将主 AAR 复制到输入法工程 `app/libs/`：

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
    implementation(files("libs/ipa-engine-v0.0.1-dev-android-arm64-v8a.aar"))
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

也可用 `new GraphEngine(dictionaryAbsolutePath)` 打开宿主准备好的独立编译文件。该入口验证二进制格式；文件来源和对应 manifest 的 SHA-256 核验由调用方负责。默认完整包已带词典，普通 UI 接入无需自行编译资料库。

`GraphEngine` 实现 `AutoCloseable`。实例方法和 `close()` 同步保护句柄，`close()` 可重复调用；关闭后的查询、配置、判定和属性访问会抛出 `IllegalStateException`。宿主仍应在同一工作队列中安排生命周期：停止接收请求、等待已提交工作结束，再关闭；不要在 UI 线程并发关闭或靠关闭句柄取消查询。服务解绑不等于实例已销毁。

映射期间保持对应文件不可变。新词典使用新的哈希文件名；确认旧实例不再使用后才能清理旧文件。库不持有编辑器、剪贴板或输入法 UI。

## 4. 地区和查询选项

`Policy`、`Options` 都是不可变配置快照：

~~~kotlin
// 顺序：en-US、en-GB、zh-CN、zh-TW、ja-JP。
engine.configure(
    GraphEngine.Policy(true, true, true, true, true),
    // 顺序：candidateLimit、prefix、deadlineMs、prefixKeyBudget。
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
| `deadlineMs` | 1–60 | 60；原生查询预算，超时返回空候选。 |
| `prefixKeyBudget` | 1–65,536 | 2,048；补全最多访问的接受键数。 |

越界选项抛出 `IllegalArgumentException`。`configure(policy)` 只更新地区，保留当前 Options；`configure(policy, options)` 同时更新二者。`getPolicy()`、`getOptions()` 返回当前快照。查询预算与诊断耗时针对原生核心，不是整个 Java 调用或 UI 调度的硬性墙钟上限。

## 5. 输入、判定与分段

每次 `raw` 是**当前完整待处理预输入**，不是新增的字符。已输入后退格、改地区或改上下文时，仍重新传完整当前串。

| 输入 | 规则 |
| --- | --- |
| `raw` | Java/Kotlin UTF-16 长度最多 64；原串先校验长度和代理配对，再做 NFC，规范化后也不得超长。 |
| `beforeCursor` / `afterCursor` | 按宿主合同各最多 256 个 UTF-16 单元；可为空，当前不参与排序。Java 包装把 null 上下文视为空串，长度限制应由宿主保证。 |
| 空 raw | 返回空候选，详细状态为 `ok`；本版没有预测功能。 |
| null raw、孤立代理、超长或未知字符 | 返回空候选，详细状态为 `unsupported_input`。 |
| 字符受支持但完整路径无匹配 | 返回空候选，详细状态为 `ok`。 |

`acceptsInput(raw)` 使用相同的 UTF-16/NFC 规则及词典字符表。它表示能处理整串，不保证有词典命中；关闭所有地区也不会使字符判定变成命中判定。空串和纯空白不接受。

`segmentPattern()` 与 `getSegmentPattern()` 返回由词典字符表生成的 Java 正则，使用 Unicode 码点字符类与 `+` 匹配非空字符段。宿主用 `Pattern.compile(engine.segmentPattern())` 或 Kotlin `Regex` 编译，并覆盖原先只识别拉丁字母/撇号的分段规则。IPA 音段、组合符号、重音、调值和内部界限应作为完整段处理，不能把有效 IPA 拆断。

精确查询保留声调、重音、长音、音节点与内部空格，仅做 NFC。例如：

| raw / 地区 | 已收录的输出示例 |
| --- | --- |
| `həˈloʊ` / en-US | hello |
| `həˈləʊ` / en-GB | hello |
| `tu` / en-US | too、two，按词频排序 |
| `ni˨˩˦ xɑʊ̯˨˩˦` / zh-CN | 你好 |
| `tʰaɪ˧˥.wan˥˥` / zh-TW | 臺灣 |
| `ɡakːoɯ` / ja-JP | 学校 |

`tu` 与 `tuː` 是不同完整键；删除声调、重音或空格也会得到另一个键，不能承诺仍命中。日语例子的 `ɡ` 是 IPA 字符 U+0261，不会在 Java 入口把普通拉丁 `g` 自动替换成它。数字调号、拼音、注音和假名没有自动转 IPA 输入方案。

非空候选替换**本次查询的全部 raw**。查询 `həˈloʊ123` 不会只转换前面再丢掉 `123`；查询字符合法的 `həˈloʊzzz` 也不会偷偷返回 hello。如果宿主先分段，外部未查询文字由宿主保留，候选仅替换该段；分段不是允许丢弃其他文字。

## 6. 查询与候选

~~~kotlin
val first = engine.query("həˈloʊ")
val second = engine.query("həˈloʊ", "左侧上下文")
val third = engine.query("həˈloʊ", "左侧上下文", "右侧上下文")

val detail = engine.queryDetailed("həˈloʊ")
val withContext = engine.queryDetailed("həˈloʊ", "", "")
~~~

三个 `query` 重载都返回非 null、不可变的 `List<GraphEngine.Candidate>`，未提供的上下文按空串处理。`queryDetailed` 提供单参数和三参数形式。无匹配、禁用、空预测、不支持输入、超时或错误的候选列表均为空；需要区分原因时使用详细结果。

| 六个宿主字段 | 内容与约束 |
| --- | --- |
| `text` | 实际上屏文字，非空白、完整 Unicode、最多 1,000 个 UTF-16 单元。不能附加读音、标签、解释或省略号。 |
| `language` | `EN`、`简`、`繁`、`日`；同文来源可能合并为斜线分隔标签。 |
| `rank` | 从 0 开始，越小越靠前。 |
| `kind` | 0=`KIND_CONVERSION`，2=`KIND_COMPLETION`。命中精确 IPA 键的转换仍是 CONVERSION，不是宿主原文 EXACT。 |
| `nativeScore` | 整数参考排序分，公式见下一节；不是概率，也未与其他引擎的分数尺度校准。 |
| `contextAffinity` | 当前为 0f；有限值，宿主允许范围为 [0,1]。 |

观察字段 `matchedIpa`、`frequencyScore`、`region`、`derived` 分别表示胜出记录的完整 IPA、量化词频、地区与派生属性。它们不能拼进 `text`。同一文字跨地区去重后，标签会合并，而这四个观察字段对应用于评分的胜出记录。

| QueryResult 字段 | 含义 |
| --- | --- |
| `candidates` | 同样的不可变候选列表。 |
| `status` | `ok`、`unsupported_input`、`timeout`、`error`；后两者清空候选。 |
| `elapsedMs` | 原生查询耗时；不包括 Java JSON 解析、候选对象构造和宿主调度。 |
| `exactFound` | 完整 IPA 键存在；地区禁用后仍可能为 true。 |
| `truncated` | 补全搜索因键预算停止；与超时不同。 |
| `matchedKeys` | 已访问候选表的接受键数。 |
| `matchedRecords` | 去重前、通过地区开关的候选记录数。 |

库返回完成后不修改候选。UI、其他引擎混排或以后模型重排需要创建新的宿主列表与 rank，不能修改库的快照。

## 7. 词频排序与补全

离线编译器使用可比较的同地区或 `mixed` 词频证据。Zipf 或每百万词频可用时，参考值为：

~~~
z = clamp(max(可用 Zipf, log10(per_million) + 3), 0, 9)
frequencyScore = floor(z * 20 + 0.5)
nativeScore = (精确转换 ? 10000 : 5000)
              + frequencyScore
              - (派生发音 ? 5 : 0)
~~~

只取可用、有限且合法的频率值；词形存在多个兼容来源时取最大参考值，不相加各语料计数。没有可用词频时评分项为 0，原始缺失信息仍在离线资料中。量化步长为 0.05 Zipf；派生罚分 5 相当于 0.25 Zipf 的参考差。同分按词形确定排序。

词频属于词形或词汇素，不能把这套参考分当成某条读音的真实概率。精确结果优先于补全；首选质量、同音词选择仍需按产品场景评估。

启用 `prefix=true` 后，例如 `hə` 可返回精确转换和以该 IPA 开头的完整词候选；补全项的 `matchedIpa` 显示完整匹配路径，`kind=2`。搜索按键预算有界运行，`truncated=true` 时只对**已访问范围**排序，不能承诺所有扩展中的全局最高频前 N 项。候选数量上限本身不表示所有源记录已经展示。

本版没有任意连续 IPA 的分词拼接、模糊发音纠错、上下文语言模型或预测。已经收录为完整键的多音节词/短语可以转换；这不等于具备句子解码能力。

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

宿主目前的服务路由、功能开关和诊断只识别 ZH/JA/EN，须增加 IPA 路由及对应映射；返回语言标签不能自动注册组件。地区选择映射到本库五个 Policy 布尔值。原生运行建议按既有合同放入独立引擎服务进程，初始化/查询故障不会终止输入法主进程或改变原文提交。以上为宿主接入所需合同；合同原文留在本地开发项目的 `docs/引擎开发规范.md`。

## 10. 在终端观察输入和输出

本节命令供持有完整本地开发项目的维护者使用；GitHub 发布仓库和 AAR 不提供源码、终端工具或测试用例。UI 接入直接使用前九节的 Java API。终端工具位于本地项目的 `scripts/`。需要 Python 3 与 C++17 编译器；首次调用自动构建桌面 CLI，查询使用与 Android 同源的原生核心。无须加载巨型资料库来查询已有编译包。

在完整本地开发项目中、尚无编译词典时，可在项目根抽出发布 AAR 自带的词典和 manifest。已有 `data/compiled/` 产物时直接使用下方查询命令；提取代码拒绝覆盖已有文件：

~~~sh
python3 - <<'PY'
from pathlib import Path
from zipfile import ZipFile
import shutil
output = Path("data/compiled")
output.mkdir(parents=True, exist_ok=True)
names = ("lexicon.ipad", "lexicon.manifest.json")
if any((output / name).exists() for name in names):
    raise SystemExit("已有本地词典或 manifest，请直接查询或另选提取目录")
aar = Path("remote/pre-release/v0.0.1-dev/ipa-engine-v0.0.1-dev-android-arm64-v8a.aar")
with ZipFile(aar) as package:
    for name in names:
        with package.open("assets/ipa/" + name) as source, (output / name).open("xb") as target:
            shutil.copyfileobj(source, target)
PY
~~~

~~~sh
python3 scripts/engine_cli.py query 'həˈloʊ' --region en-US
python3 scripts/engine_cli.py query 'tu' --region en-US --json
python3 scripts/engine_cli.py query 'ni˨˩˦ xɑʊ̯˨˩˦' --region zh-CN
python3 scripts/engine_cli.py query 'ɡakːoɯ' --region ja-JP
python3 scripts/engine_cli.py query 'hə' --prefix --prefix-budget 2048 --limit 10
python3 scripts/engine_cli.py repl
python3 scripts/engine_cli.py info
python3 scripts/engine_cli.py cases tests/cases/graph_cases.jsonl --verbose
~~~

可重复 `--region` 开启多个地区；不指定则全部开启。全局 `--dictionary PATH`、`--cli PATH` 放在子命令前。文本输出显示匹配、耗时、扫描量、截断和每条候选的 IPA、地区、词频、类型、派生属性；`--json` 用于程序读取。原生 C++ 入口要求调用方提供 NFC，Python 包装负责规范化。

用例为 JSONL，每行一条。例如：

~~~json
{"id":"us-homophones","raw":"tu","regions":["en-US"],"limit":180,"expected_all":["too","two"]}
{"id":"reject-suffix","raw":"həˈloʊ123","expected_status":"unsupported_input","expected_empty":true}
{"id":"prefix-budget","raw":"hə","regions":["en-US"],"prefix":true,"prefix_budget":1,"expected_truncated":true}
~~~

支持 `expected_any`、`expected_all`、`expected_empty`、`expected_nonempty`、`expected_status`、`expected_truncated`。用 `cases PATH --output REPORT.json` 保存输入、输出、断言结果；用例中的地区、补全和候选上限均按配置执行。桌面 CLI 允许 limit=0 检查空结果；Android Options 的范围是 1–180，运行时关闭能力使用 Policy。

已有离线 `data/ipa.sqlite` 时，还可从每地区抽取 1,000 个有效来源观测：

~~~sh
python3 scripts/engine_cli.py sample-cases --per-region 1000 --seed 42
python3 scripts/engine_cli.py cases data/compiled/source-cases.jsonl \
  --output data/compiled/source-cases-results.json
~~~

抽样需要资料库，而普通 query/repl/cases 只需要编译词典。来源观测检查验证映射保留，不能替代音系核验或同音词首选准确率。

## 11. 已完成验证与实际边界

以下数字来自已有报告，不是预发布打包时重新执行的测试。设备为三星 S21 Ultra **SM-G998U1 / Android 15 / API 35**：

| 范围 | 输入与次数 | p50 / p95 / p99 |
| --- | --- | --- |
| 手机原生核心 | 五地区 5,000 输入，5 轮共 500,000 查询；各轮中位数 | 0.002761 / 0.006979 / 0.030781 ms |
| 完整 Java/JNI queryDetailed | 五地区交替输入，预热 1,000，测量 10,000；上限 180 | 0.046615 / 0.286407 / 1.783125 ms |
| 10 Hz 持续查询 | 180 秒、1,800 次 | 1.198073 / 3.500052 / 19.387187 ms |

上述查询没有错误或超时；完整包装和持续查询每次均有候选。Java/JNI 紧密循环最大 6.427 ms；10 Hz 持续模式最大 40.957 ms，低频唤醒条件下延迟更高。首次 `openBundled` 263.523 ms，缓存初始化 149.458 ms，包含资产和 SHA 开销。

24 位实际 AAR 的 17 项手机检查通过，涵盖五地区、Unicode/NFC、配置、候选与补全、关闭、资产提取、哈希、缓存重用/损坏修复、完整包装基准和短持续测试。另有 14 个人工边界用例、5,000 条来源观测、独立格式/候选参照，以及损坏文件、并发、ASan/UBSan、JNI 验证。完整报告留在本地 `data/reports/graph-s21-comparison.json`、`android-wrapper-probe.json` 和 `graph-cases.json`；公开发布摘要同时保存在版本目录的 `release.json`。

3 分钟内原生已分配堆为 7,320,048 → 7,320,096 B，整体进程 PSS 为 52,722 → 66,202 KiB，Java 已用堆为 5,011,408 → 19,171,280 B。未强制 GC；这段观察既不证明长期无泄漏，也不能单凭 Java 堆增长判定泄漏。报告的 PSS 含测试应用及映射页，并非单独词典的内存上界。

基准未覆盖输入法 UI、Messenger 调度、真实编辑器提交、15–30 分钟使用及耗电验收，未控制手机调频、GC 或全部缓存条件。默认 24 位已经确定；32 位对照资料继续保留，预发布仅提供所选 24 位包。当前尚无句子解码、模糊匹配或上下文模型，图关系正确不等于同音词首选永远正确。

## 12. 本地开发目录、重建与保留的模型

下表是完整本地开发项目的结构，不是 GitHub 发布仓库的文件清单。所有开发命令从本地项目根目录执行。

| 目录/文件 | 职责 |
| --- | --- |
| `graph/` | DAFSA 编译器、紧凑格式、C++17 图查询核心、原生 CLI 和格式测试。 |
| `android/` | Java/JNI 包装、AAR 清单与 consumer 规则、独立测试程序源码。 |
| `ipa_model/` | 保留的 GRU 训练、量化、导出和评估。 |
| `model_runtime/` | 保留的参数模型 C++ 推理库，原目录名 mobile。 |
| `ipa_engine/` | 资料检查用 Python/SQLite 字典基线。 |
| `scripts/` | 采集、规范化、索引、离线编译、CLI、Android 构建和打包入口。 |
| `tests/` | Python 验证及终端 JSONL 用例。 |
| `catalog/` | 来源、许可、原始快照 manifest。 |
| `data/` | 原始/规范化资料、SQLite、权重、编译词典、构建中间结果和已有报告。巨型资料不随发布包提供。 |
| `docs/` | 接口、图结构设计、模型实验和数据资料库记录。 |
| `remote/pre-release/` | 版本化的编译结果与发布元数据；不含源代码/测试 APK。 |
| `.tools/` | 被 Git 忽略的本地编译器、训练环境与可重建工具缓存。 |
| `remote/VERSION` | 当前发布版本，现为 v0.0.1-dev。 |

离线编译读取已有 SQLite，只在开发机运行。默认完整 24 位产物超出预算会失败，不会静默删词：

~~~sh
python3 scripts/compile_graph_dictionary.py --index-bits 24
python3 scripts/build_android_library.py --output-dir data/android/24
python3 scripts/package_pre_release.py
~~~

图编译需要 Python 3 与 C++17 编译器；Android 构建还需要 JDK、Android SDK 与 NDK。本项目已有构建使用 NDK r30；构建脚本可用 `--ndk /path/to/android-ndk-r30`、`--sdk /path/to/Android/sdk` 显式定位。完整数据复现采用本地 `docs/数据资料库.md` 中保留的逐来源程序和口径；输入法集成直接消费主 AAR。

`scripts/package_pre_release.py` 从 `remote/VERSION` 读取版本，从已有完成验证的 24 位 AAR 复制、校验并生成结果目录，不重跑手机或模型测试。已有版本目录中内容不同时拒绝覆盖，应发布新版本。`remote/pre-release/` 结果纳入本地与远程版本管理；大型 `data/` 数据、权重/词典缓存和 `.tools/` 运行环境被忽略，源码与 `data/reports/` 报告保留。来源许可按 `THIRD_PARTY_NOTICES.json` 和目录逐条使用，混合数据没有统一替代许可证。

GRU 工具、已训练权重和参数推理库继续保留，默认 Android 图引擎没有加载或调用 GRU。可独立复现原模型实验：

~~~sh
python3 -m venv .tools/train
.tools/train/bin/pip install -r requirements-training.txt
python3 scripts/prepare_model_data.py --max-per-region 6000
.tools/train/bin/python -m ipa_model.train --epochs 20
.tools/train/bin/python -m ipa_model.evaluate data/model/run/best.pt --quantized
~~~

未来模型可对已经生成的合法图候选打分、校准词频组合并重排；需要另行验证首选改善和手机成本。当前已有模型的生成能力不足以替代图词典，模型词表缺字也不应删除合法字典候选。格式与实验记录留在本地 `docs/参数模型引擎.md`、`docs/图词典引擎.md` 和 `docs/图结构选型.md`。

## 13. 发布范围与分支约定

远程仓库根目录包含 `README.md`、`VERSION`、`CONTRIBUTING.md` 和 `pre-release/`。这些文件在本地位于 `remote/`；远程不包含本地项目源码与数据。发布包及来源许可记录的相对路径在两种布局中一致。

开发分支为 `codex/ipa-engine-v0.0.1-dev`。开发更新不直接推送到 `main`，未来合并采用 `git merge --no-ff` 保留开发分支的合并记录；不使用 fast-forward、squash 或 rebase 合并。维护流程见 [CONTRIBUTING](CONTRIBUTING.md)。
