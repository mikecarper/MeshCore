cmake_minimum_required(VERSION 3.20)
get_filename_component(examples "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(fmn "${examples}/rak_findmy")
set(hub "${examples}/rak_findhub")
set(sid "${examples}/rak_sidewalk")
set(locator "$ENV{ZEPHYR_BASE}/../nrf/samples/bluetooth/fast_pair/locator_tag")
set(KCONFIG_ROOT "${locator}/Kconfig")
set(CONF_FILE "${locator}/configuration/prj.conf;${hub}/prj.conf;${sid}/prj.conf;${CMAKE_CURRENT_SOURCE_DIR}/prj.conf")
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(rak_locator_controller_test)

if(NOT BOARD STREQUAL "rak4631")
  message(FATAL_ERROR "This controller test requires the RAK WisMesh Tag")
endif()
# Keep each complete application in the measurement, including its USB commands.
set_source_files_properties("${fmn}/src/main.c" PROPERTIES COMPILE_DEFINITIONS "main=apple_entry")
set_source_files_properties("${hub}/main.c" PROPERTIES COMPILE_DEFINITIONS "main=findhub_entry")
set_source_files_properties("${sid}/main.c" PROPERTIES COMPILE_DEFINITIONS "main=sidewalk_entry")
target_sources(app PRIVATE main.c
  "${fmn}/src/main.c" "${fmn}/src/plan.c" "${fmn}/src/unprovisioned.c" "${fmn}/src/motion.cpp"
  "${hub}/main.c" "${hub}/locator.c" "${sid}/main.c"
  "${locator}/src/ring.c" "${locator}/src/factory_reset.c" "${locator}/src/ui/common.c")
target_include_directories(app PRIVATE "${locator}/.." "${locator}/include" "${locator}/src/ui")
zephyr_linker_sources(SECTIONS "${locator}/src/factory_reset_callbacks.ld")
zephyr_linker_sources(DATA_SECTIONS "${locator}/src/ui/request_listener.ld")
include("${fmn}/mesh.cmake")
include("${fmn}/nordic_sdk.cmake")
