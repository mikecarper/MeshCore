"""Exercise production install policy before handing a store to the applier."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]


class OtaInstallPreflightTest(unittest.TestCase):
    def test_storage_failures_cannot_skip_hardware_and_auto_install_policy(self):
        context = (ROOT / "src/helpers/ota/OtaContext.h").read_text()
        production = method(context, "bool hwMatches(") + "\n" + method(
            context, "bool apply_fetched_impl(")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "preflight.h").write_text(production)
            source = path / "test.cpp"
            source.write_text(r'''
#include <helpers/ota/MotaContainer.h>
#include <helpers/ota/OtaByteIO.h>
#include <helpers/ota/FirmwareInfo.h>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "test_ota/mota_vectors.h"
using namespace mesh::ota;
struct Store {
  std::vector<uint8_t> bytes{MOTA_VEC, MOTA_VEC + MOTA_VEC_LEN};
  mutable int fail_at = -1;
  uint32_t staged_size() const { return bytes.size(); }
  bool read(uint32_t off, uint8_t* out, uint32_t len) const {
    if (int(off) == fail_at) { fail_at = -1; return false; }
    if (off > bytes.size() || len > bytes.size() - off) return false;
    memcpy(out, bytes.data() + off, len); return true;
  }
  const uint8_t* data() const { return bytes.data(); }
};
static int apply_calls = 0;
static bool apply_ok = true, self_ok = true;
static uint32_t running_version = EXP_FW_VERSION - 1;
static bool ota_self_firmware(SelfFwInfo& self) {
  self.valid = self_ok; self.fw_version = running_version; return self_ok;
}
// Hardware apply is the boundary under test: a failed preflight must never
// reach it, regardless of whether a second store read might now succeed.
static bool ota_apply_detools_mota(Store&, int, int&, char*) {
  ++apply_calls; return apply_ok;
}
static bool ota_apply_mota_nrf52(Store&, int, int&, char*) {
  ++apply_calls; return apply_ok;
}
struct Context {
  Store fetch_store;
  int allow = 0, apply_st = 0;
  bool fetch_to_folder = false, apply_pending = false, bootloader_apply_pending = false;
  char hw_id[33] = "TESTHW";
#include "preflight.h"
};
int main(int argc, char** argv) {
  assert(argc == 2);
  const int scenario = atoi(argv[1]);
  Context context;
  char reply[160] = {};
  bool trusted_auto = false;
  bool expected = false;
  if (scenario == 0) {
    strcpy(context.hw_id, "WRONG_BOARD"); context.fetch_store.fail_at = 0;
  } else if (scenario == 1) {
    context.fetch_store.fail_at = 0; trusted_auto = true; // unsigned package
  } else if (scenario == 2) {
    context.fetch_store.bytes[9] |= MFLAG_SIGNED;
    running_version = EXP_FW_VERSION; trusted_auto = true; context.fetch_store.fail_at = 0;
  } else if (scenario == 3) {
    context.fetch_store.bytes.resize(8);
  } else if (scenario == 4) {
    context.fetch_store.bytes[0] ^= 1;
  } else if (scenario == 5) {
    context.fetch_store.bytes[4] ^= 1;
  } else if (scenario == 6) {
    context.fetch_store.fail_at = 8;
  } else if (scenario == 7) {
    strcpy(context.hw_id, "WRONG_BOARD");
  } else if (scenario == 8) {
    trusted_auto = true; // unsigned is never eligible for auto-install
  } else if (scenario == 9) {
    context.fetch_store.bytes[9] |= MFLAG_SIGNED;
    trusted_auto = true; running_version = EXP_FW_VERSION;
  } else if (scenario == 10) {
    context.fetch_store.bytes[9] |= MFLAG_SIGNED;
    trusted_auto = true; self_ok = false;
  } else if (scenario == 11) {
    context.fetch_store.bytes[9] |= MFLAG_SIGNED;
    trusted_auto = true; expected = true;
  } else if (scenario == 12) {
    running_version = EXP_FW_VERSION + 1; expected = true; // manual override retained
  } else if (scenario == 13) {
    context.fetch_to_folder = true;
  } else if (scenario == 14) {
    context.hw_id[0] = 0; expected = true; // existing unknown-hardware policy
  } else if (scenario == 15) {
    apply_ok = false;
  }
  assert(context.apply_fetched_impl(nullptr, trusted_auto, reply) == expected);
  assert(context.apply_pending == expected);
  assert(!context.bootloader_apply_pending);
  assert(apply_calls == (expected || scenario == 15 ? 1 : 0));
  if (scenario == 0) {
    // Once I/O recovers, retry still evaluates hardware compatibility.
    assert(!context.apply_fetched_impl(nullptr, false, reply));
    assert(apply_calls == 0 && strstr(reply, "incompatible hardware"));
    strcpy(context.hw_id, "TESTHW");
    assert(context.apply_fetched_impl(nullptr, false, reply));
    assert(apply_calls == 1 && context.apply_pending);
  }
}
''')
            for platform in ("esp32", "nrf52_qspi"):
                defines = (["-DESP32_PLATFORM", "-DOTA_FLASH_STORE"]
                           if platform == "esp32" else ["-DNRF52_PLATFORM", "-DOTA_QSPI_STORE"])
                binary = path / platform
                flags = [] if os.name == "nt" else ["-fsanitize=address,undefined"]
                result = subprocess.run([
                    "c++", "-std=c++17", "-ffunction-sections", "-fdata-sections",
                    "-Wl,--gc-sections", *flags, *defines,
                    "-I", str(ROOT / "src"), "-I", str(ROOT / "test"),
                    "-I", str(ROOT / "test/mocks"),
                    str(source), str(ROOT / "src/helpers/ota/MotaContainer.cpp"),
                    "-o", str(binary),
                ], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                for scenario in range(16):
                    with self.subTest(platform=platform, scenario=scenario):
                        result = subprocess.run([str(binary), str(scenario)], text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
