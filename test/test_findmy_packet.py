"""Validate the Apple Find My beacon layout against independent byte vectors."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FindMyPacketTest(unittest.TestCase):
    def test_address_and_advertisement_vectors(self):
        compiler = shutil.which("g++") or shutil.which("c++")
        if not compiler:
            self.skipTest("C++ compiler not installed")

        source = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include "src/helpers/findmy/FindMyPacket.h"

int main() {
  uint8_t key[28] = {
    0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0a,0x0b,
    0x0c,0x0d,0x0e,0x0f,0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,
    0x18,0x19,0x1a,0x1b
  };
  uint8_t addr[6] = {};
  uint8_t payload[29] = {};
  const uint8_t expected_addr[] = {0xc0,0x01,0x02,0x03,0x04,0x05};
  const uint8_t expected_payload[] = {
    0x4c,0x00,0x12,0x19,0x00,0x06,0x07,0x08,0x09,0x0a,0x0b,0x0c,
    0x0d,0x0e,0x0f,0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,0x18,
    0x19,0x1a,0x1b,0x00,0x00
  };
  mesh::findmy::buildPacket(key, addr, payload);
  assert(std::memcmp(addr, expected_addr, sizeof(addr)) == 0);
  assert(std::memcmp(payload, expected_payload, sizeof(payload)) == 0);

  // All original high-bit combinations survive in the separate hint byte.
  const uint8_t first_bytes[] = {0x00, 0x40, 0x80, 0xc0};
  for (unsigned i = 0; i < 4; ++i) {
    key[0] = static_cast<uint8_t>(first_bytes[i] | 0x2a);
    mesh::findmy::buildPacket(key, addr, payload, 0x80);
    assert(addr[0] == 0xea && addr[1] == 0x01 && addr[5] == 0x05);
    assert(payload[4] == 0x80);
    assert(payload[5] == 0x06 && payload[26] == 0x1b);
    assert(payload[27] == i && payload[28] == 0);
  }
}
'''
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            source_path = path / "test.cpp"
            binary_path = path / "test"
            source_path.write_text(source, encoding="utf-8")
            subprocess.run(
                [compiler, "-std=c++11", "-Wall", "-Wextra", "-Werror",
                 "-pedantic", "-I", str(ROOT), str(source_path), "-o", str(binary_path)],
                check=True,
            )
            subprocess.run([str(binary_path)], check=True)


if __name__ == "__main__":
    unittest.main()
