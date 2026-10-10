#include "ipa_graph.h"
#include "ipa_unicode.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <limits>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <utility>
#include <unordered_set>
#ifdef _WIN32
#include <iterator>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace ipa_graph {
namespace {
using Clock = std::chrono::steady_clock;
constexpr std::size_t kHeaderSize = 108;
constexpr std::size_t kMaxBytes = 40000000;  // Safety cap; the preferred packed v2 package stays below 30 MB.
std::uint64_t symbol_bit(std::uint32_t cp) noexcept {
    return std::uint64_t(1) << ((std::uint64_t(cp) * 11400714819323198485ull) >> 58);
}

std::uint32_t u32(const std::uint8_t* p) noexcept {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) |
           (std::uint32_t(p[2]) << 16) | (std::uint32_t(p[3]) << 24);
}
std::uint64_t u64(const std::uint8_t* p) noexcept {
    return std::uint64_t(u32(p)) | (std::uint64_t(u32(p + 4)) << 32);
}
std::uint32_t u24(const std::uint8_t* p) noexcept {
    return std::uint32_t(p[0]) | (std::uint32_t(p[1]) << 8) | (std::uint32_t(p[2]) << 16);
}
bool varint(const std::uint8_t*& p, const std::uint8_t* end, std::uint32_t& value) noexcept {
    value = 0;
    for (unsigned shift = 0; shift <= 28; shift += 7) {
        if (p == end) return false;
        const auto b = *p++;
        if (shift == 28 && (b & 0xf0)) return false;
        value |= std::uint32_t(b & 127) << shift;
        if (!(b & 128)) return shift == 0 || (b & 127) != 0;
    }
    return false;
}
bool whitespace(std::uint32_t cp) noexcept {
    return cp == 0x20 || (cp >= 9 && cp <= 13) || cp == 0x85 || cp == 0xa0 ||
           cp == 0x1680 || (cp >= 0x2000 && cp <= 0x200a) || cp == 0x2028 ||
           cp == 0x2029 || cp == 0x202f || cp == 0x205f || cp == 0x3000;
}
bool utf8(const std::string& input, unsigned max_units, std::vector<std::uint32_t>* output = nullptr,
          bool require_text = false) {
    unsigned units = 0;
    bool nonblank = false;
    for (std::size_t i = 0; i < input.size();) {
        const auto a = static_cast<std::uint8_t>(input[i++]);
        std::uint32_t cp;
        unsigned continuation;
        if (a < 0x80) { cp = a; continuation = 0; }
        else if (a >= 0xc2 && a <= 0xdf) { cp = a & 31; continuation = 1; }
        else if (a >= 0xe0 && a <= 0xef) { cp = a & 15; continuation = 2; }
        else if (a >= 0xf0 && a <= 0xf4) { cp = a & 7; continuation = 3; }
        else return false;
        if (i + continuation > input.size()) return false;
        for (unsigned j = 0; j < continuation; ++j) {
            const auto b = static_cast<std::uint8_t>(input[i++]);
            if ((b & 0xc0) != 0x80) return false;
            cp = (cp << 6) | (b & 63);
        }
        if ((continuation == 1 && cp < 0x80) || (continuation == 2 && cp < 0x800) ||
            (continuation == 3 && cp < 0x10000) || cp > 0x10ffff ||
            (cp >= 0xd800 && cp <= 0xdfff)) return false;
        units += cp >= 0x10000 ? 2 : 1;
        if (units > max_units) return false;
        nonblank = nonblank || !whitespace(cp);
        if (output) output->push_back(cp);
    }
    return !require_text || nonblank;
}
void append_utf8(std::string& out, std::uint32_t cp) {
    if (cp < 0x80) out.push_back(static_cast<char>(cp));
    else if (cp < 0x800) {
        out.push_back(static_cast<char>(0xc0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 63)));
    } else if (cp < 0x10000) {
        out.push_back(static_cast<char>(0xe0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 63)));
        out.push_back(static_cast<char>(0x80 | (cp & 63)));
    } else {
        out.push_back(static_cast<char>(0xf0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 63)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 63)));
        out.push_back(static_cast<char>(0x80 | (cp & 63)));
    }
}
std::uint8_t policy_mask(const Policy& p) noexcept {
    return (p.en_us ? 1 : 0) | (p.en_gb ? 2 : 0) | (p.zh_cn ? 4 : 0) |
           (p.zh_tw ? 8 : 0) | (p.ja_jp ? 16 : 0);
}
std::string labels(std::uint8_t mask) {
    std::string out;
    const auto add = [&](const char* s) { if (!out.empty()) out += "/"; out += s; };
    if (mask & 3) add("EN");
    if (mask & 4) add("简");
    if (mask & 8) add("繁");
    if (mask & 16) add("日");
    return out;
}
}  // namespace

struct Engine::Impl {
    const std::uint8_t* data = nullptr;
    std::size_t bytes = 0;
#ifdef _WIN32
    std::vector<std::uint8_t> storage;
#else
    int fd = -1;
#endif
    std::uint32_t nodes = 0, arcs_count = 0, keys = 0, words = 0, alphabet_size = 0, root = 0;
    std::uint32_t version = 0, terminal_bit = 0;
    unsigned index_bytes = 3, arc_bytes = 4;
    const std::uint8_t* alphabet = nullptr;
    const std::uint8_t* node_offsets = nullptr;
    const std::uint8_t* node_counts = nullptr;
    const std::uint8_t* arcs = nullptr;
    const std::uint8_t* post_offsets = nullptr;
    const std::uint8_t* post_blob = nullptr;
    const std::uint8_t* word_offsets = nullptr;
    const std::uint8_t* word_blob = nullptr;
    std::size_t post_bytes = 0, word_bytes = 0;
    std::string pattern;
    std::vector<std::uint32_t> match_alphabet;
    std::vector<std::uint8_t> max_match_depth;
    // A conservative Bloom summary of symbols reachable in each suffix.
    // Collisions only reduce pruning; they can never reject a valid match.
    std::vector<std::uint64_t> match_presence;
    std::atomic<std::uint8_t> mask{31};

    ~Impl() {
#ifndef _WIN32
        if (data) munmap(const_cast<std::uint8_t*>(data), bytes);
        if (fd >= 0) close(fd);
#endif
    }
    std::uint32_t index(const std::uint8_t* table, std::uint32_t i) const noexcept {
        const auto* p = table + std::size_t(i) * index_bytes;
        return index_bytes == 3 ? u24(p) : u32(p);
    }
    std::uint32_t target(const std::uint8_t* arc) const noexcept {
        return index_bytes == 3 ? u24(arc + 1) : u32(arc + 1);
    }
    bool terminal(std::uint32_t node) const noexcept { return (index(node_counts, node) & terminal_bit) != 0; }
    std::uint32_t count(std::uint32_t node) const noexcept { return index(node_counts, node) & ~terminal_bit; }
    std::uint32_t begin(std::uint32_t node) const noexcept { return index(node_offsets, node); }
    std::uint32_t end(std::uint32_t node) const noexcept { return index(node_offsets, node + 1); }
    std::uint8_t label(std::uint32_t cp) const noexcept {
        std::uint32_t lo = 0, hi = alphabet_size;
        while (lo < hi) {
            const auto mid = lo + (hi - lo) / 2;
            const auto value = u32(alphabet + mid * 4);
            if (value < cp) lo = mid + 1;
            else hi = mid;
        }
        return lo < alphabet_size && u32(alphabet + lo * 4) == cp ? static_cast<std::uint8_t>(lo + 1) : 0;
    }
    bool input(const std::string& raw, std::vector<std::uint32_t>& cps) const {
        if (raw.empty() || raw.size() > 256 || !utf8(raw, 64, &cps, true)) return false;
        for (auto& cp : cps) {
            if (cp >= '1' && cp <= '5') cp = 0x2e9 - (cp - '1');
            const auto parts = ipa_expansion(cp);
            for (unsigned i = 0; i < parts.size; ++i)
                if (!std::binary_search(match_alphabet.begin(), match_alphabet.end(), parts.cps[i])) return false;
        }
        return true;
    }
    bool walk(const std::vector<std::uint32_t>& cps, std::uint32_t& node, std::uint32_t& rank) const noexcept {
        node = root;
        rank = 0;
        for (auto cp : cps) {
            const auto wanted = label(cp);
            if (terminal(node)) ++rank;
            bool found = false;
            for (auto i = begin(node); i < end(node); ++i) {
                const auto* arc = arcs + std::size_t(i) * arc_bytes;
                if (arc[0] < wanted) rank += count(target(arc));
                else if (arc[0] == wanted) { node = target(arc); found = true; break; }
                else break;
            }
            if (!found) return false;
        }
        return true;
    }
    std::string word(std::uint32_t id) const {
        const auto block = id / 16;
        const auto* p = word_blob + index(word_offsets, block);
        const auto* limit = word_blob + index(word_offsets, block + 1);
        std::string value;
        for (unsigned i = 0; i <= id % 16; ++i) {
            std::uint32_t shared = 0, suffix = 0;
            // All bounds and UTF-8 were validated once during initialization.
            if (!varint(p, limit, shared) || !varint(p, limit, suffix)) throw std::runtime_error("word pool changed");
            value.resize(shared);
            value.append(reinterpret_cast<const char*>(p), suffix);
            p += suffix;
        }
        return value;
    }
    void validate() {
        const auto fail = [](const char* why) { throw std::runtime_error(why); };
        if (bytes < kHeaderSize || std::memcmp(data, "IPADWG01", 8)) fail("invalid graph magic/header");
        version = u32(data + 8);
        if (version != 2 && version != 3) fail("unsupported graph version (requires v2 or v3)");
        index_bytes = version == 2 ? 3 : 4;
        arc_bytes = index_bytes + 1;
        terminal_bit = version == 2 ? 0x800000u : 0x80000000u;
        nodes = u32(data + 12); arcs_count = u32(data + 16); keys = u32(data + 20);
        words = u32(data + 24); alphabet_size = u32(data + 28); root = u32(data + 32);
        if (!nodes || (version == 2 && (nodes > 0x1000000 || arcs_count >= 0x1000000)) || !keys || keys >= terminal_bit ||
            !words || !alphabet_size || alphabet_size > 255 || root >= nodes)
            fail("invalid graph counts");
        std::uint64_t off[9];
        for (unsigned i = 0; i < 9; ++i) off[i] = u64(data + 36 + i * 8);
        if (off[0] != kHeaderSize || off[8] != bytes) fail("invalid graph size/directory");
        for (unsigned i = 0; i < 8; ++i) if (off[i] > off[i + 1]) fail("unordered graph sections");
        const std::uint64_t blocks = (std::uint64_t(words) + 15) / 16;
        if (off[1] - off[0] != std::uint64_t(alphabet_size) * 4 ||
            off[2] - off[1] != (std::uint64_t(nodes) + 1) * index_bytes ||
            off[3] - off[2] != std::uint64_t(nodes) * index_bytes ||
            off[4] - off[3] != std::uint64_t(arcs_count) * arc_bytes ||
            off[5] - off[4] != (std::uint64_t(keys) + 1) * index_bytes ||
            off[7] - off[6] != (blocks + 1) * index_bytes ||
            (version == 2 && (off[6] - off[5] >= 0x1000000 || off[8] - off[7] >= 0x1000000)))
            fail("invalid graph section lengths");
        alphabet = data + off[0]; node_offsets = data + off[1]; node_counts = data + off[2];
        arcs = data + off[3]; post_offsets = data + off[4]; post_blob = data + off[5];
        post_bytes = off[6] - off[5]; word_offsets = data + off[6]; word_blob = data + off[7];
        word_bytes = off[8] - off[7];
        std::uint32_t previous_cp = 0;
        std::ostringstream expression;
        expression << "[";
        for (unsigned i = 0; i < alphabet_size; ++i) {
            const auto cp = u32(alphabet + i * 4);
            if (!cp || cp <= previous_cp || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) fail("invalid alphabet");
            previous_cp = cp;
            const auto parts = ipa_expansion(cp);
            for (unsigned j = 0; j < parts.size; ++j) match_alphabet.push_back(parts.cps[j]);
            match_alphabet.push_back(cp);
        }
        std::sort(match_alphabet.begin(), match_alphabet.end());
        match_alphabet.erase(std::unique(match_alphabet.begin(), match_alphabet.end()), match_alphabet.end());
        // All accepted precomposed aliases must also be recognized by the
        // host's segment regex, even when only decomposed keys are recorded.
        const auto add_alias = [&](std::uint32_t cp) {
            const auto parts = ipa_expansion(cp);
            if (parts.size > 1 && std::all_of(parts.cps.begin(), parts.cps.begin() + parts.size,
                    [&](auto part) { return std::binary_search(match_alphabet.begin(), match_alphabet.end(), part); }))
                expression << "\\x{" << std::hex << cp << "}";
        };
        for (std::uint32_t cp = 0x80; cp < 0x300; ++cp) add_alias(cp);
        for (std::uint32_t cp = 0x1e00; cp < 0x2000; ++cp) add_alias(cp);
        for (auto cp : match_alphabet) expression << "\\x{" << std::hex << cp << "}";
        for (auto cp = '1'; cp <= '5'; ++cp)
            if (std::binary_search(match_alphabet.begin(), match_alphabet.end(), std::uint32_t(0x2e9 - (cp - '1'))))
                expression << "\\x{" << std::hex << cp << "}";
        expression << "]+";
        pattern = expression.str();
        if (index(node_offsets, 0) != 0 || index(node_offsets, nodes) != arcs_count) fail("invalid arc directory");
        std::vector<std::uint16_t> depths(nodes);
        max_match_depth.resize(nodes);
        match_presence.resize(nodes);
        for (std::uint32_t n = 0; n < nodes; ++n) {
            const auto first = begin(n), last = end(n);
            if (first > last || last > arcs_count) fail("invalid node arc range");
            std::uint64_t total = terminal(n) ? 1 : 0;
            unsigned previous_label = 0;
            std::uint16_t depth = 0;
            for (auto i = first; i < last; ++i) {
                const auto* arc = arcs + std::size_t(i) * arc_bytes;
                const auto child = target(arc);
                if (!arc[0] || arc[0] > alphabet_size || arc[0] <= previous_label || child >= n)
                    fail("invalid arc/order/acyclicity");
                previous_label = arc[0];
                total += count(child);
                const auto cp = u32(alphabet + (arc[0] - 1) * 4);
                depth = std::max<std::uint16_t>(depth, static_cast<std::uint16_t>(depths[child] + (cp >= 0x10000 ? 2 : 1)));
                max_match_depth[n] = std::max<unsigned>(max_match_depth[n], max_match_depth[child] + ipa_expansion(cp).size);
                match_presence[n] |= match_presence[child];
                const auto parts = ipa_expansion(cp);
                for (unsigned j = 0; j < parts.size; ++j) match_presence[n] |= symbol_bit(parts.cps[j]);
            }
            if (!total || total >= terminal_bit || count(n) != total) fail("invalid suffix count");
            if (depth > 64) fail("graph key exceeds input length bound");
            depths[n] = depth;
        }
        if (count(root) != keys) fail("root suffix count differs from key count");
        if (terminal(root)) fail("empty input key is forbidden");
        if (index(post_offsets, 0) != 0 || index(post_offsets, keys) != post_bytes) fail("invalid posting directory");
        for (std::uint32_t k = 0; k < keys; ++k) {
            const auto a = index(post_offsets, k), b = index(post_offsets, k + 1);
            if (a >= b || b > post_bytes) fail("invalid posting bounds");
            const auto* p = post_blob + a;
            const auto* limit = post_blob + b;
            std::uint32_t records = 0, prev_word = 0, prev_region = 0;
            if (!varint(p, limit, records) || !records || records > std::uint64_t(limit - p) / 2)
                fail("invalid posting count");
            for (std::uint32_t r = 0; r < records; ++r) {
                std::uint32_t code;
                if (!varint(p, limit, code) || p == limit) fail("invalid posting record");
                const auto delta = code >> 4;
                const auto region = code & 7;
                if (delta > words || prev_word >= words || delta >= words - prev_word || region > 4 || *p > 180)
                    fail("invalid posting word/region/score");
                const auto id = prev_word + delta;
                if (r && id == prev_word && region <= prev_region) fail("unordered or duplicate posting record");
                prev_word = id; prev_region = region; ++p;
            }
            if (p != limit) fail("trailing posting bytes");
        }
        if (index(word_offsets, 0) != 0 || index(word_offsets, static_cast<std::uint32_t>(blocks)) != word_bytes)
            fail("invalid word directory");
        std::string previous_word;
        for (std::uint32_t b = 0; b < blocks; ++b) {
            const auto first = index(word_offsets, b), last = index(word_offsets, b + 1);
            if (first >= last || last > word_bytes) fail("invalid word block bounds");
            const auto* p = word_blob + first;
            const auto* limit = word_blob + last;
            std::string value;
            const auto block_words = std::min<std::uint32_t>(16, words - b * 16);
            for (unsigned j = 0; j < block_words; ++j) {
                std::uint32_t shared = 0, suffix = 0;
                if (!varint(p, limit, shared) || !varint(p, limit, suffix) || shared > value.size() ||
                    (j == 0 && shared != 0) || suffix > static_cast<std::uint64_t>(limit - p) || shared + std::uint64_t(suffix) > 4000)
                    fail("invalid front-coded word");
                value.resize(shared);
                value.append(reinterpret_cast<const char*>(p), suffix); p += suffix;
                if (!utf8(value, 1000, nullptr, true) || (!previous_word.empty() && previous_word >= value))
                    fail("invalid UTF-8, blank, oversized or unordered word");
                previous_word = value;
            }
            if (p != limit) fail("trailing word block bytes");
        }
    }
};

Engine::Engine(std::unique_ptr<Impl> impl) : impl_(std::move(impl)) {}
Engine::~Engine() = default;

std::unique_ptr<Engine> Engine::load(const std::string& path, std::string& error) noexcept {
    error.clear();
    try {
        auto p = std::make_unique<Impl>();
#ifdef _WIN32
        std::ifstream file(path, std::ios::binary | std::ios::ate);
        if (!file) throw std::runtime_error("cannot open dictionary");
        const auto length = file.tellg();
        if (length < static_cast<std::streamoff>(kHeaderSize) || length >= static_cast<std::streamoff>(kMaxBytes))
            throw std::runtime_error("dictionary exceeds the 40 MB runtime safety cap");
        p->bytes = static_cast<std::size_t>(length);
        p->storage.resize(p->bytes); file.seekg(0);
        file.read(reinterpret_cast<char*>(p->storage.data()), static_cast<std::streamsize>(p->bytes));
        if (!file) throw std::runtime_error("cannot read dictionary");
        p->data = p->storage.data();
#else
        p->fd = open(path.c_str(), O_RDONLY);
        if (p->fd < 0) throw std::runtime_error("cannot open dictionary");
        struct stat status {};
        if (fstat(p->fd, &status) || !S_ISREG(status.st_mode)) throw std::runtime_error("dictionary is not a regular file");
        if (status.st_size < static_cast<off_t>(kHeaderSize) || status.st_size >= static_cast<off_t>(kMaxBytes))
            throw std::runtime_error("dictionary exceeds the 40 MB runtime safety cap");
        p->bytes = static_cast<std::size_t>(status.st_size);
        auto* mapping = mmap(nullptr, p->bytes, PROT_READ, MAP_PRIVATE, p->fd, 0);
        if (mapping == MAP_FAILED) throw std::runtime_error("cannot map dictionary");
        p->data = static_cast<const std::uint8_t*>(mapping);
#endif
        p->validate();
        return std::unique_ptr<Engine>(new Engine(std::move(p)));
    } catch (const std::exception& e) { error = e.what(); }
    catch (...) { error = "unknown dictionary initialization failure"; }
    return nullptr;
}
void Engine::configure(const Policy& policy) noexcept { impl_->mask.store(policy_mask(policy), std::memory_order_relaxed); }
bool Engine::accepts_input(const std::string& raw) const noexcept {
    try { std::vector<std::uint32_t> cps; return impl_->input(raw, cps); } catch (...) { return false; }
}
const std::string& Engine::segment_pattern() const noexcept { return impl_->pattern; }
std::size_t Engine::dictionary_bytes() const noexcept { return impl_->bytes; }
std::uint32_t Engine::node_count() const noexcept { return impl_->nodes; }
std::uint32_t Engine::arc_count() const noexcept { return impl_->arcs_count; }
std::uint32_t Engine::key_count() const noexcept { return impl_->keys; }
std::uint32_t Engine::word_count() const noexcept { return impl_->words; }
std::uint32_t Engine::alphabet_count() const noexcept { return impl_->alphabet_size; }
std::uint32_t Engine::format_version() const noexcept { return impl_->version; }
unsigned Engine::index_bits() const noexcept { return impl_->index_bytes * 8; }
unsigned Engine::arc_record_bytes() const noexcept { return impl_->arc_bytes; }
const char* Engine::status_name(QueryStatus s) noexcept {
    switch (s) { case QueryStatus::Ok: return "ok"; case QueryStatus::UnsupportedInput: return "unsupported_input";
                 case QueryStatus::Timeout: return "timeout"; case QueryStatus::Error: return "error"; }
    return "error";
}
const char* Engine::region_name(Region r) noexcept {
    switch (r) { case Region::EnUS: return "en-US"; case Region::EnGB: return "en-GB";
                 case Region::ZhCN: return "zh-CN"; case Region::ZhTW: return "zh-TW"; case Region::JaJP: return "ja-JP"; }
    return "unknown";
}

QueryResult Engine::query(const std::string& raw, const std::string&, const std::string&, QueryOptions options) const noexcept {
    QueryResult result;
    const auto start = Clock::now();
    const auto deadline = start + std::chrono::milliseconds(std::clamp(options.deadline_ms, 1u, 60u));
    const auto finish = [&] { result.elapsed_ms = std::chrono::duration<double, std::milli>(Clock::now() - start).count(); };
    try {
        if (raw.empty()) { finish(); return result; }
        std::vector<std::uint32_t> cps;
        if (!impl_->input(raw, cps)) { result.status = QueryStatus::UnsupportedInput; finish(); return result; }
        const auto mask = impl_->mask.load(std::memory_order_relaxed);
        const auto limit = std::min(options.candidate_limit, 180u);
        std::uint32_t node = 0, rank = 0;
        const bool walked = impl_->walk(cps, node, rank);
        result.exact_found = walked && impl_->terminal(node);
        if (!limit || !mask) { finish(); return result; }
        struct Hit {
            std::uint32_t id;
            std::uint8_t mask, freq;
            Region region;
            bool derived, completion;
            int score;
            std::string ipa;
            unsigned omitted;
        };
        std::vector<Hit> hits;
        hits.reserve(limit);
        const auto better = [](const Hit& a, const Hit& b) {
            return a.score > b.score || (a.score == b.score && a.id < b.id);
        };
        bool timeout = false;
        auto check_time = [&] { if (Clock::now() >= deadline) timeout = true; return timeout; };
        std::unordered_set<std::uint32_t> consumed;
        bool candidate_overflow = false;
        const auto insert_hit = [&](Hit hit) {
            const auto same = std::find_if(hits.begin(), hits.end(), [&](const Hit& h) { return h.id == hit.id; });
            if (same != hits.end()) {
                const auto merged = static_cast<std::uint8_t>(same->mask | hit.mask);
                if (better(hit, *same)) *same = std::move(hit);
                same->mask = merged;
            } else if (hits.size() < limit) hits.push_back(std::move(hit));
            else {
                candidate_overflow = true;
                const auto worst = std::max_element(hits.begin(), hits.end(), better);
                if (better(hit, *worst)) *worst = std::move(hit);
            }
        };
        const auto consume = [&](std::uint32_t key, const std::string& ipa, bool completion, unsigned omitted = 0) {
            if (!consumed.insert(key).second) return;
            ++result.matched_keys;
            const auto* p = impl_->post_blob + impl_->index(impl_->post_offsets, key);
            const auto* end = impl_->post_blob + impl_->index(impl_->post_offsets, key + 1);
            std::uint32_t records = 0, id = 0;
            if (!varint(p, end, records)) throw std::runtime_error("posting changed");
            Hit group{};
            bool have_group = false;
            for (std::uint32_t r = 0; r < records; ++r) {
                if ((r & 31) == 0 && check_time()) {
                    if (have_group) insert_hit(std::move(group));
                    return;
                }
                std::uint32_t code = 0;
                if (!varint(p, end, code) || p == end) throw std::runtime_error("posting changed");
                id += code >> 4;
                const auto region = static_cast<std::uint8_t>(code & 7);
                const bool derived = (code & 8) != 0;
                const auto freq = *p++;
                if (!(mask & (1u << region))) continue;
                ++result.matched_records;
                Hit hit{id, static_cast<std::uint8_t>(1u << region), freq, static_cast<Region>(region),
                        derived, completion, (completion ? 1000 : omitted ? 5000 - int(omitted) * 20 : 10000)
                        + freq - (derived ? 5 : 0), ipa, omitted};
                if (have_group && group.id == id) {
                    const auto merged = static_cast<std::uint8_t>(group.mask | hit.mask);
                    if (better(hit, group)) group = std::move(hit);
                    group.mask = merged;
                } else {
                    if (have_group) insert_hit(std::move(group));
                    group = std::move(hit);
                    have_group = true;
                }
            }
            if (have_group) insert_hit(std::move(group));
        };
        std::string normalized;
        for (auto cp : cps) append_utf8(normalized, cp);
        unsigned input_symbols = 0;
        for (auto cp : cps) input_symbols += ipa_expansion(cp).size;
        if (result.exact_found) consume(rank, normalized, false);
        if (options.prefix && walked && !timeout) {
            const auto budget = std::clamp(options.prefix_key_budget, 1u, 65536u);
            unsigned prefix_keys = 0;
            struct Frame { std::uint32_t node, next, rank; std::size_t bytes; bool entered; };
            std::vector<Frame> stack;
            stack.reserve(65);
            std::string ipa = normalized;
            stack.push_back({node, impl_->begin(node), rank, ipa.size(), false});
            while (!stack.empty() && !timeout) {
                if (check_time()) break;
                auto& frame = stack.back();
                if (!frame.entered) {
                    frame.entered = true;
                    if (impl_->terminal(frame.node)) {
                        if (prefix_keys++ == budget) { result.truncated = true; break; }
                        unsigned key_symbols = 0;
                        std::vector<std::uint32_t> key_cps;
                        if (!utf8(ipa, 64, &key_cps)) throw std::runtime_error("invalid prefix key");
                        for (auto cp : key_cps) key_symbols += ipa_expansion(cp).size;
                        consume(frame.rank++, ipa, ipa != normalized, key_symbols - input_symbols);
                    }
                }
                if (frame.next == impl_->end(frame.node)) { stack.pop_back(); if (!stack.empty()) ipa.resize(stack.back().bytes); continue; }
                const auto* arc = impl_->arcs + std::size_t(frame.next++) * impl_->arc_bytes;
                const auto child = impl_->target(arc), child_rank = frame.rank;
                frame.rank += impl_->count(child);
                append_utf8(ipa, u32(impl_->alphabet + (arc[0] - 1) * 4));
                stack.push_back({child, impl_->begin(child), child_rank, ipa.size(), false});
            }
        }
        // Ordered deletion matching is independent of prefix completion. Greedy
        // advancement enumerates each lexical path once, even with repeated
        // symbols. Different prefixes sharing a DAFSA state keep their key rank.
        if (options.allow_omissions && !timeout) {
            std::vector<std::uint32_t> input;
            for (auto cp : cps) {
                const auto parts = ipa_expansion(cp);
                input.insert(input.end(), parts.cps.begin(), parts.cps.begin() + parts.size);
            }
            std::vector<std::uint64_t> remaining(input.size() + 1);
            for (auto i = input.size(); i > 0; --i) remaining[i - 1] = remaining[i] | symbol_bit(input[i - 1]);
            struct State {
                std::uint32_t node, rank;
                unsigned position, omitted, cost;
                std::string ipa;
            };
            const auto later = [](const State& a, const State& b) {
                return a.cost > b.cost || (a.cost == b.cost && a.ipa > b.ipa);
            };
            std::priority_queue<State, std::vector<State>, decltype(later)> pending(later);
            pending.push({impl_->root, 0, 0, 0, 0, {}});
            const auto budget = std::clamp(options.omission_state_budget, 1u, 200000u);
            unsigned created = 1;
            while (!pending.empty() && !check_time()) {
                State current = pending.top(); pending.pop();
                ++result.explored_states;
                if (input.size() - current.position > impl_->max_match_depth[current.node]
                    || (impl_->match_presence[current.node] & remaining[current.position]) != remaining[current.position]) continue;
                auto child_rank = current.rank;
                if (impl_->terminal(current.node)) {
                    if (current.position == input.size()) {
                        if (current.omitted == 0) result.exact_found = true;
                        const bool completion = options.prefix && current.ipa != normalized
                            && current.ipa.compare(0, normalized.size(), normalized) == 0;
                        // Literal completions were handled by the separately
                        // bounded prefix phase; never bypass its key budget.
                        if (!completion) consume(child_rank, current.ipa, false, current.omitted);
                    }
                    ++child_rank;
                }
                for (auto i = impl_->begin(current.node); i < impl_->end(current.node); ++i) {
                    const auto* arc = impl_->arcs + std::size_t(i) * impl_->arc_bytes;
                    const auto child = impl_->target(arc);
                    const auto cp = u32(impl_->alphabet + (arc[0] - 1) * 4);
                    auto position = current.position, omitted = current.omitted, cost = current.cost;
                    const auto parts = ipa_expansion(cp);
                    for (unsigned j = 0; j < parts.size; ++j) {
                        if (position < input.size() && parts.cps[j] == input[position]) ++position;
                        else { ++omitted; cost += ipa_annotation(parts.cps[j]) ? 1 : 128; }
                    }
                    if (input.size() - position <= impl_->max_match_depth[child]
                        && (impl_->match_presence[child] & remaining[position]) == remaining[position]) {
                        if (created == budget) result.truncated = true;
                        else {
                            std::string ipa = current.ipa;
                            append_utf8(ipa, cp);
                            pending.push({child, child_rank, position, omitted, cost, std::move(ipa)});
                            ++created;
                        }
                    }
                    child_rank += impl_->count(child);
                }
            }
            if (timeout || !pending.empty()) result.truncated = true;
        }
        if (check_time()) timeout = true;
        if (timeout) {
            if (!options.allow_omissions) { result.status = QueryStatus::Timeout; result.candidates.clear(); finish(); return result; }
            // Deadline/budget truncation must not discard already valid hits.
            result.truncated = true;
            if (hits.empty()) { result.status = QueryStatus::Timeout; finish(); return result; }
        }
        if (candidate_overflow) result.truncated = true;
        std::sort(hits.begin(), hits.end(), better);
        result.candidates.reserve(hits.size());
        for (const auto& h : hits) {
            Candidate c;
            c.text = impl_->word(h.id); c.language = labels(h.mask); c.rank = static_cast<int>(result.candidates.size());
            c.kind = h.completion ? CandidateKind::Completion : CandidateKind::Conversion;
            c.native_score = h.score; c.matched_ipa = h.ipa; c.frequency_score = h.freq;
            c.region = h.region; c.derived = h.derived;
            c.omitted_symbols = h.omitted;
            result.candidates.push_back(std::move(c));
        }
    } catch (...) { result.status = QueryStatus::Error; result.candidates.clear(); }
    finish();
    return result;
}
}  // namespace ipa_graph
