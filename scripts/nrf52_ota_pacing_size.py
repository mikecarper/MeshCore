"""Qualified RAK Full code generation; keep the naked RTOS port outside LTO.

No SDK source, assembly, firmware feature, or ABI is changed. The inspected
FreeRTOS port uses assembler literal pools which cannot safely follow GCC's
whole-program function layout. Only that exact file is compiled without LTO.
Instrumented HIL builds retain their existing framework bindings and options.
"""

import hashlib
import importlib.util
from pathlib import Path
import shlex


PROFILE = "RAK_4631_companion_radio_full"
PORT_RELATIVE = "cores/nRF5/freertos/portable/GCC/nrf52/port.c"
PORT_SHA256 = "68f8983b7c67f63d2949829387fdf984635a1d1a23fef46488dd233b3156f36a"
PORT_PATTERN = "*cores*nRF5*freertos*portable*GCC*nrf52*port.c"
SIZE_FLAGS = (
    "-fno-caller-saves",
    "-fno-tree-dominator-opts",
    "-fno-reorder-blocks",
    "-fno-tree-loop-im",
)
_INSTALLED = "MESH_NRF52_OTA_PACING_SIZE_INSTALLED"


def _trim_helpers():
    spec = importlib.util.spec_from_file_location(
        "mesh_nrf52_ota_size_flags", Path(__file__).with_name("nrf52_flash_trim.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def flag_tokens(flags):
    # Environment lists already preserve forced-include option/argument pairs.
    # Preserve quoted tokens when a caller supplies an unparsed flag string.
    return shlex.split(flags, posix=False) if isinstance(flags, str) else list(flags)


def without_lto(flags):
    """Remove only positive LTO controls; preserve every other option in order."""
    return [flag for flag in flag_tokens(flags)
            if not isinstance(flag, str) or not flag.startswith("-flto")]


def size_policy(platform, mcu, env_name, src_filter, flags=(), cppdefines=(),
                ccflags=(), linkflags=()):
    """Fail closed outside the reviewed board, role, options and non-HIL build."""
    if (platform != "nordicnrf52" or str(mcu).lower() != "nrf52840"
            or env_name != PROFILE):
        return False
    helpers = _trim_helpers()
    if (not helpers.source_selected(src_filter, helpers.COMPANION_SOURCES[0])
            or any(helpers.source_selected(src_filter, source)
                   for source in helpers.ROLE_SOURCES + helpers.COMPANION_SOURCES[1:])):
        return False
    options = flag_tokens(flags) + flag_tokens(ccflags) + flag_tokens(linkflags)
    values = helpers.flag_defines(options, cppdefines)
    for name in ("MESH_NRF52_OTA_PACING_SIZE", "MESH_NRF52_FLASH_TRIM",
                 "MESH_NRF52_COMPANION_TRIM", "COMPANION_RADIO_FULL"):
        if name in values and not helpers._enabled(values[name]):
            return False
    # Some HIL options use #ifdef even for =0. Leave every defined HIL owner
    # untouched rather than rebinding its private port copy or instrumentation.
    if any("HIL" in name and value is not None for name, value in values.items()):
        return False
    alias = values.get("OTA_VARIANT")
    if alias is not None and str(alias).strip("\"'") != PROFILE:
        return False
    # Do not override explicit alternate code-generation choices. The base
    # recipe has no positive counterparts to these qualified negative flags.
    if any(flag.replace("-fno-", "-f", 1) in options for flag in SIZE_FLAGS):
        return False
    if "-fno-lto" in options:
        return False
    return not any(isinstance(flag, str) and flag.startswith("-flto-partition=")
                   and flag != "-flto-partition=one" for flag in options)


def source_digest(source):
    """Accept the same reviewed C source with checkout-native LF or CRLF."""
    return hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()


def reviewed_port(framework_dir):
    if not framework_dir:
        raise RuntimeError("RAK Full size policy: pinned nRF52 framework not found")
    source = (Path(framework_dir) / PORT_RELATIVE).resolve()
    if not source.is_file() or source_digest(source.read_bytes()) != PORT_SHA256:
        raise RuntimeError("RAK Full size policy: changed pinned FreeRTOS port.c; review before building")
    return source


def compile_port_without_lto(build_env, node):
    framework = build_env.PioPlatform().get_package_dir("framework-arduinoadafruitnrf52")
    expected = (Path(framework) / PORT_RELATIVE).resolve() if framework else None
    source = Path(node.srcnode().get_abspath()).resolve()
    if source != expected:
        return node  # Includes private HIL copies and unrelated files named port.c.
    reviewed_port(framework)
    options = {name: without_lto(build_env.get(name, ()))
               for name in ("CCFLAGS", "CFLAGS", "CXXFLAGS", "BUILD_FLAGS")}
    if "-fno-lto" not in options["CCFLAGS"]:
        options["CCFLAGS"].append("-fno-lto")
    return build_env.Object(node, **options)


def install(build_env):
    if build_env.get(_INSTALLED):
        return True
    platform = build_env.PioPlatform()
    if not size_policy(platform.name, build_env.BoardConfig().get("build.mcu", ""),
                       build_env.get("PIOENV", ""), build_env.get("SRC_FILTER", ()),
                       build_env.get("BUILD_FLAGS", ()), build_env.get("CPPDEFINES", ()),
                       build_env.get("CCFLAGS", ()), build_env.get("LINKFLAGS", ())):
        return False
    reviewed_port(platform.get_package_dir("framework-arduinoadafruitnrf52"))
    build_env.AppendUnique(CCFLAGS=list(SIZE_FLAGS), BUILD_FLAGS=list(SIZE_FLAGS),
                           LINKFLAGS=list(SIZE_FLAGS))
    build_env.AddBuildMiddleware(compile_port_without_lto, PORT_PATTERN)
    build_env[_INSTALLED] = True
    return True
