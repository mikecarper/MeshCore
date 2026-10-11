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
}

static void equalValues(const CompanionNodePrefs& a,
                        const CompanionNodePrefs& b) {
  @COMPARE_FIELDS@
}

static size_t adapterOffset(const CompanionNodePrefs& prefs) {
  // The value helper does not copy the adapters or their inter-field padding.
  // Use the complete object's byte representation for bounded observation.
  const uintptr_t first = reinterpret_cast<uintptr_t>(&prefs);
  const uintptr_t last = reinterpret_cast<uintptr_t>(&prefs.lost_reply);
  assert(last >= first && last - first < sizeof(prefs));
  return static_cast<size_t>(last - first) + sizeof(prefs.lost_reply);
}

static std::vector<uint8_t> adapterBytes(const CompanionNodePrefs& prefs) {
  const auto* bytes = reinterpret_cast<const uint8_t*>(&prefs);
  return {bytes + adapterOffset(prefs), bytes + sizeof(prefs)};
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
    DataStore image_store;
    assert(image_store.savePrefs(saved, 47.125, -122.25));
    const auto image = image_store.fs.files["/new_prefs"];
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
          // Every absent optional field observes EOF, then the completed
          // image receives the existing final trailing-byte check.
          assert(AVAILABLE_CALLS == optional_fields + 1);
        }
        equalValues(actual, legacy);
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
        self.assertEqual(declarations[-1], 'lost_reply')
        optional = {
            'fan_mode': 'TBEAM_1W', 'fan_lo': 'TBEAM_1W', 'fan_hi': 'TBEAM_1W',
            'wifi_ssid': 'defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)',
            'wifi_pwd': 'defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)',
        }

        def fields(template):
            rows = []
            for name in declarations:
                row = template.format(name=name)
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
            for platform in ('STM32_PLATFORM', 'NRF52_PLATFORM', 'ESP32_PLATFORM', 'RP2040_PLATFORM',
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
                            'CommonRadioPrefs.cpp', 'TxtDataHelpers.cpp')],
                        '-o', str(binary)], capture_output=True, text=True)
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    self.assertNotIn('offsetof', built.stderr)
                    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
