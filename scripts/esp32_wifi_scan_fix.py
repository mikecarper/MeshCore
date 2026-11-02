"""Zero the pinned Arduino-ESP32 scan configuration in a private build copy.

Arduino 2.0.17 leaves passive dwell and home-channel dwell uninitialized during
active scans. ESP-IDF can use passive channels in an otherwise active scan.
Preserve the caller's scan settings and let zero select the SDK defaults for
the unused fields. Never modify PlatformIO's shared framework package.
"""
import hashlib
from pathlib import Path
import re


PINNED_SCAN_SHA256 = "e96ac4be873860dc2b441d6d01d6613eb4d06bb21e3f225c3f428619496e6c21"
PATCHED_SCAN_SHA256 = "2ab9ce875ea8a21ee6635a5c95418185809d69a8e81d081b121c6463c0c89097"
SOURCE_SUFFIX = ("libraries", "WiFi", "src", "WiFiScan.cpp")
ORIGINAL_DECLARATION = "wifi_scan_config_t config;"
ZEROED_DECLARATION = "wifi_scan_config_t config = {};"
PATCHED_VERSION = (2, 0, 17)
# These existing recipes keep their own framework sources. This narrowly
# qualified Arduino 2 fix does not claim to qualify Arduino 3 scanning.
KNOWN_UNCHANGED_VERSIONS = {(3, 1, 3), (3, 3, 11), (3, 3, 12)}


def patched_scan_source(source, framework_version=PATCHED_VERSION):
    if framework_version != PATCHED_VERSION:
        raise RuntimeError("ESP32 WiFi scan fix: unreviewed patch framework version")
    source = source.replace("\r\n", "\n")
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if digest == PATCHED_SCAN_SHA256:
        return source
    if digest != PINNED_SCAN_SHA256 or source.count(ORIGINAL_DECLARATION) != 1:
        raise RuntimeError("ESP32 WiFi scan fix: changed pinned source; review SDK update")
    patched = source.replace(ORIGINAL_DECLARATION, ZEROED_DECLARATION, 1)
    if hashlib.sha256(patched.encode("utf-8")).hexdigest() != PATCHED_SCAN_SHA256:
        raise RuntimeError("ESP32 WiFi scan fix: unexpected transform result")
    return patched


def esp32_enabled(build_env):
    defines = {}
    for definition in build_env.get("CPPDEFINES", []):
        if isinstance(definition, (tuple, list)):
            defines[str(definition[0])] = str(definition[1])
        else:
            defines[str(definition)] = "1"
    return any(defines.get(name) not in (None, "0")
               for name in ("ESP32_PLATFORM", "ESP32"))


def require_known_framework(source):
    header_path = source.parents[3] / "cores" / "esp32" / "esp_arduino_version.h"
    try:
        header = header_path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError("ESP32 WiFi scan fix: missing framework version header") from error
    entries = re.findall(r"^#define ESP_ARDUINO_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)\s*$",
                         header, re.MULTILINE)
    values = dict(entries)
    if len(entries) != 3 or set(values) != {"MAJOR", "MINOR", "PATCH"}:
        raise RuntimeError("ESP32 WiFi scan fix: changed/malformed framework version")
    version = tuple(int(values[key]) for key in ("MAJOR", "MINOR", "PATCH"))
    if version != PATCHED_VERSION and version not in KNOWN_UNCHANGED_VERSIONS:
        raise RuntimeError("ESP32 WiFi scan fix: unreviewed framework version")
    return version


def replace_scan_source(build_env, node):
    if not esp32_enabled(build_env):
        return node
    source = Path(node.srcnode().get_abspath())
    if source.parts[-4:] != SOURCE_SUFFIX:
        return node
    version = require_known_framework(source)
    if version in KNOWN_UNCHANGED_VERSIONS:
        return node
    patched = patched_scan_source(source.read_text(encoding="utf-8"), version)
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-esp32-wifi" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    # The copied source includes the original WiFi library's private headers.
    build_env.AppendUnique(CPPPATH=[str(source.parent)])
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(replace_scan_source, "*libraries*WiFi*src*WiFiScan.cpp")


if "Import" in globals():
    Import("env")
    install(env)
