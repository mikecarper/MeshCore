"""Exercise actual infrastructure RX setters, checked saves, and pending service."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def preprocess(source, defines=("USE_LR2021",)):
    return subprocess.run([os.environ.get("CXX", "g++"), "-E", "-P", "-x", "c++",
                           *("-D" + value for value in defines), "-"], input=source, text=True,
                          capture_output=True, check=True).stdout


def fixture(defines=("USE_LR2021",)):
    source = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
    header = (ROOT / "src/helpers/CommonCLI.h").read_text(encoding="ascii")
    methods = "\n".join(extract_braced(source, signature) for signature in (
        "mesh::RadioParamApplyResult CommonCLI::applyRxSetting(",
        "void CommonCLI::servicePendingRxSettings(", "bool CommonCLI::saveRxSetting(",
        "void CommonCLI::restoreRxSetting(", "bool CommonCLI::trySavePrefs(",
        "void CommonCLI::loop(", "bool CommonCLI::calculateRxPowerSavingLevel("))
    setter = preprocess(extract_braced(source, "void CommonCLI::handleSetCmd("), defines)
    branches = [extract_braced(setter, signature) for signature in (
        'if (memcmp(config, "radio.rxgain ", 13) == 0)',
        'if (memcmp(config, "radio.rxps ", 11) == 0)',
        'if (strncmp(config, "radio.rxps.rfrx_disabled ", 25) == 0)',
        'if (strcmp(config, "extra.sf") == 0 || memcmp(config, "extra.sf ", 9) == 0)')]
    values = {
        "METHODS": preprocess(methods, defines),
        "SETTERS": preprocess(" else ".join(branches), defines),
        "ENUM": extract_braced(header, "enum RxSetting : uint8_t") + ";",
        "PENDING": extract_braced(header, "bool hasPendingRadioSettings() const"),
        "NOTE": preprocess(extract_braced(source, "static void appendRxPowerSavingAdjustmentNote("), defines),
    }
    result = HARNESS
    for key, value in values.items():
        result = result.replace("@" + key + "@", value)
    return result


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <deque>
#include <initializer_list>
#include <helpers/CLICommandUtils.h>
#include <helpers/PrefsSaveRouting.h>
#include <helpers/radiolib/RXPowerSaving.h>
#include <helpers/radiolib/LR2021SideDetectorConfig.h>
#define MIN_LOCAL_ADVERT_INTERVAL 60
static uint32_t now_ms = 1000;
uint32_t millis() { return now_ms; }
namespace mesh {
enum class RadioParamApplyResult : uint8_t { APPLIED, BUSY, FAILED };
struct Radio {
  bool carrier = false, morse = false;
  struct Profiles {
    bool dual = false;
    struct { uint8_t sf = 7; float bw = 125; } primary;
    bool enabled() const { return dual; }
  } profile_state;
  const Profiles* profiles() const { return &profile_state; }
  bool isCarrierWaveActive() const { return carrier; }
};
bool morseTimingActive(const Radio* radio) { return radio && radio->morse; }
}
using Result = mesh::RadioParamApplyResult;
struct Prefs {
  uint8_t rx_boosted_gain = 0, rx_powersaving_enabled = 0;
  uint32_t rx_ps_rx_us = RX_POWERSAVING_DEFAULT_RX_US;
  uint32_t rx_ps_sleep_us = RX_POWERSAVING_DEFAULT_SLEEP_US;
  uint8_t rx_ps_level = 0, rx_ps_preamble = 0, sf = 7, advert_interval = 0;
  float bw = 125;
  uint8_t extra_sf[4] = {};
  void clearDirty() {}
};
using NodePrefs = Prefs;
class CommonCLI;
struct Callbacks {
  CommonCLI* owner = nullptr;
  mesh::Radio radio;
  bool gain_supported = true, ps_supported = true, rf_supported = true;
  bool transmitting = false, save_ok = true, acknowledge = true;
  bool hardware_gain = false, hardware_rf = false;
  RxPowerSavingConfig hardware_ps;
  uint8_t hardware_sf[4] = {};
  Prefs persisted;
  unsigned saves = 0, gain_attempts = 0, ps_attempts = 0, sf_attempts = 0, rf_attempts = 0;
  std::deque<Result> gain_results, ps_results, sf_results, rf_results;
  Result admission(std::deque<Result>& results) {
    if (transmitting) return Result::BUSY;
    if (results.empty()) return Result::APPLIED;
    Result result = results.front(); results.pop_front(); return result;
  }
  bool supportsRxBoostedGain() const { return gain_supported; }
  bool supportsRxPowerSaving() const { return ps_supported; }
  bool supportsRxPowerSavingRfRxDisable() const { return rf_supported; }
  bool isRxPowerSavingRfRxDisabled() const { return hardware_rf; }
  Result trySetRxBoostedGain(bool enabled) {
    ++gain_attempts; const Result result = admission(gain_results);
    if (result != Result::BUSY) hardware_gain = enabled; // FAILED can be a partial write.
    return result;
  }
  Result trySetRxPowerSaving(bool enabled, uint32_t rx, uint32_t sleep) {
    ++ps_attempts; const Result result = admission(ps_results);
    if (result != Result::BUSY) hardware_ps = RxPowerSavingConfig(enabled, rx, sleep, 0, 0);
    return result;
  }
  Result tryConfigSideDetectors(const uint8_t* sfs, uint8_t count, float) {
    ++sf_attempts; const Result result = admission(sf_results);
    if (result != Result::BUSY) {
      memset(hardware_sf, 0, sizeof(hardware_sf)); memcpy(hardware_sf, sfs, count);
    }
    return result;
  }
  Result trySetRxPowerSavingRfRxDisabled(bool disabled) {
    ++rf_attempts; const Result result = admission(rf_results);
    if (result != Result::BUSY) hardware_rf = disabled;
    return result;
  }
  mesh::Radio* getProfileRadio() { return &radio; }
  void updateAdvertTimer() {}
  void savePrefs(PrefsSaveRouting::Scope);
};
@NOTE@
class CommonCLI {
public:
  @ENUM@
  Prefs prefs;
  Callbacks callbacks;
  Prefs* _prefs = &prefs;
  Callbacks* _callbacks = &callbacks;
  uint8_t _rx_settings_pending = 0;
  uint32_t _rx_settings_retry_at = 0;
  uint32_t _primary_radio_apply_at = 0;
  bool _tx_power_pending = false, _common_save_result_known = false, _common_save_succeeded = false;
  struct Profiles {
    bool pending = false;
    void loop() {}
    bool hasPendingTuning() const { return pending; }
  } _radio_profiles;
  struct Presets { void loop(unsigned long) {} } _radio_presets;
  CommonCLI() { callbacks.owner = this; }
  @PENDING@
  void loopManagement() {}
  void servicePendingTxPower() {}
  void loop();
  void recoverMediaMeshRegions() {}
  Result applyRxSetting(uint8_t);
  void servicePendingRxSettings();
  bool saveRxSetting(uint8_t, Result, char*);
  void restoreRxSetting(uint8_t, Result, uint8_t, uint32_t);
  bool trySavePrefs(PrefsSaveRouting::Scope scope = PrefsSaveRouting::Scope::Common);
  bool calculateRxPowerSavingLevel(uint32_t, uint8_t, float, uint32_t, uint32_t*, uint32_t*);
  void command(const char* input, char* reply) {
    char buffer[160] = {};
    strncpy(buffer, input, sizeof(buffer) - 1);
    const char* config = buffer;
    @SETTERS@
    else strcpy(reply, "unknown config");
  }
};
void Callbacks::savePrefs(PrefsSaveRouting::Scope scope) {
  assert(scope == PrefsSaveRouting::Scope::Common); ++saves;
  if (acknowledge) {
    owner->_common_save_result_known = true;
    owner->_common_save_succeeded = save_ok;
  }
  if (save_ok) persisted = owner->prefs;
}
@METHODS@
static bool ok(const char* reply) { return strncmp(reply, "OK", 2) == 0; }
int main() {
  char reply[160];
  {
    CommonCLI cli;
    for (const char* invalid : {"radio.rxgain banana", "radio.rxgain onjunk", "radio.rxgain off "}) {
      cli.command(invalid, reply); assert(!ok(reply));
    }
    cli.callbacks.gain_supported = false;
    cli.command("radio.rxgain on", reply);
    assert(!ok(reply) && !cli.callbacks.gain_attempts && !cli.callbacks.saves && !cli.hasPendingRadioSettings());
    cli.callbacks.ps_supported = false;
    cli.prefs.rx_ps_rx_us = 0; cli.prefs.rx_ps_sleep_us = 0;
    for (const char* value : {"radio.rxps banana", "radio.rxps 0 3000", "radio.rxps on"}) {
      cli.command(value, reply); assert(!ok(reply));
      assert(!cli.prefs.rx_ps_rx_us && !cli.prefs.rx_ps_sleep_us);
    }
    assert(!cli.callbacks.ps_attempts && !cli.callbacks.saves);
    cli.command("extra.sf 99", reply);
    assert(!ok(reply) && !cli.callbacks.sf_attempts && !cli.callbacks.saves);
    cli.callbacks.radio.profile_state.dual = true;
    cli.callbacks.transmitting = true;
    cli.command("extra.sf 8", reply);
    assert(!ok(reply) && !cli.callbacks.sf_attempts && !cli.callbacks.saves
        && !cli.hasPendingRadioSettings());
    cli.callbacks.radio.profile_state.dual = false;
    cli.callbacks.radio.profile_state.primary.sf = 8;
    cli.command("extra.sf 8", reply);
    assert(!ok(reply) && !cli.callbacks.sf_attempts && !cli.hasPendingRadioSettings());
    cli.callbacks.rf_supported = false;
    cli.command("radio.rxps.rfrx_disabled on", reply);
    assert(!ok(reply) && !cli.callbacks.rf_attempts && !cli.hasPendingRadioSettings());
  }
  {
    CommonCLI cli; cli.callbacks.transmitting = true;
    cli.command("radio.rxgain on", reply);
    assert(ok(reply) && strstr(reply, "pending") && cli.hasPendingRadioSettings());
    assert(cli.prefs.rx_boosted_gain && cli.callbacks.persisted.rx_boosted_gain);
    cli.command("radio.rxps 2000 3000", reply);
    assert(ok(reply) && strstr(reply, "pending"));
    cli.command("extra.sf 8,9", reply);
    assert(ok(reply) && strstr(reply, "pending"));
    assert(cli.callbacks.saves == 3);
    cli.command("radio.rxps.rfrx_disabled on", reply);
    assert(ok(reply) && strstr(reply, "pending") && cli.callbacks.saves == 3);
    cli.loop(); assert(!cli.callbacks.hardware_gain && !cli.callbacks.hardware_ps.enabled);
    cli.callbacks.transmitting = false;
    cli.loop();
    assert(!cli.hasPendingRadioSettings() && cli.callbacks.hardware_gain);
    assert(cli.callbacks.hardware_ps.enabled && cli.callbacks.hardware_ps.rx_us == 2000);
    assert(cli.callbacks.hardware_sf[0] == 8 && cli.callbacks.hardware_sf[1] == 9);
    assert(cli.callbacks.hardware_rf && cli.callbacks.saves == 3);
    cli.loop(); assert(cli.callbacks.saves == 3);
    cli._radio_profiles.pending = true; assert(cli.hasPendingRadioSettings());
  }
  {
    CommonCLI cli; cli.callbacks.transmitting = true;
    cli.command("radio.rxgain on", reply);
    cli.command("radio.rxgain off", reply);
    cli.command("radio.rxps 2000 3000", reply);
    cli.command("radio.rxps 4000 5000", reply);
    cli.command("extra.sf 8", reply); cli.command("extra.sf 9", reply);
    cli.command("radio.rxps.rfrx_disabled on", reply);
    cli.command("radio.rxps.rfrx_disabled off", reply);
    cli.callbacks.transmitting = false; cli.servicePendingRxSettings();
    assert(!cli.hasPendingRadioSettings() && !cli.callbacks.hardware_gain && !cli.callbacks.hardware_rf);
    assert(cli.callbacks.hardware_ps.rx_us == 4000 && cli.callbacks.hardware_sf[0] == 9);
    assert(cli.callbacks.saves == 6 && cli.callbacks.persisted.rx_ps_rx_us == 4000);
  }
  {
    CommonCLI cli;
    cli.callbacks.save_ok = false;
    cli.command("radio.rxgain on", reply);
    assert(!ok(reply) && !cli.prefs.rx_boosted_gain && !cli.callbacks.hardware_gain);
    cli.command("radio.rxps 2000 3000", reply);
    assert(!ok(reply) && !cli.prefs.rx_powersaving_enabled && !cli.callbacks.hardware_ps.enabled);
    assert(cli.prefs.rx_ps_rx_us == RX_POWERSAVING_DEFAULT_RX_US && cli.prefs.rx_ps_level == 0);
    cli.command("extra.sf 8", reply);
    assert(!ok(reply) && !cli.prefs.extra_sf[0] && !cli.callbacks.hardware_sf[0]);
    assert(!cli.hasPendingRadioSettings());
    cli.callbacks.save_ok = true; cli.callbacks.acknowledge = false;
    cli.command("radio.rxgain on", reply);
    assert(!ok(reply) && !cli.prefs.rx_boosted_gain && !cli.callbacks.hardware_gain);
  }
  {
    CommonCLI cli; cli.callbacks.transmitting = true;
    cli.command("radio.rxgain on", reply);
    cli.callbacks.save_ok = false;
    cli.command("radio.rxgain off", reply);
    assert(!ok(reply) && cli.prefs.rx_boosted_gain && cli.hasPendingRadioSettings());
    cli.callbacks.transmitting = false; cli.servicePendingRxSettings();
    assert(cli.callbacks.hardware_gain && !cli.hasPendingRadioSettings() && cli.callbacks.saves == 2);
  }
  {
    CommonCLI cli; cli.callbacks.gain_results = {Result::APPLIED, Result::BUSY};
    cli.callbacks.save_ok = false;
    cli.command("radio.rxgain on", reply);
    assert(!ok(reply) && !cli.prefs.rx_boosted_gain && cli.hasPendingRadioSettings());
    assert(cli.callbacks.hardware_gain);
    cli.servicePendingRxSettings();
    assert(!cli.callbacks.hardware_gain && !cli.hasPendingRadioSettings());
    cli.callbacks.gain_results = {Result::FAILED, Result::FAILED};
    cli.command("radio.rxgain on", reply);
    assert(!ok(reply) && !cli.prefs.rx_boosted_gain && cli.hasPendingRadioSettings());
    const unsigned attempts = cli.callbacks.gain_attempts;
    cli.servicePendingRxSettings(); assert(cli.callbacks.gain_attempts == attempts);
    now_ms += 1000; cli.servicePendingRxSettings();
    assert(!cli.hasPendingRadioSettings() && !cli.callbacks.hardware_gain);
  }
  {
    CommonCLI cli; cli.callbacks.radio.morse = true;
    cli.command("radio.rxgain on", reply);
    cli.command("radio.rxps 2000 3000", reply);
    cli.command("extra.sf 8", reply);
    cli.command("radio.rxps.rfrx_disabled on", reply);
    cli.servicePendingRxSettings(); cli.loop();
    assert(!cli.callbacks.gain_attempts && !cli.callbacks.ps_attempts
        && !cli.callbacks.sf_attempts && !cli.callbacks.rf_attempts);
    cli.callbacks.radio.morse = false; cli.callbacks.radio.carrier = true;
    cli.servicePendingRxSettings();
    assert(!cli.callbacks.gain_attempts && !cli.callbacks.ps_attempts);
    cli.callbacks.radio.carrier = false; cli.servicePendingRxSettings();
    assert(!cli.hasPendingRadioSettings() && cli.callbacks.hardware_gain && cli.callbacks.hardware_rf);
    assert(cli.callbacks.saves == 3);
  }
  {
    CommonCLI cli;
    cli.command("radio.rxps level 8 preamble 32", reply);
    assert(ok(reply) && cli.prefs.rx_ps_level == 8 && cli.prefs.rx_ps_preamble == 32);
    const auto old_rx = cli.prefs.rx_ps_rx_us;
    cli.callbacks.save_ok = false;
    cli.command("radio.rxps 2000 3000", reply);
    assert(!ok(reply) && cli.prefs.rx_ps_level == 8 && cli.prefs.rx_ps_preamble == 32);
    assert(cli.prefs.rx_ps_rx_us == old_rx && cli.callbacks.hardware_ps.rx_us == old_rx);
  }
}
'''


class InfrastructureRxSettingsTests(unittest.TestCase):
    def compile_run(self, code):
        with tempfile.TemporaryDirectory(prefix="mesh-infra-rx-") as directory:
            source, exe = Path(directory) / "test.cpp", Path(directory) / "test"
            source.write_text(code, encoding="ascii")
            built = subprocess.run([
                os.environ.get("CXX", "g++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-I", str(ROOT / "src"), str(source), "-o", str(exe),
            ], text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(exe)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_commands_and_pending_transactions(self):
        self.compile_run(fixture())

    def test_integrated_stm32_preserves_unsupported_errors_and_off_transactions(self):
        code = fixture(("STM32_PLATFORM", "STM32WLxx", "USE_STM32WL")).split("static bool ok(", 1)[0]
        code += r'''
int main() {
  char reply[160];
  CommonCLI cli;
  cli.callbacks.gain_supported = cli.callbacks.ps_supported = cli.callbacks.rf_supported = false;
  for (const char* value : {"radio.rxgain on", "radio.rxgain off",
                           "radio.rxps.rfrx_disabled on", "radio.rxps.rfrx_disabled off"}) {
    cli.command(value, reply); assert(!strcmp(reply, "Error: unsupported"));
  }
  for (const char* value : {"radio.rxgain banana", "radio.rxgain on ",
                           "radio.rxps.rfrx_disabled banana", "radio.rxps.rfrx_disabled off "}) {
    cli.command(value, reply); assert(!strcmp(reply, "Error: state must be on or off"));
  }
  for (const char* value : {"radio.rxps on", "radio.rxps conservative", "radio.rxps balanced",
                           "radio.rxps level 8 preamble 32", "radio.rxps 2000 3000"}) {
    cli.command(value, reply); assert(!strcmp(reply, "ERROR: RX powersaving unsupported"));
  }
  cli.command("radio.rxps banana", reply); assert(strstr(reply, "ERROR: use off|on|"));
  assert(!cli.callbacks.gain_attempts && !cli.callbacks.rf_attempts
      && !cli.callbacks.ps_attempts && !cli.callbacks.saves && !cli.hasPendingRadioSettings());
  // Off retains accepted timing/level intent, checked persistence and safe-gap
  // application even though this integrated driver cannot enable duty cycling.
  cli.prefs.rx_powersaving_enabled = 1; cli.prefs.rx_ps_rx_us = 2000;
  cli.prefs.rx_ps_sleep_us = 3000; cli.prefs.rx_ps_level = 8; cli.prefs.rx_ps_preamble = 32;
  cli.callbacks.hardware_ps = RxPowerSavingConfig(true, 2000, 3000, 8, 32);
  cli.callbacks.transmitting = true;
  cli.command("radio.rxps off", reply);
  assert(!strcmp(reply, "OK - off,2000,3000; saved; pending"));
  assert(!cli.prefs.rx_powersaving_enabled && cli.prefs.rx_ps_level == 8
      && cli.prefs.rx_ps_preamble == 32 && cli.callbacks.saves == 1);
  assert(!cli.callbacks.persisted.rx_powersaving_enabled && cli.callbacks.hardware_ps.enabled);
  assert(cli.hasPendingRadioSettings() && cli._rx_settings_pending == CommonCLI::RxPowerSaving);
  cli.callbacks.transmitting = false; cli.servicePendingRxSettings();
  assert(!cli.hasPendingRadioSettings() && !cli.callbacks.hardware_ps.enabled);
  assert(cli.callbacks.ps_attempts == 2 && cli.callbacks.saves == 1);
  assert(!cli.callbacks.gain_attempts && !cli.callbacks.rf_attempts && !cli.callbacks.sf_attempts);
  // A failed save restores the previous accepted values and pending deadline.
  cli.callbacks.save_ok = false; cli.callbacks.transmitting = true;
  cli._rx_settings_pending = CommonCLI::RxPowerSaving; cli._rx_settings_retry_at = 1234;
  cli.command("radio.rxps off", reply);
  assert(!strcmp(reply, "Error: RX save unconfirmed; reboot may differ"));
  assert(cli._rx_settings_pending == CommonCLI::RxPowerSaving && cli._rx_settings_retry_at == 1234);
  assert(cli.prefs.rx_ps_rx_us == 2000 && cli.prefs.rx_ps_sleep_us == 3000
      && cli.prefs.rx_ps_level == 8 && cli.prefs.rx_ps_preamble == 32);
  cli.callbacks.transmitting = false; now_ms = 1234; cli.servicePendingRxSettings();
  assert(!cli.hasPendingRadioSettings());
  cli.callbacks.save_ok = true;
  cli.callbacks.ps_results = {Result::FAILED, Result::APPLIED};
  const unsigned saves = cli.callbacks.saves;
  cli.command("radio.rxps off", reply);
  assert(!strcmp(reply, "Error: RX setting rejected") && cli.callbacks.saves == saves);
  assert(!cli.hasPendingRadioSettings() && !cli.callbacks.hardware_ps.enabled);
  // The existing Off path still normalizes absent timing values locally.
  cli.prefs.rx_ps_rx_us = cli.prefs.rx_ps_sleep_us = 0;
  cli.command("radio.rxps off", reply);
  assert(!strncmp(reply, "OK - off,", 9) && cli.prefs.rx_ps_rx_us == RX_POWERSAVING_DEFAULT_RX_US
      && cli.prefs.rx_ps_sleep_us == RX_POWERSAVING_DEFAULT_SLEEP_US);
}
'''
        self.compile_run(code)

    def test_other_platforms_and_external_radio_keep_supported_runtime_controls(self):
        for defines in (("STM32_PLATFORM",), ("STM32WLxx",),
                        ("STM32_PLATFORM", "STM32WLxx")):
            with self.subTest(defines=defines):
                code = fixture(defines).split("static bool ok(", 1)[0]
                code += r'''
int main() {
  CommonCLI cli; char reply[160];
  cli.command("radio.rxgain on", reply);
  assert(!strcmp(reply, "OK") && cli.callbacks.hardware_gain && cli.callbacks.saves == 1);
  cli.command("radio.rxps 2000 3000", reply);
  assert(!strncmp(reply, "OK - on,", 8) && cli.callbacks.hardware_ps.enabled && cli.callbacks.saves == 2);
  cli.command("radio.rxps.rfrx_disabled on", reply);
  assert(strstr(reply, "OK - radio.rxps.rfrx_disabled on") && cli.callbacks.hardware_rf);
}
'''
                self.compile_run(code)

    def test_stm32_fold_matches_the_integrated_driver_capabilities(self):
        driver = (ROOT / "src/helpers/radiolib/CustomSTM32WLxWrapper.h").read_text(encoding="ascii")
        self.assertNotRegex(driver, r"\b(?:supportsRxBoostedGainMode|supportsRxPowerSavingRfRxDisable|supportsRxPowerSaving)\s*\(")
        self.assertNotRegex(driver, r"\bSupportsRxPowerSaving\b")
        self.assertIn("#define USE_STM32WL", driver)

    def test_pending_radio_settings_keep_all_infrastructure_roles_awake(self):
        for role, name in (("simple_repeater", "MyMesh"), ("simple_room_server", "MyMesh"),
                           ("simple_sensor", "SensorMesh")):
            with self.subTest(role=role):
                source = (ROOT / "examples" / role / (name + ".cpp")).read_text(encoding="ascii")
                header = (ROOT / "examples" / role / (name + ".h")).read_text(encoding="ascii")
                self.assertIn("_cli.hasPendingRadioSettings()", extract_braced(
                    source, f"mesh::powerdiag::Mask {name}::pendingWorkReason() const"))
                self.assertIn("!_cli.hasPendingRadioSettings()", extract_braced(
                    header, "bool canRecoverUsbLogging() const"))
        tracker = (ROOT / "examples/simple_sensor/Tracker.cpp").read_text(encoding="ascii")
        self.assertIn("_cli.hasPendingRadioSettings()", extract_braced(
            tracker, "bool SensorMesh::trackerNeedsRadio() const"))


if __name__ == "__main__":
    unittest.main()
