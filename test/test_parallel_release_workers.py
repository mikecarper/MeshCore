#!/usr/bin/env python3
"""Exercise release worker isolation and merge admission without PlatformIO."""

import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import parallel_release_workers as workers


VERSION = "v1.18.0.2-halo-keymind-cascade-dev"
SOURCE = "a" * 40


class ParallelReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="parallel-release-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def specifications(self):
        pool = self.root / "pool"
        result = []
        for index in range(2):
            output = pool / f"output-{index}"
            output.mkdir(parents=True)
            result.append({"index": index, "checkout": self.root,
                           "output": output, "embedded": pool / f"embedded-{index}",
                           "environment": {"PATH": "/usr/bin"}})
        return result

    def ledgers(self, specs):
        all_targets = ["a", "b", "c", "d"]
        for spec in specs:
            prefix = spec["output"] / f"release-shard-{spec['index']}-of-2"
            Path(str(prefix) + "-all-targets.txt").write_text("\n".join(all_targets) + "\n")
            Path(str(prefix) + "-targets.txt").write_text("\n".join(all_targets[spec['index']::2]) + "\n")

    def test_each_worker_has_independent_source_core_build_dependencies_and_output(self):
        def add_checkout(command, **_kwargs):
            if command[:3] == ["git", "worktree", "add"]:
                Path(command[-2]).mkdir(parents=True)
            return subprocess.CompletedProcess(command, 0)
        with mock.patch.object(workers, "_validate_source"), \
                mock.patch.object(workers, "_seed_core") as seed, \
                mock.patch.object(workers.subprocess, "run", side_effect=add_checkout), \
                mock.patch.object(workers.shutil, "which", return_value="/usr/bin/pio"):
            specs = workers._prepare_workers(self.root, VERSION, SOURCE, 2, 2,
                                              {"PATH": "/usr/bin", "PLATFORMIO_CORE_DIR": str(self.root / "seed"),
                                               "MESHCORE_RELEASE_SHARD_INDEX": "9"})
        for name in ("checkout", "output", "embedded"):
            self.assertNotEqual(specs[0][name], specs[1][name])
        for name in ("PLATFORMIO_CORE_DIR", "PLATFORMIO_BUILD_DIR", "PLATFORMIO_LIBDEPS_DIR", "OUTPUT_DIR"):
            self.assertNotEqual(specs[0]["environment"][name], specs[1]["environment"][name])
        self.assertEqual(seed.call_count, 2)
        self.assertNotIn("MESHCORE_RELEASE_SHARD_INDEX", specs[0]["environment"])
        wrapper = Path(specs[0]["environment"]["PATH"].split(":")[0]) / "pio"
        self.assertIn("worker_pio_main", wrapper.read_text())

    def test_core_seed_copies_never_hardlinks_and_refuses_active_platformio(self):
        source = self.root / "source"
        (source / "packages").mkdir(parents=True)
        destination = self.root / "core"
        with mock.patch.object(workers, "_active_platformio", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "active"):
                workers._seed_core(source, destination)
        with mock.patch.object(workers, "_active_platformio", return_value=False), \
                mock.patch.object(workers.subprocess, "run") as run:
            workers._seed_core(source, destination)
        self.assertIn("--reflink=auto", run.call_args.args[0])
        self.assertNotIn("-l", run.call_args.args[0])
        self.assertTrue((destination / ".meshcore-seeded.json").is_file())

    def test_seeded_packages_can_be_modified_without_modifying_originals(self):
        source = self.root / "source"
        (source / "packages/framework").mkdir(parents=True)
        original = source / "packages/framework/package.json"
        original.write_text("original")
        destination = self.root / "core"
        with mock.patch.object(workers, "_active_platformio", return_value=False):
            workers._seed_core(source, destination)
        copy = destination / "packages/framework/package.json"
        self.assertNotEqual(copy.stat().st_ino, original.stat().st_ino)
        copy.write_text("worker change")
        self.assertEqual(original.read_text(), "original")

    def test_shard_ledgers_must_be_complete_disjoint_and_same_canonical_order(self):
        specs = self.specifications()
        self.ledgers(specs)
        workers._validate_shards(specs)
        path = specs[1]["output"] / "release-shard-1-of-2-targets.txt"
        path.write_text("a\nc\n")
        with self.assertRaisesRegex(ValueError, "selection"):
            workers._validate_shards(specs)

    def test_merge_preserves_equal_exclusions_and_namespaces_logs(self):
        specs = self.specifications()
        for spec in specs:
            output = spec["output"]
            (output / "ota-excluded-targets.txt").write_text("stm32\n")
            (output / "build-logs").mkdir()
            (output / "build-logs" / "same.log").write_text(str(spec["index"]))
            (output / f"board-{spec['index']}.bin").write_bytes(bytes([spec["index"]]))
        destination = self.root / "merged"
        workers.merge_worker_outputs([spec["output"] for spec in specs], destination)
        self.assertEqual((destination / "build-logs/worker-1/same.log").read_text(), "1")
        self.assertEqual((destination / "ota-excluded-targets.txt").read_text(), "stm32\n")

    def test_merge_rejects_conflict_or_symlink_before_copying_any_package(self):
        specs = self.specifications()
        for spec in specs:
            (spec["output"] / "same.bin").write_bytes(bytes([spec["index"]]))
        destination = self.root / "merged"
        with self.assertRaisesRegex(ValueError, "conflicting"):
            workers.merge_worker_outputs([spec["output"] for spec in specs], destination)
        self.assertFalse(destination.exists())
        (specs[1]["output"] / "same.bin").unlink()
        (specs[1]["output"] / "unsafe").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "symlink"):
            workers.merge_worker_outputs([spec["output"] for spec in specs], destination)
        self.assertFalse(destination.exists())

    def test_matrix_failure_does_not_merge_any_partial_output(self):
        specs = self.specifications()
        with mock.patch.object(workers, "_prepare_workers", return_value=specs), \
                mock.patch.object(workers, "_run_workers", side_effect=RuntimeError("worker failed")), \
                mock.patch.object(workers, "merge_worker_outputs") as merge:
            with self.assertRaisesRegex(RuntimeError, "failed"):
                workers.build_parallel_matrix(self.root, VERSION, SOURCE, 2, 2, {}, self.root / "merged")
        merge.assert_not_called()

    def test_matrix_assigns_shards_but_embedded_commands_do_not_inherit_them(self):
        specs = self.specifications()
        self.ledgers(specs)
        def run_matrix(selected, command, phase):
            self.assertEqual(phase, "matrix")
            for spec in selected:
                self.assertEqual(spec["environment"]["MESHCORE_RELEASE_SHARD_INDEX"], str(spec["index"]))
                self.assertIn("build-firmwares-logging-matrix", command(spec))
        with mock.patch.object(workers, "_prepare_workers", return_value=specs), \
                mock.patch.object(workers, "_run_workers", side_effect=run_matrix):
            workers.build_parallel_matrix(self.root, VERSION, SOURCE, 2, 2, {}, self.root / "merged")
        for spec in specs:
            spec["environment"].pop("MESHCORE_RELEASE_SHARD_INDEX")
            spec["environment"].pop("MESHCORE_RELEASE_SHARD_COUNT")
        def run_embedded(selected, command, phase):
            self.assertEqual(phase, "embedded")
            for spec in selected:
                self.assertNotIn("MESHCORE_RELEASE_SHARD_INDEX", spec["environment"])
                self.assertIn("build_embedded_bootloader_artifacts", command(spec)[2])
        with mock.patch.object(workers, "_prepare_workers", return_value=specs), \
                mock.patch.object(workers, "_run_workers", side_effect=run_embedded):
            workers.build_parallel_embedded(self.root, VERSION, SOURCE, 2, 2, {},
                                            self.root / "merged", self.root / "embedded")

    def test_worker_failure_stops_and_reaps_siblings(self):
        specs = self.specifications()
        first = mock.Mock(returncode=None)
        first.poll.return_value = None
        second = mock.Mock(returncode=1)
        second.poll.return_value = 1
        with mock.patch.object(workers.subprocess, "Popen", side_effect=[first, second]), \
                mock.patch.object(workers, "_stop") as stop:
            with self.assertRaisesRegex(RuntimeError, "failed"):
                workers._run_workers(specs, lambda _: ["unused"], "matrix")
        self.assertCountEqual(stop.call_args_list, [mock.call(first), mock.call(second)])

    def test_sigterm_cleans_worker_groups_and_restores_previous_handler(self):
        specs = self.specifications()
        process = mock.Mock(returncode=None)
        process.poll.return_value = None
        previous = signal.getsignal(signal.SIGTERM)
        def terminate(_seconds):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        with mock.patch.object(workers.subprocess, "Popen", return_value=process), \
                mock.patch.object(workers.time, "sleep", side_effect=terminate), \
                mock.patch.object(workers, "_stop") as stop:
            with self.assertRaises(KeyboardInterrupt):
                workers._run_workers(specs, lambda _: ["unused"], "matrix")
        self.assertEqual(stop.call_count, 2)
        self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_stop_kills_descendants_even_when_group_leader_already_exited(self):
        process = mock.Mock(pid=12345)
        process.poll.return_value = 1
        with mock.patch.object(workers.os, "killpg") as kill:
            workers._stop(process)
        self.assertEqual([call.args[0] for call in kill.call_args_list], [12345, 12345])
        process.wait.assert_called()

    def test_cache_eviction_preserves_active_environment_and_symlinks(self):
        build, libdeps = self.root / "build", self.root / "libdeps"
        for parent in (build, libdeps):
            (parent / "active").mkdir(parents=True)
            (parent / "old").mkdir()
        (build / "linked").symlink_to(libdeps / "active", target_is_directory=True)
        with mock.patch.object(workers.shutil, "disk_usage", return_value=mock.Mock(free=0)):
            removed = workers.cleanup_worker_caches(build, libdeps, {"active"}, minimum_free=1)
        self.assertEqual(len(removed), 2)
        self.assertTrue((build / "active").is_dir())
        self.assertTrue((libdeps / "active").is_dir())
        self.assertTrue((build / "linked").is_symlink())


if __name__ == "__main__":
    unittest.main()
