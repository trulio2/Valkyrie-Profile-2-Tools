cmake_minimum_required(VERSION 3.20)
project(vp2_tac_encoder C)

if(NOT DEFINED VGMSTREAM_SOURCE)
    message(FATAL_ERROR "VGMSTREAM_SOURCE must point at the pinned checkout")
endif()

add_executable(vp2-tac-encode tac_encode.c)
target_include_directories(
    vp2-tac-encode PRIVATE "${VGMSTREAM_SOURCE}/src/coding/libs"
)
if(NOT MSVC)
    target_link_libraries(vp2-tac-encode PRIVATE m)
endif()

