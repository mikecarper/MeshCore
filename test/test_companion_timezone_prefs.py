"""Keep fractional Companion timezones outside the published binary prefix."""
import binascii
import math
import os
from pathlib import Path
import random
import struct
import subprocess
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS, esp_recovery_helpers
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = (
    "STM32_PLATFORM", "NRF52_PLATFORM", "NRF52_PLATFORM,RAK_4631",
    "ESP32_PLATFORM", "ESP32_PLATFORM,TBEAM_1W", "RP2040_PLATFORM",
    "RP2040_PLATFORM,ENABLE_WIFI_INTERFACE",
)


def checked_footer(payload):
    return payload + struct.pack(">H", binascii.crc_hqx(payload, 0xffff))


def literal_legacy_image(platform):
    """An old wire image, independent of current member types or serializers."""
    fan = "TBEAM_1W" in platform
    wifi = "RP2040_PLATFORM" in platform and "ENABLE_WIFI_INTERFACE" in platform
    prefix = bytearray(243 if fan else 333 if wifi else 236)

    def number(offset, fmt, value):
        struct.pack_into("<" + fmt, prefix, offset, value)

    def text(offset, value):
        prefix[offset:offset + len(value)] = value

    number(0, "f", 1.25)
    text(4, b"literal timezone image")
    number(40, "d", 47.125)
    number(48, "d", -122.25)
    number(56, "f", 915.25)
    prefix[60:64] = bytes((7, 5, 1, 1))
    number(64, "f", 62.5)
    number(68, "b", -9)
    prefix[69:72] = bytes((1, 2, 0))
    number(72, "f", .5)
    prefix[76:79] = bytes((1, 2, 1))
    number(80, "I", 876543)
    prefix[84:86] = bytes((1, 1))
    number(86, "I", 123)
    prefix[90:93] = bytes((3, 4, 1))
    text(93, b"literal scope")
    prefix[124:140] = bytes(range(16))
    prefix[140:145] = bytes((1, 1, 1, 0, 1))
    number(145, "I", 15000)
    number(149, "I", 30000)
    prefix[153:159] = bytes((3, 16, 1, 1, 1, 1))
    text(159, b"literal Bluetooth name")
    number(191, "H", 180)
    prefix[193] = 1
    number(194, "H", 50)
    number(196, "H", 60)
    number(198, "H", 70)
    prefix[200] = 1
    prefix[201:207] = bytes((2, 4, 6, 8, 10, 12))
    prefix[207] = 1
    prefix[208:214] = bytes((12, 10, 8, 6, 4, 2))
    prefix[214] = 2
    number(215, "f", 1.25)
    number(219, "f", .75)
    prefix[223:226] = bytes((8, 15, 0xf9))  # signed -7, always exactly byte 225
    tail = 226
    if fan:
        text(tail, b"on\0\0\0")
        prefix[tail + 5:tail + 7] = bytes((33, 42))
        tail += 7
    if wifi:
        text(tail, b"literal-network")
        text(tail + 33, b"literal-password")
        tail += 97
    prefix[tail:tail + 10] = bytes((4, 3, 2, 0, 1, 0, 0x50, 1, 1, 2))
    assert tail + 10 == len(prefix)
    if "RAK_4631" in platform:
        prefix += checked_footer(b"PMO\x01\x01\x00")
    prefix += checked_footer(b"TXR\x01\xa2\xa4")
    return bytes(prefix)


def cpp_bytes(data):
    return "{" + ",".join(f"0x{value:02x}" for value in data) + "}"


def fractional_footer(value):
    return checked_footer(b"TZF\x01" + struct.pack("<f", value))


TESTS = r'''
#include <cmath>
#include <limits>
#include <vector>
#include <helpers/CLICommandUtils.h>

static const uint8_t LEGACY_IMAGE[] = @LEGACY_IMAGE@;

static std::vector<uint8_t> legacyImage(int8_t hours = -7) {
  std::vector<uint8_t> bytes(LEGACY_IMAGE, LEGACY_IMAGE + sizeof(LEGACY_IMAGE));
  bytes[225] = static_cast<uint8_t>(hours);
  return bytes;
}

static void tag(CompanionNodePrefs& prefs, const char* value) {
  assert(prefs.getCustom()->setByKey("tz_test_tag", value));
}

static void expectTag(CompanionNodePrefs& prefs, const char* expected) {
  char value[40];
  assert(prefs.getCustom()->getByKey("tz_test_tag", value, sizeof(value)));
  assert(!strcmp(value, expected));
}

static void expectLiteralSettings(const CompanionNodePrefs& prefs) {
  assert(!strcmp(prefs.node_name, "literal timezone image"));
  assert(prefs.airtime_factor == 1.25f && prefs.freq == 915.25f);
  assert(prefs.sf == 7 && prefs.cr == 5 && prefs.bw == 62.5f);
  assert(prefs.ble_pin == 876543 && prefs.gps_interval == 123);
  assert(!strcmp(prefs.default_scope_name, "literal scope"));
  for (unsigned i = 0; i < 16; ++i) assert(prefs.default_scope_key[i] == i);
  assert(!strcmp(prefs.bluetooth_name, "literal Bluetooth name"));
  assert(prefs.display_rotation_degrees == 180 && prefs.cad_max_duration_ms == 70);
  assert(prefs.tx_delay_factor == 1.25f && prefs.direct_tx_delay_factor == .75f);
  assert(prefs.interference_threshold == 8 && prefs.agc_reset_interval == 15);
  assert(prefs.flood_retry_attempts == 4 && prefs.flood_retry_max_path == 3);
  assert(prefs.flood_retry_group_max_path == 2 && prefs.flood_retry_advert_enabled == 0);
  assert(prefs.one_key_dm_enabled == 1 && prefs.bluetooth_enabled == 0);
  assert(prefs.gps_sync_interval_hours == 336 && prefs.usb_debug_enabled == 1);
  assert(prefs.lost_reply == 2 && prefs.wifi_enabled == 1);
  assert(prefs.tx_media == mesh::RADIO_TX_SECONDARY && prefs.tx_ssh == mesh::RADIO_TX_OFF);
#ifdef TBEAM_1W
  assert(!strcmp(prefs.fan_mode, "on") && prefs.fan_lo == 33 && prefs.fan_hi == 42);
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
  assert(!strcmp(prefs.wifi_ssid, "literal-network"));
  assert(!strcmp(prefs.wifi_pwd, "literal-password"));
#endif
#if defined(RAK_4631)
  assert(prefs.peripherals_shared == 1);
#endif
}

static void seedLive(CompanionNodePrefs& prefs) {
  strcpy(prefs.node_name, "untouched live settings");
  prefs.freq = 900.5f; prefs.ble_pin = 765432;
  prefs.tz_offset = 11.25f; prefs.tz_offset_legacy = -11;
  prefs.flood_retry_attempts = 9; prefs.gps_sync_interval_hours = 12;
  prefs.tx_media = mesh::RADIO_TX_BOTH; prefs.tx_ssh = mesh::RADIO_TX_PRIMARY;
  tag(prefs, "live adapter");
}

static void expectLive(CompanionNodePrefs& prefs, double lat, double lon) {
  assert(!strcmp(prefs.node_name, "untouched live settings"));
  assert(prefs.freq == 900.5f && prefs.ble_pin == 765432);
  assert(prefs.tz_offset == 11.25f && prefs.tz_offset_legacy == -11);
  assert(prefs.flood_retry_attempts == 9 && prefs.gps_sync_interval_hours == 12);
  assert(prefs.tx_media == mesh::RADIO_TX_BOTH && prefs.tx_ssh == mesh::RADIO_TX_PRIMARY);
  assert(lat == 1 && lon == 2);
  expectTag(prefs, "live adapter");
}

static void rejectImage(const std::vector<uint8_t>& candidate, int read_after = -1) {
  DataStore store; store.fs.files["/new_prefs"] = candidate;
  store.fs.fail_read_after = read_after;
  CompanionNodePrefs prefs; seedLive(prefs);
  double lat = 1, lon = 2;
  assert(!store.loadPrefsInt("/new_prefs", prefs, lat, lon));
  expectLive(prefs, lat, lon);
  assert(store.fs.files.at("/new_prefs") == candidate);
}

struct FractionVector { float hours; std::vector<uint8_t> footer; };
static const FractionVector FRACTIONS[] = {
  @FRACTIONS@
};

class TimezoneCLI {
public:
  CompanionNodePrefs _prefs;
  DataStore store;
  unsigned saves = 0;
  bool savePrefs() { ++saves; return store.savePrefs(_prefs, 47.125, -122.25); }
  @SAVE_PREFERENCE@
  bool command(const char* command, char* reply) {
    @GETTER@
    @SETTER@
    return false;
  }
};

static void cliTests() {
  TimezoneCLI cli;
  cli.store.fs.files["/new_prefs"] = legacyImage();
  double lat = 1, lon = 2;
  assert(cli.store.loadPrefsInt("/new_prefs", cli._prefs, lat, lon));
  char reply[160];
  struct Value { const char* text; float hours; };
  for (const Value& value : {
      Value{"5.5", 5.5f}, Value{"5.75", 5.75f}, Value{"-3.5", -3.5f},
      Value{"+12.75", 12.75f}, Value{" .25\t", .25f},
      Value{"-12", -12.0f}, Value{"14", 14.0f}}) {
    const unsigned saves = cli.saves;
    const std::string command = std::string("set tz.offset ") + value.text;
    assert(cli.command(command.c_str(), reply));
    assert(!strcmp(reply, "OK") && cli.saves == saves + 1);
    assert(cli._prefs.tz_offset == value.hours);
    assert(cli.command("get tz.offset", reply));
    assert(!strncmp(reply, "> ", 2));
    char* end = nullptr;
    assert(strtof(reply + 2, &end) == value.hours && *end == 0);
    CompanionNodePrefs reboot;
    assert(cli.store.loadPrefsInt("/new_prefs", reboot, lat, lon));
    assert(reboot.tz_offset == value.hours);
    expectLiteralSettings(reboot);
  }
  assert(cli.command("set tz.offset 5.75", reply) && !strcmp(reply, "OK"));
  assert(cli.command("get tz.offset", reply) && !strcmp(reply, "> 5.75"));
  const auto disk = cli.store.fs.files.at("/new_prefs");
  for (const char* invalid : {"", " ", ".", "+", "nan", "NaN", "inf", "-inf",
      "Infinity", "5.75junk", "5.5e0", "0x1p2", "--5", "15", "-12.25",
      "14.25", "9999999999999999999999999", "5\n"}) {
    const unsigned saves = cli.saves;
    const std::string command = std::string("set tz.offset ") + invalid;
    assert(cli.command(command.c_str(), reply));
    assert(!strncmp(reply, "Error", 5) && cli.saves == saves);
    assert(cli._prefs.tz_offset == 5.75f && cli.store.fs.files.at("/new_prefs") == disk);
  }
  // The production setter must roll back the complete float after a failed
  // real preferences transaction, rather than narrowing it to an int8_t.
  cli.store.fs.fail_write = true;
  const unsigned saves = cli.saves;
  assert(cli.command("set tz.offset -3.5", reply));
  assert(strstr(reply, "could not be saved") && cli.saves == saves + 1);
  assert(cli._prefs.tz_offset == 5.75f && cli.store.fs.files.at("/new_prefs") == disk);
}

int main() {
  static_assert(sizeof(CompanionNodePrefs::tz_offset_legacy) == 1, "legacy timezone slot");
  static_assert(sizeof(CompanionNodePrefs::tz_offset) == 4, "RAM timezone float");
  static_assert(mesh::companion::timezone::FooterSize == 10, "published TZF1 length");
  // Signed legacy hours are literal old bytes, not emitted using the new
  // type or current serializer. Every populated tail must retain its offset.
  for (int8_t hours : {-12, -7, 0, 5, 14}) {
    const auto original = legacyImage(hours);
    DataStore store; store.fs.files["/new_prefs"] = original;
    CompanionNodePrefs loaded; loaded.tz_offset = 8.75f;
    tag(loaded, "local adapter");
    double lat = 1, lon = 2;
    assert(store.loadPrefsInt("/new_prefs", loaded, lat, lon));
    assert(loaded.tz_offset == float(hours) && loaded.tz_offset_legacy == hours);
    assert(lat == 47.125 && lon == -122.25);
    expectLiteralSettings(loaded); expectTag(loaded, "local adapter");
    assert(store.savePrefs(loaded, lat, lon));
    assert(store.fs.files.at("/new_prefs") == original);
    // Pre-tail images already had the one-byte timezone at 225.
    store.fs.files["/new_prefs"].resize(226);
    loaded.tz_offset = -8.75f;
    assert(store.loadPrefsInt("/new_prefs", loaded, lat, lon));
    assert(loaded.tz_offset == float(hours));
  }
  {
    DataStore old; old.fs.files["/new_prefs"] = legacyImage();
    old.fs.files["/new_prefs"].resize(215);
    CompanionNodePrefs defaults; double lat = 1, lon = 2;
    assert(old.loadPrefsInt("/new_prefs", defaults, lat, lon));
    assert(defaults.tz_offset == 0);
    // A missing optional timezone must preserve an exact caller default,
    // including fractional precision introduced after this old image.
    defaults.tz_offset = 5.75f;
    assert(old.loadPrefsInt("/new_prefs", defaults, lat, lon));
    assert(defaults.tz_offset == 5.75f);
  }
  for (const FractionVector& vector : FRACTIONS) {
    DataStore store; store.fs.files["/new_prefs"] = legacyImage();
    CompanionNodePrefs loaded; double lat = 1, lon = 2;
    assert(store.loadPrefsInt("/new_prefs", loaded, lat, lon));
    loaded.tz_offset = vector.hours;
    assert(store.savePrefs(loaded, lat, lon));
    auto expected = legacyImage(static_cast<int8_t>(vector.hours));
    expected.insert(expected.end(), vector.footer.begin(), vector.footer.end());
    assert(store.fs.files.at("/new_prefs") == expected);
    CompanionNodePrefs reboot; reboot.tz_offset = 9.5f;
    tag(reboot, "reboot adapter");
    assert(store.loadPrefsInt("/new_prefs", reboot, lat, lon));
    assert(reboot.tz_offset == vector.hours);
    assert(reboot.tz_offset_legacy == static_cast<int8_t>(vector.hours));
    expectLiteralSettings(reboot); expectTag(reboot, "reboot adapter");
    // Returning to integer hours removes only the optional extension.
    reboot.tz_offset = -7;
    assert(store.savePrefs(reboot, lat, lon));
    assert(store.fs.files.at("/new_prefs") == legacyImage());
  }

  auto complete = legacyImage(5);
  const std::vector<uint8_t> fraction = @FIVE_SEVENTY_FIVE@;
  complete.insert(complete.end(), fraction.begin(), fraction.end());
  const size_t extension = sizeof(LEGACY_IMAGE);
  // A declared fractional image must not silently fall back to its legacy
  // byte when a stream prematurely reports EOF before the checked footers.
  for (size_t cutoff : {size_t(84), extension - size_t(8), extension}) {
    FORCED_EOF_CURSOR = cutoff;
    rejectImage(complete, static_cast<int>(cutoff));
    FORCED_EOF_CURSOR = size_t(-1);
  }
  // Every damaged extension byte fails atomically. Removing the complete
  // optional footer is a legitimate old-format integer image, tested above.
  for (size_t i = 0; i < 10; ++i) {
    auto corrupt = complete; corrupt[extension + i] ^= 0x80;
    rejectImage(corrupt);
    rejectImage(complete, static_cast<int>(extension + i));
    if (i != 0) {
      auto torn = complete; torn.resize(extension + i);
      rejectImage(torn);
    }
  }
  // Correct checksums cannot make unknown versions, NaN, infinity or an
  // out-of-range float valid. These are independent literal wire vectors.
  const std::vector<std::vector<uint8_t>> invalid_footers = {
    @BAD_FOOTERS@
  };
  for (const auto& footer : invalid_footers) {
    auto invalid = legacyImage(5);
    invalid.insert(invalid.end(), footer.begin(), footer.end());
    rejectImage(invalid);
  }
  auto unknown = complete; unknown.push_back(0);
  rejectImage(unknown);
  unknown = complete; unknown.insert(unknown.end(), fraction.begin(), fraction.end());
  rejectImage(unknown);
  unknown = complete; unknown.resize(complete.size() + 65536);
  rejectImage(unknown);

  for (float invalid : {std::numeric_limits<float>::quiet_NaN(),
      std::numeric_limits<float>::infinity(), -std::numeric_limits<float>::infinity(),
      14.25f, -12.25f}) {
    DataStore store; store.fs.files["/new_prefs"] = legacyImage();
    CompanionNodePrefs prefs; double lat = 1, lon = 2;
    assert(store.loadPrefsInt("/new_prefs", prefs, lat, lon));
    prefs.tz_offset = invalid;
    const auto before = store.fs.files;
    assert(!store.savePrefs(prefs, lat, lon));
    assert(store.fs.files == before); // validation precedes temporary-file work
  }
  // Short writes/readback at each new footer boundary must leave the prior
  // complete image authoritative. The next successful save must still work.
  for (bool readback : {false, true}) {
    for (size_t i = 0; i < 10; ++i) {
      DataStore store; store.fs.files["/new_prefs"] = legacyImage();
      CompanionNodePrefs prefs; double lat = 1, lon = 2;
      assert(store.loadPrefsInt("/new_prefs", prefs, lat, lon));
      prefs.tz_offset = -3.75f; prefs.freq = 910.5f;
      if (readback) store.fs.fail_read_after = static_cast<int>(extension + i);
      else store.fs.fail_write_after = static_cast<int>(extension + i);
      assert(!store.savePrefs(prefs, lat, lon));
      assert(store.fs.files.at("/new_prefs") == legacyImage());
      assert(!store.fs.exists("/new_prefs.tmp"));
      store.fs.fail_read_after = store.fs.fail_write_after = -1;
      CompanionNodePrefs prior;
      assert(store.loadPrefsInt("/new_prefs", prior, lat, lon));
      assert(prior.tz_offset == -7); expectLiteralSettings(prior);
      assert(store.savePrefs(prefs, lat, lon));
      assert(store.loadPrefsInt("/new_prefs", prior, lat, lon));
      assert(prior.tz_offset == -3.75f && prior.freq == 910.5f);
    }
  }
  cliTests();
  puts("PASS: literal legacy timezone ABI, fractional footer, atomic faults and CLI rollback");
}
'''


class CompanionTimezonePrefsTests(unittest.TestCase):
    def test_ieee_validation_and_footer_under_size_and_math_modes(self):
        # Expected validity comes from Python's decoded binary32 values, not
        # the production bit thresholds or a compiler's fast-math comparisons.
        bits = {
            0, 0x80000000, 1, 0x80000001, 0x007fffff, 0x807fffff,
            0x00800000, 0x80800000, 0x7f7fffff, 0xff7fffff,
            0x7f800000, 0xff800000, 0x7f800001, 0xff800001,
            0x7fc00000, 0xffc00000, 0x7fffffff, 0xffffffff,
        }
        for hours in (-14.0, -12.0, 12.0, 14.0, -3.75, 5.75):
            center = struct.unpack("<I", struct.pack("<f", hours))[0]
            bits.update((center - 1, center, center + 1))
        generator = random.Random(0x545a4631)
        bits.update(generator.getrandbits(32) for _ in range(1024))
        rows = []
        for raw in sorted(bits):
            hours = struct.unpack("<f", struct.pack("<I", raw))[0]
            valid = math.isfinite(hours) and -12.0 <= hours <= 14.0
            # Use the original bits directly: unpacking/repacking a signaling
            # NaN through Python could otherwise quiet its payload.
            footer = checked_footer(b"TZF\x01" + struct.pack("<I", raw))
            rows.append("{" + f"0x{raw:08x}u,{str(valid).lower()}," + cpp_bytes(footer) + "}")
        code = r'''
#include <cassert>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <helpers/CompanionTimezonePrefs.h>
struct Case { uint32_t bits; bool valid; uint8_t footer[10]; };
static const Case CASES[] = {
@CASES@
};
int main() {
  unsigned valid_cases = 0, invalid_cases = 0;
  for (const Case& test : CASES) {
    float hours;
    memcpy(&hours, &test.bits, 4);
    assert(mesh::companion::timezone::valid(hours) == test.valid);
    const uint32_t sentinel = 0x40b80000u; // exactly 5.75
    float decoded;
    memcpy(&decoded, &sentinel, 4);
    if (test.valid) {
      ++valid_cases;
      uint8_t footer[10];
      mesh::companion::timezone::encodeFooter(footer, hours);
      assert(!memcmp(footer, test.footer, sizeof(footer)));
      assert(mesh::companion::timezone::decodeFooter(footer, sizeof(footer), decoded));
      uint32_t restored;
      memcpy(&restored, &decoded, 4);
      assert(restored == test.bits); // includes negative zero and subnormals
    } else {
      ++invalid_cases;
      // A correct header/checksum cannot admit NaN, infinity or bad range,
      // even when floating-point comparisons are compiled with fast-math.
      assert(!mesh::companion::timezone::decodeFooter(test.footer, 10, decoded));
      uint32_t unchanged;
      memcpy(&unchanged, &decoded, 4);
      assert(unchanged == sentinel);
    }
  }
  assert(valid_cases > 400 && invalid_cases > 400);
  puts("PASS: independent IEEE validity and exact timezone footer bits/CRC");
}
'''.replace("@CASES@", ",\n".join(rows))
        with tempfile.TemporaryDirectory(prefix="meshcore-timezone-ieee-") as directory:
            work = Path(directory)
            cpp = work / "test.cpp"
            cpp.write_text(code)
            for mode, flags in (("size", []), ("fast-math", ["-ffast-math"]),
                                ("portable-endian", ["-U__BYTE_ORDER__"])):
                with self.subTest(mode=mode):
                    binary = work / "test"
                    built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17", "-Os",
                        *flags, "-I", str(ROOT / "src"), str(cpp), "-o", str(binary)],
                        capture_output=True, text=True, timeout=60)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                    self.assertIn("PASS: independent IEEE validity", ran.stdout)

    def test_literal_legacy_fractional_persistence_and_cli(self):
        source = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        methods = esp_recovery_helpers(source) + "\n".join(method(source, signature) for signature in (
            "bool DataStore::loadPrefs(", "bool DataStore::loadPrefsInt(", "bool DataStore::savePrefs("))
        code = HARNESS.split("int main(")[0].replace("@METHODS@", methods)
        getter = method(mesh, '  if (strcmp(command, "get tz.offset") == 0)')
        setter = method(mesh, '  if (strncmp(command, "set tz.offset ", 14) == 0)')
        header = (ROOT / "examples/companion_radio/MyMesh.h").read_text()
        save_preference = method(header, 'template <typename T> bool savePreference(')
        tests = TESTS.replace("@GETTER@", getter).replace("@SETTER@", setter)
        tests = tests.replace("@SAVE_PREFERENCE@", save_preference)
        values = (5.5, 5.75, -3.5, -3.75, 12.75, 13.75, -11.75, .25, -.25)
        tests = tests.replace("@FRACTIONS@", ",\n  ".join(
            "{" + str(value) + "f," + cpp_bytes(fractional_footer(value)) + "}" for value in values))
        tests = tests.replace("@FIVE_SEVENTY_FIVE@", cpp_bytes(fractional_footer(5.75)))
        bad = [checked_footer(b"TZF\x02" + struct.pack("<f", 5.75)),
               checked_footer(b"TZX\x01" + struct.pack("<f", 5.75))]
        bad += [checked_footer(b"TZF\x01" + struct.pack("<I", bits)) for bits in (
            0x7fc00000, 0x7f800000, 0xff800000, 0x41640000, 0xc1440000)]
        tests = tests.replace("@BAD_FOOTERS@", ",\n    ".join(cpp_bytes(value) for value in bad))
        with tempfile.TemporaryDirectory(prefix="meshcore-timezone-prefs-") as directory:
            work = Path(directory)
            (work / "Utils.h").write_text('''#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
''')
            (work / "platform_shim.h").write_text('''#include <cstdlib>
#include <cstdio>
inline char* utoa(unsigned int value,char* output,int base){
 if(base!=10)abort();sprintf(output,"%u",value);return output;
}
''')
            # Only the transport boundary is fault-injected: size() retains
            # the complete declared image while available() can report EOF.
            mock = (ROOT / "test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h").read_text()
            mock = mock.replace("class MemoryFS;", "static size_t FORCED_EOF_CURSOR = size_t(-1);\nclass MemoryFS;", 1)
            available = "size_t available() const { return size() - cursor_; }"
            self.assertEqual(mock.count(available), 1)
            mock = mock.replace(available,
                "size_t available() const { return cursor_ >= FORCED_EOF_CURSOR ? 0 : size() - cursor_; }")
            (work / "helpers").mkdir()
            (work / "helpers/IdentityStore.h").write_text(mock)
            transaction = (ROOT / "src/helpers/ContactFileTransaction.h").read_text()
            (work / "ContactFileTransaction.h").write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            for platform in PLATFORMS:
                with self.subTest(platform=platform):
                    cpp = work / "test.cpp"
                    cpp.write_text(code + tests.replace(
                        "@LEGACY_IMAGE@", cpp_bytes(literal_legacy_image(platform))))
                    binary = work / "test"
                    sanitizers = [] if os.name == "nt" else [
                        "-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-fno-pie", "-no-pie"]
                    built = subprocess.run([os.environ.get("CXX", "g++"), "-std=c++17",
                        *["-D" + flag + "=1" for flag in platform.split(",")], *sanitizers,
                        "-include", str(work / "platform_shim.h"), "-I", str(work),
                        "-I", str(ROOT / "test/fixtures/radio_profiles/mocks"),
                        "-I", str(ROOT / "test/mocks"), "-I", str(ROOT / "src"),
                        "-I", str(ROOT / "src/helpers"), "-I", str(ROOT), str(cpp),
                        *[str(ROOT / "src/helpers" / name) for name in (
                            "ConfigSerializer.cpp", "DynamicConfigSerializer.cpp",
                            "CommonRadioPrefs.cpp", "TxtDataHelpers.cpp")],
                        "-o", str(binary)], capture_output=True, text=True, timeout=90)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                    self.assertIn("PASS: literal legacy timezone ABI", ran.stdout)


if __name__ == "__main__":
    unittest.main()
