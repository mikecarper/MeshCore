#!/usr/bin/env python3
"""Exercise the production shared RXPS getters and ASCII adapter under sanitizers."""

from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced


ROOT = Path(__file__).resolve().parents[1]


class CompanionRxpsGetterTests(unittest.TestCase):
    def test_actual_status_formatter_and_prefixed_route_are_bounded(self):
        shared = (ROOT / "src/helpers/CommonRadioPrefs.cpp").read_text(encoding="ascii")
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        formatter = extract_braced(shared, "void CommonRadioPrefs::formatRxPowerSavingStatus(")
        branch = extract_braced(common, 'if (strcmp(config, "radio.rxps.status") == 0)')
        source = r'''
#include <helpers/radiolib/RXPowerSavingCLI.h>
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
struct Control : RxPowerSavingControl {
  RxPowerSavingStatus status;
  RxPowerSavingStatus getRxPowerSavingStatus() const override { return status; }
} control;
#define WRAPPER_CLASS NativeRadio
struct NativeRadio {
  struct Profiles { struct Params { uint8_t sf = 7; float bw = 125; } primary; } profile;
  const Profiles* profiles() const { return &profile; }
  void getRxPowerSavingTimings(uint32_t& rx_us, uint32_t& sleep_us) const {
    rx_us = sleep_us = 30000000;
  }
  uint16_t currentPreambleLength() const { return 128; }
  RxPowerSavingStatus getRxPowerSavingStatus() const { return control.status; }
} radio_driver;
struct CommonRadioPrefs {
  RxPowerSavingConfig desired;
  uint8_t getSpreadFactor() const { return 7; }
  float getBandwidth() const { return 125; }
  RxPowerSavingConfig readRxPowerSavingConfig() const { return desired; }
  void formatRxPowerSavingStatus(char*, size_t) const;
} radio;
@FORMATTER@
struct Prefs { CommonRadioPrefs* getRadioPrefs() { return &radio; } } prefs;
struct CommonCLI {
  Prefs* _prefs = &prefs;
  void get(const char* config, char* reply) { @STATUS_BRANCH@ }
};
int main() {
  radio.desired.enabled = 1;
  radio.desired.rx_us = radio.desired.sleep_us = 30000000;
  radio.desired.preamble = 128;
  control.status.supported = true;
  control.status.armed = true;
  control.status.last_error = INT16_MIN;
  control.status.arm_failures = UINT32_MAX;
  control.status.effective_rx_us = UINT32_MAX;
  control.status.effective_sleep_us = UINT32_MAX;
  bool saw_truncation = false;
  for (bool supported : {false, true}) {
    control.status.supported = supported;
   for (int16_t error : {int16_t(INT16_MIN), int16_t(0)}) {
    control.status.last_error = error;
    for (uint8_t level : {0, 6, 9, 10}) {
      radio.desired.level = level;
      const RxPowerSavingConfig before = radio.desired;
      char full[512];
      radio.formatRxPowerSavingStatus(full, sizeof(full));
      const std::string expected = full;
      assert(expected.find(error ? "err=-32768,fail=4294967295" : "err=0,fail=4294967295")
          != std::string::npos);
      if (level == 9) assert(expected.find("level=9(!)") != std::string::npos);
      if (level == 10) assert(expected.find("level=10(!)") != std::string::npos);
      saw_truncation = saw_truncation || expected.size() > 156;
      for (size_t capacity = 0; capacity <= 180; ++capacity) {
        std::vector<unsigned char> bytes(capacity + 16, 0xA7);
        char* reply = reinterpret_cast<char*>(bytes.data() + 8);
        radio.formatRxPowerSavingStatus(reply, capacity);
        if (capacity) {
          const size_t count = std::min(expected.size(), capacity - 1);
          assert(strlen(reply) == count && !memcmp(reply, expected.data(), count));
        }
        for (size_t n = 0; n < 8; ++n) assert(bytes[n] == 0xA7);
        for (size_t n = 8 + capacity; n < bytes.size(); ++n) assert(bytes[n] == 0xA7);
      }
      // Infrastructure replies already reserve three bytes of their 160-byte
      // buffer. Execute the production route with those bytes in place.
      std::vector<unsigned char> bytes(176, 0xA7);
      char* reply = reinterpret_cast<char*>(bytes.data() + 8);
      memcpy(reply, "  >", 3);
      CommonCLI cli;
      cli.get("radio.rxps.status", reply + 3);
      assert(!memcmp(reply, "  >", 3));
      assert(strlen(reply + 3) == std::min(expected.size(), size_t(156)));
      for (size_t n = 0; n < 8; ++n) assert(bytes[n] == 0xA7);
      for (size_t n = 168; n < bytes.size(); ++n) assert(bytes[n] == 0xA7);
      assert(!memcmp(&radio.desired, &before, sizeof(before)));
    }
   }
  }
  assert(saw_truncation && "The 157-byte prefixed route must exercise real truncation");
  radio.formatRxPowerSavingStatus(nullptr, 160);
}
'''.replace("@FORMATTER@", formatter).replace("@STATUS_BRANCH@", branch)
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "RXPS formatter needs a host C++17 compiler")
        sanitizers = [] if os.name == "nt" else [
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        with tempfile.TemporaryDirectory(prefix="meshcore-rxps-status-bounds-") as directory:
            work = Path(directory)
            cpp, binary = work / "bounds.cpp", work / "bounds"
            cpp.write_text(source, encoding="ascii")
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    *sanitizers, "-I", str(ROOT / "src"),
                                    str(cpp), str(ROOT / "src/helpers/radiolib/RXPowerSaving.cpp"),
                                    str(ROOT / "src/helpers/radiolib/RXPowerSavingCLI.cpp"),
                                    "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_status_alias_routes_to_shared_diagnostics_without_changing_legacy_getters(self):
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        whitelist = extract_braced(companion, "static bool isCompanionRadioPrefsCommand(")
        branch = extract_braced(common, 'if (strcmp(config, "radio.rxps.status") == 0)')
        source = r'''
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
struct RadioPrefs {
  unsigned calls = 0;
  void formatRxPowerSavingStatus(char* reply, size_t capacity) {
    assert(capacity == 157);
    ++calls;
    snprintf(reply, capacity, "> desired=on,effective=continuous,supported=yes,err=-2,fail=3");
  }
} radio;
struct Prefs { RadioPrefs* getRadioPrefs() { return &radio; } } prefs;
struct CommonCLI {
  Prefs* _prefs = &prefs;
  void get(const char* config, uint32_t sender_timestamp, char* reply) {
    (void)sender_timestamp;
    @STATUS_BRANCH@
    else strcpy(reply, "unhandled");
  }
};
@WHITELIST@
int main() {
  assert(isCompanionRadioPrefsCommand("get radio.rxps.status"));
  for (const char* other : {"get radio.rxps.statusx", "get radio.rxps.status extra",
       "get radio.rxps.config", "get radio.rxps", "set radio.rxps on"}) {
    assert(!isCompanionRadioPrefsCommand(other));
  }
  CommonCLI cli;
  char reply[160];
  cli.get("radio.rxps.status", 123, reply);
  assert(radio.calls == 1);
  assert(strstr(reply, "effective=continuous") && strstr(reply, "err=-2,fail=3"));
  cli.get("radio.rxps.statusx", 123, reply);
  assert(radio.calls == 1 && !strcmp(reply, "unhandled"));
}
'''.replace("@STATUS_BRANCH@", branch).replace("@WHITELIST@", whitelist)
        source = "#include <initializer_list>\n" + source
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "RXPS routing needs a host C++17 compiler")
        sanitizers = [] if os.name == "nt" else [
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        with tempfile.TemporaryDirectory(prefix="meshcore-rxps-status-routes-") as directory:
            work = Path(directory)
            cpp, binary = work / "routes.cpp", work / "routes"
            cpp.write_text(source, encoding="ascii")
            built = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    *sanitizers, str(cpp), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_real_getters_are_bounded_readonly_and_match_active_primary(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
        getter = extract_braced(source, "bool MyMesh::handleRxPowerSavingGetCommand(")
        local = extract_braced(source, "bool MyMesh::handleLocalControlCommand(")
        signature = "  if (handleRxPowerSavingGetCommand(command, reply, reply_size)) return true;"
        self.assertIn(signature, local)
        # Keep the real local entry validation/space handling and registration;
        # unrelated setters/transport helpers are not part of this fixture.
        entry = local[:local.index("  if (mesh::powerdiag::handleCommand")]
        entry += signature + "\n  return false;\n}\n"
        terminal = extract_braced(source, "void MyMesh::handleTerminalCommand(")
        ascii_adapter = extract_braced(terminal, "if (handleRxPowerSavingGetCommand(command, local_reply, sizeof(local_reply)))")
        with tempfile.TemporaryDirectory(prefix="meshcore-companion-rxps-get-") as directory:
            work = Path(directory)
            (work / "production_getters.inc").write_text(getter + "\n" + entry, encoding="ascii")
            (work / "production_ascii.inc").write_text(ascii_adapter, encoding="ascii")
            for platform in ("NRF52_PLATFORM", "ESP32_PLATFORM", "STM32_PLATFORM"):
                for temporary in (0, 1):
                    with self.subTest(platform=platform, temporary=temporary):
                        binary = work / (platform + str(temporary))
                        built = subprocess.run([
                            os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                            "-D" + platform + "=1", "-DCOMPANION_FEATURE_TEMP_RADIO=" + str(temporary),
                            "-I", str(ROOT / "src"), "-isystem", str(ROOT / "test/mocks"), "-I", str(work),
                            str(ROOT / "test/fixtures/companion_rxps_getters.cpp"), "-o", str(binary),
                        ], capture_output=True, text=True, timeout=60)
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                        self.assertIn("RXPS getter contracts passed", ran.stdout)

    def test_framed_admin_and_ascii_share_the_same_getter(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
        local = extract_braced(source, "bool MyMesh::handleLocalControlCommand(")
        self.assertIn("handleRxPowerSavingGetCommand(command, reply, reply_size)", local)
        shared = extract_braced(source, "bool MyMesh::handleCommand(")
        self.assertIn("handleLocalControlCommand(command, reply, reply_capacity)", shared)
        framed = source[source.index("} else if (mesh::companion::isRunCliFrame("):]
        self.assertIn("handled = handleCommand(text, 0, reply_buf);", framed)
        admin = extract_braced(source, "void MyMesh::execAdminCommand(")
        self.assertIn("if (handleCommand(cmd, 0, reply)) return;", admin)
        self.assertNotIn("get radio.rxps", admin)
        terminal = extract_braced(source, "void MyMesh::handleTerminalCommand(")
        self.assertLess(terminal.index("handleRxPowerSavingGetCommand("), terminal.index("handleLocalControlCommand("))
        self.assertNotIn("recalcRxPowerSavingFromLevel", terminal)
        helper = extract_braced(source, "bool MyMesh::handleRxPowerSavingGetCommand(")
        self.assertNotIn("savePrefs", helper)
        self.assertNotIn("setRxPowerSaving", helper)
        self.assertNotIn("ota_", helper)
        self.assertNotIn("_prefs.rx_ps_rx_us =", helper)

    def test_framed_setters_share_checked_persistence_and_bounded_local_replies(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="ascii")
        local = extract_braced(source, "bool MyMesh::handleLocalControlCommand(")
        setter = extract_braced(local, 'if (strncmp(command, "set radio.rxps", 14) == 0')
        self.assertIn("applyAndSaveRxPowerSaving(value, result)", setter)
        # Keep the actual entry validation and command recognition. Unrelated
        # hardware/transport handlers need no boundaries in this RXPS fixture.
        entry = local[:local.index("#if defined(ESP32_PLATFORM)")]
        entry += setter + "\n  return false;\n}\n"
        shared = extract_braced(source, "bool MyMesh::handleCommand(")
        adapter = shared[:shared.index("#if defined(ESP32_PLATFORM)")]
        registration = "  if (handleLocalControlCommand(command, reply, reply_capacity)) return true;"
        self.assertIn(registration, shared)
        adapter += "  (void)sender_timestamp;\n" + registration + "\n  return false;\n}\n"
        frame = extract_braced(source, "} else if (mesh::companion::isRunCliFrame(")
        frame = frame.replace("} else if", "if", 1)
        methods = "\n".join((
            extract_braced(source, "bool MyMesh::applyAndSaveRxPowerSaving("),
            extract_braced(source, "void MyMesh::queueSavedRadioApply("), entry, adapter,
        ))
        constants = "\n".join(line for line in source.splitlines()
                              if line.startswith(("#define RESP_CODE_CLI_REPLY ",
                                                  "#define ERR_CODE_ILLEGAL_ARG ")))
        fixture = (ROOT / "test/fixtures/companion_rxps_setters.cpp").read_text(encoding="ascii")
        fixture = constants + "\n" + fixture.replace("@METHODS@", methods).replace("@FRAME@", frame)
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "RXPS routing needs a host C++17 compiler")
        sanitizers = [] if os.name == "nt" else [
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        with tempfile.TemporaryDirectory(prefix="meshcore-rxps-setter-routes-") as directory:
            work = Path(directory)
            cpp, binary = work / "routes.cpp", work / "routes"
            for removed_route in (False, True):
                with self.subTest(removed_route=removed_route):
                    # The old firmware had the parser and save helper but
                    # omitted this dispatch registration. It must fail here.
                    cpp.write_text(fixture.replace(setter, "", 1) if removed_route else fixture,
                                   encoding="ascii")
                    built = subprocess.run([
                        compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", *sanitizers,
                        "-DCOMPANION_FEATURE_TEMP_RADIO=0", "-I", str(ROOT / "src"),
                        "-I", str(ROOT / "test/fixtures"),
                        "-isystem", str(ROOT / "test/mocks"), str(cpp),
                        str(ROOT / "src/helpers/radiolib/RXPowerSaving.cpp"), "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    if removed_route:
                        self.assertNotEqual(ran.returncode, 0, "Missing RXPS dispatch was not detected")
                        self.assertIn("Assertion", ran.stderr)
                    else:
                        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                        self.assertIn("RXPS framed setter and bounded shared-route contracts passed", ran.stdout)


if __name__ == "__main__":
    unittest.main()
