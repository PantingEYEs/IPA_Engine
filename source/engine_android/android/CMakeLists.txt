cmake_minimum_required(VERSION 3.16)
project(ipa_graph_android VERSION 1.0 LANGUAGES CXX)

if(NOT ANDROID_ABI STREQUAL "arm64-v8a")
    message(FATAL_ERROR "This build currently supports arm64-v8a")
endif()
find_package(Python3 COMPONENTS Interpreter REQUIRED)
set(RUST_LIBRARY "${CMAKE_CURRENT_SOURCE_DIR}/../../data/engine_android/builds/rust/aarch64-linux-android/release/libipa_graph.a")
file(GLOB_RECURSE RUST_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/../ipa_graph/src/*.rs")
add_custom_command(OUTPUT "${RUST_LIBRARY}"
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_CURRENT_SOURCE_DIR}/../scripts/build_rust_core.py"
            --target aarch64-linux-android --ndk "${CMAKE_ANDROID_NDK}" --api "${ANDROID_PLATFORM_LEVEL}"
    DEPENDS ${RUST_SOURCES} "${CMAKE_CURRENT_SOURCE_DIR}/../ipa_graph/Cargo.toml"
    VERBATIM)
add_custom_target(ipa_graph_rust_build DEPENDS "${RUST_LIBRARY}")
add_library(ipa_graph_rust STATIC IMPORTED)
set_target_properties(ipa_graph_rust PROPERTIES IMPORTED_LOCATION "${RUST_LIBRARY}")
add_library(ipa_graph_android SHARED src/main/cpp/ipa_graph_jni.cpp)
add_dependencies(ipa_graph_android ipa_graph_rust_build)
target_link_libraries(ipa_graph_android PRIVATE ipa_graph_rust dl m log)
target_link_options(ipa_graph_android PRIVATE -Wl,--exclude-libs,ALL -Wl,-z,max-page-size=16384)
target_compile_features(ipa_graph_android PRIVATE cxx_std_17)
target_compile_options(ipa_graph_android PRIVATE -Wall -Wextra -Wpedantic)
