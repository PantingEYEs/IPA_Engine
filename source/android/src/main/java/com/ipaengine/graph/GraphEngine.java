package com.ipaengine.graph;

import android.content.Context;
import android.content.res.AssetManager;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Android adapter for the read-only IPA graph. Every request contains the entire
 * current preedit. Context is accepted but does not affect frequency ordering.
 * Calls and close are serialized; results are immutable snapshots.
 */
public final class GraphEngine implements AutoCloseable {
    public static final int KIND_CONVERSION = 0;
    public static final int KIND_COMPLETION = 2;
    public static final long RECOMMENDED_DICTIONARY_BYTES = 30_000_000L;
    // The user permits a larger comparison build; this is an absolute load cap.
    public static final long MAX_DICTIONARY_BYTES = 40_000_000L;
    private static final String DICTIONARY_ASSET = "ipa/lexicon.ipad";
    private static final String MANIFEST_ASSET = "ipa/lexicon.manifest.json";
    private static final Throwable LIBRARY_ERROR;

    static {
        Throwable failure = null;
        try {
            System.loadLibrary("ipa_graph_android");
        } catch (LinkageError | SecurityException error) {
            failure = error;
        }
        LIBRARY_ERROR = failure;
    }

    /** Region snapshot. Disabled regions never produce candidates. */
    public static final class Policy {
        public final boolean enUS;
        public final boolean enGB;
        public final boolean zhCN;
        public final boolean zhTW;
        public final boolean jaJP;

        public Policy() { this(true, true, true, true, true); }
        public Policy(boolean enUS, boolean enGB, boolean zhCN, boolean zhTW, boolean jaJP) {
            this.enUS = enUS;
            this.enGB = enGB;
            this.zhCN = zhCN;
            this.zhTW = zhTW;
            this.jaJP = jaJP;
        }
    }

    /** Bounded per-query work. Prefix completion is opt-in. */
    public static final class Options {
        public final int candidateLimit;
        public final boolean prefix;
        public final int deadlineMs;
        public final int prefixKeyBudget;
        public final boolean allowOmissions;
        public final int omissionStateBudget;

        public Options() { this(30, false, 60, 2048); }
        public Options(int candidateLimit, boolean prefix, int deadlineMs, int prefixKeyBudget) {
            this(candidateLimit, prefix, deadlineMs, prefixKeyBudget, true, 20000);
        }
        public Options(int candidateLimit, boolean prefix, int deadlineMs, int prefixKeyBudget,
                       boolean allowOmissions, int omissionStateBudget) {
            if (candidateLimit < 1 || candidateLimit > 180) {
                throw new IllegalArgumentException("candidateLimit must be between 1 and 180");
            }
            if (deadlineMs < 1 || deadlineMs > 60) {
                throw new IllegalArgumentException("deadlineMs must be between 1 and 60");
            }
            if (prefixKeyBudget < 1 || prefixKeyBudget > 65536) {
                throw new IllegalArgumentException("prefixKeyBudget must be between 1 and 65536");
            }
            if (omissionStateBudget < 1 || omissionStateBudget > 200000) {
                throw new IllegalArgumentException("omissionStateBudget must be between 1 and 200000");
            }
            this.candidateLimit = candidateLimit;
            this.prefix = prefix;
            this.deadlineMs = deadlineMs;
            this.prefixKeyBudget = prefixKeyBudget;
            this.allowOmissions = allowOmissions;
            this.omissionStateBudget = omissionStateBudget;
        }
    }

    /** Host fields plus separate lexical diagnostics; text contains only output. */
    public static final class Candidate {
        public final String text;
        public final String language;
        public final int rank;
        public final int kind;
        public final int nativeScore;
        public final float contextAffinity;
        public final String matchedIpa;
        public final int frequencyScore;
        public final String region;
        public final boolean derived;
        public final int omittedSymbols;

        private Candidate(JSONObject item) throws JSONException {
            text = item.getString("text");
            language = item.getString("language");
            rank = item.getInt("rank");
            kind = item.getInt("kind");
            nativeScore = item.getInt("native_score");
            contextAffinity = (float) item.getDouble("context_affinity");
            matchedIpa = item.getString("matched_ipa");
            frequencyScore = item.getInt("frequency_score");
            region = item.getString("region");
            derived = item.getBoolean("derived");
            omittedSymbols = item.getInt("omitted_symbols");
            if (text.trim().isEmpty() || text.length() > 1000 || !validScalars(text)
                    || language.isEmpty() || rank < 0
                    || (kind != KIND_CONVERSION && kind != KIND_COMPLETION)
                    || !Float.isFinite(contextAffinity) || contextAffinity < 0f || contextAffinity > 1f
                    || frequencyScore < 0 || frequencyScore > 180 || omittedSymbols < 0 || omittedSymbols > 192) {
                throw new JSONException("invalid native candidate");
            }
        }
    }

    /** Diagnostics never form part of committed text or a host error message. */
    public static final class QueryResult {
        public final List<Candidate> candidates;
        public final String status;
        public final double elapsedMs;
        public final boolean exactFound;
        public final boolean truncated;
        public final long matchedKeys;
        public final long matchedRecords;
        public final int exploredStates;

        private QueryResult(List<Candidate> candidates, String status, double elapsedMs,
                            boolean exactFound, boolean truncated, long matchedKeys, long matchedRecords, int exploredStates) {
            this.candidates = Collections.unmodifiableList(new ArrayList<>(candidates));
            this.status = status;
            this.elapsedMs = elapsedMs;
            this.exactFound = exactFound;
            this.truncated = truncated;
            this.matchedKeys = matchedKeys;
            this.matchedRecords = matchedRecords;
            this.exploredStates = exploredStates;
        }

        private static QueryResult empty(String status) {
            return new QueryResult(Collections.emptyList(), status, 0d, false, false, 0L, 0L, 0);
        }

        private static QueryResult fromJson(String serialized, int candidateLimit) throws JSONException {
            JSONObject root = new JSONObject(serialized);
            String status = root.getString("status");
            if (!status.equals("ok") && !status.equals("unsupported_input")
                    && !status.equals("timeout") && !status.equals("error")) {
                throw new JSONException("invalid native status");
            }
            double elapsed = root.getDouble("elapsed_ms");
            long keys = root.getLong("matched_keys");
            long records = root.getLong("matched_records");
            int states = root.getInt("explored_states");
            JSONArray items = root.getJSONArray("candidates");
            if (!Double.isFinite(elapsed) || elapsed < 0d || keys < 0L || records < 0L || states < 0 || states > 200000
                    || items.length() > candidateLimit || (!status.equals("ok") && items.length() != 0)) {
                throw new JSONException("invalid native result");
            }
            List<Candidate> candidates = new ArrayList<>(items.length());
            for (int i = 0; i < items.length(); i++) {
                Candidate candidate = new Candidate(items.getJSONObject(i));
                if (candidate.rank != i) throw new JSONException("invalid candidate rank");
                candidates.add(candidate);
            }
            return new QueryResult(candidates, status, elapsed, root.getBoolean("exact_found"),
                    root.getBoolean("truncated"), keys, records, states);
        }
    }

    private long nativeHandle;
    private Policy policy;
    private Options options;
    private final String pattern;

    /** Initializes an existing complete compiled dictionary, never a SQLite file. */
    public GraphEngine(String dictionaryPath) throws IOException {
        if (LIBRARY_ERROR != null) throw new IOException("native IPA library unavailable", LIBRARY_ERROR);
        if (dictionaryPath == null || dictionaryPath.isEmpty() || !validScalars(dictionaryPath)
                || dictionaryPath.indexOf('\0') >= 0) {
            throw new IOException("invalid dictionary path");
        }
        final long handle;
        try {
            handle = nativeOpen(dictionaryPath);
        } catch (LinkageError | RuntimeException failure) {
            throw new IOException("dictionary initialization failed", failure);
        }
        if (handle == 0L) throw new IOException("dictionary initialization failed");
        String loadedPattern;
        try {
            loadedPattern = nativeSegmentPattern(handle);
            if (loadedPattern == null || loadedPattern.isEmpty()) throw new IOException("invalid segment pattern");
            nativeConfigure(handle, true, true, true, true, true);
        } catch (IOException | RuntimeException | LinkageError failure) {
            nativeClose(handle);
            throw new IOException("dictionary initialization failed", failure);
        }
        nativeHandle = handle;
        pattern = loadedPattern;
        policy = new Policy();
        options = new Options();
    }

    /** Extracts, verifies, and opens the dictionary bundled in this library's assets. */
    public static synchronized GraphEngine openBundled(Context context) throws IOException {
        if (context == null) throw new IOException("Android context is required");
        Context application = context.getApplicationContext();
        if (application == null) application = context;
        AssetManager assets = application.getAssets();
        final String expectedHash;
        final long expectedBytes;
        try (InputStream source = assets.open(MANIFEST_ASSET)) {
            JSONObject manifest = new JSONObject(readManifest(source));
            expectedHash = manifest.getString("sha256").toLowerCase(java.util.Locale.ROOT);
            expectedBytes = manifest.getLong("bytes");
            if (!manifest.getBoolean("complete") || !manifest.getString("status").equals("complete")
                    || !expectedHash.matches("[0-9a-f]{64}")
                    || expectedBytes < 108L || expectedBytes >= MAX_DICTIONARY_BYTES) {
                throw new IOException("invalid bundled dictionary manifest");
            }
        } catch (JSONException invalid) {
            throw new IOException("invalid bundled dictionary manifest", invalid);
        }
        File directory = new File(application.getNoBackupFilesDir(), "ipa-graph");
        if (!directory.isDirectory() && !directory.mkdirs() && !directory.isDirectory()) {
            throw new IOException("cannot create dictionary directory");
        }
        File destination = new File(directory, "lexicon-" + expectedHash + ".ipad");
        if (!destination.isFile() || destination.length() != expectedBytes
                || !hashFile(destination).equals(expectedHash)) {
            File temporary = File.createTempFile("lexicon-", ".part", directory);
            try {
                MessageDigest hash = sha256();
                long copied = 0L;
                try (InputStream source = assets.open(DICTIONARY_ASSET);
                     FileOutputStream target = new FileOutputStream(temporary)) {
                    byte[] buffer = new byte[65536];
                    for (int count; (count = source.read(buffer)) != -1;) {
                        copied += count;
                        if (copied > expectedBytes) throw new IOException("bundled dictionary size mismatch");
                        hash.update(buffer, 0, count);
                        target.write(buffer, 0, count);
                    }
                    target.getFD().sync();
                }
                if (copied != expectedBytes || !hex(hash.digest()).equals(expectedHash)) {
                    throw new IOException("bundled dictionary checksum mismatch");
                }
                try {
                    Files.move(temporary.toPath(), destination.toPath(),
                            StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
                } catch (AtomicMoveNotSupportedException unavailable) {
                    Files.move(temporary.toPath(), destination.toPath(), StandardCopyOption.REPLACE_EXISTING);
                }
            } finally {
                if (temporary.exists()) temporary.delete();
            }
        }
        return new GraphEngine(destination.getAbsolutePath());
    }

    public synchronized void configure(Policy updated) {
        requireOpen();
        if (updated == null) throw new IllegalArgumentException("policy is required");
        nativeConfigure(nativeHandle, updated.enUS, updated.enGB, updated.zhCN, updated.zhTW, updated.jaJP);
        policy = updated;
    }

    public synchronized void configure(Policy updated, Options queryOptions) {
        requireOpen();
        if (queryOptions == null) throw new IllegalArgumentException("options are required");
        configure(updated);
        options = queryOptions;
    }

    public synchronized Policy getPolicy() { requireOpen(); return policy; }
    public synchronized Options getOptions() { requireOpen(); return options; }
    public synchronized String segmentPattern() { requireOpen(); return pattern; }
    public synchronized String getSegmentPattern() { requireOpen(); return pattern; }

    public synchronized boolean acceptsInput(String raw) {
        requireOpen();
        String normalized = normalizeRaw(raw);
        return normalized != null && nativeAcceptsInput(nativeHandle, normalized);
    }

    public synchronized List<Candidate> query(String raw) { return queryDetailed(raw, "", "").candidates; }
    public synchronized List<Candidate> query(String raw, String beforeCursor) {
        return queryDetailed(raw, beforeCursor, "").candidates;
    }
    public synchronized List<Candidate> query(String raw, String beforeCursor, String afterCursor) {
        return queryDetailed(raw, beforeCursor, afterCursor).candidates;
    }
    public synchronized QueryResult queryDetailed(String raw) { return queryDetailed(raw, "", ""); }

    public synchronized QueryResult queryDetailed(String raw, String beforeCursor, String afterCursor) {
        requireOpen();
        String normalized = normalizeRaw(raw);
        if (normalized == null) return QueryResult.empty("unsupported_input");
        if (normalized.isEmpty()) return QueryResult.empty("ok"); // Prediction is not implemented.
        try {
            String serialized = nativeQuery(nativeHandle, normalized,
                    beforeCursor == null ? "" : beforeCursor, afterCursor == null ? "" : afterCursor,
                    options.candidateLimit, options.prefix, options.deadlineMs, options.prefixKeyBudget,
                    options.allowOmissions, options.omissionStateBudget);
            if (serialized == null) return QueryResult.empty("error");
            return QueryResult.fromJson(serialized, options.candidateLimit);
        } catch (JSONException | RuntimeException failure) {
            return QueryResult.empty("error");
        }
    }

    @Override public synchronized void close() {
        if (nativeHandle != 0L) {
            long old = nativeHandle;
            nativeHandle = 0L;
            nativeClose(old);
        }
    }

    private void requireOpen() {
        if (nativeHandle == 0L) throw new IllegalStateException("IPA engine is closed");
    }

    private static String normalizeRaw(String raw) {
        if (raw == null || raw.length() > 64 || !validScalars(raw)) return null;
        String normalized = Normalizer.normalize(raw, Normalizer.Form.NFC);
        return normalized.length() <= 64 ? normalized : null;
    }

    private static boolean validScalars(String value) {
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (Character.isHighSurrogate(c)) {
                if (++i >= value.length() || !Character.isLowSurrogate(value.charAt(i))) return false;
            } else if (Character.isLowSurrogate(c)) return false;
        }
        return true;
    }

    private static String readManifest(InputStream source) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] block = new byte[8192];
        for (int count; (count = source.read(block)) != -1;) {
            if (buffer.size() + count > 1024 * 1024) throw new IOException("manifest is too large");
            buffer.write(block, 0, count);
        }
        return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
    }

    private static MessageDigest sha256() throws IOException {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException impossible) { throw new IOException("SHA-256 is unavailable", impossible); }
    }

    private static String hashFile(File file) throws IOException {
        MessageDigest hash = sha256();
        try (InputStream source = new FileInputStream(file)) {
            byte[] buffer = new byte[65536];
            for (int count; (count = source.read(buffer)) != -1;) hash.update(buffer, 0, count);
        }
        return hex(hash.digest());
    }

    private static String hex(byte[] bytes) {
        char[] result = new char[bytes.length * 2];
        final char[] digits = "0123456789abcdef".toCharArray();
        for (int i = 0; i < bytes.length; i++) {
            result[i * 2] = digits[(bytes[i] & 0xff) >>> 4];
            result[i * 2 + 1] = digits[bytes[i] & 15];
        }
        return new String(result);
    }

    private static native long nativeOpen(String path) throws IOException;
    private static native void nativeConfigure(long handle, boolean enUS, boolean enGB,
                                               boolean zhCN, boolean zhTW, boolean jaJP);
    private static native boolean nativeAcceptsInput(long handle, String raw);
    private static native String nativeSegmentPattern(long handle);
    private static native String nativeQuery(long handle, String raw, String before, String after,
                                            int limit, boolean prefix, int deadlineMs, int prefixBudget,
                                            boolean allowOmissions, int omissionBudget);
    private static native void nativeClose(long handle);
}
