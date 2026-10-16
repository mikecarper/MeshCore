#!/usr/bin/env python3
"""Execute bounded CRC batching in the production contact transaction."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SIGNATURE = ("CommitProgress serviceCommit(bool valid = true, unsigned max_verify_chunks = 1,\n"
             "                                bool defer_backup_cleanup = false) {")
OPAQUE_FILE = """#pragma once
#include <stddef.h>
#include <stdint.h>
class File {
  void* impl;
public:
  File();
  explicit operator bool() const;
  size_t read(uint8_t*, size_t);
  size_t write(const uint8_t*, size_t);
  size_t size() const;
  bool setBufferSize(size_t);
  void flush();
  void close();
};
class FileSystem {
public:
  bool exists(const char*);
  bool remove(const char*);
  bool rename(const char*, const char*);
  File open(const char*, const char*, bool = false);
};
#define FILESYSTEM FileSystem
"""


class ContactCRCBatchTest(unittest.TestCase):
    def source(self):
        source = (ROOT / "src/helpers/ContactFileTransaction.h").read_text()
        self.assertEqual(source.count(SIGNATURE), 1)
        return source.replace('#include "IdentityStore.h"',
                              '// Filesystem contract is supplied by this native fixture.').replace(
            '#include "PersistentStoreFormat.h"', '#include <helpers/PersistentStoreFormat.h>')

    def run_native(self, platform="ESP32_PLATFORM", negative=None):
        source = self.source()
        if negative == "clamp":
            anchor = "if (max_verify_chunks > 8) max_verify_chunks = 8;"
            self.assertEqual(source.count(anchor), 1)
            source = source.replace(anchor, "// Negative control: unbounded budget.")
        elif negative == "synchronous":
            anchor = "do { progress = serviceCommit(valid); }"
            self.assertEqual(source.count(anchor), 1)
            source = source.replace(anchor, "do { progress = serviceCommit(valid, 8); }")
        elif negative == "stdio":
            anchor = "ESP_VERIFY_BUFFER_SIZE = 128;"
            self.assertEqual(source.count(anchor), 1)
            source = source.replace(anchor, "ESP_VERIFY_BUFFER_SIZE = 4096;")
        elif negative == "background_stdio":
            anchor = "max_verify_chunks >= 8 ? 512 : ESP_VERIFY_BUFFER_SIZE"
            self.assertEqual(source.count(anchor), 1)
            source = source.replace(
                anchor, "max_verify_chunks >= 8 ? 4096 : ESP_VERIFY_BUFFER_SIZE")
        fixture = (ROOT / "test/fixtures/contact_crc_batch/test.cpp").read_text()
        if negative == "background_stdio":
            # Start with a large file so the negative executes a real >512B
            # refill, rather than failing only the selected-size assertion.
            self.assertEqual(fixture.count("  limitsAndTails();"), 1)
            fixture = fixture.replace("  limitsAndTails();", "  verify(347 * 152, 8);")
        # Count actual commit() calls without replacing its body or algorithm.
        source = source.replace(SIGNATURE, SIGNATURE + "\n    ++fixture_commit_calls;")
        with tempfile.TemporaryDirectory(prefix="mesh-contact-crc-") as directory:
            temp = Path(directory)
            (temp / "transaction_under_test.h").write_text(source)
            (temp / "fixture.cpp").write_text(fixture)
            binary = temp / "fixture"
            result = subprocess.run([
                "c++", "-std=c++17", "-O1", "-g", "-Wall", "-Wextra",
                "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                "-D" + platform + "=1", "-I", str(temp), "-I", str(ROOT / "src"),
                str(temp / "fixture.cpp"),
                "-o", str(binary),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            if negative:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                markers = {"clamp": "reads <= bound", "synchronous": "fixture_commit_calls == 829",
                           "stdio": "reader_buffer_size == selected",
                           "background_stdio": "backend_bytes <= 512"}
                self.assertIn(markers[negative], result.stderr)
                return
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

    def test_esp_bounds_tails_failures_crc_cancellation_and_crash(self):
        result = self.run_native()
        self.assertEqual(result, {"one_passes": 829, "eight_passes": 108,
                                 "logical_reads": 825, "backend_reads": 104,
                                 "max_pass_reads": 8, "max_pass_refills": 1,
                                 "max_pass_backend_bytes": 512, "reader_buffer_size": 512,
                                 "one_backend_reads": 413, "one_reader_buffer_size": 128})

    def test_other_platform_ignores_requested_batch(self):
        result = self.run_native("RP2040_PLATFORM")
        self.assertEqual(result["one_passes"], 829)
        self.assertEqual(result["eight_passes"], 829)
        self.assertEqual(result["max_pass_reads"], 1)

    def test_unbounded_budget_is_detected(self):
        self.run_native(negative="clamp")

    def test_synchronous_batching_is_detected(self):
        self.run_native(negative="synchronous")

    def test_large_stdio_read_ahead_is_detected(self):
        self.run_native(negative="stdio")

    def test_large_background_stdio_read_ahead_is_detected(self):
        self.run_native(negative="background_stdio")

    def test_opaque_xtensa_fixture_frame_and_object_sizes(self):
        compiler = os.environ.get("CONTACT_CRC_XTENSA_CXX") or shutil.which("xtensa-esp32s3-elf-g++")
        if not compiler:
            candidate = Path.home() / ".platformio/packages/toolchain-xtensa-esp32s3/bin/xtensa-esp32s3-elf-g++"
            if candidate.is_file():
                compiler = str(candidate)
        if not compiler:
            self.skipTest("Xtensa S3 compiler is not installed")
        # Isolate the real CFT/CRC bodies from the native vector/mock internals.
        # This opaque File has a different layout from Arduino's File/Stream;
        # these sizes describe this fixture, not the SDK ABI or final firmware.
        with tempfile.TemporaryDirectory(prefix="mesh-contact-crc-stack-") as directory:
            temp = Path(directory)
            (temp / "file_contract.h").write_text(OPAQUE_FILE)
            source = self.source().replace(
                '// Filesystem contract is supplied by this native fixture.', '#include "file_contract.h"')
            (temp / "transaction_under_test.h").write_text(source)
            (temp / "frame.cpp").write_text(
                '#include "transaction_under_test.h"\n'
                'extern "C" int commit_frame(mesh::ContactFileTransaction* p,unsigned n) '
                '{ return int(p->serviceCommit(true,n)); }\n'
                'char transaction_object_size[sizeof(mesh::ContactFileTransaction)];\n')
            result = subprocess.run([
                compiler, "-std=c++17", "-Os", "-fno-inline", "-fstack-usage",
                "-DESP32_PLATFORM=1", "-I", str(ROOT / "src"), "-c",
                str(temp / "frame.cpp"), "-o", str(temp / "frame.o"),
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            usage = (temp / "frame.su").read_text().splitlines()
            frames = [line.split("\t") for line in usage
                      if "ContactFileTransaction::serviceCommit(" in line]
            self.assertEqual(len(frames), 1)
            self.assertEqual(int(frames[0][1]), 112)
            # Publication is separate from the 64-byte read buffer. Include
            # every known nested helper, not just the smaller caller frame.
            # The old opaque fixture peaked at 128 + 48 + 32 = 208 bytes;
            # the new chain retains that bound. SDK/callback frames remain
            # outside this isolated contract, as does the full firmware ABI.
            helper_frames = {}
            for name, expected in (("finishCommit", 64), ("probe", 32), ("updateCRC32C", 32)):
                found = [line.split("\t") for line in usage
                         if "ContactFileTransaction::" + name + "(" in line]
                self.assertEqual(len(found), 1, name)
                helper_frames[name] = int(found[0][1])
                self.assertEqual(helper_frames[name], expected, name)
            self.assertFalse(any("ContactFileTransaction::moveState(" in line
                                 or "ContactFileTransaction::removeIfPresent(" in line
                                 for line in usage))
            self.assertEqual(int(frames[0][1]) + max(
                helper_frames["updateCRC32C"],
                helper_frames["finishCommit"] + helper_frames["probe"]), 208)
            nm = str(Path(compiler).with_name("xtensa-esp32s3-elf-nm"))
            symbols = subprocess.check_output([nm, "-S", str(temp / "frame.o")], text=True)
            size = next(int(line.split()[1], 16) for line in symbols.splitlines()
                        if line.endswith("transaction_object_size"))
            self.assertEqual(size, 144)


if __name__ == "__main__":
    unittest.main()
