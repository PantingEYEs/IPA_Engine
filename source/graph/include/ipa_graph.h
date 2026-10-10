#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace ipa_graph {

enum class QueryStatus { Ok, UnsupportedInput, Timeout, Error };
// These values match the host CandidateKind ordinal.
enum class CandidateKind { Conversion = 0, Completion = 2 };
enum class Region : std::uint8_t { EnUS = 0, EnGB = 1, ZhCN = 2, ZhTW = 3, JaJP = 4 };

struct Candidate {
    std::string text;
    std::string language;
    int rank = 0;
    CandidateKind kind = CandidateKind::Conversion;
    int native_score = 0;
    float context_affinity = 0.0f;
    std::string matched_ipa;
    std::uint8_t frequency_score = 0;  // Quantized Zipf frequency, multiplied by 20.
    Region region = Region::EnUS;
    bool derived = false;
    unsigned omitted_symbols = 0;       // NFD comparison symbols missing from raw.
};

struct Policy {
    bool en_us = true;
    bool en_gb = true;
    bool zh_cn = true;
    bool zh_tw = true;
    bool ja_jp = true;
};

struct QueryOptions {
    unsigned candidate_limit = 30;       // Clamped to 0..180.
    bool prefix = false;                 // Explicit, bounded completion mode.
    unsigned prefix_key_budget = 2048;   // Clamped to 1..65536.
    unsigned deadline_ms = 60;            // Clamped to 1..60; timeout returns no candidates.
    bool allow_omissions = true;          // Ordered subsequence, no substitutions/transpositions.
    unsigned omission_state_budget = 20000; // Bounded desktop/mobile graph search.
};

struct QueryResult {
    std::vector<Candidate> candidates;
    QueryStatus status = QueryStatus::Ok;
    double elapsed_ms = 0;
    bool exact_found = false;             // A terminal key exists, before policy filtering.
    bool truncated = false;               // Candidate/search budget or soft deadline cut results.
    std::uint32_t matched_keys = 0;        // Posting lists inspected.
    std::uint64_t matched_records = 0;     // Enabled records encountered before deduplication.
    unsigned explored_states = 0;         // Omission traversal states examined.
};

// Immutable, validated, read-only mmap graph. The graph and word pool are never
// expanded into a runtime dictionary. Each query uses an atomic policy snapshot.
// Concurrent queries are safe; callers must finish before destroying the engine.
class Engine final {
public:
    static std::unique_ptr<Engine> load(const std::string& path, std::string& error) noexcept;
    ~Engine();
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    void configure(const Policy& policy) noexcept;
    bool accepts_input(const std::string& raw) const noexcept;
    const std::string& segment_pattern() const noexcept;
    QueryResult query(const std::string& raw, const std::string& before_cursor = {},
                      const std::string& after_cursor = {}, QueryOptions options = {}) const noexcept;
    std::size_t dictionary_bytes() const noexcept;
    std::uint32_t node_count() const noexcept;
    std::uint32_t arc_count() const noexcept;
    std::uint32_t key_count() const noexcept;
    std::uint32_t word_count() const noexcept;
    std::uint32_t alphabet_count() const noexcept;
    std::uint32_t format_version() const noexcept;
    unsigned index_bits() const noexcept;
    unsigned arc_record_bytes() const noexcept;
    static const char* status_name(QueryStatus status) noexcept;
    static const char* region_name(Region region) noexcept;

private:
    struct Impl;
    explicit Engine(std::unique_ptr<Impl> impl);
    std::unique_ptr<Impl> impl_;
};

}  // namespace ipa_graph
