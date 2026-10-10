#include "ipa_graph_rust.h"

#include <jni.h>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void throw_java(JNIEnv* env, const char* name, const char* message) {
    if (env->ExceptionCheck()) return;
    jclass type = env->FindClass(name);
    if (type != nullptr) {
        env->ThrowNew(type, message); // These diagnostics are ASCII, not candidate text.
        env->DeleteLocalRef(type);
    }
}

class StringChars final {
public:
    StringChars(JNIEnv* env, jstring value) : env_(env), value_(value) {
        if (value_ != nullptr) {
            size_ = env_->GetStringLength(value_);
            chars_ = env_->GetStringChars(value_, nullptr);
        }
    }
    ~StringChars() { if (chars_ != nullptr) env_->ReleaseStringChars(value_, chars_); }
    StringChars(const StringChars&) = delete;
    StringChars& operator=(const StringChars&) = delete;
    const jchar* data() const { return chars_; }
    jsize size() const { return size_; }
private:
    JNIEnv* env_;
    jstring value_;
    const jchar* chars_ = nullptr;
    jsize size_ = 0;
};

// JNI's modified UTF-8 encodes supplementary characters as surrogate pairs.
// The graph format instead requires standard Unicode UTF-8, so convert UTF-16.
bool from_java(JNIEnv* env, jstring input, std::string& output) {
    if (input == nullptr) return false;
    StringChars value(env, input);
    if (value.data() == nullptr) return false;
    output.clear();
    output.reserve(static_cast<std::size_t>(value.size()));
    for (jsize i = 0; i < value.size(); ++i) {
        std::uint32_t cp = value.data()[i];
        if (cp >= 0xd800 && cp <= 0xdbff) {
            if (++i >= value.size()) return false;
            const std::uint32_t low = value.data()[i];
            if (low < 0xdc00 || low > 0xdfff) return false;
            cp = 0x10000 + ((cp - 0xd800) << 10) + low - 0xdc00;
        } else if (cp >= 0xdc00 && cp <= 0xdfff) return false;
        if (cp < 0x80) output.push_back(static_cast<char>(cp));
        else if (cp < 0x800) {
            output.push_back(static_cast<char>(0xc0 | (cp >> 6)));
            output.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
        } else if (cp < 0x10000) {
            output.push_back(static_cast<char>(0xe0 | (cp >> 12)));
            output.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
            output.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
        } else {
            output.push_back(static_cast<char>(0xf0 | (cp >> 18)));
            output.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3f)));
            output.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
            output.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
        }
    }
    return true;
}

// Validate standard UTF-8 and build a real UTF-16 jstring. Never use NewStringUTF
// for IPA, output words, JSON, or paths that may include non-BMP characters.
jstring to_java(JNIEnv* env, const std::string& text) {
    std::vector<jchar> units;
    units.reserve(text.size());
    for (std::size_t i = 0; i < text.size();) {
        const auto lead = static_cast<unsigned char>(text[i++]);
        std::uint32_t cp = 0;
        unsigned remaining = 0;
        std::uint32_t minimum = 0;
        if (lead < 0x80) cp = lead;
        else if (lead >= 0xc2 && lead <= 0xdf) { cp = lead & 0x1f; remaining = 1; minimum = 0x80; }
        else if (lead >= 0xe0 && lead <= 0xef) { cp = lead & 0x0f; remaining = 2; minimum = 0x800; }
        else if (lead >= 0xf0 && lead <= 0xf4) { cp = lead & 7; remaining = 3; minimum = 0x10000; }
        else throw std::runtime_error("invalid native UTF-8");
        if (remaining > text.size() - i) throw std::runtime_error("truncated native UTF-8");
        for (unsigned j = 0; j < remaining; ++j) {
            const auto byte = static_cast<unsigned char>(text[i++]);
            if ((byte & 0xc0) != 0x80) throw std::runtime_error("invalid native UTF-8 continuation");
            cp = (cp << 6) | (byte & 0x3f);
        }
        if (cp < minimum || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) {
            throw std::runtime_error("invalid native Unicode scalar");
        }
        if (cp < 0x10000) units.push_back(static_cast<jchar>(cp));
        else {
            cp -= 0x10000;
            units.push_back(static_cast<jchar>(0xd800 + (cp >> 10)));
            units.push_back(static_cast<jchar>(0xdc00 + (cp & 0x3ff)));
        }
    }
    if (units.size() > static_cast<std::size_t>(std::numeric_limits<jsize>::max())) {
        throw std::runtime_error("native string too large");
    }
    const jchar empty = 0;
    return env->NewString(units.empty() ? &empty : units.data(), static_cast<jsize>(units.size()));
}

IpaGraphEngine* engine(JNIEnv* env, jlong handle) {
    if (handle == 0) {
        throw_java(env, "java/lang/IllegalStateException", "IPA engine is closed");
        return nullptr;
    }
    return reinterpret_cast<IpaGraphEngine*>(static_cast<std::uintptr_t>(handle));
}

class RustString final {
public:
    ~RustString() { ipa_graph_string_free(value_); }
    RustString() = default;
    RustString(const RustString&) = delete;
    RustString& operator=(const RustString&) = delete;
    char** output() { return &value_; }
    const char* get() const { return value_; }
private:
    char* value_ = nullptr;
};

jstring empty_result(JNIEnv* env, bool unsupported) {
    RustString output;
    if (ipa_graph_empty_result(unsupported ? 1 : 0, output.output()) != IPA_GRAPH_OK || output.get() == nullptr) {
        throw std::runtime_error("IPA result serialization failed");
    }
    return to_java(env, output.get());
}
} // namespace

extern "C" JNIEXPORT jlong JNICALL
Java_com_ipaengine_graph_GraphEngine_nativeOpen(JNIEnv* env, jclass, jstring path) {
    try {
        std::string filename;
        if (!from_java(env, path, filename) || filename.empty() || filename.find('\0') != std::string::npos) {
            throw_java(env, "java/io/IOException", "invalid IPA dictionary path");
            return 0;
        }
        IpaGraphEngine* value = nullptr;
        RustString diagnostic;
        const auto code = ipa_graph_open(reinterpret_cast<const uint8_t*>(filename.data()),
                filename.size(), &value, diagnostic.output());
        if (code != IPA_GRAPH_OK || value == nullptr) {
            throw_java(env, "java/io/IOException", "IPA dictionary initialization failed");
            return 0;
        }
        return static_cast<jlong>(reinterpret_cast<std::uintptr_t>(value));
    } catch (...) {
        throw_java(env, "java/io/IOException", "IPA dictionary initialization failed");
        return 0;
    }
}

extern "C" JNIEXPORT void JNICALL
Java_com_ipaengine_graph_GraphEngine_nativeConfigure(JNIEnv* env, jclass, jlong handle,
        jboolean en_us, jboolean en_gb, jboolean zh_cn, jboolean zh_tw, jboolean ja_jp) {
    try {
        auto* value = engine(env, handle);
        const uint32_t mask = (en_us ? 1u : 0u) | (en_gb ? 2u : 0u) | (zh_cn ? 4u : 0u)
                | (zh_tw ? 8u : 0u) | (ja_jp ? 16u : 0u);
        if (value != nullptr && ipa_graph_configure(value, mask) != IPA_GRAPH_OK) {
            throw_java(env, "java/lang/IllegalStateException", "IPA configuration failed");
        }
    } catch (...) {
        throw_java(env, "java/lang/IllegalStateException", "IPA configuration failed");
    }
}

extern "C" JNIEXPORT jboolean JNICALL
Java_com_ipaengine_graph_GraphEngine_nativeAcceptsInput(JNIEnv* env, jclass, jlong handle, jstring raw) {
    try {
        auto* value = engine(env, handle);
        std::string input;
        if (value == nullptr || !from_java(env, raw, input)) return JNI_FALSE;
        uint32_t accepts = 0;
        return ipa_graph_accepts_input(value, reinterpret_cast<const uint8_t*>(input.data()),
                input.size(), &accepts) == IPA_GRAPH_OK && accepts != 0 ? JNI_TRUE : JNI_FALSE;
    } catch (...) { return JNI_FALSE; }
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_ipaengine_graph_GraphEngine_nativeSegmentPattern(JNIEnv* env, jclass, jlong handle) {
    try {
        auto* value = engine(env, handle);
        if (value == nullptr) return nullptr;
        RustString output;
        if (ipa_graph_segment_pattern(value, output.output()) != IPA_GRAPH_OK || output.get() == nullptr) {
            throw std::runtime_error("IPA segment pattern unavailable");
        }
        return to_java(env, output.get());
    } catch (...) {
        throw_java(env, "java/lang/IllegalStateException", "IPA segment pattern unavailable");
        return nullptr;
    }
}

extern "C" JNIEXPORT jstring JNICALL
Java_com_ipaengine_graph_GraphEngine_nativeQuery(JNIEnv* env, jclass, jlong handle, jstring raw,
        jstring before, jstring after, jint limit, jboolean prefix, jint deadline_ms, jint prefix_budget,
        jboolean allow_omissions, jint omission_budget) {
    try {
        auto* value = engine(env, handle);
        if (value == nullptr) return nullptr;
        std::string input;
        if (!from_java(env, raw, input)) {
            if (env->ExceptionCheck()) return nullptr;
            return empty_result(env, true);
        }
        // Context belongs to the stable host signature. Ordering remains lexical.
        (void) before;
        (void) after;
        const IpaGraphQueryOptions options = {
            limit < 0 ? 0u : static_cast<uint32_t>(limit), prefix ? 1u : 0u,
            prefix_budget < 1 ? 1u : static_cast<uint32_t>(prefix_budget),
            deadline_ms < 1 ? 1u : static_cast<uint32_t>(deadline_ms),
            allow_omissions ? 1u : 0u,
            omission_budget < 1 ? 1u : static_cast<uint32_t>(omission_budget)
        };
        RustString output;
        if (ipa_graph_query(value, reinterpret_cast<const uint8_t*>(input.data()), input.size(),
                &options, output.output()) != IPA_GRAPH_OK || output.get() == nullptr) {
            return empty_result(env, false);
        }
        return to_java(env, output.get());
    } catch (...) {
        if (env->ExceptionCheck()) return nullptr;
        try { return empty_result(env, false); }
        catch (...) {
            throw_java(env, "java/lang/IllegalStateException", "IPA query serialization failed");
            return nullptr;
        }
    }
}

extern "C" JNIEXPORT void JNICALL
Java_com_ipaengine_graph_GraphEngine_nativeClose(JNIEnv* env, jclass, jlong handle) {
    try {
        // Java serializes release with every operation and clears the handle.
        auto* value = reinterpret_cast<IpaGraphEngine*>(static_cast<std::uintptr_t>(handle));
        if (ipa_graph_close(value) != IPA_GRAPH_OK) {
            throw_java(env, "java/lang/IllegalStateException", "IPA engine release failed");
        }
    } catch (...) {
        throw_java(env, "java/lang/IllegalStateException", "IPA engine release failed");
    }
}
