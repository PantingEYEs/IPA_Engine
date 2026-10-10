// Incremental construction of a minimal acyclic acceptor from sorted byte keys.
// Labels are alphabet IDs, not UTF-8 bytes. Canonical children precede parents.
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

struct Node {
    bool final = false;
    std::vector<std::pair<uint8_t, uint32_t>> arcs;
};
static void put32(std::ostream &out, uint32_t x) {
    for (int i = 0; i < 4; ++i) out.put(char(x >> (8 * i)));
}
static void put64(std::ostream &out, uint64_t x) {
    for (int i = 0; i < 8; ++i) out.put(char(x >> (8 * i)));
}

int main(int argc, char **argv) {
    try {
        if (argc != 3 && argc != 4) throw std::runtime_error("usage: build_dawg KEY_STREAM GRAPH_SECTIONS [24|32]");
        int index_bits = argc == 4 ? std::stoi(argv[3]) : 24;
        if (index_bits != 24 && index_bits != 32) throw std::runtime_error("index width must be 24 or 32");
        std::ifstream input(argv[1], std::ios::binary);
        if (!input) throw std::runtime_error("cannot open key stream");
        std::vector<Node> canonical;
        std::vector<uint32_t> counts;
        std::unordered_map<std::string, uint32_t> registry;
        std::vector<Node> path(1);
        std::string previous;
        uint64_t trie_nodes = 1;
        uint32_t keys = 0;
        auto minimize = [&](Node &node) -> uint32_t {
            std::string signature(1, node.final ? '\1' : '\0');
            uint64_t count = node.final ? 1 : 0;
            for (auto arc : node.arcs) {
                if (arc.second >= canonical.size()) throw std::runtime_error("unfrozen child");
                signature.push_back(char(arc.first));
                for (int i = 0; i < 4; ++i) signature.push_back(char(arc.second >> (8 * i)));
                count += counts[arc.second] & 0x7fffffffU;
            }
            auto found = registry.find(signature);
            if (found != registry.end()) return found->second;
            if (count > 0x7fffffffU) throw std::runtime_error("accepted suffix count exceeds 31 bits");
            uint64_t node_limit = index_bits == 24 ? 0x1000000ULL : 0xffffffffULL;
            if (canonical.size() >= node_limit) throw std::runtime_error("node count exceeds index target width");
            uint32_t id = static_cast<uint32_t>(canonical.size());
            registry.emplace(std::move(signature), id);
            counts.push_back(static_cast<uint32_t>(count) | (node.final ? 0x80000000U : 0));
            canonical.push_back(std::move(node));
            return id;
        };
        auto freeze_to = [&](size_t prefix) {
            while (path.size() > prefix + 1) {
                uint32_t id = minimize(path.back());
                path.pop_back();
                path.back().arcs.back().second = id;
            }
        };
        for (;;) {
            int lo = input.get();
            if (lo == EOF) break;
            int hi = input.get();
            if (hi == EOF) throw std::runtime_error("truncated key length");
            size_t length = static_cast<unsigned>(lo) | (static_cast<unsigned>(hi) << 8);
            if (!length || length > 64) throw std::runtime_error("key scalar length outside 1..64");
            std::string key(length, '\0');
            input.read(&key[0], static_cast<std::streamsize>(length));
            if (!input || key.find('\0') != std::string::npos) throw std::runtime_error("truncated key or zero label");
            // std::string ordering can depend on signed char; compare unsigned.
            auto less = [](const std::string &a, const std::string &b) {
                size_t n = std::min(a.size(), b.size());
                for (size_t i = 0; i < n; ++i) {
                    auto x = static_cast<unsigned char>(a[i]), y = static_cast<unsigned char>(b[i]);
                    if (x != y) return x < y;
                }
                return a.size() < b.size();
            };
            if (keys && !less(previous, key)) throw std::runtime_error("keys must be unique and sorted by labels");
            size_t shared = 0;
            while (shared < key.size() && shared < previous.size() && key[shared] == previous[shared]) ++shared;
            freeze_to(shared);
            for (size_t i = shared; i < key.size(); ++i) {
                path.back().arcs.emplace_back(static_cast<uint8_t>(key[i]), std::numeric_limits<uint32_t>::max());
                path.emplace_back();
            }
            path.back().final = true;
            trie_nodes += key.size() - shared;
            if (keys == 0x7fffffffU) throw std::runtime_error("too many keys");
            ++keys;
            previous = std::move(key);
        }
        freeze_to(0);
        uint32_t root = minimize(path.back());
        uint64_t arc_total = 0;
        for (const auto &node : canonical) arc_total += node.arcs.size();
        if (arc_total > 0xffffffffULL) throw std::runtime_error("arc count exceeds uint32");
        std::ofstream out(argv[2], std::ios::binary | std::ios::trunc);
        if (!out) throw std::runtime_error("cannot open graph sections");
        put32(out, static_cast<uint32_t>(canonical.size()));
        put32(out, static_cast<uint32_t>(arc_total));
        put32(out, keys);
        put32(out, root);
        put64(out, trie_nodes);
        uint32_t offset = 0;
        for (const auto &node : canonical) {
            put32(out, offset);
            offset += static_cast<uint32_t>(node.arcs.size());
        }
        put32(out, offset);
        for (uint32_t value : counts) put32(out, value);
        for (uint32_t id = 0; id < canonical.size(); ++id) {
            for (auto arc : canonical[id].arcs) {
                if (arc.second >= id) throw std::runtime_error("child must precede parent");
                out.put(char(arc.first));
                for (int i = 0; i < index_bits / 8; ++i) out.put(char(arc.second >> (8 * i)));
            }
        }
        out.close();
        if (!out) throw std::runtime_error("graph write failed");
        std::cout << "{\"nodes\":" << canonical.size() << ",\"arcs\":" << arc_total
                  << ",\"keys\":" << keys << ",\"root\":" << root
                  << ",\"trie_nodes\":" << trie_nodes << ",\"trie_arcs\":" << trie_nodes - 1 << "}\n";
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "build_dawg: " << error.what() << '\n';
        return 1;
    }
}
