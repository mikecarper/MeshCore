"""Verify serial ownership checks and optional SDK dependency isolation."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import os
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


class SerialPortOwnershipTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "serial_ownership_test", Path(__file__).with_name("serial_session.py"))
        self.session = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.session)

    def inspect(self, result):
        with patch.object(self.session.sys, "platform", "linux"), \
                patch.object(self.session.shutil, "which", return_value="/usr/bin/fuser"), \
                patch.object(self.session.os.path, "realpath", return_value="/dev/ttyACM7"), \
                patch.object(self.session.subprocess, "run", return_value=result) as run:
            self.session.assert_port_idle("/dev/serial/by-id/exact-device")
        return run

    def test_quiet_no_owner_uses_absolute_path_without_unsupported_separator(self):
        run = self.inspect(subprocess.CompletedProcess([], 1, stdout="", stderr=""))
        run.assert_called_once_with(["/usr/bin/fuser", "/dev/ttyACM7"],
                                    capture_output=True, text=True, timeout=3)

    def test_pi_usage_error_is_not_mistaken_for_an_idle_port(self):
        result = subprocess.CompletedProcess([], 1, stdout="",
                                             stderr="Usage: fuser [-a|-s] [-4|-6] [-SIGNAL] NAME...\n")
        with self.assertRaisesRegex(RuntimeError, "fuser error"):
            self.inspect(result)

    def test_other_output_with_no_owner_status_refuses(self):
        for stdout, stderr in (("1234\n", ""), ("", "permission denied\n"), ("\n", "")):
            with self.subTest(stdout=stdout, stderr=stderr), \
                    self.assertRaisesRegex(RuntimeError, "fuser error"):
                self.inspect(subprocess.CompletedProcess([], 1, stdout=stdout, stderr=stderr))

    def test_existing_owner_still_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "already in use"):
            self.inspect(subprocess.CompletedProcess([], 0, stdout="1234\n", stderr="/dev/ttyACM7:"))


@unittest.skipUnless(os.name == "posix", "POSIX modem-line control")
class AtomicModemOpenTests(unittest.TestCase):
    class Port:
        dtr = rts = dsrdtr = rtscts = False

        def __init__(self):
            self.is_open = False
            self.events = []
            self.driver_dtr = self.driver_rts = True

        def _update_dtr_state(self):
            self.events.append("individual_dtr")
            self.driver_dtr = self.dtr

        def _update_rts_state(self):
            self.events.append("individual_rts")
            self.driver_rts = self.rts

        def open(self):
            self.events.append("open")
            self.is_open = True
            if not self.dsrdtr:
                self._update_dtr_state()
            if not self.rtscts:
                self._update_rts_state()
            self.events.append(("opened_levels", self.driver_dtr, self.driver_rts))

        def close(self):
            self.events.append("close")
            self.is_open = False

        def fileno(self):
            return 77

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "atomic_serial_session", Path(__file__).with_name("serial_session.py"))
        self.session = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.session)

    def exercise(self, port, fail=None):
        import fcntl
        import termios

        def get_attributes(fd):
            self.assertEqual(fd, 77)
            port.events.append("attributes")
            if fail == "attributes":
                raise OSError("attributes failed")
            return [1, 2, termios.HUPCL | 0x4000, 4, 5, 6, [7]]

        def set_attributes(fd, when, attributes):
            self.assertEqual((fd, when), (77, termios.TCSANOW))
            self.assertEqual(attributes, [1, 2, 0x4000, 4, 5, 6, [7]])
            port.events.append("clear_hupcl")
            if fail == "set_attributes":
                raise OSError("set_attributes failed")

        def clear_lines(fd, request, packed):
            self.assertEqual((fd, request), (77, termios.TIOCMBIC))
            self.assertEqual(struct.unpack("I", packed)[0], termios.TIOCM_DTR | termios.TIOCM_RTS)
            port.events.append("joint_clear")
            if fail == "ioctl":
                raise OSError("ioctl failed")
            port.driver_dtr = port.driver_rts = False

        with patch.object(self.session.sys, "platform", "linux"), \
                patch.object(termios, "tcgetattr", side_effect=get_attributes), \
                patch.object(termios, "tcsetattr", side_effect=set_attributes), \
                patch.object(fcntl, "ioctl", side_effect=clear_lines) as modem:
            self.session.open_configured_session(port)
        return modem

    def test_joint_clear_has_no_intermediate_reset_or_boot_levels(self):
        port = self.Port()
        modem = self.exercise(port)
        self.assertEqual(port.events, ["open", ("opened_levels", True, True),
                                      "attributes", "clear_hupcl", "joint_clear"])
        modem.assert_called_once()
        self.assertFalse(port.driver_dtr or port.driver_rts)
        self.assertFalse(port.dsrdtr or port.rtscts)
        self.assertNotIn("_update_dtr_state", port.__dict__)
        self.assertNotIn("_update_rts_state", port.__dict__)
        port._update_dtr_state()
        port._update_rts_state()
        self.assertEqual(port.events[-2:], ["individual_dtr", "individual_rts"])

    def test_prior_dtr_only_deferral_has_an_intermediate_boot_level(self):
        port = self.Port()
        port.dsrdtr = True
        port.open()
        self.assertEqual(port.events[-1], ("opened_levels", True, False))

    def test_all_failure_stages_close_and_restore_instance_overrides(self):
        for fail in ("open", "attributes", "set_attributes", "ioctl"):
            with self.subTest(stage=fail):
                port = self.Port()
                original_dtr = lambda: port.events.append("original_dtr")
                original_rts = lambda: port.events.append("original_rts")
                port._update_dtr_state = original_dtr
                port._update_rts_state = original_rts
                if fail == "open":
                    def failed_open():
                        port.is_open = True
                        raise OSError("open failed")
                    port.open = failed_open
                with self.assertRaisesRegex(OSError, fail + " failed"):
                    self.exercise(port, fail)
                self.assertFalse(port.is_open)
                self.assertEqual(port.events[-1], "close")
                self.assertIs(port._update_dtr_state, original_dtr)
                self.assertIs(port._update_rts_state, original_rts)

    def test_true_dtr_and_other_platforms_retain_their_existing_opener(self):
        for platform, dtr in (("linux", True), ("win32", False), ("darwin", False)):
            with self.subTest(platform=platform, dtr=dtr):
                port = self.Port()
                port.dtr = dtr
                with patch.object(self.session.sys, "platform", platform), \
                        patch.object(self.session, "_open_linux_inactive_modem_session") as atomic:
                    self.session.open_configured_session(port)
                atomic.assert_not_called()
                self.assertTrue(port.is_open)
                self.assertEqual(port.dtr, dtr)

    def test_installed_pyserial_open_uses_the_deferred_methods(self):
        try:
            import serial.serialposix as backend
        except ImportError:
            self.skipTest("installed pySerial backend required")
        port = self.Port()
        port._port = port.portstr = "/dev/test-only"
        port._dsrdtr = port._rtscts = False
        port._reconfigure_port = lambda **kwargs: port.events.append("configure")
        port._reset_input_buffer = lambda: port.events.append("reset_input")
        port.open = lambda: backend.Serial.open(port)
        with patch.object(backend.os, "open", return_value=77), \
                patch.object(backend.os, "pipe", side_effect=[(80, 81), (82, 83)]), \
                patch.object(backend.fcntl, "fcntl"):
            self.exercise(port)
        self.assertNotIn("individual_dtr", port.events)
        self.assertNotIn("individual_rts", port.events)
        self.assertTrue(port.is_open)
        self.assertEqual(port.events, ["configure", "reset_input", "attributes",
                                      "clear_hupcl", "joint_clear"])


class SerialSessionImportTests(unittest.TestCase):
    def test_immutable_collectors_require_only_their_shipped_dependencies(self):
        base = Path(__file__).parent
        fixture_names = ("profile_pair.py", "profile_pair_run.py", "profile_four_tx_fixture.py",
                         "profile_switch.py", "profile_switch_channels.py",
                         "profile_switch_packets.py", "profile_switch_sweep.py")
        for script, end in (("profile_pair_launch.py", "with (root/'collector.log')"),
                            ("profile_four_tx_repeat_run.py", "code=base64.b64decode")):
            source = (base / script).read_text()
            # Execute the actual dependency-selection and hash-validation
            # block, without launch, subprocess, firmware or output operations.
            block = source[source.index("dependencies="):source.index(end)]
            for current, helper in ((False, "absent"), (True, "valid"),
                                    (True, "absent"), (True, "tampered")):
                with self.subTest(script=script, current=current, helper=helper), TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    for name in fixture_names:
                        (root / name).write_text("")
                    (root / "profile_switch.py").write_text(
                        "from serial_session import open_configured_session\n" if current else "# Legacy snapshot\n")
                    if helper != "absent":
                        (root / "serial_session.py").write_text("# Shipped helper\n")
                    manifest = {"files": {file.name: {"sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                                          for file in root.iterdir()}}
                    if helper == "tampered":
                        (root / "serial_session.py").write_text("# Changed after packaging\n")
                    namespace = dict(root=root, previous=root, manifest=manifest,
                                     ast=ast, hashlib=hashlib)
                    if current and helper == "absent":
                        with self.assertRaises(FileNotFoundError):
                            exec(block, namespace)
                    elif helper == "tampered":
                        with self.assertRaises(RuntimeError):
                            exec(block, namespace)
                    else:
                        exec(block, namespace)
                        self.assertEqual("serial_session.py" in namespace["dependencies"], current)

    def test_helpers_import_with_only_the_standard_library(self):
        # -S removes installed site packages. The companion/FEM/soak modules
        # must remain importable until their caller actually requests serial.
        program = ("import sys; sys.path.insert(0, " + repr(str(Path(__file__).parent)) + "); "
                   "import serial_session, esp32_companion_serial_stress, "
                   "fem_noise_floor_test, s3_memory_soak; "
                   "assert 'serial' not in sys.modules")
        result = subprocess.run([sys.executable, "-I", "-S", "-c", program],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
