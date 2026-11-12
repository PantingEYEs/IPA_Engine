# Copyright 2026 PantingEYEs. All rights reserved.
cmake_minimum_required(VERSION 3.22)
project(ipa_opencc_build LANGUAGES CXX)
if(NOT DEFINED OPENCC_SOURCE_DIR)
  message(FATAL_ERROR "Set OPENCC_SOURCE_DIR to the pinned official source checkout")
endif()
set(OPENCC_ENABLE_INSTALL ON CACHE BOOL "" FORCE)
set(BUILD_OPENCC_JIEBA_PLUGIN OFF CACHE BOOL "" FORCE)
set(ENABLE_GTEST OFF CACHE BOOL "" FORCE)
set(ENABLE_BENCHMARK OFF CACHE BOOL "" FORCE)
set(BUILD_PYTHON OFF CACHE BOOL "" FORCE)
set(BUILD_DOCUMENTATION OFF CACHE BOOL "" FORCE)
set(OPENCC_DICT_FORMAT text CACHE STRING "" FORCE)
add_subdirectory("${OPENCC_SOURCE_DIR}" upstream EXCLUDE_FROM_ALL)
if(ANDROID)
  # Android APK loaders use plain .so names, not desktop versioned SONAME links.
  set_target_properties(libopencc PROPERTIES VERSION "" SOVERSION "")
  target_link_options(libopencc PRIVATE
    "-Wl,-z,max-page-size=16384" "-Wl,-z,common-page-size=16384"
    "-Wl,--exclude-libs,ALL")
endif()