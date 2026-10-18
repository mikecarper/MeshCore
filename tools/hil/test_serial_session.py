"""Verify serial ownership checks and optional SDK dependency isolation."""
from pathlib import Path
import ast
import hashlib
import importlib.util
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
