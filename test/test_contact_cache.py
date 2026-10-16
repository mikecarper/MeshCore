#!/usr/bin/env python3
"""Exercise real contact handles, cache policy and persistence under sanitizers."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]


def crypto_objects(temp):
    objects = []
    for name in ("fe", "ge", "sc", "sha512", "keypair", "key_exchange"):
        obj = temp / (name + ".o")
        subprocess.run([
            "cc", "-O1", "-g", "-fsanitize=address", "-fwrapv", "-c",
            str(ROOT / "lib/ed25519" / (name + ".c")), "-o", str(obj),
        ], capture_output=True, text=True, check=True)
        objects.append(str(obj))
    return objects


class ContactCacheTest(unittest.TestCase):
    def test_esp32_misses_recalculate_without_flash_io(self):
        with tempfile.TemporaryDirectory(prefix="mesh-secret-power-") as temp:
            temp = Path(temp)
            binary = temp / "test"
            result = subprocess.run([
                "c++", "-std=c++17", "-O1", "-g", "-fsanitize=address,undefined",
                "-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1",
                "-I", str(ROOT / "test/fixtures/contact_cache/mocks"),
                "-I", str(ROOT / "lib/ed25519"), "-I", str(ROOT / "src"),
                str(ROOT / "test/fixtures/contact_cache/test_secret_power.cpp"),
                *crypto_objects(temp), "-lcrypto", "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_eviction_snapshots_transactions_and_identity_changes(self):
        self.run_cache_test("ESP32_PLATFORM")

    def test_nrf52_paged_store_migration_and_snapshots(self):
        self.run_cache_test("NRF52_PLATFORM")

    def test_esp32_default_crc_cadence_is_detected(self):
        self.run_cache_test("ESP32_PLATFORM", disable_crc_batch=True)

    def run_cache_test(self, platform, disable_crc_batch=False):
        with tempfile.TemporaryDirectory(prefix="mesh-contact-cache-") as temp:
            temp = Path(temp)
            store = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
            functions = [
                "static bool serializeContactRecord(",
                "static bool deserializeContactRecord(",
                "void DataStore::loadContacts(",
                "bool DataStore::saveContacts(",
                "bool DataStore::flushContactWrites(",
                "bool DataStore::readStoredPath(",
                "bool DataStore::flushCachedPaths(",
                "uint16_t DataStore::secretSlot(",
                "bool DataStore::readSavedSecret(",
                "bool DataStore::saveSecret(",
            ]
            helper_start = store.index("namespace {\nbool cachedContactFilter")
            helper_end = store.index("bool DataStore::readStoredPath(", helper_start)
            implementation = store[helper_start:helper_end]
            header = (ROOT / "examples/companion_radio/DataStore.h").read_text()
            state_start = header.index("  mesh::ContactFileTransaction* _contact_write")
            state_end = header.index("\n#endif", state_start)
            (temp / "contact_write_state_under_test.h").write_text(
                header[state_start:state_end])
            functions.append("DataStore::~DataStore()")
            if platform == "ESP32_PLATFORM":
                functions.extend((
                    "void DataStore::cancelContactWrite(",
                    "bool DataStore::cancelCooperativeWrite(",
                    "bool DataStore::serviceContactWrite(",
                    "bool DataStore::markContactDirty(",
                    "bool DataStore::releaseContact(",
                    "bool DataStore::serviceContactWrites(",
                    "bool DataStore::hasPendingContactWrites() const",
                    "void DataStore::begin()",
                    "void DataStore::disableSecondaryFS(",
                ))
            if platform == "NRF52_PLATFORM":
                implementation += method(store, "static void makeContactPagePath(") + "\n"
                implementation += method(store, "static void discardInvalidContactPage(") + "\n"
                functions.append("bool DataStore::writeContactPage(")
                functions.append("bool DataStore::loadContactPages(")
            implementation += "\n".join(method(store, signature) for signature in functions)
            if disable_crc_batch:
                self.assertIn("_contact_write->serviceCommit(true, 8, true)", implementation)
                implementation = implementation.replace(
                    "_contact_write->serviceCommit(true, 8, true)",
                    "_contact_write->serviceCommit(true, 1, true)")
            (temp / "store_under_test.h").write_text(implementation)
            packet = (ROOT / "src/Packet.cpp").read_text()
            (temp / "packet_under_test.h").write_text("namespace mesh {\n" + "\n".join(
                method(packet, signature) for signature in (
                    "Packet::Packet()", "int Packet::getRawLength() const",
                    "bool Packet::isValidPathLen(", "size_t Packet::writePath(",
                    "uint8_t Packet::copyPath(")) + "\n}\n")
            chat = (ROOT / "src/helpers/BaseChatMesh.cpp").read_text()
            mesh = (ROOT / "src/Mesh.cpp").read_text()
            (temp / "send_under_test.h").write_text(
                "namespace mesh {\n" + method(mesh, "bool Mesh::sendDirect(") + "\n}\n"
                + "\n".join(method(chat, signature) for signature in (
                    "int BaseChatMesh::sendContactPacket(",
                    "int BaseChatMesh::sendLogin(", "int BaseChatMesh::sendAnonReq(",
                    "bool BaseChatMesh::allocateRequestTag(",
                    "int  BaseChatMesh::sendRequest(const ContactInfo& recipient, const uint8_t*",
                    "int  BaseChatMesh::sendRequest(const ContactInfo& recipient, uint8_t"))
            )
            binary = temp / "test"
            result = subprocess.run([
                "c++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                "-DMESH_CONTACT_CACHE=1", "-DCOMPANION_RADIO_FULL=1",
                "-DMESH_CONTACT_SECRET_FLASH_CACHE=1",
                "-D" + platform + "=1", "-DMAX_CONTACTS=350",
                "-I", str(ROOT / "test/fixtures/contact_cache/mocks"),
                "-I", str(ROOT / "lib/ed25519"),
                "-I", str(ROOT / "src"), "-I", str(temp),
                str(ROOT / "test/fixtures/contact_cache/test_contact_cache.cpp"),
                str(ROOT / "src/helpers/ContactInfo.cpp"), *crypto_objects(temp),
                "-lcrypto", "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            if disable_crc_batch:
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("crc_read_passes == 104", result.stderr)
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('PASS: 114 shared request/login payload and routing traces', result.stdout)


if __name__ == "__main__":
    unittest.main()
