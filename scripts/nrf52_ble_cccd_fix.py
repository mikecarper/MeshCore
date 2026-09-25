"""Apply nRF52 Bluefruit fixes in private build copies of framework sources.

The pinned core saves every bonded CCCD write from its callback worker into
InternalFS. On a T1000-E Full Companion this reproducibly resets the radio as
the client enables notifications, even after formatting InternalFS. Bond-key
persistence still works. The Companion keeps recent CCCD state in RAM across
BLE reconnects, without a live flash write.
Patch a private build copy; never edit PlatformIO's shared framework cache.
With the pinned GCC 14/LTO toolchain, the optimized Bluefruit.begin() fails
on the RAK4631 repeater: S140 rejects a valid single-peripheral role request
with NRF_ERROR_RESOURCES. The unoptimized method starts DFU successfully.
"""

from pathlib import Path


OLD_SAVE = """      {
        conn->saveCccd();
      }
"""
FIXED_SAVE = """      {
#if !defined(COMPANION_RADIO_FULL) || !COMPANION_RADIO_FULL
        conn->saveCccd();
#endif
      }
"""
OLD_LOAD = "        if ( !loadCccd() )  sd_ble_gatts_sys_attr_set(_conn_hdl, NULL, 0, 0);\n"
FIXED_LOAD = """#if defined(COMPANION_RADIO_FULL) && COMPANION_RADIO_FULL
        if ( !mesh_nrf52_restore_ram_cccd(_conn_hdl, &_bond_id_addr) &&
             !loadCccd() )
        {
          sd_ble_gatts_sys_attr_set(_conn_hdl, NULL, 0, 0);
        }
#else
        if ( !loadCccd() )  sd_ble_gatts_sys_attr_set(_conn_hdl, NULL, 0, 0);
#endif
"""
OLD_CONNECTION_INCLUDE = '#include "bluefruit.h"\n'
FIXED_CONNECTION_INCLUDE = """#include "bluefruit.h"

#if defined(COMPANION_RADIO_FULL) && COMPANION_RADIO_FULL
bool mesh_nrf52_restore_ram_cccd(uint16_t conn_handle,
                                  const ble_gap_addr_t* peer);
#endif
"""
OLD_BEGIN = "bool AdafruitBluefruit::begin(uint8_t prph_count, uint8_t central_count)"
FIXED_BEGIN = (
    'bool __attribute__((noinline, optimize("O0"))) '
    'AdafruitBluefruit::begin(uint8_t prph_count, uint8_t central_count)'
)


def patched_source(source):
    source = source.replace("\r\n", "\n")
    if FIXED_SAVE in source and OLD_SAVE not in source:
        return source
    if source.count(OLD_SAVE) != 1:
        raise RuntimeError(
            "nRF52 BLE CCCD fix: unrecognized framework source; review before building"
        )
    return source.replace(OLD_SAVE, FIXED_SAVE)


def patched_connection_source(source):
    source = source.replace("\r\n", "\n")
    if FIXED_LOAD in source and FIXED_CONNECTION_INCLUDE in source:
        return source
    if source.count(OLD_LOAD) != 1 or source.count(OLD_CONNECTION_INCLUDE) != 1:
        raise RuntimeError(
            "nRF52 BLE CCCD fix: unrecognized BLEConnection source; review before building"
        )
    return source.replace(OLD_CONNECTION_INCLUDE, FIXED_CONNECTION_INCLUDE).replace(
        OLD_LOAD, FIXED_LOAD
    )


def patched_bluefruit_source(source):
    source = source.replace("\r\n", "\n")
    if FIXED_BEGIN in source and OLD_BEGIN not in source:
        return source
    if source.count(OLD_BEGIN) != 1:
        raise RuntimeError(
            "nRF52 BLE startup fix: unrecognized framework source; review before building"
        )
    return source.replace(OLD_BEGIN, FIXED_BEGIN)


def replace_framework_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    if source.name not in ("BLEGatt.cpp", "BLEConnection.cpp", "bluefruit.cpp") or source.parent.name != "src":
        return node
    original = source.read_text(encoding="utf-8")
    if source.name == "BLEGatt.cpp":
        patched = patched_source(original)
    elif source.name == "BLEConnection.cpp":
        patched = patched_connection_source(original)
    else:
        patched = patched_bluefruit_source(original)
    build_env.AppendUnique(CPPPATH=[str(source.parent)])
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-nrf52-ble" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    print(f"nRF52 BLE: private framework fix in {source.name}")
    return build_env.File(str(destination))


if "Import" in globals():
    Import("env")
    env.AddBuildMiddleware(replace_framework_source, "*BLEGatt.cpp")
    env.AddBuildMiddleware(replace_framework_source, "*BLEConnection.cpp")
    env.AddBuildMiddleware(replace_framework_source, "*bluefruit.cpp")
