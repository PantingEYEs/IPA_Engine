# Copyright 2026 PantingEYEs. All rights reserved.
cmake_minimum_required(VERSION 3.31)
project(ipa_latinime LANGUAGES CXX)
if(NOT ANDROID OR NOT ANDROID_PLATFORM STREQUAL "android-26")
  message(FATAL_ERROR "This recipe requires Android API 26 via the pinned NDK")
endif()
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
if(NOT DEFINED HELIBOARD_SOURCE OR NOT DEFINED GENERATED_SOURCE_LIST)
  message(FATAL_ERROR "Use scripts/build.py to verify sources and configure this project")
endif()
include("${GENERATED_SOURCE_LIST}")
set(CORE_ROOT "${HELIBOARD_SOURCE}/app/src/main/jni/src")
list(TRANSFORM LATINIME_CORE_SOURCES PREPEND "${CORE_ROOT}/")
add_library(latinime_core STATIC ${LATINIME_CORE_SOURCES})
target_include_directories(latinime_core SYSTEM PUBLIC "${CORE_ROOT}")
target_compile_options(latinime_core PRIVATE -Wall -Wextra -Wno-unused-parameter
  -Wno-unused-function -Wno-sign-compare -ffunction-sections -fdata-sections
  "-ffile-prefix-map=${HELIBOARD_SOURCE}=/upstream/HeliBoard")
option(LATINIME_BUILD_BRIDGE "Build the new read-only JNI owner" ON)
if(LATINIME_BUILD_BRIDGE)
  if(NOT DEFINED IDENTITY_INCLUDE)
    message(FATAL_ERROR "Missing verified identity header directory")
  endif()
  add_library(ipa_latinime SHARED bridge/latinime_jni.cpp)
  target_include_directories(ipa_latinime PRIVATE "${IDENTITY_INCLUDE}")
  target_compile_options(ipa_latinime PRIVATE -Wall -Wextra -Werror
    -ffunction-sections -fdata-sections
    "-ffile-prefix-map=${CMAKE_SOURCE_DIR}=/source/LatinIME")
  target_link_libraries(ipa_latinime PRIVATE latinime_core dl)
  target_link_options(ipa_latinime PRIVATE
    "-Wl,--version-script=${CMAKE_SOURCE_DIR}/bridge/exports.map"
    -Wl,--no-undefined -Wl,--gc-sections -Wl,--exclude-libs,ALL
    -Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384
    -Wl,-z,relro -Wl,-z,now -Wl,--build-id=sha1)
  set_target_properties(ipa_latinime PROPERTIES LINK_DEPENDS "${CMAKE_SOURCE_DIR}/bridge/exports.map")
endif()
