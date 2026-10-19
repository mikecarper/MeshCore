"""Apply nRF52 Bluefruit fixes in private build copies of framework sources.

Keep bonded CCCD persistence enabled, including Full Companions. The global
SoftDeviceSvcCompat.h guard protects the framework's system-attribute queries
from LTO miscompilation. The previous Full-only RAM workaround lost client
notification subscriptions after reboot while retaining the pairing keys.
Keep the RAM cache as a warm reconnect optimization, with the framework's
persistent bond record as the cold boot fallback.
Patch a private build copy; never edit PlatformIO's shared framework cache.
With the pinned GCC 14/LTO toolchain, the optimized Bluefruit.begin() fails
on the RAK4631 repeater: S140 rejects a valid single-peripheral role request
with NRF_ERROR_RESOURCES. The unoptimized method starts DFU successfully.
Cache the dispatch role before a disconnect deletes its BLEConnection. The
remaining peripheral/central routing must not dereference that freed object.
"""

from pathlib import Path


OLD_SAVE = """      {
        conn->saveCccd();
      }
"""
FIXED_SAVE = """      {
        // SoftDeviceSvcCompat.h protects system-attribute query side effects.
        conn->saveCccd();
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

DISCONNECT_PATCHES = (
    (
        "  BLEConnection* conn = this->Connection(conn_hdl);\n\n"
        '  LOG_LV2("BLE",',
        "  BLEConnection* conn = this->Connection(conn_hdl);\n"
        "  // Keep role routing valid after disconnect deletes the connection.\n"
        "  uint8_t conn_role = conn ? conn->getRole() : BLE_GAP_ROLE_INVALID;\n\n"
        '  LOG_LV2("BLE",',
    ),
    (
        "      conn = _connection[conn_hdl];\n\n"
        "      // Invoke connect callback\n",
        "      conn = _connection[conn_hdl];\n"
        "      conn_role = conn->getRole();\n\n"
        "      // Invoke connect callback\n",
    ),
    (
        "  if ( (conn == NULL) || (conn->getRole() == BLE_GAP_ROLE_PERIPH) )\n",
        "  if ( (conn_role == BLE_GAP_ROLE_INVALID) || (conn_role == BLE_GAP_ROLE_PERIPH) )\n",
    ),
    (
        "  if ( (conn == NULL) || (conn->getRole() == BLE_GAP_ROLE_CENTRAL) )\n",
        "  if ( (conn_role == BLE_GAP_ROLE_INVALID) || (conn_role == BLE_GAP_ROLE_CENTRAL) )\n",
    ),
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
    source = patched_disconnect_source(source)
    if FIXED_BEGIN in source and OLD_BEGIN not in source:
        return source
    if source.count(OLD_BEGIN) != 1:
        raise RuntimeError(
            "nRF52 BLE startup fix: unrecognized framework source; review before building"
        )
    return source.replace(OLD_BEGIN, FIXED_BEGIN)


def patched_disconnect_source(source):
    source = source.replace("\r\n", "\n")
    counts = [(source.count(old), source.count(fixed))
              for old, fixed in DISCONNECT_PATCHES]
    if all(old == 0 and fixed == 1 for old, fixed in counts):
        return source
    if not all(old == 1 and fixed == 0 for old, fixed in counts):
        raise RuntimeError(
            "nRF52 BLE disconnect fix: unrecognized framework dispatcher; review before building"
        )
    for old, fixed in DISCONNECT_PATCHES:
        source = source.replace(old, fixed)
    return source


def patched_trace_source(source):
    """Bracket real BLE dispatch in diagnostic builds, including stuck handlers."""
    include = '#include "bluefruit.h"\n'
    entry = 'void AdafruitBluefruit::_ble_handler(ble_evt_t* evt)\n{\n'
    exit_marker = '  if (_event_cb) _event_cb(evt);\n'
    marker = '// MeshCore diagnostic dispatch trace'
    if marker in source:
        return source
    if any(source.count(value) != 1 for value in (include, entry, exit_marker)):
        raise RuntimeError("nRF52 BLE trace: unrecognized event dispatcher")
    source = source.replace(include, include + '''
#if defined(MESH_NRF52_BLE_TRACE) && MESH_NRF52_BLE_TRACE
#include <helpers/nrf52/BleDebugTrace.h>
#endif
''')
    source = source.replace(entry, entry + '''  // MeshCore diagnostic dispatch trace
#if defined(MESH_NRF52_BLE_TRACE) && MESH_NRF52_BLE_TRACE
  meshBleTraceDispatchEnter(evt->header.evt_id, evt->evt.common_evt.conn_handle);
#endif
''')
    return source.replace(exit_marker, exit_marker + '''#if defined(MESH_NRF52_BLE_TRACE) && MESH_NRF52_BLE_TRACE
  meshBleTraceDispatchExit(evt->header.evt_id, evt->evt.common_evt.conn_handle);
#endif
''')


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
        if "MESH_NRF52_BLE_TRACE" in str(build_env.get("CPPDEFINES", [])):
            patched = patched_trace_source(patched)
            build_env.AppendUnique(CPPPATH=[build_env.subst("$PROJECT_DIR/src")])
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
