#include "ipa_graph.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
std::string json(const std::string& input) {
    std::ostringstream out;
    out << '"';
    for (const unsigned char c : input) {
        if (c == '"') out << "\\\"";
        else if (c == '\\') out << "\\\\";
        else if (c < 0x20) out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << unsigned(c) << std::dec;
        else out << c;
    }
    out << '"';
    return out.str();
}
unsigned number(const std::string& value) {
    std::size_t used = 0;
    const auto n = std::stoul(value, &used);
    if (used != value.size() || n > 10000000) throw std::runtime_error("invalid integer option");
    return static_cast<unsigned>(n);
}
void output_query(const std::string& raw, const ipa_graph::Engine& engine, const ipa_graph::QueryResult& r) {
    std::cout << "{\"raw\":" << json(raw) << ",\"accepts_input\":" << (engine.accepts_input(raw) ? "true" : "false")
              << ",\"status\":" << json(ipa_graph::Engine::status_name(r.status))
              << ",\"elapsed_ms\":" << std::setprecision(8) << r.elapsed_ms
              << ",\"exact_found\":" << (r.exact_found ? "true" : "false")
              << ",\"truncated\":" << (r.truncated ? "true" : "false")
              << ",\"matched_keys\":" << r.matched_keys << ",\"matched_records\":" << r.matched_records
              << ",\"explored_states\":" << r.explored_states
              << ",\"returned\":" << r.candidates.size() << ",\"candidates\":[";
    bool first = true;
    for (const auto& c : r.candidates) {
        if (!first) std::cout << ',';
        first = false;
        std::cout << "{\"text\":" << json(c.text) << ",\"language\":" << json(c.language)
                  << ",\"rank\":" << c.rank << ",\"kind\":" << (c.kind == ipa_graph::CandidateKind::Conversion ? "\"conversion\"" : "\"completion\"")
                  << ",\"native_score\":" << c.native_score << ",\"context_affinity\":0"
                  << ",\"matched_ipa\":" << json(c.matched_ipa)
                  << ",\"frequency_score\":" << unsigned(c.frequency_score)
                  << ",\"frequency_zipf\":" << (c.frequency_score / 20.0)
                  << ",\"region\":" << json(ipa_graph::Engine::region_name(c.region))
                  << ",\"derived\":" << (c.derived ? "true" : "false")
                  << ",\"omitted_symbols\":" << c.omitted_symbols << '}';
    }
    std::cout << "]}\n";
}
void usage() {
    std::cerr << "Usage:\n"
              << "  ipa_graph info DICTIONARY\n"
              << "  ipa_graph query DICTIONARY IPA [--region en-US|en-GB|zh-CN|zh-TW|ja-JP] [--prefix] [--limit N]\n"
              << "  ipa_graph repl DICTIONARY [options]     # one IPA line in, one JSON line out\n"
              << "  ipa_graph bench DICTIONARY IPA [--iterations N] [options]\n"
              << "  ipa_graph bench DICTIONARY - < IPA_LINES  # cycle through varied inputs\n"
              << "Options: --strict --omission-budget N --prefix-budget N --deadline-ms N --limit N; repeated --region enables multiple regions.\n";
}
}

int main(int argc, char** argv) {
    if (argc < 3) { usage(); return 2; }
    const std::string command = argv[1];
    if (command != "info" && command != "query" && command != "repl" && command != "bench") { usage(); return 2; }
    try {
        int opt = 3;
        std::string raw;
        if (command == "query" || command == "bench") {
            if (argc < 4) { usage(); return 2; }
            raw = argv[3]; opt = 4;
        }
        ipa_graph::QueryOptions options;
        ipa_graph::Policy policy;
        bool explicit_regions = false;
        unsigned iterations = 1000;
        unsigned warmup = 100;
        for (; opt < argc; ++opt) {
            const std::string arg = argv[opt];
            if (arg == "--prefix") options.prefix = true;
            else if (arg == "--strict") options.allow_omissions = false;
            else if (arg == "--region" && opt + 1 < argc) {
                if (!explicit_regions) { policy = {false, false, false, false, false}; explicit_regions = true; }
                const std::string region = argv[++opt];
                if (region == "en-US") policy.en_us = true;
                else if (region == "en-GB") policy.en_gb = true;
                else if (region == "zh-CN") policy.zh_cn = true;
                else if (region == "zh-TW") policy.zh_tw = true;
                else if (region == "ja-JP") policy.ja_jp = true;
                else throw std::runtime_error("unknown region: " + region);
            } else if (opt + 1 < argc && arg == "--limit") options.candidate_limit = number(argv[++opt]);
            else if (opt + 1 < argc && arg == "--prefix-budget") options.prefix_key_budget = number(argv[++opt]);
            else if (opt + 1 < argc && arg == "--omission-budget") options.omission_state_budget = number(argv[++opt]);
            else if (opt + 1 < argc && arg == "--deadline-ms") options.deadline_ms = number(argv[++opt]);
            else if (opt + 1 < argc && arg == "--iterations") iterations = std::clamp(number(argv[++opt]), 1u, 1000000u);
            else if (opt + 1 < argc && arg == "--warmup") warmup = std::min(number(argv[++opt]), 1000000u);
            else throw std::runtime_error("unknown or incomplete option: " + arg);
        }
        std::string error;
        const auto begin = std::chrono::steady_clock::now();
        auto engine = ipa_graph::Engine::load(argv[2], error);
        const auto load_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - begin).count();
        if (!engine) { std::cerr << "Dictionary initialization failed: " << error << '\n'; return 1; }
        engine->configure(policy);
        if (command == "info") {
            std::cout << "{\"dictionary_bytes\":" << engine->dictionary_bytes()
                      << ",\"format_version\":" << engine->format_version() << ",\"index_bits\":" << engine->index_bits()
                      << ",\"arc_record_bytes\":" << engine->arc_record_bytes()
                      << ",\"load_ms\":" << load_ms << ",\"nodes\":" << engine->node_count()
                      << ",\"arcs\":" << engine->arc_count() << ",\"keys\":" << engine->key_count()
                      << ",\"words\":" << engine->word_count() << ",\"alphabet\":" << engine->alphabet_count()
                      << ",\"segment_pattern\":" << json(engine->segment_pattern()) << "}\n";
        } else if (command == "query") {
            const auto result = engine->query(raw, {}, {}, options);
            output_query(raw, *engine, result);
            return result.status == ipa_graph::QueryStatus::Timeout || result.status == ipa_graph::QueryStatus::Error ? 1 : 0;
        } else if (command == "repl") {
            while (std::getline(std::cin, raw)) {
                if (!raw.empty() && raw.back() == '\r') raw.pop_back();
                output_query(raw, *engine, engine->query(raw, {}, {}, options));
                std::cout.flush();
            }
        } else {
            std::vector<std::string> cases;
            if (raw == "-") {
                std::string line;
                while (std::getline(std::cin, line)) {
                    if (!line.empty() && line.back() == '\r') line.pop_back();
                    if (cases.size() >= 1000000 || line.size() > 256) throw std::runtime_error("benchmark input list exceeds its bound");
                    cases.push_back(std::move(line));
                }
                if (cases.empty()) throw std::runtime_error("benchmark requires at least one input line");
            } else cases.push_back(raw);
            for (unsigned i = 0; i < warmup; ++i) engine->query(cases[i % cases.size()], {}, {}, options);
            std::vector<double> times;
            times.reserve(iterations);
            unsigned timeouts = 0, errors = 0, returned = 0;
            for (unsigned i = 0; i < iterations; ++i) {
                auto result = engine->query(cases[i % cases.size()], {}, {}, options);
                times.push_back(result.elapsed_ms);
                timeouts += result.status == ipa_graph::QueryStatus::Timeout;
                errors += result.status == ipa_graph::QueryStatus::Error;
                returned = static_cast<unsigned>(result.candidates.size());
            }
            std::sort(times.begin(), times.end());
            const auto percentile = [&](double q) { return times[static_cast<std::size_t>(std::ceil(q * times.size())) - 1]; };
            std::cout << "{\"raw\":" << json(raw) << ",\"iterations\":" << iterations
                      << ",\"cases\":" << cases.size() << ",\"warmup\":" << warmup
                      << ",\"format_version\":" << engine->format_version() << ",\"index_bits\":" << engine->index_bits()
                      << ",\"dictionary_bytes\":" << engine->dictionary_bytes()
                      << ",\"load_ms\":" << load_ms << ",\"p50_ms\":" << percentile(.50)
                      << ",\"p95_ms\":" << percentile(.95) << ",\"p99_ms\":" << percentile(.99)
                      << ",\"max_ms\":" << times.back() << ",\"timeouts\":" << timeouts
                      << ",\"errors\":" << errors << ",\"last_returned\":" << returned << "}\n";
            return timeouts || errors ? 1 : 0;
        }
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 2; }
    return 0;
}
