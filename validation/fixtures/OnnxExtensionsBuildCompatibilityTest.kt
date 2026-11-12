package com.example.ipa_board

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtException
import ai.onnxruntime.OrtSession
import ai.onnxruntime.extensions.OrtxLibrary
import ai.onnxruntime.extensions.OrtxPackage
import android.content.Context
import android.content.ContextWrapper
import android.content.res.AssetManager
import android.content.Intent
import android.content.ServiceConnection
import android.os.Build
import android.os.Bundle
import android.os.Process
import android.system.Os
import android.system.OsConstants
import android.util.Base64
import androidx.test.platform.app.InstrumentationRegistry
import com.example.ipa_board.diagnostics.DiagnosticComponent
import com.example.ipa_board.diagnostics.DiagnosticStage
import com.example.ipa_board.diagnostics.DiagnosticStageException
import com.example.ipa_board.ime.Candidate
import com.example.ipa_board.ime.ContextRankingSettings
import com.example.ipa_board.ime.EngineBackend
import com.example.ipa_board.ime.EngineRuntimeState
import com.example.ipa_board.ime.EngineRuntimeStates
import com.example.ipa_board.ime.SemanticEngine
import com.example.ipa_board.ime.SemanticModelFiles
import com.example.ipa_board.ime.SemanticRankerClient
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Build-verification fixture, copied only into an isolated IPA_Board checkout.
 * No tokenizer IDs are fabricated: compare mode requires a captured old-AAR baseline.
 * Run exclusively through the repository's protected regression runner.
 */
class OnnxExtensionsBuildCompatibilityTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val environment get() = OrtEnvironment.getEnvironment()
    private val configuration by lazy { testAssetJson("onnx-extension-validation.json") }
    private val manifest by lazy {
        JSONObject(context.assets.open("engines/semantic/manifest.json").bufferedReader().use { it.readText() })
    }
    private val samples = listOf(
        "empty" to "",
        "query-prefix" to "query: ",
        "english" to "query: apple pie",
        "chinese" to "query: 我要吃苹果",
        "japanese" to "query: 林檎",
        "japanese-sentence" to "query: こんにちは、世界。",
        "accent-composed" to "☺ café",
        "accent-decomposed" to "☺ cafe\u0301",
        "fullwidth" to "query: \u3000全角Ａ１２３",
        "ipa" to "query: əˈʊ ɪŋ θ ð ɹ̠",
        "emoji" to "query: 🙂 👩‍💻 👨‍👩‍👧‍👦 🇯🇵",
        "whitespace" to "query:\t apple  pie\r\n\u00a0中文\n日本語",
        "mixed-languages" to "query: English中文日本語🙂 əˈʊ",
        "punctuation" to "query: 123.45 -6% / [a] {b} (c) !? @#&",
        "other-scripts" to "query: Ελληνικά русский العربية 한국어",
        "format-characters" to "query: a\u200Bb\u2060c\uFEFFd",
        "long-mixed" to "query: " + "apple 苹果 林檎🙂 ".repeat(40)
    )

    @Test fun actualRuntimeMatchesRequestedPageSizeAndProcessArchitecture() {
        val expectedPageSize = configuration.getLong("expectedPageSize")
        assertTrue("Only explicit 4KB/16KB validation targets are accepted", expectedPageSize == 4096L || expectedPageSize == 16384L)
        assertEquals("The running process must use the requested page size", expectedPageSize, Os.sysconf(OsConstants._SC_PAGESIZE))
        assertTrue("This fixture validates a real 64-bit process", Process.is64Bit())
        if (configuration.getString("mode") == "compare") {
            assertTrue("The rebuilt public JNI registration entry point must resolve", OrtxLibrary.getNativeExtensionOperatorRegister() != 0L)
        }
        val processArchitecture = when (val value = System.getProperty("os.arch")) {
            "aarch64", "arm64" -> "arm64-v8a"
            "x86_64", "amd64" -> "x86_64"
            else -> throw AssertionError("Unexpected 64-bit process architecture: $value")
        }
        assertEquals(configuration.getString("expectedProcessArchitecture"), processArchitecture)
        assertEquals(MODEL_SHA256, manifest.getString("modelSha256"))
        assertEquals(TOKENIZER_SHA256, manifest.getString("tokenizerSha256"))
        emit("onnx_runtime_environment_b64", JSONObject()
            .put("pageSize", expectedPageSize)
            .put("processArchitecture", processArchitecture)
            .put("apiLevel", Build.VERSION.SDK_INT)
            .put("modelSha256", MODEL_SHA256)
            .put("tokenizerSha256", TOKENIZER_SHA256))
    }

    @Test fun tokenizerMatchesOldAarForMultilingualUnicodeAndLongInputs() {
        val mode = configuration.getString("mode")
        assertTrue("mode must be capture or compare", mode == "capture" || mode == "compare")
        val baseline = if (mode == "compare") testAssetJson("onnx-tokenizer-baseline.json") else null
        baseline?.let {
            assertEquals(1, it.getInt("formatVersion"))
            assertEquals(MODEL_SHA256, it.getString("modelSha256"))
            assertEquals(TOKENIZER_SHA256, it.getString("tokenizerSha256"))
            assertEquals(samples.size, it.getJSONArray("samples").length())
        }
        val actualSamples = JSONArray()
        tokenizer().use { session ->
            samples.forEachIndexed { index, (id, text) ->
                val actual = tokenize(session, text)
                assertTrue("$id must produce token IDs", actual.isNotEmpty())
                assertTrue("$id must not produce negative token IDs", actual.all { it >= 0 })
                assertArrayEquals("$id must be deterministic", actual, tokenize(session, text))
                baseline?.getJSONArray("samples")?.getJSONObject(index)?.let { expected ->
                    assertEquals(id, expected.getString("id"))
                    assertEquals(text, expected.getString("input"))
                    val expectedTokens = expected.getJSONArray("ids")
                    assertArrayEquals("$id differs from the original Extensions AAR", IntArray(expectedTokens.length()) { expectedTokens.getInt(it) }, actual)
                }
                actualSamples.put(JSONObject().put("id", id).put("input", text).put("ids", JSONArray(actual.toList())))
            }
        }
        emit("onnx_tokenizer_baseline_b64", JSONObject()
            .put("formatVersion", 1)
            .put("modelSha256", MODEL_SHA256)
            .put("tokenizerSha256", TOKENIZER_SHA256)
            .put("samples", actualSamples))
    }

    @Test fun tokenizerCannotRunWithoutCustomOperatorRegistration() {
        val tokenizerFile = File(SemanticModelFiles.deploy(context), "tokenizer.onnx")
        OrtSession.SessionOptions().use { options ->
            try {
                environment.createSession(tokenizerFile.absolutePath, options).use { }
                fail("The production SentencePiece graph must require the Extensions custom operator")
            } catch (_: OrtException) {
                // The positive tokenizer test proves that registration makes this same graph work.
            }
        }
    }

    @Test fun realE5ScoresEnglishChineseJapaneseAndFollowingContext() {
        val probes = JSONArray()
        SemanticEngine(context).use { engine ->
            val words = listOf("错", "子", "code", "copy", "coffee")
            val drink = scores(engine, "I would like to drink ", "", words)
            assertTrue("English drink context must distinguish coffee from code", drink[4] > drink[2])
            probes.put(probe("english-before", drink))
            val chinese = scores(engine, "我想喝杯咖啡", "来提提神", words)
            assertTrue("Chinese context must retain the existing cross-language coffee relationship", chinese[4] > chinese[0])
            probes.put(probe("chinese-both-sides", chinese))
            listOf("coffee", "咖啡", "コーヒー").forEach { word ->
                val following = scores(engine, "", word, listOf(word, "software", "天气", "電車"))
                assertTrue("Following context must preserve semantic identity for $word", following[0] > 0.85f)
                probes.put(probe("following-$word", following))
            }
        }
        val report = JSONObject().put("formatVersion", 1).put("modelSha256", MODEL_SHA256)
            .put("tokenizerSha256", TOKENIZER_SHA256).put("probes", probes)
        if (configuration.getString("mode") == "compare") {
            val tolerance = configuration.getDouble("scoreAbsoluteTolerance")
            assertTrue("Comparison tolerance must stay at or below 0.001", tolerance in 0.0..0.001)
            val baseline = testAssetJson("onnx-e5-baseline.json")
            assertEquals(1, baseline.getInt("formatVersion"))
            assertEquals(MODEL_SHA256, baseline.getString("modelSha256"))
            assertEquals(TOKENIZER_SHA256, baseline.getString("tokenizerSha256"))
            val expected = baseline.getJSONArray("probes")
            assertEquals(expected.length(), probes.length())
            var maximumDifference = 0.0
            for (i in 0 until probes.length()) {
                val previous = expected.getJSONObject(i)
                val current = probes.getJSONObject(i)
                assertEquals(previous.getString("id"), current.getString("id"))
                val expectedScores = previous.getJSONArray("scores")
                val actualScores = current.getJSONArray("scores")
                assertEquals(expectedScores.length(), actualScores.length())
                for (j in 0 until actualScores.length()) {
                    val difference = kotlin.math.abs(expectedScores.getDouble(j) - actualScores.getDouble(j))
                    maximumDifference = maxOf(maximumDifference, difference)
                    assertEquals("${current.getString("id")}[$j] differs from original-AAR E5 inference",
                        expectedScores.getDouble(j), actualScores.getDouble(j), tolerance)
                }
            }
            report.put("maximumAbsoluteDifference", maximumDifference).put("scoreAbsoluteTolerance", tolerance)
        }
        emit("onnx_e5_probe_b64", report)
    }

    @Test fun cancellationReturnsNoPartialResultAndNextQueryStillWorks() {
        SemanticEngine(context).use { engine ->
            val words = listOf("coffee", "code", "copy")
            assertNull(engine.score("drink", "", words) { true })
            var checks = 0
            val cancelled = engine.score("I would like to drink ", "", words) { ++checks >= 3 }
            assertEquals("Cancellation must be checked while processing candidates", 3, checks)
            assertNull("A partly calculated result must not escape", cancelled)
            val first = scores(engine, "", "coffee", words)
            val cached = scores(engine, "", "coffee", words)
            assertArrayEquals("Cancellation must not poison later cached queries", first, cached, 0f)
            assertTrue(first[0] > 0.85f)
        }
    }

    @Test fun emptyContextIsNeutralAndLongContextKeepsFollowingTextRelevant() {
        SemanticEngine(context).use { engine ->
            val words = listOf("coffee", "code", "林檎")
            assertArrayEquals(FloatArray(words.size), scores(engine, "", "", words), 0f)
            val longBefore = "This synthetic software discussion concerns code. ".repeat(40)
            val beforeOnly = scores(engine, longBefore, "", words)
            val bothSides = scores(engine, longBefore, "coffee", words)
            assertTrue("Long preceding context must not erase the effect of following text",
                beforeOnly.indices.any { kotlin.math.abs(beforeOnly[it] - bothSides[it]) > 0.00001f })
        }
    }

    @Test fun unavailableSemanticServicePreservesTheEntireNativeCandidateSet() {
        val preferences = ContextRankingSettings.preferences(context)
        val hadPreference = preferences.contains(ContextRankingSettings.KEY_ENABLED)
        val oldPreference = ContextRankingSettings.isEnabled(context)
        val candidates = listOf(Candidate("coffee", "EN", 4), Candidate("错", "简", 0), Candidate("子", "日", 0))
        val returned = CountDownLatch(1)
        val states = EngineRuntimeStates()
        var actual: List<Candidate>? = null
        var status = ""
        var revision = -1L
        var bindAttempts = 0
        val unavailableContext = object : ContextWrapper(context) {
            override fun bindService(intent: Intent, connection: ServiceConnection, flags: Int): Boolean {
                bindAttempts++
                return false
            }
        }
        var client: SemanticRankerClient? = null
        try {
            instrumentation.runOnMainSync {
                ContextRankingSettings.setEnabled(context, true)
                client = SemanticRankerClient(unavailableContext, states) { receivedRevision, items, state ->
                    revision = receivedRevision
                    actual = items
                    status = state
                    returned.countDown()
                }
                client!!.query(91L, "co", "drink", "", candidates)
            }
            assertTrue("Rejected service binding must report its fallback", returned.await(10, TimeUnit.SECONDS))
            instrumentation.runOnMainSync {
                assertEquals(91L, revision)
                assertEquals(candidates, actual)
                assertTrue(status.isNotEmpty())
                assertEquals(1, bindAttempts)
                assertEquals(EngineRuntimeState.UNAVAILABLE, states.snapshot()[EngineBackend.SEMANTIC])
            }
        } finally {
            instrumentation.runOnMainSync {
                client?.release()
                preferences.edit().apply {
                    if (hadPreference) putBoolean(ContextRankingSettings.KEY_ENABLED, oldPreference)
                    else remove(ContextRankingSettings.KEY_ENABLED)
                }.commit()
            }
        }
    }

    @Test fun controlledAssetAndMalformedModelFailuresRetainTheirStagesAndRecover() {
        val ownedRoot = File(context.cacheDir, "onnx-failure-${UUID.randomUUID()}")
        assertTrue(ownedRoot.mkdirs())
        try {
            val blocker = File(ownedRoot, "blocked-directory").apply { writeText("synthetic obstruction") }
            val blockedContext = object : ContextWrapper(context) {
                override fun getNoBackupFilesDir(): File = blocker
            }
            try {
                SemanticModelFiles.deploy(blockedContext)
                fail("A test-owned regular file must reject model deployment")
            } catch (error: DiagnosticStageException) {
                assertEquals(DiagnosticComponent.SEMANTIC, error.issue.component)
                assertEquals(DiagnosticStage.ASSETS, error.issue.stage)
            }
            assertEquals("synthetic obstruction", blocker.readText())
            val isolatedModelRoot = File(ownedRoot, "malformed-model").apply { mkdirs() }
            val malformedContext = object : ContextWrapper(context) {
                override fun getNoBackupFilesDir(): File = isolatedModelRoot
                override fun getAssets(): AssetManager = instrumentation.context.assets
            }
            try {
                SemanticEngine(malformedContext).use { }
                fail("A malformed test-owned ONNX model must fail initialization")
            } catch (error: DiagnosticStageException) {
                assertEquals(DiagnosticComponent.SEMANTIC, error.issue.component)
                assertEquals(DiagnosticStage.MODEL, error.issue.stage)
                assertTrue("The registered tokenizer must succeed before model parsing fails", error.cause is OrtException)
            }
            SemanticEngine(context).use { engine ->
                assertTrue("A model-init failure must leave the shared runtime usable", scores(engine, "", "coffee", listOf("coffee"))[0] > 0.85f)
            }
        } finally {
            assertEquals(context.cacheDir.canonicalFile, ownedRoot.canonicalFile.parentFile)
            assertTrue("Remove only the fixture's private temporary directory", ownedRoot.deleteRecursively())
        }
    }
    private fun tokenizer(): OrtSession {
        val file = File(SemanticModelFiles.deploy(context), "tokenizer.onnx")
        return OrtSession.SessionOptions().use { options ->
            options.setIntraOpNumThreads(1)
            options.registerCustomOpLibrary(OrtxPackage.getLibraryPath())
            environment.createSession(file.absolutePath, options)
        }
    }

    private fun tokenize(session: OrtSession, text: String): IntArray =
        OnnxTensor.createTensor(environment, arrayOf(text)).use { input ->
            session.run(mapOf("inputs" to input)).use { result -> (result[0].value as IntArray).clone() }
        }

    private fun scores(engine: SemanticEngine, before: String, after: String, words: List<String>): FloatArray {
        val result = engine.score(before, after, words) { false }
        assertNotNull("Uncancelled E5 query must return a result", result)
        return result!!.also {
            assertEquals(words.size, it.size)
            assertTrue("Every semantic score must be finite", it.all { score -> score.isFinite() })
        }
    }

    private fun probe(id: String, values: FloatArray) = JSONObject().put("id", id).put("scores", JSONArray(values.toList()))
    private fun testAssetJson(name: String) = JSONObject(instrumentation.context.assets.open(name).bufferedReader().use { it.readText() })
    private fun emit(key: String, value: JSONObject) {
        // Instrumentation output survives regression rollback; never read application-private user data.
        val encoded = Base64.encodeToString(value.toString().toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
        instrumentation.sendStatus(2, Bundle().apply { putString(key, encoded) })
    }

    companion object {
        private const val MODEL_SHA256 = "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"
        private const val TOKENIZER_SHA256 = "4d28a2a61017a7b222164065d832e51103fbb3a4451c4e4938a2eeb8e83e44e8"
    }
}