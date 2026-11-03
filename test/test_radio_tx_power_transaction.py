"""Execute real TX-power admission, retry and unchanged-value behavior."""
from pathlib import Path
import re
import subprocess
import sys
import unittest

from test_radio_interrupt_recovery import compile_run
from test_radio_receive_contract import method
from test_onboard_qspi_profiles import effective_defines
from platformio.project.config import ProjectConfig


ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <helpers/RadioDirectionPolicy.h>
#include <initializer_list>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define RADIOLIB_ERR_NONE 0
#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
using Result = mesh::RadioParamApplyResult;
struct Chip {
  int standby_result = 0, power_result = 0, direct_result = 0;
  int8_t power = 17;
  bool write_before_failure = false;
  unsigned standbys = 0, writes = 0, keys = 0;
  int standby() { ++standbys; return standby_result; }
  int setOutputPower(int8_t requested) {
    ++writes;
    if (!power_result || write_before_failure) power = requested;
    return power_result;
  }
  int transmitDirect() { ++keys; return direct_result; }
  int checkOutputPower(int8_t requested, int8_t*) const {
    return requested >= -9 && requested <= 22 ? 0 : -1;
  }
};
struct RadioLibWrapper {
  Chip chip;
  Chip* _radio = &chip;
  uint8_t state = STATE_RX;
  int8_t _cur_dbm = 17;
  float _cur_freq = 915;
  bool _dbm_valid = true, _dbm_applied = true, _profile_repair_required = false;
  bool _profile_refresh_required = false, _rx_hold_continuous = false;
  bool _rx_ps_armed = true, _cw_active = false, _cw_stopping = false;
  bool _beacon_owned = false, _beacon_restoring = false;
  bool _morse_owned = false, _morse_rx_owned = false;
  bool pending = false, busy = false, packet = false;
  unsigned irq_reads = 0, busy_reads = 0, packet_reads = 0, claims = 0;
  unsigned resumes = 0, carrier_stops = 0;
  bool hasPendingRadioInterrupt() { ++irq_reads; return pending; }
  bool claimRadioInterrupt() { ++claims; bool value = pending; pending = false; return value; }
  bool isChipBusy() { ++busy_reads; return busy; }
  bool isReceivingPacket() { ++packet_reads; return packet; }
  int16_t applyCachedTxPower(int8_t requested) { return chip.setOutputPower(requested); }
  bool stopCarrierWave() { ++carrier_stops; _cw_active = false; return true; }
  void startRecv() { ++resumes; state = STATE_RX; }
  uint8_t beginReconfigure();
  void endReconfigure(bool);
  bool setTxPower(int8_t);
  bool isTxPowerValid(int8_t) const;
  bool isTxPowerApplied(int8_t) const;
  virtual bool checkBeaconOutputPower(float, int8_t) const;
  Result trySetTxPower(int8_t);
};
@METHODS@
int main() {
  // Status needs proof of current normal hardware power, without SPI reads.
  {
    RadioLibWrapper radio;
    assert(radio.isTxPowerApplied(17) && !radio.isTxPowerApplied(2));
    radio.pending = radio.busy = radio.packet = true;
    assert(radio.isTxPowerApplied(17));
    radio._dbm_applied = false;
    assert(!radio.isTxPowerApplied(17));
    radio._dbm_applied = true; radio._dbm_valid = false;
    assert(!radio.isTxPowerApplied(17));
    radio._dbm_valid = true; radio._profile_repair_required = true;
    assert(!radio.isTxPowerApplied(17));
    radio._profile_repair_required = false; radio._cw_active = true;
    assert(!radio.isTxPowerApplied(17));
    radio._cw_active = false; radio._beacon_owned = true;
    assert(!radio.isTxPowerApplied(17));
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.irq_reads
           && !radio.busy_reads && !radio.packet_reads && !radio.claims);
  }
  {
    RadioLibWrapper radio; radio.pending = radio.busy = radio.packet = true;
    assert(!radio.isTxPowerValid(23) && radio.trySetTxPower(23) == Result::FAILED);
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.irq_reads && !radio.claims);
  }
  // A temporary receive/TX condition does not become a hardware rejection.
  for (unsigned reason = 0; reason < 4; ++reason) {
    RadioLibWrapper radio;
    radio.pending = reason == 0;
    if (reason == 1) radio.state = STATE_TX_WAIT;
    radio.busy = reason == 2;
    radio.packet = reason == 3;
    assert(radio.trySetTxPower(10) == Result::BUSY);
    assert(!radio.setTxPower(10));
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.resumes);
    assert(radio._cur_dbm == 17 && radio.chip.power == 17);
    if (reason == 0) assert(radio.pending && !radio.claims);
    radio.pending = radio.busy = radio.packet = false;
    radio.state = STATE_RX;
    assert(radio.trySetTxPower(10) == Result::APPLIED);
    assert(radio.chip.writes == 1 && radio.chip.standbys == 1 && radio.resumes == 1);
    assert(radio._cur_dbm == 10 && radio._dbm_valid && radio._dbm_applied && radio.chip.power == 10);
    assert(radio.isTxPowerApplied(10) && !radio.isTxPowerApplied(17));
  }
  // App saves can resend the current power while an unread packet owns RX.
  for (unsigned reason = 0; reason < 4; ++reason) {
    RadioLibWrapper radio;
    radio.pending = reason == 0;
    if (reason == 1) radio.state = STATE_TX_WAIT;
    radio.busy = reason == 2;
    radio.packet = reason == 3;
    const auto original_state = radio.state;
    assert(radio.trySetTxPower(17) == Result::APPLIED && radio.setTxPower(17));
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.resumes);
    assert(!radio.irq_reads && !radio.busy_reads && !radio.packet_reads && !radio.claims);
    assert(radio.state == original_state && radio._rx_ps_armed);
    assert(radio.pending == (reason == 0) && radio.packet == (reason == 3));
  }
  // A matching number without a valid hardware cache must still be applied.
  {
    RadioLibWrapper radio; radio._dbm_valid = false; radio.busy = true;
    assert(radio.trySetTxPower(17) == Result::BUSY && !radio.chip.writes);
    radio.busy = false;
    assert(radio.trySetTxPower(17) == Result::APPLIED && radio.chip.writes == 1);
  }
  // A failed earlier transaction cannot be hidden by the unchanged shortcut.
  {
    RadioLibWrapper radio; radio._profile_repair_required = true; radio.busy = true;
    assert(radio.trySetTxPower(17) == Result::BUSY && !radio.chip.writes);
    radio.busy = false;
    assert(radio.trySetTxPower(17) == Result::APPLIED);
    assert(radio.chip.standbys == 1 && radio.chip.writes == 1 && radio.resumes == 1);
  }
  // Beacon and Morse owners remain exclusive even for the cached power.
  for (unsigned owner = 0; owner < 4; ++owner) {
    RadioLibWrapper radio;
    radio._beacon_owned = owner == 0;
    radio._cw_active = owner != 0;
    radio._morse_owned = owner == 1;
    radio._morse_rx_owned = owner == 2;
    radio._cw_stopping = owner == 3;
    assert(radio.trySetTxPower(17) == Result::BUSY && !radio.setTxPower(17));
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.chip.keys);
    assert(!radio.resumes && !radio.carrier_stops);
  }
#if MESH_ENABLE_CARRIER_TOOLS
  {
    RadioLibWrapper radio; radio._cw_active = true;
    assert(radio.trySetTxPower(17) == Result::APPLIED);
    assert(radio.chip.writes == 1 && radio.chip.keys == 1 && !radio.resumes);
  }
#endif
  // A failed SPI command may already have changed the physical output power.
  // The old cached request is usable again only after a verified hardware apply.
  {
    RadioLibWrapper radio;
    radio.chip.power_result = -705; radio.chip.write_before_failure = true;
    assert(radio.trySetTxPower(10) == Result::FAILED);
    assert(radio._cur_dbm == 17 && radio.chip.power == 10 && !radio._dbm_applied);
    assert(!radio.isTxPowerApplied(17) && !radio.isTxPowerApplied(10));
    radio.busy = true;
    assert(radio.trySetTxPower(17) == Result::BUSY && radio.chip.writes == 1);
    radio.busy = false; radio.chip.power_result = 0;
    assert(radio.trySetTxPower(17) == Result::APPLIED && radio.chip.writes == 2);
    assert(radio.chip.power == 17 && radio._dbm_applied);
    assert(radio.isTxPowerApplied(17));
    assert(radio.trySetTxPower(17) == Result::APPLIED && radio.chip.writes == 2);
  }
  // Real command and standby failures remain failures, with the requested cache intact.
  {
    RadioLibWrapper radio; radio.chip.power_result = -705;
    assert(radio.trySetTxPower(10) == Result::FAILED && !radio.setTxPower(10));
    assert(radio._cur_dbm == 17 && radio.chip.power == 17);
    assert(!radio._dbm_applied);
    assert(radio.chip.writes == 2 && radio.resumes == 2);
  }
  {
    RadioLibWrapper radio; radio.chip.standby_result = -705;
    assert(radio.trySetTxPower(10) == Result::FAILED && !radio.setTxPower(10));
    assert(!radio.chip.writes && radio._profile_repair_required && !radio.resumes);
  }
}
'''


class RadioTxPowerTransactionTests(unittest.TestCase):
    def test_pure_family_and_board_limits_preserve_band_clipping(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        validator = method(source, "bool RadioLibWrapper::isTxPowerValid(")
        checker = method(source, "bool RadioLibWrapper::checkBeaconOutputPower(")
        dac = method((ROOT / "src/helpers/radiolib/DacPaSX1276Wrapper.h").read_text(),
                     "bool checkBeaconOutputPower(")
        code = r'''
#include <cassert>
#include <cstdint>
#include <helpers/radiolib/LR2021Band.h>
#define RADIOLIB_ERR_NONE 0
struct Chip {
  mutable unsigned checks = 0;
  int checkOutputPower(int8_t power, int8_t*, bool rfo = false) const {
    ++checks;
#if MESH_RADIO_PROFILE_CHIP == 1276 || MESH_RADIO_PROFILE_CHIP == 1272
    return rfo ? (power >= -4 && power <= 15 ? 0 : -1)
               : ((power >= 2 && power <= 17) || power == 20 ? 0 : -1);
#else
    return power >= -9 && power <= 22 ? 0 : -1;
#endif
  }
};
using SX1276 = Chip;
using SX1272 = Chip;
struct RadioLibWrapper {
  Chip chip;
  Chip* _radio = &chip;
  float _cur_freq = 2440;
  virtual bool checkBeaconOutputPower(float, int8_t) const;
  bool isTxPowerValid(int8_t) const;
};
@CHECKER@
@VALIDATOR@
struct DacWrapper : RadioLibWrapper {
  int8_t _min_dbm = 17, _max_dbm = 30;
  @DAC@
};
int main() {
  RadioLibWrapper radio;
  DacWrapper amplified;
  for (int power = -128; power <= 127; ++power) {
    bool board_ok = true;
#ifdef MIN_LORA_TX_POWER
    board_ok = board_ok && power >= MIN_LORA_TX_POWER;
#endif
#ifdef MAX_LORA_TX_POWER
    board_ok = board_ok && power <= MAX_LORA_TX_POWER;
#endif
#if MESH_RADIO_PROFILE_CHIP == 1121 || MESH_RADIO_PROFILE_CHIP == 2021
    // Ordinary desired power remains valid on a lower-power temporary band.
    const bool chip_ok = true;
#elif MESH_RADIO_PROFILE_CHIP == 1276 || MESH_RADIO_PROFILE_CHIP == 1272
    const bool chip_ok = (power >= -4 && power <= 17) || power == 20;
#else
    const bool chip_ok = power >= -9 && power <= 22;
#endif
    assert(radio.isTxPowerValid(int8_t(power)) == (board_ok && chip_ok));
    assert(amplified.isTxPowerValid(int8_t(power)) ==
#if MESH_RADIO_PROFILE_CHIP == 1121 || MESH_RADIO_PROFILE_CHIP == 2021
           board_ok);
#else
           (board_ok && power >= 17 && power <= 30));
#endif
  }
#if MESH_RADIO_PROFILE_CHIP == 1121 || MESH_RADIO_PROFILE_CHIP == 2021
  assert(radio.chip.checks == 0);
#endif
  assert(amplified.chip.checks == 0);
}
'''
        code = code.replace("@CHECKER@", checker).replace("@VALIDATOR@", validator).replace("@DAC@", dac)
        for family in (1262, 1268, 68, 1276, 1272, 1110, 1121, 2021):
            for limits in ((), ("-DMIN_LORA_TX_POWER=17", "-DMAX_LORA_TX_POWER=30")):
                with self.subTest(family=family, limits=limits):
                    compile_run(code, defines=(f"-DMESH_RADIO_PROFILE_CHIP={family}", *limits))

    def test_actual_busy_retry_noop_ownership_and_failures(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        methods = "\n".join(method(source, signature) for signature in (
            "uint8_t RadioLibWrapper::beginReconfigure(",
            "void RadioLibWrapper::endReconfigure(",
            "bool RadioLibWrapper::setTxPower(",
            "bool RadioLibWrapper::isTxPowerValid(",
            "bool RadioLibWrapper::isTxPowerApplied(",
            "bool RadioLibWrapper::checkBeaconOutputPower(",
            "mesh::RadioParamApplyResult RadioLibWrapper::trySetTxPower("))
        for carrier_tools in (0, 1):
            with self.subTest(carrier_tools=carrier_tools):
                compile_run(HARNESS.replace("@METHODS@", methods), defines=(
                    "-DMESH_ENABLE_TX_BEACONS=1", "-DMESH_ENABLE_MORSE_RX=1",
                    f"-DMESH_ENABLE_CARRIER_TOOLS={carrier_tools}"))


class ResolvedPaProfiles:
    """Read-only, fully resolved build recipes shared by PA safety checks."""

    @classmethod
    def setUpClass(cls):
        # Resolve the actual PlatformIO inheritance and interpolation rules.
        # Loading this Python library does not launch a PlatformIO process.
        cls.config = ProjectConfig(str(ROOT / "platformio.ini"))

    def option(self, section, key):
        value = self.config.get(section, key, default="")
        return "\n".join(value) if isinstance(value, list) else str(value)

    def defines(self, target):
        return effective_defines(self.config.items("env:" + target, as_dict=True))


class ExternalPaPowerLimitTests(ResolvedPaProfiles, unittest.TestCase):
    """A safe startup default must not become the only external PA protection."""

    def targets(self, cap):
        solar_roles = ("companion_radio_ble", "companion_radio_usb", "repeater",
                       "repeater_bridge_rs232", "room_server", "room_server_lora_ota",
                       "kiss_modem")
        ikoka_roles = ("companion_radio_ble", "companion_radio_usb", "repeater",
                       "room_server", "room_server_lora_ota", "kiss_modem")
        expected = {"solarxiao_33S_" + role for role in solar_roles} if cap == 8 else set()
        if cap in (9, 20):
            rating = "33" if cap == 9 else "30"
            expected.update("ikoka_" + board + "_nrf_" + rating + "dbm_" + role
                            for board in ("nano", "stick") for role in ikoka_roles)
        if cap == 20:
            expected.update("ikoka_handheld_nrf_" + role for role in (
                "e22_30dbm_096_companion_radio_ble", "e22_30dbm_096_rotated_companion_radio_ble",
                "e22_30dbm_096_companion_radio_usb", "e22_30dbm_096_rotated_companion_radio_usb",
                "e22_30dbm_repeater", "e22_30dbm_room_server", "e22_30dbm_room_server_lora_ota",
                "kiss_modem"))
        pattern = {8: r"env:solarxiao_33S_",
                   9: r"env:ikoka_(?:nano|stick)_nrf_33dbm_",
                   20: r"env:(?:ikoka_(?:nano|stick)_nrf_30dbm_|ikoka_handheld_nrf_(?:e22_30dbm_|kiss_modem$))"}[cap]
        discovered = {section[4:] for section in self.config.sections()
                      if re.match(pattern, section)}
        self.assertEqual(discovered, expected,
                         "New external PA roles must join the power-safety regression matrix")
        return sorted(expected)

    def power_defines(self, target):
        return {name: int(value) for name, value in self.defines(target).items()
                if name in ("LORA_TX_POWER", "MAX_LORA_TX_POWER")}

    def test_all_39_external_pa_roles_have_their_documented_input_limit(self):
        for cap in (8, 9, 20):
            for target in self.targets(cap):
                with self.subTest(target=target):
                    defines = self.power_defines(target)
                    self.assertEqual(defines.get("LORA_TX_POWER"), cap)
                    self.assertEqual(defines.get("MAX_LORA_TX_POWER"), cap,
                                     "A safe startup default does not prevent later overdrive")

    def test_other_pa_and_unamplified_board_limits_are_preserved(self):
        controls = (("Generic_E22_sx1262_repeater", 22, None),
                    ("solarxiao_30S_repeater", 22, None),
                    ("ikoka_nano_nrf_22dbm_repeater", 22, None),
                    ("ikoka_stick_nrf_22dbm_repeater", 22, None),
                    ("Station_G2_repeater", 7, 19),
                    ("meshnology_w12_repeater", 5, 13))
        for target, default, cap in controls:
            with self.subTest(target=target):
                defines = self.power_defines(target)
                self.assertEqual(defines.get("LORA_TX_POWER"), default)
                self.assertEqual(defines.get("MAX_LORA_TX_POWER"), cap)

    def test_actual_admission_transactions_and_saved_value_clamps(self):
        # Compile each distinct resolved cap through production admission and
        # apply paths, rather than using a comment or assumed startup default.
        profiles = set()
        for cap in (8, 9, 20):
            resolved = {tuple(sorted(self.power_defines(target).items()))
                        for target in self.targets(cap)}
            self.assertEqual(resolved, {(("LORA_TX_POWER", cap), ("MAX_LORA_TX_POWER", cap))})
            profiles.update(resolved)
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        methods = "\n".join(method(source, signature) for signature in (
            "uint8_t RadioLibWrapper::beginReconfigure(",
            "void RadioLibWrapper::endReconfigure(",
            "bool RadioLibWrapper::setTxPower(",
            "bool RadioLibWrapper::isTxPowerValid(",
            "bool RadioLibWrapper::isTxPowerApplied(",
            "bool RadioLibWrapper::checkBeaconOutputPower(",
            "mesh::RadioParamApplyResult RadioLibWrapper::trySetTxPower("))
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text()
        companion = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        common_clamp = re.search(
            r"_prefs->tx_power_dbm\s*=\s*mesh::clampLoRaTxPower\([^;]+;", common)
        companion_clamp = re.search(
            r"_prefs\.tx_power_dbm\s*=\s*constrain\([^;]+;", companion)
        self.assertIsNotNone(common_clamp, "Infrastructure saved TX must be clamped on load")
        self.assertIsNotNone(companion_clamp, "Companion saved TX must be clamped on load")
        prefix = HARNESS.split("int main() {", 1)[0].replace("@METHODS@", methods)
        code = prefix + r'''
#include <helpers/radiolib/RadioPowerLimits.h>
template<typename A, typename B, typename C>
int constrain(A value, B minimum, C maximum) {
  return value < minimum ? minimum : value > maximum ? maximum : value;
}
struct Prefs { int8_t tx_power_dbm; float freq; };
void loadInfrastructure(Prefs* _prefs) { @COMMON_CLAMP@ }
void loadCompanion(Prefs& _prefs) { @COMPANION_CLAMP@ }
int main() {
  for (float frequency : {433.0f, 869.618f, 910.525f, 915.0f}) {
    const int cap = MAX_LORA_TX_POWER;
    assert(mesh::maxLoRaTxPowerForFrequency(frequency) == cap);
    // Old settings and imported values must be safe before any TX starts.
    for (int power = -128; power <= 127; ++power) {
      Prefs infrastructure{int8_t(power), frequency};
      Prefs companion = infrastructure;
      loadInfrastructure(&infrastructure);
      loadCompanion(companion);
      assert(infrastructure.tx_power_dbm == (power > cap ? cap : power));
      assert(companion.tx_power_dbm == (power < -9 ? -9 : power > cap ? cap : power));
      assert(mesh::isLoRaTxPowerValid(power, frequency) == (power <= cap));
      assert(mesh::clampLoRaTxPower(power, frequency) == (power > cap ? cap : power));
      RadioLibWrapper radio;
      radio._cur_freq = frequency;
      radio._dbm_valid = false;  // Require a real setter call even at the cached value.
      const bool valid = power >= -9 && power <= cap;
      assert(radio.isTxPowerValid(int8_t(power)) == valid);
      const auto result = radio.trySetTxPower(int8_t(power));
      assert(result == (valid ? Result::APPLIED : Result::FAILED));
      if (valid) {
        assert(radio.chip.power == power && radio._cur_dbm == power);
        assert(radio.chip.writes == 1 && radio.resumes == 1);
        assert(radio.isTxPowerApplied(int8_t(power)));
      } else {
        assert(radio.chip.writes == 0 && radio.chip.standbys == 0);
        assert(radio.irq_reads == 0 && radio.busy_reads == 0 && radio.packet_reads == 0);
      }
    }
  }
  // Admission must precede even the same-value fast path: a stale unsafe
  // hardware cache cannot legitimize a request after a firmware upgrade.
  for (int power = MAX_LORA_TX_POWER + 1; power <= 127; ++power) {
    RadioLibWrapper radio;
    radio._cur_dbm = radio.chip.power = power;
    radio.pending = radio.busy = radio.packet = true;
    assert(radio.trySetTxPower(power) == Result::FAILED);
    assert(!radio.setTxPower(power));
    assert(!radio.chip.writes && !radio.chip.standbys && !radio.irq_reads);
  }
}
'''
        code = code.replace("@COMMON_CLAMP@", common_clamp.group())
        code = code.replace("@COMPANION_CLAMP@", companion_clamp.group())
        for profile in sorted(profiles):
            with self.subTest(profile=profile):
                defines = tuple("-D" + key + "=" + str(value) for key, value in profile)
                compile_run(code, defines=defines + ("-DMESH_RADIO_PROFILE_CHIP=1262",))

M33_MODULE_PROFILES = (
    ("ikoka_handheld_nrf_e22_33dbm", "ikoka_handheld_nrf_e22_30dbm", "sx1262"),
    ("ikoka_handheld_nrf_e22_400m33s", "ikoka_handheld_nrf_e22_30dbm", "sx1268"),
    ("Generic_E22_m33s", "Generic_E22", "auto"),
    ("Meshadventurer_m33s", "Meshadventurer", "auto"),
    ("ProMicro_m33s", "ProMicro", "sx1262"),
    ("ProMicro_400m33s", "ProMicro", "sx1268"),
    ("Xiao_nrf52_m33s", "Xiao_nrf52", "sx1262"),
    ("Xiao_nrf52_400m33s", "Xiao_nrf52", "sx1268"),
    ("solarxiao_400M33S", "solarxiao_33S", "sx1268"),
    ("ikoka_nano_nrf_400m33s", "ikoka_nano_nrf_33dbm", "sx1268"),
    ("ikoka_stick_nrf_400m33s", "ikoka_stick_nrf_33dbm", "sx1268"),
)
M33_COMMON_ROLES = ("companion_radio_ble", "companion_radio_usb", "repeater",
                    "room_server_lora_ota", "kiss_modem")


def m33_roles(hardware):
    if hardware.startswith("ikoka_handheld_"):
        return ("096_companion_radio_ble", "096_companion_radio_usb",
                "repeater", "room_server_lora_ota", "kiss_modem")
    if hardware == "Generic_E22_m33s":
        return ("repeater", "repeater_bridge_espnow", "kiss_modem")
    if hardware == "Meshadventurer_m33s":
        return ("repeater", "repeater_bridge_espnow", "companion_radio_usb",
                "companion_radio_ble", "terminal_chat", "room_server", "kiss_modem")
    if hardware.startswith("ProMicro_"):
        return ("repeater", "room_server", "terminal_chat", "companion_radio_usb", "companion_radio_ble",
                "sensor", "kiss_modem")
    if hardware.startswith(("Xiao_nrf52_", "solarxiao_")):
        return M33_COMMON_ROLES
    return M33_COMMON_ROLES


class M33ModuleVariantTests(ResolvedPaProfiles, unittest.TestCase):
    """M33 PA hardware needs its own safe image before the first radio init."""

    def profiles(self):
        for hardware, parent, family in M33_MODULE_PROFILES:
            expected = {hardware + "_" + role for role in m33_roles(hardware)}
            actual = {section[4:] for section in self.config.sections()
                      if section.startswith("env:" + hardware + "_")}
            self.assertEqual(actual, expected, "All firmware roles need a safe M33 recipe")
            for target in sorted(expected):
                role = target[len(hardware) + 1:]
                source = parent + "_" + role
                if hardware.startswith("ikoka_handheld_") and role == "kiss_modem":
                    source = "ikoka_handheld_nrf_kiss_modem"
                yield target, source, family

    def test_all_m33_roles_limit_initial_and_runtime_drive_to_8_dbm(self):
        for target, _, family in self.profiles():
            with self.subTest(target=target):
                definitions = self.defines(target)
                self.assertEqual(definitions["LORA_TX_POWER"], "8")
                self.assertEqual(definitions["MAX_LORA_TX_POWER"], "8")
                if family == "auto":
                    self.assertEqual(definitions["RADIO_CLASS"], "UniversalLoRaRadio")
                    self.assertEqual(definitions["WRAPPER_CLASS"], "UniversalLoRaRadio")
                    self.assertEqual(definitions["MESH_RADIO_AUTO_FAMILY"], "1")
                    self.assertEqual(definitions["MESH_RADIO_AUTO_SX1268"], "1")
                    self.assertEqual(definitions["MESH_RADIO_AUTO_SX127X"], "0")
                else:
                    suffix = "SX1268" if family == "sx1268" else "SX1262"
                    self.assertEqual(definitions["RADIO_CLASS"], "Custom" + suffix)
                    self.assertEqual(definitions["WRAPPER_CLASS"], "Custom" + suffix + "Wrapper")
                    self.assertIn("USE_" + suffix, definitions)
                    self.assertNotIn("USE_SX1262" if family == "sx1268" else "USE_SX1268",
                                     definitions)
                    if family == "sx1268":
                        self.assertEqual(float(definitions["LORA_FREQ"]), 433.0)

    def test_module_selection_preserves_carrier_wiring_storage_and_roles(self):
        def carrier_pins(definitions):
            return {key: value for key, value in definitions.items()
                    if key.startswith(("P_LORA_", "SX126X_", "PIN_GPS_", "PIN_BOARD_",
                                       "PIN_WIRE_")) or key == "PIN_USER_BTN"}

        for target, parent, _ in self.profiles():
            with self.subTest(target=target, parent=parent):
                definitions, parent_definitions = self.defines(target), self.defines(parent)
                self.assertEqual(carrier_pins(definitions), carrier_pins(parent_definitions))
                for option in ("board", "board_build.ldscript", "board_upload.maximum_size",
                               "board_build.partitions", "build_src_filter", "lib_deps"):
                    self.assertEqual(self.option("env:" + target, option),
                                     self.option("env:" + parent, option), option)
                for name in ("ENABLE_OTA", "DISABLE_LORA_OTA", "OTA_QSPI_STORE",
                             "OTA_QSPI_BOOTLOADER_UPDATE", "QSPIFLASH", "ENABLE_USB_INTERFACE",
                             "BLE_PIN_CODE", "DISPLAY_CLASS", "DISPLAY_ROTATION"):
                    self.assertEqual(definitions.get(name), parent_definitions.get(name), name)

    def test_m33_choices_get_distinct_ota_identity_and_target_catalog_entries(self):
        sys.path.insert(0, str(ROOT / "tools/mota"))
        from motalib import hardware_id_for_env, target_id_for_env

        rows = {name: int(identifier, 16) for identifier, name in re.findall(
            r'\{ 0x([0-9a-fA-F]{8}), "([^"]+)" \},',
            (ROOT / "src/helpers/ota/OtaTargets.h").read_text())}
        target_ids = set()
        for target, parent, _ in self.profiles():
            with self.subTest(target=target):
                definitions = self.defines(target)
                self.assertNotIn("OTA_VARIANT", definitions,
                                 "A module choice must not silently alias its parent's OTA image")
                self.assertNotIn("MOTA_TARGET_ID", definitions)
                self.assertNotIn("MOTA_HW_ID", definitions)
                identity = target_id_for_env(target)
                self.assertNotEqual(identity, target_id_for_env(parent))
                self.assertNotIn(identity, target_ids)
                target_ids.add(identity)
                self.assertNotEqual(hardware_id_for_env(target), hardware_id_for_env(parent))
                if "ENABLE_OTA" in definitions and "DISABLE_LORA_OTA" not in definitions:
                    self.assertEqual(rows.get(target), identity,
                                     "OTA-enabled module choices must be recognizable over LoRa")

    def test_bootloader_choices_share_only_the_mcu_bootloader_identity(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        from nrf52_embedded_bootloader import load_manifest, select_profile
        manifest = load_manifest()
        for target, parent, family in self.profiles():
            if family == "auto" or not target.endswith(("_repeater", "_room_server")):
                continue
            with self.subTest(target=target):
                expected, _ = select_profile(manifest, parent)
                actual, metadata = select_profile(manifest, target)
                self.assertEqual(actual["id"], expected["id"])
                self.assertEqual(metadata["sourceHardware"],
                                 target.rsplit("_room_server" if target.endswith("_room_server")
                                               else "_repeater", 1)[0])

    def test_release_presets_cannot_retune_a_400m33s_image_to_900_mhz(self):
        program = r'''
set -euo pipefail
source "$1/build.sh"
RADIO_FREQ_OVERRIDE=$3
RADIO_BW_OVERRIDE=62.5
RADIO_SF_OVERRIDE=7
RADIO_CR_OVERRIDE=5
RADIO_SETTING_TITLE=$USA_CASCADIA_RADIO_TITLE
PLATFORMIO_BUILD_FLAGS=-DKEEP_CALLER_FLAG=1
apply_radio_overrides "$2" >/dev/null
printf '%s\n' "$PLATFORMIO_BUILD_FLAGS"
'''
        for hardware, _, family in M33_MODULE_PROFILES:
            if family != "sx1268":
                continue
            for frequency, expected in (("910.525", "433"), ("869.618", "433"),
                                        ("409.999", "433"), ("493.001", "433"),
                                        ("410", "410"), ("433.175", "433.175"),
                                        ("493", "493")):
                with self.subTest(hardware=hardware, frequency=frequency):
                    result = subprocess.run(
                        ["bash", "-c", program, "m33-release-test", str(ROOT),
                         hardware + "_repeater", frequency], capture_output=True,
                        text=True, timeout=30, check=True, cwd=ROOT)
                    definitions = effective_defines({"build_flags": result.stdout})
                    self.assertEqual(definitions["LORA_FREQ"], expected)
                    self.assertEqual(definitions["KEEP_CALLER_FLAG"], "1")
                    self.assertEqual(definitions["LORA_BW"], "62.5")
                    self.assertNotIn("MESHCORE_USA_RADIO_PRESET", definitions)
        for target in ("ProMicro_repeater", "Xiao_nrf52_m33s_repeater",
                       "solarxiao_33S_repeater", "Generic_E22_m33s_repeater"):
            with self.subTest(target=target):
                result = subprocess.run(
                    ["bash", "-c", program, "m33-release-test", str(ROOT),
                     target, "910.525"], capture_output=True, text=True,
                    timeout=30, check=True, cwd=ROOT)
                definitions = effective_defines({"build_flags": result.stdout})
                self.assertEqual(definitions["LORA_FREQ"], "910.525")
                self.assertEqual(definitions["MESHCORE_USA_RADIO_PRESET"], "1")

    def test_first_production_begin_uses_safe_power_before_preferences_load(self):
        # Execute the production init bodies with resolved board defaults. The
        # begin() probe represents the first RadioLib hardware write, before
        # CommonCLI/Companion loads and clamps any old saved preferences.
        startup_profiles = set()
        for target, _, family in self.profiles():
            definitions = self.defines(target)
            chips = (1262, 1268) if family == "auto" else (1268 if family == "sx1268" else 1262,)
            for chip in chips:
                selected = {key: value for key, value in definitions.items()
                            if key in ("LORA_FREQ", "LORA_BW", "LORA_SF", "LORA_CR",
                                       "LORA_TX_POWER", "MAX_LORA_TX_POWER", "NRF52_PLATFORM",
                                       "ESP32_PLATFORM", "MESH_RADIO_AUTO_SX1268", "MESH_E22_M33S")
                            or key.startswith(("P_LORA_", "SX126X_"))}
                startup_profiles.add((chip, tuple(sorted(selected.items()))))
        for chip, definitions in sorted(startup_profiles):
            source = (ROOT / f"src/helpers/radiolib/CustomSX{chip}.h").read_text()
            init = method(source, "bool std_init(SPIClass* spi = NULL)")
            # Both declarations share this body; remove the closing directive
            # for the preceding RP2040-only alternative declaration.
            init = "bool std_init(SPIClass* spi = NULL) " + init[init.index("{"):]
            code = r'''
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <helpers/LoRaRadioFamily.h>
#define MESH_DEBUG_PRINTLN(...) ((void)0)
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_CHIP_NOT_FOUND -2
#define RADIOLIB_ERR_SPI_CMD_FAILED -3
#define RADIOLIB_NC -1
#define RADIOLIB_SX126X_SYNC_WORD_PRIVATE 18
#define D0 0
#define D1 1
#define D2 2
#define D3 3
#define D4 4
#define D5 5
#define D6 6
#define D7 7
#define D8 8
#define D9 9
#define D10 10
struct SPIClass {
  template <typename... Args> void begin(Args...) {}
  template <typename... Args> void setPins(Args...) {}
};
namespace mesh {
struct Logger { template <typename T> void print(T) {} template <typename T> void println(T) {} };
Logger logger;
Logger& usbLoggingPort() { return logger; }
}
struct Chip {
  unsigned begins = 0;
  uint32_t tcxoDelay = 1600, _rxDutyCycleTransitionUs = 0;
  int begin(float frequency, float, int, int, int, int power, int, float, bool = false) {
    ++begins;
    assert(begins == 1 && power == 8 && power == MAX_LORA_TX_POWER);
#if MESH_RADIO_AUTO_SX1268 && TEST_CHIP == 1268
    assert(frequency == mesh::autoLoRaInitialFrequency(mesh::LoRaRadioFamily::SX1268, LORA_FREQ));
    assert(frequency == 433.0f);
#else
    assert(frequency == static_cast<float>(LORA_FREQ));
#endif
    return RADIOLIB_ERR_NONE;
  }
  void setCRC(int) {}
  void setCurrentLimit(int) {}
  void setDio2AsRfSwitch(bool) {}
  void setRxBoostedGainMode(bool) {}
  void setRfSwitchPins(int, int) {}
  @INIT@
};
int main() { SPIClass spi; Chip radio; assert(radio.std_init(&spi) && radio.begins == 1); }
'''
            flags = tuple("-D" + key + ("=" + value if value is not None else "")
                          for key, value in definitions)
            with self.subTest(chip=chip, definitions=definitions):
                compile_run(code.replace("@INIT@", init),
                            defines=flags + (f"-DTEST_CHIP={chip}",))


if __name__ == "__main__":
    unittest.main()
