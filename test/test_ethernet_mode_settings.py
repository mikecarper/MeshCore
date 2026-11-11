#!/usr/bin/env python3
"""Qualify saved RAK Ethernet mode using the production atomic filesystem path."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]
FS_FIXTURE = ROOT / "test/fixtures/guest_login"


class EthernetModeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise unittest.SkipTest("host C++ compiler required")
        cls.work = tempfile.TemporaryDirectory(prefix="mesh-ethernet-mode-")
        folder = Path(cls.work.name)
        # Reuse the fault-injecting LittleFS fixture. Its path-specific failures
        # target this independent record, without replacing production storage.
        mocks = folder / "mocks"
        shutil.copytree(FS_FIXTURE / "mocks", mocks)
        fs_mock = mocks / "GuestMemoryFS.h"
        fs_mock.write_text(fs_mock.read_text().replace("/guest_login", "/eth_mode"))
        harness = (FS_FIXTURE / "test.cpp").read_text()
        harness = harness.replace("<helpers/GuestLoginSettings.h>",
                                  "<helpers/nrf52/EthernetModeSettings.h>")
        harness = harness.replace("using mesh::GuestLoginSettings;",
                                  "using mesh::nrf52::EthernetModeSettings;")
        harness = harness.replace("GuestLoginSettings", "EthernetModeSettings")
        harness = harness.replace("/guest_login", "/eth_mode")
        harness = harness.replace("'G', 'L', 'G', '1'", "'E', 'T', 'H', '1'")
        source = folder / "test.cpp"
        source.write_text(harness)
        cls.binary = folder / "ethernet-mode"
        sanitizer = (["-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie"]
                     if os.name != "nt" else [])
        checked = subprocess.run([
            compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
            "-Wno-misleading-indentation", "-Wno-sign-compare", *sanitizer,
            "-DNRF52_PLATFORM", "-I", str(mocks), "-I", str(ROOT / "src"),
            str(source), str(ROOT / "src/helpers/AtomicFileWriter.cpp"),
            "-o", str(cls.binary),
        ], text=True, capture_output=True, timeout=60)
        if checked.returncode:
            cls.work.cleanup()
            raise AssertionError(checked.stdout + checked.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def scenario(self, name):
        checked = subprocess.run([str(self.binary), name], text=True,
                                 capture_output=True, timeout=15)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_default_off_and_saved_on_off_survive_restart(self):
        self.scenario("basics")

    def test_malformed_records_fail_closed_and_can_be_repaired(self):
        self.scenario("malformed")

    def test_failed_writes_preserve_previous_mode(self):
        self.scenario("failed_saves")

    def test_uncertain_publication_requires_reload(self):
        self.scenario("uncertain")

    def test_restart_at_every_filesystem_boundary(self):
        self.scenario("reboot")

    def test_load_errors_prevent_false_saved_acknowledgement(self):
        self.scenario("load_errors")

    def test_all_three_roles_load_saved_mode_before_optional_hardware_start(self):
        for role in ("simple_repeater", "simple_room_server"):
            with self.subTest(role=role):
                source = (ROOT / "examples" / role / "main.cpp").read_text()
                setup = extract_braced(source, "void setup()")
                load = setup.index("ethernet_mode_settings.begin(volatile_primary_fs ? nullptr : fs)")
                configure = setup.index("ethernet_configure(hooks, ethernet_mode_settings.enabled())")
                self.assertLess(setup.index("the_mesh.begin(fs)"), load)
                self.assertLess(load, configure)
                self.assertLess(configure, setup.index("ethernet_start_task()"))
                self.assertIn("hooks.save = [](void*, bool enabled) { return ethernet_mode_settings.setEnabled(enabled); }", setup)
        source = (ROOT / "examples/companion_radio/main.cpp").read_text()
        setup = extract_braced(source, "void setup()")
        load = setup.index("ethernet_mode_settings.begin(store.isVolatilePrimaryFS() ? nullptr : store.getPrimaryFS())")
        persistence = setup.index("ethernet_interface.setModePersistenceHook(")
        begin = setup.index("ethernet_interface.begin()")
        restore = setup.index("if (ethernet_mode_settings.enabled()) (void)ethernet_interface.tryEnable()")
        self.assertLess(load, persistence)
        self.assertLess(persistence, begin)
        self.assertLess(begin, restore)
        command = extract_braced(source, "bool handleRak4631PeripheralCommand(")
        self.assertIn("ethernet_interface.tryEnable(true)", command)
        self.assertIn("ethernet_interface.tryDisable(true)", command)
        self.assertIn("saved=%s", command)


if __name__ == "__main__":
    unittest.main()
