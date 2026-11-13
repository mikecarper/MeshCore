"""Keep complete ESP32 infrastructure firmware inside physical dual-OTA slots.

No source-built MeshCore/framework/dependency code uses C++ exception handlers.
Arduino otherwise forces unwinding metadata into each linked function. Apply
this policy after framework flags are populated, including library clones.
Constrained 4 MiB infrastructure images also share the already-qualified
compact Ed25519 arithmetic/hash and lossless OTA target-name table.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path("scripts").resolve()
SPEC = importlib.util.spec_from_file_location(
    "esp32_full_size_source_policy", SCRIPT_DIR / "nrf52_flash_trim.py")
SOURCE_POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE_POLICY)

ROLES = (
    "../examples/simple_repeater/main.cpp",
    "../examples/simple_room_server/main.cpp",
    "../examples/simple_sensor/main.cpp",
)
EXCLUDED_ROLES = (
    "../examples/companion_radio/main.cpp",
    "../examples/companion_radio/MyMesh.cpp",
    "../examples/simple_secure_chat/main.cpp",
    "../examples/kiss_modem/main.cpp",
    "../examples/partition_expander/main.cpp",
    "../examples/esp32_partition_migrator/main.cpp",
    "../examples/esp32_partition_legacy_seed/main.cpp",
)
COMPACT_DEFINES = {
    # SMALL_BASE uses the existing 960-byte table, not a RAM-built table.
    "ED25519_SMALL_BASE": 1,
    "ED25519_COMPACT_SCALAR": 1,
    "ED25519_COMPACT_FIELD": 1,
    "ED25519_USE_CRYPTO_SHA512": 1,
    "OTA_TARGET_NAME_FRONT_CODED": 1,
}
POSITIVE_FLAGS = {"-fexceptions", "-funwind-tables", "-fasynchronous-unwind-tables"}
UNWIND_FLAGS = ["-fno-unwind-tables", "-fno-asynchronous-unwind-tables"]


def infrastructure_policy(platform, mcu, src_filter, *, full=False, companion=False):
    selected = SOURCE_POLICY.source_selected
    return bool(
        full and not companion and platform == "espressif32"
        and str(mcu).lower().startswith("esp32")
        and sum(selected(src_filter, source) for source in ROLES) == 1
        and not any(selected(src_filter, source) for source in EXCLUDED_ROLES)
    )


def compact_definitions(flash_bytes, flags=(), cppdefines=()):
    if flash_bytes > 4 * 1024 * 1024:
        return {}
    values = SOURCE_POLICY.flag_defines(flags, cppdefines)
    definitions = {name: value for name, value in COMPACT_DEFINES.items() if name not in values}
    # An explicitly chosen base implementation remains authoritative.
    if "ED25519_COMPACT_BASE" in values:
        definitions.pop("ED25519_SMALL_BASE", None)
    return definitions


def flag_values(values):
    return values.split() if isinstance(values, str) else list(values)


def apply_final_flags(build_env):
    """Runs per object after SDK defaults and library-clone flags exist."""
    updates = {}
    for name in ("CCFLAGS", "CFLAGS", "CXXFLAGS", "LINKFLAGS"):
        values = [value for value in flag_values(build_env.get(name, []))
                  if value not in POSITIVE_FLAGS]
        additions = UNWIND_FLAGS + (["-fno-exceptions"] if name in ("CXXFLAGS", "LINKFLAGS") else [])
        updates[name] = values + [flag for flag in additions if flag not in values]
    build_env.Replace(**updates)


def prepare_source(build_env, node):
    # Earlier middleware may already have substituted a private patched source.
    # Change flags in the actual clone, preserving that substituted node.
    apply_final_flags(build_env)
    values = SOURCE_POLICY.flag_defines(
        build_env.get("BUILD_FLAGS", ()), build_env.get("CPPDEFINES", ()))
    if SOURCE_POLICY._enabled(values.get("MESH_CLI_SHARE_MEMORY_OPERATIONS", "0")):
        source = Path(node.srcnode().get_abspath()).resolve()
        parser = Path(build_env.subst("$PROJECT_DIR/src/helpers/CommonCLI.cpp")).resolve()
        if source == parser:
            # This parser is not an RF hot path. Keep one-use settings helpers
            # out of line on the constrained C6 images, preserving all bytes.
            flags = flag_values(build_env.get("CCFLAGS", ()))
            if "-fno-inline-functions-called-once" not in flags:
                flags.append("-fno-inline-functions-called-once")
            build_env.Replace(CCFLAGS=flags)
    return node


def flash_size_bytes(value):
    text = str(value).strip().upper()
    for suffix, multiplier in (("MB", 1024 * 1024), ("KB", 1024)):
        if text.endswith(suffix):
            return int(text[:-len(suffix)]) * multiplier
    return int(text, 0)


def install(build_env, process_environment=None):
    settings = os.environ if process_environment is None else process_environment
    board = build_env.BoardConfig()
    source_filter = build_env.get("SRC_FILTER")
    if source_filter is None:
        source_filter = build_env.GetProjectOption("build_src_filter", "")
    if not infrastructure_policy(
        build_env.PioPlatform().name, board.get("build.mcu", ""), source_filter,
        full=settings.get("MESHCORE_ESP32_FULL_BUILD") == "1",
        companion=settings.get("MESHCORE_COMPANION_RADIO_FULL") == "1",
    ):
        return False
    definitions = compact_definitions(
        flash_size_bytes(board.get("upload.flash_size", "4MB")),
        build_env.get("BUILD_FLAGS", ()), build_env.get("CPPDEFINES", ()),
    )
    if definitions:
        build_env.AppendUnique(CPPDEFINES=list(definitions.items()))
    # Pre-script flags may be followed by SDK -fexceptions. Middleware sees
    # the populated environment for each framework/library/application object.
    build_env.AddBuildMiddleware(prepare_source)
    # LINKFLAGS belong to this final application environment, not lib clones.
    # This also covers objects emitted by a custom builder with no source hook.
    apply_final_flags(build_env)
    print("ESP32 Full infrastructure: no exception/unwind metadata; capacities and dual OTA retained")
    return True


if "Import" in globals():
    Import("env")
    install(env)
