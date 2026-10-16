"""Exercise application-only DFU adapter guards without any physical USB access.

Bash integration tests use a mock flasher, mock fuser and mock serial inventory.
They run on native Linux/WSL; the real serial SDK and DFU tool are never invoked.
Inventory/ZIP refusal tests also run on Windows without Bash dependencies.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/hil"
SPEC = importlib.util.spec_from_file_location("verified_nrf_flash_adapter_test", TOOLS / "verified_nrf_flash.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
SERIAL = "SIMULATED123456"
PORT = "/dev/ttyACM99"
BOARD = "RAK4631"


def make_package(path, extra_component=None):
    manifest = {"application": {"bin_file": "application.bin", "dat_file": "application.dat"}}
    if extra_component:
        manifest[extra_component] = {"bin_file": "application.bin", "dat_file": "application.dat"}
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("manifest.json", json.dumps({"manifest": manifest}))
        package.writestr("application.bin", b"NOT-REAL-FIRMWARE")
        package.writestr("application.dat", b"NOT-A-REAL-INIT-PACKET")


def inventory():
    return {"devices": {SERIAL: {"board": BOARD, "firmware_prefixes": ["RAK_4631"],
                                 "bootloader_prefixes": ["RAK_4631_bootloader"]}}}


class MercerNrfAppPackageTest(unittest.TestCase):
    def test_unknown_physical_serial_cannot_select_an_image(self):
        with self.assertRaisesRegex(ValueError, "unrecorded USB serial"):
            VERIFIER.verify(inventory(), "UNREVIEWED123", BOARD, "RAK_4631_companion_test.zip")

    def test_board_mismatch_cannot_reuse_the_reviewed_serial(self):
        with self.assertRaisesRegex(ValueError, "not WrongBoard"):
            VERIFIER.verify(inventory(), SERIAL, "WrongBoard", "RAK_4631_companion_test.zip")

    def test_bootloader_and_softdevice_components_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="meshcore-nrf-package-guard-") as directory:
            for component in ("bootloader", "softdevice", "softdevice_bootloader"):
                with self.subTest(component=component):
                    path = Path(directory) / (component + ".zip")
                    make_package(path, component)
                    with self.assertRaisesRegex(ValueError, "forbids bootloader, SoftDevice"):
                        VERIFIER.verify_application_zip(path)


@unittest.skipUnless(os.name == "posix" and shutil.which("bash"), "native Bash required; run on Linux/WSL")
class MercerNrfAppShellTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="meshcore-nrf-app-mock-")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.adapter = self.work / "mercer_nrf_app_flash.sh"
        shutil.copyfile(TOOLS / "mercer_nrf_app_flash.sh", self.adapter)
        shutil.copyfile(TOOLS / "verified_nrf_flash.py", self.work / "verified_nrf_flash.py")
        self.flasher = self.work / "mock-mcfirmware.sh"
        self.package = self.work / "RAK_4631_companion_test.zip"
        make_package(self.package)
        self.inventory_file = self.work / "inventory.json"
        self.inventory_file.write_text(json.dumps(inventory()), encoding="utf-8")
        self.startup_marker = self.work / "STARTUP-WAS-SOURCED"
        self.dfu_marker = self.work / "MOCK-DFU-ONLY"
        self.env = dict(os.environ, SIM_STARTUP_MARKER=str(self.startup_marker),
                        SIM_DFU_MARKER=str(self.dfu_marker))

        def executable(name, text):
            path = self.bin / name
            path.write_text(text, encoding="utf-8")
            path.chmod(0o700)

        # These commands cannot inspect, reset, open, or claim a USB endpoint.
        executable("fuser", "#!/bin/sh\nexit 1\n")
        executable("python3", "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
        for command in ("pipx", "udevadm", "socat", "stty"):
            executable(command, "#!/bin/sh\necho 'Unexpected external hardware/dependency call' >&2\nexit 89\n")
        for command in ("timeout", "stat", "perl", "unzip", "strings"):
            if not shutil.which(command):
                executable(command, "#!/bin/sh\nexit 89\n")
        self.env["PATH"] = str(self.bin) + os.pathsep + os.environ.get("PATH", "")

        # A Python serial package exposing only a synthetic USB inventory.
        # It has no Serial constructor, making an accidental physical open fail.
        fake_modules = self.work / "python-mocks"
        serial_tools = fake_modules / "serial/tools"
        serial_tools.mkdir(parents=True)
        (serial_tools.parent / "__init__.py").write_text("", encoding="utf-8")
        (serial_tools / "__init__.py").write_text("", encoding="utf-8")
        (serial_tools / "list_ports.py").write_text(
            "from types import SimpleNamespace\n"
            f"def comports(): return [SimpleNamespace(device={PORT!r}, serial_number={SERIAL!r})]\n",
            encoding="utf-8")
        self.env["PYTHONPATH"] = str(fake_modules)
        functions = {
            "preferred_flash_serial_port": "printf '%s\\n' \"$1\"",
            "save_selected_serial_port": "return 0",
            "nrf52_validate_rak_board_pair": "return 0",
            "nrf52_selected_by_id_path": "printf '%s\\n' '/dev/serial/by-id/SIMULATED-ONLY'",
            "udev_device_property": f"if [[ \"$2\" == ID_SERIAL_SHORT ]]; then printf '%s\\n' {SERIAL}; else printf '%s\\n' 'SIMULATED-PATH'; fi",
            "nrf52_usb_path_stem": "printf '%s\\n' \"$1\"",
            "run_nrf52_dfu_package_buttonless": 'printf \'%s|%s|%s|%s\\n\' "$1" "$2" "$3" "$4" > "$SIM_DFU_MARKER"',
        }
        text = ('#!/usr/bin/env bash\n'
                'printf \'STARTUP MUST NEVER RUN\\n\' > "$SIM_STARTUP_MARKER"\n'
                'exit 93\n')
        for name, body in functions.items():
            text += f"{name}() {{\n    {body}\n}}\n"
        for index in range(100):
            text += f"unused_mock_function_{index}() {{\n    return 0\n}}\n"
        self.flasher.write_text(text, encoding="utf-8")

    def invoke(self, overrides=None, *, consent=True):
        arguments = {
            "--flasher": str(self.flasher), "--port": PORT, "--workdir": str(self.work),
            "--inventory": str(self.inventory_file), "--serial": SERIAL, "--board": BOARD,
            "--artifact": str(self.package), "--sha256": hashlib.sha256(self.package.read_bytes()).hexdigest(),
        }
        arguments.update(overrides or {})
        command = [shutil.which("bash"), str(self.adapter)]
        for option, value in arguments.items():
            command.extend((option, value))
        if consent:
            command.append("--allow-write")
        result = subprocess.run(command, env=self.env, capture_output=True, text=True,
                                timeout=15, check=False)
        self.assertFalse(self.startup_marker.exists(), "the flasher's startup code was sourced")
        return result

    def assert_refused(self, result, reason):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(reason, result.stderr)
        self.assertFalse(self.dfu_marker.exists(), "DFU was called before all gates passed")

    def test_missing_write_consent_fails_before_any_flasher_call(self):
        self.assert_refused(self.invoke(consent=False), "requires --allow-write")

    def test_bad_endpoint_and_hash_syntax_fail_closed(self):
        for overrides, reason in (({"--port": "/dev/../ttyACM99"}, "local USB serial tty"),
                                  ({"--port": "/dev/serial/by-id/anything"}, "local USB serial tty"),
                                  ({"--sha256": "abc"}, "exactly 64 hex digits")):
            with self.subTest(overrides=overrides):
                self.assert_refused(self.invoke(overrides), reason)

    def test_actual_artifact_hash_mismatch_prevents_dfu(self):
        self.assert_refused(self.invoke({"--sha256": "0" * 64}), "ZIP SHA256 mismatch")

    def test_unreviewed_physical_serial_prevents_dfu(self):
        self.assert_refused(self.invoke({"--serial": "UNREVIEWED123"}), "unrecorded USB serial")

    def test_bootloader_component_prevents_dfu_even_with_matching_hash(self):
        make_package(self.package, "bootloader")
        self.assert_refused(self.invoke(), "forbids bootloader, SoftDevice")

    def test_definitions_only_source_reaches_only_mock_application_dfu(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.dfu_marker.exists())
        call = self.dfu_marker.read_text(encoding="utf-8").strip().split("|")
        self.assertEqual(call[1:], [PORT, "0", "0"])
        self.assertNotEqual(Path(call[0]), self.package)
        self.assertEqual(Path(call[0]).name, self.package.name)
        self.assertIn("Application-only DFU completed", result.stdout)


if __name__ == "__main__":
    unittest.main()
