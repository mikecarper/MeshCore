#!/usr/bin/env python3
"""Verify pinned SDK ownership, private destruction, and build-local patching."""
from pathlib import Path
import hashlib
import importlib.util
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("esp32_ble_lifecycle_fix", ROOT / "scripts/esp32_ble_lifecycle_fix.py")
FIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FIX)

HARNESS = r'''
#include <cassert>
#include <cstdio>
#include <vector>
static bool host_running = false;
static unsigned objects = 0;
struct Lifetime {
  Lifetime() { ++objects; }
  ~Lifetime() { assert(!host_running); --objects; }
};
struct DescriptorDefinition { int permissions = 0; };
struct CharacteristicDefinition { const void* uuid = nullptr; DescriptorDefinition* descriptors = nullptr; };
struct ServiceDefinition { CharacteristicDefinition* characteristics = nullptr; };
class BLEDevice;
class BLEService : Lifetime {
  friend class BLEDevice;
  ServiceDefinition* m_pSvcDef = nullptr;
  ~BLEService(); // Matches the actual SDK's private destructor.
public:
  BLEService() {
#if defined(CONFIG_NIMBLE_ENABLED)
    m_pSvcDef = new ServiceDefinition[2]{};
    auto* chars = new CharacteristicDefinition[3]{};
    for (int i = 0; i < 2; ++i) {
      chars[i].uuid = this;
      chars[i].descriptors = new DescriptorDefinition[2]{};
    }
    m_pSvcDef[0].characteristics = chars;
#endif
  }
};
@SERVICE_DESTRUCTOR@
struct ServiceMap {
  std::vector<BLEService*> pointers;
  unsigned next = 0;
  BLEService* getFirst() { next = 0; return getNext(); }
  BLEService* getNext() { return next < pointers.size() ? pointers[next++] : nullptr; }
};
class BLEServer : Lifetime {
  friend class BLEDevice;
  ServiceMap m_serviceMap; // Raw-pointer map, as in the SDK.
public:
  void createService() { m_serviceMap.pointers.push_back(new BLEService); }
};
class BLEDevice {
public:
  inline static BLEServer* m_pServer = nullptr;
  static void deinit() {
    assert(!host_running);
@SERVER_DELETE@
  }
};
int main() {
  BLEDevice::deinit(); // Init failed before any server existed.
  for (unsigned cycle = 0; cycle < 100; ++cycle) {
    BLEDevice::m_pServer = new BLEServer;
    if (cycle % 3 != 0) {
      BLEDevice::m_pServer->createService();
      BLEDevice::m_pServer->createService();
    }
    // The SDK service destructor owns its private definition arrays, but
    // NUS TX/RX/2902 objects remain app-owned and must not be deleted twice.
    auto* tx = new Lifetime;
    auto* rx = new Lifetime;
    auto* cccd = new Lifetime;
    host_running = true;
    host_running = false;
    BLEDevice::deinit();
    assert(BLEDevice::m_pServer == nullptr && objects == 3);
    delete tx; delete rx; delete cccd;
    assert(objects == 0);
  }
  puts("private service ownership cycles passed");
}
'''


class FakeNode:
    def __init__(self, path):
        self.path = path

    def srcnode(self):
        return self

    def get_abspath(self):
        return str(self.path)


class FakeEnvironment:
    def __init__(self, build):
        self.build = build
        self.paths = []

    def subst(self, expression):
        assert expression == "$BUILD_DIR"
        return str(self.build)

    def AppendUnique(self, **kwargs):
        self.paths.extend(kwargs["CPPPATH"])

    def File(self, path):
        return Path(path)


class BleLifecycleFixTests(unittest.TestCase):
    def test_private_service_destruction_and_native_arrays_under_sanitizers(self):
        for native in (False, True):
            with self.subTest(native=native), tempfile.TemporaryDirectory(prefix="ble-private-service-") as directory:
                root = Path(directory)
                cpp, exe = root / "test.cpp", root / "test"
                text = HARNESS.replace("@SERVER_DELETE@", FIX.REPAIRED).replace(
                    "@SERVICE_DESTRUCTOR@", FIX.SERVICE_REPAIRED)
                cpp.write_text(text)
                command = [os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                           "-fsanitize=address,undefined", *( ["-DCONFIG_NIMBLE_ENABLED=1"] if native else []),
                           str(cpp), "-o", str(exe)]
                built = subprocess.run(command, text=True, capture_output=True, timeout=30)
                self.assertEqual(built.returncode, 0, built.stderr)
                run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=10)
                self.assertEqual(run.returncode, 0, run.stderr)
                if native:
                    # The original SDK destructor used scalar delete for
                    # arrays. Ensure ASan catches that precise mismatch.
                    cpp.write_text(text.replace(FIX.SERVICE_REPAIRED, FIX.SERVICE_ORIGINAL))
                    built = subprocess.run(command, text=True, capture_output=True, timeout=30)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    run = subprocess.run([str(exe)], text=True, capture_output=True, timeout=10)
                    self.assertNotEqual(run.returncode, 0)
                    self.assertIn("alloc-dealloc-mismatch", run.stderr)

    def test_hash_guards_idempotence_and_old_sdk_unchanged(self):
        for version, sources in FIX.FRAMEWORK_PINS.items():
            for name, (_, _, original, repaired) in sources.items():
                with self.subTest(version=version, name=name):
                    source = "// reviewed source fixture\n" + original
                    expected = source.replace(original, repaired)
                    pins = {name: (hashlib.sha256(source.encode()).hexdigest(),
                                   hashlib.sha256(expected.encode()).hexdigest(), original, repaired)}
                    with patch.object(FIX, "FRAMEWORK_PINS", {version: pins}):
                        self.assertEqual(FIX.patched_ble_source(source, version, name), expected)
                        self.assertEqual(FIX.patched_ble_source(expected, version, name), expected)
                        self.assertEqual(FIX.patched_ble_source(source.replace("\n", "\r\n"), version, name), expected)
                        for old in ((2, 0, 17), (3, 1, 3)):
                            self.assertEqual(FIX.patched_ble_source(source, old, name), source)
                        with self.assertRaisesRegex(RuntimeError, "changed pinned source"):
                            FIX.patched_ble_source(source + "// changed\n", version, name)
                        with self.assertRaisesRegex(RuntimeError, "unreviewed framework"):
                            FIX.patched_ble_source(source, (3, 3, 13), name)
                        with self.assertRaisesRegex(RuntimeError, "unreviewed source"):
                            FIX.patched_ble_source(source, version, "BLEOther.cpp")

    def test_middleware_copies_only_pinned_sources_without_touching_shared_sdk(self):
        with tempfile.TemporaryDirectory(prefix="ble-sdk-middleware-") as directory:
            root = Path(directory)
            sdk = root / "framework"
            source = sdk / "libraries/BLE/src/BLEDevice.cpp"
            version = sdk / "cores/esp32/esp_arduino_version.h"
            source.parent.mkdir(parents=True)
            version.parent.mkdir(parents=True)
            version.write_text("#define ESP_ARDUINO_VERSION_MAJOR 3\n#define ESP_ARDUINO_VERSION_MINOR 3\n#define ESP_ARDUINO_VERSION_PATCH 11\n")
            original = "// fixture\n" + FIX.ORIGINAL
            expected = original.replace(FIX.ORIGINAL, FIX.REPAIRED)
            source.write_text(original)
            pins = {"BLEDevice.cpp": (hashlib.sha256(original.encode()).hexdigest(),
                    hashlib.sha256(expected.encode()).hexdigest(), FIX.ORIGINAL, FIX.REPAIRED)}
            node, env = FakeNode(source), FakeEnvironment(root / "build")
            with patch.object(FIX, "FRAMEWORK_PINS", {(3, 3, 11): pins, (3, 3, 12): pins}):
                destination = FIX.replace_ble_source(env, node)
                self.assertEqual(source.read_text(), original)
                self.assertEqual(destination.read_text(), expected)
                self.assertEqual(env.paths, [str(source.parent)])
                timestamp = destination.stat().st_mtime_ns
                self.assertEqual(FIX.replace_ble_source(env, node), destination)
                self.assertEqual(destination.stat().st_mtime_ns, timestamp)
                version.write_text(version.read_text().replace("PATCH 11", "PATCH 12"))
                self.assertEqual(FIX.replace_ble_source(env, node), destination)
                version.write_text(version.read_text().replace("PATCH 12", "PATCH 13"))
                with self.assertRaisesRegex(RuntimeError, "unreviewed framework"):
                    FIX.replace_ble_source(env, node)

    def test_installed_exact_sdk_pins_when_available(self):
        core_dir = Path(os.environ.get("PLATFORMIO_CORE_DIR", str(Path.home() / ".platformio")))
        candidates = list((core_dir / "packages").glob("framework-arduinoespressif32*"))
        if os.environ.get("MESH_ARDUINO_SDK_TEST_DIR"):
            candidates.append(Path(os.environ["MESH_ARDUINO_SDK_TEST_DIR"]))
        tested = 0
        for candidate in candidates:
            source = candidate / "libraries/BLE/src/BLEDevice.cpp"
            if not source.exists():
                continue
            version = FIX.framework_version(source)
            if version not in FIX.FRAMEWORK_PINS:
                continue
            for name in FIX.FRAMEWORK_PINS[version]:
                original = (source.parent / name).read_text(encoding="utf-8")
                patched = FIX.patched_ble_source(original, version, name)
                self.assertNotEqual(original, patched)
                self.assertEqual(FIX.patched_ble_source(patched, version, name), patched)
                old, new = FIX.FRAMEWORK_PINS[version][name][2:]
                self.assertEqual(patched.replace(new, old, 1), original)
            tested += 1
        if not tested:
            self.skipTest("Pinned Arduino3.3.11/3.3.12 SDK is not installed")


if __name__ == "__main__":
    unittest.main()
