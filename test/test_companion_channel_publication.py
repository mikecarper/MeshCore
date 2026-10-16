#!/usr/bin/env python3
"""Verify real channel writers and callers preserve publication authority."""
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_companion_tx_routing as tx
from test_radio_receive_contract import method
from test_companion_preferences_transaction import esp_recovery_helpers

ROOT = Path(__file__).resolve().parents[1]


def publication_fixture(work, missing_latch):
    store = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
    header = (ROOT / "examples/companion_radio/DataStore.h").read_text()
    mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
    backend = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text()
    backend = backend.replace("  bool fail_write = false;", """
  bool arm_public_read_failure = false, arm_namespace_failure = false;
  unsigned public_read_failures = 0;
  mutable unsigned namespace_failures = 0;
  bool fail_write = false;""", 1)
    backend = backend.replace("  bool exists(const char* path) const { return files.count(path); }", """
  bool exists(const char* path) const {
    if (namespace_failures && !strcmp(path, "/channels2")) {
      --namespace_failures;
      return false; // Arduino-Pico hides an lfs_stat IO error as absence.
    }
    return files.count(path);
  }""", 1)
    backend = backend.replace("    files[to] = files[from]; files.erase(from); return true;", """
    files[to] = files[from]; files.erase(from);
    if (!strcmp(to, "/channels2")) {
      if (arm_public_read_failure && !strcmp(from, "/channels2.tmp")) {
        public_read_failures = 3;
        arm_public_read_failure = false;
      }
      if (arm_namespace_failure) {
        namespace_failures = 1;
        arm_namespace_failure = false;
      }
    }
    return true;""", 1)
    backend = backend.replace("    if (*mode == 'r' && fail_read_open) return {};", """
    if (*mode == 'r' && public_read_failures && !strcmp(path, "/channels2")) {
      --public_read_failures;
      return {};
    }
    if (*mode == 'r' && fail_read_open) return {};""", 1)
    (work / "helpers").mkdir()
    (work / "helpers/IdentityStore.h").write_text(backend)
    transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text()
    (work / "ContactFileTransaction.h").write_text(transaction.replace(
        '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))

    save = method(store, "bool DataStore::saveChannels(")
    latch = ("  if (file.outcome() == decltype(file)::Outcome::Indeterminate)\n"
             "    _channel_load_incomplete = true;\n")
    if missing_latch:
        # Restore precisely the pre-fix missing publication-authority latch.
        assert save.count(latch) == 1
        save = save.replace(latch, "")
    # Observability only: record the real writer result, including a failed
    # constructor which never entered the if(file) or commit() branches.
    assert save.count("  return success;") == 1
    save = save.replace("  return success;", "  last_channel_outcome = int(file.outcome());\n  return success;")
    recovery = esp_recovery_helpers(store)
    recovery = recovery.replace("  assert(!strncmp(path, \"/spiffs\", 7));", """
  assert(!strncmp(path, "/spiffs", 7));
  if (fs->namespace_failures && !strcmp(path + 7, "/channels2")) {
    --fs->namespace_failures;
    errno = EIO;
    return -1; // ESP metadata errors remain distinguishable from absence.
  }""", 1)
    methods = recovery + "\n" + method(mesh, "bool MyMesh::handleTxRoutingCommand(")
    methods += "\n" + method(store, "void DataStore::loadChannels(")
    methods += "\n" + save + "\n" + method(store, "bool DataStore::hasIncompleteContactLoad(")
    binary_start = mesh.index("  } else if (cmd_frame[0] == CMD_SET_CHANNEL && len >= 2 + 32 + 16) {")
    binary_end = mesh.index("  } else if (cmd_frame[0] == CMD_SIGN_START)", binary_start)
    methods += ("\nvoid MyMesh::applyChannelFrame(const uint8_t* cmd_frame, size_t len) {\n"
                "  if (false) {\n" + mesh[binary_start:binary_end] + "\n  }\n}\n")

    source = tx.HARNESS[:tx.HARNESS.index("int main() {")]
    source = source.replace("#if defined(STM32_PLATFORM)\n#define ATOMIC_FILE_WRITER_IMPLEMENTATION",
                            "#if defined(STM32_PLATFORM) || defined(NRF52_PLATFORM)\n#define ATOMIC_FILE_WRITER_IMPLEMENTATION", 1)
    source = source.replace("#define MESH_DEBUG_PRINTLN(...) ((void)0)", """
#define MESH_DEBUG_PRINTLN(...) ((void)0)
static int last_channel_outcome = -1;
enum { CMD_SET_CHANNEL = 32, ERR_CODE_ILLEGAL_ARG = 1, ERR_CODE_BAD_STATE = 2,
       ERR_CODE_UNSUPPORTED_CMD = 3, ERR_CODE_NOT_FOUND = 4, ERR_CODE_FILE_IO_ERROR = 5 };
struct StrHelper {
  static void strncpy(char* target, const char* from, size_t size) {
    ::strncpy(target, from, size - 1); target[size - 1] = 0;
  }
};
""", 1)
    source = source.replace(" bool _channel_load_incomplete=false;", """
 bool _channel_load_incomplete=false;
#if defined(NRF52_PLATFORM)
 bool _contact_load_incomplete=false;
 struct { bool empty() const { return true; } } _unread_contact_pages;
#endif
""", 1)
    source = source.replace(" bool handleTxRoutingCommand(const char*,char*,size_t);", """
 bool handleTxRoutingCommand(const char*,char*,size_t);
 int last_error = 0;
 bool binary_ack = false;
 void writeErrFrame(int error) { last_error = error; }
 void writeOKFrame() { binary_ack = true; }
 void seedEmergencyChannelNotice(uint8_t, const ChannelDetails&) {}
 bool resolveChannelAlias(ChannelDetails&) const { return true; }
 void applyChannelFrame(const uint8_t*, size_t);
""", 1)
    source = source.replace("@RECOVERY_METHOD@", method(header, "  bool contactRecoveryPending() const"))
    source = source.replace("@CHANNEL_METHOD@", method(header, "  bool hasIncompleteChannelLoad() const"))
    source = source.replace("@METHODS@", """
#if defined(NRF52_PLATFORM)
static bool contactPathPresence(FILESYSTEM* fs, const char* path, bool& present, uint32_t* size = nullptr) {
  present = fs->exists(path);
  if (size) *size = present ? fs->files.at(path).size() : 0;
  return true;
}
#endif
""" + methods)
    source += (ROOT / "test/fixtures/companion_channel_publication/checks.h").read_text()
    generated = work / "test.cpp"
    generated.write_text(source)
    return generated


class CompanionChannelPublicationTest(unittest.TestCase):
    def run_platform(self, platform, missing_latch=False):
        with tempfile.TemporaryDirectory(prefix="mesh-channel-publication-") as directory:
            work = Path(directory)
            fixture = publication_fixture(work, missing_latch)
            binary = work / "test"
            compiled = subprocess.run([
                "g++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-D" + platform + "=1",
                "-DMESH_CONTACT_CACHE=0", "-DMESH_ENABLE_MESHTEXT_CHAT=0",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(work), "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"), "-I", str(ROOT / "src/helpers"),
                str(fixture), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if missing_latch:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('strstr(reply, "storage unavailable")', result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS: channel publication authority", result.stdout)

    def test_atomic_stm32_publication_and_retry_controls(self):
        self.run_platform("STM32_PLATFORM")

    def test_atomic_nrf52_publication_and_retry_controls(self):
        self.run_platform("NRF52_PLATFORM")

    def test_esp32_namespace_and_constructor_uncertainty(self):
        self.run_platform("ESP32_PLATFORM")

    def test_rp2040_namespace_and_constructor_uncertainty(self):
        self.run_platform("RP2040_PLATFORM")

    def test_missing_stm32_publication_latch_is_detected(self):
        self.run_platform("STM32_PLATFORM", missing_latch=True)

    def test_missing_nrf52_publication_latch_is_detected(self):
        self.run_platform("NRF52_PLATFORM", missing_latch=True)

    def test_missing_esp32_publication_latch_is_detected(self):
        self.run_platform("ESP32_PLATFORM", missing_latch=True)

    def test_missing_rp2040_publication_latch_is_detected(self):
        self.run_platform("RP2040_PLATFORM", missing_latch=True)


if __name__ == "__main__":
    unittest.main()
