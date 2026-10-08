"""Use the smaller ClientInfo layout only for an effective repeater source role.

One shared compiler definition covers application and helper translation units.
Unknown/custom roles keep the complete layout; room and sensor session fields
are never removed from builds that use them.
"""

import importlib.util
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path("scripts").resolve()
SPEC = importlib.util.spec_from_file_location("client_acl_source_policy", SCRIPT_DIR / "nrf52_flash_trim.py")
SOURCE_POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE_POLICY)

DEFINITION = "MESH_CLIENT_REPEATER_ONLY"
REPEATER_SOURCES = ("../examples/simple_repeater/main.cpp", "../examples/simple_repeater/MyMesh.cpp")
OTHER_ROLES = ("simple_room_server", "simple_sensor", "companion_radio", "simple_secure_chat",
               "kiss_modem", "partition_expander", "esp32_partition_migrator", "esp32_partition_legacy_seed")


def role_policy(src_filter, flags=(), cppdefines=()):
    values = SOURCE_POLICY.flag_defines(flags, cppdefines)
    selected = SOURCE_POLICY.source_selected
    qualified = (all(selected(src_filter, source) for source in REPEATER_SOURCES)
                 and not any(selected(src_filter, "../examples/" + role + "/main.cpp")
                             or selected(src_filter, "../examples/" + role + "/MyMesh.cpp")
                             or selected(src_filter, "../examples/" + role + "/SensorMesh.cpp")
                             for role in OTHER_ROLES)
                 and not any(name.startswith("COMPANION_") for name in values))
    if DEFINITION in values:
        value = values[DEFINITION]
        text = "0" if value is None else str(value).strip("\"'() ")
        if text not in ("0", "1"):
            raise ValueError(DEFINITION + " override must be 0 or 1")
        if text == "1" and not qualified:
            raise ValueError(DEFINITION + "=1 requires only the repeater source role")
        return {}
    return {DEFINITION: 1} if qualified else {}


def install(build_env):
    src_filter = build_env.get("SRC_FILTER")
    if src_filter is None:
        src_filter = build_env.GetProjectOption("build_src_filter", "")
    definitions = role_policy(src_filter, build_env.get("BUILD_FLAGS", ()),
                              build_env.get("CPPDEFINES", ()))
    if definitions:
        build_env.AppendUnique(CPPDEFINES=list(definitions.items()))


if "Import" in globals():
    Import("env")
    install(env)
