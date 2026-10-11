"""Check the explicit preference wire layout and bounded STM32 staging."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

from test_companion_preferences_transaction import HARNESS, esp_recovery_helpers
from test_radio_receive_contract import method

ROOT = Path(__file__).resolve().parents[1]

# Published field order and sizes, independent of the implementation's emitter
# and the class's in-memory member order or padding. None represents zero pad.
WIRE_FIELDS = (
    ('airtime_factor', 4), ('node_name', 32), (None, 4),
    ('@lat', 8), ('@lon', 8), ('freq', 4), ('sf', 1), ('cr', 1),
    ('client_repeat', 1), ('manual_add_contacts', 1), ('bw', 4),
    ('tx_power_dbm', 1), ('telemetry_mode_base', 1), ('telemetry_mode_loc', 1),
    ('telemetry_mode_env', 1), ('rx_delay_base', 4), ('advert_loc_policy', 1),
    ('multi_acks', 1), ('path_hash_mode', 1), (None, 1), ('ble_pin', 4),
    ('buzzer_quiet', 1), ('gps_enabled', 1), ('gps_interval', 4),
    ('autoadd_config', 1), ('autoadd_max_hops', 1), ('rx_boosted_gain', 1),
    ('default_scope_name', 31), ('default_scope_key', 16),
    ('radio_fem_rxgain', 1), ('radio_fem_rxgain_override', 1),
    ('vibe_quiet', 1), ('radio_fem_txgain', 1), ('rx_powersaving_enabled', 1),
    ('rx_ps_rx_us', 4), ('rx_ps_sleep_us', 4), ('rx_ps_level', 1),
    ('rx_ps_preamble', 1), ('powersaving_enabled', 1), ('wifi_enabled', 1),
    ('powersaving_policy_version', 1), ('usb_logging_enabled', 1),
    ('bluetooth_name', 32), ('display_rotation_degrees', 2), ('cad_enabled', 1),
    ('cad_scan_timeout_ms', 2), ('cad_retry_delay_ms', 2),
    ('cad_max_duration_ms', 2), ('bluetooth_mac_mode', 1), ('bluetooth_mac', 6),
    ('bluetooth_stealth_peer_type', 1), ('bluetooth_stealth_peer', 6),
    ('bluetooth_stealth_mode', 1), ('tx_delay_factor', 4),
    ('direct_tx_delay_factor', 4), ('interference_threshold', 1),
    ('agc_reset_interval', 1), ('tz_offset', 1),
)
TAIL_FIELDS = (
    ('flood_retry_attempts', 1), ('flood_retry_max_path', 1),
    ('flood_retry_group_max_path', 1), ('flood_retry_advert_enabled', 1),
    ('one_key_dm_enabled', 1), ('bluetooth_enabled', 1),
    ('gps_sync_interval_hours', 2), ('usb_debug_enabled', 1),
    ('lost_reply', 1),
)
OPTIONAL_FIELDS = (
    ('defined(TBEAM_1W)', (('fan_mode', 5), ('fan_lo', 1), ('fan_hi', 1))),
    ('defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)',
     (('wifi_ssid', 33), ('wifi_pwd', 64))),
)

TESTS = r'''
#include <algorithm>
#include <vector>

static void seed(CompanionNodePrefs& prefs, uint32_t& random) {
  auto fill = [&](void* destination, size_t size) {
    auto* bytes = static_cast<uint8_t*>(destination);
    for (size_t i = 0; i < size; ++i) {
      random = random * 1664525u + 1013904223u;
      bytes[i] = static_cast<uint8_t>(random >> 24);
    }
  };
  @SEED@
}

static std::vector<uint8_t> golden(const CompanionNodePrefs& prefs,
                                   double lat, double lon) {
  std::vector<uint8_t> bytes;
  auto append = [&](const void* data, size_t size) {
    const auto* first = static_cast<const uint8_t*>(data);
    bytes.insert(bytes.end(), first, first + size);
  };
  @GOLDEN@
#if defined(TBEAM_1W)
  assert(bytes.size() == 243);
#elif defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
  assert(bytes.size() == 333);
#else
  assert(bytes.size() == 236);
#endif
  return bytes;
}

static void unchanged(const DataStore& store,
                      const std::vector<uint8_t>& before) {
  assert(store.fs.files.at("/new_prefs") == before);
  assert(!store.fs.exists("/new_prefs.tmp"));
}

int main() {
  uint32_t random = 0x31415926;
  unsigned comparisons = 0;
  for (unsigned trial = 0; trial < 512; ++trial) {
    CompanionNodePrefs prefs;
    // Exercise every persisted byte, not a memcpy of the nontrivial class.
    // Raw invalid values are intentionally retained by the writer; validation
    // and migration remain the loader/CLI's existing responsibility.
    seed(prefs, random);
    DataStore store;
    assert(store.savePrefs(prefs, 47.125, -122.25));
    assert(store.fs.files.at("/new_prefs") == golden(prefs, 47.125, -122.25));
    assert(!store.fs.exists("/new_prefs.tmp"));
    ++comparisons;
  }

  CompanionNodePrefs original;
  assert(original.lost_reply == 0);
  strcpy(original.node_name, "durable image");
  original.freq = 915.25f; original.ble_pin = 123456;
  original.bluetooth_enabled = 1; original.usb_debug_enabled = 1;
  original.gps_sync_interval_hours = 336;
  original.lost_reply = 2;
  DataStore initial;
  assert(initial.savePrefs(original, 47.125, -122.25));
  const auto before = golden(original, 47.125, -122.25);
  assert(initial.fs.files.at("/new_prefs") == before);

  // Every complete historical wire boundary stays readable, and an old
  // image always disables the new opt-in response even if live RAM was on.
  const std::vector<size_t> legacy_sizes = {
      84, 85, 90, 91, 92, 93, 140, 141, 142, 143, 144, 155,
      156, 157, 158, 159, 191, 193, 200, 207, 214, 215, 226,
#if defined(TBEAM_1W)
      // The generic 231-byte compatibility boundary also ends at TBeam's
      // complete fan-mode string; preserve that existing accepted image.
      231, 233, 237, 238, 239, 241, 242
#elif defined(RP2040_PLATFORM) && defined(ENABLE_WIFI_INTERFACE)
      323, 327, 328, 329, 331, 332
#else
      230, 231, 232, 234, 235
#endif
  };
  for (size_t length : legacy_sizes) {
    DataStore reboot;
    auto historical = before; historical.resize(length);
    reboot.fs.files["/new_prefs"] = historical;
    CompanionNodePrefs live; live.lost_reply = 2;
    live.getCustom()->setByKey("local_tag", "unchanged");
    double lat = 1, lon = 2;
    assert(reboot.loadPrefsInt("/new_prefs", live, lat, lon));
    assert(live.lost_reply == 0 && live.ble_pin == original.ble_pin);
    assert(live.freq == original.freq && !strcmp(live.node_name, original.node_name));
    assert(lat == 47.125 && lon == -122.25);
    assert(reboot.fs.files.at("/new_prefs") == historical);
    char tag[16] = {};
    assert(live.getCustom()->getByKey("local_tag", tag, sizeof(tag)));
    assert(!strcmp(tag, "unchanged"));
    ++comparisons;
  }
  // The accepted-size table is an exact allowlist. Check every length through
  // a full extra byte range, including sizes that could alias legacy values
  // if the underlying file size were accidentally narrowed with the table.
  std::vector<size_t> accepted_sizes = legacy_sizes;
  accepted_sizes.push_back(before.size());
  for (size_t length = 0; length <= before.size() + 256; ++length) {
    DataStore reboot;
    auto candidate = before;
    candidate.resize(length);
    reboot.fs.files["/new_prefs"] = candidate;
    CompanionNodePrefs live;
    live.freq = 920.5f;
    live.lost_reply = 1;
    live.getCustom()->setByKey("length_tag", "unchanged");
    double lat = 1, lon = 2;
    const bool expected = std::find(accepted_sizes.begin(), accepted_sizes.end(),
                                    length) != accepted_sizes.end();
    assert(reboot.loadPrefsInt("/new_prefs", live, lat, lon) == expected);
    if (expected) {
      assert(live.freq == original.freq);
      assert(live.lost_reply == (length == before.size() ? 2 : 0));
      assert(lat == 47.125 && lon == -122.25);
    } else {
      assert(live.freq == 920.5f && live.lost_reply == 1);
      assert(lat == 1 && lon == 2);
    }
    char tag[16] = {};
    assert(live.getCustom()->getByKey("length_tag", tag, sizeof(tag)));
    assert(!strcmp(tag, "unchanged"));
    assert(reboot.fs.files.at("/new_prefs") == candidate);
    ++comparisons;
  }
  // All 256 byte encodings are preserved by the append-only serializer.
  // Only off/no/yes are accepted on reboot; corrupt values normalize off
  // without erasing radio settings or changing the committed image.
  for (unsigned value = 0; value < 256; ++value) {
    CompanionNodePrefs saved;
    assert(saved.copyPersistedValuesFrom(original));
    saved.lost_reply = static_cast<uint8_t>(value);
    DataStore writer;
    assert(writer.savePrefs(saved, 47.125, -122.25));
    const auto disk = writer.fs.files.at("/new_prefs");
    assert(disk.back() == value);
    assert(std::equal(disk.begin(), disk.end() - 1, before.begin()));
    DataStore reboot; reboot.fs.files["/new_prefs"] = disk;
    CompanionNodePrefs live; live.lost_reply = 2;
    double lat = 1, lon = 2;
    assert(reboot.loadPrefsInt("/new_prefs", live, lat, lon));
    assert(live.lost_reply == (value <= 2 ? value : 0));
    assert(live.ble_pin == original.ble_pin && live.freq == original.freq);
    assert(live.gps_sync_interval_hours == 336 && live.usb_debug_enabled == 1);
    assert(lat == 47.125 && lon == -122.25);
    assert(reboot.fs.files.at("/new_prefs") == disk);
    ++comparisons;
  }
  {
    DataStore torn; torn.fs.files["/new_prefs"] = before;
    torn.fs.fail_read_after = static_cast<int>(before.size() - 1);
    CompanionNodePrefs live; live.lost_reply = 1; live.freq = 920.5f;
    strcpy(live.node_name, "live unchanged");
    double lat = 1, lon = 2;
    assert(!torn.loadPrefsInt("/new_prefs", live, lat, lon));
    assert(live.lost_reply == 1 && live.freq == 920.5f);
    assert(!strcmp(live.node_name, "live unchanged") && lat == 1 && lon == 2);
    assert(torn.fs.files.at("/new_prefs") == before);
    ++comparisons;
  }
  CompanionNodePrefs changed;
  assert(changed.copyPersistedValuesFrom(original));
  strcpy(changed.node_name, "replacement image");
  changed.freq = 910.5f; changed.usb_debug_enabled = 0;
  changed.gps_sync_interval_hours = 24;
  changed.lost_reply = 1;
  const auto after = golden(changed, 42.25, -121.125);

  // Every possible short write and readback, including every tail byte,
  // must preserve the old durable image. STM32 issues only one image write;
  // the other supported platforms preserve their scalar-write path.
  for (size_t quota = 0; quota < before.size(); ++quota) {
    DataStore write;
    write.fs.files["/new_prefs"] = before;
    write.fs.fail_write_after = static_cast<int>(quota);
    assert(!write.savePrefs(changed, 42.25, -121.125));
    unchanged(write, before);
    write.fs.fail_write_after = -1;
    assert(write.savePrefs(changed, 42.25, -121.125));
    assert(write.fs.files.at("/new_prefs") == after);
    DataStore read;
    read.fs.files["/new_prefs"] = before;
    read.fs.fail_read_after = static_cast<int>(quota);
    assert(!read.savePrefs(changed, 42.25, -121.125));
    unchanged(read, before);
    ++comparisons;
  }
  for (unsigned fault = 0; fault < 3; ++fault) {
    DataStore store;
    store.fs.files["/new_prefs"] = before;
    if (fault == 0) store.fs.fail_write_open = true;
    if (fault == 1) store.fs.fail_write = true;
    if (fault == 2) store.fs.fail_read_open = true;
    assert(!store.savePrefs(changed, 42.25, -121.125));
    unchanged(store, before);
    ++comparisons;
  }
#if defined(STM32_PLATFORM) || defined(NRF52_PLATFORM)
  const unsigned rename_steps = 1;
#else
  const unsigned rename_steps = 2;
#endif
  for (unsigned failure = 1; failure <= rename_steps; ++failure) {
    DataStore store;
    store.fs.files["/new_prefs"] = before;
    store.fs.fail_rename = static_cast<int>(failure);
    assert(!store.savePrefs(changed, 42.25, -121.125));
    unchanged(store, before);
    ++comparisons;
  }
  assert(initial.savePrefs(changed, 42.25, -121.125));
  CompanionNodePrefs loaded;
  double lat = 0, lon = 0;
  assert(initial.loadPrefs(loaded, lat, lon));
  assert(!strcmp(loaded.node_name, changed.node_name));
  assert(loaded.freq == changed.freq && loaded.ble_pin == changed.ble_pin);
  assert(loaded.gps_sync_interval_hours == 24 && loaded.usb_debug_enabled == 0);
  assert(loaded.lost_reply == 1);
  assert(lat == 42.25 && lon == -121.125);

#if defined(STM32_PLATFORM)
  // A future field or wrong staging capacity must fail closed, not overrun
  // the stack or silently publish a truncated/extra-byte format.
  for (bool undersized : {false, true}) {
    DataStore store;
    store.fs.files["/new_prefs"] = before;
    assert(!(undersized ? store.savePrefsTooSmall(changed, lat, lon)
                        : store.savePrefsTooLarge(changed, lat, lon)));
    unchanged(store, before);
    ++comparisons;
  }
#endif
  printf("PASS: %u independent wire/fault comparisons\n", comparisons);
}
'''


def field_code(rows, seed=False):
    result = []
    for name, size in rows:
        if seed:
            if name is not None and not name.startswith('@'):
                result.append(f'fill(&prefs.{name}, sizeof(prefs.{name}));')
        elif name is None:
            result.append(f'bytes.insert(bytes.end(), {size}, uint8_t(0));')
        else:
            value = name[1:] if name.startswith('@') else 'prefs.' + name
            result.append(f'static_assert(sizeof({value}) == {size}, "published field size");')
            result.append(f'append(&{value}, {size});')
    return '\n  '.join(result)


class CompanionPrefsImageTests(unittest.TestCase):
    def test_explicit_wire_layout_and_atomic_staging(self):
        source = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        save = method(source, 'bool DataStore::savePrefs(')
        methods = esp_recovery_helpers(source) + '\n'.join(
            method(source, signature) for signature in (
                'bool DataStore::loadPrefs(', 'bool DataStore::loadPrefsInt(')
        ) + '\n' + save
        for suffix, capacity in (('TooSmall', 235), ('TooLarge', 237)):
            variant = save.replace('DataStore::savePrefs(', 'DataStore::savePrefs' + suffix + '(', 1)
            self.assertIn('uint8_t image[236];', variant)
            variant = variant.replace('uint8_t image[236];', f'uint8_t image[{capacity}];', 1)
            methods += '\n#if defined(STM32_PLATFORM)\n' + variant + '\n#endif\n'
        code = HARNESS.split('int main(')[0].replace('@METHODS@', methods)
        code = code.replace(' bool savePrefs(const CompanionNodePrefs&, double, double);',
                            ' bool savePrefs(const CompanionNodePrefs&, double, double);\n'
                            ' bool savePrefsTooSmall(const CompanionNodePrefs&, double, double);\n'
                            ' bool savePrefsTooLarge(const CompanionNodePrefs&, double, double);')
        self.assertEqual(sum(size for _, size in WIRE_FIELDS), 226)
        self.assertEqual(sum(size for _, size in TAIL_FIELDS), 10)
        generated = {}
        for seed in (False, True):
            rows = [field_code(WIRE_FIELDS, seed)]
            for condition, fields in OPTIONAL_FIELDS:
                rows.append('#if ' + condition + '\n' + field_code(fields, seed) + '\n#endif')
            rows.append(field_code(TAIL_FIELDS, seed))
            generated['@SEED@' if seed else '@GOLDEN@'] = '\n  '.join(rows)
        tests = TESTS
        for placeholder, value in generated.items():
            tests = tests.replace(placeholder, value)
        with tempfile.TemporaryDirectory(prefix='meshcore-prefs-image-') as directory:
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
            # Reuse the existing memory-filesystem boundary, adding only an
            # observable open failure needed to exercise the actual writer.
            fs = (ROOT / 'test/fixtures/radio_profiles/mocks/helpers/IdentityStore.h').read_text()
            fs = fs.replace('  bool fail_write = false;',
                            '  bool fail_write = false, fail_write_open = false;')
            fs = fs.replace('    if (*mode == \'w\') files[path].clear();',
                            '    if (*mode == \'w\' && fail_write_open) return {};\n'
                            '    if (*mode == \'w\') files[path].clear();')
            (work / 'helpers').mkdir()
            (work / 'helpers/IdentityStore.h').write_text(fs)
            cpp = work / 'test.cpp'
            cpp.write_text(code + tests)
            for platform in ('STM32_PLATFORM', 'NRF52_PLATFORM', 'ESP32_PLATFORM',
                             'RP2040_PLATFORM', 'ESP32_PLATFORM,TBEAM_1W',
                             'RP2040_PLATFORM,ENABLE_WIFI_INTERFACE'):
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
                    ran = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
