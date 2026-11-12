// Copyright (c) 2026 PantingEYEs. All rights reserved.
// Independently authored read-only adapter; no project license has been selected.
// Upstream LatinIME files retain their own Apache-2.0 notices.
#include <jni.h>
#include "build_identity.h"
#include "dictionary/property/ngram_context.h"
#include "dictionary/structure/dictionary_structure_with_buffer_policy_factory.h"
#include "suggest/core/dictionary/dictionary.h"
#include "suggest/core/layout/proximity_info.h"
#include "suggest/core/result/suggestion_results.h"
#include "suggest/core/session/dic_traverse_session.h"
#include "suggest/core/suggest_options.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <fstream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <utility>
#include <vector>

static_assert(IPA_LATINIME_ABI_VERSION == 1 && IPA_LATINIME_PACKET_VERSION == 1,
              "Adapter must be reviewed when its interface version changes");
static_assert(MAX_WORD_LENGTH == 48 && MAX_RESULTS == 18 && MAX_PREV_WORD_COUNT_FOR_N_GRAM == 3,
              "Upstream limits changed");

namespace {
struct Failure : std::exception {
  const char* code;
  bool argument;
  explicit Failure(const char* value, bool invalid = false) : code(value), argument(invalid) {}
  const char* what() const noexcept override { return code; }
};
struct PendingJavaException {};
[[noreturn]] void invalid() { throw Failure("IPA_LATINIME_INVALID_ARGUMENT", true); }
void pending(JNIEnv* env) { if (env->ExceptionCheck()) throw PendingJavaException{}; }

template <class T> class LocalRef {
 public:
  LocalRef(JNIEnv* env, T value) : env_(env), value_(value) {
    if (!value_) { pending(env_); throw std::bad_alloc(); }
  }
  ~LocalRef() { env_->DeleteLocalRef(value_); }
  LocalRef(const LocalRef&) = delete;
  LocalRef& operator=(const LocalRef&) = delete;
  T get() const { return value_; }
 private:
  JNIEnv* env_;
  T value_;
};

std::vector<int> codePoints(const std::string& input) {
  std::vector<int> result;
  for (std::size_t offset = 0; offset < input.size();) {
    const auto first = static_cast<unsigned char>(input[offset++]);
    std::uint32_t value = 0, minimum = 0;
    unsigned tails = 0;
    if (first == 0) invalid();
    if (first < 0x80) value = first;
    else if (first >= 0xc2 && first <= 0xdf) { value = first & 0x1f; tails = 1; minimum = 0x80; }
    else if (first >= 0xe0 && first <= 0xef) { value = first & 0x0f; tails = 2; minimum = 0x800; }
    else if (first >= 0xf0 && first <= 0xf4) { value = first & 7; tails = 3; minimum = 0x10000; }
    else invalid();
    if (tails > input.size() - offset) invalid();
    while (tails-- != 0) {
      const auto next = static_cast<unsigned char>(input[offset++]);
      if ((next & 0xc0) != 0x80) invalid();
      value = (value << 6) | (next & 0x3f);
    }
    if (value < minimum || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) invalid();
    result.push_back(static_cast<int>(value));
  }
  return result;
}

std::string bytes(JNIEnv* env, jbyteArray array, std::size_t maximum, bool allowEmpty) {
  if (!array) invalid();
  const auto length = env->GetArrayLength(array);
  if (length < 0 || static_cast<std::size_t>(length) > maximum || (!allowEmpty && length == 0)) invalid();
  std::string value(static_cast<std::size_t>(length), '\0');
  if (length) env->GetByteArrayRegion(array, 0, length, reinterpret_cast<jbyte*>(value.data()));
  pending(env);
  codePoints(value);
  return value;
}

std::string utf8(const int* values, std::size_t length) {
  std::string result;
  for (std::size_t i = 0; i != length; ++i) {
    const auto cp = static_cast<std::uint32_t>(values[i]);
    if (cp == 0 || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff))
      throw Failure("IPA_LATINIME_INVALID_NATIVE_TEXT");
    if (cp < 0x80) result.push_back(static_cast<char>(cp));
    else if (cp < 0x800) {
      result.push_back(static_cast<char>(0xc0 | (cp >> 6)));
      result.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
    } else if (cp < 0x10000) {
      result.push_back(static_cast<char>(0xe0 | (cp >> 12)));
      result.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
      result.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
    } else {
      result.push_back(static_cast<char>(0xf0 | (cp >> 18)));
      result.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3f)));
      result.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
      result.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
    }
  }
  return result;
}

jbyteArray output(JNIEnv* env, const std::vector<unsigned char>& value) {
  if (value.size() > 16384) throw Failure("IPA_LATINIME_OUTPUT_TOO_LARGE");
  auto result = env->NewByteArray(static_cast<jsize>(value.size()));
  if (!result) { pending(env); throw std::bad_alloc(); }
  if (!value.empty()) env->SetByteArrayRegion(result, 0, static_cast<jsize>(value.size()),
      reinterpret_cast<const jbyte*>(value.data()));
  pending(env);
  return result;
}

latinime::NgramContext context(JNIEnv* env, jobjectArray words, bool beginning) {
  if (!words) invalid();
  const auto count = env->GetArrayLength(words);
  if (count < 0 || count > MAX_PREV_WORD_COUNT_FOR_N_GRAM || (beginning && count != 0)) invalid();
  int previous[MAX_PREV_WORD_COUNT_FOR_N_GRAM][MAX_WORD_LENGTH]{};
  int lengths[MAX_PREV_WORD_COUNT_FOR_N_GRAM]{};
  bool flags[MAX_PREV_WORD_COUNT_FOR_N_GRAM]{};
  for (jsize index = 0; index != count; ++index) {
    const auto value = static_cast<jbyteArray>(env->GetObjectArrayElement(words, index));
    pending(env);
    if (!value) invalid();
    LocalRef<jbyteArray> word(env, value);
    const auto points = codePoints(bytes(env, word.get(), 4 * (MAX_WORD_LENGTH - 1), false));
    if (points.size() >= MAX_WORD_LENGTH) invalid();
    lengths[index] = static_cast<int>(points.size());
    std::copy(points.begin(), points.end(), previous[index]);
  }
  flags[0] = beginning;
  return latinime::NgramContext(previous, lengths, flags, beginning ? 1 : count);
}

std::unique_ptr<latinime::ProximityInfo> proximity(JNIEnv* env) {
  auto array = [env](const std::vector<jint>& values) {
    auto result = env->NewIntArray(static_cast<jsize>(values.size()));
    if (!result) { pending(env); throw std::bad_alloc(); }
    env->SetIntArrayRegion(result, 0, static_cast<jsize>(values.size()), values.data());
    pending(env);
    return result;
  };
  std::vector<jint> keys(27), xs(27);
  for (int i = 0; i != 27; ++i) { keys[i] = i < 26 ? 'a' + i : '\''; xs[i] = i * 100; }
  LocalRef<jintArray> chars(env, array(std::vector<jint>(16, -1)));
  LocalRef<jintArray> keyCodes(env, array(keys));
  LocalRef<jintArray> keyXs(env, array(xs));
  LocalRef<jintArray> zeroes(env, array(std::vector<jint>(27, 0)));
  LocalRef<jintArray> sizes(env, array(std::vector<jint>(27, 100)));
  auto result = std::make_unique<latinime::ProximityInfo>(env, 2700, 100, 1, 1, 100, 100,
      chars.get(), 27, keyXs.get(), zeroes.get(), sizes.get(), sizes.get(), keyCodes.get(),
      nullptr, nullptr, nullptr);
  pending(env);
  return result;
}

class Owner {
 public:
  Owner(JNIEnv* env, const std::string& path) {
    struct stat info{};
    if (::stat(path.c_str(), &info) != 0 || !S_ISREG(info.st_mode) || info.st_size < 12 ||
        info.st_size > std::numeric_limits<int>::max())
      throw Failure("IPA_LATINIME_DICTIONARY_UNAVAILABLE");
    // Only the immutable file-based v202 format is accepted, never writable directory dictionaries.
    std::array<unsigned char, 12> header{};
    std::ifstream stream(path, std::ios::binary);
    if (!stream.read(reinterpret_cast<char*>(header.data()), header.size()))
      throw Failure("IPA_LATINIME_DICTIONARY_UNAVAILABLE");
    const std::uint32_t headerSize = (static_cast<std::uint32_t>(header[8]) << 24) |
        (static_cast<std::uint32_t>(header[9]) << 16) | (static_cast<std::uint32_t>(header[10]) << 8) | header[11];
    if (header[0] != 0x9b || header[1] != 0xc1 || header[2] != 0x3a || header[3] != 0xfe ||
        header[4] != 0 || header[5] != 202 || headerSize < 12 ||
        headerSize > static_cast<std::uint64_t>(info.st_size))
      throw Failure("IPA_LATINIME_DICTIONARY_FORMAT");
    auto policy = latinime::DictionaryStructureWithBufferPolicyFactory::newPolicyForExistingDictFile(
        path.c_str(), 0, static_cast<int>(info.st_size), false);
    if (!policy) throw Failure("IPA_LATINIME_DICTIONARY_UNAVAILABLE");
    dictionary_ = std::make_unique<latinime::Dictionary>(env, std::move(policy));
    pending(env);
    proximity_ = proximity(env);
  }

  std::vector<unsigned char> query(JNIEnv* env, const std::string& input,
                                   const latinime::NgramContext& previous) {
    latinime::SuggestionResults results(MAX_RESULTS);
    if (input.empty()) dictionary_->getPredictions(&previous, &results);
    else {
      std::array<int, MAX_WORD_LENGTH> codes, xs, ys, times{}, pointers{};
      codes.fill(-1); xs.fill(-1); ys.fill(-1);
      for (std::size_t i = 0; i != input.size(); ++i) codes[i] = static_cast<unsigned char>(input[i]);
      const int values[] = {0, 1, 0, 0, 1000};
      const latinime::SuggestOptions options(values, 5);
      // The pinned constructor does not retain locale/JNIEnv. A fresh cache belongs to this call.
      LocalRef<jstring> locale(env, env->NewStringUTF("en_US"));
      latinime::DicTraverseSession traversal(env, locale.get(), false);
      dictionary_->getSuggestions(proximity_.get(), &traversal, xs.data(), ys.data(), times.data(),
          pointers.data(), codes.data(), static_cast<int>(input.size()), &previous, &options, -1.0f, &results);
    }
    pending(env);
    if (results.getSuggestionCount() < 0 || results.getSuggestionCount() > MAX_RESULTS)
      throw Failure("IPA_LATINIME_INVALID_NATIVE_RESULT");
    LocalRef<jintArray> count(env, env->NewIntArray(1));
    LocalRef<jintArray> codeArray(env, env->NewIntArray(MAX_RESULTS * MAX_WORD_LENGTH));
    LocalRef<jintArray> scores(env, env->NewIntArray(MAX_RESULTS));
    LocalRef<jintArray> indices(env, env->NewIntArray(MAX_RESULTS));
    LocalRef<jintArray> types(env, env->NewIntArray(MAX_RESULTS));
    LocalRef<jintArray> confidence(env, env->NewIntArray(1));
    LocalRef<jfloatArray> weight(env, env->NewFloatArray(1));
    results.outputSuggestions(env, count.get(), codeArray.get(), scores.get(), indices.get(),
        types.get(), confidence.get(), weight.get());
    pending(env);
    jint found = 0;
    std::array<jint, MAX_RESULTS * MAX_WORD_LENGTH> codeValues{};
    std::array<jint, MAX_RESULTS> scoreValues{}, typeValues{};
    env->GetIntArrayRegion(count.get(), 0, 1, &found);
    if (found < 0 || found > MAX_RESULTS) throw Failure("IPA_LATINIME_INVALID_NATIVE_RESULT");
    env->GetIntArrayRegion(codeArray.get(), 0, codeValues.size(), codeValues.data());
    env->GetIntArrayRegion(scores.get(), 0, found, scoreValues.data());
    env->GetIntArrayRegion(types.get(), 0, found, typeValues.data());
    pending(env);
    std::vector<std::string> texts;
    std::size_t total = 24 + static_cast<std::size_t>(found) * 16;
    for (int i = 0; i != found; ++i) {
      const int* word = codeValues.data() + i * MAX_WORD_LENGTH;
      const auto end = std::find(word, word + MAX_WORD_LENGTH, 0);
      if (end == word) throw Failure("IPA_LATINIME_INVALID_NATIVE_RESULT");
      texts.push_back(utf8(word, end - word));
      total += texts.back().size();
    }
    if (total > 16384) throw Failure("IPA_LATINIME_OUTPUT_TOO_LARGE");
    std::vector<unsigned char> packet(total);
    auto put = [&](std::size_t position, std::uint32_t value) {
      for (unsigned b = 0; b != 4; ++b) packet.at(position + b) = static_cast<unsigned char>(value >> (8 * b));
    };
    put(0, 0x4c41544e); put(4, 1); put(8, found); put(12, total); put(16, 16); put(20, 0);
    std::size_t offset = 24 + found * 16;
    for (int i = 0; i != found; ++i) {
      const auto entry = 24 + i * 16;
      put(entry, static_cast<std::uint32_t>(scoreValues[i]));
      put(entry + 4, static_cast<std::uint32_t>(typeValues[i]));
      put(entry + 8, offset); put(entry + 12, texts[i].size());
      std::copy(texts[i].begin(), texts[i].end(), packet.begin() + offset);
      offset += texts[i].size();
    }
    return packet;
  }

  int probability(const latinime::NgramContext& previous, const std::vector<int>& word) {
    return dictionary_->getNgramProbability(&previous, latinime::CodePointArrayView(word.data(), word.size()));
  }
 private:
  std::unique_ptr<latinime::Dictionary> dictionary_;
  std::unique_ptr<latinime::ProximityInfo> proximity_;
};

std::mutex gate;
std::map<jlong, std::unique_ptr<Owner>> owners;
jlong nextHandle = 1;
Owner& owner(jlong handle) {
  const auto found = owners.find(handle);
  if (handle <= 0 || found == owners.end()) throw Failure("IPA_LATINIME_INVALID_HANDLE");
  return *found->second;
}
void javaError(JNIEnv* env, const char* type, const char* code) noexcept {
  if (env->ExceptionCheck()) return;
  auto exception = env->FindClass(type);
  if (exception) { env->ThrowNew(exception, code); env->DeleteLocalRef(exception); }
}
template <typename Result, typename Body>
Result invoke(JNIEnv* env, Result fallback, Body&& body) noexcept {
  try { std::lock_guard<std::mutex> lock(gate); return body(); }
  catch (const PendingJavaException&) {}
  catch (const Failure& failure) {
    javaError(env, failure.argument ? "java/lang/IllegalArgumentException" : "java/lang/IllegalStateException", failure.code);
  } catch (const std::bad_alloc&) { javaError(env, "java/lang/OutOfMemoryError", "IPA_LATINIME_ALLOCATION_FAILED"); }
  catch (...) { javaError(env, "java/lang/IllegalStateException", "IPA_LATINIME_NATIVE_FAILURE"); }
  return fallback;
}
} // namespace

extern "C" JNIEXPORT jint JNICALL Java_org_ipaboard_latinime_LatinImeNative_abiVersion(JNIEnv* env, jclass) {
  return invoke<jint>(env, 0, [] { return IPA_LATINIME_ABI_VERSION; });
}
extern "C" JNIEXPORT jbyteArray JNICALL Java_org_ipaboard_latinime_LatinImeNative_identity(JNIEnv* env, jclass) {
  return invoke<jbyteArray>(env, nullptr, [&] {
    const std::string identity = "ipa-latinime;abi=1;packet=1;heliboard=4.1;source=" IPA_LATINIME_SOURCE_REVISION
        ";dictionary=" IPA_LATINIME_DICTIONARY_SHA256 ";build=" IPA_LATINIME_BUILD_ID;
    return output(env, std::vector<unsigned char>(identity.begin(), identity.end()));
  });
}
extern "C" JNIEXPORT jlong JNICALL Java_org_ipaboard_latinime_LatinImeNative_open(JNIEnv* env, jclass, jbyteArray path) {
  return invoke<jlong>(env, 0, [&] {
    const auto name = bytes(env, path, 4096, false);
    if (name.front() != '/') invalid();
    if (owners.size() >= 8) throw Failure("IPA_LATINIME_OWNER_LIMIT");
    if (nextHandle == std::numeric_limits<jlong>::max()) throw Failure("IPA_LATINIME_HANDLE_EXHAUSTED");
    auto created = std::make_unique<Owner>(env, name);
    const auto handle = nextHandle++;
    owners.emplace(handle, std::move(created));
    return handle;
  });
}
extern "C" JNIEXPORT jbyteArray JNICALL Java_org_ipaboard_latinime_LatinImeNative_query(JNIEnv* env, jclass,
    jlong handle, jbyteArray input, jobjectArray previousWords, jboolean beginning) {
  return invoke<jbyteArray>(env, nullptr, [&] {
    auto& runtime = owner(handle);
    auto spelling = bytes(env, input, MAX_WORD_LENGTH - 1, true);
    for (auto& ch : spelling) {
      if (ch >= 'A' && ch <= 'Z') ch += 'a' - 'A';
      if (!((ch >= 'a' && ch <= 'z') || ch == '\'')) invalid();
    }
    const auto previous = context(env, previousWords, beginning == JNI_TRUE);
    return output(env, runtime.query(env, spelling, previous));
  });
}
extern "C" JNIEXPORT jint JNICALL Java_org_ipaboard_latinime_LatinImeNative_probability(JNIEnv* env, jclass,
    jlong handle, jobjectArray previousWords, jboolean beginning, jbyteArray word) {
  return invoke<jint>(env, -1, [&] {
    auto& runtime = owner(handle);
    const auto points = codePoints(bytes(env, word, MAX_WORD_LENGTH * 4, false));
    if (points.size() > MAX_WORD_LENGTH) invalid();
    const auto previous = context(env, previousWords, beginning == JNI_TRUE);
    return runtime.probability(previous, points);
  });
}
extern "C" JNIEXPORT void JNICALL Java_org_ipaboard_latinime_LatinImeNative_close(JNIEnv* env, jclass, jlong handle) {
  invoke<int>(env, 0, [&] { owner(handle); owners.erase(handle); return 1; });
}
