#!/usr/bin/env python3
"""Compile the complete disabled management implementation at its FS boundary."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

HEADER = r'''
#pragma once
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#define OUT_PATH_UNKNOWN 255
struct TransportKey { unsigned marker = 17; };
struct Filesystem {
  unsigned reads = 0;
  bool exists(const char* path) {
    // No management/data.tx probe, recovery or write is permitted when off.
    assert(!strcmp(path, "/fleet_channel"));
    ++reads;
    return true;
  }
};
using FILESYSTEM = Filesystem;
struct Callbacks { bool supportsFleetControl() const { return true; } };
namespace mesh {
struct Mesh {};
struct RegionMap {};
struct FleetChannel {
  static inline unsigned live = 0;
  FleetChannel(FILESYSTEM*, Callbacks*, RegionMap*) { ++live; }
  ~FleetChannel() { --live; }
  bool handleConfig(const char*, char* reply, size_t size) {
    snprintf(reply, size, "fleet remains available"); return true;
  }
};
}
struct CommonCLI {
  mesh::Mesh* _management_mesh = nullptr;
  FILESYSTEM* _management_fs = nullptr;
  mesh::RegionMap region;
  mesh::RegionMap* _region_map = &region;
  Callbacks callbacks;
  Callbacks* _callbacks = &callbacks;
  mesh::FleetChannel* _fleet_channel = nullptr;
  unsigned beacon_starts = 0;
  ~CommonCLI() { delete _fleet_channel; }
  void beginBeacons() { ++beacon_starts; }
  void beginManagement(mesh::Mesh&, FILESYSTEM*);
  void loopManagement();
  bool handleFleetCommand(const char*, char*);
  bool getDataTxPath(const uint8_t*&, uint8_t&) const;
  bool resolveDataTxScope(TransportKey&, char*, size_t, bool*) const;
  bool adoptLegacyDataTxPath(const uint8_t*, uint8_t);
  bool setDataTxPath(const char*, char*, size_t);
  bool handleDataTxCommand(char*, char*);
  bool handleManagementCommand(char*, char*);
};
'''

HARNESS = r'''
#include "CommonCLI.h"
#include <string>
#include <vector>
int main() {
  Filesystem fs;
  mesh::Mesh mesh;
  {
    CommonCLI cli;
    cli.beginManagement(mesh, &fs);
    assert(cli._management_mesh == &mesh && cli._management_fs == &fs);
    assert(cli.beacon_starts == MESH_ENABLE_TX_BEACONS);
    assert(mesh::FleetChannel::live == MESH_ENABLE_FLEET_CONTROL);
    assert(fs.reads == MESH_ENABLE_FLEET_CONTROL);
    cli.loopManagement();
    assert(fs.reads == MESH_ENABLE_FLEET_CONTROL);
#if MESH_ENABLE_FLEET_CONTROL
    char fleet_reply[160] = {};
    assert(cli.handleFleetCommand("get fleet.stats", fleet_reply));
    assert(!strcmp(fleet_reply, "fleet remains available"));
#endif
    const uint8_t byte = 42;
    const uint8_t* path = &byte;
    uint8_t length = 3;
    assert(!cli.getDataTxPath(path, length));
    assert(path == nullptr && length == OUT_PATH_UNKNOWN);
    assert(!cli.adoptLegacyDataTxPath(&byte, 1));
    TransportKey key;
    char name[8] = "old";
    bool ambiguous = true;
    assert(!cli.resolveDataTxScope(key, name, sizeof(name), &ambiguous));
    assert(!*name && !ambiguous && key.marker == 17);
    assert(!cli.resolveDataTxScope(key, nullptr, 0, nullptr));
    for (const char* command : {"ver", "board", "stats-core", "stats-radio",
                               "stats-packets", "get usb.logging", "get radio2",
                               "get password", "host cpu-temp", "get data.txExtra"}) {
      char input[160], reply[160] = "not handled";
      strcpy(input, command);
      assert(!cli.handleManagementCommand(input, reply));
      assert(!strcmp(input, command) && !strcmp(reply, "not handled"));
    }
    for (const char* command : {"get mgmt", "get mgmt.password", "set mgmt.enabled on",
                               "get mgmt.path", "set mgmt.path direct",
                               "get data.tx", "get data.tx.path", "set data.tx region none"}) {
      char input[160], reply[160] = {};
      strcpy(input, command);
      assert(cli.handleManagementCommand(input, reply));
      assert(!strcmp(input, command));
      assert(!strcmp(reply, !strncmp(command + 4, "data.tx", 7)
          ? "Err - data.tx unavailable" : "ERR: management unavailable"));
    }
    for (size_t length : {size_t(0), size_t(5), size_t(96), size_t(200)}) {
      const std::string original = "set mgmt.password " + std::string(length, 's');
      std::vector<char> guarded(original.size() + 9, '\x5A');
      char* command = guarded.data() + 4;
      memcpy(command, original.c_str(), original.size() + 1);
      char reply[160] = {};
      assert(cli.handleManagementCommand(command, reply));
      assert(!strcmp(reply, "ERR: management unavailable"));
      assert(!memcmp(command, "set mgmt.password ", 18));
      for (size_t i = 18; i <= original.size(); ++i) assert(command[i] == 0);
      for (size_t i = 0; i < 4; ++i) assert(guarded[i] == '\x5A');
      for (size_t i = original.size() + 5; i < guarded.size(); ++i) assert(guarded[i] == '\x5A');
    }
    char guarded_reply[12]; memset(guarded_reply, '\x5A', sizeof(guarded_reply));
    assert(!cli.setDataTxPath("direct", guarded_reply + 2, 6));
    assert(guarded_reply[0] == '\x5A' && guarded_reply[1] == '\x5A');
    assert(guarded_reply[7] == 0 && guarded_reply[8] == '\x5A');
    assert(fs.reads == MESH_ENABLE_FLEET_CONTROL);
  }
  assert(mesh::FleetChannel::live == 0);
  puts("PASS: disabled management fails closed, wipes passwords and retains Fleet/Beacon context");
}
'''


class ManagementFeatureGateTest(unittest.TestCase):
    def test_default_is_enabled_and_explicit_values_are_boolean(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        header = ROOT / "src/helpers/ManagementFeatureConfig.h"
        for platform in (None, "STM32_PLATFORM", "NRF52_PLATFORM", "ESP32_PLATFORM", "RP2040_PLATFORM"):
            for value in (None, 0, 1, -1, 2):
                with self.subTest(platform=platform, value=value):
                    command = [compiler, "-E", "-P", "-x", "c++", "-include", str(header)]
                    if platform:
                        command.append("-D" + platform + "=1")
                    if value is not None:
                        command.append("-DMESH_ENABLE_MANAGEMENT_REPORTING=" + str(value))
                    command.append("-")
                    result = subprocess.run(command, input="MESH_ENABLE_MANAGEMENT_REPORTING\n",
                                            capture_output=True, text=True, timeout=10)
                    if value in (-1, 2):
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("must be 0 or 1", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout.strip(), str(1 if value is None else value))

    def test_whole_disabled_translation_units_have_no_crypto_or_storage_dependency(self):
        compiler = os.environ.get("CXX") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "a host C++17 compiler is required")
        with tempfile.TemporaryDirectory(prefix="management-off-") as directory:
            work = Path(directory)
            (work / "CommonCLI.h").write_text(HEADER, encoding="ascii")
            (work / "test.cpp").write_text(HARNESS, encoding="ascii")
            for name in ("CommonCLI_Management.cpp", "ManagementReporter.cpp"):
                shutil.copyfile(ROOT / "src/helpers" / name, work / name)
            sanitizers = (["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                           "-fno-pie", "-no-pie"] if sys.platform.startswith("linux") else [])
            for beacons, fleet in ((0, 0), (0, 1), (1, 0), (1, 1)):
                with self.subTest(beacons=beacons, fleet=fleet):
                    executable = work / ("test-%u-%u" % (beacons, fleet))
                    result = subprocess.run([
                        compiler, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", *sanitizers,
                        "-DMESH_ENABLE_MANAGEMENT_REPORTING=0", "-DMESH_ENABLE_TX_BEACONS=" + str(beacons),
                        "-DMESH_ENABLE_FLEET_CONTROL=" + str(fleet), "-I", str(work),
                        "-I", str(ROOT / "src/helpers"), str(work / "CommonCLI_Management.cpp"),
                        str(work / "ManagementReporter.cpp"), str(work / "test.cpp"), "-o", str(executable),
                    ], capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("PASS: disabled management fails closed", result.stdout)


if __name__ == "__main__":
    unittest.main()
