/* Copyright (c) 2026 PantingEYEs. All rights reserved.
 * No open-source license has been granted for this project-authored API.
 */
package org.ipaboard.latinime;

/** Owned, read-only LatinIME interface. All byte arrays use NUL-free standard UTF-8.
 * Calls are serialized natively, including query/close races. Handles are opaque
 * monotonically increasing IDs; stale/invalid handles throw, never address memory.
 */
public final class LatinImeNative {
    public static final int ABI_VERSION = 1;
    public static final int PACKET_VERSION = 1;
    public static final int MAX_INPUT_LENGTH = 47;
    public static final int MAX_RESULTS = 18;
    static {
        System.loadLibrary("ipa_latinime");
        if (abiVersion() != ABI_VERSION) throw new LinkageError("IPA_LATINIME_ABI_MISMATCH");
    }
    private LatinImeNative() {}
    public static native int abiVersion();
    public static native byte[] identity();

    /** Opens an existing regular v202 dictionary file read-only; at most 8 owners.
     * Caller must verify the dictionary hash against the recorded immutable artifact
     * before opening. No learning, update, save, migration or flush API is exposed.
     */
    public static native long open(byte[] absoluteDictionaryPath);

    /** Input: 0..47 ASCII letters/apostrophes, case folded to lowercase internally.
     * Empty input requests dictionary predictions. Previous words are most recent
     * first, at most 3, each 1..47 Unicode code points. beginningOfSentence requires
     * an empty previous-word array and creates the native BOS context.
     * A fresh traversal cache is used for each nonempty input.
     * Returns an owned little-endian LATN v1 packet, max 18 results. Sorting and
     * correction/completion filtering remain frontend policy.
     */
    public static native byte[] query(long owner, byte[] input, byte[][] previousWords,
                                      boolean beginningOfSentence);

    /** Returns a dictionary probability, or -1 when unknown. Word is 1..48 code points.
     * Empty previous words with beginningOfSentence=false request unigram probability.
     */
    public static native int probability(long owner, byte[][] previousWords,
                                         boolean beginningOfSentence, byte[] word);

    /** Releases the dictionary mapping and proximity resources; repeated close fails. */
    public static native void close(long owner);
}
