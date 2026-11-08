"""Exercise the production calibration store and rollback across filesystems."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/BatteryCalibrationStore.h>
#include <helpers/BatteryCalibration.h>
#include <cassert>
#include <cmath>
#include <cstring>
#include <string>
#include <vector>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include <helpers/AtomicFileWriter.h>
#endif
#include "store_under_test.cpp"

struct Board : mesh::MainBoard {
  bool capable = true;
  uint16_t raw = 4000;
  bool supportsBatteryCalibration() const override { return capable; }
  uint16_t getBattMilliVolts() override { return calibrateBatteryMillivolts(raw); }
  const char* getManufacturerName() const override { return "fixture"; }
  uint8_t getStartupReason() const override { return 0; }
  void reboot() override {}
  void sleep(uint32_t) override {}
};
int main() {
  using namespace mesh::power;
  MemoryFS fs;
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  fs.rename_replaces = true;
#endif
  Board board;
  assert(beginBatteryCalibrationStore(board, &fs));
  assert(fs.files.empty() && board.getAdcMultiplier() == 1);
  char reply[160];
  auto run = [&](const char* command) {
    strcpy(reply, "untouched");
    return handleStoredBatteryCalibrationCommand(board, command, reply, sizeof(reply));
  };
  assert(run("get adc.multiplier") && !strcmp(reply, "> 1.000"));
  assert(!run("get adc.multiplierXYZ") && !strcmp(reply, "untouched"));
  assert(run("battery calibrate 4.18") && strstr(reply, "(saved)"));
  assert(fabs(board.getAdcMultiplier() - 1.045f) < 0.00001f);
  assert(board.getBattMilliVolts() == 4180);
  const auto good = fs.files.at("/battery_cal");
  assert(good.size() == 12);
  Board rebooted;
  assert(beginBatteryCalibrationStore(rebooted, &fs));
  assert(fabs(rebooted.getAdcMultiplier() - 1.045f) < 0.00001f);
  fs.fail_write = true;
  assert(run("set adc.multiplier 1.1") && strstr(reply, "save unconfirmed"));
  assert(fabs(board.getAdcMultiplier() - 1.045f) < 0.00001f);
  assert(fs.files.at("/battery_cal") == good);
  fs.fail_write = false;
  // Publication can succeed before verification fails. Report uncertainty
  // and restore runtime without claiming the old disk image survived.
  fs.fail_after_publication = true;
  assert(run("set adc.multiplier 1.1") && strstr(reply, "save unconfirmed"));
  assert(fabs(board.getAdcMultiplier() - 1.045f) < 0.00001f);
  assert(fs.files.at("/battery_cal") != good);
  fs.fail_after_publication = false;
  fs.fail_read_open = false;
  Board published;
  assert(beginBatteryCalibrationStore(published, &fs));
  assert(fabs(published.getAdcMultiplier() - 1.1f) < 0.00001f);
  fs.files["/battery_cal"] = good;
  for (const char* command : {"set adc.multiplier nan", "set adc.multiplier 1junk",
       "set adc.multiplier -1", "set adc.multiplier 1.6",
       "get adc.multiplier junk", "battery calibrate 0"}) {
    assert(run(command) && !strncmp(reply, "Error:", 6));
    assert(fs.files.at("/battery_cal") == good);
  }
  // Complete but corrupted records, truncations and invalid factors cannot
  // change the factory conversion on a new instance.
  for (size_t bit = 0; bit < good.size() * 8; ++bit) {
    fs.files["/battery_cal"] = good;
    fs.files["/battery_cal"][bit / 8] ^= uint8_t(1U << (bit % 8));
    Board fresh;
    assert(!beginBatteryCalibrationStore(fresh, &fs));
    assert(fresh.getAdcMultiplier() == 1);
  }
  fs.files["/battery_cal"] = good;
  fs.files["/battery_cal"].pop_back();
  Board truncated;
  assert(!beginBatteryCalibrationStore(truncated, &fs));
  assert(truncated.getAdcMultiplier() == 1);
  fs.files["/battery_cal"] = good;
  assert(run("battery calibrate reset") && strstr(reply, "(saved)"));
  assert(board.getAdcMultiplier() == 1);
  Board reset;
  assert(beginBatteryCalibrationStore(reset, &fs) && reset.getAdcMultiplier() == 1);
  board.capable = false;
  assert(run("get adc.multiplier") && strstr(reply, "unsupported"));
  assert(run("battery calibrate 4.18") && strstr(reply, "unsupported"));
  assert(run("set adc.multiplier 1.1") && strstr(reply, "unsupported"));
  board.capable = true;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  // Recover the old record if a reset interrupted the two-rename publication.
  fs.files["/battery_cal.bak"] = good;
  fs.files.erase("/battery_cal");
  Board recovered;
  assert(beginBatteryCalibrationStore(recovered, &fs));
  assert(recovered.getBattMilliVolts() == 4180);
#endif
  char bounded[4] = {'X', 'X', 'X', 'Z'};
  assert(handleStoredBatteryCalibrationCommand(board, "get adc.multiplier", bounded, 3));
  assert(bounded[2] == 0 && bounded[3] == 'Z');
  // A failed mount or volatile fallback must not claim reboot persistence.
  assert(!beginBatteryCalibrationStore(board, nullptr));
  const float before_missing_fs = board.getAdcMultiplier();
  assert(run("battery calibrate 4.18") && strstr(reply, "save unconfirmed"));
  assert(board.getAdcMultiplier() == before_missing_fs);
}
'''


class BatteryCalibrationStoreTests(unittest.TestCase):
    def test_policy_only_recipe_loads_policy_before_application_store(self):
        recipe = (ROOT / "variants/rak4631/platformio.ini").read_text(encoding="ascii")
        board = (ROOT / "variants/rak4631/RAK4631Board.cpp").read_text(encoding="ascii")
        nrf52 = (ROOT / "src/helpers/NRF52Board.cpp").read_text()
        self.assertIn("-D NRF52_POWER_MANAGEMENT", recipe)
        self.assertIn("-D BATTERY_CALIBRATION_VOLTAGE_POLICY_ONLY=1", recipe)
        begin = board[board.index("void RAK4631Board::begin() {"):]
        self.assertIn("checkBootVoltage(&power_config);", begin)
        self.assertIn("mesh::power::loadVoltagePolicyAtBoot(config->voltage_bootlock);", nrf52)

    def test_nrf52_typed_policy_setter_retains_record_cli_and_failure_behavior(self):
        with tempfile.TemporaryDirectory(prefix="battery-calibration-policy-") as temp:
            work = Path(temp)
            (work / "helpers").mkdir()
            mock = ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h"
            (work / "helpers/IdentityStore.h").write_text(mock.read_text(encoding="ascii"), encoding="ascii")
            (work / "InternalFileSystem.h").write_text(r'''
#pragma once
#include <helpers/IdentityStore.h>
struct lfs_config {};
class Adafruit_LittleFS : public MemoryFS {
public:
  bool mount_ok = true;
  bool begin(struct lfs_config* = nullptr) { return mount_ok; }
  void end() {}
};
Adafruit_LittleFS InternalFS;
''', encoding="ascii")
            policy = (ROOT / "src/helpers/NRF52VoltagePolicy.cpp").read_text(encoding="ascii")
            for header in ("NRF52VoltagePolicy.h", "IdentityStore.h", "AtomicFileWriter.h",
                           "PersistentStoreFormat.h", "CLICommandUtils.h"):
                policy = policy.replace(f'#include "{header}"', f'#include <helpers/{header}>')
            (work / "policy_under_test.cpp").write_text(policy, encoding="ascii")
            store = (ROOT / "src/helpers/BatteryCalibrationStore.cpp").read_text(encoding="ascii")
            for header in ("BatteryCalibrationStore.h", "BatteryCalibration.h", "CheckedSettingsFile.h",
                           "CLICommandUtils.h", "PersistentStoreFormat.h", "AtomicFileWriter.h",
                           "NRF52VoltagePolicy.h"):
                store = store.replace(f'#include "{header}"', f'#include <helpers/{header}>')
            (work / "store_under_test.cpp").write_text(store, encoding="ascii")
            (work / "test.cpp").write_text(r'''
#include <cassert>
#include <cmath>
#include <cstring>
#include <initializer_list>
#define ATOMIC_FILE_WRITER_IMPLEMENTATION
#include "policy_under_test.cpp"
#undef ATOMIC_FILE_WRITER_IMPLEMENTATION
#include "store_under_test.cpp"
struct PolicyBoard : mesh::MainBoard {
  bool supportsBatteryCalibration() const override { return true; }
  float getAdcMultiplier() const override { return mesh::power::configuredAdcPermille() / 1000.0f; }
  uint16_t getBattMilliVolts() override {
    return mesh::power::calibratedMillivolts(4000, mesh::power::configuredAdcPermille());
  }
  const char* getManufacturerName() const override { return "fixture"; }
  uint8_t getStartupReason() const override { return 0; }
  void reboot() override {}
  void sleep(uint32_t) override {}
};
int main() {
  using namespace mesh::power;
  PolicyBoard board;
  char reply[160];
  InternalFS.files["/battery_cal"] = {0xde, 0xad};
  const auto unused_record = InternalFS.files.at("/battery_cal");
  // Neither a supplied filesystem nor no filesystem may silently select a
  // second store before the tagged board has loaded its authoritative policy.
  assert(!beginBatteryCalibrationStore(board, &InternalFS));
  assert(!beginBatteryCalibrationStore(board, nullptr));
  assert(!setStoredAdcMultiplier(board, 1.1f, reply, sizeof(reply)));
  assert(strstr(reply, "unsupported"));
  InternalFS.mount_ok = false;
  loadVoltagePolicyAtBoot(3300);
  assert(voltagePolicySupported() && configuredAdcPermille() == 1000);
  assert(beginBatteryCalibrationStore(board, nullptr));
  InternalFS.fail_write = true;
  assert(!setStoredAdcMultiplier(board, 1.1f, reply, sizeof(reply)));
  assert(configuredAdcPermille() == 1000);
  InternalFS.fail_write = false;
  InternalFS.mount_ok = true;
  InternalFS.rename_replaces = true;
  loadVoltagePolicyAtBoot(3300);
  assert(beginBatteryCalibrationStore(board, &InternalFS));
  assert(handleStoredBatteryCalibrationCommand(board, "battery calibrate 4.18", reply, sizeof(reply)));
  assert(strstr(reply, "4.180 V") && strstr(reply, "(saved)"));
  assert(configuredAdcPermille() == 1045);
  const auto good = InternalFS.files.at("/pwrmgt_v1");
  assert(good.size() == 20 && !memcmp(good.data(), "NVP2", 4));
  assert(mesh::storage::readLE16(good.data() + 12) == 1045);
  assert(mesh::storage::readLE32(good.data() + 16)
      == mesh::storage::updateCRC32(0xffffffffUL, good.data(), 16));
  loadVoltagePolicyAtBoot(3300);
  assert(configuredAdcPermille() == 1045);
  InternalFS.files["/pwrmgt_v1"][16] ^= 1;
  loadVoltagePolicyAtBoot(3300);
  assert(configuredAdcPermille() == 1000);
  assert(beginBatteryCalibrationStore(board, nullptr));
  assert(InternalFS.files.at("/battery_cal") == unused_record);
  InternalFS.files["/pwrmgt_v1"] = good;
  loadVoltagePolicyAtBoot(3300);
  assert(configuredAdcPermille() == 1045);
  assert(handleVoltagePolicyCommand("set adc.multiplier 1.1", reply, sizeof(reply)));
  assert(!strcmp(reply, "OK - ADC multiplier 1.100 saved"));
  assert(configuredAdcPermille() == 1100);
  assert(setConfiguredAdcMultiplier(0, reply, sizeof(reply)));
  assert(configuredAdcPermille() == 1000);
  assert(setConfiguredAdcMultiplier(0.5f, reply, sizeof(reply)));
  assert(configuredAdcPermille() == 500);
  assert(setConfiguredAdcMultiplier(1.5f, reply, sizeof(reply)));
  assert(configuredAdcPermille() == 1500);
  const auto before_invalid = InternalFS.files.at("/pwrmgt_v1");
  for (float invalid : {NAN, INFINITY, -INFINITY, -1.0f, 0.49f, 1.51f, 65536.0f}) {
    assert(!setConfiguredAdcMultiplier(invalid, reply, sizeof(reply)));
    assert(!strncmp(reply, "Error:", 6));
    assert(configuredAdcPermille() == 1500);
    assert(InternalFS.files.at("/pwrmgt_v1") == before_invalid);
  }
  InternalFS.fail_write = true;
  assert(!setConfiguredAdcMultiplier(1.1f, reply, sizeof(reply)));
  assert(strstr(reply, "could not be saved"));
  assert(configuredAdcPermille() == 1500);
  assert(InternalFS.files.at("/pwrmgt_v1") == before_invalid);
  InternalFS.fail_write = false;
  InternalFS.fail_rename = 1;
  assert(!setConfiguredAdcMultiplier(1.1f, reply, sizeof(reply)));
  assert(configuredAdcPermille() == 1500);
  assert(InternalFS.files.at("/pwrmgt_v1") == before_invalid);
  char bounded[4] = {'X', 'X', 'X', 'Z'};
  assert(!setConfiguredAdcMultiplier(NAN, bounded, 3));
  assert(bounded[2] == 0 && bounded[3] == 'Z');
  assert(!setConfiguredAdcMultiplier(1, nullptr, sizeof(reply)));
  assert(!setConfiguredAdcMultiplier(1, reply, 0));
  assert(InternalFS.files.at("/battery_cal") == unused_record);
}
''', encoding="ascii")
            binary = work / "policy"
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                "-Wno-sign-compare", "-Wno-unused-parameter", "-fsanitize=address,undefined",
                "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-DNRF52_PLATFORM", "-DNRF52_POWER_MANAGEMENT",
                "-DBATTERY_CALIBRATION_VOLTAGE_POLICY_ONLY=1",
                "-I" + str(work), "-I" + str(ROOT / "src"),
                str(work / "test.cpp"), "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_roundtrip_corruption_write_failure_reset_and_backup_recovery(self):
        with tempfile.TemporaryDirectory(prefix="battery-calibration-store-") as temp:
            work = Path(temp)
            (work / "helpers").mkdir()
            # Link the real platform policy, including MainBoard's out-of-line
            # virtual methods. Native test boards override sleep, so remove
            # only its ARM-only WFI instruction from a temporary header.
            (work / "Arduino.h").write_text("#pragma once\n", encoding="ascii")
            (work / "target.h").write_text("#pragma once\n#include <MeshCore.h>\n", encoding="ascii")
            core = (ROOT / "src/MeshCore.h").read_text()
            self.assertEqual(core.count('__asm volatile("wfi");'), 1)
            (work / "MeshCore.h").write_text(core.replace('__asm volatile("wfi");', '(void)secs;'))
            mock = ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h"
            mock_text = mock.read_text(encoding="ascii")
            mock_text = mock_text.replace("  bool fail_read_open = false;",
                                          "  bool fail_read_open = false;\n  bool fail_after_publication = false;")
            mock_text = mock_text.replace("    files[to] = files[from]; files.erase(from); return true;",
                "    files[to] = files[from]; files.erase(from);\n"
                "    if (fail_after_publication && !strcmp(to, \"/battery_cal\")) fail_read_open = true;\n"
                "    return true;")
            (work / "helpers/IdentityStore.h").write_text(mock_text, encoding="ascii")
            # Redirect only the hardware boundary include. The transaction and
            # calibration implementation bodies remain production code.
            transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text(encoding="ascii")
            transaction = transaction.replace('#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>')
            transaction = transaction.replace('#include "PersistentStoreFormat.h"', '#include <helpers/PersistentStoreFormat.h>')
            (work / "helpers/ContactFileTransaction.h").write_text(transaction, encoding="ascii")
            store = (ROOT / "src/helpers/BatteryCalibrationStore.cpp").read_text(encoding="ascii")
            for header in ("BatteryCalibrationStore.h", "BatteryCalibration.h", "CheckedSettingsFile.h",
                           "CLICommandUtils.h", "PersistentStoreFormat.h", "AtomicFileWriter.h",
                           "ContactFileTransaction.h", "NRF52VoltagePolicy.h"):
                store = store.replace(f'#include "{header}"', f'#include <helpers/{header}>')
            (work / "store_under_test.cpp").write_text(store, encoding="ascii")
            (work / "test.cpp").write_text(HARNESS, encoding="ascii")
            for platform in ("ESP32_PLATFORM", "RP2040_PLATFORM", "NRF52_PLATFORM", "STM32_PLATFORM"):
                with self.subTest(platform=platform):
                    binary = work / platform
                    built = subprocess.run([
                        os.environ.get("CXX", "g++"), "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                        "-Wno-sign-compare", "-Wno-unused-parameter", "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                        "-fno-pie", "-no-pie", "-D" + platform,
                        "-I" + str(work), "-I" + str(ROOT / "src"),
                        str(work / "test.cpp"), str(ROOT / "src/helpers/PeripheralPinPolicy.cpp"),
                        "-o", str(binary),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
