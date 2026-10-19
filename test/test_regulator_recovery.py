"""Exercise the production retained-RAM regulator recovery policy on the host."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_serial_wifi_sessions import SANITIZER_FLAGS

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/RegulatorRecovery.h>
#include <cassert>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <type_traits>
using namespace mesh::power;

static_assert(std::is_trivial<RecoveryRecord>::value, "retention must be POD");
static_assert(std::is_standard_layout<RecoveryRecord>::value, "retention layout");

void initial(RecoveryRecord& r, uint8_t domains = RECOVERY_DOMAINS,
             uint8_t percent = 50, bool valid = true, uint32_t now = 0) {
  RecoveryPolicy p(r);
  p.begin(ResetKind::Cold, 0);
  p.arm(domains, now, percent, valid);
}
void counted(RecoveryRecord& r, unsigned crashes, uint8_t domains = RECOVERY_DOMAINS) {
  initial(r, domains);
  for (unsigned i = 0; i < crashes; ++i) {
    RecoveryPolicy boot(r);
    boot.begin(ResetKind::Crash, 0);
    boot.arm(domains, 0, 50, true);
  }
}
void assertCounts(const RecoveryPolicy& p, unsigned mcu, unsigned radio, unsigned fallback = 0) {
  assert(p.crashCount(RECOVERY_MCU) == mcu);
  assert(p.crashCount(RECOVERY_RADIO) == radio);
  assert(p.fallbackMask() == fallback);
}

int main(int argc, char** argv) {
  assert(argc == 2);
  const int scenario = std::atoi(argv[1]);
  RecoveryRecord r{};
  if (scenario == 0) {
    initial(r);
    for (unsigned n = 1; n <= 3; ++n) {
      RecoveryPolicy boot(r); boot.begin(ResetKind::Crash, 0);
      assertCounts(boot, n, n, n == 3 ? RECOVERY_DOMAINS : 0);
      boot.arm(RECOVERY_DOMAINS, 10, 50, true);
    }
    // A learned LDO fallback never flips back to DC-DC after more resets.
    for (unsigned n = 0; n < 4; ++n) {
      RecoveryPolicy boot(r); boot.begin(ResetKind::Crash, 0);
      assertCounts(boot, 0, 0, RECOVERY_DOMAINS);
      boot.arm(RECOVERY_DOMAINS, 10, 100, true);
    }
  } else if (scenario == 1) {
    // Battery threshold is inclusive at 50%; a low sample breaks a streak.
    counted(r, 2);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); p.clearDomain(RECOVERY_DOMAINS);
      p.arm(RECOVERY_DOMAINS, 0, 49, true); }
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0);
      p.arm(RECOVERY_DOMAINS, 0, 50, true); }
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 1, 1); }
    for (unsigned percent : {0u, 49u, 101u, 255u}) {
      initial(r, RECOVERY_DOMAINS, percent, true);
      RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0);
    }
  } else if (scenario == 2) {
    // USB voltage, a missing battery, or an unknown percentage is not evidence.
    for (unsigned percent : {0u, 50u, 100u, 255u}) {
      initial(r, RECOVERY_DOMAINS, percent, false);
      RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0);
    }
    initial(r);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 1, 1);
      p.arm(RECOVERY_DOMAINS, 0, 90, true); p.updateBattery(100, 90, false);
      p.heartbeat(200); }
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0); }
  } else if (scenario == 3) {
    for (uint32_t age : {59999u, 60000u, 60001u, 299999u}) {
      initial(r, RECOVERY_DOMAINS, 50, true, 1000);
      { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); p.arm(RECOVERY_DOMAINS, 1000, 50, true);
        p.heartbeat(1000 + age); }
      RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0);
      assertCounts(p, age <= 60000 ? 2 : 0, age <= 60000 ? 2 : 0);
    }
    // A fresh new sample, rather than an old value re-used by a heartbeat,
    // restores eligibility even when battery measurements are infrequent.
    initial(r);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); p.arm(RECOVERY_DOMAINS, 0, 50, true);
      p.heartbeat(70000); p.updateBattery(70000, 51, true); p.heartbeat(70001); }
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 2, 2); }
  } else if (scenario == 4) {
    for (ResetKind reset : {ResetKind::Cold, ResetKind::Intentional, ResetKind::Wake,
                           ResetKind::Unknown}) {
      counted(r, 2);
      RecoveryPolicy p(r); p.begin(reset, 0); assertCounts(p, 0, 0);
      p.arm(RECOVERY_DOMAINS, 0, 50, true);
      RecoveryPolicy next(r); next.begin(ResetKind::Crash, 0); assertCounts(next, 1, 1);
    }
  } else if (scenario == 5) {
    counted(r, 2);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); p.clearDomain(RECOVERY_DOMAINS);
      p.arm(RECOVERY_DOMAINS, 0, 90, true); p.markCleanReset(); }
    // A clean marker beats even a hardware reset cause labelled as a crash.
    RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0);
  } else if (scenario == 6) {
    counted(r, 2);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 3, 3, RECOVERY_DOMAINS); }
    // Sticky fallback survives power loss, sleep, software reset, unknown cause.
    for (ResetKind reset : {ResetKind::Cold, ResetKind::Intentional, ResetKind::Wake,
                           ResetKind::Unknown, ResetKind::Crash}) {
      RecoveryPolicy p(r); p.begin(reset, 0); assertCounts(p, 0, 0, RECOVERY_DOMAINS);
    }
    // With retention lost, the flash-persisted fallback still applies.
    std::memset(&r, 0xff, sizeof(r));
    RecoveryPolicy p(r); p.begin(ResetKind::Cold, RECOVERY_RADIO | 0x80);
    assertCounts(p, 0, 0, RECOVERY_RADIO);
  } else if (scenario == 7) {
    // Every single corrupted bit of a valid 32-byte record is fail-closed.
    initial(r);
    const RecoveryRecord valid = r;
    for (size_t byte = 0; byte < sizeof(r); ++byte) {
      for (unsigned bit = 0; bit < 8; ++bit) {
        r = valid;
        reinterpret_cast<unsigned char*>(&r)[byte] ^= 1u << bit;
        RecoveryPolicy p(r); p.begin(ResetKind::Crash, RECOVERY_RADIO);
        assertCounts(p, 0, 0, RECOVERY_RADIO);
      }
    }
    // Uninitialised memory and an interrupted commit cannot create a streak.
    for (unsigned fill : {0u, 0x55u, 0xaau, 0xffu}) {
      std::memset(&r, fill, sizeof(r));
      RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0);
    }
    r = valid; r.magic = 0;
    RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0);
  } else if (scenario == 8) {
    counted(r, 1);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 2, 2);
      p.arm(RECOVERY_DOMAINS, 123, 90, true);
      p.tick(123 + 299999, 90, true); assertCounts(p, 2, 2);
      p.tick(123 + 300000, 90, true); assertCounts(p, 0, 0);
      p.tick(123 + 301000, 90, true); assertCounts(p, 0, 0); }
    // Healthy operation breaks the streak; a later crash starts a new one.
    RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 1, 1);
  } else if (scenario == 9) {
    // Both the stability window and sample age work across millis() rollover.
    const uint32_t start = 0xffff0000u;
    initial(r, RECOVERY_DOMAINS, 90, true, start);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); p.arm(RECOVERY_DOMAINS, start, 90, true);
      p.tick(start + 299999u, 90, true); assertCounts(p, 1, 1);
      p.tick(start + 300000u, 90, true); assertCounts(p, 0, 0); }
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 1, 1); }
    for (uint32_t age : {60000u, 60001u}) {
      initial(r, RECOVERY_DOMAINS, 90, true, 0xfffffff0u);
      { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0);
        p.arm(RECOVERY_DOMAINS, 0xfffffff0u, 90, true); p.heartbeat(0xfffffff0u + age); }
      RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0);
      assertCounts(p, age <= 60000 ? 2 : 0, age <= 60000 ? 2 : 0);
    }
  } else if (scenario == 10) {
    // A radio DC-DC streak does not accuse a processor already using LDO.
    counted(r, 2, RECOVERY_RADIO);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 3, RECOVERY_RADIO);
      p.arm(RECOVERY_MCU, 0, 90, true); }
    for (unsigned n = 1; n <= 3; ++n) {
      RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0);
      assertCounts(p, n, 0, RECOVERY_RADIO | (n == 3 ? RECOVERY_MCU : 0));
      if (n < 3) p.arm(RECOVERY_MCU, 0, 90, true);
    }
  } else if (scenario == 11) {
    // Disabling one domain breaks only its consecutive-crash streak.
    counted(r, 1);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 2, 2);
      p.arm(RECOVERY_RADIO, 0, 90, true); }
    RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 3, RECOVERY_RADIO);
    assert(p.crashCount(RECOVERY_DOMAINS) == 0 && p.crashCount(0x80) == 0);
  } else if (scenario == 12) {
    counted(r, 2);
    RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 3, 3, RECOVERY_DOMAINS);
    p.clearDomain(RECOVERY_RADIO | 0x80); assertCounts(p, 3, 0, RECOVERY_MCU);
    p.arm(RECOVERY_RADIO, 0, 90, true);
    RecoveryPolicy next(r); next.begin(ResetKind::Crash, RECOVERY_MCU);
    assertCounts(next, 0, 1, RECOVERY_MCU);
    next.clearDomain(RECOVERY_MCU); assertCounts(next, 0, 1);
    next.clearDomain(0); assertCounts(next, 0, 1);
  } else if (scenario == 13) {
    initial(r);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 1, 1); }
    // Crash before the next arm() cannot blame a regulator not yet enabled.
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0);
      p.arm(0, 0, 90, true); }
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0); }
  } else if (scenario == 14) {
    // Independently persisted and retained fallbacks are merged, never toggled.
    counted(r, 2, RECOVERY_MCU);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, RECOVERY_RADIO);
      assertCounts(p, 3, 0, RECOVERY_DOMAINS); }
    RecoveryPolicy p(r); p.begin(ResetKind::Cold, RECOVERY_MCU);
    assertCounts(p, 0, 0, RECOVERY_DOMAINS);
  } else if (scenario == 15) {
    initial(r);
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); p.arm(RECOVERY_DOMAINS, 100, 90, true);
      p.updateBattery(101, 90, true); }
    // A sample apparently newer than the last heartbeat is conservative until
    // that heartbeat catches up; subtraction cannot turn it into a fresh age.
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0); assertCounts(p, 0, 0); }
  } else if (scenario == 16) {
    // Frozen retained record with two MCU crashes and one radio crash. Its CRC
    // and the fresh-record CRC below were independently calculated by zlib.
    const uint8_t legacy[] = {
      0x52, 0x47, 0x50, 0x52, 0x01, 0x00, 0x20, 0x00,
      0xbc, 0xcc, 0x5b, 0x07, 0x15, 0xcd, 0x5b, 0x07,
      0x0c, 0xcd, 0x5b, 0x07, 0x03, 0x00, 0x02, 0x01,
      0x4b, 0x05, 0x00, 0x00, 0x6b, 0x64, 0x72, 0xa2,
    };
    static_assert(sizeof(legacy) == sizeof(r), "frozen retained format");
    std::memcpy(&r, legacy, sizeof(r));
    { RecoveryPolicy p(r); p.begin(ResetKind::Crash, 0);
      assertCounts(p, 3, 2, RECOVERY_MCU); }
    // Losing the final complement, seed or payload extent must fail this
    // compatibility check even if newly written records remain self-consistent.
    std::memset(&r, 0, sizeof(r));
    initial(r, RECOVERY_DOMAINS, 75, true, 123456789);
    assert(r.crc == 0x1d384636u);
  } else assert(false);
  std::cout << "PASS: regulator recovery " << scenario << '\n';
}
'''


class RegulatorRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise unittest.SkipTest("a host C++11 compiler is required")
        cls.folder = tempfile.TemporaryDirectory(prefix="regulator-recovery-")
        cls.addClassCleanup(cls.folder.cleanup)
        work = Path(cls.folder.name)
        source = work / "test.cpp"
        source.write_text(HARNESS, encoding="utf-8")
        cls.binary = work / "test.exe"
        built = subprocess.run(
            [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror", *SANITIZER_FLAGS,
             "-I" + str(ROOT / "src"), str(source), "-o", str(cls.binary)],
            capture_output=True, text=True, timeout=60,
        )
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    def test_retained_state_machine(self):
        scenarios = (
            "third crash latches fallback and never reverses it",
            "50 percent threshold and invalid percentage",
            "unknown battery and USB-only readings do not count",
            "fresh sample boundary and heartbeat timestamp separation",
            "cold intentional wake and unknown causes break streak",
            "clean reset marker defeats misleading reset reason",
            "sticky fallback survives normal resets and retention loss",
            "every record bit corruption fails closed",
            "five minute stability clears the streak",
            "stability and freshness survive timer rollover",
            "radio and MCU learn fallback independently",
            "inactive regulator does not accrue crashes",
            "manual domain change clears only its fallback and count",
            "a boot is disarmed until the actual modes are selected",
            "saved and retained fallback masks merge",
            "future sample is ineligible until heartbeat catches up",
            "retained CRC remains compatible with independently encoded records",
        )
        for number, scenario in enumerate(scenarios):
            with self.subTest(scenario=scenario):
                checked = subprocess.run([str(self.binary), str(number)],
                                         capture_output=True, text=True, timeout=10)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                self.assertIn(f"PASS: regulator recovery {number}", checked.stdout)


if __name__ == "__main__":
    unittest.main()
