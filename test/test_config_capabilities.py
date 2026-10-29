"""Execute the bounded settings manifest and the actual role-specific readers."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <helpers/ConfigCapabilities.h>
#include <helpers/RadioProfileCommandUtils.h>
#include <cassert>
#include <cstring>
#include <initializer_list>
using namespace mesh::configcap;
static uint32_t bit(Key key) { return uint32_t(1) << key; }
static void valid(const Manifest& cap) {
  const uint32_t known = (uint32_t(1) << KeyCount) - 1;
  assert(!((cap.get | cap.set | cap.fixed | cap.na) & ~known));
  assert(!(cap.na & (cap.get | cap.set | cap.fixed)));
  assert(!(cap.fixed & cap.set));
}
static Manifest decode(const char* reply) {
  unsigned long get, set, fixed, na;
  int consumed = 0;
  assert(sscanf(reply, "config.cap v1 get=%8lX set=%8lX fixed=%8lX na=%8lX%n",
                &get, &set, &fixed, &na, &consumed) == 4);
  assert(reply[consumed] == 0);
  Manifest cap;
  cap.get=get; cap.set=set; cap.fixed=fixed; cap.na=na;
  valid(cap);
  return cap;
}
struct Board {
  bool watchdog = false, fem_rx = false, fem_tx = false, fixed_pa = false;
  bool canControlLoRaFemLna() const { return fem_rx; }
  bool canControlLoRaFemPaGain() const { return fem_tx; }
  bool isLoRaFemPaGainEnabled() const { return fixed_pa; }
  bool getSystemWatchdogRunning(bool& running) const {
    running = true;
    return watchdog;
  }
};
struct Radio {
  bool rf_rx = false;
  bool supportsRxPowerSavingRfRxDisable() const { return rf_rx; }
};
struct UI {
  bool rotation = false;
  bool supportsDisplayRotation() const { return rotation; }
};
struct Profiles {
  uint16_t saved = 0;
  uint16_t primaryPreamble() const { return saved; }
};
struct Prefs { uint16_t primary_radio_preamble = 0; };
struct CommonCLI {
  Board board;
  Board* _board = &board;
  Radio radio;
  Radio* _callbacks = &radio;
  Prefs prefs;
  Prefs* _prefs = &prefs;
  void handleGetCmd(char* command, char* reply) {
    const char* config = command + 4;
    @COMMON@
    @COMMON_PREAMBLE@
    strcpy(reply, "unsupported");
  }
};
struct MyMesh {
  Board board;
  Radio radio_driver;
  UI ui;
  UI* _ui = nullptr;
  Profiles _radio_profiles;
  bool handleCommand(const char* command, uint32_t sender_timestamp, char* reply);
  bool handleLocalControlCommand(const char* command, char* reply, size_t reply_size) {
    @COMPANION@
    @COMPANION_PREAMBLE@
    return false;
  }
};
@COMPANION_ENTRY@
static void check(const Manifest& cap, Key key, bool get, bool set,
                  bool fixed, bool na) {
  assert(bool(cap.get & bit(key)) == get);
  assert(bool(cap.set & bit(key)) == set);
  assert(bool(cap.fixed & bit(key)) == fixed);
  assert(bool(cap.na & bit(key)) == na);
}
int main() {
  static_assert(SystemWatchdog == 0 && RadioCad == 1 && RadioFemRxGain == 2
      && RadioFemTxGain == 3 && RadioRxpsRfRxDisabled == 4 && Bluetooth == 5
      && BluetoothName == 6 && DisplayRotation == 7 && UsbLoggingAlways == 8
      && UsbLogging == 9 && UsbDebug == 10 && RadioPreamble == 11
      && KeyCount == 12, "v1 wire bits changed");
  Features features;
  char reply[160] = {};
  assert(handleCommand("get config.cap", reply, sizeof(reply), features));
  assert(!strcmp(reply, "config.cap v1 get=00000800 set=00000800 fixed=00000002 na=000007FD"));
  const char expected[] = "config.cap v1 get=00000800 set=00000800 fixed=00000002 na=000007FD";
  assert(sizeof(expected) < 160);
  for (const char* command : std::initializer_list<const char*>{nullptr, "", "get config.cap extra",
       "get config.cap ", "get config.cap\n", "get CONFIG.CAP", "set config.cap"}) {
    strcpy(reply, "unchanged");
    assert(!handleCommand(command, reply, sizeof(reply), features));
    assert(!strcmp(reply, "unchanged"));
  }
  assert(!handleCommand("get config.cap", nullptr, sizeof(reply), features));
  assert(!handleCommand("get config.cap", reply, 0, features));
  assert(!strcmp(reply, "unchanged"));
  for (size_t capacity = 1; capacity <= sizeof(expected); ++capacity) {
    unsigned char guarded[160];
    memset(guarded, 0xA5, sizeof(guarded));
    assert(handleCommand("get config.cap", reinterpret_cast<char*>(guarded + 1), capacity, features));
    assert(guarded[0] == 0xA5 && guarded[capacity + 1] == 0xA5);
    assert(guarded[capacity] == 0);
    assert(!strncmp(reinterpret_cast<char*>(guarded + 1), expected, capacity - 1));
  }
  // Exercise every combination, including contradictory SDK inputs: the wire
  // result must never classify a setting as both mutable and not applicable.
  for (unsigned flags = 0; flags < (1u << 12); ++flags) {
    features.watchdog_read = flags & 1;
    features.watchdog_write = flags & 2;
    features.cad_timings = flags & 4;
    features.fem_rxgain = flags & 8;
    features.fem_txgain = flags & 16;
    features.fem_txgain_fixed = flags & 32;
    features.rxps_rfrx_disabled = flags & 64;
    features.bluetooth = flags & 128;
    features.bluetooth_name = flags & 256;
    features.display_rotation = flags & 512;
    features.usb_logging = flags & 1024;
    features.usb_logging_always = flags & 2048;
    valid(describe(features));
  }
  CommonCLI common;
  MyMesh companion;
  char cap_command[] = "get config.cap";
  assert(companion.handleCommand("xy| \tget config.cap", 0, reply));
  assert(!strncmp(reply, "xy|config.cap v1 ", 17));
  assert(companion.handleCommand("xy|get radio.preamble", 0, reply));
  assert(!strcmp(reply, "xy|> auto"));
  for (unsigned flags = 0; flags < 64; ++flags) {
    common.board.fem_rx = companion.board.fem_rx = flags & 1;
    common.board.fem_tx = companion.board.fem_tx = flags & 2;
    common.radio.rf_rx = companion.radio_driver.rf_rx = flags & 4;
    companion.board.fixed_pa = flags & 8;
    companion.board.watchdog = flags & 16;
    companion.ui.rotation = flags & 32;
    companion._ui = &companion.ui;
    common.handleGetCmd(cap_command, reply);
    Manifest cap = decode(reply);
#if defined(NRF52_PLATFORM)
    check(cap, SystemWatchdog, true, true, false, false);
#else
    check(cap, SystemWatchdog, false, false, false, true);
#endif
    check(cap, RadioCad, false, false, true, false);
    check(cap, RadioFemRxGain, flags & 1, flags & 1, false, !(flags & 1));
    check(cap, RadioFemTxGain, flags & 2, flags & 2, false, !(flags & 2));
    check(cap, RadioRxpsRfRxDisabled, flags & 4, flags & 4, false, !(flags & 4));
    check(cap, Bluetooth, false, false, false, true);
    check(cap, BluetoothName, false, false, false, true);
#if defined(HELTEC_V4_R8_TFT) && defined(HELTEC_TFT_RUNTIME_ROTATION) && HELTEC_TFT_RUNTIME_ROTATION == 1
    check(cap, DisplayRotation, true, true, false, false);
#else
    check(cap, DisplayRotation, false, false, false, true);
#endif
    assert(companion.handleLocalControlCommand(cap_command, reply, sizeof(reply)));
    cap = decode(reply);
    check(cap, SystemWatchdog, flags & 16, false, flags & 16, !(flags & 16));
    check(cap, RadioCad, true, true, false, false);
    check(cap, RadioFemRxGain, flags & 1, flags & 1, false, !(flags & 1));
    const bool pa_fixed = !(flags & 2) && (flags & 8);
    check(cap, RadioFemTxGain, (flags & 2) || pa_fixed, flags & 2, pa_fixed,
          !(flags & 2) && !pa_fixed);
    check(cap, RadioRxpsRfRxDisabled, flags & 4, flags & 4, false, !(flags & 4));
#if defined(BLE_PIN_CODE)
    check(cap, Bluetooth, true, true, false, false);
    check(cap, BluetoothName, true, true, false, false);
#else
    check(cap, Bluetooth, false, false, false, true);
    check(cap, BluetoothName, false, false, false, true);
#endif
    check(cap, DisplayRotation, flags & 32, flags & 32, false, !(flags & 32));
    for (bool is_common : {true, false}) {
      if (is_common) common.handleGetCmd(cap_command, reply);
      else assert(companion.handleLocalControlCommand(cap_command, reply, sizeof(reply)));
      cap = decode(reply);
#if MESH_USB_LOGGING_AVAILABLE
      check(cap, UsbLogging, true, true, false, false);
      check(cap, UsbDebug, true, true, false, false);
#if !defined(MESH_DUAL_CDC_LOGGING)
      check(cap, UsbLoggingAlways, true, true, false, false);
#else
      check(cap, UsbLoggingAlways, false, false, false, true);
#endif
#else
      check(cap, UsbLogging, false, false, false, true);
      check(cap, UsbDebug, false, false, false, true);
      check(cap, UsbLoggingAlways, false, false, false, true);
#endif
      check(cap, RadioPreamble, true, true, false, false);
    }
  }
  companion._ui = nullptr;
  assert(companion.handleLocalControlCommand(cap_command, reply, sizeof(reply)));
  check(decode(reply), DisplayRotation, false, false, false, true);
  char preamble_command[] = "get radio.preamble";
  for (uint16_t preamble : {0, 8, 32, 88, 65528}) {
    common.prefs.primary_radio_preamble = companion._radio_profiles.saved = preamble;
    common.handleGetCmd(preamble_command, reply);
    char actual[160];
    assert(companion.handleLocalControlCommand(preamble_command, actual, sizeof(actual)));
    assert(!strcmp(actual, reply));
    if (!preamble) assert(!strcmp(actual, "> auto"));
    else { char expected_preamble[16]; snprintf(expected_preamble, sizeof(expected_preamble), "> %u", (unsigned)preamble);
      assert(!strcmp(actual, expected_preamble)); }
    assert(common.prefs.primary_radio_preamble == preamble);
    assert(companion._radio_profiles.saved == preamble);
    uint16_t parsed = 123;
    assert(mesh::cli::parseRadioPreamble(actual + 2, parsed));
    assert(parsed == preamble);
  }
  for (const char* invalid : {"1", "4", "7", "65529", "65535", "65536", "-1"}) {
    uint16_t parsed = 123;
    assert(!mesh::cli::parseRadioPreamble(invalid, parsed));
  }
  formatPrimaryPreamble(32, nullptr, sizeof(reply));
  strcpy(reply, "unchanged");
  formatPrimaryPreamble(32, reply, 0);
  assert(!strcmp(reply, "unchanged"));
}
'''


class ConfigCapabilitiesTests(unittest.TestCase):
    def test_compiled_role_capabilities_and_saved_preamble(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("a host C++11 compiler is required")
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        common_get = body(common, "void CommonCLI::handleGetCmd(")
        companion_control = body(companion, "bool MyMesh::handleLocalControlCommand(")
        source = HARNESS.replace("@COMMON@", body(common_get, "if (mesh::configcap::isCommand(command))"))
        source = source.replace("@COMMON_PREAMBLE@", body(common_get, 'if (strcmp(config, "radio.preamble") == 0)'))
        source = source.replace("@COMPANION@", body(companion_control, "if (mesh::configcap::isCommand(command))"))
        source = source.replace("@COMPANION_PREAMBLE@", body(companion_control, 'if (strcmp(command, "get radio.preamble") == 0)'))
        # Retain the production protocol-14 request prefix and capacity logic;
        # omit unrelated radio handlers after its first ESP32-specific block.
        companion_entry = body(companion, "bool MyMesh::handleCommand(")
        companion_entry = companion_entry[:companion_entry.index("#if defined(ESP32_PLATFORM)")]
        companion_entry = companion_entry.replace("{", "{ (void)sender_timestamp;", 1)
        companion_entry += "return handleLocalControlCommand(command, reply, reply_capacity);\n}"
        source = source.replace("@COMPANION_ENTRY@", companion_entry)
        self.assertIn("handleLocalControlCommand(command, reply, reply_capacity)",
                      body(companion, "bool MyMesh::handleCommand("))
        self.assertIn('memcmp(command, "get ", 4)', body(common, "void CommonCLI::handleCommand("))
        configurations = (
            [],
            ["NRF52_PLATFORM", "MESH_USB_LOGGING_AVAILABLE=1", "BLE_PIN_CODE=123456"],
            ["NRF52_PLATFORM", "MESH_USB_LOGGING_AVAILABLE=1", "MESH_DUAL_CDC_LOGGING", "BLE_PIN_CODE=123456"],
            ["ESP32_PLATFORM", "MESH_USB_LOGGING_AVAILABLE=1", "BLE_PIN_CODE=123456"],
            ["ESP32_PLATFORM", "MESH_USB_LOGGING_AVAILABLE=1", "HELTEC_V4_R8_TFT", "HELTEC_TFT_RUNTIME_ROTATION=1"],
            ["ESP32_PLATFORM", "MESH_USB_LOGGING_AVAILABLE=0", "HELTEC_V4_R8_TFT", "HELTEC_TFT_RUNTIME_ROTATION=0"],
        )
        with tempfile.TemporaryDirectory(prefix="meshcore-config-cap-") as directory:
            path = Path(directory) / "config_cap.cpp"
            path.write_text(source, encoding="ascii")
            for language in ("c++11", "c++17"):
                for index, flags in enumerate(configurations):
                    with self.subTest(language=language, flags=flags):
                        binary = Path(directory) / ("config_cap_" + language + "_" + str(index) + ".exe")
                        built = subprocess.run([compiler, "-std=" + language, "-Wall", "-Wextra", "-Werror",
                            *([] if os.name == "nt" else ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]),
                            *["-D" + flag for flag in flags], "-I" + str(ROOT / "src"),
                            str(path), "-o", str(binary)], capture_output=True, text=True, timeout=60)
                        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
                        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
