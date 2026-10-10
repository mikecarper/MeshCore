#!/usr/bin/env python3
"""Compile the actual bounded journal with fault injection on five FS APIs."""

from pathlib import Path
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test/fixtures/room_history_store"

STAT_MOCK = r'''
#pragma once
struct stat {};
inline int stat(const char* absolute, struct stat*) {
  if (!metadata_filesystem || strncmp(absolute, "/spiffs", 7) != 0) { errno = EIO; return -1; }
  const char* path = absolute + 7;
  const std::string op = std::string("stat:") + path;
  const bool okay = metadata_filesystem->begin(op);
  const bool present = metadata_filesystem->files.count(path) || metadata_filesystem->directories.count(path);
  metadata_filesystem->end(op);
  if (!okay) { errno = EIO; return -1; }
  if (!present) { errno = ENOENT; return -1; }
  return 0;
}
'''


def crc_image(payload):
    return payload + struct.pack("<I", zlib.crc32(payload))


class RoomHistoryStoreTest(unittest.TestCase):
    def test_actual_bounded_journal_backend_modes_faults_and_powercuts(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        if compiler is None:
            self.skipTest("a host C++ compiler is required")
        vectors = {
            "HEADER": crc_image(b"RHJ\x01" + struct.pack("<HBB", 192, 32, 64)),
            "CONFIG_ON": crc_image(b"RHC\x01\x01" + bytes(7)),
            "CONFIG_OFF": crc_image(b"RHC\x01\x00" + bytes(7)),
            "RECORD_ONE": crc_image(b"\x01" + bytes(30) + b"\x01" + struct.pack("<I", 1001)
                                      + b"post 1".ljust(152, b"\x00")),
        }
        rendered = "\n".join("static const std::vector<uint8_t> " + name + " = {"
                              + ",".join(str(byte) for byte in image) + "};"
                              for name, image in vectors.items())
        sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                       "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
        with tempfile.TemporaryDirectory(prefix="meshcore-room-history-") as temporary:
            work = Path(temporary)
            (work / "sys").mkdir()
            (work / "sys/stat.h").write_text(STAT_MOCK)
            (work / "vectors.inc").write_text(rendered)
            for platform in (None, "NRF52_PLATFORM", "STM32_PLATFORM", "RP2040_PLATFORM", "ESP32_PLATFORM"):
                with self.subTest(platform=platform):
                    binary = work / (platform or "generic")
                    defines = ["-D" + platform + "=1"] if platform else []
                    built = subprocess.run([compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                            *sanitizers, *defines, "-I" + str(work), "-I" + str(ROOT / "src"),
                                            "-I" + str(FIXTURES), str(FIXTURES / "test.cpp"), "-o", str(binary)],
                                           capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    checked = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                    self.assertIn("bounded room history fault and recovery scenarios", checked.stdout)
                    print(checked.stdout.strip())


if __name__ == "__main__":
    unittest.main()
