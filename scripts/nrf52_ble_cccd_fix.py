"""Avoid Bluefruit's unstable live CCCD persistence on nRF52 Full Companions.

The pinned core saves every bonded CCCD write from its callback worker into
InternalFS. On a T1000-E Full Companion this reproducibly resets the radio as
the client enables notifications, even after formatting InternalFS. Bond-key
persistence still works. The Companion client re-subscribes on connection;
this workaround sacrifices CCCD persistence, not key-based reconnection.
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


def patched_source(source):
    source = source.replace("\r\n", "\n")
    if FIXED_SAVE in source and OLD_SAVE not in source:
        return source
    if source.count(OLD_SAVE) != 1:
        raise RuntimeError(
            "nRF52 BLE CCCD fix: unrecognized framework source; review before building"
        )
    return source.replace(OLD_SAVE, FIXED_SAVE)


def replace_framework_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    if source.name != "BLEGatt.cpp" or source.parent.name != "src":
        return node
    patched = patched_source(source.read_text(encoding="utf-8"))
    build_env.AppendUnique(CPPPATH=[str(source.parent)])
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-nrf52-ble" / "BLEGatt.cpp"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    print("nRF52 BLE: Full Companion CCCD persistence workaround enabled")
    return build_env.File(str(destination))


if "Import" in globals():
    Import("env")
    env.AddBuildMiddleware(replace_framework_source, "*BLEGatt.cpp")
