#!/usr/bin/env python3
"""Execute the actual transient beacon radio methods against a faultable modem."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def method(source, signature):
    start = source.index(signature)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <initializer_list>
#include <RadioProfiles.h>
#include <helpers/radiolib/RadioProfileChip.h>
#include <helpers/radiolib/RadioPowerLimits.h>
#define MESH_ENABLE_TX_BEACONS 1
#define MESH_ENABLE_MESHTEXT_WIRE 1
#define STATE_IDLE 0
#define STATE_RX 1
#define STATE_TX_WAIT 3
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_ERR_SPI_CMD_TIMEOUT -705
#define RADIOLIB_CHANNEL_FREE -15
#define MAX_TRANS_UNIT 255
#define NF_CALIB_SETTLE_MS 7
#define MESH_DEBUG_PRINTLN(...) ((void)0)
namespace mesh { enum class RadioParamApplyResult { APPLIED, BUSY, FAILED }; }
using Result = mesh::RadioParamApplyResult;
uint32_t now_ms = 100;
uint32_t millis() { return now_ms; }
uint32_t profileTimestamp(bool) { return now_ms * 1000; }
struct Board {
    unsigned before = 0, after = 0;
    void onBeforeTransmit() { ++before; }
    void onAfterTransmit() { ++after; }
};
struct Modem {
    bool standby_fail = false, clear_fail = false, param_fail = false, arm_fail = false;
    int tx_result = 0, scan_result = RADIOLIB_CHANNEL_FREE;
    float freq = 906, bw = 62.5;
    uint8_t sf = 7, cr = 5;
    uint16_t preamble = 32;
    mesh::RadioProfileProtocol protocol = mesh::RadioProfileProtocol::MeshCore;
    int8_t power = 20;
    unsigned standbys = 0, clears = 0, finishes = 0;
    int standby() { ++standbys; return standby_fail ? -1 : 0; }
    int clearIrqFlags(uint32_t) { ++clears; return clear_fail ? -1 : 0; }
    int finishTransmit() { ++finishes; return 0; }
    int startTransmit(uint8_t*, int) { return tx_result; }
    int setPreambleLength(uint16_t value) { preamble = value; return 0; }
    int checkOutputPower(int8_t value, int8_t*) { return value >= -9 && value <= 22 ? 0 : -1; }
};
struct RadioLibWrapper {
    Modem modem;
    Modem* _radio = &modem;
    Board board;
    Board* _board = &board;
    mesh::RadioProfiles _profiles;
    bool _beacon_owned = false, _beacon_restoring = false;
    uint8_t _beacon_restore_profile = 0;
    uint32_t _beacon_restore_at = 0, _beacon_cad_timeout = 0;
    uint8_t state = STATE_RX, _active_profile = 0;
    uint32_t _profile_generation = 1, _profile_visit_stamp = 0;
    bool _profile_refresh_required = false;
    uint16_t _physical_preamble = 32;
    mesh::RadioProfileProtocol _physical_protocol = mesh::RadioProfileProtocol::MeshCore;
    float _cur_freq = 906, _cur_bw = 62.5;
    uint8_t _cur_sf = 7, _cur_cr = 5;
    int8_t _cur_dbm = 20;
    bool _params_valid = true, _dbm_valid = true;
    bool _cw_active = false, pending = false, packet = false, busy = false, calibrating = false;
    bool _rx_ps_enabled = false, _rx_ps_armed = false, _rx_hold_continuous = false;
    bool _rx_ps_continuous_fallback = false, _nf_calib_active = false;
    bool _rx_boosted_gain_valid = true, _cur_rx_boosted_gain = true;
    bool _wd_last_busy = false;
    uint8_t _startrx_fails = 0, _irq_probe_failures = 0;
    uint32_t _rx_ps_rx_us = 50000, _rx_ps_sleep_us = 50000;
    uint32_t _wd_last_transition = 0, _wd_stuck_thresh = 0, _wd_observe_ms = 0;
    uint32_t n_wd_soft = 0, n_wd_hard = 0;
    unsigned long _nf_sample_from = 0;
    unsigned applications = 0, protocols = 0, power_sets = 0, arms = 0, stops = 0;
    unsigned deep_calls = 0, resets = 0, recalibrations = 0;
    unsigned retune_begins = 0, retune_ends = 0, scans = 0;
    bool deep_ok = true, repair_on_deep = false, power_fail = false, protocol_fail = false;
    RadioLibWrapper() {
        _profiles.primary.freq = 906; _profiles.primary.bw = 62.5;
        _profiles.primary.sf = 7; _profiles.primary.cr = 5;
    }
    bool validateProfile(const mesh::RadioProfileParams& p) const {
        return mesh::RadioProfiles::valid(p) && p.freq <= 960 && p.bw <= 500;
    }
    bool isInRecvMode() const { return state == STATE_RX; }
    bool isCalibratingNoiseFloor() const { return calibrating; }
    bool isChipBusy() { return busy; }
    bool isReceivingPacket() { return packet; }
    bool hasPendingRadioInterrupt() const { return pending; }
    bool claimRadioInterrupt() { bool had = pending; pending = false; return had; }
    void beginProfileRetune(bool) { ++retune_begins; }
    void endProfileRetune(bool) { ++retune_ends; }
    void cacheParams(float f, float b, uint8_t s, uint8_t c) {
        _cur_freq = f; _cur_bw = b; _cur_sf = s; _cur_cr = c; _params_valid = true;
    }
    uint16_t profilePreamble(uint8_t profile) const { return _profiles.preamble(profile, 32); }
    uint16_t currentPreambleLength() const { return _physical_preamble; }
    bool applyParams(float f, float b, uint8_t s, uint8_t c) {
        ++applications;
        if (modem.param_fail) return false;
        modem.freq = f; modem.bw = b; modem.sf = s; modem.cr = c;
        modem.preamble = _physical_preamble;
        return true;
    }
    bool applyProtocol(mesh::RadioProfileProtocol p) {
        ++protocols;
        if (protocol_fail) return false;
        modem.protocol = p; return true;
    }
    int applyCachedTxPower(int8_t power) {
        ++power_sets; if (power_fail) return -1; modem.power = power; return 0;
    }
    uint32_t cadScanTimeoutMillis(uint8_t sf, float) const { return sf * 10; }
    int performChannelScanWithTimeout(uint32_t timeout) {
        assert(timeout == _beacon_cad_timeout); ++scans; pending = true;
        return modem.scan_result;
    }
    void stopReceiveDutyCycle() { ++stops; _rx_ps_armed = false; }
    int startReceiveMode() {
        ++arms;
        if (modem.arm_fail) return -1;
        _rx_ps_armed = _rx_ps_enabled; return 0;
    }
    uint32_t getEstAirtimeFor(int) { return 100; }
    bool registerInterruptAction() { return true; }
    void recalibrateNoiseFloor() { ++recalibrations; }
    bool applyRxBoostedGainMode(bool) { return true; }
    bool supportsRadioDeepInit() const { return true; }
    bool radioDeepInit() {
        ++deep_calls;
        if (!deep_ok) return false;
        if (repair_on_deep) {
            busy = false; modem.standby_fail = modem.clear_fail = modem.param_fail = modem.arm_fail = false;
            power_fail = protocol_fail = false;
        }
        return true;
    }
    void resetAGC() { ++resets; }
    void idle() { modem.standby(); state = STATE_IDLE; }
    uint8_t beginReconfigure();
    bool restoreAfterDeepInit();
    bool recoverRadio(bool hard);
    void startRecv();
    bool startSendRaw(const uint8_t*, int);
    void onSendFinished();
    uint32_t getBeaconAirtime(const mesh::RadioProfileParams&, int);
    bool validateBeaconPower(float, int8_t) const;
    virtual bool checkBeaconOutputPower(float, int8_t) const;
    void cacheBeaconRestoreParams();
    Result prepareBeaconTransmit(const mesh::RadioProfileParams&, int8_t);
    Result restoreBeaconReceive();
    bool isBeaconChannelBusy();
};
@METHODS@
mesh::RadioProfileParams beacon(bool meshtext = false) {
    mesh::RadioProfileParams p;
    p.freq = 915; p.bw = 250; p.sf = 11; p.cr = 5;
    if (meshtext) p.protocol = mesh::RadioProfileProtocol::Meshtext;
    return p;
}
int main() {
    unsigned cases = 0;
    for (bool dual : {false, true}) for (bool rxps : {false, true}) for (bool mt : {false, true}) {
        RadioLibWrapper w; now_ms = 100;
        if (dual) {
            w._profiles.secondary.params = w._profiles.primary;
            w._profiles.secondary.params.freq = 908;
            w._profiles.secondary.mode = mesh::RadioProfileMode::RxTx;
            w._active_profile = 1; w._cur_freq = 908; w.modem.freq = 908;
        }
        w._rx_ps_enabled = w._rx_ps_armed = rxps;
        const auto before = w._profiles;
        assert(w.prepareBeaconTransmit(beacon(mt), 10) == Result::APPLIED);
        assert(w._beacon_owned && w.state == STATE_IDLE && w.arms == 0);
        assert(w.modem.freq == 915 && w.modem.power == 10);
        assert(w.modem.preamble == (mt ? 16 : 32));
        assert(w._cur_freq == (dual ? 908 : 906) && w._cur_dbm == 20);
        assert(w._profiles.primary == before.primary && w._profiles.secondary.params == before.secondary.params);
        assert(w._profiles.generation[0] == before.generation[0] && w._profiles.generation[1] == before.generation[1]);
        assert(w._profiles.switches == 0 && w._profiles.switch_test_samples[0] == 0);
        assert(w.beginReconfigure() == 2);
        w.startRecv(); assert(w.arms == 0);
        assert(w.recoverRadio(true) == false && w.deep_calls == 0);
        assert(!w.isBeaconChannelBusy() && !w.pending && w.state == STATE_IDLE);
        assert(w.restoreBeaconReceive() == Result::APPLIED);
        assert(!w._beacon_owned && w.state == STATE_RX && w._rx_ps_armed == rxps);
        assert(w.modem.freq == (dual ? 908 : 906) && w.modem.power == 20);
        assert(w.modem.protocol == mesh::RadioProfileProtocol::MeshCore);
        assert(w.recalibrations == 0 && w.deep_calls == 0);
        ++cases;
    }
    for (unsigned reason = 0; reason < 5; ++reason) {
        RadioLibWrapper w;
        if (reason == 0) w.pending = true;
        if (reason == 1) w.packet = true;
        if (reason == 2) w.busy = true;
        if (reason == 3) w._cw_active = true;
        if (reason == 4) w.calibrating = true;
        assert(w.prepareBeaconTransmit(beacon(), 10) == Result::BUSY);
        assert(!w._beacon_owned && w.applications == 0 && w.power_sets == 0);
        ++cases;
    }
    for (unsigned failure = 0; failure < 4; ++failure) {
        RadioLibWrapper w;
        if (failure == 0) w.modem.param_fail = true;
        if (failure == 1) w.protocol_fail = true;
        if (failure == 2) w.power_fail = true;
        if (failure == 3) w.modem.standby_fail = true;
        assert(w.prepareBeaconTransmit(beacon(true), 10) == Result::FAILED && w._beacon_owned);
        w.repair_on_deep = true;
        assert(w.restoreBeaconReceive() == Result::APPLIED && !w._beacon_owned);
        assert(w.modem.freq == 906 && w.modem.protocol == mesh::RadioProfileProtocol::MeshCore && w.modem.power == 20);
        ++cases;
    }
    for (unsigned changed = 0; changed < 2; ++changed) {
        RadioLibWrapper w;
        w._profiles.secondary.params = w._profiles.primary;
        w._profiles.secondary.params.freq = 908;
        w._profiles.secondary.mode = mesh::RadioProfileMode::RxTx;
        w._active_profile = 1;
        assert(w.prepareBeaconTransmit(beacon(), 10) == Result::APPLIED);
        if (changed == 0) {
            w._profiles.secondary.params.freq = 909; ++w._profiles.generation[1];
        } else w._profiles.secondary.mode = mesh::RadioProfileMode::Off;
        assert(w.restoreBeaconReceive() == Result::APPLIED);
        assert(w.modem.freq == (changed == 0 ? 909 : 906));
        assert(w._active_profile == (changed == 0 ? 1 : 0));
        ++cases;
    }
    for (int cad_result : {RADIOLIB_CHANNEL_FREE, -6, -16, -702}) {
        RadioLibWrapper w;
        assert(w.prepareBeaconTransmit(beacon(), 10) == Result::APPLIED);
        w.modem.scan_result = cad_result;
        assert(w.isBeaconChannelBusy() == (cad_result != RADIOLIB_CHANNEL_FREE));
        assert(!w.pending && w.arms == 0 && w.state == STATE_IDLE);
        assert(w.restoreBeaconReceive() == Result::APPLIED);
        ++cases;
    }
    for (bool spi_timeout : {false, true}) {
        RadioLibWrapper w;
        assert(w.prepareBeaconTransmit(beacon(true), 10) == Result::APPLIED);
        w.modem.tx_result = spi_timeout ? RADIOLIB_ERR_SPI_CMD_TIMEOUT : -1;
        const uint8_t bytes[] = {1, 2};
        assert(!w.startSendRaw(bytes, 2) && w._beacon_owned && w.deep_calls == 0);
        assert(w._cur_freq == 906 && w._cur_dbm == 20);
        assert(w.board.before == 1 && w.board.after == 1);
        assert(w.restoreBeaconReceive() == Result::APPLIED && w.modem.freq == 906);
        ++cases;
    }
    {
        RadioLibWrapper w;
        assert(w.prepareBeaconTransmit(beacon(), 10) == Result::APPLIED);
        const uint8_t bytes[] = {1, 2};
        assert(w.startSendRaw(bytes, 2));
        assert(w.restoreBeaconReceive() == Result::BUSY && w._beacon_owned);
        w.onSendFinished();
        assert(w.restoreBeaconReceive() == Result::APPLIED);
        assert(w.board.before == 1 && w.board.after == 1 && w.modem.finishes == 1);
        ++cases;
    }
    for (unsigned failure = 0; failure < 5; ++failure) {
        RadioLibWrapper w; now_ms = UINT32_MAX - 50;
        assert(w.prepareBeaconTransmit(beacon(true), 10) == Result::APPLIED);
        if (failure == 0) w.modem.standby_fail = true;
        if (failure == 1) w.modem.param_fail = true;
        if (failure == 2) w.modem.clear_fail = true;
        if (failure == 3) w.modem.arm_fail = true;
        if (failure == 4) w.busy = true;
        w.deep_ok = false;
        assert(w.restoreBeaconReceive() == Result::BUSY && w._beacon_owned);
        const unsigned before = w.applications;
        ++now_ms;
        assert(w.restoreBeaconReceive() == Result::BUSY && w.applications == before);
        now_ms += 100; w.deep_ok = w.repair_on_deep = true;
        assert(w.restoreBeaconReceive() == Result::APPLIED && !w._beacon_owned);
        assert(w.modem.freq == 906 && w.modem.power == 20 && w.state == STATE_RX);
        ++cases;
    }
    {
        RadioLibWrapper w;
        assert(w.prepareBeaconTransmit(beacon(true), 10) == Result::APPLIED);
        assert(w.restoreAfterDeepInit());
        assert(w.modem.freq == 906 && w.modem.power == 20);
        assert(w.modem.protocol == mesh::RadioProfileProtocol::MeshCore && w._beacon_owned);
        assert(w.restoreBeaconReceive() == Result::APPLIED);
        ++cases;
    }
    {
        RadioLibWrapper w;
        auto p = beacon();
        assert(w.getBeaconAirtime(p, 255) > 0);
        assert(!w.getBeaconAirtime(p, 0) && !w.getBeaconAirtime(p, 256));
        p.freq = 1000;
        assert(!w.getBeaconAirtime(p, 20));
        assert(w.prepareBeaconTransmit(p, 10) == Result::FAILED && !w._beacon_owned);
        ++cases;
    }
    for (int power : {-10, 23, 100, 127}) {
        RadioLibWrapper w;
        assert(w.prepareBeaconTransmit(beacon(), int8_t(power)) == Result::FAILED);
        assert(!w._beacon_owned && !w.applications && !w.power_sets);
        assert(!w.modem.standbys && !w.retune_begins && !w.stops);
        ++cases;
    }
    std::printf("PASS: %u actual-method beacon radio cases\n", cases);
}
'''


class BeaconRadioTest(unittest.TestCase):
    def source(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        signatures = [
            "uint8_t RadioLibWrapper::beginReconfigure()",
            "bool RadioLibWrapper::restoreAfterDeepInit()",
            "bool RadioLibWrapper::recoverRadio(bool hard)",
            "void RadioLibWrapper::startRecv()",
            "bool RadioLibWrapper::startSendRaw(const uint8_t* bytes, int len)",
            "void RadioLibWrapper::onSendFinished()",
            "uint32_t RadioLibWrapper::getBeaconAirtime(",
            "bool RadioLibWrapper::validateBeaconPower(",
            "bool RadioLibWrapper::checkBeaconOutputPower(",
            "void RadioLibWrapper::cacheBeaconRestoreParams()",
            "mesh::RadioParamApplyResult RadioLibWrapper::prepareBeaconTransmit(",
            "bool RadioLibWrapper::isBeaconChannelBusy()",
            "mesh::RadioParamApplyResult RadioLibWrapper::restoreBeaconReceive()",
        ]
        return HARNESS.replace("@METHODS@", "\n\n".join(method(source, name) for name in signatures))

    def run_harness(self, code, expect_pass=True):
        with tempfile.TemporaryDirectory(prefix="meshcore-beacon-radio-") as directory:
            cpp = Path(directory) / "test.cpp"
            binary = Path(directory) / "test"
            cpp.write_text(code, encoding="ascii")
            command = ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "src"),
                       "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
                       str(cpp), "-o", str(binary)]
            build = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
                       UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
            result = subprocess.run([str(binary)], capture_output=True, text=True, env=env)
            if expect_pass:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS:", result.stdout)
                print(result.stdout.strip())
            else:
                self.assertNotEqual(result.returncode, 0, "negative control unexpectedly passed")
                self.assertIn("Assertion", result.stderr)

    def test_actual_radio_transaction(self):
        self.run_harness(self.source())

    def test_ownership_and_snapshot_negative_controls(self):
        source = self.source()
        mutations = [
            ("if (_beacon_owned && !_beacon_restoring) return 2;", "/* remove ownership guard */"),
            ("if (_beacon_owned && !_beacon_restoring) return;", "/* permit RX on beacon tuple */"),
            ("bool applied = _radio->standby() == RADIOLIB_ERR_NONE",
             "cacheParams(p.freq, p.bw, p.sf, p.cr); bool applied = _radio->standby() == RADIOLIB_ERR_NONE"),
        ]
        for old, new in mutations:
            with self.subTest(mutation=old):
                self.assertIn(old, source)
                self.run_harness(source.replace(old, new, 1), expect_pass=False)

    def test_feature_gate_matrix(self):
        cases = [
            ([], 0, 0),
            (["STM32_PLATFORM"], 0, 0),
            (["USE_SX1262"], 0, 0),
            (["NRF52_PLATFORM", "USE_SX1262"], 0, 1),
            # The codec is stable across translation units, including the
            # Indicator's SPI LoRa radio with expander-controlled GPIOs.
            (["NRF52_PLATFORM", "USE_SX1262", "SENSECAP_INDICATOR_LORA"], 0, 1),
            (["USE_SX1262", "MESH_ENABLE_TX_BEACONS=0"], 0, 0),
            (["USE_SX1262", "MESH_PRIMARY_ESPNOW=1"], 0, 0),
            (["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32S3", "COMPANION_RADIO_FULL", "USE_SX1262"], 1, 1),
            (["ESP32_PLATFORM", "CONFIG_IDF_TARGET_ESP32S3", "COMPANION_RADIO_FULL", "USE_SX1262",
              "MESH_ENABLE_TX_BEACONS=0"], 1, 1),
            (["NRF52_PLATFORM", "RAK_4631", "COMPANION_RADIO_FULL", "USE_SX1262"], 1, 1),
        ]
        for flags, chat, wire in cases:
            with self.subTest(flags=flags):
                code = '#include <helpers/MeshtextConfig.h>\n'
                code += f'static_assert(MESH_ENABLE_MESHTEXT_CHAT == {chat}, "chat gate");\n'
                code += f'static_assert(MESH_ENABLE_MESHTEXT_WIRE == {wire}, "wire gate");\n'
                command = ["g++", "-std=c++17", "-fsyntax-only", "-x", "c++", "-I", str(ROOT / "src")]
                command += ["-D" + flag for flag in flags] + ["-"]
                result = subprocess.run(command, input=code, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
