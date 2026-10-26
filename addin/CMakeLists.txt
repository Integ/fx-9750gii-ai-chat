# AI Chat add-in for fx-9860G II (SH4), built with gint and the fxSDK.
# Configure and build with:  fxsdk build-fx

cmake_minimum_required(VERSION 3.15)
project(AIChat)

include(GenerateG1A)
include(Fxconv)
find_package(Gint 2.9 REQUIRED)

set(SOURCES
  src/main.c
)

add_executable(aichat ${SOURCES})
target_compile_options(aichat PRIVATE -Wall -Wextra -Os -g -flto)
target_link_libraries(aichat Gint::Gint)

if("${FXSDK_PLATFORM_LONG}" STREQUAL fx9860G)
  generate_g1a(TARGET aichat OUTPUT "AICHAT.g1a"
    NAME "AI Chat" ICON assets-fx/icon.png)
else()
  message(FATAL_ERROR "This add-in targets the fx-9860G II only (use fxsdk build-fx)")
endif()
