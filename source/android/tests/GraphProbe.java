package com.ipaengine.graph.probe;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Context;
import android.os.Build;
import android.os.Bundle;
import android.os.Debug;
import com.ipaengine.graph.GraphEngine;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.regex.Pattern;

/** On-device test of the actual packaged AAR, Java API, JNI and asset lifecycle. */
public final class GraphProbe extends Instrumentation {
    private final JSONArray checks = new JSONArray();
    private final JSONArray queries = new JSONArray();
    private boolean passed = true;
    private boolean assetVerified = false;
    private Bundle arguments = new Bundle();

    private interface Test { void run() throws Exception; }
    private static void require(boolean value, String description) {
        if (!value) throw new AssertionError(description);
    }
    private void test(String name, Test action) throws Exception {
        JSONObject result = new JSONObject().put("id", name);
        try {
            action.run();
            result.put("passed", true);
        } catch (Throwable error) {
            passed = false;
            result.put("passed", false).put("error", error.getClass().getName() + ": " + error.getMessage());
        }
        checks.put(result);
        Bundle update = new Bundle();
        update.putString("check", name);
        update.putBoolean("passed", result.getBoolean("passed"));
        sendStatus(0, update);
    }
    private static String hash(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream source = new FileInputStream(file)) {
            byte[] buffer = new byte[65536];
            for (int count; (count = source.read(buffer)) != -1;) digest.update(buffer, 0, count);
        }
        StringBuilder value = new StringBuilder();
        for (byte b : digest.digest()) value.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return value.toString();
    }
    private static String read(InputStream source) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        for (int count; (count = source.read(buffer)) != -1;) {
            if (output.size() + count > 1048576) throw new IOException("manifest exceeds test bound");
            output.write(buffer, 0, count);
        }
        return new String(output.toByteArray(), StandardCharsets.UTF_8);
    }
    private GraphEngine.QueryResult query(String id, GraphEngine engine, String raw) throws Exception {
        GraphEngine.QueryResult result = engine.queryDetailed(raw, "游标前 context", "あと");
        JSONObject item = new JSONObject().put("id", id).put("raw", raw).put("status", result.status)
                .put("elapsed_ms", result.elapsedMs).put("exact_found", result.exactFound)
                .put("truncated", result.truncated).put("matched_keys", result.matchedKeys)
                .put("matched_records", result.matchedRecords).put("returned", result.candidates.size());
        JSONArray candidates = new JSONArray();
        for (GraphEngine.Candidate candidate : result.candidates) {
            candidates.put(new JSONObject().put("text", candidate.text).put("language", candidate.language)
                    .put("rank", candidate.rank).put("kind", candidate.kind).put("native_score", candidate.nativeScore)
                    .put("frequency_score", candidate.frequencyScore).put("matched_ipa", candidate.matchedIpa)
                    .put("region", candidate.region).put("derived", candidate.derived));
        }
        item.put("candidates", candidates);
        queries.put(item);
        return result;
    }
    private static boolean contains(GraphEngine.QueryResult result, String text, String language) {
        for (GraphEngine.Candidate candidate : result.candidates)
            if (candidate.text.equals(text) && candidate.language.equals(language)) return true;
        return false;
    }
    private static GraphEngine.Policy region(int id) {
        return new GraphEngine.Policy(id == 0, id == 1, id == 2, id == 3, id == 4);
    }
    private static void expectClosed(Test call) throws Exception {
        try { call.run(); throw new AssertionError("closed engine accepted call"); }
        catch (IllegalStateException expected) { }
    }
    private static JSONObject memory() throws Exception {
        Debug.MemoryInfo info = new Debug.MemoryInfo();
        Debug.getMemoryInfo(info);
        Runtime runtime = Runtime.getRuntime();
        return new JSONObject().put("total_pss_kib", info.getTotalPss())
                .put("total_private_dirty_kib", info.getTotalPrivateDirty())
                .put("dalvik_pss_kib", info.dalvikPss).put("native_pss_kib", info.nativePss)
                .put("other_pss_kib", info.otherPss)
                .put("native_heap_allocated_bytes", Debug.getNativeHeapAllocatedSize())
                .put("native_heap_size_bytes", Debug.getNativeHeapSize())
                .put("java_heap_used_bytes", runtime.totalMemory() - runtime.freeMemory())
                .put("java_heap_committed_bytes", runtime.totalMemory())
                .put("java_heap_max_bytes", runtime.maxMemory());
    }
    private static int statusIndex(String status) {
        if (status.equals("ok")) return 0;
        if (status.equals("unsupported_input")) return 1;
        if (status.equals("timeout")) return 2;
        return 3;
    }
    private static JSONObject statuses(int[] values) throws Exception {
        return new JSONObject().put("ok", values[0]).put("unsupported_input", values[1])
                .put("timeout", values[2]).put("error", values[3]);
    }
    private static double percentile(long[] values, double q) {
        return values[Math.max(0, (int) Math.ceil(values.length * q) - 1)] / 1000000.0;
    }
    private static JSONObject timings(long[] elapsed) throws Exception {
        Arrays.sort(elapsed);
        return new JSONObject().put("p50_ms", percentile(elapsed, 0.50))
                .put("p95_ms", percentile(elapsed, 0.95)).put("p99_ms", percentile(elapsed, 0.99))
                .put("max_ms", elapsed[elapsed.length - 1] / 1000000.0);
    }
    private ArrayList<String> benchmarkCases(Context context) throws Exception {
        ArrayList<String> result = new ArrayList<>(5000);
        try (BufferedReader source = new BufferedReader(new InputStreamReader(
                context.getAssets().open("probe/ipa-cases.txt"), StandardCharsets.UTF_8))) {
            for (String line; (line = source.readLine()) != null;) {
                require(!line.isEmpty() && line.length() <= 64, "invalid benchmark IPA line");
                require(result.size() < 5000, "benchmark case asset has too many inputs");
                result.add(line);
            }
        }
        require(result.size() == 5000, "benchmark requires exactly 5000 balanced inputs");
        return result;
    }
    private String argument(String key, String fallback) {
        String value = arguments.getString(key);
        return value == null ? fallback : value;
    }
    private JSONObject benchmark(Context context, GraphEngine engine) throws Exception {
        ArrayList<String> cases = benchmarkCases(context);
        final int warmup = Integer.parseInt(argument("warmup", "1000"));
        final int iterations = Integer.parseInt(argument("iterations", "10000"));
        require(warmup >= 0 && warmup <= 1000000 && iterations >= 1 && iterations <= 1000000,
                "invalid benchmark iteration bounds");
        engine.configure(new GraphEngine.Policy(), new GraphEngine.Options(180, false, 60, 2048, false, 20000));
        for (int i = 0; i < warmup; i++) engine.queryDetailed(cases.get(i % cases.size()));
        long[] elapsed = new long[iterations];
        long[] nativeElapsed = new long[iterations];
        JSONObject before = memory();
        int[] states = new int[4];
        long candidates = 0L;
        int nonempty = 0;
        long begin = System.nanoTime();
        for (int i = 0; i < iterations; i++) {
            String raw = cases.get(i % cases.size());
            long start = System.nanoTime();
            GraphEngine.QueryResult result = engine.queryDetailed(raw);
            elapsed[i] = System.nanoTime() - start;
            nativeElapsed[i] = (long) (result.elapsedMs * 1000000.0);
            states[statusIndex(result.status)]++;
            candidates += result.candidates.size();
            if (!result.candidates.isEmpty()) nonempty++;
        }
        double wall = (System.nanoTime() - begin) / 1000000.0;
        JSONObject after = memory();
        JSONObject result = timings(elapsed).put("cases", cases.size()).put("warmup", warmup)
                .put("iterations", iterations).put("regions", "all").put("candidate_limit", 180)
                .put("total_wall_ms", wall).put("statuses", statuses(states)).put("nonempty_queries", nonempty)
                .put("candidate_total", candidates).put("native_core_timings", timings(nativeElapsed))
                .put("memory_before", before).put("memory_after", after)
                .put("scope", "GraphEngine.queryDetailed: UTF16/NFC, JNI, native lookup/JSON, Android JSON parsing and candidate objects; excludes input-method UI and asset loading")
                .put("gc_forced", false);
        try (InputStream source = context.getAssets().open("probe/cases.manifest.json")) {
            result.put("case_asset", new JSONObject(read(source)));
        }
        return result;
    }
    private JSONObject soak(Context context, GraphEngine engine, double seconds, double rate) throws Exception {
        require(Double.isFinite(seconds) && seconds > 0 && seconds <= 3600,
                "soak seconds must be in (0,3600]");
        require(Double.isFinite(rate) && rate >= 1 && rate <= 60, "query rate must be in [1,60]");
        ArrayList<String> cases = benchmarkCases(context);
        engine.configure(new GraphEngine.Policy(), new GraphEngine.Options(180, false, 60, 2048, false, 20000));
        long[] elapsed = new long[(int) Math.ceil(seconds * rate) + 1];
        JSONObject before = memory();
        JSONArray samples = new JSONArray().put(new JSONObject().put("at_seconds", 0).put("memory", before));
        long begin = System.nanoTime(), end = begin + (long) (seconds * 1000000000.0);
        long next = begin, interval = (long) (1000000000.0 / rate), nextSample = begin + 5000000000L;
        int count = 0, nonempty = 0;
        int[] states = new int[4];
        while (System.nanoTime() < end) {
            long wait = next - System.nanoTime();
            if (wait > 0) Thread.sleep(wait / 1000000L, (int) (wait % 1000000L));
            if (System.nanoTime() >= end) break;
            long start = System.nanoTime();
            GraphEngine.QueryResult value = engine.queryDetailed(cases.get(count % cases.size()));
            elapsed[count++] = System.nanoTime() - start;
            states[statusIndex(value.status)]++;
            if (!value.candidates.isEmpty()) nonempty++;
            long now = System.nanoTime();
            next = Math.max(next + interval, now);
            if (now >= nextSample) {
                double at = (now - begin) / 1000000000.0;
                samples.put(new JSONObject().put("at_seconds", at).put("memory", memory()));
                Bundle update = new Bundle();
                update.putString("soak_progress", "queries=" + count + ",seconds=" + at);
                sendStatus(0, update);
                nextSample = now + 5000000000L;
            }
        }
        double duration = (System.nanoTime() - begin) / 1000000000.0;
        JSONObject after = memory();
        samples.put(new JSONObject().put("at_seconds", duration).put("memory", after));
        require(count > 0, "soak produced no query");
        return timings(Arrays.copyOf(elapsed, count)).put("requested_seconds", seconds)
                .put("elapsed_seconds", duration).put("requested_queries_per_second", rate)
                .put("actual_queries_per_second", count / duration).put("queries", count)
                .put("nonempty_queries", nonempty).put("statuses", statuses(states))
                .put("memory_before", before).put("memory_after", after).put("memory_samples", samples)
                .put("scope", "paced GraphEngine.queryDetailed calls, no input-method UI; memory samples every 5 seconds")
                .put("gc_forced", false);
    }
    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        this.arguments = arguments == null ? new Bundle() : new Bundle(arguments);
        start();
    }

    @Override public void onStart() {
        JSONObject report = new JSONObject();
        GraphEngine engine = null;
        try {
            Context context = getTargetContext();
            report.put("package", context.getPackageName()).put("model", Build.MODEL)
                    .put("manufacturer", Build.MANUFACTURER).put("sdk_int", Build.VERSION.SDK_INT)
                    .put("fingerprint", Build.FINGERPRINT).put("abi", Build.SUPPORTED_ABIS[0]);
            JSONObject manifest;
            try (InputStream source = context.getAssets().open("ipa/lexicon.manifest.json")) {
                manifest = new JSONObject(read(source));
            }
            require(manifest.getBoolean("complete"), "AAR dictionary is incomplete");
            final String expectedHash = manifest.getString("sha256");
            final long expectedBytes = manifest.getLong("bytes");
            final File dictionary = new File(new File(context.getNoBackupFilesDir(), "ipa-graph"),
                    "lexicon-" + expectedHash + ".ipad");
            // Only this probe's private cache is removed, to require a real asset extraction.
            if (dictionary.exists()) require(dictionary.delete(), "cannot clear probe's private dictionary cache");
            long begin = System.nanoTime();
            engine = GraphEngine.openBundled(context);
            report.put("fresh_open_ms", (System.nanoTime() - begin) / 1000000.0)
                    .put("dictionary_bytes", expectedBytes).put("dictionary_sha256", expectedHash);
            final GraphEngine loaded = engine;
            test("asset-extraction-and-sha256", () -> {
                require(dictionary.isFile() && dictionary.length() == expectedBytes, "extracted dictionary size mismatch");
                require(hash(dictionary).equals(expectedHash), "extracted dictionary SHA256 mismatch");
                assetVerified = true;
            });
            final String[] raws = {"həˈloʊ", "həˈləʊ", "ni˨˩˦ xɑʊ̯˨˩˦", "tʰaɪ˧˥.wan˥˥", "ɡakːoɯ"};
            final String[] expected = {"hello", "hello", "你好", "臺灣", "学校"};
            final String[] labels = {"EN", "EN", "简", "繁", "日"};
            final String[] names = {"en-US", "en-GB", "zh-CN", "zh-TW", "ja-JP"};
            for (int index = 0; index < raws.length; index++) {
                final int id = index;
                test("exact-" + names[id], () -> {
                    loaded.configure(region(id), new GraphEngine.Options(30, false, 60, 2048, false, 20000));
                    require(loaded.acceptsInput(raws[id]), "valid IPA rejected");
                    GraphEngine.QueryResult result = query(names[id], loaded, raws[id]);
                    require(result.status.equals("ok") && result.exactFound, "exact query failed");
                    require(contains(result, expected[id], labels[id]), "expected output/label missing");
                    for (GraphEngine.Candidate candidate : result.candidates) {
                        require(candidate.region.equals(names[id]), "disabled region leaked");
                        require(candidate.kind == GraphEngine.KIND_CONVERSION, "exact result kind incorrect");
                    }
                });
            }
            test("ordered-omissions-and-numeric-tones", () -> {
                loaded.configure(region(0), new GraphEngine.Options());
                require(contains(query("missing-stress", loaded, "maɪ"), "my", "EN"), "missing stress lost my");
                loaded.configure(region(2), new GraphEngine.Options());
                require(contains(query("missing-tones-and-boundary", loaded, "nixɑʊ"), "你好", "简"), "omissions lost nihao");
                GraphEngine.QueryResult numeric = query("numeric-tones", loaded, "ni35 xɑʊ̯21");
                require(numeric.exactFound && contains(numeric, "你好", "简"), "numeric tone alias failed");
                loaded.configure(region(0), new GraphEngine.Options(30, false, 60, 2048, false, 20000));
            });
            test("region-disable-and-reenable", () -> {
                loaded.configure(new GraphEngine.Policy(false, false, false, false, false));
                require(query("all-disabled", loaded, raws[0]).candidates.isEmpty(), "disabled policy produced candidates");
                loaded.configure(region(0));
                require(contains(query("reenabled-en-US", loaded, raws[0]), "hello", "EN"), "reenabling failed");
            });
            test("prefix-completion-and-budget", () -> {
                loaded.configure(region(0), new GraphEngine.Options(30, true, 60, 2048, false, 20000));
                GraphEngine.QueryResult complete = query("prefix-hə", loaded, "hə");
                require(complete.status.equals("ok") && !complete.candidates.isEmpty(), "prefix query failed");
                boolean completion = false;
                for (GraphEngine.Candidate candidate : complete.candidates)
                    completion |= candidate.kind == GraphEngine.KIND_COMPLETION;
                require(completion, "completion kind missing");
                loaded.configure(region(0), new GraphEngine.Options(30, true, 60, 1, false, 20000));
                GraphEngine.QueryResult bounded = query("prefix-budget1", loaded, "hə");
                require(bounded.status.equals("ok") && bounded.truncated && bounded.matchedKeys == 1,
                        "prefix key budget not applied");
                loaded.configure(region(0), new GraphEngine.Options(30, false, 60, 2048, false, 20000));
            });
            test("candidate-limit-and-immutability", () -> {
                loaded.configure(region(0), new GraphEngine.Options(1, false, 60, 2048, false, 20000));
                GraphEngine.QueryResult result = query("limit1-tu", loaded, "tu");
                require(result.status.equals("ok") && result.candidates.size() == 1, "candidate limit not applied");
                try { result.candidates.clear(); throw new AssertionError("returned list is mutable"); }
                catch (UnsupportedOperationException expectedFailure) { }
                loaded.configure(region(0), new GraphEngine.Options(30, false, 60, 2048, false, 20000));
            });
            test("empty-and-whole-input", () -> {
                require(loaded.query("").isEmpty() && query("empty", loaded, "").status.equals("ok"), "empty input prediction");
                require(!loaded.acceptsInput("həˈloʊ0"), "invalid suffix accepted");
                GraphEngine.QueryResult invalid = query("unsupported-suffix", loaded, "həˈloʊ0");
                require(invalid.status.equals("unsupported_input") && invalid.candidates.isEmpty(), "unsupported suffix converted");
                GraphEngine.QueryResult unmatched = query("unmatched-known-suffix", loaded, "həˈloʊzzz");
                require(unmatched.status.equals("ok") && unmatched.candidates.isEmpty(), "known suffix silently dropped");
                require(query("blank", loaded, " ").candidates.isEmpty(), "blank input generated candidates");
            });
            test("utf16-and-nfc", () -> {
                for (String invalid : new String[]{"\ud800", "\udc00", "\ud800a", "\ud83d\ude00"}) {
                    require(!loaded.acceptsInput(invalid), "invalid or unsupported UTF16 accepted");
                    require(loaded.queryDetailed(invalid).status.equals("unsupported_input"), "invalid UTF16 query status");
                }
                String sixtyFive = new String(new char[65]).replace('\0', 'a');
                require(!loaded.acceptsInput(sixtyFive) && loaded.query(sixtyFive).isEmpty(), "UTF16 input bound missing");
                require(loaded.acceptsInput("a\u0301") == loaded.acceptsInput("\u00e1"), "NFC accepts normalization mismatch");
                GraphEngine.QueryResult decomposed = loaded.queryDetailed("a\u0301");
                GraphEngine.QueryResult composed = loaded.queryDetailed("\u00e1");
                require(decomposed.status.equals(composed.status) && decomposed.candidates.size() == composed.candidates.size(),
                        "NFC query normalization mismatch");
                for (int i = 0; i < composed.candidates.size(); i++)
                    require(decomposed.candidates.get(i).text.equals(composed.candidates.get(i).text), "NFC output mismatch");
                Pattern pattern = Pattern.compile(loaded.segmentPattern());
                require(pattern.matcher(raws[0]).matches() && !pattern.matcher("").find(), "segment regex invalid or empty");
            });
            test("missing-path-ioexception", () -> {
                try { new GraphEngine(dictionary.getAbsolutePath() + ".missing"); throw new AssertionError("missing path loaded"); }
                catch (IOException expectedFailure) { }
            });
            test("bundled-cache-reuse", () -> {
                long modified = dictionary.lastModified();
                long start = System.nanoTime();
                try (GraphEngine cached = GraphEngine.openBundled(context)) {
                    cached.configure(region(0));
                    require(contains(query("cached-open", cached, raws[0]), "hello", "EN"), "cached asset query failed");
                }
                report.put("cached_open_ms", (System.nanoTime() - start) / 1000000.0);
                require(dictionary.lastModified() == modified, "cached dictionary was unnecessarily rewritten");
            });
            test("java-jni-end-to-end-benchmark", () -> {
                JSONObject value = benchmark(context, loaded);
                report.put("wrapper_benchmark", value);
                JSONObject states = value.getJSONObject("statuses");
                require(states.getInt("error") == 0 && states.getInt("timeout") == 0
                                && states.getInt("unsupported_input") == 0
                                && states.getInt("ok") == value.getInt("iterations"),
                        "wrapper benchmark had errors, timeouts or unsupported inputs");
                require(value.getInt("nonempty_queries") == value.getInt("iterations"),
                        "wrapper benchmark lost source-backed candidates");
            });
            double soakSeconds = Double.parseDouble(argument("soak_seconds", "0"));
            if (soakSeconds > 0) test("java-jni-paced-soak", () -> {
                JSONObject value = soak(context, loaded, soakSeconds, Double.parseDouble(argument("query_rate", "10")));
                report.put("wrapper_soak", value);
                JSONObject states = value.getJSONObject("statuses");
                require(states.getInt("error") == 0 && states.getInt("timeout") == 0
                                && states.getInt("unsupported_input") == 0
                                && states.getInt("ok") == value.getInt("queries"),
                        "wrapper soak had errors, timeouts or unsupported inputs");
                require(value.getInt("nonempty_queries") == value.getInt("queries"),
                        "wrapper soak lost source-backed candidates");
            });
            loaded.close();
            test("closed-engine-and-idempotent-close", () -> {
                loaded.close();
                expectClosed(() -> loaded.acceptsInput(raws[0]));
                expectClosed(() -> loaded.query(raws[0]));
                expectClosed(() -> loaded.configure(region(0)));
                expectClosed(() -> loaded.segmentPattern());
            });
            test("corrupt-private-cache-repaired", () -> {
                try (RandomAccessFile cache = new RandomAccessFile(dictionary, "rw")) { cache.writeByte('X'); }
                require(!hash(dictionary).equals(expectedHash), "test cache corruption did not take effect");
                try (GraphEngine repaired = GraphEngine.openBundled(context)) {
                    repaired.configure(region(0));
                    require(contains(query("repaired-cache", repaired, raws[0]), "hello", "EN"), "cache repair query failed");
                }
                require(hash(dictionary).equals(expectedHash), "corrupted cache SHA256 not repaired");
            });
        } catch (Throwable failure) {
            passed = false;
            try { report.put("fatal_error", failure.getClass().getName() + ": " + failure.getMessage()); }
            catch (Exception ignored) { }
        } finally {
            if (engine != null) engine.close();
            Bundle result = new Bundle();
            try {
                report.put("passed", passed).put("checks", checks).put("queries", queries)
                        .put("android_device_execution", true).put("aar_asset_extraction_tested", assetVerified);
                result.putString("report", report.toString());
                result.putBoolean("passed", passed);
            } catch (Exception failure) { result.putString("report", "{\"passed\":false,\"fatal_error\":\"report serialization\"}"); }
            finish(passed ? Activity.RESULT_OK : Activity.RESULT_CANCELED, result);
        }
    }
}
