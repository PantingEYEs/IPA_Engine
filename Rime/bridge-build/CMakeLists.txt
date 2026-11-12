cmake_minimum_required(VERSION 3.22)
project(ipa_rime LANGUAGES CXX)
# Copyright (c) 2026 PantingEYEs. All rights reserved.
get_filename_component(RIME_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
foreach(REQUIRED RIME_CORE_BUILD RIME_DEP_PREFIX OPENCC_PREFIX BOOST_ROOT IPA_RIME_BUILD_ID)
    if(NOT DEFINED ${REQUIRED})
        message(FATAL_ERROR "Missing ${REQUIRED}")
    endif()
endforeach()
add_library(ipa_rime SHARED "${RIME_ROOT}/bridge/rime_jni.cpp")
target_compile_features(ipa_rime PRIVATE cxx_std_17)
target_include_directories(ipa_rime SYSTEM PRIVATE
    "${RIME_ROOT}/.work/librime/src" "${RIME_CORE_BUILD}/src"
    "${OPENCC_PREFIX}/include/opencc" "${OPENCC_PREFIX}/include"
    "${RIME_DEP_PREFIX}/include" "${BOOST_ROOT}")
target_compile_definitions(ipa_rime PRIVATE
    IPA_RIME_BUILD_ID="${IPA_RIME_BUILD_ID}" IPA_OPENCC_VERSION="1.4.2"
    Opencc_BUILT_AS_STATIC YAML_CPP_STATIC_DEFINE)
target_compile_options(ipa_rime PRIVATE -Wall -Wextra -Werror -fvisibility=hidden -fvisibility-inlines-hidden)
target_link_libraries(ipa_rime PRIVATE
    "-Wl,--whole-archive" "${RIME_CORE_BUILD}/lib/librime.a" "-Wl,--no-whole-archive"
    "${RIME_DEP_PREFIX}/lib/libyaml-cpp.a" "${RIME_DEP_PREFIX}/lib/libleveldb.a"
    "${OPENCC_PREFIX}/lib/libopencc.a" "${OPENCC_PREFIX}/lib/libmarisa.a" dl)
target_link_options(ipa_rime PRIVATE
    "-Wl,-z,max-page-size=16384" "-Wl,-z,common-page-size=16384"
    "-Wl,-z,relro" "-Wl,-z,now" "-Wl,--exclude-libs,ALL" "-Wl,--no-undefined"
    "-Wl,--version-script=${CMAKE_CURRENT_LIST_DIR}/exports.map")
set_property(TARGET ipa_rime APPEND PROPERTY LINK_DEPENDS "${CMAKE_CURRENT_LIST_DIR}/exports.map")
