#!/usr/bin/env python3
"""MediaMesh scope policy through the real CommonCLI and RegionMap methods."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cpp_source import body
from test_region_map_transactions import region_implementation

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "test/fixtures/mediamesh_scope_integration.cpp"
REGION_FIXTURE = ROOT / "test/fixtures/region_map_transactions"


def implementation():
    region = (ROOT / "src/helpers/RegionMap.cpp").read_text(encoding="ascii")
    generated = region_implementation()
    for signature in ("int RegionMap::getTransportKeysFor(",
                      "bool RegionMap::matchesScope(", "bool RegionMap::allowsUnmatchedFlood(",
                      "bool RegionMap::removeRegion(", "void RegionMap::resetFrom(",
                      "RegionEntry* RegionMap::findMatch("):
        generated += "\n" + body(region, signature)
    common = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
    generated += "\n" + body(common, "bool CommonCLI::mediaMeshRegionStoreMissing(")
    generated += "\n" + body(common, "bool CommonCLI::updateMediaMeshScope(")
    generated += "\n" + body(common, "void CommonCLI::onRegionsLoaded(")
    generated += "\n" + body(common, "void CommonCLI::recoverMediaMeshRegions(")
    repeater = (ROOT / "examples/simple_repeater/MyMesh.cpp").read_text(encoding="ascii")
    generated += "\n" + body(repeater, "void MyMesh::onDefaultRegionChanged(")
    return generated


class MediaMeshScopeIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            raise AssertionError("a native C++ compiler is required")
        cls.temporary = tempfile.TemporaryDirectory(prefix="meshcore-mediamesh-scope-")
        cls.work = Path(cls.temporary.name)
        (cls.work / "scope_under_test.inc").write_text(implementation(), encoding="ascii")
        (cls.work / "unmatched.inc").write_text(body(
            (ROOT / "src/helpers/CommonCLI.h").read_text(), "bool allowsUnmatchedFlood("))
        (cls.work / "forward.inc").write_text(body((ROOT / "src/Mesh.h").read_text(), "bool canForwardPacket("))
        (cls.work / "contact_transaction_under_test.h").write_text(
            (ROOT / "src/helpers/ContactFileTransaction.h").read_text(encoding="ascii"),
            encoding="ascii")
        (cls.work / "IdentityStore.h").write_text(
            '#include <helpers/IdentityStore.h>\n', encoding="ascii")
        (cls.work / "PersistentStoreFormat.h").write_text(
            '#include <helpers/PersistentStoreFormat.h>\n', encoding="ascii")
        # The host SHA-256 implementation computes real digests. Only adapt its
        # typed output pointers to the firmware library's void* signature.
        (cls.work / "SHA256.h").write_text(f'''
#pragma once
#define SHA256 NativeSHA256
#include "{ROOT / 'test/mocks/SHA256.h'}"
#undef SHA256
class SHA256 : public NativeSHA256 {{ public:
 void finalize(void* out, size_t length) {{
  NativeSHA256::finalize(static_cast<uint8_t*>(out), length);
 }}
 void finalizeHMAC(const uint8_t* key, size_t size, void* out, size_t length) {{
  NativeSHA256::finalizeHMAC(key, size, static_cast<uint8_t*>(out), length);
 }}
}};
''', encoding="ascii")
        cls.binaries = {}
        for platform in ("ESP32_PLATFORM", "NRF52_PLATFORM"):
            binary = cls.work / platform
            command = [
                compiler, "-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                "-Wno-unused-parameter", "-Wno-sign-compare", "-Wno-unused-function",
                "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie",
                "-D" + platform + "=1", "-I", str(cls.work),
                "-I", str(REGION_FIXTURE / "mocks"),
                "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                str(FIXTURE), str(ROOT / "src/Packet.cpp"), str(ROOT / "src/helpers/TransportKeyStore.cpp"),
                "-o", str(binary),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            if result.returncode:
                cls.temporary.cleanup()
                raise AssertionError(result.stdout + result.stderr)
            cls.binaries[platform] = binary

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_case(self, case):
        environment = os.environ.copy()
        environment["ASAN_OPTIONS"] = "detect_leaks=1:abort_on_error=1"
        environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
        for platform, binary in self.binaries.items():
            with self.subTest(platform=platform):
                result = subprocess.run([str(binary), case], capture_output=True,
                                        text=True, timeout=60, env=environment)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("PASS: " + case, result.stdout)

    def test_real_scope_key_filter_and_reboot_persistence(self):
        self.run_case("scope-filter")

    def test_unknown_forwarding_and_blackhole_sink(self):
        self.run_case("unknown-forward")

    def test_opt_in_unscoped_wildcard_text_exception(self):
        self.run_case("wildcard-except-text")

    def test_existing_hierarchy_and_nonflood_policy_are_preserved(self):
        self.run_case("existing-policy")

    def test_full_region_table_preflight_and_missing_scope_disable(self):
        self.run_case("full-table")

    def test_save_failures_retry_even_when_ram_flags_already_match(self):
        self.run_case("save-retry")

    def test_invalid_callback_context_is_refused(self):
        self.run_case("invalid-context")

    def test_unreadable_existing_regions_are_held_then_reloaded_automatically(self):
        self.run_case("unreadable-boot")

    def test_malformed_regions_and_transaction_siblings_are_not_overwritten(self):
        self.run_case("malformed-boot")

    def test_metadata_errors_cannot_look_like_a_fresh_region_store(self):
        self.run_case("metadata-boot")

    def test_all_infrastructure_roles_bind_after_loading_regions(self):
        for directory, filename, classname in (
                ("simple_repeater", "MyMesh.cpp", "MyMesh"),
                ("simple_room_server", "MyMesh.cpp", "MyMesh"),
                ("simple_sensor", "SensorMesh.cpp", "SensorMesh")):
            with self.subTest(role=directory):
                source = (ROOT / "examples" / directory / filename).read_text(encoding="ascii")
                begin = body(source, "void " + classname + "::begin(")
                self.assertLess(begin.index("_cli.loadPrefs(_fs)"),
                                begin.index("region_map.load(_fs)"))
                self.assertLess(begin.index("region_map.load(_fs)"),
                                begin.index("_cli.onRegionsLoaded(_fs, regions_loaded)"))
                self.assertIn("const bool regions_loaded = region_map.load(_fs);", begin)
        common = (ROOT / "src/helpers/CommonCLI.cpp").read_text(encoding="ascii")
        loop = body(common, "void CommonCLI::loop()")
        guarded = body(loop, "if (!mesh::morseTimingActive(")
        self.assertIn("recoverMediaMeshRegions();", guarded)


if __name__ == "__main__":
    unittest.main()
