"""Avoid Bluefruit's unstable live CCCD persistence on nRF52 Full Companions.

The pinned core saves every bonded CCCD write from its callback worker into
InternalFS. On a T1000-E Full Companion this reproducibly resets the radio as
the client enables notifications, even after formatting InternalFS. Bond-key
persistence still works. The Companion keeps recent CCCD state in RAM across
BLE reconnects, without a live flash write.
Patch a private build copy; never edit PlatformIO's shared framework cache.
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


def replace_framework_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    if source.name not in ("BLEGatt.cpp", "BLEConnection.cpp") or source.parent.name != "src":
        return node
    original = source.read_text(encoding="utf-8")
    patched = (patched_source(original) if source.name == "BLEGatt.cpp"
               else patched_connection_source(original))
    build_env.AppendUnique(CPPPATH=[str(source.parent)])
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-nrf52-ble" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    print(f"nRF52 BLE: Full Companion CCCD workaround in {source.name}")
    return build_env.File(str(destination))


if "Import" in globals():
    Import("env")
    env.AddBuildMiddleware(replace_framework_source, "*BLEGatt.cpp")
    env.AddBuildMiddleware(replace_framework_source, "*BLEConnection.cpp")
