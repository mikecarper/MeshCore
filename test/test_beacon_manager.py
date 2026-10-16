"""Exercise production beacon CLI, scheduling, and transactional persistence.

The preset/modem helpers are production code; AES is deliberately not used by
this fixture. Real-AES wire vectors have their separate meshtext codec suite.
"""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from cpp_source import body

ROOT = Path(__file__).resolve().parents[1]


class BeaconManagerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise AssertionError("a C++ compiler is required")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-beacon-manager-")
        cls.exe = Path(cls.work.name) / "manager"
        command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                   *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                     if sys.platform.startswith("linux") else []),
                   "-DMESH_ENABLE_TX_BEACONS=1", "-DMESH_ENABLE_MESHTEXT_CHAT=1",
                   "-DMESH_ENABLE_MESHTEXT_WIRE=1",
                   "-I", str(ROOT / "test/fixtures/beacons/mocks"),
                   "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                   "-isystem", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                   str(ROOT / "src/helpers/BeaconManager.cpp"),
                   str(ROOT / "src/helpers/MeshtextChat.cpp"),
                   str(ROOT / "test/fixtures/beacons/manager_test.cpp"), "-o", str(cls.exe)]
        cls.build_command = command
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def run_scenario(self, scenario):
        result = subprocess.run([str(self.exe), scenario], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_commands_and_bounds(self):
        self.run_scenario("commands")

    def test_all_intervals_and_wrap(self):
        self.run_scenario("schedule")

    def test_rolling_guards_and_no_retries(self):
        self.run_scenario("rolling")

    def test_every_config_short_write_and_recovery(self):
        self.run_scenario("storage")

    def test_structural_config_corruption_remains_held(self):
        self.run_scenario("structural_storage")

    def test_durable_meshtext_ids_fail_closed(self):
        self.run_scenario("ids")

    def test_only_committed_mutations_cancel_queued_frames(self):
        self.run_scenario("mutations")

    def test_missing_active_counter_never_rewinds_to_backup(self):
        self.run_scenario("stale_backup")

    def test_failed_live_promotion_never_restores_an_unrelated_backup(self):
        self.run_scenario("live_backup")

    def negative_control(self, name, source, scenario):
        generated = Path(self.work.name) / (name + ".cpp")
        generated.write_text(source, encoding="ascii")
        executable = Path(self.work.name) / name
        command = list(self.build_command)
        command[command.index(str(ROOT / "src/helpers/BeaconManager.cpp"))] = str(generated)
        command[-1] = str(executable)
        command[1:1] = ["-I", str(ROOT / "src/helpers")]
        compiled = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
        result = subprocess.run([str(executable), scenario], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, "old behavior unexpectedly passed the regression")
        self.assertIn("Assertion", result.stderr)

    def test_negative_control_uncancelled_old_frame_is_detected(self):
        source = (ROOT / "src/helpers/BeaconManager.cpp").read_text(encoding="ascii")
        cancellation = "if (mesh_) mesh_->cancelBeaconRadioFrame();"
        self.assertEqual(source.count(cancellation), 4)
        self.negative_control("uncancelled", source.replace(cancellation, ""), "mutations")

    def test_negative_control_counter_backup_rewind_is_detected(self):
        source = (ROOT / "src/helpers/BeaconManager.cpp").read_text(encoding="ascii")
        held = """      // Missing active metadata does not prove an interrupted rename. A
      // stale backup can survive a cleanup failure after a newer ID was
      // transmitted. Never rewind to it, even before the first MT setting.
      ids_hold_ = true;"""
        restored = """      if (result != ReadResult::Missing || !fs_->rename(IdBackup, IdPath)) ids_hold_ = true;
      else result = ReadResult::Valid;"""
        self.assertEqual(source.count(held), 1)
        self.negative_control("rewound", source.replace(held, restored), "stale_backup")

    def test_negative_control_live_counter_backup_rewind_is_detected(self):
        source = (ROOT / "src/helpers/BeaconManager.cpp").read_text(encoding="ascii")
        guard = "if (active_backed_up && moveFile(fs_, backup, path) != FileMove::Moved)"
        self.assertEqual(source.count(guard), 1)
        self.negative_control("live_rewound", source.replace(
            guard, "if ((active_backed_up || backup_present) && moveFile(fs_, backup, path) != FileMove::Moved)"),
            "live_backup")


class BeaconNamespaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            raise AssertionError("a C++ compiler is required")
        cls.work = tempfile.TemporaryDirectory(prefix="meshcore-beacon-namespace-")
        cls.addClassCleanup(cls.work.cleanup)
        cls.exe = Path(cls.work.name) / "namespace"
        # nRF's real filePresence path exposes metadata errors. Both replacing
        # (LittleFS/Pico) and nonreplacing (SPIFFS) rename models run in C++.
        cls.command = [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                       *(["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                         if sys.platform.startswith("linux") else []),
                       "-DNRF52_PLATFORM=1", "-DMESH_ENABLE_TX_BEACONS=1",
                       "-DMESH_ENABLE_MESHTEXT_CHAT=1", "-DMESH_ENABLE_MESHTEXT_WIRE=1",
                       "-I", str(ROOT / "test/fixtures/beacons/namespace_mocks"),
                       "-I", str(ROOT / "test/fixtures/beacons/mocks"),
                       "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                       "-isystem", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                       "-I", str(ROOT / "src/helpers"),
                       str(ROOT / "src/helpers/BeaconManager.cpp"),
                       str(ROOT / "src/helpers/MeshtextChat.cpp"),
                       str(ROOT / "test/fixtures/beacons/namespace_test.cpp"), "-o", str(cls.exe)]
        result = subprocess.run(cls.command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def run_scenario(self, scenario, executable=None):
        result = subprocess.run([str(executable or self.exe), scenario], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_healthy_publications_and_id_reservations(self):
        self.run_scenario("healthy")

    def test_config_acknowledgement_follows_observed_publication(self):
        self.run_scenario("config")

    def test_uncertain_moves_preserve_authorities_and_cancel_admitted_old_frame(self):
        self.run_scenario("uncertainty")

    def test_rollback_does_not_overwrite_an_uncertain_candidate(self):
        self.run_scenario("rollback")

    def test_startup_backup_recovery_confirms_namespace_moves(self):
        self.run_scenario("recovery")

    def test_meshtext_ids_are_reserved_only_after_observed_publication(self):
        self.run_scenario("ids")

    def test_rename_return_only_negative_control_is_detected(self):
        source = (ROOT / "src/helpers/BeaconManager.cpp").read_text(encoding="ascii")
        original = body(source, "bool BeaconManager::commit(")
        old = r'''bool BeaconManager::commit(const char* path, const char* backup,
                           const char* temporary, bool& hold) {
  bool active_present = false, backup_present = false;
  bool active_backed_up = false;
  if (!filePresence(fs_, path, active_present) || !filePresence(fs_, backup, backup_present)) return false;
  if (active_present) {
    if (backup_present && !fs_->remove(backup)) return false;
    if (!fs_->rename(path, backup)) return false;
    active_backed_up = true;
    backup_present = true;
  }
  if (!fs_->rename(temporary, path)) {
    if (active_backed_up && !fs_->rename(backup, path)) hold = true;
    return false;
  }
  if (backup_present) fs_->remove(backup);
  return true;
}'''
        self.assertNotEqual(original, old)
        generated = Path(self.work.name) / "rename_return_only.cpp"
        generated.write_text(source.replace(original, old), encoding="ascii")
        executable = Path(self.work.name) / "rename_return_only"
        command = list(self.command)
        command[command.index(str(ROOT / "src/helpers/BeaconManager.cpp"))] = str(generated)
        command[-1] = str(executable)
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_scenario("healthy", executable)
        result = subprocess.run([str(executable), "config"], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, "rename-return-only behavior escaped the matrix")
        self.assertIn("Assertion", result.stderr)


if __name__ == "__main__":
    unittest.main()
