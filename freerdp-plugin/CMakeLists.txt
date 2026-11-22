cmake_minimum_required(VERSION 3.20)
project(nvda-rdp-dvc VERSION 0.4.0 LANGUAGES C)

include(GNUInstallDirs)

# Developed against FreeRDP 3.30; only the FreeRDP 3 dynamic virtual channel
# plugin API is used (DVCPluginEntry, IWTSPlugin, IWTSVirtualChannelManager).
find_package(FreeRDP 3 CONFIG REQUIRED)
find_package(FreeRDP-Client 3 CONFIG REQUIRED)
find_package(WinPR 3 CONFIG REQUIRED)
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

# FreeRDP only loads plugins from its own plugin directory, which its CMake
# package exports relative to its install prefix (e.g. lib/freerdp3 on Arch
# and Nix, lib/x86_64-linux-gnu/freerdp3 on Debian).
if(DEFINED FreeRDP_RELATIVE_PLUGIN_DIR)
  set(NVDA_RDP_PLUGIN_DIR "${FreeRDP_RELATIVE_PLUGIN_DIR}")
else()
  set(NVDA_RDP_PLUGIN_DIR "${CMAKE_INSTALL_LIBDIR}/freerdp3")
endif()
set(NVDA_RDP_PLUGIN_DIR "${NVDA_RDP_PLUGIN_DIR}" CACHE STRING
  "Install directory of the plugin, relative to CMAKE_INSTALL_PREFIX")
message(STATUS "Installing the FreeRDP plugin into ${NVDA_RDP_PLUGIN_DIR}")

install(TARGETS nvda-rdp-dvc LIBRARY DESTINATION "${NVDA_RDP_PLUGIN_DIR}")
