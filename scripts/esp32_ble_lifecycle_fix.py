"""Restore pinned Arduino 3.3.11/3.3.12 BLE ownership for reversible shutdown.

BLEDevice owns the new wrapper's server, but its implicit destructor leaves
raw service pointers behind. BLEService's destructor is private; delete it
from BLEDevice's existing friend context. NUS characteristics/descriptors stay
application-owned, as on the older wrappers. Never modify the shared SDK.
"""
from pathlib import Path
import hashlib
import re


FRAMEWORKS_UNCHANGED = {(2, 0, 17), (3, 1, 3)}
PINNED_SHA256 = "ec3735051f3263fc0101986fe33602a2593c294c6ccbd9f7b1b416c15529c3c9"
PATCHED_SHA256 = "bf8fecfd6367eb0a2c23dd610336fefa17687d1f7bbc38e9d8f8642835763e87"
SOURCE_DIRECTORY = ("libraries", "BLE", "src")
ORIGINAL = '''  if (m_pServer != nullptr) {
    delete m_pServer;
    m_pServer = nullptr;
  }
'''
REPAIRED = '''  if (m_pServer != nullptr) {
    // MeshCore: the server map owns raw service pointers. BLEDevice is a
    // friend of BLEServer/BLEService, whose destructor is private here.
    // The application has already stopped host callback dispatch.
    BLEService* service = m_pServer->m_serviceMap.getFirst();
    while (service != nullptr) {
      delete service;
      service = m_pServer->m_serviceMap.getNext();
    }
    delete m_pServer;
    m_pServer = nullptr;
  }
'''

SERVICE_ORIGINAL = 'BLEService::~BLEService() {\n#if defined(CONFIG_NIMBLE_ENABLED)\n  if (m_pSvcDef != nullptr) {\n    if (m_pSvcDef->characteristics != nullptr) {\n      for (int i = 0; m_pSvcDef->characteristics[i].uuid != NULL; ++i) {\n        if (m_pSvcDef->characteristics[i].descriptors) {\n          delete (m_pSvcDef->characteristics[i].descriptors);\n        }\n      }\n      delete (m_pSvcDef->characteristics);\n    }\n\n    delete (m_pSvcDef);\n  }\n#endif\n}'
# Each definition is allocated with new[] by BLEService::start().
SERVICE_REPAIRED = SERVICE_ORIGINAL.replace("delete (", "delete[] (")
SOURCE_PINS = {
    "BLEDevice.cpp": (PINNED_SHA256, PATCHED_SHA256, ORIGINAL, REPAIRED),
    "BLEService.cpp": (
        "2b71699e1962cdc37a54ace429ebdd408b0e40b379d621d427add5bb5a93b252",
        "8c4d8917ffb768a9eab468b1be06be209b4b8fc065b125c821682b6579a30267", SERVICE_ORIGINAL, SERVICE_REPAIRED),
}
FRAMEWORK_PINS = {
    (3, 3, 11): SOURCE_PINS,
    (3, 3, 12): {
        # 3.3.12 adds pairing-result logs; retain them while repairing only the
        # unchanged server/service ownership and array destruction below.
        "BLEDevice.cpp": (
            "212c62b3575f4ca8aff3be57a066291e1b1522f0d609b9e727bb547ab600371d",
            "8790bb0f75b177a3c47bdfb03e436a705f7e1f0b21868039244242cc1f3d08fc",
            ORIGINAL, REPAIRED),
        "BLEService.cpp": SOURCE_PINS["BLEService.cpp"],
    },
}


def patched_ble_source(source, framework_version, name="BLEDevice.cpp"):
    if framework_version in FRAMEWORKS_UNCHANGED:
        return source
    if framework_version not in FRAMEWORK_PINS:
        raise RuntimeError("ESP32 BLE lifecycle fix: unreviewed framework version")
    source = source.replace("\r\n", "\n")
    try:
        original_digest, patched_digest, original, repaired = FRAMEWORK_PINS[framework_version][name]
    except KeyError as error:
        raise RuntimeError("ESP32 BLE lifecycle fix: unreviewed source") from error
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if digest == patched_digest:
        return source
    if digest != original_digest or source.count(original) != 1:
        raise RuntimeError("ESP32 BLE lifecycle fix: changed pinned source; review SDK update")
    patched = source.replace(original, repaired, 1)
    if hashlib.sha256(patched.encode("utf-8")).hexdigest() != patched_digest:
        raise RuntimeError("ESP32 BLE lifecycle fix: unexpected transform result")
    return patched


def framework_version(source):
    header = source.parents[3] / "cores" / "esp32" / "esp_arduino_version.h"
    try:
        text = header.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError("ESP32 BLE lifecycle fix: missing framework version header") from error
    entries = re.findall(r"^#define ESP_ARDUINO_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)\s*$",
                         text, re.MULTILINE)
    values = dict(entries)
    if len(entries) != 3 or set(values) != {"MAJOR", "MINOR", "PATCH"}:
        raise RuntimeError("ESP32 BLE lifecycle fix: malformed framework version")
    return tuple(int(values[key]) for key in ("MAJOR", "MINOR", "PATCH"))


def replace_ble_source(build_env, node):
    source = Path(node.srcnode().get_abspath())
    if source.parts[-4:-1] != SOURCE_DIRECTORY or source.name not in SOURCE_PINS:
        return node
    version = framework_version(source)
    original = source.read_text(encoding="utf-8")
    patched = patched_ble_source(original, version, source.name)
    if patched == original:
        return node
    destination = Path(build_env.subst("$BUILD_DIR")) / "patched-esp32-ble" / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_text(encoding="utf-8") != patched:
        destination.write_text(patched, encoding="utf-8")
    build_env.AppendUnique(CPPPATH=[str(source.parent)])
    return build_env.File(str(destination))


def install(build_env):
    build_env.AddBuildMiddleware(replace_ble_source, "*libraries*BLE*src*BLE*.cpp")


if "Import" in globals():
    Import("env")
    install(env)
