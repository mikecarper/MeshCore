"""Execute saved repeater discovery controls with real CLI and prefs images."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_repeater_trace_repeat import production_harness
from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]

MAIN = r'''
struct MyMesh {
  CommonCLI _cli;
  NodePrefs& _prefs = _cli.prefs;
  bool handleDiscoveryResponseCommand(const char*, char*, size_t);
};
@HANDLER@

static void run(MyMesh& mesh, const char* command, const char* expected) {
  char reply[160] = {};
  assert(mesh.handleDiscoveryResponseCommand(command, reply, sizeof(reply)));
  assert(strcmp(reply, expected) == 0);
}

static std::vector<uint8_t> image(CommonCLI& cli) {
  Capture capture;
  assert(writeCommonPrefsImage(capture, &cli.prefs));
  return capture.bytes;
}

static void assertLoaded(const std::vector<uint8_t>& bytes, uint8_t expected) {
  CommonCLI reader;
  reader.prefs.discovery_responses = uint8_t(expected ^ 3);
  reader.fs.files["/com_prefs"] = bytes;
  reader.loadPrefsInt(&reader.fs, "/com_prefs");
  assert(reader.prefs.discovery_responses == expected);
  assert(reader.prefs.freq == 910 && reader.prefs.primary_radio_preamble == 48);
}

static void cliChoices() {
  for (uint8_t initial : {0, 1, 2, 3}) {
    for (uint8_t bit : {1, 2}) {
      for (bool enabled : {false, true}) {
        MyMesh mesh;
        mesh._prefs.discovery_responses = initial;
        const char* name = bit == 1 ? "discovery.node.responses" : "discovery.region.responses";
        char command[80], expected[100];
        snprintf(command, sizeof(command), "get %s", name);
        run(mesh, command, (initial & bit) ? "> on" : "> off");
        snprintf(command, sizeof(command), "set %s %s", name, enabled ? "on" : "off");
        snprintf(expected, sizeof(expected), "OK - %s %s", name, enabled ? "on" : "off");
        run(mesh, command, expected);
        const uint8_t mask = enabled ? uint8_t(initial | bit) : uint8_t(initial & ~bit);
        assert(mesh._prefs.discovery_responses == mask);
        assert((mesh._prefs.discovery_responses & (bit ^ 3)) == (initial & (bit ^ 3)));
        assert(mesh._cli._common_save_succeeded);
        assert(mesh._cli.fs.files["/com_prefs"] == image(mesh._cli));
        assertLoaded(mesh._cli.fs.files["/com_prefs"], mask);
        snprintf(command, sizeof(command), "get %s", name);
        run(mesh, command, enabled ? "> on" : "> off");
      }
    }
  }
  MyMesh mesh;
  assert(mesh._prefs.discovery_responses == 3);
  run(mesh, "set discovery.node.responses\t \toff", "OK - discovery.node.responses off");
  run(mesh, "set discovery.region.responses off", "OK - discovery.region.responses off");
  assert(mesh._prefs.discovery_responses == 0);
}

static void invalidCommands() {
  for (const char* name : {"discovery.node.responses", "discovery.region.responses"}) {
    for (const char* value : {"", " ", "true", "false", "1", "0", "ON", "Off",
        "on reboot", "off reboot", "on junk", "off ", "on\t", "on\n", "on;off"}) {
      MyMesh mesh;
      mesh._prefs.discovery_responses = 1;
      mesh._cli.savePrefs(&mesh._cli.fs, PrefsSaveRouting::Scope::Common);
      const auto before = mesh._cli.fs.files["/com_prefs"];
      char command[100], reply[160] = {};
      snprintf(command, sizeof(command), "set %s %s", name, value);
      assert(mesh.handleDiscoveryResponseCommand(command, reply, sizeof(reply)));
      assert(strncmp(reply, "Err - use ", 10) == 0);
      assert(mesh._prefs.discovery_responses == 1 && mesh._cli.fs.files["/com_prefs"] == before);
      snprintf(command, sizeof(command), "get %s on", name);
      assert(mesh.handleDiscoveryResponseCommand(command, reply, sizeof(reply)));
      assert(strncmp(reply, "Err - use ", 10) == 0);
      assert(mesh._prefs.discovery_responses == 1 && mesh._cli.fs.files["/com_prefs"] == before);
    }
  }
  for (const char* command : {"get discovery.node.responsesx", "set discovery.region.responses_extra off",
      "get discovery.node.response", "set discovery.region.responses/off", "discovery.node.responses on"}) {
    MyMesh mesh;
    const auto before = mesh._cli.fs.files;
    char reply[160] = "unchanged";
    assert(!mesh.handleDiscoveryResponseCommand(command, reply, sizeof(reply)));
    assert(strcmp(reply, "unchanged") == 0 && mesh._prefs.discovery_responses == 3);
    assert(mesh._cli.fs.files == before);
  }
}

static void failedSaves() {
  for (uint8_t initial : {0, 1, 2, 3}) {
    for (uint8_t bit : {1, 2}) {
      for (unsigned fault : {0, 1, 2, 3}) {
        MyMesh mesh;
        mesh._prefs.discovery_responses = initial;
        mesh._cli.savePrefs(&mesh._cli.fs, PrefsSaveRouting::Scope::Common);
        assert(mesh._cli._common_save_succeeded);
        const auto before = mesh._cli.fs.files["/com_prefs"];
        if (fault == 0) mesh._cli.fs.fail_write = true;
        if (fault == 1) {
          const size_t offset = mesh::discovery::COMMON_LEGACY_SIZE
#ifdef WITH_RS232_BRIDGE
              + mesh::discovery::LEGACY_RS232_TAIL_SIZE
#endif
              ;
          mesh._cli.fs.fail_write_after = (initial ^ bit) == 3 ? offset - 1 : offset + 4;
        }
        if (fault == 2) mesh._cli.fs.fail_read_open = true;
        if (fault == 3) mesh._cli.fs.fail_rename = 1;
        char command[100];
        snprintf(command, sizeof(command), "set discovery.%s.responses %s",
                 bit == 1 ? "node" : "region", (initial & bit) ? "off" : "on");
        run(mesh, command, "Err - save unconfirmed; reboot may differ");
        assert(mesh._prefs.discovery_responses == initial);
        assert(mesh._cli.fs.files["/com_prefs"] == before);
      }
    }
  }
}

static void persistence() {
  CommonCLI cli;
  const auto baseline = image(cli);
  const size_t offset = mesh::discovery::COMMON_LEGACY_SIZE
#ifdef WITH_RS232_BRIDGE
      + mesh::discovery::LEGACY_RS232_TAIL_SIZE
#endif
      ;
  assert(cli.prefs.discovery_responses == 3);
  for (uint8_t mask : {0, 1, 2, 3}) {
    cli.prefs.discovery_responses = mask;
    cli.savePrefs(&cli.fs, PrefsSaveRouting::Scope::Common);
    assert(cli._common_save_succeeded);
    const auto saved = image(cli);
    assert(cli.fs.files["/com_prefs"] == saved);
    assert(saved.size() == baseline.size() + (mask == 3 ? 0 : mesh::discovery::PREFS_SIZE));
    assert(std::equal(saved.begin(), saved.begin() + offset, baseline.begin()));
    assertLoaded(saved, mask);
    if (mask == 3) continue;
    uint8_t decoded = 3;
    assert(mesh::discovery::decodePrefs(saved.data() + offset, mesh::discovery::PREFS_SIZE, decoded));
    assert(decoded == mask);

    // All historical prefixes and partial optional records keep replies ON,
    // even when a previous load into the same object had disabled them.
    for (size_t length = 0; length < offset + mesh::discovery::PREFS_SIZE; ++length) {
      CommonCLI reader;
      reader.prefs.discovery_responses = 0;
      reader.fs.files["/com_prefs"] = {saved.begin(), saved.begin() + length};
      reader.loadPrefsInt(&reader.fs, "/com_prefs");
      assert(reader.prefs.discovery_responses == 3);
    }
    for (size_t byte = 0; byte < mesh::discovery::PREFS_SIZE; ++byte) {
      auto damaged = saved;
      damaged[offset + byte] ^= 0x80;
      assertLoaded(damaged, 3);
    }
  }
  for (uint8_t responses : {0, 3}) {
    CommonCLI configured;
    configured.prefs.discovery_responses = responses;
    configured.prefs.unscoped_limits[16] = 22;
    configured.prefs.unscoped_limits[6] = 1;
    configured.savePrefs(&configured.fs, PrefsSaveRouting::Scope::Common);
    auto bytes = configured.fs.files["/com_prefs"];
    assert(image(configured) == bytes);
    CommonCLI reboot;
    reboot.fs.files["/com_prefs"] = bytes;
    reboot.loadPrefsInt(&reboot.fs, "/com_prefs");
    assert(reboot.prefs.discovery_responses == responses);
    assert(!memcmp(reboot.prefs.unscoped_limits, configured.prefs.unscoped_limits, 17));
    const size_t start = offset + (responses == 3 ? 0 : mesh::discovery::PREFS_SIZE);
    bytes[start + 4] ^= 1;
    reboot.fs.files["/com_prefs"] = bytes;
    reboot.loadPrefsInt(&reboot.fs, "/com_prefs");
    for (uint8_t limit : reboot.prefs.unscoped_limits) assert(limit == 0);
  }
  CommonCLI missing;
  missing.prefs.discovery_responses = 0;
  missing.loadPrefsInt(&missing.fs, "/missing");
  assert(missing.prefs.discovery_responses == 3);

  // A complete, checksummed record with a forbidden mask must still reject.
  auto invalid = baseline;
  uint8_t record[mesh::discovery::PREFS_SIZE];
  mesh::discovery::encodePrefs(record, 0);
  record[4] = 4;
  const uint16_t crc = mesh::discovery::checksum(record, 6);
  record[6] = uint8_t(crc >> 8); record[7] = uint8_t(crc);
  invalid.insert(invalid.begin() + offset, record, record + sizeof(record));
  assertLoaded(invalid, 3);

#if defined(ESP32_PLATFORM)
  // The optional discovery record must not displace the existing CPU footer.
  cli.prefs.discovery_responses = 0;
  cli.prefs.cpu_freq_mhz = 80;
  auto combined = image(cli);
  CommonCLI cpu;
  cpu.fs.files["/com_prefs"] = combined;
  cpu.loadPrefsInt(&cpu.fs, "/com_prefs");
  assert(cpu.prefs.discovery_responses == 0 && cpu.prefs.cpu_freq_mhz == 80);
#endif
#if defined(RAK_4631)
  cli.prefs.discovery_responses = 1;
  cli.prefs.peripherals_shared = 1;
  auto combined = image(cli);
  CommonCLI peripheral;
  peripheral.fs.files["/com_prefs"] = combined;
  peripheral.loadPrefsInt(&peripheral.fs, "/com_prefs");
  assert(peripheral.prefs.discovery_responses == 1 && peripheral.prefs.peripherals_shared == 1);
#endif
#ifndef WITH_RS232_BRIDGE
  // Loading an old UART profile on a non-UART image must still find its saved
  // discovery record after the source image's checked ten-byte power tail.
  CommonCLI plain;
  auto uart = image(plain);
  uint8_t power[mesh::bridge::rs232power::PREFS_SIZE];
  mesh::bridge::rs232power::encodePrefs(power, 1, 1, 300);
  mesh::discovery::encodePrefs(record, 2);
  uart.insert(uart.begin() + mesh::discovery::COMMON_LEGACY_SIZE, power, power + sizeof(power));
  uart.insert(uart.begin() + mesh::discovery::COMMON_LEGACY_SIZE + sizeof(power),
              record, record + sizeof(record));
  assertLoaded(uart, 2);
#endif
}

int main() {
  cliChoices();
  invalidCommands();
  failedSaves();
  persistence();
}
'''

OUTBOUND = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <helpers/DiscoveryResponsePrefs.h>
#define PUB_KEY_SIZE 32
#define PAYLOAD_TYPE_ANON_REQ 7
namespace mesh {
struct Identity {
  void calcSharedSecret(uint8_t* out, const Identity&) { memset(out, 0xa5, PUB_KEY_SIZE); }
};
struct Packet { uint8_t payload[16] = {}; size_t length = 0; };
}
struct MyMesh {
  static constexpr unsigned NEIGHBOR_DISCOVER_MIN_FREE_PACKETS = @MIN_FREE@;
  struct { uint8_t discovery_responses = 3; } _prefs;
  struct Manager { unsigned free = NEIGHBOR_DISCOVER_MIN_FREE_PACKETS;
    unsigned getFreeCount() { return free; }
  } manager;
  Manager* _mgr = &manager;
  struct Clock { uint32_t unique = 1000; uint32_t getCurrentTimeUnique() { return ++unique; } } clock;
  Clock* getRTCClock() { return &clock; }
  mesh::Identity self_id;
  mesh::Packet packet;
  bool allocation_ok = true;
  unsigned allocations = 0, sends = 0;
  mesh::Packet* createAnonDatagram(unsigned type, const mesh::Identity&,
      const mesh::Identity&, const uint8_t*, const uint8_t* bytes, size_t length) {
    ++allocations;
    assert(type == PAYLOAD_TYPE_ANON_REQ && length == 6);
    if (!allocation_ok) return nullptr;
    memcpy(packet.payload, bytes, length); packet.length = length;
    return &packet;
  }
  void sendDirect(mesh::Packet* sent, const uint8_t* path, uint8_t length, uint32_t delay) {
    assert(sent == &packet && path == nullptr && length == 0 && delay == 0); ++sends;
  }
  mesh::Packet* sendAnonRegionsReq(const mesh::Identity&, uint32_t&);
};
@CONSTANTS@
@METHOD@
int main() {
  const mesh::Identity target;
  for (uint8_t mask : {0, 1, 2, 3}) {
    MyMesh mesh;
    mesh._prefs.discovery_responses = mask;
    uint32_t tag = 99;
    assert(mesh.sendAnonRegionsReq(target, tag) == &mesh.packet);
    assert(mesh.allocations == 1 && mesh.sends == 1 && tag == 1001);
    uint32_t sent_tag = 0; memcpy(&sent_tag, mesh.packet.payload, sizeof(sent_tag));
    assert(sent_tag == tag && mesh.packet.payload[4] == ANON_REQ_TYPE_REGIONS
           && mesh.packet.payload[5] == 0);
    mesh.manager.free = MyMesh::NEIGHBOR_DISCOVER_MIN_FREE_PACKETS - 1;
    assert(mesh.sendAnonRegionsReq(target, tag) == nullptr);
    assert(mesh.allocations == 1 && mesh.sends == 1 && tag == 1001);
    mesh.manager.free = MyMesh::NEIGHBOR_DISCOVER_MIN_FREE_PACKETS;
    mesh.allocation_ok = false;
    assert(mesh.sendAnonRegionsReq(target, tag) == nullptr);
    assert(mesh.allocations == 2 && mesh.sends == 1);
  }
}
'''


class RepeaterDiscoverySettingsTests(unittest.TestCase):
    def test_response_choices_do_not_disable_outbound_region_queries(self):
        import re
        source = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text()
        header = (ROOT / 'examples/simple_repeater/MyMesh.h').read_text()
        minimum = re.search(r'NEIGHBOR_DISCOVER_MIN_FREE_PACKETS\s*=\s*(\d+)', header).group(1)
        constant = re.search(r'^#define ANON_REQ_TYPE_REGIONS\s+[^\n]+', source, re.M).group()
        code = (OUTBOUND.replace('@MIN_FREE@', minimum).replace('@CONSTANTS@', constant)
                .replace('@METHOD@', extract_braced(source, 'mesh::Packet* MyMesh::sendAnonRegionsReq(')))
        with tempfile.TemporaryDirectory(prefix='repeater-discovery-outbound-') as directory:
            work = Path(directory)
            (work / 'test.cpp').write_text(code)
            built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                                    '-Wall', '-Wextra', '-Werror', '-I' + str(ROOT / 'src'),
                                    str(work / 'test.cpp'), '-o', str(work / 'test')],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            tested = subprocess.run([str(work / 'test')], capture_output=True, text=True, timeout=10)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)

    def test_real_commands_saved_images_failures_and_legacy_defaults(self):
        source = (ROOT / 'src/helpers/CommonCLI.cpp').read_text()
        header = (ROOT / 'src/helpers/CommonCLI.h').read_text()
        repeater = (ROOT / 'examples/simple_repeater/MyMesh.cpp').read_text()
        code = production_harness(source, header).split('int main() {', 1)[0]
        code = code.replace('  bool trySavePrefs(', '  bool saveCommonPrefs();\n  bool trySavePrefs(', 1)
        code = ('#include <algorithm>\n#include <helpers/PeripheralModePrefs.h>\n'
                '#include <helpers/bridges/RS232PowerSettings.h>\n' + code)
        code += extract_braced(source, 'bool CommonCLI::saveCommonPrefs(') + '\n'
        code += MAIN.replace('@HANDLER@', extract_braced(
            repeater, 'bool MyMesh::handleDiscoveryResponseCommand('))
        sanitizer_flags = [] if os.name == 'nt' else [
            '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-pie', '-no-pie']
        variants = [('ENABLE_OTA', platform) for platform in (
            'NRF52_PLATFORM', 'STM32_PLATFORM', 'ESP32_PLATFORM', 'RP2040_PLATFORM')]
        variants += [('NRF52_PLATFORM', 'RAK_4631'),
                     ('ENABLE_OTA', 'ESP32_PLATFORM', 'WITH_MQTT_BRIDGE'),
                     ('NRF52_PLATFORM', 'WITH_RS232_BRIDGE', 'WITH_RS232_BRIDGE_UART=2')]
        with tempfile.TemporaryDirectory(prefix='repeater-discovery-settings-') as directory:
            work = Path(directory)
            transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text()
            (work / 'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            (work / 'test.cpp').write_text(code)
            for macros in variants:
                with self.subTest(macros=macros):
                    binary = work / ('test.exe' if os.name == 'nt' else 'test')
                    built = subprocess.run([
                        os.environ.get('CXX', 'g++'), '-std=c++17',
                        *['-D' + macro if '=' in macro else '-D' + macro + '=1' for macro in macros],
                        *sanitizer_flags, '-I' + str(work),
                        '-I' + str(ROOT / 'test/fixtures/radio_profiles/mocks'),
                        '-I' + str(ROOT / 'src'), '-I' + str(ROOT / 'src/helpers'),
                        str(work / 'test.cpp'), '-o', str(binary)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
                    self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)


if __name__ == '__main__':
    unittest.main()
