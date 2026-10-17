#!/usr/bin/env python3
"""Execute sleep preparation/cache code with modeled nRF52 event semantics.

The pinned BSP's loop-task yield and empty CDC available() poll both send SEV
through portYIELD. The fixture models the documented event-register/single-SD-
wait contract so fixes cannot trade stale-event insomnia for lost application
work. Production cache and extracted board methods run against these hardware
primitives. Actual residency and SoftDevice behavior still need hardware tests.
"""

from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import unittest

from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class Nrf52SleepEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="meshcore-nrf52-event-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binary = Path(cls.temp.name) / "sleep-events"
        result = subprocess.run(
            shlex.split(os.environ.get("CXX", "g++"))
            + ["-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
               str(ROOT / "test/fixtures/nrf52_sleep_events.cpp"),
               "-o", str(cls.binary)],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        board = (ROOT / "src/helpers/NRF52Board.cpp").read_text(encoding="ascii")
        methods = [body(board, signature) for signature in (
            "void NRF52Board::prepareSleepCycle()",
            "bool NRF52Board::isExternalPowered()",
            "void NRF52Board::sleep(uint32_t secs)",
        )]
        fixture = (ROOT / "test/fixtures/nrf52_sleep_cycle.cpp").read_text(encoding="ascii")
        cls.cycle_source = fixture.replace("@BOARD_METHODS@", "\n".join(methods))
        cls.cycle_binary = cls.compile_cycle("cycle", cls.cycle_source)
        # Negative control executes the real code with only the old late state
        # SVC restored. It must reproduce insomnia with non-yielding RX polls.
        late_query = ("uint8_t ignored_sd_state = 0;\n"
                      "  mesh_nrf52::softdeviceIsEnabled(ignored_sd_state);\n  ")
        late_methods = methods.copy()
        late_methods[-1] = late_methods[-1].replace(
            "if (cycle.softdevice_enabled)", late_query + "if (cycle.softdevice_enabled)", 1)
        cls.late_query_binary = cls.compile_cycle(
            "late-query", fixture.replace("@BOARD_METHODS@", "\n".join(late_methods)))
        no_ack = cls.cycle_source.replace("error = sd_app_evt_wait();", "error = NRF_SUCCESS;", 1)
        cls.no_ack_binary = cls.compile_cycle("no-sdk-ack", no_ack)
        no_yield = cls.cycle_source.replace("    taskYIELD();", "    // Negative control: no real PendSV.", 1)
        cls.no_yield_binary = cls.compile_cycle("no-forced-pendsv", no_yield)

    @classmethod
    def compile_cycle(cls, name, source):
        cpp = Path(cls.temp.name) / (name + ".cpp")
        binary = Path(cls.temp.name) / name
        cpp.write_text(source, encoding="ascii")
        result = subprocess.run(
            shlex.split(os.environ.get("CXX", "g++"))
            + ["-std=c++11", "-Wall", "-Wextra", "-Werror", "-pedantic",
               "-fsanitize=address,undefined", "-fno-sanitize-recover=all",
               "-fno-pie", "-no-pie", "-I", str(ROOT / "src"), str(cpp),
               str(ROOT / "test/fixtures/nrf52_sleep_cycle_other.cpp"),
               "-o", str(binary)],
            capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return binary

    def run_case(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def run_cycle_case(self, name):
        result = subprocess.run([str(self.cycle_binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_prior_scheduler_yield_prevents_every_single_late_wait(self):
        self.run_case("scheduler")

    def test_entry_clear_and_non_yielding_rx_allow_idle_sleep(self):
        self.run_case("entry_idle")

    def test_entry_clear_alone_cannot_fix_empty_cdc_poll_yield(self):
        self.run_case("poll_yield")

    def test_irqs_at_every_entry_work_predicate_and_wait_boundary_are_safe(self):
        self.run_case("injections")

    def test_event_latch_protects_work_not_visible_to_the_sleep_predicate(self):
        self.run_case("unobserved")

    def test_late_double_wait_can_sleep_with_unserviced_application_work(self):
        self.run_case("late_double")

    def test_raw_cleanup_after_first_wait_does_not_wait_again(self):
        self.run_case("cleanup")

    def test_raw_entry_clear_cannot_acknowledge_private_sdk_irq_state(self):
        self.run_case("private_counter")

    def test_sev_alone_cannot_guarantee_nonblocking_early_sdk_wait(self):
        self.run_case("sev_only")

    def test_private_irq_latch_cannot_wrap_back_to_acknowledged_state(self):
        self.run_case("counter_wrap")

    def test_sdk_entry_ack_preserves_irq_history_from_during_its_wait(self):
        self.run_case("wait_irq_history")

    def test_persistent_disabled_irq_survives_event_clear_and_sdk_ack(self):
        self.run_case("disabled_irq")

    def test_late_disabled_crypto_irq_returns_then_next_entry_recovers(self):
        self.run_case("late_disabled_irq")

    def test_enabled_crypto_owner_and_other_pending_irqs_are_preserved(self):
        self.run_case("irq_owner")

    def test_all_sleeping_roles_prepare_before_any_work_or_policy_check(self):
        entry = "\nvoid loop() {\n#if defined(NRF52_PLATFORM)\n  board.prepareSleepCycle();\n#endif\n"
        for role in ("simple_repeater", "simple_room_server", "simple_sensor",
                     "companion_radio"):
            with self.subTest(role=role):
                source = (ROOT / "examples" / role / "main.cpp").read_text(encoding="ascii")
                actual = body(source, "\nvoid loop() {")
                self.assertTrue(actual.startswith(entry))
                self.assertEqual(actual.count("board.prepareSleepCycle();"), 1)

    def test_actual_cache_single_attempt_stale_publication_errors_and_recovery(self):
        self.run_cycle_case("cache")

    def test_actual_board_raw_wait_does_not_issue_a_late_state_query(self):
        self.run_cycle_case("raw")

    def test_actual_board_softdevice_wait_does_not_issue_a_late_state_query(self):
        self.run_cycle_case("sd")

    def test_actual_raw_wait_keeps_an_irq_after_the_predicate_visible(self):
        self.run_cycle_case("raw_irq")

    def test_actual_softdevice_wait_keeps_an_irq_after_the_predicate_visible(self):
        self.run_cycle_case("sd_irq")

    def test_actual_cross_translation_unit_start_or_partial_start_invalidates_sleep(self):
        self.run_cycle_case("transition")

    def test_actual_query_errors_remain_awake_and_next_valid_cycle_recovers(self):
        self.run_cycle_case("query_error")

    def test_actual_invalidation_during_query_cannot_publish_stale_state(self):
        self.run_cycle_case("during_query")

    def test_actual_irq_during_preparation_preserves_work_for_following_service(self):
        self.run_cycle_case("prepare_irq")

    def test_actual_usb_power_snapshot_avoids_ui_svcs_and_refreshes_each_loop(self):
        self.run_cycle_case("usb")

    def test_actual_disabled_crypto_pending_cleanup_allows_sdk_sleep(self):
        self.run_cycle_case("cc_recover")

    def test_actual_late_crypto_pending_bit_is_not_cleared_until_next_entry(self):
        self.run_cycle_case("cc_late")

    def test_actual_enabled_crypto_owner_and_other_irqs_are_not_cleared(self):
        self.run_cycle_case("cc_owner")
        self.run_cycle_case("other_owner")

    def test_actual_sdk_critical_region_prevents_queries_or_early_ack(self):
        self.run_cycle_case("sdk_critical")
        self.run_cycle_case("sdk_critical_after_yield")

    def test_restoring_old_late_svc_is_a_detected_insomnia_negative_control(self):
        result = subprocess.run(
            [str(self.late_query_binary), "raw"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Assertion", result.stderr)

    def test_missing_early_sdk_ack_or_forced_real_irq_are_detected_negative_controls(self):
        for binary in (self.no_ack_binary, self.no_yield_binary):
            with self.subTest(binary=binary.name):
                result = subprocess.run([str(binary), "sd"], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Assertion", result.stderr)


if __name__ == "__main__":
    unittest.main()
