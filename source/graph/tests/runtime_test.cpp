#include "ipa_graph.h"

#include <atomic>
#include <cmath>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {
void require(bool condition, const char* message) { if (!condition) throw std::runtime_error(message); }
std::uint64_t u64(const std::vector<char>& data, std::size_t p) {
    std::uint64_t n = 0;
    for (unsigned i = 0; i < 8; ++i) n |= std::uint64_t(static_cast<unsigned char>(data[p + i])) << (i * 8);
    return n;
}
void corrupt(const std::string& path, const std::vector<char>& original, std::size_t at, unsigned char value) {
    auto bytes = original;
    bytes[at] = static_cast<char>(value);
    std::ofstream file(path, std::ios::binary);
    file.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    file.close();
    std::string error;
    require(!ipa_graph::Engine::load(path, error), "corrupt file accepted");
    require(!error.empty(), "corrupt file had no error");
}
}

int main(int argc, char** argv) {
    try {
        require(argc == 2, "fixture required");
        std::string error;
        auto engine = ipa_graph::Engine::load(argv[1], error);
        require(bool(engine), error.c_str());
        require(engine->key_count() == 33, "key count");
        require(engine->word_count() == 32, "word count");
        require(engine->index_bits() == 24 || engine->index_bits() == 32, "index bits");
        require(engine->format_version() == (engine->index_bits() == 24 ? 2u : 3u), "format version");
        require(engine->arc_record_bytes() == engine->index_bits() / 8 + 1, "arc width");
        require(engine->segment_pattern().back() == '+', "nonempty segment regex");
        require(!engine->accepts_input(""), "empty input accepted");
        require(!engine->accepts_input(std::string(65, 'a')), "UTF16 bound");
        require(!engine->accepts_input("a🙂"), "unsupported suffix accepted");
        require(!engine->accepts_input(std::string("\xc0\xaf", 2)), "overlong UTF8 accepted");
        require(!engine->accepts_input(std::string("\xed\xa0\x80", 3)), "surrogate UTF8 accepted");
        require(engine->query("").candidates.empty(), "empty prediction generated");
        // Retain the original exact/prefix regression checks explicitly.
        const auto query = [&](const std::string& raw, const std::string& before = {},
                               const std::string& after = {}, ipa_graph::QueryOptions options = {}) {
            options.allow_omissions = false;
            return engine->query(raw, before, after, options);
        };
        auto result = query("a");
        require(result.status == ipa_graph::QueryStatus::Ok && result.exact_found, "exact key failed");
        require(result.candidates.size() == 2, "text dedup failed");
        require(result.candidates[0].text == "alpha" && result.candidates[0].language == "EN", "exact order/EN label");
        require(result.matched_records == 3, "posting records");
        require(query("ba").candidates[0].text == "alpha", "shared DAWG suffix rank failed");
        require(query("ɕoː").candidates[0].text == "学校", "UTF8 IPA or Japanese pool failed");
        require(query("ni˨˩˦").candidates[0].language == "简/繁", "merged regions");
        ipa_graph::QueryOptions one;
        one.candidate_limit = 1;
        result = query("cb", {}, {}, one);
        require(result.candidates.size() == 1 && result.candidates[0].text == "爱" &&
                result.candidates[0].language == "简/繁", "source labels lost at top-k boundary");
        require(query("ax").candidates.empty(), "unmatched suffix silently dropped");
        require(query("a🙂").status == ipa_graph::QueryStatus::UnsupportedInput, "unsupported status");
        ipa_graph::QueryOptions prefix;
        prefix.prefix = true;
        result = query("a", {}, {}, prefix);
        require(result.candidates.size() == 4 && result.matched_keys == 3, "prefix enumeration");
        require(result.candidates[0].kind == ipa_graph::CandidateKind::Conversion, "exact must precede completion");
        require(result.candidates[2].kind == ipa_graph::CandidateKind::Completion, "completion kind");
        require(result.candidates[2].matched_ipa == "ac" && result.candidates[2].derived, "completion explanation");
        prefix.prefix_key_budget = 1;
        result = query("a", {}, {}, prefix);
        require(result.truncated && result.matched_keys == 1 && result.candidates.size() == 2, "prefix budget");
        prefix.prefix_key_budget = 65536;
        prefix.candidate_limit = 180;
        result = query("x", {}, {}, prefix);
        require(result.candidates.size() == 26 && result.candidates[0].text == "word-25", "global frequency top within visited keys");
        require(result.candidates.back().text == "word-00", "multiple front-coded blocks");
        ipa_graph::QueryOptions none;
        none.candidate_limit = 0;
        require(query("a", {}, {}, none).candidates.empty(), "zero limit");
        engine->configure({false, false, true, false, false});
        result = query("a");
        require(result.candidates.size() == 1 && result.candidates[0].text == "啊", "policy filter");
        engine->configure({false, false, false, false, false});
        require(query("a").candidates.empty(), "disabled policy");
        engine->configure({});
        std::atomic<bool> failed{false};
        std::vector<std::thread> workers;
        for (unsigned t = 0; t < 4; ++t) workers.emplace_back([&] {
            for (unsigned i = 0; i < 1000; ++i) {
                auto r = query("a");
                if (r.status != ipa_graph::QueryStatus::Ok || r.candidates.size() > 2) failed = true;
                for (const auto& c : r.candidates) if (c.rank < 0 || !std::isfinite(c.context_affinity)) failed = true;
            }
        });
        for (unsigned i = 0; i < 1000; ++i) engine->configure({i % 2 == 0, false, true, false, false});
        for (auto& worker : workers) worker.join();
        require(!failed, "concurrent query/configure failed");
        std::ifstream fixture(argv[1], std::ios::binary);
        std::vector<char> bytes((std::istreambuf_iterator<char>(fixture)), {});
        const auto corrupt_path = std::string(argv[1]) + ".corrupt";
        corrupt(corrupt_path, bytes, 0, 'X');
        corrupt(corrupt_path, bytes, 8, 99);
        corrupt(corrupt_path, bytes, 36, 0); // Alphabet directory.
        corrupt(corrupt_path, bytes, static_cast<std::size_t>(u64(bytes, 36 + 3 * 8)), 0); // Arc label.
        corrupt(corrupt_path, bytes, static_cast<std::size_t>(u64(bytes, 36 + 5 * 8)), 0); // Empty posting.
        corrupt(corrupt_path, bytes, static_cast<std::size_t>(u64(bytes, 36 + 7 * 8)), 1); // First word shared prefix.
        for (std::size_t cut : {std::size_t(0), std::size_t(107), bytes.size() - 1}) {
            std::ofstream truncated(corrupt_path, std::ios::binary);
            truncated.write(bytes.data(), static_cast<std::streamsize>(cut)); truncated.close();
            require(!ipa_graph::Engine::load(corrupt_path, error), "truncated file accepted");
        }
        std::remove(corrupt_path.c_str());
        std::cout << "graph runtime: exact/prefix/rank/pool/UTF8/policy/concurrency/corruption tests passed\n";
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
    return 0;
}
