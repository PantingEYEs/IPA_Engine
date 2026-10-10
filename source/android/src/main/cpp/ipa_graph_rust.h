#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Opaque Rust engine. Calls may run concurrently, but must finish before close.
// A closed handle must never be used again. Rust output strings are owned by the
// caller until ipa_graph_string_free; UTF-8 inputs are borrowed for each call.
// The dictionary inode must not be modified or truncated while its handle is
// open. Atomically replacing the pathname with another inode is permitted.
typedef struct IpaGraphEngine IpaGraphEngine;
typedef struct IpaGraphQueryOptions {
    uint32_t candidate_limit;
    uint32_t prefix;
    uint32_t prefix_key_budget;
    uint32_t deadline_ms;
    uint32_t allow_omissions;
    uint32_t omission_state_budget;
} IpaGraphQueryOptions;

enum IpaGraphStatus {
    IPA_GRAPH_OK = 0,
    IPA_GRAPH_INVALID_ARGUMENT = 1,
    IPA_GRAPH_LOAD_ERROR = 2,
    IPA_GRAPH_PANIC = 3
};

int32_t ipa_graph_open(const uint8_t* path, size_t path_length,
        IpaGraphEngine** handle, char** diagnostic);
int32_t ipa_graph_close(IpaGraphEngine* handle);
int32_t ipa_graph_configure(const IpaGraphEngine* handle, uint32_t region_mask);
int32_t ipa_graph_accepts_input(const IpaGraphEngine* handle,
        const uint8_t* raw, size_t raw_length, uint32_t* accepts);
int32_t ipa_graph_segment_pattern(const IpaGraphEngine* handle, char** text);
int32_t ipa_graph_query(const IpaGraphEngine* handle, const uint8_t* raw,
        size_t raw_length, const IpaGraphQueryOptions* options, char** text);
int32_t ipa_graph_metadata(const IpaGraphEngine* handle, char** text);
int32_t ipa_graph_empty_result(uint32_t unsupported, char** text);
void ipa_graph_string_free(char* text);

#ifdef __cplusplus
}
#endif
