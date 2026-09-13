#!/usr/bin/env python3
"""A previously passing RAM/capability manifest cannot bypass a new contract."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FullLoggingResumeTest(unittest.TestCase):
    def check_cached(self, source, *, packet_present=True, logging_required=True):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            stem = "Station_G2_repeater_observer_mqtt-full-usb-wifi-ota-vtest"
            checks = [{"capability": "logging.usb.control", "evidence": "USB control", "present": True},
                      {"capability": "logging.usb.packets", "evidence": "packet logger", "present": packet_present}]
            if source is not None:
                for check in checks:
                    check["source"] = source
            manifest = {"schema_version": 2, "verified": True, "verification": checks}
            (directory / (stem + ".capabilities.json")).write_text(json.dumps(manifest))
            (directory / (stem + ".bin")).write_bytes(b"firmware")
            (directory / (stem + "-merged.bin")).write_bytes(b"merged firmware")
            proof = dict(schema_version=1, passed=True, available_internal_bytes=80000,
                         required_heap_bytes=50000, largest_internal_region_bytes=80000,
                         required_contiguous_bytes=5120, elf_sha256="a" * 64,
                         files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()})
            (directory / (stem + ".memory.json")).write_text(json.dumps(proof))
            command = '''
source build.sh
OUTPUT_DIR="$1"
BUILD_APPLICATION_EXPECTATIONS=()
if [ "$3" = yes ]; then
  BUILD_APPLICATION_EXPECTATIONS=('logging.usb.control=USB control' 'logging.usb.packets=packet logger')
fi
build_artifacts_exist ESP32_PLATFORM "$2"
'''
            return subprocess.run(["bash", "-c", command, "resume-test", str(directory), stem,
                                   "yes" if logging_required else "no"], cwd=ROOT,
                                  text=True, capture_output=True)

    def test_old_and_elf_only_manifests_force_a_rebuild(self):
        for source in (None, "linked image"):
            with self.subTest(source=source):
                result = self.check_cached(source)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_verified_packaged_logger_can_resume(self):
        result = self.check_cached("packaged application")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_packet_logger_cannot_resume(self):
        result = self.check_cached("packaged application", packet_present=False)
        self.assertNotEqual(result.returncode, 0)

    def test_other_profiles_keep_their_existing_resume_policy(self):
        result = self.check_cached(None, logging_required=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
