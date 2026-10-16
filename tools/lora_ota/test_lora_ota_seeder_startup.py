"""Seeder startup timing tests; no radios or transmissions."""

import argparse
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import lora_ota as ota
import rak3401_mota_chain as rak_chain


PREPARING = (
    "motatool serve: 1 valid .mota in /served (recursive)\n"
    "update.mota : blocks=264 size=541358\n"
    "preparing raw-DEFLATE transport with Zopfli --i1000 before opening the link\n"
)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        # Advance quickly without waiting in real time. Deadline decisions
        # remain based on the same clock used to select the visible log lines.
        self.now += max(seconds, 1.0)


class SeederStartupTests(unittest.TestCase):
    def setUp(self):
        self.args = argparse.Namespace(
            motatool="motatool", source_serial="/dev/source",
            source_tcp=None, source_baud=115200,
            seeder_start_wait=5, seeder_prepare_wait=1800,
        )
        self.clock = Clock()
        self.output = io.StringIO()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(ota.time, "monotonic", self.clock.monotonic).start()
        mock.patch.object(ota.time, "sleep", self.clock.sleep).start()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def seeder(self, events):
        seeder = ota.SeederProcess(self.args, Path("/served"), Path("/work"))
        seeder.process = mock.Mock()
        seeder.process.poll.return_value = None

        def log_tail():
            return next(
                (text for when, text in reversed(events) if when <= self.clock.now),
                "no seeder log output",
            )

        seeder._log_tail = mock.Mock(side_effect=log_tail)
        return seeder

    def test_slow_compression_gets_fresh_count_timer(self):
        seeder = self.seeder([
            (0, PREPARING),
            (12, PREPARING + "  compressed 64/264 blocks\n"),
            (30, PREPARING + "  compressed 256/264 blocks\n"),
            (40, PREPARING + "  compressed 264/264 blocks\n"),
            (44, "  compressed 264/264 blocks\n  [dev] COUNT -> 1\n"),
        ])
        seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 44)
        self.assertIn("64/264 blocks", self.output.getvalue())
        self.assertIn("host compression complete", self.output.getvalue())

    def test_preparation_remains_recognized_after_header_leaves_log_tail(self):
        seeder = self.seeder([
            (0, PREPARING),
            (10, "  compressed 64/264 blocks\n"),
            (20, "  compressed 264/264 blocks\n"),
            (24, "  [dev] COUNT -> 1\n"),
        ])
        seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 24)

    def test_progress_can_identify_phase_without_header(self):
        seeder = self.seeder([
            (0, "  compressed 64/264 blocks\n"),
            (20, "  compressed 264/264 blocks\n"),
            (22, "  [dev] COUNT -> 1\n"),
        ])
        seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 22)

    def test_crlf_progress_starts_count_timer(self):
        seeder = self.seeder([
            (0, PREPARING.replace("\n", "\r\n")),
            (20, "  compressed 264/264 blocks\r\n"),
        ])
        with self.assertRaisesRegex(ota.OtaError, "after host compression completed"):
            seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 25)

    def test_invalid_completion_counts_do_not_start_count_timer(self):
        self.args.seeder_prepare_wait = 10
        for counts in ("0/0", "265/264"):
            with self.subTest(counts=counts):
                self.clock.now = 0
                seeder = self.seeder([(0, PREPARING + f"  compressed {counts} blocks\n")])
                with self.assertRaisesRegex(ota.OtaError, "host compression did not complete"):
                    seeder._wait_until_attached()
                self.assertEqual(self.clock.now, 10)

    def test_final_progress_does_not_prove_device_readiness_or_reset_timer(self):
        seeder = self.seeder([
            (0, PREPARING),
            (40, PREPARING + "  compressed 264/264 blocks\n"),
        ])
        with self.assertRaisesRegex(
            ota.OtaError, "COUNT acknowledgement within 5s after host compression completed"
        ):
            seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 45)

    def test_stalled_preparation_has_its_own_bounded_timeout(self):
        self.args.seeder_prepare_wait = 60
        seeder = self.seeder([(0, PREPARING)])
        with self.assertRaisesRegex(ota.OtaError, "host compression did not complete within 60s"):
            seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 60)
        self.assertIn("still compressing", self.output.getvalue())
        self.assertIn("30s elapsed", self.output.getvalue())

    def test_progress_does_not_extend_preparation_deadline(self):
        self.args.seeder_prepare_wait = 60
        seeder = self.seeder([
            (0, PREPARING),
            (20, PREPARING + "  compressed 64/264 blocks\n"),
            (40, PREPARING + "  compressed 128/264 blocks\n"),
            (59, PREPARING + "  compressed 256/264 blocks\n"),
        ])
        with self.assertRaisesRegex(ota.OtaError, "host compression did not complete within 60s"):
            seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 60)

    def test_custom_preparation_timeout_is_honored(self):
        self.args.seeder_prepare_wait = 12
        seeder = self.seeder([(0, PREPARING)])
        with self.assertRaisesRegex(ota.OtaError, "within 12s"):
            seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 12)

    def test_namespace_without_new_option_uses_default_preparation_limit(self):
        del self.args.seeder_prepare_wait
        seeder = self.seeder([(0, PREPARING)])
        with self.assertRaisesRegex(ota.OtaError, "host compression did not complete within 1800s"):
            seeder._wait_until_attached()
        self.assertEqual(self.clock.now, ota.DEFAULT_SEEDER_PREPARE_WAIT_SECONDS)

    def test_device_error_stops_both_startup_phases_immediately(self):
        for phase, elapsed in (("preparing", 10), ("attaching", 41)):
            with self.subTest(phase=phase):
                self.clock.now = 0
                seeder = self.seeder([
                    (0, PREPARING),
                    (40, "  compressed 264/264 blocks\n"),
                    (elapsed, "  [dev] ERR folder source unavailable\n"),
                ])
                with self.assertRaisesRegex(ota.OtaError, "rejected by the device"):
                    seeder._wait_until_attached()
                self.assertEqual(self.clock.now, elapsed)

    def test_process_exit_stops_both_startup_phases_immediately(self):
        for elapsed in (10, 41):
            with self.subTest(elapsed=elapsed):
                self.clock.now = 0
                seeder = self.seeder([
                    (0, PREPARING),
                    (40, "  compressed 264/264 blocks\n"),
                ])
                seeder.process.poll.side_effect = lambda: 2 if self.clock.now >= elapsed else None
                with self.assertRaisesRegex(ota.OtaError, "exited with status 2 during startup"):
                    seeder._wait_until_attached()
                self.assertEqual(self.clock.now, elapsed)

    def test_legacy_tool_keeps_original_startup_timeout(self):
        seeder = self.seeder([(0, "serving on /dev/source ... Ctrl-C to stop\n")])
        with self.assertRaisesRegex(ota.OtaError, "COUNT acknowledgement within 5s:"):
            seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 5)
        self.assertNotIn("preparing compressed", self.output.getvalue())

    def test_legacy_tool_is_ready_only_with_count(self):
        seeder = self.seeder([
            (0, "serving on /dev/source ... Ctrl-C to stop\n"),
            (4, "  [dev] COUNT -> 1\n"),
        ])
        seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 4)

    def test_genuine_count_can_confirm_readiness_without_final_progress(self):
        seeder = self.seeder([
            (0, PREPARING),
            (10, PREPARING + "  [dev] COUNT -> 1\n"),
        ])
        seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 10)

    def test_flush_failure_does_not_hide_a_real_count(self):
        seeder = ota.SeederProcess(self.args, Path("/served"), Path("/work"))
        seeder.process = mock.Mock()
        seeder.process.poll.return_value = None
        seeder.log_file = mock.Mock(closed=False)
        seeder.log_file.flush.side_effect = OSError("log flush failed")
        with mock.patch.object(Path, "read_text", return_value="[dev] COUNT -> 1\n"):
            seeder._wait_until_attached()
        self.assertEqual(self.clock.now, 0)

    def test_flush_and_read_failures_cannot_prove_readiness_or_skip_deadline(self):
        seeder = ota.SeederProcess(self.args, Path("/served"), Path("/work"))
        seeder.process = mock.Mock()
        seeder.process.poll.return_value = None
        seeder.log_file = mock.Mock(closed=False)
        seeder.log_file.flush.side_effect = OSError("log flush failed")
        with mock.patch.object(Path, "read_text", side_effect=PermissionError("unreadable log")):
            with self.assertRaisesRegex(ota.OtaError, "COUNT acknowledgement within 5s") as failure:
                seeder._wait_until_attached()
        self.assertIn("could not read seeder log", str(failure.exception))
        self.assertEqual(self.clock.now, 5)

    def test_flush_failure_preserves_process_exit_diagnostics(self):
        seeder = ota.SeederProcess(self.args, Path("/served"), Path("/work"))
        seeder.process = mock.Mock()
        seeder.process.poll.return_value = 17
        seeder.log_file = mock.Mock(closed=False)
        seeder.log_file.flush.side_effect = OSError("log flush failed")
        with mock.patch.object(Path, "read_text", return_value="child diagnostic\n"):
            with self.assertRaisesRegex(ota.OtaError, "exited with status 17") as failure:
                seeder.ensure_running("during startup")
        self.assertIn("child diagnostic", str(failure.exception))


class SeederOptionTests(unittest.TestCase):
    def arguments(self, chain=False, *options):
        parser = rak_chain.build_parser() if chain else ota.build_parser()
        values = [] if chain else ["release.mota", "remote"]
        values.extend([
            "--controller-serial", "/dev/controller",
            "--source-serial", "/dev/source", *options,
        ])
        return parser, parser.parse_args(values)

    def test_generic_and_chain_have_matching_defaults_and_overrides(self):
        for chain in (False, True):
            with self.subTest(chain=chain):
                _, args = self.arguments(chain)
                self.assertEqual(args.seeder_prepare_wait, 1800)
                self.assertEqual(args.seeder_start_wait, 5)
                _, args = self.arguments(chain, "--seeder-prepare-wait", "2400")
                self.assertEqual(args.seeder_prepare_wait, 2400)

    def test_both_runners_reject_nonpositive_preparation_timeouts(self):
        for chain in (False, True):
            for value in ("0", "-1"):
                with self.subTest(chain=chain, value=value):
                    parser, args = self.arguments(chain, "--seeder-prepare-wait", value)
                    error = io.StringIO()
                    validate = rak_chain.validate_args if chain else ota.validate_args
                    with contextlib.redirect_stderr(error), self.assertRaises(SystemExit):
                        validate(args, parser)
                    self.assertIn("--seeder-prepare-wait must be positive", error.getvalue())

    def test_preparation_budget_remains_advisory_with_twenty_minute_lease(self):
        for chain in (False, True):
            with self.subTest(chain=chain):
                parser, args = self.arguments(chain, "--temp-radio-minutes", "20")
                validate = rak_chain.validate_args if chain else ota.validate_args
                with contextlib.redirect_stderr(io.StringIO()):
                    validate(args, parser)
                self.assertEqual(args.temp_values[-1], 20)
                original = ota.recommended_temp_radio_minutes(args)
                args.seeder_prepare_wait += 60
                self.assertEqual(ota.recommended_temp_radio_minutes(args), original + 1)


class SeederSubprocessTests(unittest.TestCase):
    def test_real_subprocess_prepares_longer_than_attach_timeout(self):
        # Exercise actual redirected output and lifecycle, not only mocked
        # log snapshots. The child is a host-only stand-in, never motatool.
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(
                motatool="unused", source_serial="/dev/unused",
                source_tcp=None, source_baud=115200,
                seeder_start_wait=1.0, seeder_prepare_wait=10,
            )
            seeder = ota.SeederProcess(args, Path(directory), Path(directory))
            seeder.command = [
                sys.executable, "-u", "-c",
                "import time\n"
                f"print({PREPARING!r}, flush=True)\n"
                "time.sleep(1.25)\n"
                "print('  compressed 264/264 blocks', flush=True)\n"
                "time.sleep(0.2)\n"
                "print('  [dev] COUNT -> 1', flush=True)\n"
                "time.sleep(30)\n",
            ]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                try:
                    seeder.start()
                    self.assertIsNone(seeder.process.poll())
                finally:
                    seeder.stop()
            self.assertIn("running (device COUNT confirmed)", output.getvalue())
            self.assertIn("host compression complete", output.getvalue())
            self.assertIsNone(seeder.process)


if __name__ == "__main__":
    unittest.main()
