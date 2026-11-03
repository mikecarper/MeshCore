"""Execute real TX-power admission, retry and unchanged-value behavior."""
import configparser
from pathlib import Path
import re
import unittest

from test_radio_interrupt_recovery import compile_run
from test_radio_receive_contract import method


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


class ExternalPaPowerLimitTests(unittest.TestCase):
    """A safe startup default must not become the only external PA protection."""

    @classmethod
    def setUpClass(cls):
        cls.config = configparser.ConfigParser(
            interpolation=None, strict=False, inline_comment_prefixes=(";",))
        cls.config.read([ROOT / "platformio.ini",
                         *sorted((ROOT / "variants").glob("*/platformio.ini"))])

    def option(self, section, key, stack=()):
        pair = (section, key)
        self.assertNotIn(pair, stack, "recursive PlatformIO option")
        value = self.config.get(section, key, fallback=None)
        if value is None:
            for parent in self.config.get(section, "extends", fallback="").replace(",", " ").split():
                inherited = self.option(parent, key, stack + (pair,))
                if inherited:
                    return inherited
            return ""

        def replace(match):
            reference_section, reference_key = match.group(1).rsplit(".", 1)
            return self.option(reference_section, reference_key, stack + (pair,))

        return re.sub(r"\$\{([^}]+)\}", replace, value)

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
                   20: r"env:(?:ikoka_(?:nano|stick)_nrf_30dbm_|ikoka_handheld_nrf_)"}[cap]
        discovered = {section[4:] for section in self.config.sections()
                      if re.match(pattern, section)}
        self.assertEqual(discovered, expected,
                         "New external PA roles must join the power-safety regression matrix")
        return sorted(expected)

    def power_defines(self, target):
        pattern = r"-D\s*(LORA_TX_POWER|MAX_LORA_TX_POWER)\s*=\s*(-?\d+)\b"
        unflags = set(re.findall(pattern, self.option("env:" + target, "build_unflags")))
        return {key: int(value) for key, value in re.findall(
            pattern, self.option("env:" + target, "build_flags"))
            if (key, value) not in unflags}

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


if __name__ == "__main__":
    unittest.main()
