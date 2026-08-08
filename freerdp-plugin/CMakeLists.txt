cmake_minimum_required(VERSION 3.20)
project(nvda-rdp-dvc VERSION 0.1.0 LANGUAGES C)

find_package(FreeRDP-Client 3.30 CONFIG REQUIRED)
find_package(WinPR 3.30 CONFIG REQUIRED)
find_package(Threads REQUIRED)

add_library(nvda-rdp-dvc MODULE nvda_rdp_dvc.c)
target_compile_features(nvda-rdp-dvc PRIVATE c_std_11)
target_compile_options(nvda-rdp-dvc PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(nvda-rdp-dvc PRIVATE freerdp-client winpr Threads::Threads)

# FreeRDP's /dvc:nvda-rdp-dvc loader expects this exact file name.
set_target_properties(
  nvda-rdp-dvc
  PROPERTIES
    OUTPUT_NAME "nvda-rdp-dvc-client"
    INSTALL_RPATH "$ORIGIN/.."
)

install(TARGETS nvda-rdp-dvc LIBRARY DESTINATION "lib/freerdp3")
