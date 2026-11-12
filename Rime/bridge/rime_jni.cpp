// Copyright (c) 2026 PantingEYEs. All rights reserved.
// No open-source license is granted for this project-authored adapter.
// Upstream Rime and OpenCC retain their separate licenses.

#include <jni.h>
#include <rime_api.h>
#include <rime/candidate.h>
#include <rime/context.h>
#include <rime/menu.h>
#include <rime/service.h>
#include <opencc.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <new>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

#ifndef IPA_RIME_BUILD_ID
#error "IPA_RIME_BUILD_ID must identify the locked sources and adapter"
#endif
#ifndef IPA_OPENCC_VERSION
#error "IPA_OPENCC_VERSION must identify the linked OpenCC runtime"
#endif

namespace {
constexpr jint kInterfaceVersion = 1;
constexpr std::size_t kInputLimit = 256;
constexpr std::size_t kCandidateLimit = 40;
constexpr std::size_t kTextLimit = 16384;
constexpr std::size_t kPacketLimit = 1024 * 1024;
constexpr std::size_t kPathLimit = 4096;
constexpr std::size_t kConversionLimit = 1024 * 1024;

class Failure final : public std::exception {
 public:
  explicit Failure(const char* code, bool argument = false)
      : code_(code), argument_(argument) {}
  const char* what() const noexcept override { return code_; }
  bool argument() const noexcept { return argument_; }
 private:
  const char* code_;
  bool argument_;
};
struct PendingJavaException {};

[[noreturn]] void invalid() { throw Failure("IPA_RIME_INVALID_ARGUMENT", true); }
[[noreturn]] void corrupt() { throw Failure("IPA_RIME_INVALID_NATIVE_STATE"); }

// Validate standard UTF-8, excluding NUL (all downstream APIs use C strings).
// JNI's modified UTF-8 encoding is deliberately not used anywhere.
bool validUtf8(const std::string& text) {
  std::size_t position = 0;
  while (position < text.size()) {
    const auto first = static_cast<unsigned char>(text[position++]);
    if (first == 0) return false;
    if (first < 0x80) continue;
    unsigned tails;
    std::uint32_t value;
    std::uint32_t minimum;
    if (first >= 0xc2 && first <= 0xdf) {
      tails = 1; value = first & 0x1f; minimum = 0x80;
    } else if (first >= 0xe0 && first <= 0xef) {
      tails = 2; value = first & 0x0f; minimum = 0x800;
    } else if (first >= 0xf0 && first <= 0xf4) {
      tails = 3; value = first & 0x07; minimum = 0x10000;
    } else return false;
    if (tails > text.size() - position) return false;
    while (tails-- != 0) {
      const auto next = static_cast<unsigned char>(text[position++]);
      if ((next & 0xc0) != 0x80) return false;
      value = (value << 6) | (next & 0x3f);
    }
    if (value < minimum || value > 0x10ffff ||
        (value >= 0xd800 && value <= 0xdfff)) return false;
  }
  return true;
}

std::string bytes(JNIEnv* env, jbyteArray input, std::size_t maximum, bool empty) {
  if (!input) invalid();
  const jsize length = env->GetArrayLength(input);
  if (length < 0 || static_cast<std::size_t>(length) > maximum ||
      (!empty && length == 0)) invalid();
  std::string result(static_cast<std::size_t>(length), '\0');
  if (length != 0) {
    env->GetByteArrayRegion(input, 0, length,
                           reinterpret_cast<jbyte*>(result.data()));
    if (env->ExceptionCheck()) throw PendingJavaException{};
  }
  if (!validUtf8(result)) invalid();
  return result;
}

std::string path(JNIEnv* env, jbyteArray input) {
  auto result = bytes(env, input, kPathLimit, false);
  if (result.front() != '/') invalid();
  return result;
}

jbyteArray javaBytes(JNIEnv* env, const unsigned char* data, std::size_t length) {
  if (length > static_cast<std::size_t>(std::numeric_limits<jsize>::max()))
    throw Failure("IPA_RIME_OUTPUT_TOO_LARGE");
  auto result = env->NewByteArray(static_cast<jsize>(length));
  if (!result) throw PendingJavaException{};
  if (length != 0) {
    env->SetByteArrayRegion(result, 0, static_cast<jsize>(length),
                           reinterpret_cast<const jbyte*>(data));
    if (env->ExceptionCheck()) throw PendingJavaException{};
  }
  return result;
}

jbyteArray javaBytes(JNIEnv* env, const std::string& data) {
  return javaBytes(env, reinterpret_cast<const unsigned char*>(data.data()),
                   data.size());
}

struct CandidateRow {
  std::uint32_t rank;
  std::uint32_t begin;
  std::uint32_t end;
  std::string text;
};

// This is a byte-level protocol, not a serialization of compiler-dependent structs.
struct Snapshot {
  std::uint32_t status = 0;
  std::uint32_t retained = 0;
  std::uint32_t prefix = 0;
  std::uint32_t committed = 0;
  std::vector<CandidateRow> rows;

  std::vector<unsigned char> encode() const {
    if (rows.size() > kCandidateLimit) corrupt();
    std::size_t total = 32 + 20 * rows.size();
    for (const auto& row : rows) {
      if (row.text.size() > kTextLimit || !validUtf8(row.text) ||
          row.text.size() > kPacketLimit - total) corrupt();
      total += row.text.size();
    }
    std::vector<unsigned char> output(total);
    auto word = [&](std::size_t at, std::uint32_t value) {
      for (unsigned byte = 0; byte != 4; ++byte)
        output.at(at + byte) = static_cast<unsigned char>(value >> (byte * 8));
    };
    word(0, 0x52494d45); word(4, 3); word(8, status);
    word(12, static_cast<std::uint32_t>(rows.size()));
    word(16, retained); word(20, static_cast<std::uint32_t>(total));
    word(24, prefix); word(28, committed);
    std::size_t textOffset = 32 + 20 * rows.size();
    for (std::size_t index = 0; index != rows.size(); ++index) {
      const auto& row = rows[index];
      const auto entry = 32 + index * 20;
      word(entry, row.rank); word(entry + 4, row.begin); word(entry + 8, row.end);
      word(entry + 12, static_cast<std::uint32_t>(textOffset));
      word(entry + 16, static_cast<std::uint32_t>(row.text.size()));
      std::copy(row.text.begin(), row.text.end(), output.begin() + textOffset);
      textOffset += row.text.size();
    }
    return output;
  }
};

RimeApi* checkedApi() {
  auto* api = rime_get_api();
  if (!api || !RIME_API_AVAILABLE(api, setup) || !RIME_API_AVAILABLE(api, initialize) ||
      !RIME_API_AVAILABLE(api, finalize) || !RIME_API_AVAILABLE(api, start_maintenance) ||
      !RIME_API_AVAILABLE(api, join_maintenance_thread) ||
      !RIME_API_AVAILABLE(api, create_session) || !RIME_API_AVAILABLE(api, destroy_session) ||
      !RIME_API_AVAILABLE(api, cleanup_all_sessions) || !RIME_API_AVAILABLE(api, select_schema) ||
      !RIME_API_AVAILABLE(api, set_option) || !RIME_API_AVAILABLE(api, process_key) ||
      !RIME_API_AVAILABLE(api, clear_composition) || !RIME_API_AVAILABLE(api, schema_open) ||
      !RIME_API_AVAILABLE(api, config_close) || !RIME_API_AVAILABLE(api, config_get_string) ||
      !RIME_API_AVAILABLE(api, get_version))
    throw Failure("IPA_RIME_API_UNSUPPORTED");
  return api;
}

class Converter final {
 public:
  explicit Converter(const std::string& config) : handle_(opencc_open(config.c_str())) {
    if (!handle_ || handle_ == reinterpret_cast<opencc_t>(static_cast<std::intptr_t>(-1)))
      throw Failure("IPA_RIME_CONVERSION_OPEN_FAILED");
  }
  ~Converter() noexcept { if (handle_) { try { opencc_close(handle_); } catch (...) {} } }
  Converter(const Converter&) = delete;
  Converter& operator=(const Converter&) = delete;
  std::string convert(const std::string& input) const {
    std::unique_ptr<char, decltype(&opencc_convert_utf8_free)> output(
        opencc_convert_utf8(handle_, input.c_str(), input.size()),
        &opencc_convert_utf8_free);
    if (!output) throw Failure("IPA_RIME_CONVERSION_FAILED");
    const auto size = strnlen(output.get(), kConversionLimit + 1);
    if (size > kConversionLimit) throw Failure("IPA_RIME_OUTPUT_TOO_LARGE");
    std::string result(output.get(), size);
    if (!validUtf8(result)) throw Failure("IPA_RIME_INVALID_NATIVE_UTF8");
    return result;
  }
 private:
  opencc_t handle_;
};

// Freeze only observable composition state. Preparing lazy candidate caches is allowed.
struct Observation {
  std::string input;
  std::string pendingCommit;
  std::size_t caret;
  std::vector<std::tuple<std::size_t, std::size_t, int, std::size_t>> segments;
  explicit Observation(const rime::Session& session) {
    const auto* context = session.context();
    if (!context) corrupt();
    input = context->input(); pendingCommit = session.commit_text();
    caret = context->caret_pos();
    for (const auto& segment : context->composition())
      segments.emplace_back(segment.start, segment.end,
                            static_cast<int>(segment.status), segment.selected_index);
  }
  bool equals(const Observation& other) const {
    return input == other.input && pendingCommit == other.pendingCommit &&
           caret == other.caret && segments == other.segments;
  }
};

void appendText(std::string& destination, const std::string& text) {
  if (text.size() > kTextLimit || destination.size() > kTextLimit - text.size())
    throw Failure("IPA_RIME_OUTPUT_TOO_LARGE");
  if (!validUtf8(text)) throw Failure("IPA_RIME_INVALID_NATIVE_UTF8");
  destination.append(text);
}

Snapshot observeCandidates(rime::Session& session, const std::string& schema,
                           const std::string& supplied, std::size_t limit, bool handled) {
  auto* context = session.context();
  if (!context) corrupt();
  const Observation original(session);
  const auto& retained = original.input;
  Snapshot result;
  const bool suffix = retained.size() <= supplied.size() &&
      supplied.compare(supplied.size() - retained.size(), retained.size(), retained) == 0;
  const auto lost = suffix ? supplied.size() - retained.size() : 0;
  if (!handled || !suffix || (lost != 0 && (schema == "bopomofo" || original.pendingCommit.empty()))) {
    result.status = 2;
    result.retained = static_cast<std::uint32_t>(supplied.size());
    return result;
  }
  result.retained = static_cast<std::uint32_t>(retained.size());
  result.committed = static_cast<std::uint32_t>(lost);
  if (!context->HasMenu()) return result;
  const auto& composition = context->composition();
  if (composition.empty() || !composition.back().menu) corrupt();
  const auto activeStart = composition.back().start;
  if (activeStart > retained.size()) corrupt();
  result.prefix = static_cast<std::uint32_t>(activeStart);

  std::string completed;
  appendText(completed, original.pendingCommit);
  std::size_t boundary = 0;
  for (auto segment = composition.begin(); segment + 1 != composition.end(); ++segment) {
    if (segment->start != boundary || segment->end < segment->start ||
        segment->end > retained.size()) corrupt();
    const auto selection = segment->GetSelectedCandidate();
    if (selection) {
      if (selection->start() != boundary || selection->end() < boundary ||
          selection->end() > retained.size()) corrupt();
      appendText(completed, selection->text());
      boundary = selection->end();
    } else {
      if (!segment->HasTag("phony"))
        appendText(completed, retained.substr(boundary, segment->end - boundary));
      boundary = segment->end;
    }
  }
  if (boundary != activeStart) corrupt();
  auto& menu = *composition.back().menu;
  menu.Prepare(limit);
  const auto available = std::min(limit, menu.candidate_count());
  result.rows.reserve(available);
  for (std::size_t rank = 0; rank != available; ++rank) {
    const auto candidate = menu.GetCandidateAt(rank);
    if (!candidate || candidate->start() > candidate->end() ||
        candidate->end() > retained.size()) corrupt();
    std::string text(completed);
    appendText(text, candidate->text());
    result.rows.push_back(CandidateRow{static_cast<std::uint32_t>(rank),
        static_cast<std::uint32_t>(candidate->start()),
        static_cast<std::uint32_t>(candidate->end()), std::move(text)});
  }
  if (!original.equals(Observation(session)))
    throw Failure("IPA_RIME_ENUMERATION_MUTATED_STATE");
  return result;
}

class Runtime final {
 public:
  Runtime(std::string shared, std::string user)
      : api_(checkedApi()), shared_(std::move(shared)), user_(std::move(user)) {}
  ~Runtime() noexcept {
    simplified_.reset(); traditional_.reset();
    if (started_) {
      try { api_->cleanup_all_sessions(); } catch (...) {}
      try { api_->finalize(); } catch (...) {}
    }
  }
  Runtime(const Runtime&) = delete;
  Runtime& operator=(const Runtime&) = delete;

  void start(const std::string& t2s, const std::string& s2t) {
    RimeTraits traits{};
    RIME_STRUCT_INIT(RimeTraits, traits);
    traits.shared_data_dir = shared_.c_str();
    traits.user_data_dir = user_.c_str();
    traits.distribution_name = "IPA Board";
    traits.distribution_code_name = "ipa_board";
    traits.distribution_version = "1";
    traits.app_name = "rime.ipa_board";
    traits.min_log_level = 3;
    traits.log_dir = "";
    // Finalize a partially initialized upstream runtime if setup/initialize throws.
    started_ = true;
    api_->setup(&traits);
    api_->initialize(&traits);
    api_->start_maintenance(False);
    api_->join_maintenance_thread();
    simplified_ = std::make_unique<Converter>(t2s);
    traditional_ = std::make_unique<Converter>(s2t);
  }

  std::vector<unsigned char> query(const std::string& schema,
                                   const std::string& input, std::size_t limit) {
    const auto id = sessionFor(schema);
    try {
      const auto session = rime::Service::instance().GetSession(id);
      if (!session || !session->context()) corrupt();
      auto reset = [&] {
        api_->clear_composition(id);
        session->ResetCommitText();
      };
      reset();
      bool handled = true;
      for (const auto key : input) {
        const bool accepted = api_->process_key(id, static_cast<unsigned char>(key), 0);
        handled = handled && accepted;
      }
      auto result = observeCandidates(*session, schema, input, limit, handled).encode();
      reset();
      return result;
    } catch (...) {
      // Destroying this one session discards all pending output on every failure path.
      // Other schemas keep their sessions and remain usable.
      try { api_->destroy_session(id); } catch (...) {}
      sessions_.erase(schema);
      throw;
    }
  }

  std::string convert(const std::string& text, jint direction) const {
    if (direction == 0) return simplified_->convert(text);
    if (direction == 1) return traditional_->convert(text);
    invalid();
  }

 private:
  RimeSessionId sessionFor(const std::string& schema) {
    const auto known = sessions_.find(schema);
    if (known != sessions_.end()) return known->second;
    // select_schema() itself accepts an absent schema upstream. Inspect the compiled
    // schema's actual identity before allocating a usable session.
    RimeConfig config{};
    if (!api_->schema_open(schema.c_str(), &config))
      throw Failure("IPA_RIME_SCHEMA_UNAVAILABLE");
    struct ConfigScope {
      RimeApi* api;
      RimeConfig* config;
      ~ConfigScope() noexcept { try { api->config_close(config); } catch (...) {} }
    } guard{api_, &config};
    char actual[128]{};
    if (!api_->config_get_string(&config, "schema/schema_id", actual, sizeof(actual)) ||
        schema != actual)
      throw Failure("IPA_RIME_SCHEMA_UNAVAILABLE");
    const auto id = api_->create_session();
    if (!id) throw Failure("IPA_RIME_SESSION_FAILED");
    try {
      if (!api_->select_schema(id, schema.c_str()))
        throw Failure("IPA_RIME_SCHEMA_UNAVAILABLE");
      for (const char* option : {"ascii_mode", "simplification", "zh_hans", "zh_hant_hk", "zh_hant_tw"})
        api_->set_option(id, option, False);
      api_->set_option(id, "_auto_commit", True);
      sessions_.emplace(schema, id);
      return id;
    } catch (...) {
      try { api_->destroy_session(id); } catch (...) {}
      throw;
    }
  }

  RimeApi* api_;
  std::string shared_;
  std::string user_;
  bool started_ = false;
  std::map<std::string, RimeSessionId> sessions_;
  std::unique_ptr<Converter> simplified_;
  std::unique_ptr<Converter> traditional_;
};

std::mutex gate;
std::unique_ptr<Runtime> active;
jlong activeHandle = 0;
jlong nextHandle = 1;

Runtime& runtime(jlong handle) {
  if (!active || handle <= 0 || handle != activeHandle)
    throw Failure("IPA_RIME_INVALID_HANDLE");
  return *active;
}

void javaFailure(JNIEnv* env, const char* type, const char* message) noexcept {
  if (env->ExceptionCheck()) return;
  const auto exception = env->FindClass(type);
  if (exception) {
    env->ThrowNew(exception, message);
    env->DeleteLocalRef(exception);
  }
}

template <typename Result, typename Body>
Result invoke(JNIEnv* env, Result failure, Body&& body) noexcept {
  try {
    std::lock_guard<std::mutex> lock(gate);
    return body();
  } catch (const PendingJavaException&) {
  } catch (const Failure& error) {
    javaFailure(env, error.argument() ? "java/lang/IllegalArgumentException" :
        "java/lang/IllegalStateException", error.what());
  } catch (const std::bad_alloc&) {
    javaFailure(env, "java/lang/OutOfMemoryError", "IPA_RIME_ALLOCATION_FAILED");
  } catch (...) {
    javaFailure(env, "java/lang/IllegalStateException", "IPA_RIME_NATIVE_FAILURE");
  }
  return failure;
}
}  // namespace

extern "C" JNIEXPORT jint JNICALL
Java_org_ipaboard_rime_RimeNative_abiVersion(JNIEnv* env, jclass) {
  return invoke<jint>(env, 0, [] { return kInterfaceVersion; });
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_org_ipaboard_rime_RimeNative_identity(JNIEnv* env, jclass) {
  return invoke<jbyteArray>(env, nullptr, [&] {
    const char* version = checkedApi()->get_version();
    if (!version) throw Failure("IPA_RIME_VERSION_UNAVAILABLE");
    const std::string identity = std::string("ipa-rime;abi=1;packet=3;librime=") +
        version + ";opencc=" IPA_OPENCC_VERSION ";build=" IPA_RIME_BUILD_ID;
    return javaBytes(env, identity);
  });
}

extern "C" JNIEXPORT jlong JNICALL
Java_org_ipaboard_rime_RimeNative_open(JNIEnv* env, jclass, jbyteArray shared,
                                    jbyteArray user, jbyteArray t2s, jbyteArray s2t) {
  return invoke<jlong>(env, 0, [&] {
    if (active) throw Failure("IPA_RIME_ALREADY_OPEN");
    auto sharedPath = path(env, shared);
    auto userPath = path(env, user);
    auto t2sPath = path(env, t2s);
    auto s2tPath = path(env, s2t);
    if (sharedPath == userPath) invalid();
    if (nextHandle == std::numeric_limits<jlong>::max())
      throw Failure("IPA_RIME_HANDLE_EXHAUSTED");
    auto created = std::make_unique<Runtime>(std::move(sharedPath), std::move(userPath));
    created->start(t2sPath, s2tPath);
    activeHandle = nextHandle++;
    active = std::move(created);
    return activeHandle;
  });
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_org_ipaboard_rime_RimeNative_query(JNIEnv* env, jclass, jlong handle,
                                     jbyteArray schema, jbyteArray input, jint limit) {
  return invoke<jbyteArray>(env, nullptr, [&] {
    auto& owner = runtime(handle);
    const auto schemaName = bytes(env, schema, 64, false);
    if (schemaName != "luna_pinyin" && schemaName != "terra_pinyin" && schemaName != "bopomofo")
      invalid();
    const auto spelling = bytes(env, input, kInputLimit, false);
    if (limit < 1 || limit > static_cast<jint>(kCandidateLimit) ||
        std::any_of(spelling.begin(), spelling.end(), [](unsigned char key) {
          return key < 0x20 || key > 0x7e;
        })) invalid();
    const auto packet = owner.query(schemaName, spelling, static_cast<std::size_t>(limit));
    return javaBytes(env, packet.data(), packet.size());
  });
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_org_ipaboard_rime_RimeNative_convert(JNIEnv* env, jclass, jlong handle,
                                       jbyteArray input, jint direction) {
  return invoke<jbyteArray>(env, nullptr, [&] {
    auto& owner = runtime(handle);
    const auto text = bytes(env, input, kConversionLimit, true);
    return javaBytes(env, owner.convert(text, direction));
  });
}

extern "C" JNIEXPORT void JNICALL
Java_org_ipaboard_rime_RimeNative_close(JNIEnv* env, jclass, jlong handle) {
  invoke<int>(env, 0, [&] {
    runtime(handle);
    active.reset();
    activeHandle = 0;
    return 1;
  });
}