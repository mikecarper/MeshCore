"""Derive feature-preserving role definitions from effective application sources.

Shared compiler definitions cover application and helper translation units.
Unknown/custom roles keep the complete layout; room and sensor session fields
are never removed from builds that use them. Companions omit only unbound
forwarding-region callbacks because that role has no RegionMap.
"""

import importlib.util
from pathlib import Path
import re


SCRIPT_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path("scripts").resolve()
SPEC = importlib.util.spec_from_file_location("client_acl_source_policy", SCRIPT_DIR / "nrf52_flash_trim.py")
SOURCE_POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOURCE_POLICY)

DEFINITION = "MESH_CLIENT_REPEATER_ONLY"
REPEATER_SOURCES = ("../examples/simple_repeater/main.cpp", "../examples/simple_repeater/MyMesh.cpp")
OTHER_ROLES = ("simple_room_server", "simple_sensor", "companion_radio", "simple_secure_chat",
               "kiss_modem", "partition_expander", "esp32_partition_migrator", "esp32_partition_legacy_seed")
COMPANION_DEFINITION = "MESH_ROLE_COMPANION"
COMPANION_SOURCES = ("../examples/companion_radio/main.cpp", "../examples/companion_radio/MyMesh.cpp")
EMERGENCY_DEFINITION = "MESH_ENABLE_EMERGENCY_POWER"


def emergency_power_policy(src_filter, flags=(), cppdefines=()):
    """Enable scheduled battery sleep only on complete supported infrastructure roles."""
    values = SOURCE_POLICY.flag_defines(flags, cppdefines)
    if EMERGENCY_DEFINITION in values:
        value = str(values[EMERGENCY_DEFINITION]).strip("\"'() ")
        if value not in ("0", "1"):
            raise ValueError(EMERGENCY_DEFINITION + " override must be 0 or 1")
        return {}
    if not any(name in values for name in ("ESP32_PLATFORM", "NRF52_PLATFORM")):
        return {}
    if str(values.get("MESH_ENABLE_BATTERY_FORWARDING", "1")).strip("\"'() ") == "0":
        return {}
    selected = SOURCE_POLICY.source_selected
    text = src_filter if isinstance(src_filter, str) else " ".join(map(str, src_filter))
    includes = re.findall(r"\+<\.\./examples/([^>]+)>", text.replace("\\", "/"))
    for role, mesh_source in (("simple_repeater", "MyMesh.cpp"),
                              ("simple_room_server", "MyMesh.cpp"),
                              ("simple_sensor", "SensorMesh.cpp")):
        if includes and all(path == role or path.startswith(role + "/") for path in includes) \
                and all(selected(src_filter, "../examples/" + role + "/" + source)
                        for source in ("main.cpp", mesh_source)):
            return {EMERGENCY_DEFINITION: 1}
    return {}


def companion_role_policy(src_filter, flags=(), cppdefines=()):
    """Exclude unbound forwarding-scope callbacks, retaining all radio commands.

    Companions have no RegionMap. Select the complete exclusive source role,
    never an environment-name alias, so every translation unit shares its ABI.
    Unknown and mixed roles retain the general callback implementation.
    """
    values = SOURCE_POLICY.flag_defines(flags, cppdefines)
    selected = SOURCE_POLICY.source_selected
    other_roles = ("simple_repeater", *(role for role in OTHER_ROLES
                                       if role != "companion_radio"))
    filter_text = src_filter if isinstance(src_filter, str) else " ".join(map(str, src_filter))
    example_includes = re.findall(r"\+<\.\./examples/([^>]+)>", filter_text.replace("\\", "/"))
    known_companion_only = all(path == "companion_radio" or path.startswith("companion_radio/")
                               for path in example_includes)
    qualified = (known_companion_only
                 and all(selected(src_filter, source) for source in COMPANION_SOURCES)
                 and not any(selected(src_filter, "../examples/" + role + "/" + source)
                             for role in other_roles
                             for source in ("main.cpp", "MyMesh.cpp", "SensorMesh.cpp")))
    if COMPANION_DEFINITION in values:
        value = values[COMPANION_DEFINITION]
        text = "0" if value is None else str(value).strip("\"'() ")
        if text not in ("0", "1"):
            raise ValueError(COMPANION_DEFINITION + " override must be 0 or 1")
        if text == "1" and not qualified:
            raise ValueError(COMPANION_DEFINITION + "=1 requires only the Companion source role")
        return {}
    return {COMPANION_DEFINITION: 1} if qualified else {}


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
    definitions.update(companion_role_policy(src_filter, build_env.get("BUILD_FLAGS", ()),
                                            build_env.get("CPPDEFINES", ())))
    definitions.update(emergency_power_policy(src_filter, build_env.get("BUILD_FLAGS", ()),
                                             build_env.get("CPPDEFINES", ())))
    if definitions:
        build_env.AppendUnique(CPPDEFINES=list(definitions.items()))


if "Import" in globals():
    Import("env")
    install(env)
