"""Run the production bootloader CLI branch with and without an embedded image."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body


ROOT = Path(__file__).resolve().parents[1]
SOURCE = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <helpers/CLIReplyText.h>

namespace mesh { struct Utils {
  static bool fromHex(uint8_t* out, size_t size, const char* text) {
    for (size_t i = 0; i < size; i++) {
      unsigned value;
      if (sscanf(text + i * 2, "%2x", &value) != 1) return false;
      out[i] = value;
    }
    return true;
  }
  static void toHex(char* out, const uint8_t* bytes, size_t size) {
    for (size_t i = 0; i < size; i++) sprintf(out + i * 2, "%02X", bytes[i]);
  }
}; }
struct OtaBootloaderIdentity {
  bool present = true, crc_ok = true;
  uint32_t board_id = 0x12345678, crc32 = 0;
  const char* device_name = "TEST_DFU";
};
struct OtaBlCaps { unsigned apply_abi = 3, storage_flags = 1; };
static uint32_t ota_bootloader_target_id(const OtaBootloaderIdentity& id) { return id.board_id; }
static bool ota_bootloader_self_update_caps_valid(const OtaBlCaps&) { return true; }
struct MotaManifest {
  uint8_t image_hash[8] = {};
  bool is_bootloader() const { return true; }
};
struct OtaManager {
  enum FetchState { IDLE, WANT_MANIFEST, FETCHING, COMPLETE, FAILED, PAUSED };
  FetchState state = IDLE;
  bool serve_jobs = false, manifest_jobs = false;
  uint8_t mid[4] = {0x12, 0x34, 0x56, 0x78};
  FetchState fetchState() const { return state; }
  bool pendingServeJobs() const { return serve_jobs; }
  bool pendingManifestJobs() const { return manifest_jobs; }
  const uint8_t* fetchManifestId() const { return mid; }
  bool fetched_is_bootloader() const { return true; }
};
struct OtaContext {
  OtaManager manager;
  struct Store { unsigned staged_size() const { return 100; } } fetch_store;
  bool apply_pending = false, bootloader_apply_pending = false, fetch_to_folder = false;
  bool sd_cache_fetching = false;
  unsigned staged_installs = 0;
  OtaBootloaderIdentity identity;
  OtaBlCaps caps;
  bool sdCacheFetching() const { return sd_cache_fetching; }
  @IDLE@
  const OtaBootloaderIdentity& bootloaderIdentity() const { return identity; }
  const OtaBlCaps& bootloaderUpdateCaps() const { return caps; }
  bool apply_fetched_bootloader(const uint8_t* mid, const uint8_t* hash, char* reply) {
    assert(mid[0] == 0x12 && mid[3] == 0x78 && hash[0] == 0x01 && hash[7] == 0xEF);
    staged_installs++;
    strcpy(reply, "staged install");
    return true;
  }
};
static bool staged_manifest(OtaContext&, MotaManifest&) { return true; }
static unsigned requests = 0, statuses = 0, audits = 0;
static bool automatic = false;
static std::string confirmation;
static void bootloader_migration_status(char* out, size_t) { statuses++; strcpy(out, "STATUS"); }
static void bootloader_migration_audit(char* out, size_t) { audits++; strcpy(out, "AUDIT"); }
static bool bootloader_migration_request(const char* hash, char* out, size_t) {
  requests++;
  automatic = hash == nullptr;
  confirmation = hash ? hash : "";
  strcpy(out, automatic ? "AUTO" : "HASH");
  return true;
}
@HELPERS@
static bool command(const char* a, char* reply, OtaContext& c) {
  const char* rest = nullptr;
  @COMMAND@
  else { return false; }
  return true;
}
static void expect(const char* text, const char* expected, OtaContext& c) {
  char reply[160] = {};
  assert(command(text, reply, c));
  if (strcmp(reply, expected)) {
    fprintf(stderr, "%s: expected '%s', got '%s'\n", text, expected, reply);
    assert(false);
  }
}
int main() {
  OtaContext c;
#if defined(OTA_BOOTLOADER_MIGRATION)
  expect("bootloader update ignored", "ERR usage: ota bootloader update", c);
  assert(requests == 0);
  expect("bootloader update", "AUTO", c);
  assert(requests == 1 && automatic);
  expect("blupdate   update   ", "AUTO", c);
  assert(requests == 2 && automatic);
  for (auto state : {OtaManager::IDLE, OtaManager::COMPLETE, OtaManager::FAILED}) {
    c.manager.state = state;
    expect("bootloader update", "AUTO", c);
  }
  for (auto state : {OtaManager::FETCHING, OtaManager::WANT_MANIFEST, OtaManager::PAUSED}) {
    c.manager.state = state;
    expect("bootloader update", "ERR another OTA operation is active", c);
  }
  c.manager.state = OtaManager::IDLE;
  for (bool* flag : {&c.apply_pending, &c.bootloader_apply_pending, &c.manager.serve_jobs,
                     &c.manager.manifest_jobs, &c.sd_cache_fetching}) {
    *flag = true;
    expect("bootloader update", "ERR another OTA operation is active", c);
    expect("bootloader embedded install 0123456789ABCDEF",
           "ERR another OTA operation is active", c);
    *flag = false;
  }
  assert(requests == 5);
  expect("bootloader embedded", "STATUS", c);
  expect("bootloader embedded status", "STATUS", c);
  expect("bootloader embedded audit", "AUDIT", c);
  assert(statuses == 2 && audits == 1 && requests == 5);
  expect("bootloader embedded install 0123456789ABCDEF", "HASH", c);
  assert(requests == 6 && !automatic && confirmation == "0123456789ABCDEF");
  expect("bootloader embedded install", "HASH", c);
  assert(requests == 7 && !automatic && confirmation.empty());
  expect("bootloader embedded install 0123456789ABCDEF extra", "HASH", c);
  assert(requests == 8 && !automatic && confirmation == "0123456789ABCDEF extra");
#else
  // Ordinary images keep their existing staged-update command surface.
#if defined(NRF52_PLATFORM) && !defined(OTA_SEEDER_ONLY) && defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
  expect("bootloader update", "ERR usage: ota bootloader install <MID8> <HASH16>", c);
  expect("bootloader update ignored", "ERR usage: ota bootloader install <MID8> <HASH16>", c);
#else
  expect("bootloader update", "ERR this build cannot update its bootloader over LoRa", c);
#endif
  assert(requests == 0);
#endif
#if defined(NRF52_PLATFORM) && !defined(OTA_SEEDER_ONLY) && defined(OTA_INTERNAL_BOOTLOADER_UPDATE)
  expect("bootloader updatejunk", "ERR usage: ota bootloader install <MID8> <HASH16>", c);
  expect("bootloader install", "ERR usage: ota bootloader install <MID8> <HASH16>", c);
  c.manager.state = OtaManager::COMPLETE;
  expect("bootloader install 12345678 0123456789ABCDEF", "OK | staged install", c);
  assert(c.staged_installs == 1);
  expect("bootloader install 12345678 0123456789ABCDEF extra",
         "ERR usage: ota bootloader install <MID8> <HASH16>", c);
  assert(c.staged_installs == 1);
  for (const char* text : {
      "bootloader install   12345678   0123456789ABCDEF   ",
      "bootloader install \t12345678\v\f0123456789ABCDEF\r\n"}) {
    expect(text, "OK | staged install", c);
  }
  assert(c.staged_installs == 3);
  for (const char* text : {
      "bootloader install \t\n", "bootloader install 12345678",
      "bootloader install 123456789 0123456789ABCDEF",
      "bootloader install 12345678 0123456789ABCDEFF",
      "bootloader install 123456780123456789ABCDEF",
      "bootloader install 12345678 0123456789ABCDEF \t extra"}) {
    expect(text, "ERR usage: ota bootloader install <MID8> <HASH16>", c);
  }
  for (const char* text : {
      "bootloader install 1234567 0123456789ABCDEF",
      "bootloader install 12345678 0123456789ABCDE",
      "bootloader install 1234567Z 0123456789ABCDEF",
      "bootloader install 12345678 0123456789ABCDEZ"}) {
    expect(text, "ERR MID must be 8 hex and HASH must be 16 hex characters", c);
  }
  assert(c.staged_installs == 3);
#else
  expect("bootloader install 12345678 0123456789ABCDEF",
         "ERR this build cannot update its bootloader over LoRa", c);
  assert(c.staged_installs == 0);
#endif
}
'''


def harness():
    cli = (ROOT / "src/helpers/ota/OtaCli.cpp").read_text(encoding="utf-8")
    context = (ROOT / "src/helpers/ota/OtaContext.h").read_text(encoding="utf-8")
    return (SOURCE.replace("@IDLE@", body(context, "bool embeddedBootloaderIdle() const"))
            .replace("@HELPERS@", "\n".join(body(cli, signature) for signature in (
                "static bool is_cmd(", "static bool parse_hex_exact(",
                "static __attribute__((noinline)) int parse_two_words(")))
            .replace("@COMMAND@", body(cli, 'if (is_cmd(a, "bootloader|blupdate", &rest))')))


class BootloaderUpdateCliTest(unittest.TestCase):
    def test_production_dispatch_keeps_embedded_and_staged_updates_separate(self):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++17 compiler is required")
        variants = {
            "embedded": ("OTA_BOOTLOADER_MIGRATION", "NRF52_PLATFORM",
                         "OTA_INTERNAL_BOOTLOADER_UPDATE", "OTA_SD_STORE"),
            "staged": ("NRF52_PLATFORM", "OTA_INTERNAL_BOOTLOADER_UPDATE"),
            "unsupported": (),
            "seeder": ("NRF52_PLATFORM", "OTA_INTERNAL_BOOTLOADER_UPDATE", "OTA_SEEDER_ONLY"),
        }
        sanitize = [] if os.name == "nt" else [
            "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
        with tempfile.TemporaryDirectory(prefix="bootloader-update-cli-") as directory:
            source = Path(directory) / "cli.cpp"
            source.write_text(harness(), encoding="ascii")
            for name, macros in variants.items():
                with self.subTest(variant=name):
                    binary = Path(directory) / (name + (".exe" if os.name == "nt" else ""))
                    built = subprocess.run([
                        compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-function", "-Wno-unused-parameter", *sanitize,
                        *["-D" + macro + "=1" for macro in macros], "-I", str(ROOT / "src"),
                        str(source), "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)


if __name__ == "__main__":
    unittest.main()
