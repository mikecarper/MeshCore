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

    def run_platform(self, platform, fixture_transform=None, expected_failure=None):
        source = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        signatures = [
            "static bool companionPathPresence(",
            "static bool readableContactFile(",
            "const char* DataStore::selectContactFile(",
            "bool DataStore::prepareContactSave(",
            "void DataStore::clearContactLoadFailure(",
            "void DataStore::recoverContacts(",
            "bool DataStore::loadMainIdentity(",
            "bool DataStore::canCreateMainIdentity() const",
            "bool DataStore::saveMainIdentity(",
            "static bool deserializeContactRecord(",
            "void DataStore::loadContacts(",
            "bool DataStore::saveContacts(",
            "bool DataStore::hasIncompleteContactLoad() const",
        ]
        if platform == "ESP32_PLATFORM":
            signatures.extend((
                "static bool serializeContactRecord(",
                "DataStore::~DataStore()",
                "void DataStore::cancelContactWrite(",
                "bool DataStore::serviceContactWrite(",
                "bool DataStore::markContactDirty(",
                "bool DataStore::releaseContact(",
                "bool DataStore::serviceContactWrites(",
                "bool DataStore::flushContactWrites(",
                "bool DataStore::hasPendingContactWrites() const",
            ))
            # The serializer must be declared before save/service uses it.
            signatures.insert(0, signatures.pop(signatures.index(
                "static bool serializeContactRecord(")))
        packet = (ROOT / "src/Packet.cpp").read_text()
        generated = "namespace mesh {\n" + extract_braced(
            packet, "bool Packet::isValidPathLen(") + "\n}\n"
        generated += "\n".join(extract_braced(source, s) for s in signatures)
        with tempfile.TemporaryDirectory(prefix="mesh-uncached-store-") as temp:
            temp = Path(temp)
            (temp / "store_under_test.h").write_text(generated)
            header = (ROOT / "examples/companion_radio/DataStore.h").read_text()
            start = header.index("  // Contact-only recovery")
            end = header.index("#if defined(ESP32_PLATFORM)", start)
            (temp / "contact_recovery_state_under_test.h").write_text(header[start:end])
            (temp / "contact_recovery_api_under_test.h").write_text("\n".join(
                extract_braced(header, signature) for signature in (
                    "  bool contactRecoveryPending() const",
                    "  bool contactsAreVolatile() const")))
            start = header.index("  mesh::ContactFileTransaction* _contact_write")
            end = header.index("\n#endif", start)
            (temp / "contact_write_state_under_test.h").write_text(header[start:end])
            # IdentityStore.h only needs the platform filesystem declaration;
            # the concrete mock below implements the same file API.
            (temp / "FS.h").write_text(
                "#pragma once\nnamespace fs { using FS = FakeFilesystem; }\n"
            )
            (temp / "InternalFileSystem.h").write_text(
                "#pragma once\n#define InternalFS filesystem\n")
            binary = temp / "test"
            fixture = ROOT / "test/fixtures/companion_uncached_storage/test.cpp"
            if fixture_transform is not None:
                fixture = fixture_transform(temp)
            compiled = subprocess.run([
                "c++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra",
                "-Werror", "-Wno-unused-parameter", "-fsanitize=address,undefined", "-fno-pie", "-no-pie",
                "-DMESH_CONTACT_CACHE=0", "-D" + platform + "=1",
                "-I", str(temp),
                "-I", str(ROOT / "test/fixtures/contact_cache/mocks"),
                "-I", str(ROOT / "src"),
                "-I", str(ROOT / "lib/ed25519"),
                str(fixture),
                "-o", str(binary),
            ], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            if expected_failure is None:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            else:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_failure, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
