#!/usr/bin/env python3
"""Run the uncached Companion persistence paths against faulting filesystems."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


class CompanionUncachedStorageTest(unittest.TestCase):
    def test_esp32_uncached_storage(self):
        self.run_platform("ESP32_PLATFORM")

    def test_stm32_uncached_storage(self):
        self.run_platform("STM32_PLATFORM")

    def test_rp2040_uncached_storage(self):
        self.run_platform("RP2040_PLATFORM")

    def run_platform(self, platform):
        source = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        signatures = (
            "static bool companionPathPresence(",
            "bool DataStore::loadMainIdentity(",
            "bool DataStore::canCreateMainIdentity() const",
            "bool DataStore::saveMainIdentity(",
            "static bool deserializeContactRecord(",
            "void DataStore::loadContacts(",
            "bool DataStore::saveContacts(",
            "bool DataStore::hasIncompleteContactLoad() const",
        )
        packet = (ROOT / "src/Packet.cpp").read_text()
        generated = "namespace mesh {\n" + extract_braced(
            packet, "bool Packet::isValidPathLen(") + "\n}\n"
        generated += "\n".join(extract_braced(source, s) for s in signatures)
        with tempfile.TemporaryDirectory(prefix="mesh-uncached-store-") as temp:
            temp = Path(temp)
            (temp / "store_under_test.h").write_text(generated)
            # IdentityStore.h only needs the platform filesystem declaration;
            # the concrete mock below implements the same file API.
            (temp / "FS.h").write_text(
                "#pragma once\nnamespace fs { using FS = FakeFilesystem; }\n"
            )
            binary = temp / "test"
            compiled = subprocess.run([
                "c++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra",
                "-Werror", "-Wno-unused-parameter", "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                "-DMESH_CONTACT_CACHE=0", "-D" + platform + "=1",
                "-I", str(temp),
                "-I", str(ROOT / "test/fixtures/contact_cache/mocks"),
                "-I", str(ROOT / "src"),
                "-I", str(ROOT / "lib/ed25519"),
                str(ROOT / "test/fixtures/companion_uncached_storage/test.cpp"),
                "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
