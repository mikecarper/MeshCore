"""Canonical release target sharding without invoking PlatformIO."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "build_legacy.sh"
TARGETS = ["RAK_3401_repeater", "RAK_4631_room_server", "heltec_v3_repeater",
           "heltec_v4_room_server", "Xiao_S3_WIO_repeater", "wio_l1_companion_radio_full",
           "Wio_Tracker_L1_repeater"]


def shell(body, environment=None, *, check=True):
    env = os.environ.copy()
    env.pop("MESHCORE_RELEASE_SHARD_INDEX", None)
    env.pop("MESHCORE_RELEASE_SHARD_COUNT", None)
    env.update(environment or {})
    command = 'source "$1"\n' + body
    return subprocess.run(["bash", "-c", command, "shards", str(SCRIPT)],
                          cwd=ROOT, env=env, check=check, text=True,
                          capture_output=True, timeout=15)


def targets_body():
    return "RESOLVED_BUILD_TARGETS=(" + " ".join(TARGETS) + ")\n"


class ReleaseTargetShardsTest(unittest.TestCase):
    def selected(self, index=None, count=None):
        environment = {}
        if index is not None:
            environment["MESHCORE_RELEASE_SHARD_INDEX"] = str(index)
        if count is not None:
            environment["MESHCORE_RELEASE_SHARD_COUNT"] = str(count)
        result = shell(targets_body() + "apply_release_build_shard build-firmwares-logging-matrix >/dev/null || exit $?\n"
                       "printf '%s\\n' \"${RESOLVED_BUILD_TARGETS[@]}\"\n", environment)
        return result.stdout.splitlines()

    def test_two_workers_are_disjoint_complete_and_stable(self):
        first, second = self.selected(0, 2), self.selected(1, 2)
        self.assertEqual(first, TARGETS[::2])
        self.assertEqual(second, TARGETS[1::2])
        self.assertFalse(set(first) & set(second))
        self.assertEqual(set(first) | set(second), set(TARGETS))
        self.assertEqual(first, self.selected(0, 2))

    def test_unsharded_and_one_worker_keep_every_target(self):
        self.assertEqual(self.selected(), TARGETS)
        self.assertEqual(self.selected(0, 1), TARGETS)

    def test_malformed_or_incomplete_settings_fail(self):
        invalid = [{"MESHCORE_RELEASE_SHARD_INDEX": "0"},
                   {"MESHCORE_RELEASE_SHARD_COUNT": "2"}]
        invalid += [{"MESHCORE_RELEASE_SHARD_INDEX": index,
                     "MESHCORE_RELEASE_SHARD_COUNT": count}
                    for index, count in [("", ""), ("-1", "2"), ("1", "0"),
                                         ("2", "2"), ("01", "2"), ("0", "02"),
                                         ("1+1", "3"), ("0", "1000000000")]]
        for environment in invalid:
            with self.subTest(environment=environment):
                result = shell(targets_body() + "apply_release_build_shard build-firmwares-logging-matrix\n",
                               environment, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Set both", result.stderr)

    def test_sharding_is_only_for_the_complete_logging_matrix(self):
        for command in ["build-firmware", "build-companion-firmwares-logging-matrix", "build-firmwares"]:
            with self.subTest(command=command):
                result = shell(targets_body() + "apply_release_build_shard " + command + "\n",
                               {"MESHCORE_RELEASE_SHARD_INDEX": "0", "MESHCORE_RELEASE_SHARD_COUNT": "2"},
                               check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("only with build-firmwares-logging-matrix", result.stderr)

    def test_empty_worker_is_an_error(self):
        result = shell("RESOLVED_BUILD_TARGETS=(only_target)\n"
                       "apply_release_build_shard build-firmwares-logging-matrix\n",
                       {"MESHCORE_RELEASE_SHARD_INDEX": "1", "MESHCORE_RELEASE_SHARD_COUNT": "2"},
                       check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no targets", result.stderr)

    def test_worker_ledgers_bind_shards_to_the_same_canonical_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            for index in (0, 1):
                shell(targets_body() + "apply_release_build_shard build-firmwares-logging-matrix >/dev/null\n"
                      "write_release_build_shard_ledgers\n",
                      {"MESHCORE_RELEASE_SHARD_INDEX": str(index), "MESHCORE_RELEASE_SHARD_COUNT": "2",
                       "OUTPUT_DIR": directory})
                stem = Path(directory) / f"release-shard-{index}-of-2"
                self.assertEqual(Path(str(stem) + "-targets.txt").read_text().splitlines(), TARGETS[index::2])
                self.assertEqual(Path(str(stem) + "-all-targets.txt").read_text().splitlines(), TARGETS)

    def test_main_rejects_bad_settings_before_platformio_discovery(self):
        environment = os.environ.copy()
        environment["MESHCORE_RELEASE_SHARD_INDEX"] = "0"
        environment.pop("MESHCORE_RELEASE_SHARD_COUNT", None)
        result = subprocess.run(["bash", str(SCRIPT), "build-firmwares-logging-matrix"],
                                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set both", result.stderr)
        self.assertNotIn("Processing ", result.stdout)

    def test_sharding_follows_alias_normalization_and_sorting(self):
        source = SCRIPT.read_text()
        resolver = source.split("resolve_command_targets() {", 1)[1].split("\nconfigure_effective_build_profile()", 1)[0]
        self.assertLess(resolver.index("normalize_nrf52_sensor_ota_pair_targets"),
                        resolver.index('apply_release_build_shard "$1"'))
        self.assertLess(resolver.index("sort_build_targets_by_platform_and_name"),
                        resolver.index('apply_release_build_shard "$1"'))


if __name__ == "__main__":
    unittest.main()
