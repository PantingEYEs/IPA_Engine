/* Copyright (c) 2026 PantingEYEs. All rights reserved.
 * No open-source license is granted for this project-authored adapter.
 */
package org.ipaboard.rime;

/**
 * The versioned boundary of the source-built IPA Rime/OpenCC runtime.
 *
 * All byte arrays use standard UTF-8, never JNI modified UTF-8. Paths must be
 * absolute and NUL-free. One owner is allowed per process. Calls are serialized
 * natively; callers should use a background worker and close the owner explicitly.
 * No Rime session pointer or native allocation address is exposed to Java.
 */
public final class RimeNative {
    public static final int ABI_VERSION = 1;
    public static final int PACKET_VERSION = 3;
    public static final int TO_SIMPLIFIED = 0;
    public static final int TO_TRADITIONAL = 1;

    static {
        System.loadLibrary("ipa_rime");
        if (abiVersion() != ABI_VERSION) {
            throw new LinkageError("IPA_RIME_ABI_MISMATCH");
        }
    }
    private RimeNative() {}

    /** The class initializer enforces ABI_VERSION before any owner can be opened. */
    public static native int abiVersion();

    /** UTF-8 build identity, including runtime versions and the build identifier. */
    public static native byte[] identity();

    /**
     * Initializes Rime, completes maintenance, and opens both OpenCC converters.
     * Resource files and schema patches must already be deployed by the caller.
     * Schema patches must disable personal learning and completion as required by
     * IPA Board. A second simultaneous open is rejected.
     */
    public static native long open(byte[] sharedDirectory, byte[] userDirectory,
                                   byte[] traditionalToSimplifiedConfig,
                                   byte[] simplifiedToTraditionalConfig);

    /**
     * Processes one nonempty ASCII-encoded spelling (maximum 256 bytes) once,
     * then returns an owned packet with up to 40 candidates and native byte ranges.
     * Allowed schema IDs: luna_pinyin, terra_pinyin, bopomofo.
     * The packet is little-endian RIME v3; status 2 means the retained native input
     * cannot safely cover the supplied spelling and must yield no conversions.
     * Input and pending commits are discarded after the snapshot. No candidate is
     * selected or committed by enumeration. Failure drops only the affected session.
     */
    public static native byte[] query(long handle, byte[] schema, byte[] spelling, int limit);

    /** Converts at most 1 MiB of valid, NUL-free UTF-8 text. Empty input is valid. */
    public static native byte[] convert(long handle, byte[] input, int direction);

    /** Closes both converters and the Rime runtime; stale/repeated handles fail. */
    public static native void close(long handle);
}