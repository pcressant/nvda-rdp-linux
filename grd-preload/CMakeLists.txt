cmake_minimum_required(VERSION 3.20)
project(nvda-rdp-grd-preload VERSION 0.5.0 LANGUAGES C)

include(GNUInstallDirs)

# Loaded with LD_PRELOAD into GNOME Remote Desktop's session daemon. WinPR 3
# provides the headers only: its functions are looked up at run time in the
# process, so that they are GNOME Remote Desktop's own (see the source).
find_package(WinPR 3 CONFIG REQUIRED)
find_package(Threads REQUIRED)

add_library(nvda-rdp-grd-preload MODULE nvda_rdp_grd_preload.c)
target_compile_features(nvda-rdp-grd-preload PRIVATE c_std_11)
# No -Wpedantic: ISO C forbids converting dlsym's result to a function
# pointer, which POSIX requires to work and this library does for every call.
target_compile_options(nvda-rdp-grd-preload PRIVATE -Wall -Wextra)
target_include_directories(
  nvda-rdp-grd-preload PRIVATE $<TARGET_PROPERTY:winpr,INTERFACE_INCLUDE_DIRECTORIES>
)
target_link_libraries(nvda-rdp-grd-preload PRIVATE Threads::Threads ${CMAKE_DL_LIBS})
set_target_properties(nvda-rdp-grd-preload PROPERTIES PREFIX "lib")

install(TARGETS nvda-rdp-grd-preload LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}/nvda-rdp-linux")
