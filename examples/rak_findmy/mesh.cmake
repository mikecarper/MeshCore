set(RAK_TAG_ROLE sensor CACHE STRING "Tracker role, sensor or companion")
if(RAK_TAG_ROLE STREQUAL "companion")
  target_compile_definitions(app PRIVATE RAK_TAG_COMPANION=1)
elseif(NOT RAK_TAG_ROLE STREQUAL "sensor")
  message(FATAL_ERROR "RAK_TAG_ROLE must be sensor or companion")
endif()
target_include_directories(app PRIVATE "${CMAKE_CURRENT_LIST_DIR}/../../src")
set(MESHCORE_TAG_LIBDEPS "${CMAKE_CURRENT_LIST_DIR}/../../.pio/libdeps/RAK_WisMesh_Tag_sensor"
    CACHE PATH "Directory containing the RAK PlatformIO RadioLib, Crypto and MicroNMEA libraries")
if(NOT EXISTS "${MESHCORE_TAG_LIBDEPS}/RadioLib/src/Hal.cpp" OR
   NOT EXISTS "${MESHCORE_TAG_LIBDEPS}/Crypto/AES128.cpp" OR
   NOT EXISTS "${MESHCORE_TAG_LIBDEPS}/MicroNMEA/src/MicroNMEA.cpp")
  message(FATAL_ERROR "Build the RAK Sensor target or set MESHCORE_TAG_LIBDEPS to its library directory.")
endif()
set(crypto "${MESHCORE_TAG_LIBDEPS}/Crypto")
set(eddsa "${CMAKE_CURRENT_LIST_DIR}/../../lib/ed25519")
target_include_directories(app PRIVATE "${CMAKE_CURRENT_LIST_DIR}/compat" "${crypto}" "${eddsa}" "${MESHCORE_TAG_LIBDEPS}/MicroNMEA/src")
target_sources(app PRIVATE "${CMAKE_CURRENT_LIST_DIR}/src/mesh.cpp" "${CMAKE_CURRENT_LIST_DIR}/src/gnss.cpp"
  "${CMAKE_CURRENT_LIST_DIR}/src/battery.c"
  "${CMAKE_CURRENT_LIST_DIR}/../../src/Packet.cpp" "${CMAKE_CURRENT_LIST_DIR}/../../src/Utils.cpp"
  "${MESHCORE_TAG_LIBDEPS}/MicroNMEA/src/MicroNMEA.cpp"
  "${crypto}/Crypto.cpp" "${crypto}/Hash.cpp" "${crypto}/SHA256.cpp"
  "${crypto}/AES128.cpp" "${crypto}/AESCommon.cpp" "${crypto}/BlockCipher.cpp"
  "${eddsa}/keypair.c" "${eddsa}/key_exchange.c" "${eddsa}/sign.c"
  "${eddsa}/sc.c" "${eddsa}/fe.c" "${eddsa}/ge.c" "${eddsa}/sha512.c")

set(radiolib "${MESHCORE_TAG_LIBDEPS}/RadioLib/src")
target_include_directories(app PRIVATE "${radiolib}")
target_sources(app PRIVATE "${CMAKE_CURRENT_LIST_DIR}/src/radio.cpp"
  "${radiolib}/Hal.cpp" "${radiolib}/Module.cpp"
  "${radiolib}/modules/SX126x/SX1262.cpp" "${radiolib}/modules/SX126x/SX126x.cpp"
  "${radiolib}/modules/SX126x/SX126x_commands.cpp" "${radiolib}/modules/SX126x/SX126x_config.cpp"
  "${radiolib}/modules/SX126x/SX126x_LR_FHSS.cpp"
  "${radiolib}/protocols/PhysicalLayer/PhysicalLayer.cpp"
  "${radiolib}/utils/Utils.cpp" "${radiolib}/utils/CRC.cpp" "${radiolib}/utils/ConvCode.cpp")
