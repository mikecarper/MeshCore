"""Exercise saved bridge ingress and storage failures using the real writers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstring>
#include <vector>
#include "BridgeIngressSettings.h"
#if !MESH_ENABLE_BRIDGE_INGRESS
#error "Raw bridge transport flags must activate settings before CommonCLI.h"
#endif
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include "AtomicFileWriter.h"
#endif
#include "settings_under_test.cpp"

int main() {
  using namespace mesh::bridge;
  MemoryFS fs;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  fs.rename_replaces = true;
#endif
  const char* path = "/bridge_ingress";
  const std::vector<uint8_t> legacy = {1, 8, 4, 7, 9};
  fs.files["/com_prefs"] = legacy;
  assert(ingressProfile() == IngressProfile::Blocked);
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Radio);
  assert(fs.files.size() == 1 && fs.files.at("/com_prefs") == legacy);
  char reply[160];
  auto run = [&](const char* command) {
    strcpy(reply, "untouched");
    return handleIngressCommand(command, reply, sizeof(reply));
  };
  assert(run("get bridge.ingress") && !strcmp(reply, "> radio"));
  assert(!run("get bridge.ingressXYZ") && !strcmp(reply, "untouched"));
  assert(run("set bridge.ingress radio2") && !strncmp(reply, "OK", 2));
  assert(ingressProfile() == IngressProfile::Radio2);
  const auto good = fs.files.at(path);
  assert(good.size() == 10 && !memcmp(good.data(), "BIN\1", 4));
  assert(good[4] == 1 && good[5] == 0xfe);
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Radio2);
  assert(run("get bridge.ingress") && !strcmp(reply, "> radio2"));

  assert(run("set bridge.ingress radio") && !strncmp(reply, "OK", 2));
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Radio);
  assert(run("set bridge.ingress\tradio2") && !strncmp(reply, "OK", 2));
  assert(fs.files.at(path) == good);
  for (const char* invalid : {"set bridge.ingress", "set bridge.ingress both",
       "set bridge.ingress Radio2", "set bridge.ingress radio2junk",
       "set bridge.ingress radio trailing", "get bridge.ingress extra"}) {
    assert(run(invalid) && !strncmp(reply, "Error:", 6));
    assert(ingressProfile() == IngressProfile::Radio2);
    assert(fs.files.at(path) == good);
  }
  assert(!run("set bridge.ingress2 radio"));
  assert(!handleIngressCommand(nullptr, reply, sizeof(reply)));
  assert(!handleIngressCommand("set bridge.ingress radio", nullptr, sizeof(reply)));
  assert(!handleIngressCommand("set bridge.ingress radio", reply, 0));
  char bounded[] = {'x', 'x', 'x', 'z'};
  assert(handleIngressCommand("get bridge.ingress", bounded, 3));
  assert(bounded[2] == 0 && bounded[3] == 'z');

  // Failed staging leaves the public preference and live target unchanged.
  fs.fail_write = true;
  assert(run("set bridge.ingress radio") && !strncmp(reply, "Error:", 6));
  assert(ingressProfile() == IngressProfile::Radio2 && fs.files.at(path) == good);
  fs.fail_write = false;
  fs.fail_write_after = 4;
  assert(run("set bridge.ingress radio") && !strncmp(reply, "Error:", 6));
  assert(ingressProfile() == IngressProfile::Radio2 && fs.files.at(path) == good);
  fs.fail_write_after = -1;
  assert(run("set bridge.ingress radio2") && !strncmp(reply, "OK", 2));

  // Publication may happen before readback fails. Quarantine until a reload;
  // neither ingress nor another setter may silently fall back to primary.
  fs.fail_after_publication = true;
  assert(run("set bridge.ingress radio") && !strncmp(reply, "Error:", 6));
  assert(ingressProfile() == IngressProfile::Blocked);
  const auto published = fs.files.at(path);
  assert(published != good);
  fs.fail_after_publication = false;
  fs.fail_read_open = false;
  assert(run("set bridge.ingress radio2") && !strncmp(reply, "Error:", 6));
  assert(ingressProfile() == IngressProfile::Blocked && fs.files.at(path) == published);
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Radio);
  assert(run("set bridge.ingress radio2") && !strncmp(reply, "OK", 2));

  // Complete malformed records remain blocked but permit explicit repair.
  for (size_t bit = 0; bit < good.size() * 8; ++bit) {
    fs.files[path] = good;
    fs.files[path][bit / 8] ^= uint8_t(1U << (bit % 8));
    loadIngressSettings(&fs);
    assert(ingressProfile() == IngressProfile::Blocked);
    assert(run("get bridge.ingress") && strstr(reply, "blocked"));
  }
  assert(run("set bridge.ingress radio2") && !strncmp(reply, "OK", 2));
  for (size_t size = 0; size < good.size(); ++size) {
    fs.files[path] = std::vector<uint8_t>(good.begin(), good.begin() + size);
    loadIngressSettings(&fs);
    assert(ingressProfile() == IngressProfile::Blocked);
  }
  assert(run("set bridge.ingress radio2") && !strncmp(reply, "OK", 2));
  fs.files[path].push_back(0);
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Blocked);
  assert(run("set bridge.ingress radio2") && !strncmp(reply, "OK", 2));

  // Read/metadata uncertainty requires reclassification before another write.
  fs.fail_read_open = true;
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Blocked);
  fs.fail_read_open = false;
  assert(run("set bridge.ingress radio") && !strncmp(reply, "Error:", 6));
  assert(fs.files.at(path) == good);
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Radio2);
  fs.fail_read_after = 4;
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Blocked);
  fs.fail_read_after = -1;
  assert(run("set bridge.ingress radio") && !strncmp(reply, "Error:", 6));
  loadIngressSettings(&fs);
  fs.fail_metadata = true;
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Blocked);
  fs.fail_metadata = false;
  assert(run("set bridge.ingress radio") && !strncmp(reply, "Error:", 6));
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Radio2);

#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  // Recover only the old complete record across the SPIFFS rename gap.
  fs.files["/bridge_ingress.bak"] = good;
  fs.files.erase(path);
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Radio2 && fs.files.at(path) == good);
  fs.files["/bridge_ingress.bak"] = good;
  fs.files.erase(path);
  fs.fail_rename = 1;
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Blocked);
  assert(fs.files.at("/bridge_ingress.bak") == good);
  assert(run("set bridge.ingress radio") && !strncmp(reply, "Error:", 6));
  loadIngressSettings(&fs);
  assert(ingressProfile() == IngressProfile::Radio2);
#endif
  loadIngressSettings(nullptr);
  assert(ingressProfile() == IngressProfile::Blocked);
  assert(run("set bridge.ingress radio") && !strncmp(reply, "Error:", 6));
  assert(fs.files.at("/com_prefs") == legacy);
}
'''


class BridgeIngressSettingsTests(unittest.TestCase):
    def test_real_settings_and_transactions_across_filesystems(self):
        with tempfile.TemporaryDirectory(prefix="bridge-ingress-settings-") as temporary:
            work = Path(temporary)
            (work / "helpers").mkdir()
            mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text()
            mock = mock.replace("  bool fail_read_open = false;",
                "  bool fail_read_open = false;\n"
                "  bool fail_after_publication = false;\n  bool fail_metadata = false;")
            mock = mock.replace("    files[to] = files[from]; files.erase(from); return true;",
                "    files[to] = files[from]; files.erase(from);\n"
                "    if (fail_after_publication && (!strcmp(to, \"/bridge_ingress\") || !strcmp(to, \"/bridge_routes\"))) fail_read_open = true;\n"
                "    return true;")
            (work / "IdentityStore.h").write_text(mock)
            (work / "helpers/IdentityStore.h").write_text('#include "../IdentityStore.h"\n')
            (work / "helpers/AtomicFileWriter.h").write_text('#include "../AtomicFileWriter.h"\n')
            # Replace only the hardware metadata adapter; both production
            # publication algorithms and all setting/parser bytes are unchanged.
            (work / "FilePresence.h").write_text(r'''
#pragma once
#include "IdentityStore.h"
namespace mesh {
template <typename FS> bool filePresence(FS* fs, const char* path, bool& present) {
  if (!fs || !path || fs->fail_metadata) return false;
  present = fs->exists(path);
  return true;
}
}
''')
            for name in ("BridgeIngressSettings.h", "BridgeIngressProfile.h",
                         "CheckedSettingsFile.h", "ContactFileTransaction.h", "AtomicFileWriter.h"):
                (work / name).write_bytes((ROOT / "src/helpers" / name).read_bytes())
            (work / "settings_under_test.cpp").write_bytes(
                (ROOT / "src/helpers/BridgeIngressSettings.cpp").read_bytes())
            (work / "test.cpp").write_text(HARNESS)
            # CommonCLI.h synthesizes WITH_BRIDGE for these raw transport
            # flags. Standalone settings/transport translation units have not
            # included it yet, so each flag must activate the shared selector.
            cases = [(platform, "WITH_BRIDGE") for platform in
                     ("ESP32_PLATFORM", "RP2040_PLATFORM", "NRF52_PLATFORM", "STM32_PLATFORM")]
            cases += [("ESP32_PLATFORM", transport) for transport in
                      ("WITH_ESPNOW_BRIDGE", "WITH_RS232_BRIDGE", "WITH_MQTT_BRIDGE", "WITH_UDP_BRIDGE")]
            for platform, transport in cases:
                with self.subTest(platform=platform, transport=transport):
                    executable = work / (platform + "_" + transport)
                    built = subprocess.run([
                        os.environ.get("CXX", "c++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                        "-Wno-sign-compare", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                        "-fno-pie", "-no-pie", "-D" + platform, "-D" + transport + "=1",
                        "-I" + str(work), "-I" + str(ROOT / "src"), "-I" + str(ROOT / "src/helpers"),
                        str(work / "test.cpp"), "-o", str(executable)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(executable)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)

    def test_bridge_free_build_has_no_storage_or_runtime_selector(self):
        with tempfile.TemporaryDirectory(prefix="no-bridge-ingress-") as temporary:
            work = Path(temporary)
            obj = work / "empty.o"
            built = subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17",
                "-DNRF52_PLATFORM", "-c", str(ROOT / "src/helpers/BridgeIngressSettings.cpp"),
                "-o", str(obj)], capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            symbols = subprocess.check_output(["nm", "-C", str(obj)], text=True)
            for name in ("settings_fs", "selected", "writable", "loadIngressSettings", "handleIngressCommand"):
                self.assertNotIn(name, symbols)


if __name__ == "__main__":
    unittest.main()
