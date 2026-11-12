// Copyright 2026 PantingEYEs. All rights reserved.
#include <opencc.h>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
struct Example { const char* config; const char* input; const char* expected; };
int main(int argc, char** argv) {
  if (argc != 2) return 2;
  const std::string directory = argv[1];
  const std::vector<Example> cases = {
    {"t2s.json", "漢語轉換", "汉语转换"},
    {"s2t.json", "汉语转换", "漢語轉換"},
    {"t2s.json", "ABC 123 日本語 🙂 é", "ABC 123 日本语 🙂 é"},
    {"s2t.json", "ABC 123 カタカナ 🙂 é", "ABC 123 カタカナ 🙂 é"},
    {"t2s.json", "", ""},
    {"s2t.json", "", ""}
  };
  for (const auto& e : cases) {
    opencc_t handle = opencc_open((directory + "/" + e.config).c_str());
    if (handle == reinterpret_cast<opencc_t>(-1)) throw std::runtime_error(opencc_error());
    char* converted = opencc_convert_utf8(handle, e.input, static_cast<size_t>(-1));
    if (!converted) throw std::runtime_error(opencc_error());
    const bool match = std::string(converted) == e.expected;
    opencc_convert_utf8_free(converted);
    if (opencc_close(handle) != 0) throw std::runtime_error("close failed");
    if (!match) throw std::runtime_error(std::string("conversion mismatch: ") + e.config);
  }
  opencc_t rejected = opencc_open((directory + "/test-owned-missing-config.json").c_str());
  if (rejected != reinterpret_cast<opencc_t>(-1)) {
    opencc_close(rejected);
    throw std::runtime_error("missing configuration accepted");
  }
  if (!opencc_error() || std::strlen(opencc_error()) == 0)
    throw std::runtime_error("missing configuration did not report an error");
  std::cout << "{\"passed\":7,\"scope\":\"host C API with pinned IPA Board-compatible OpenCC 1.2.0 text resources\"}\n";
}