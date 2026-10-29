"""Role-specific feature-preserving flash trims for nRF52 firmware.

Decide from the effective source filter as well as the environment/OTA alias
and flags: build.sh can synthesize profiles over another PlatformIO recipe.
Unknown roles fail closed. No shared framework or downloaded library is edited.
CDC, USB host, sensor/GPS support, BLE P-256, and stored Ed25519 keys remain.
Infrastructure uses RAM-built compact Ed25519 and display-font trims.
RAK3401/RAK4631 repeaters also share CLI reply copies and use compact scalar
arithmetic and the reviewed GNSS version parser without changing protocols.
Fast field arithmetic is retained to avoid long radio-service stalls.
Companion uses a 960-byte Ed25519 flash table to keep signing stack bounded,
and retains all UI fonts and the Reader. The nRF52 base keeps an 8 KiB loop
stack for crypto and the existing OTA path.
"""

import fnmatch
import hashlib
import importlib.util
import re
import shlex
from pathlib import Path


ROLE_SOURCES = (
    "../examples/simple_repeater/main.cpp",
    "../examples/simple_room_server/main.cpp",
    "../examples/simple_sensor/main.cpp",
    "../examples/kiss_modem/main.cpp",
)
COMPANION_SOURCES = (
    "../examples/companion_radio/MyMesh.cpp",
    "../examples/simple_secure_chat/main.cpp",
)
TRIM_DEFINES = {
    "MESH_NRF52_FLASH_TRIM": 1,
    "ED25519_COMPACT_BASE": 1,
    "ED25519_COMPACT_SHA512": 1,
    # No infrastructure role instantiates these USB *device* classes. Do not
    # touch CDC (including dual CDC logging), DFU, or the MAX3421 USB host.
    "CFG_TUD_MSC": 0,
    "CFG_TUD_HID": 0,
    "CFG_TUD_MIDI": 0,
    "CFG_TUD_VENDOR": 0,
    "CFG_TUD_VIDEO": 0,
    "CFG_TUD_VIDEO_STREAMING": 0,
    # MeshCore uses UBX navigation, not SparkFun's automatic NMEA cache. This
    # does not disable navigation, the UBX parser, or MeshCore NMEA logging.
    "SFE_UBLOX_DISABLE_AUTO_NMEA": 1,
    "SFE_UBLOX_REDUCED_PROG_MEM": 1,
    # SSD1306Display clears the vendor splash before its first panel transfer.
    # Keep MeshCore's own startup logo; only omit the invisible library bitmap.
    "SSD1306_NO_SPLASH": 1,
}
# The reviewed main runs the Arduino loop forever. Scheduler failure and every
# normal reset/update path reset the MCU; global process-exit cleanup cannot run.
# Unknown/custom core revisions retain the standard ABI registration behavior.
NRF52_MAIN_NO_EXIT_SHA256 = "25c3a05b2631a6daff58048a517b8f86c33e97aa978d8b365f03a5b4fa371231"

CC310_ARCHIVE_SHA256 = "97dc648d44520e47252f62c402a71f976e36d7b3aef99235bbdc4de79a928577"
CC310_ARCHIVE = "src/cortex-m4/fpv4-sp-d16-hard/libnrf_cc310_0.9.13-no-interrupts.a"

# Companion retains all UI fonts and uses a small Ed25519 flash table instead
# of the infrastructure implementation's extra 1.8 KiB of signing stack.
# These options remove library code/data that its application never uses;
# CDC, DFU, BLE pairing, USB host and UBX navigation remain available.
COMPANION_TRIM_DEFINES = {
    name: value for name, value in TRIM_DEFINES.items()
    if name not in ("MESH_NRF52_FLASH_TRIM", "ED25519_COMPACT_BASE",
                    "ED25519_COMPACT_SHA512")
}
COMPANION_TRIM_DEFINES["MESH_NRF52_COMPANION_TRIM"] = 1
COMPANION_TRIM_DEFINES["ED25519_SMALL_BASE"] = 1
COMPANION_TRIM_DEFINES["ED25519_COMPACT_SHA512"] = 1
COMPANION_TRIM_DEFINES["ED25519_COMPACT_SCALAR"] = 1

RAK_REPEATER_TRIM_DEFINES = {
    "ED25519_COMPACT_SCALAR": 1,
    "MESH_CLI_SHARE_REPLIES": 1,
}


def flag_defines(flags=(), cppdefines=()):
    """Read effective defines without importing PlatformIO/SCons for tests."""
    values = {}
    for define in cppdefines:
        if isinstance(define, (tuple, list)):
            values[str(define[0])] = str(define[1]) if len(define) > 1 else "1"
        else:
            values[str(define)] = "1"
    text = flags if isinstance(flags, str) else " ".join(map(str, flags))
    words = shlex.split(text)
    index = 0
    while index < len(words):
        word = words[index]
        if word in ("-D", "-U"):
            index += 1
            word += words[index] if index < len(words) else ""
        if word.startswith("-D"):
            name, _, value = word[2:].partition("=")
            values[name] = value if "=" in word else "1"
        elif word.startswith("-U"):
            values[word[2:]] = None
        index += 1
    return values


def _enabled(value):
    return value is not None and str(value).strip("\"'() ") not in ("", "0")


def source_selected(src_filter, source):
    """Resolve ordered includes/excludes for the explicit outside-src roles."""
    text = src_filter if isinstance(src_filter, str) else " ".join(map(str, src_filter))
    selected = False
    for sign, pattern in re.findall(r"([+-])<([^>]+)>", text.replace("\\", "/")):
        # +<*.cpp> and +<*> scan PROJECT_SRC_DIR, not ../examples. They do
        # not imply that any example role is part of the image.
        if source.startswith("../examples/") and "examples/" not in pattern:
            continue
        pattern = pattern.rstrip("/")
        if fnmatch.fnmatchcase(source, pattern) or source.startswith(pattern + "/"):
            selected = sign == "+"
    return selected


def is_rak_repeater(mcu, src_filter, values):
    """Qualify hardware and the effective role, including release overlays."""
    return (str(mcu).lower() == "nrf52840"
            and any(_enabled(values.get(board)) for board in ("RAK_3401", "RAK_4631"))
            and source_selected(src_filter, ROLE_SOURCES[0])
            and not any(source_selected(src_filter, source)
                        for source in ROLE_SOURCES[1:] + COMPANION_SOURCES))


def trim_policy(platform, mcu, env_name, src_filter, flags=(), cppdefines=(),
                ota_name_mode="front-coded"):
    """Return defaults for a qualified non-Companion role, otherwise nothing.

    The name policy is explicit so the profile can use a different verified
    representation later without changing role selection or hardware trims.
    """
    if platform != "nordicnrf52" or not str(mcu).lower().startswith("nrf52"):
        return {}
    values = flag_defines(flags, cppdefines)
    names = (str(env_name), str(values.get("OTA_VARIANT", "")))
    if any(any(tag in name.lower() for tag in (
            "companion", "comp_radio", "terminal_chat", "term_chat", "terminal-chat", "secure_chat")) for name in names):
        return {}
    if any(_enabled(value) for name, value in values.items()
           if name.startswith("COMPANION_")):
        return {}
    if any(source_selected(src_filter, source) for source in COMPANION_SOURCES):
        return {}
    if not any(source_selected(src_filter, source) for source in ROLE_SOURCES):
        return {}
    if "MESH_NRF52_FLASH_TRIM" in values and not _enabled(values["MESH_NRF52_FLASH_TRIM"]):
        return {}
    if ota_name_mode != "front-coded":
        raise ValueError("unsupported nRF52 OTA name trim policy: " + ota_name_mode)
    definitions = dict(TRIM_DEFINES, OTA_TARGET_NAME_FRONT_CODED=1)
    if is_rak_repeater(mcu, src_filter, values):
        definitions.update(RAK_REPEATER_TRIM_DEFINES)
    if _enabled(values.get("MESH_OTA_LOCAL_TARGET_NAME_ONLY")):
        # Release overlays can compile a different PIOENV from their deployed
        # OTA identity. Keep the logical name/ID in that case; direct builds
        # use the same PIOENV hash as the ordinary EndF/board identity code.
        target_id = values.get("MOTA_TARGET_ID")
        if target_id is None:
            target_id = "0x%08x" % int.from_bytes(
                hashlib.sha256(str(env_name).encode()).digest()[:4], "little")
        definitions.update(
            OTA_TARGET_NAME_TABLE=0,
            OTA_LOCAL_TARGET_ID=target_id,
            OTA_LOCAL_TARGET_NAME="OTA_VARIANT" if values.get("OTA_VARIANT")
                                  else '"%s"' % env_name,
        )
    # Explicit board/user flags retain their value. In particular never undo
    # an opt-out or a board-specific USB configuration by re-defining it.
    return {name: value for name, value in definitions.items() if name not in values}


def supported_cc310(framework_dir, mcu, defines):
    """Only wrap the exact inspected archive; future SDKs retain their getter."""
    if str(mcu).lower() != "nrf52840" or not _enabled(defines.get("USE_CC310_HW_CRYPTO")):
        return False
    archive = Path(framework_dir) / "libraries/Adafruit_nRFCrypto" / CC310_ARCHIVE
    return archive.is_file() and hashlib.sha256(archive.read_bytes()).hexdigest() == CC310_ARCHIVE_SHA256


def supported_program_lifetime(framework_dir):
    if not framework_dir:
        return False
    main = Path(framework_dir) / "cores/nRF5/main.cpp"
    return main.is_file() and hashlib.sha256(main.read_bytes()).hexdigest() == NRF52_MAIN_NO_EXIT_SHA256


def companion_trim_policy(platform, mcu, src_filter, flags=(), cppdefines=()):
    """Qualify the actual Companion sources, independent of board/alias name."""
    if platform != "nordicnrf52" or not str(mcu).lower().startswith("nrf52"):
        return {}
    if not source_selected(src_filter, COMPANION_SOURCES[0]):
        return {}
    if any(source_selected(src_filter, source) for source in ROLE_SOURCES):
        return {}
    values = flag_defines(flags, cppdefines)
    for name in ("MESH_NRF52_FLASH_TRIM", "MESH_NRF52_COMPANION_TRIM"):
        if name in values and not _enabled(values[name]):
            return {}
    definitions = {name: value for name, value in COMPANION_TRIM_DEFINES.items()
                   if name not in values}
    # The older compact option uses #ifdef, so even an explicit =0 selects
    # that path. Preserve any caller choice instead of enabling both modes.
    if values.get("ED25519_COMPACT_BASE") is not None:
        definitions.pop("ED25519_SMALL_BASE", None)
    return definitions


def trim_tft_library(build_env, node):
    # PlatformIO invokes this on TFT_eSPI's library builder, which is already
    # a private clone. Do not add a global forced include to the project: its
    # User_Setup defines are not appropriate for unrelated sources. The
    # matching ST7735Display translation unit includes this header directly.
    if not build_env.get("MESH_NRF52_TFT_FONT_TRIM_APPLIED", False):
        build_env["MESH_NRF52_TFT_FONT_TRIM_APPLIED"] = True
        build_env.Append(CCFLAGS=[
            "-include", build_env.subst("$PROJECT_DIR/src/helpers/ui/Nrf52TftFontTrim.h"),
        ])
    return node


def packed_help_policy(platform, mcu, src_filter, flags=(), cppdefines=()):
    if platform != "nordicnrf52" or str(mcu).lower() != "nrf52840":
        return False
    if not source_selected(src_filter, COMPANION_SOURCES[0]):
        return False
    if any(source_selected(src_filter, source) for source in ROLE_SOURCES):
        return False
    # Board and Full profile defines can both arrive after pre-scripts. Only
    # the exact help source changes language; its C++ gate leaves every other
    # board/profile empty, without changing MyMesh or framework compilation.
    return True


def compile_packed_help(build_env, node):
    source = Path(node.srcnode().get_abspath()).resolve()
    expected = Path(build_env.subst("$PROJECT_DIR/examples/companion_radio/CompanionHelpPacked.cpp")).resolve()
    if source != expected:
        return node
    # The framework appends gnu++11 after pre-scripts. Override only this
    # source's final environment, leaving MyMesh, libraries and all other
    # targets on their original language standard and feature context.
    flags = {name: [value for value in build_env.get(name, ())
                    if not str(value).startswith("-std=")]
             for name in ("CCFLAGS", "CXXFLAGS")}
    flags["CXXFLAGS"].append("-std=gnu++17")
    print("Companion help: isolated C++17 source")
    # Builder overrides stay attached to this object even when PlatformIO
    # collects it through the parent construction environment afterward.
    return build_env.Object(node, **flags)


def _compile_shared_reply_copies(build_env, node, relative_path):
    source = Path(node.srcnode().get_abspath()).resolve()
    expected = Path(build_env.subst("$PROJECT_DIR/" + relative_path)).resolve()
    if source != expected:
        return node
    # GCC otherwise expands many constant strcpy replies into repeated stores.
    # Use the same libc function and strings, with no parser or output changes.
    flags = list(build_env.get("CCFLAGS", ()))
    if "-fno-builtin-strcpy" not in flags:
        flags.append("-fno-builtin-strcpy")
    return build_env.Object(node, CCFLAGS=flags)


def compile_shared_cli_replies(build_env, node):
    """Keep constant reply copies out of line in the qualified CLI object only."""
    return _compile_shared_reply_copies(build_env, node, "src/helpers/CommonCLI.cpp")


def companion_shared_replies_policy(platform, mcu, env_name, src_filter,
                                   flags=(), cppdefines=()):
    if (platform != "nordicnrf52" or env_name != "RAK_4631_companion_radio_full"
            or str(mcu).lower() != "nrf52840"
            or not source_selected(src_filter, COMPANION_SOURCES[0])
            or any(source_selected(src_filter, source) for source in ROLE_SOURCES)):
        return False
    values = flag_defines(flags, cppdefines)
    return all(name not in values or _enabled(values[name]) for name in (
        "MESH_NRF52_FLASH_TRIM", "MESH_NRF52_COMPANION_TRIM", "MESH_CLI_SHARE_REPLIES",
        "MESH_CLI_SHARE_REPLY_COPIES"))


def install(build_env):
    platform = build_env.PioPlatform()
    mcu = build_env.BoardConfig().get("build.mcu", "")
    flags = build_env.get("BUILD_FLAGS", ())
    cppdefines = build_env.get("CPPDEFINES", ())
    if packed_help_policy(platform.name, mcu, build_env.get("SRC_FILTER", ()), flags, cppdefines):
        build_env.AddBuildMiddleware(compile_packed_help, "*CompanionHelpPacked.cpp")
    definitions = trim_policy(platform.name, mcu, build_env["PIOENV"],
                              build_env.get("SRC_FILTER", ()), flags, cppdefines)
    infrastructure = bool(definitions)
    if not infrastructure:
        definitions = companion_trim_policy(platform.name, mcu,
                                            build_env.get("SRC_FILTER", ()), flags, cppdefines)
    if not definitions:
        return
    local_name = definitions.get("OTA_LOCAL_TARGET_NAME", "")
    if local_name.startswith('"') and local_name.endswith('"'):
        definitions["OTA_LOCAL_TARGET_NAME"] = build_env.StringifyMacro(local_name[1:-1])
    build_env["MESH_NRF52_FLASH_TRIM_ACTIVE"] = True
    build_env.AppendUnique(CPPDEFINES=list(definitions.items()))
    hook_path = build_env.subst("$PROJECT_DIR/scripts/nrf52_vl53l0x_const.py")
    hook_spec = importlib.util.spec_from_file_location("mesh_nrf52_vl53_const", hook_path)
    hook = importlib.util.module_from_spec(hook_spec)
    hook_spec.loader.exec_module(hook)
    hook.install(build_env)
    values = flag_defines(flags, cppdefines)
    rak_repeater = infrastructure and is_rak_repeater(
        mcu, build_env.get("SRC_FILTER", ()), values)
    if rak_repeater and _enabled(values.get("MESH_CLI_SHARE_REPLIES",
                                            definitions.get("MESH_CLI_SHARE_REPLIES"))):
        build_env.AddBuildMiddleware(compile_shared_cli_replies, "*CommonCLI.cpp")
    if not infrastructure and companion_shared_replies_policy(
            platform.name, mcu, build_env["PIOENV"], build_env.get("SRC_FILTER", ()),
            flags, cppdefines):
        if "MESH_CLI_SHARE_REPLY_COPIES" not in flag_defines(flags, cppdefines):
            build_env.AppendUnique(CPPDEFINES=[("MESH_CLI_SHARE_REPLY_COPIES", 1)])
    if infrastructure:
        names = ("local OTA target name only" if definitions.get("OTA_TARGET_NAME_TABLE") == 0
                 or flag_defines(flags, cppdefines).get("OTA_TARGET_NAME_TABLE") == "0"
                 else "compact OTA target names")
        print("nRF52 infrastructure flash trim: compact crypto, unused USB device classes/GPS cache/vendor splash, " + names)
    else:
        print("nRF52 Companion flash trim: small crypto table, unused USB device classes/GPS cache/vendor splash; fonts and Reader retained")
    if (not infrastructure or rak_repeater) and _enabled(
            values.get("MESH_NRF52_GNSS_VERSION_TRIM", "1")):
        hook_path = build_env.subst("$PROJECT_DIR/scripts/nrf52_gnss_version_trim.py")
        hook_spec = importlib.util.spec_from_file_location("mesh_nrf52_gnss_trim", hook_path)
        hook = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(hook)
        hook.install(build_env)
    framework = platform.get_package_dir("framework-arduinoadafruitnrf52")
    if "MESH_NRF52_NO_PROGRAM_EXIT" not in values and supported_program_lifetime(framework):
        build_env.AppendUnique(CPPDEFINES=[("MESH_NRF52_NO_PROGRAM_EXIT", 1)])
        print("nRF52 reviewed firmware lifetime: omit unreachable process-exit registrations")
    if infrastructure and values.get("ST7789") is not None:
        # Keep the font license with this qualified display build, including
        # cached DFU packages. The hook independently checks the effective
        # font override and excludes every Companion transport/profile.
        hook_path = build_env.subst("$PROJECT_DIR/scripts/package_nrf52_font_license.py")
        hook_spec = importlib.util.spec_from_file_location("mesh_nrf52_font_license", hook_path)
        font_license = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(font_license)
        font_license.install(build_env)
    if (infrastructure and values.get("DISPLAY_CLASS") == "ST7735Display"
            and source_selected(build_env.get("SRC_FILTER", ()), "helpers/ui/ST7735Display.cpp")):
        build_env.AddBuildMiddleware(trim_tft_library, "*TFT_eSPI*/*.cpp")
    if framework and supported_cc310(framework, mcu, values):
        build_env.AppendUnique(LINKFLAGS=["-Wl,--wrap=CRYS_ECPKI_GetEcDomain"])
        build_env.AppendUnique(CPPPATH=[str(Path(framework) / "libraries/Adafruit_nRFCrypto/src/nrf_cc310/include")])
        # BuildSources is called on this selected nRF52 environment only. The
        # wrapper is not in the common source filter and cannot leak to other
        # platforms or an unqualified role.
        build_env.BuildSources("$BUILD_DIR/nrf52-flash-trim",
                               "$PROJECT_DIR/src/helpers/nrf52",
                               "+<Cc310DomainTrim.c>")
    else:
        print("nRF52 CC310 trim: unsupported/unused SDK; ordinary domain lookup retained")


if "Import" in globals():
    Import("env")
    install(env)
