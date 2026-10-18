#!/usr/bin/env python3
"""Execute timed CW RSSI decoding and actual exclusive receive transitions."""
from pathlib import Path
import unittest
from test_morse_tx import compile_run, portable_radio_header
from test_carrier_wave import production_harness
from test_radio_receive_contract import method
from morse_rx_floor_step_experiment import legacy_estimator

ROOT = Path(__file__).resolve().parents[1]


class MorseRxTests(unittest.TestCase):
    def test_actual_hil_fast_off_keeps_rising_deglitch_and_production_unchanged(self):
        code=(ROOT / "test/fixtures/morse_rx_fast_off.cpp").read_text(encoding="ascii")
        for hil,flag,expected in ((1,None,0),(1,0,0),(1,1,1),(0,1,0),(0,-1,0),(0,2,0)):
            with self.subTest(hil=hil,fast_off=flag):
                flags=["-DMESH_MORSE_MAX_WPM=240",f"-DMESH_MORSE_HIL={hil}",f"-DEXPECTED_FAST_OFF={expected}"]
                if flag is not None: flags.append(f"-DMESH_MORSE_HIL_FAST_OFF_DEBOUNCE={flag}")
                compile_run(self,code,flags=flags)
        for invalid in (-1,2):
            with self.subTest(invalid_hil_flag=invalid),self.assertRaisesRegex(AssertionError,"fast OFF debounce flag must be 0 or 1"):
                compile_run(self,code,flags=("-DMESH_MORSE_MAX_WPM=240","-DMESH_MORSE_HIL=1",
                            f"-DMESH_MORSE_HIL_FAST_OFF_DEBOUNCE={invalid}","-DEXPECTED_FAST_OFF=0"))

    def test_actual_retained_capture_and_trace_restart_with_only_safety_reserve(self):
        source=(ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text(encoding="utf-8")
        mocks=("static size_t total_free=0,largest_free=0;\nstatic unsigned largest_queries=0;\n"
               "struct ESPMock { size_t getFreeHeap() const { return total_free; } } ESP;\n"
               "constexpr int MALLOC_CAP_8BIT=1;\nsize_t heap_caps_get_largest_free_block(int) { ++largest_queries; return largest_free; }\n")
        code="#include <string>\n#include <cstddef>\n"+mocks+production_harness().split("int main() {",1)[0]
        code=code.replace("virtual bool canAllocateMorseReceiver() const { return receiver_allocation_ok; }",
                          "virtual bool canAllocateMorseReceiver() const;")
        code+=method(source,"bool RadioLibWrapper::canAllocateMorseReceiver(")
        code+=(ROOT / "test/fixtures/morse_rx_driver.cpp").read_text(encoding="ascii").split("int main() {",1)[0]
        code+=(ROOT / "test/fixtures/morse_rx_reuse_admission.cpp").read_text(encoding="ascii")
        arduino=("#pragma once\n#include <cstdint>\n#include <cmath>\n#include <cstdio>\n#include <cstdlib>\n#include <cstring>\n"
                 "inline uint32_t g_mock_millis=0;\ninline uint32_t millis() { return g_mock_millis; }\n")
        for hil in (0,1):
            with self.subTest(hil_trace=bool(hil)):
                compile_run(self,code,flags=("-Wno-unused-parameter","-DESP32_PLATFORM",f"-DMESH_MORSE_HIL={hil}"),
                            headers={"Dispatcher.h":portable_radio_header(),"Arduino.h":arduino})

    def test_actual_hil_cadence_isolated_from_production_wrap_stalls_and_no_spi_bursts(self):
        code=production_harness().split("int main() {",1)[0]
        code+=(ROOT / "test/fixtures/morse_rx_sample_cadence.cpp").read_text(encoding="ascii")
        arduino=("#pragma once\n#include <cstdint>\n#include <cmath>\n#include <cstdio>\n#include <cstdlib>\n#include <cstring>\n"
                 "inline uint32_t g_mock_millis=0;\ninline uint32_t millis() { return g_mock_millis; }\n")
        for hil,requested,expected in ((1,1,1),(1,2,2),(1,10,10),(0,1,2),(0,10,2)):
            with self.subTest(hil=hil,requested_ms=requested):
                compile_run(self,code,flags=("-Wno-unused-parameter",f"-DMESH_MORSE_HIL={hil}",
                            f"-DMESH_MORSE_HIL_SAMPLE_INTERVAL_MS={requested}",f"-DEXPECTED_SAMPLE_MS={expected}"),
                            headers={"Dispatcher.h":portable_radio_header(),"Arduino.h":arduino})
        for invalid in (-1,0,11):
            with self.subTest(invalid_hil_ms=invalid),self.assertRaisesRegex(AssertionError,"sample interval must be 1..10 milliseconds"):
                compile_run(self,code,flags=("-Wno-unused-parameter","-DMESH_MORSE_HIL=1",
                            f"-DMESH_MORSE_HIL_SAMPLE_INTERVAL_MS={invalid}","-DEXPECTED_SAMPLE_MS=2"),
                            headers={"Dispatcher.h":portable_radio_header(),"Arduino.h":arduino})

    def test_actual_four_main_loops_defer_optional_work_but_keep_radio_io_and_safety_live(self):
        first=(ROOT / "test/fixtures/morse_cli.cpp").read_text(encoding="ascii").split("int main() {",1)[0]
        fixture=(ROOT / "test/fixtures/morse_main_loop.cpp").read_text(encoding="ascii")
        fs_mock=(ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text(encoding="ascii")
        for role in ("companion_radio","simple_repeater","simple_room_server","simple_sensor"):
            with self.subTest(role=role):
                source=(ROOT / f"examples/{role}/main.cpp").read_text(encoding="utf-8")
                code=first+fixture.replace("@LOOP@",method(source,"\nvoid loop() {"))
                flags=["-Wno-unused-parameter","-Wno-sign-compare","-DNRF52_PLATFORM","-DDISPLAY_CLASS=1",
                       "-DENABLE_USB_INTERFACE=1","-DHAS_EXTERNAL_WATCHDOG=1","-DTBEAM_1W=1",
                       "-DPOWERSAVING_FIRSTSLEEP_SECS=1","-DPOWERSAVING_FIRST_SLEEP_SECS=1","-DBLE_PIN_CODE=123456"]
                flags.append("-DROLE_"+({"companion_radio":"COMPANION","simple_repeater":"REPEATER",
                             "simple_room_server":"ROOM","simple_sensor":"SENSOR"}[role])+"=1")
                compile_run(self,code,flags=flags,headers={"Dispatcher.h":portable_radio_header(),
                            "helpers/IdentityStore.h":fs_mock})

    def test_actual_decoder_speed_noise_gaps_bounds_timeout_and_retention(self):
        code = (ROOT / "test/fixtures/morse_rx_decoder.cpp").read_text(encoding="ascii")
        output = compile_run(self, code)
        self.assertIn("MorseRx size:", output)
        print(output.strip())

    def test_actual_seeded_estimator_floor_jump_fading_weak_noise_and_lab_speed_boundaries(self):
        code = (ROOT / "test/fixtures/morse_rx_floor_step.cpp").read_text(encoding="ascii")
        for extended in (False, True):
            with self.subTest(lab_speed_sweep=extended):
                flags = ["-DMORSE_ESTIMATOR_REQUIRE_PASS"]
                if extended:
                    flags += ["-DMESH_MORSE_MAX_WPM=240", "-DMORSE_ESTIMATOR_EXTENDED"]
                compile_run(self, code, flags=flags)
        original = (ROOT / "src/helpers/MorseRx.h").read_text(encoding="utf-8")
        old = legacy_estimator(original)
        self.assertNotEqual(original, old, "negative control must remove the real floor-step repair")
        with self.assertRaisesRegex(AssertionError, "Assertion|assert"):
            compile_run(self, code, flags=("-DMORSE_ESTIMATOR_REQUIRE_PASS",), headers={
                        "helpers/MorseRx.h": old,
                        "helpers/MorseTx.h": (ROOT / "src/helpers/MorseTx.h").read_text(encoding="utf-8")})

    def test_actual_smooth_first_floor_margin_strong_fades_and_transition_spans(self):
        code=(ROOT / "test/fixtures/morse_rx_mark_fade.cpp").read_text(encoding="ascii")
        for extended in (False,True):
            with self.subTest(lab_speed_sweep=extended):
                flags=["-DMORSE_MARK_FADE_REQUIRE_PASS"]
                if extended:
                    flags += ["-DMESH_MORSE_MAX_WPM=240","-DMORSE_HYBRID_MATRIX"]
                compile_run(self,code,flags=flags)
        source=(ROOT / "src/helpers/MorseRx.h").read_text(encoding="utf-8")
        provisional=method(source,"if (_keyed && !_floor_settled)")
        regressed=source.replace(provisional,"if (_keyed && threshold < _signal-5.0f) threshold=_signal-5.0f;")
        self.assertNotEqual(source,regressed)
        with self.assertRaisesRegex(AssertionError,"Assertion|assert"):
            compile_run(self,code,flags=("-DMORSE_MARK_FADE_REQUIRE_PASS",),headers={
                        "helpers/MorseRx.h":regressed,"helpers/MorseTx.h":(ROOT / "src/helpers/MorseTx.h").read_text(encoding="utf-8")})

    def test_actual_off_decay_shoulder_does_not_hide_next_weak_word(self):
        code = (ROOT / "test/fixtures/morse_rx_decay_shoulder.cpp").read_text(encoding="ascii")
        compile_run(self, code, flags=("-DMORSE_SHOULDER_REQUIRE_PASS",))
        source = (ROOT / "src/helpers/MorseRx.h").read_text(encoding="utf-8")
        quiet = method(source, "if (!_keyed && !raw_keyed && !_candidate_valid)")
        regressed = quiet.replace("      _signal = _noise + 12.0f;\n", "")
        self.assertNotEqual(quiet, regressed)
        with self.assertRaisesRegex(AssertionError, "Assertion|assert"):
            compile_run(self, code, flags=("-DMORSE_SHOULDER_REQUIRE_PASS",), headers={
                        "helpers/MorseRx.h": source.replace(quiet, regressed),
                        "helpers/MorseTx.h": (ROOT / "src/helpers/MorseTx.h").read_text(encoding="utf-8")})

    def test_actual_receiver_ownership_no_tx_and_fault_recovery(self):
        code = production_harness().split("int main() {", 1)[0]
        code = "#include <string>\n" + code
        code += (ROOT / "test/fixtures/morse_rx_driver.cpp").read_text(encoding="ascii")
        arduino = ("#pragma once\n#include <cstdint>\n#include <cmath>\n#include <cstdio>\n"
                   "#include <cstdlib>\n#include <cstring>\ninline uint32_t g_mock_millis=0;\n"
                   "inline uint32_t millis() { return g_mock_millis; }\n")
        compile_run(self, code, flags=("-Wno-unused-parameter",), headers={
                    "Dispatcher.h": portable_radio_header(), "Arduino.h": arduino})

    def test_actual_lazy_capture_allocation_preserves_safety_reserve_on_every_backend(self):
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text(encoding="utf-8")
        code = (ROOT / "test/fixtures/morse_rx_admission.cpp").read_text(encoding="ascii").replace(
            "@ADMISSION@", method(source, "bool RadioLibWrapper::canAllocateMorseReceiver("))
        for hil in (0, 1):
            for platform in (None, "ESP32_PLATFORM", "NRF52_PLATFORM", "RP2040_PLATFORM", "STM32_PLATFORM"):
                with self.subTest(platform=platform, hil=hil):
                    flags = ["-Wno-unused-variable", f"-DMESH_MORSE_HIL={hil}"]
                    if platform:
                        flags.append("-D" + platform)
                    if platform == "STM32_PLATFORM":
                        flags.append("-DMESH_ENABLE_MORSE_RX=1")
                    compile_run(self, code, flags=flags)

    def test_stm32_default_rx_gate_still_keeps_actual_morse_tx_and_opt_in_is_explicit(self):
        code = production_harness().split("int main() {", 1)[0]
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text(encoding="utf-8")
        code = code.replace("virtual bool canAllocateMorseReceiver() const { return receiver_allocation_ok; }",
                            "virtual bool canAllocateMorseReceiver() const;")
        code = "#include <sys/types.h>\n#include <type_traits>\ncaddr_t _sbrk(int) { return reinterpret_cast<caddr_t>(1); }\n" + code
        code += "#if MESH_ENABLE_MORSE_RX\n"+method(source, "bool RadioLibWrapper::canAllocateMorseReceiver(")+"\n#endif\n"
        stm = (ROOT / "src/helpers/radiolib/CustomSTM32WLxWrapper.h").read_text(encoding="utf-8")
        code += "struct STMReceive : RadioLibWrapper {\n#if MESH_ENABLE_MORSE_RX\n"+method(stm,"bool supportsMorseRx(")+"\n#endif\n"
        code += method(stm,"bool supportsMorseTx(")+"\n};\n"
        code += """
template<typename T,typename=void> struct HasCapturePointer : std::false_type {};
template<typename T> struct HasCapturePointer<T,std::void_t<decltype(T::_morse_rx)>> : std::true_type {};
template<typename T,typename=void> struct HasCaptureAllocator : std::false_type {};
template<typename T> struct HasCaptureAllocator<T,std::void_t<decltype(&T::canAllocateMorseReceiver)>> : std::true_type {};
template<typename T,typename=void> struct HasCaptureOwner : std::false_type {};
template<typename T> struct HasCaptureOwner<T,std::void_t<decltype(T::_morse_rx_owned)>> : std::true_type {};
template<typename T,typename=void> struct HasCaptureReadErrors : std::false_type {};
template<typename T> struct HasCaptureReadErrors<T,std::void_t<decltype(T::_morse_rx_read_errors)>> : std::true_type {};
template<typename T,typename=void> struct HasFskState : std::false_type {};
template<typename T> struct HasFskState<T,std::void_t<decltype(T::_morse_rx_fsk_mode)>> : std::true_type {};
template<typename T,typename=void> struct HasReceivePreparation : std::false_type {};
template<typename T> struct HasReceivePreparation<T,std::void_t<decltype(&T::prepareMorseReceiver)>> : std::true_type {};
template<typename T,typename=void> struct HasReceiveStart : std::false_type {};
template<typename T> struct HasReceiveStart<T,std::void_t<decltype(&T::startMorseReceiver)>> : std::true_type {};
template<typename T,typename=void> struct HasReceiveRead : std::false_type {};
template<typename T> struct HasReceiveRead<T,std::void_t<decltype(&T::readMorseRssi)>> : std::true_type {};
template<typename T,typename=void> struct HasReceiveBandwidth : std::false_type {};
template<typename T> struct HasReceiveBandwidth<T,std::void_t<decltype(&T::morseReceiverBandwidthKHz)>> : std::true_type {};
template<typename T,typename=void> struct HasReceiveService : std::false_type {};
template<typename T> struct HasReceiveService<T,std::void_t<decltype(&T::serviceMorseRx)>> : std::true_type {};
int main() {
  static_assert(MESH_ENABLE_MORSE_RX==EXPECTED_RX,"STM32 RX opt-in must be explicit");
  static_assert(HasCapturePointer<STMReceive>::value==bool(EXPECTED_RX),"disabled RX must exclude capture metadata");
  static_assert(HasCaptureAllocator<STMReceive>::value==bool(EXPECTED_RX),"disabled RX must exclude allocation machinery");
  static_assert(HasCaptureOwner<STMReceive>::value==bool(EXPECTED_RX),"disabled RX must exclude ownership state");
  static_assert(HasCaptureReadErrors<STMReceive>::value==bool(EXPECTED_RX),"disabled RX must exclude read-error state");
  static_assert(HasReceiveService<STMReceive>::value==bool(EXPECTED_RX),"disabled RX must exclude its service implementation");
#if MESH_ENABLE_MORSE_RX || !defined(STM32_PLATFORM)
  constexpr bool expected_native_hooks=true;
#else
  constexpr bool expected_native_hooks=false;
#endif
  static_assert(HasFskState<STMReceive>::value==expected_native_hooks,"compact STM must exclude the unused FSK byte");
  static_assert(HasReceivePreparation<STMReceive>::value==expected_native_hooks,"compact STM must exclude native RX preparation");
  static_assert(HasReceiveStart<STMReceive>::value==expected_native_hooks,"compact STM must exclude native RX start");
  static_assert(HasReceiveRead<STMReceive>::value==expected_native_hooks,"compact STM must exclude native RSSI read");
  static_assert(HasReceiveBandwidth<STMReceive>::value==expected_native_hooks,"compact STM must exclude native bandwidth hook");
  advance(0);
  STMReceive w;
#if MESH_ENABLE_MORSE_RX
  assert(w.supportsMorseRx() && w.canAllocateMorseReceiver());
  assert(w.startMorseRx(902.1f,25,1000)==mesh::MorseRxResult::Started);
  assert(w._morse_rx && w.isMorseRxActive() && !w.chip.starts);
  assert(w.stopMorseRx()==mesh::MorseRxResult::Stopped && !w.isMorseRxActive());
#else
  assert(!w.supportsMorseRx());
  assert(w.startMorseRx(902.1f,25,60000)==mesh::MorseRxResult::Unsupported);
  assert(!w.chip.standby_calls && !w.board.awake);
  assert(w.stopMorseRx()==mesh::MorseRxResult::Unsupported);
  mesh::MorseRxDiagnostics stats;
  assert(!w.morseRxDiagnostics(stats) && !w.morseCaptureGeneration() && !w.morseCaptureLength());
  assert(!w.isMorseRxActive() && !w.morseRxRecovering() && !w.morseRxLearning());
  assert(!w.morseRxBandwidthKHz() && !w.morseRxRemainingMillis() && !w.morseCaptureTruncated());
  assert(w.morseRxFrequency()==mesh::MorseTx::DefaultFrequencyMHz && !strcmp(w.morseCaptureText(),""));
  w.clearMorseCapture(); assert(!w.morseCaptureGeneration());
  char empty[2]={'X','Y'};
  assert(w.readMorseCapture(0,empty,sizeof(empty))==0 && empty[0]==0 && empty[1]=='Y');
#endif
  assert(w.supportsMorseTx());
  assert(w.startMorseTx(902.1f,25,"EE")==mesh::MorseTxResult::Started);
  while(w.isMorseTxActive()) { g_mock_millis=w._morse.nextDeadline(); w.serviceCarrierWave(); }
  assert(w.chip.starts==2 && !w._cw_active && !w.chip.carrier && w.isInRecvMode());
}
"""
        arduino = ("#pragma once\n#include <cstdint>\n#include <cmath>\n#include <cstdio>\n"
                   "#include <cstdlib>\n#include <cstring>\ninline uint32_t g_mock_millis=0;\n"
                   "inline uint32_t millis() { return g_mock_millis; }\n")
        for platform, enabled in (("STM32_PLATFORM",0),("STM32_PLATFORM",1),(None,0)):
            with self.subTest(platform=platform, explicit_rx_opt_in=bool(enabled)):
                flags = ["-Wno-unused-parameter", f"-DEXPECTED_RX={enabled}"]
                if platform:
                    flags.append(f"-D{platform}")
                if enabled or not platform:
                    flags.append(f"-DMESH_ENABLE_MORSE_RX={enabled}")
                compile_run(self, code, flags=flags, headers={
                            "Dispatcher.h": portable_radio_header(), "Arduino.h": arduino})

    def test_hil_lazy_trace_checked_allocation_wrap_retention_and_clear(self):
        allocator = ("#include <new>\n#include <cstddef>\n"
                     "static unsigned nothrow_calls=0, fail_nothrow_call=0;\n"
                     "void* operator new(std::size_t size,const std::nothrow_t&) noexcept {\n"
                     "  if (++nothrow_calls==fail_nothrow_call) return nullptr;\n"
                     "  try { return ::operator new(size); } catch (...) { return nullptr; }\n}\n")
        code = allocator + "#include <string>\n" + production_harness().split("int main() {", 1)[0]
        code += (ROOT / "test/fixtures/morse_rx_driver.cpp").read_text(encoding="ascii").split("int main() {", 1)[0]
        code += (ROOT / "test/fixtures/morse_rx_hil_driver.cpp").read_text(encoding="ascii")
        arduino = ("#pragma once\n#include <cstdint>\n#include <cmath>\n#include <cstdio>\n"
                   "#include <cstdlib>\n#include <cstring>\ninline uint32_t g_mock_millis=0;\n"
                   "inline uint32_t millis() { return g_mock_millis; }\n")
        compile_run(self, code, flags=("-Wno-unused-parameter", "-DMESH_MORSE_HIL=1"), headers={
                    "Dispatcher.h": portable_radio_header(), "Arduino.h": arduino})

    def test_actual_companion_owned_terminal_partial_writes_pause_and_final_capture(self):
        source = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text(encoding="utf-8")
        code = (ROOT / "test/fixtures/morse_rx_terminal.cpp").read_text(encoding="ascii").replace(
            "@TERMINAL@", method(source, "void MyMesh::serviceMorseTerminal("))
        for reader in (0, 1):
            with self.subTest(reader_enabled=bool(reader)):
                compile_run(self, code, flags=(f"-DCOMPANION_FEATURE_READER={reader}",))
        regressed = code.replace("  if (mesh::readerCommandActive(_terminal_output)) return;\n", "")
        self.assertNotEqual(code, regressed)
        with self.assertRaisesRegex(AssertionError, "Assertion|assert"):
            compile_run(self, regressed, flags=("-DCOMPANION_FEATURE_READER=1",))
        loop = method(source, "void MyMesh::loop(")
        self.assertIn("serviceMorseTerminal();", loop)
        self.assertNotIn("serviceMorseTerminal();", method(source, "void MyMesh::sendTerminalTraceRoute("))

    def test_native_receive_requires_restorable_chip_and_all_stm32_targets_register_real_init(self):
        wrapper = (ROOT / "src/helpers/radiolib/RadioLibWrappers.h").read_text(encoding="utf-8")
        self.assertIn("return false", method(wrapper, "bool supportsMorseRx("))
        default_prepare = method(wrapper, "virtual int16_t prepareMorseReceiver(")
        self.assertNotIn("applyParams", default_prepare)
        self.assertNotIn("startReceive", default_prepare)
        stm = (ROOT / "src/helpers/radiolib/CustomSTM32WLxWrapper.h").read_text(encoding="utf-8")
        self.assertIn("_deep_init != nullptr", method(stm, "bool supportsRadioDeepInit("))
        self.assertIn("_deep_init && _deep_init()", method(stm, "bool radioDeepInit("))
        for board in ("rak3x72", "tiny_relay", "wio-e5-mini", "wio-e5-dev"):
            with self.subTest(board=board):
                source = (ROOT / f"variants/{board}/target.cpp").read_text(encoding="utf-8")
                initialize = method(source, "bool radio_init(")
                self.assertIn("radio_driver.setDeepInitCallback(radio_init)", initialize)
                self.assertLess(initialize.index("radio.begin("), initialize.index("setDeepInitCallback("))

    def test_actual_chip_rssi_reads_are_checked_and_never_restart_receiver(self):
        code = (ROOT / "test/fixtures/morse_rx_chip_rssi.cpp").read_text(encoding="ascii")
        sx1262 = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text(encoding="utf-8")
        code = code.replace("@SX1262_GETTER@", method(sx1262, "float getMorseRssi("))
        wrapper = (ROOT / "src/helpers/radiolib/RadioLibWrappers.h").read_text(encoding="utf-8")
        code = code.replace("@DEFAULT_BANDWIDTH@", method(wrapper, "virtual float morseReceiverBandwidthKHz("))
        sx1276 = (ROOT / "src/helpers/radiolib/CustomSX1276.h").read_text(encoding="utf-8")
        code = code.replace("@SX1276_GETTER@", method(sx1276, "float getMorseRssi("))
        for chip in ("SX1262", "SX1268", "LLCC68", "STM32WLx", "LR1110", "LR1121", "LR2021", "SX1276"):
            source = (ROOT / f"src/helpers/radiolib/Custom{chip}Wrapper.h").read_text(encoding="utf-8")
            methods = method(source, "float readMorseRssi(")
            if "float morseReceiverBandwidthKHz(" in source:
                methods += "\n" + method(source, "float morseReceiverBandwidthKHz(")
            code = code.replace(f"@{chip}@", methods)
        compile_run(self, code)

    def test_actual_native_fsk_receive_filters_reference_supply_rf_routes_and_checked_failures(self):
        code = (ROOT / "test/fixtures/morse_rx_native.cpp").read_text(encoding="ascii")
        for chip in ("LR1110", "LR1121", "LR2021", "SX1276"):
            source = (ROOT / f"src/helpers/radiolib/Custom{chip}.h").read_text(encoding="utf-8")
            code = code.replace(f"@{chip}@", method(source, "int16_t beginMorseReceive("))
            if chip == "LR2021":
                code = code.replace("@LR2021_RESTORE@", method(source, "int16_t restoreRfSwitchTable("))
        sx1262 = (ROOT / "src/helpers/radiolib/CustomSX1262.h").read_text(encoding="utf-8")
        code = code.replace("@FAST_RSSI@", method(sx1262, "int16_t setMorseFastRssi("))
        for chip in ("SX1262", "SX1268", "LLCC68", "STM32WLx"):
            source = (ROOT / f"src/helpers/radiolib/Custom{chip}.h").read_text(encoding="utf-8")
            self.assertIn("setSX126xMorseRssiWindow(this)", method(source, "int16_t setMorseFastRssi("))
        for flags in ((), ("-DMESH_MORSE_HIL=1", "-DMESH_MORSE_HIL_RX_BITRATE_KBPS=20.0f"),
                      ("-DMESH_MORSE_HIL=0", "-DMESH_MORSE_HIL_RX_BITRATE_KBPS=20.0f"),
                      ("-DMESH_MORSE_HIL=1", "-DMESH_MORSE_HIL_RSSI_WINDOW=2"),
                      ("-DMESH_MORSE_HIL=0", "-DMESH_MORSE_HIL_RSSI_WINDOW=2"),
                      ("-DMESH_MORSE_HIL=1","-DMESH_MORSE_HIL_PACKET_GATING=0"),
                      ("-DMESH_MORSE_HIL=1","-DMESH_MORSE_HIL_PACKET_GATING=1"),
                      ("-DMESH_MORSE_HIL=0","-DMESH_MORSE_HIL_PACKET_GATING=1")):
            with self.subTest(receiver_clock=flags):
                compile_run(self, code, flags=flags, headers={"RadioLib.h": "#pragma once\n"})
        for invalid_window in (-1,8):
            with self.subTest(invalid_rssi_window=invalid_window), self.assertRaisesRegex(
                    AssertionError,"RSSI averaging exponent must fit bits4:2"):
                compile_run(self,code,flags=("-DMESH_MORSE_HIL=1",f"-DMESH_MORSE_HIL_RSSI_WINDOW={invalid_window}"),
                            headers={"RadioLib.h":"#pragma once\n"})
        for invalid in (-1,2):
            with self.subTest(invalid_packet_gate=invalid),self.assertRaisesRegex(AssertionError,"packet gating must be 0 or 1"):
                compile_run(self,code,flags=("-DMESH_MORSE_HIL=1",f"-DMESH_MORSE_HIL_PACKET_GATING={invalid}"),
                            headers={"RadioLib.h":"#pragma once\n"})

    def test_actual_all_native_wrapper_admissions_partial_reset_and_delayed_lora_recovery(self):
        code = production_harness().split("int main() {", 1)[0]
        code = code.replace("struct Chip {", "struct Chip {\n  unsigned invalidations=0;\n  void invalidateMorseModemCache() { ++invalidations; }\n  int beginMorseReceive(float freq) { ++fsk_starts; fsk=true; frequency=freq; return fsk_result; }\n")
        code = code.replace("  bool awake=false;", "  bool reset_prepare_ok=true,reset_finish_ok=true; unsigned reset_prepares=0,reset_finishes=0;\n  bool finishRadioHardReset() { ++reset_finishes; return reset_finish_ok; }\n  bool awake=false;")
        code = code.replace("  uint8_t beginReconfigure();", "  bool prepareRadioHardReset() { ++board.reset_prepares; return board.reset_prepare_ok; }\n  uint8_t beginReconfigure();")
        # Execute each actual wrapper admission; low-level FSK configuration is
        # covered in the separate native-helper fixture above.
        code += "\ntemplate<typename T> int16_t prepareSX126xMorseReceiver(T* radio,float frequency) { return radio->beginMorseReceive(frequency); }\n"
        fixture = (ROOT / "test/fixtures/morse_rx_chip_preparation.cpp").read_text(encoding="ascii")
        for chip in ("SX1262", "SX1268", "LLCC68", "STM32WLx", "LR1110", "LR1121", "LR2021", "SX1276"):
            source = (ROOT / f"src/helpers/radiolib/Custom{chip}Wrapper.h").read_text(encoding="utf-8")
            extracted = "\n".join(method(source, signature) for signature in (
                "bool supportsMorseRx(", "float morseReceiverBandwidthKHz(", "int16_t prepareMorseReceiver("))
            if "int16_t startMorseReceiver(" in source:
                extracted += "\n"+method(source,"int16_t startMorseReceiver(")
            fixture = fixture.replace(f"@{chip}@", extracted)
        code += fixture
        arduino = ("#pragma once\n#include <cstdint>\n#include <cmath>\n#include <cstdio>\n"
                   "#include <cstdlib>\n#include <cstring>\ninline uint32_t g_mock_millis=0;\n"
                   "inline uint32_t millis() { return g_mock_millis; }\n#define RADIOLIB_ERR_UNSUPPORTED -17\n#define RADIOLIB_ERR_CHIP_NOT_FOUND -2\n"
                   "#define RADIOLIB_LR11X0_RX_TIMEOUT_INF 0xffffffU\n#define RADIOLIB_LR2021_RX_TIMEOUT_INF 0xffffffU\n")
        compile_run(self, code, flags=("-Wno-unused-parameter",), headers={
                    "Dispatcher.h": portable_radio_header(), "Arduino.h": arduino})


if __name__ == "__main__":
    unittest.main()
