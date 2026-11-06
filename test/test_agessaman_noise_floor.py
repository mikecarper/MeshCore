#!/usr/bin/env python3
"""Compare the production noise sampler with the pinned upstream control flow."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AgessamanNoiseFloorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("noise sampler parity tests require a host C++ compiler")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-agessaman-floor-")
        cls.addClassCleanup(cls.work.cleanup)
        cls.binary = Path(cls.work.name) / ("parity.exe" if os.name == "nt" else "parity")
        flags = ["-std=c++11", "-O1", "-g", "-Wall", "-Wextra", "-Werror"]
        if sys.platform.startswith("linux"):
            flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        result = subprocess.run([
            compiler, *flags, "-I" + str(ROOT / "src"),
            str(ROOT / "test/fixtures/agessaman_noise_floor/main.cpp"), "-o", str(cls.binary),
        ], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, scenario):
        result = subprocess.run([str(self.binary), scenario], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: " + scenario, result.stdout)

    def test_samples_on_every_loop_and_publishes_on_the_next_call(self):
        self.run_case("cadence")

    def test_receive_and_packet_guards_only_call_the_required_adapters(self):
        self.run_case("guards")

    def test_integer_samples_mean_strict_gate_and_clamp_match_source(self):
        self.run_case("arithmetic")

    def test_triggers_preserve_partial_blocks_and_discard_completed_ones(self):
        self.run_case("triggers")

    def test_reset_clears_samples_while_the_caller_owns_the_published_floor(self):
        self.run_case("reset")

    def test_zero_sum_is_not_published_and_completed_blocks_publish_outside_rx(self):
        self.run_case("publication")

    def test_mixed_event_traces_match_pinned_source_after_every_step(self):
        self.run_case("traces")


class AgessamanWrapperIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The shared receive harness normally exercises the guarded adapter.
        # Select the V4 opt-in here and execute the complete real
        # wrapper methods, including the enabled exact-sampling dispatch.
        from test_radio_receive_contract import method, transition_harness

        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise RuntimeError("noise wrapper tests require a host C++ compiler")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-agessaman-wrapper-")
        cls.addClassCleanup(cls.work.cleanup)
        work = Path(cls.work.name)
        cls.binary = work / ("wrapper.exe" if os.name == "nt" else "wrapper")
        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        header = (ROOT / "src/helpers/radiolib/RadioLibWrappers.h").read_text()
        feature_default = re.search(
            r"#ifndef MC_AGESSAMAN_NOISE_FLOOR_EXACT\s*\n"
            r"#define MC_AGESSAMAN_NOISE_FLOOR_EXACT [^\n]+\n#endif", header)
        if not feature_default:
            raise AssertionError("Missing production exact-sampling default")
        collector = re.search(r"^\s*AgessamanNoiseFloor (\w+);", header, re.MULTILINE)
        if not collector:
            raise AssertionError("Missing production exact collector")
        methods = "\n".join(method(source, signature) for signature in (
            "bool RadioLibWrapper::usesAgessamanNoiseFloor(",
            "void RadioLibWrapper::serviceAgessamanNoiseFloor(",
            "void RadioLibWrapper::resetAgessamanNoiseFloor("))
        harness = transition_harness(source).split("struct LegacyRadio {", 1)[0]
        harness = harness.replace("struct RadioLibWrapper {", "struct RadioLibWrapper {\n"
            + collector.group(0) + "\n"
            + "  bool isInRecvMode() const { return state == STATE_RX; }\n"
            + method(header, "bool isCalibratingNoiseFloor() const").replace(" override", "") + "\n"
            + method(header, "float profileNoiseFloorSecondsRemaining(") + "\n"
            + "  const NoiseFloorEstimator& profileFloorEstimator(uint8_t p) const { return p == 1 ? _secondary_floor_estimator : _floor_estimator; }\n"
            + method(header, "bool getNoiseFloorReading(").replace(" override", "") + "\n"
            + "  bool usesAgessamanNoiseFloor() const;\n"
            + "  void serviceAgessamanNoiseFloor();\n"
            + "  void resetAgessamanNoiseFloor();", 1)
        fixture = (ROOT / "test/fixtures/agessaman_noise_floor/wrapper.cpp").read_text()
        cpp = work / "wrapper.cpp"
        cpp.write_text(feature_default.group(0)
            + '\n#include <helpers/radiolib/AgessamanNoiseFloor.h>\n'
            + "#define EXACT_COLLECTOR " + collector.group(1) + "\n"
            + harness + methods + fixture, encoding="utf-8")
        flags = ["-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                 "-DMESH_ENABLE_TELEMETRY_HISTORY=1", "-DUSE_SX1262=1",
                 "-DMC_AGESSAMAN_NOISE_FLOOR_EXACT=1"]
        if sys.platform.startswith("linux"):
            flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-omit-frame-pointer", "-fno-pie", "-no-pie"]
        result = subprocess.run([compiler, *flags, "-I" + str(ROOT / "src"),
                                 str(cpp), "-o", str(cls.binary)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_case(self, scenario):
        result = subprocess.run([str(self.binary), scenario], capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: " + scenario, result.stdout)

    def test_only_v4_family_recipes_opt_in_while_wrapper_default_is_off(self):
        header = (ROOT / "src/helpers/radiolib/RadioLibWrappers.h").read_text()
        self.assertRegex(header, r"#ifndef MC_AGESSAMAN_NOISE_FLOOR_EXACT\s*\n#define MC_AGESSAMAN_NOISE_FLOOR_EXACT 0")
        for variant in ("heltec_v4", "heltec_v4_r8"):
            recipe = (ROOT / "variants" / variant / "platformio.ini").read_text()
            base_section = recipe.split("\n[", 1)[0]
            self.assertEqual(base_section.count("-D MC_AGESSAMAN_NOISE_FLOOR_EXACT=1"), 1)
        opted_in = [p.parent.name for p in (ROOT / "variants").glob("*/platformio.ini")
                    if "-D MC_AGESSAMAN_NOISE_FLOOR_EXACT=1" in p.read_text()]
        self.assertEqual(sorted(opted_in), ["heltec_v4", "heltec_v4_r8"])

    def test_parameter_transaction_resets_only_successful_exact_path_changes(self):
        from test_radio_profile_scan import HARNESS
        from test_radio_receive_contract import method

        source = (ROOT / "src/helpers/radiolib/RadioLibWrappers.cpp").read_text()
        methods = "\n".join(method(source, signature) for signature in (
            "uint8_t RadioLibWrapper::beginReconfigure(",
            "void RadioLibWrapper::endReconfigure(",
            "mesh::RadioParamApplyResult RadioLibWrapper::trySetParams(",
            "bool RadioLibWrapper::usesAgessamanNoiseFloor("))
        harness = HARNESS.split("int main() {", 1)[0]
        harness = harness.replace("  bool _cw_active=false;", "  bool _cw_active=false;\n"
            "  unsigned resets=0;\n  bool usesAgessamanNoiseFloor() const;")
        harness = harness.replace("void recalibrateNoiseFloor() { _noise_floor_valid=false; }",
            "void recalibrateNoiseFloor() { ++resets; _noise_floor_valid=false; }")
        harness = harness.replace("@METHODS@", methods)
        harness += r"""
int main() {
  using Result = mesh::RadioParamApplyResult;
  RadioLibWrapper w;
  w._rx_ps_enabled = w._rx_ps_armed = false;
  assert(w.trySetParams(909.5f,62.5f,7,5) == Result::APPLIED && w.resets == 0);
  w.busy = true;
  assert(w.trySetParams(911.5f,125,8,5) == Result::BUSY && w.resets == 0);
  w.busy = false; w.fail = true;
  assert(w.trySetParams(911.5f,125,8,5) == Result::FAILED && w.resets == 0);
  assert(w.usesAgessamanNoiseFloor());
  w.fail = false;
  const uint32_t timings[] = {50000, 50000};
  assert(w.trySetParams(909.5f,62.5f,7,5,timings) == Result::APPLIED);
  assert(w.resets == 1 && !w.usesAgessamanNoiseFloor() && !w._noise_floor_valid);
  assert(w.trySetParams(909.5f,62.5f,7,5,timings) == Result::APPLIED && w.resets == 1);
  assert(w.trySetParams(911.5f,125,8,5) == Result::APPLIED && w.resets == 1);
  RadioLibWrapper exact;
  exact._rx_ps_enabled = exact._rx_ps_armed = false;
  assert(exact.trySetParams(911.5f,125,8,5) == Result::APPLIED && exact.resets == 1);
}
"""
        work = Path(self.work.name)
        cpp, binary = work / "parameters.cpp", work / "parameters"
        cpp.write_text(harness, encoding="utf-8")
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        flags = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                 "-DMC_AGESSAMAN_NOISE_FLOOR_EXACT=1"]
        if sys.platform.startswith("linux"):
            flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                      "-fno-pie", "-no-pie"]
        built = subprocess.run([compiler, *flags, "-I" + str(ROOT / "src"),
            str(cpp), "-o", str(binary)], capture_output=True, text=True, timeout=60)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_history_only_updates_on_publication_and_invalidates_after_reset(self):
        self.run_case("history")

    def test_enabled_real_wrapper_bypasses_settling_spacing_and_status_sampling(self):
        self.run_case("cadence")

    def test_real_wrapper_preserves_irq_and_packet_ownership(self):
        self.run_case("ownership")

    def test_real_trigger_and_agc_reset_match_source_progress_and_baseline(self):
        self.run_case("reset_trigger")

    def test_dual_profile_and_duty_cycling_select_their_guarded_adapter(self):
        self.run_case("adapter")

    def test_a_blocked_passive_gate_does_not_claim_exclusive_radio_ownership(self):
        self.run_case("passive")

    def test_mode_changes_reset_collectors_but_rejected_and_noop_changes_preserve_them(self):
        self.run_case("toggles")


if __name__ == "__main__":
    unittest.main()
