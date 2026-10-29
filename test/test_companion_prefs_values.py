"""Exercise value-only snapshots using the real adapter-bearing preferences."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS, esp_recovery_helpers
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

TESTS = r'''
#include <type_traits>
#include <vector>
@FIELD_CHECKS@

static void seedValues(CompanionNodePrefs& prefs, uint32_t& random) {
  auto fill = [&](void* destination, size_t size) {
    auto* bytes = static_cast<uint8_t*>(destination);
    for (size_t i = 0; i < size; ++i) {
      random = random * 1664525u + 1013904223u;
      bytes[i] = static_cast<uint8_t>(random >> 24);
    }
  };
  @SEED_FIELDS@
  prefs.tz_offset = float(int(random % 27) - 12);
  prefs.tz_offset_legacy = static_cast<int8_t>(prefs.tz_offset);
}

static void equalValues(const CompanionNodePrefs& a,
                        const CompanionNodePrefs& b) {
  @COMPARE_FIELDS@
  assert(a.tz_offset == b.tz_offset);
}

static size_t adapterOffset(const CompanionNodePrefs& prefs) {
  // The value helper does not copy the adapters or their inter-field padding.
  // Use the complete object's byte representation for bounded observation.
  const uintptr_t first = reinterpret_cast<uintptr_t>(&prefs);
  const uintptr_t last = reinterpret_cast<uintptr_t>(&prefs.usb_logging_always);
  assert(last >= first && last - first < sizeof(prefs));
  return static_cast<size_t>(last - first) + sizeof(prefs.usb_logging_always);
}

static std::vector<uint8_t> adapterBytes(const CompanionNodePrefs& prefs) {
  const auto* bytes = reinterpret_cast<const uint8_t*>(&prefs);
  // The separately copied runtime timezone follows both adapters.
  return {bytes + adapterOffset(prefs),
      reinterpret_cast<const uint8_t*>(&prefs.tz_offset)};
}

static void expectTag(CompanionNodePrefs& prefs, const char* expected) {
  char value[32] = {};
  assert(prefs.getCustom()->getByKey("snapshot_tag", value, sizeof(value)));
  assert(!strcmp(value, expected));
}

int main() {
  uint32_t random = 0x19283746;
  for (unsigned trial = 0; trial < 64; ++trial) {
    CompanionNodePrefs source, snapshot;
    seedValues(source, random);
    source.getRadioPrefs()->setFreq(915.25f);
    assert(source.getCustom()->setByKey("snapshot_tag", "source"));
    assert(snapshot.getCustom()->setByKey("snapshot_tag", "snapshot"));
    snapshot.clearDirty();
    assert(source.isDirty() && !snapshot.isDirty());
    auto source_adapters = adapterBytes(source);
    auto snapshot_adapters = adapterBytes(snapshot);
    assert(snapshot.copyPersistedValuesFrom(source));
    equalValues(source, snapshot);
    assert(adapterBytes(source) == source_adapters);
    assert(adapterBytes(snapshot) == snapshot_adapters);
    assert(source.isDirty() && !snapshot.isDirty());
    expectTag(source, "source"); expectTag(snapshot, "snapshot");
    const auto* bytes = reinterpret_cast<const uint8_t*>(&snapshot);
    std::vector<uint8_t> before_self(bytes, bytes + sizeof(snapshot));
    assert(snapshot.copyPersistedValuesFrom(snapshot));
    assert(!memcmp(&snapshot, before_self.data(), before_self.size()));
    // A normally constructed scratch adapter must still own the scratch
    // object, including its dynamic-config fallback after copying values.
    snapshot.getRadioPrefs()->setFreq(910.5f);
    assert(snapshot.freq == 910.5f && source.freq == 915.25f);
    source.radio_fem_txgain = 0;
    assert(snapshot.getCustom()->setByKey("fem_txgain", "1"));
    assert(snapshot.radio_fem_txgain == 1 && source.radio_fem_txgain == 0);
    source.getRadioPrefs()->setFreq(920.5f);
    assert(source.freq == 920.5f && snapshot.freq == 910.5f);
  }

  unsigned comparisons = 0;
  for (unsigned trial = 0; trial < 3; ++trial) {
    CompanionNodePrefs saved;
    seedValues(saved, random);
    saved.bluetooth_enabled = static_cast<uint8_t>(trial);
    saved.usb_logging_always = trial & 1;
    DataStore image_store;
    assert(image_store.savePrefs(saved, 47.125, -122.25));
    auto image = image_store.fs.files["/new_prefs"];
    assert(image[36] == (saved.usb_logging_always ? 0xa1 : 0));
    assert(image[37] == 0 && image[38] == 0 && image[39] == 0);
#if defined(WIO_E5_MINI_HEADLESS_COMPANION_UI)
    assert(image[79] == saved.leds_quiet);
#endif
    // The frozen reference remains authoritative only for the historical
    // prefix. Checked new-footer behavior has dedicated persistence tests.
    image.resize(image.size()-mesh::companion::traffic::FooterSize);
    // Test every possible old/torn image length, including every officially
    // supported append boundary and unknown lengths beyond the current tail.
    for (size_t length = 0; length <= image.size() + 5; ++length) {
      auto disk = image;
      disk.resize(length);
      for (size_t quota = 0; quota <= length; ++quota) {
        DataStore actual_store, legacy_store;
        actual_store.fs.files["/new_prefs"] = disk;
        legacy_store.fs.files["/new_prefs"] = disk;
        actual_store.fs.fail_read_after = static_cast<int>(quota);
        legacy_store.fs.fail_read_after = static_cast<int>(quota);
        CompanionNodePrefs actual, legacy;
        seedValues(actual, random);
        assert(legacy.copyPersistedValuesFrom(actual));
        actual.getRadioPrefs()->setFreq(915.25f);
        legacy.getRadioPrefs()->setFreq(915.25f);
        assert(actual.getCustom()->setByKey("snapshot_tag", "actual"));
        assert(legacy.getCustom()->setByKey("snapshot_tag", "legacy"));
        auto actual_adapters = adapterBytes(actual);
        auto legacy_adapters = adapterBytes(legacy);
        double lat = 1, lon = 2, legacy_lat = 1, legacy_lon = 2;
        READ_CALLS.clear(); AVAILABLE_CALLS = 0;
        const bool expected = legacy_store.loadPrefsLegacy(
            "/new_prefs", legacy, legacy_lat, legacy_lon);
        const auto expected_reads = READ_CALLS;
        const unsigned expected_available = AVAILABLE_CALLS;
        READ_CALLS.clear(); AVAILABLE_CALLS = 0;
        const bool got = actual_store.loadPrefsInt(
            "/new_prefs", actual, lat, lon);
        assert(got == expected);
        assert(READ_CALLS == expected_reads);
        // Footer presence follows the already validated declared size; every
        // historical field read and EOF probe still matches the old reader.
        assert(AVAILABLE_CALLS == expected_available);
        if (length == 84 && quota == 84) {
          // The original mandatory prefix has 21 reads, ending at ble_pin.
          // This order is an independent wire contract, not the new table.
          const std::vector<size_t> mandatory = {
              4, 32, 4, 8, 8, 4, 1, 1, 1, 1, 4, 1, 1, 1, 1,
              4, 1, 1, 1, 1, 4};
          assert(expected && READ_CALLS == mandatory);
          unsigned optional_fields = 46;
#ifdef TBEAM_1W
          optional_fields += 3;
#endif
#if defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
          optional_fields += 2;
#endif
#if defined(RAK_4631)
          optional_fields += 1; // Checked peripheral footer has its own EOF probe.
#endif
          // Every absent optional field observes EOF, then the completed
          // image receives the existing final trailing-byte check.
          assert(AVAILABLE_CALLS == optional_fields + 1);
        }
        if(got)legacy.tx_media=legacy.tx_ssh=mesh::RADIO_TX_AUTO;
        equalValues(actual, legacy);
#if defined(WIO_E5_MINI_HEADLESS_COMPANION_UI)
        if (got) assert(actual.leds_quiet == disk[79]);
#endif
        assert(!memcmp(&lat, &legacy_lat, sizeof(lat)));
        assert(!memcmp(&lon, &legacy_lon, sizeof(lon)));
        assert(actual_store.fs.files["/new_prefs"] == disk);
        assert(legacy_store.fs.files["/new_prefs"] == disk);
        assert(adapterBytes(actual) == actual_adapters);
        assert(adapterBytes(legacy) == legacy_adapters);
        assert(actual.isDirty() && legacy.isDirty());
        expectTag(actual, "actual"); expectTag(legacy, "legacy");
        auto legacy_txgain = legacy.radio_fem_txgain;
        assert(actual.getCustom()->setByKey("fem_txgain", "1"));
        assert(actual.radio_fem_txgain == 1
            && legacy.radio_fem_txgain == legacy_txgain);
        ++comparisons;
      }
    }
  }
  printf("PASS: %u actual-loader value/adapter/failure comparisons\n", comparisons);
}
'''


class CompanionPrefsValuesTests(unittest.TestCase):
    def test_value_only_copy_and_full_loader_match_legacy(self):
        source = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        loader = method(source, 'bool DataStore::loadPrefsInt(')
        # A frozen sequential reader, not a clone of the current field table,
        # is the authority for all old/torn images and read failure traces.
        legacy = (ROOT / 'test/fixtures/companion_prefs_storage/legacy_loader.inc').read_text()
        self.assertNotIn('copyPersistedValuesFrom', legacy)
        # The headless Mini assigns the existing reserved byte at 79 to LED
        # policy. Keep the independent scalar read and every failure boundary;
        # only this target stores its value instead of discarding it as padding.
        reserved_byte = '    readField(pad, 1);'
        self.assertEqual(legacy.count(reserved_byte), 1)
        legacy = legacy.replace(reserved_byte,
            '#if defined(WIO_E5_MINI_HEADLESS_COMPANION_UI)\n'
            '    readField(&loaded_prefs.leds_quiet, 1);\n'
            '#else\n' + reserved_byte + '\n#endif')
        # The always-log opt-in uses the first byte of the existing reserved
        # word at 36. Keep its one historical four-byte read and independent
        # literal marker decoding, including all failed-read boundaries.
        logging_word = '    readField(pad, 4);'
        self.assertEqual(legacy.count(logging_word), 1)
        legacy = legacy.replace(logging_word, logging_word + '\n'
            '    loaded_prefs.usb_logging_always = success && pad[0] == 0xa1;')
        # Keep the frozen scalar prefix independent of the new GNU table.
        # The RAK-only checked tail was published later; add it explicitly
        # to this test-local reference without rewriting the frozen fixture.
        legacy = legacy.replace('    loaded_prefs.lost_reply = 0;',
            '    loaded_prefs.lost_reply = 0;\n'
            '#if defined(RAK_4631)\n    loaded_prefs.peripherals_shared = 0;\n#endif')
        size_check = '    if (prefs_size < MIN_PREFS_SIZE || !known_size) {'
        self.assertEqual(legacy.count(size_check), 1)
        legacy = legacy.replace(size_check,
            '#if defined(RAK_4631)\n'
            '    known_size = known_size || prefs_size == 244;\n'
            '#endif\n' + size_check)
        trailing_check = '    // Any bytes left over form only part of a historically appended field.'
        self.assertEqual(legacy.count(trailing_check), 1)
        legacy = legacy.replace(trailing_check,
            '#if defined(RAK_4631)\n'
            '    if (file.available() != 0) {\n'
            '      uint8_t footer[8] = {};\n'
            '      success = success && readField(footer, sizeof(footer))\n'
            '          && mesh::peripherals::decodeFooter(footer, sizeof(footer),\n'
            '              loaded_prefs.peripherals_shared);\n'
            '    }\n#endif\n\n' + trailing_check)
        methods = esp_recovery_helpers(source) + '\n'.join((loader, legacy,
            method(source, 'bool DataStore::savePrefs(')))
        code = HARNESS.split('int main(')[0].replace('@METHODS@', methods)
        code = code.replace(' bool loadPrefsInt(const char*, CompanionNodePrefs&, double&, double&);',
                            ' bool loadPrefsInt(const char*, CompanionNodePrefs&, double&, double&);\n'
                            ' bool loadPrefsLegacy(const char*, CompanionNodePrefs&, double&, double&);')
        prefs = (ROOT / 'examples/companion_radio/NodePrefs.h').read_text()
        prefix = prefs[prefs.index('class CompanionNodePrefs {'):prefs.index('\nprivate:')]
        declarations = re.findall(
            r'^\s*(?:float|char|u?int(?:8|16|32)_t)\s+([A-Za-z_][A-Za-z_0-9]*)',
            prefix, re.M)
        self.assertEqual(declarations[0], 'airtime_factor')
        self.assertEqual(declarations[-3:], ['tx_media', 'tx_ssh', 'usb_logging_always'])
        optional = {
            'fan_mode': 'TBEAM_1W', 'fan_lo': 'TBEAM_1W', 'fan_hi': 'TBEAM_1W',
            'wifi_ssid': 'defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)',
            'wifi_pwd': 'defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)',
            'peripherals_shared': 'RAK_4631',
            'leds_quiet': 'WIO_E5_MINI_HEADLESS_COMPANION_UI',
            'cpu_freq_mhz': 'ESP32_PLATFORM',
        }

        def fields(template):
            rows = []
            for name in declarations:
                row = template.format(name=name)
                # Historical byte/EOF comparisons use Auto; explicit CPU
                # footer round trips have their own actual-codec fault suite.
                if name == 'cpu_freq_mhz' and template.startswith('fill('):
                    row = 'prefs.cpu_freq_mhz = 0;'
                if name in optional:
                    condition = optional[name]
                    if not condition.startswith('defined('):
                        condition = 'defined(' + condition + ')'
                    row = '#if ' + condition + '\n' + row + '\n#endif'
                rows.append(row)
            return '\n'.join(rows)

        tests = TESTS.replace('@FIELD_CHECKS@', fields(
            'static_assert(std::is_trivially_copyable<decltype(CompanionNodePrefs::{name})>::value,\n'
            '              "A persisted snapshot member must remain a value");'))
        tests = tests.replace('@SEED_FIELDS@', fields('fill(&prefs.{name}, sizeof(prefs.{name}));'))
        tests = tests.replace('@COMPARE_FIELDS@', fields(
            'assert(!memcmp(&a.{name}, &b.{name}, sizeof(a.{name})));'))
        with tempfile.TemporaryDirectory(prefix='meshcore-prefs-values-') as directory:
            work = Path(directory)
            (work / 'Utils.h').write_text('''#pragma once
#include <Arduino.h>
#include <cassert>
namespace mesh { struct Utils {
 static void printHex(Stream&,const uint8_t*,size_t){assert(false);}
 static void fromHex(uint8_t*,size_t,const char*){assert(false);}
}; }
''')
            (work / 'platform_shim.h').write_text('''#include <cstdlib>
#include <cstdio>
inline char* utoa(unsigned int value,char* output,int base){
 if(base!=10)abort();sprintf(output,"%u",value);return output;
}
''')
            transaction = (ROOT / 'src/helpers/ContactFileTransaction.h').read_text()
            (work / 'ContactFileTransaction.h').write_text(transaction.replace(
                '#include "IdentityStore.h"', '#include <helpers/IdentityStore.h>'))
            # Instrument only the existing filesystem boundary. Both actual
            # readers and the real transactional writer remain unchanged.
            fs = (ROOT / 'test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h').read_text()
            fs = fs.replace('class MemoryFS;',
                            'static std::vector<size_t> READ_CALLS;\n'
                            'static unsigned AVAILABLE_CALLS = 0;\nclass MemoryFS;')
            fs = fs.replace('size_t available() const { return size() - cursor_; }',
                            'size_t available() const { ++AVAILABLE_CALLS;return size() - cursor_; }')
            fs = fs.replace('inline int File::read(uint8_t* data, size_t size) {',
                            'inline int File::read(uint8_t* data, size_t size) {\n'
                            '  READ_CALLS.push_back(size);')
            (work / 'helpers').mkdir()
            (work / 'helpers/IdentityStore.h').write_text(fs)
            cpp = work / 'test.cpp'
            cpp.write_text(code + tests)
            for platform in ('STM32_PLATFORM', 'STM32_PLATFORM,WIO_E5_MINI_HEADLESS_COMPANION_UI',
                             'NRF52_PLATFORM', 'NRF52_PLATFORM,RAK_4631', 'ESP32_PLATFORM', 'RP2040_PLATFORM',
                             'ESP32_PLATFORM,TBEAM_1W', 'RP2040_PLATFORM,ENABLE_WIFI_INTERFACE'):
                with self.subTest(platform=platform):
                    binary = work / 'test'
                    sanitizers = [] if os.name == 'nt' else [
                        '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                        '-fno-pie', '-no-pie']
                    built = subprocess.run([os.environ.get('CXX', 'g++'), '-std=c++17',
                        *['-D' + flag + '=1' for flag in platform.split(',')],
                        *sanitizers, '-include', str(work / 'platform_shim.h'),
                        '-I', str(work), '-I', str(ROOT / 'test/fixtures/radio_profiles/mocks'),
                        '-I', str(ROOT / 'test/mocks'), '-I', str(ROOT / 'src'),
                        '-I', str(ROOT / 'src/helpers'), '-I', str(ROOT), str(cpp),
                        *[str(ROOT / 'src/helpers' / name) for name in (
                            'ConfigSerializer.cpp', 'DynamicConfigSerializer.cpp',
                            'CommonRadioPrefs.cpp', 'radiolib/RXPowerSaving.cpp',
                            'radiolib/RXPowerSavingCLI.cpp', 'TxtDataHelpers.cpp')],
                        '-o', str(binary)], capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    self.assertNotIn('offsetof', built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
