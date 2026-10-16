#!/usr/bin/env python3
"""Run production ESP contact transactions with inline paths, including PSRAM."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]


class UncachedESPContactsTest(unittest.TestCase):
    def test_actual_psram_cache_policy_and_cooperative_durability(self):
        self.run_fixture(["-DBOARD_HAS_PSRAM=1"])

    def test_explicit_uncached_qualification_configuration(self):
        self.run_fixture(["-DMESH_CONTACT_CACHE=0"])

    def test_default_stdio_writer_batch_is_detected(self):
        self.run_fixture(["-DBOARD_HAS_PSRAM=1"], disable="writer")

    def test_default_stdio_reader_readahead_is_detected(self):
        self.run_fixture(["-DBOARD_HAS_PSRAM=1"], disable="reader")

    def test_synchronous_startup_batch_is_detected(self):
        self.run_fixture(["-DBOARD_HAS_PSRAM=1"], disable="begin")

    def test_default_background_crc_cadence_is_detected(self):
        self.run_fixture(["-DBOARD_HAS_PSRAM=1"], disable="crc_batch")

    def test_disabled_contact_recovery_is_detected(self):
        self.run_fixture(["-DBOARD_HAS_PSRAM=1"], disable="recovery")

    def test_bounded_buffers_reduce_backend_work_without_stack_growth(self):
        previous = self.run_fixture(["-DBOARD_HAS_PSRAM=1"], benchmark="previous")
        current = self.run_fixture(["-DBOARD_HAS_PSRAM=1"], benchmark="current")
        for result in (previous, current):
            self.assertEqual(result["bytes"], 347 * 152)
            self.assertEqual(result["logical_writes"], 347)
            self.assertEqual(result["logical_reads"], 825)
        self.assertEqual(previous["backend_writes"], 347)
        self.assertEqual(current["backend_writes"], 211)
        self.assertEqual(previous["backend_reads"], 825)
        self.assertEqual(current["backend_reads"], 413)
        self.assertEqual(current["max_backend_write"], 251)
        self.assertEqual(current["max_backend_read"], 128)
        self.assertEqual(current["commit_passes"], previous["commit_passes"])
        self.assertEqual(current["transaction_size"], previous["transaction_size"])
        # Actual production methods are compiled out of line with identical
        # options. Buffer changes must not increase their native stack frames.
        self.assertEqual(current["stack_frames"], previous["stack_frames"])
        self.assertTrue(any("serviceCommit" in method for method in current["stack_frames"]))
        self.assertTrue(any("serviceBegin" in method for method in current["stack_frames"]))

    def run_fixture(self, policy, disable=None, benchmark=None):
        with tempfile.TemporaryDirectory(prefix="mesh-esp-inline-contacts-") as directory:
            temp = Path(directory)
            source = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
            header = (ROOT / "examples/companion_radio/DataStore.h").read_text()
            recovery_start = header.index("  // Contact-only recovery")
            recovery_end = header.index("#if defined(ESP32_PLATFORM)", recovery_start)
            (temp / "contact_recovery_state_under_test.h").write_text(
                header[recovery_start:recovery_end])
            (temp / "contact_recovery_api_under_test.h").write_text(
                method(header, "  bool contactRecoveryPending() const") + "\n" +
                method(header, "  bool contactsAreVolatile() const"))
            start = header.index("  mesh::ContactFileTransaction* _contact_write")
            end = header.index("\n#endif", start)
            (temp / "contact_write_state_under_test.h").write_text(header[start:end])
            signatures = (
                "static bool serializeContactRecord(",
                "static bool deserializeContactRecord(",
                "static bool readableContactFile(",
                "const char* DataStore::selectContactFile(",
                "bool DataStore::prepareContactSave(",
                "void DataStore::clearContactLoadFailure(",
                "void DataStore::recoverContacts(",
                "DataStore::~DataStore()",
                "void DataStore::cancelContactWrite(",
                "bool DataStore::serviceContactWrite(",
                "bool DataStore::markContactDirty(",
                "bool DataStore::releaseContact(",
                "bool DataStore::serviceContactWrites(",
                "bool DataStore::saveContacts(",
                "bool DataStore::flushContactWrites(",
                "bool DataStore::hasPendingContactWrites() const",
                "bool DataStore::hasIncompleteContactLoad() const",
                "void DataStore::loadContacts(",
                "inline void makeBlobPath(",
            )
            implementation = "\n".join(method(source, signature) for signature in signatures)
            if disable == "recovery":
                recovery = method(source, "void DataStore::recoverContacts(")
                self.assertIn(recovery, implementation)
                implementation = implementation.replace(
                    recovery, "void DataStore::recoverContacts(DataStoreHost*) {}")
            elif disable == "begin":
                self.assertIn('"/contacts3", companionPathPresence, true)', implementation)
                implementation = implementation.replace(
                    '"/contacts3", companionPathPresence, true)',
                    '"/contacts3", companionPathPresence, false)')
            elif disable == "crc_batch":
                anchor = "_contact_write->serviceCommit(true, 8, true)"
                self.assertEqual(implementation.count(anchor), 1)
                implementation = implementation.replace(
                    anchor, "_contact_write->serviceCommit(true, 1, true)")
            # Only the native SDK stat seam is substituted. The production
            # presence logic still distinguishes ENOENT from metadata failure.
            presence = method(source, "static bool companionPathPresence(")
            presence = presence.replace("::stat(vfs_path, &info)",
                                        "fixtureStat(fs, vfs_path, &info)")
            (temp / "presence_under_test.h").write_text(presence)
            # NRF/STM and ESP implementations have the same public signature.
            delete_start = source.rindex("bool DataStore::deleteBlobByKey(")
            implementation += "\n" + method(source[delete_start:],
                                            "bool DataStore::deleteBlobByKey(")
            utils = (ROOT / "src/Utils.cpp").read_text()
            implementation += "\nnamespace mesh {\n"
            implementation += 'static const char hex_chars[] = "0123456789ABCDEF";\n'
            implementation += method(utils, "void Utils::toHex(") + "\n}\n"
            (temp / "store_under_test.h").write_text(implementation)
            transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text()
            if benchmark == "previous":
                self.assertIn("ESP_WRITE_BUFFER_SIZE = 251;", transaction)
                self.assertIn("ESP_VERIFY_BUFFER_SIZE = 128;", transaction)
                transaction = transaction.replace("ESP_WRITE_BUFFER_SIZE = 251;",
                                                  "ESP_WRITE_BUFFER_SIZE = 152;")
                transaction = transaction.replace("ESP_VERIFY_BUFFER_SIZE = 128;",
                                                  "ESP_VERIFY_BUFFER_SIZE = 64;")
            if disable == "writer":
                anchor = ("ok = _file.setBufferSize(background_contact_write\n"
                          "            ? ESP_BACKGROUND_WRITE_BUFFER_SIZE : ESP_WRITE_BUFFER_SIZE);")
                self.assertEqual(transaction.count(anchor), 1)
                transaction = transaction.replace(
                    anchor,
                    "ok = true; // Negative control: retain default writer buffer.")
            elif disable == "reader":
                anchor = ("ok = ok && _verify.setBufferSize(\n"
                          "          max_verify_chunks >= 8 ? 512 : ESP_VERIFY_BUFFER_SIZE);")
                self.assertEqual(transaction.count(anchor), 1)
                transaction = transaction.replace(anchor,
                    "// Negative control: retain the SDK's default reader buffer.")
            transaction = transaction.replace('#include "IdentityStore.h"',
                                              '#include <helpers/IdentityStore.h>')
            transaction = transaction.replace('#include "PersistentStoreFormat.h"',
                                              '#include <helpers/PersistentStoreFormat.h>')
            (temp / "transaction_under_test.h").write_text(transaction)
            fixture = (ROOT / "test/fixtures/contact_persistence/test_esp32_uncached.cpp").read_text()
            fixture = fixture.replace('#include <helpers/ContactFileTransaction.h>',
                                      '#include "transaction_under_test.h"')
            (temp / "test.cpp").write_text(fixture)
            packet = (ROOT / "src/Packet.cpp").read_text()
            (temp / "packet_under_test.h").write_text("namespace mesh {\n" + "\n".join(
                method(packet, signature) for signature in (
                    "bool Packet::isValidPathLen(", "size_t Packet::writePath(",
                    "uint8_t Packet::copyPath("))
                + "\n}\n")
            binary = temp / "test"
            benchmark_options = (["-DCONTACT_BUFFER_BENCHMARK=1", "-fstack-usage", "-fno-inline"]
                                 if benchmark else [])
            result = subprocess.run([
                "c++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                "-DESP32_PLATFORM=1", "-DCOMPANION_RADIO_FULL=1", *policy, *benchmark_options,
                "-I", str(ROOT / "test/fixtures/contact_cache/mocks"),
                "-I", str(ROOT / "test/fixtures/contact_cache"),
                "-I", str(ROOT / "lib/ed25519"), "-I", str(ROOT / "src"),
                "-I", str(temp),
                str(temp / "test.cpp"),
                "-lcrypto", "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            if disable is None:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if benchmark:
                    measurements = json.loads(result.stdout)
                    frames = {}
                    for usage in temp.glob("*.su"):
                        for line in usage.read_text().splitlines():
                            fields = line.split("\t")
                            if len(fields) == 3 and "ContactFileTransaction::" in fields[0]:
                                # Strip the generated path/line prefix; all
                                # method frames (including CRC verification)
                                # must remain unchanged between both builds.
                                name = fields[0].split("mesh::ContactFileTransaction", 1)[1]
                                frames[name] = [int(fields[1]), fields[2]]
                    self.assertTrue(frames)
                    measurements["stack_frames"] = frames
                    return measurements
            else:
                self.assertNotEqual(result.returncode, 0)
                marker = ("!failed_boot.hasIncompleteContactLoad()" if disable == "recovery" else
                          "SPIFFS.startupOperations() - before == 1" if disable == "begin" else
                          "crc_read_passes == 104" if disable == "crc_batch" else
                          "largest_backend_" + ("write" if disable == "writer" else "read"))
                self.assertIn(marker, result.stderr)


if __name__ == "__main__":
    unittest.main()
