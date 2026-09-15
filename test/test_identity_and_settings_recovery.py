"""Production identity transactions and bounded Companion settings recovery."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_replay_reset_integration import extract_braced

ROOT = Path(__file__).resolve().parents[1]


def fixture_prefix():
    text = (ROOT / 'test/fixtures/companion_uncached_storage/test.cpp').read_text()
    return text[:text.index('struct Host {')]


def compile_run(program, platform):
    with tempfile.TemporaryDirectory(prefix='mesh-storage-recovery-') as directory:
        directory = Path(directory)
        (directory / 'FS.h').write_text('#pragma once\nnamespace fs { using FS = FakeFilesystem; }\n')
        (directory / 'Adafruit_LittleFS.h').write_text(
            '#pragma once\nusing Adafruit_LittleFS = FakeFilesystem;\n'
            'namespace Adafruit_LittleFS_Namespace {}\n')
        cpp, exe = directory / 'test.cpp', directory / 'test'
        cpp.write_text(program)
        compiled = subprocess.run([
            'c++', '-std=c++17', '-O1', '-g', '-fsanitize=address,undefined',
            '-fno-pie', '-no-pie', '-DMESH_CONTACT_CACHE=0', '-D' + platform + '=1',
            '-I', str(directory), '-I', str(ROOT / 'test/fixtures/contact_cache/mocks'),
            '-I', str(ROOT / 'src'), '-I', str(ROOT / 'lib/ed25519'),
            '-I', str(ROOT / 'examples/companion_radio'), str(cpp), '-o', str(exe),
        ], capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise AssertionError(compiled.stdout + compiled.stderr)
        run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
        if run.returncode:
            raise AssertionError(run.stdout + run.stderr)


class IdentityAndSettingsRecovery(unittest.TestCase):
    def test_identity_atomic_esp32(self):
        self.check_identity('ESP32_PLATFORM')

    def test_identity_atomic_rp2040(self):
        self.check_identity('RP2040_PLATFORM')

    def test_identity_atomic_stm32(self):
        self.check_identity('STM32_PLATFORM')

    def check_identity(self, platform):
        source = (ROOT / 'src/helpers/IdentityStore.cpp').read_text()
        presence = (ROOT / 'src/helpers/FilePresence.h').read_text()
        program = fixture_prefix() + '\n#include <helpers/IdentityStore.h>\n'
        program += 'namespace mesh { template <typename Filesystem>\n' + extract_braced(
            presence, 'bool filePresence(') + '\n}\n'
        for signature in ('bool IdentityStore::recover(',
                          'bool IdentityStore::save(const char *name, const mesh::LocalIdentity& id)',
                          'bool IdentityStore::save(const char *name, const mesh::LocalIdentity& id, const char display_name[])'):
            program += extract_braced(source, signature) + '\n'
        program += r'''
int main() {
  for (bool display : {false, true}) {
    for (unsigned fault = 0; fault < 4; ++fault) {
      filesystem = FakeFilesystem();
      IdentityStore store(filesystem, "");
      mesh::LocalIdentity old_id, new_id;
      memset(old_id.pub_key, 7, PUB_KEY_SIZE); memset(new_id.pub_key, 9, PUB_KEY_SIZE);
      assert(display ? store.save("main", old_id, "old") : store.save("main", old_id));
      const auto old = filesystem.files["/main.id"];
      if (fault == 0) filesystem.max_write = 3;
      if (fault == 1) filesystem.max_read = 3;
      if (fault == 2) filesystem.fail_open = "/main.id.tmp";
      if (fault == 3) filesystem.fail_rename = filesystem.renames +
#if defined(STM32_PLATFORM)
          1;
#else
          2;
#endif
      assert(!(display ? store.save("main", new_id, "new") : store.save("main", new_id)));
      assert(filesystem.files["/main.id"] == old);
      filesystem.max_write = filesystem.max_read = std::numeric_limits<size_t>::max();
      filesystem.fail_open.clear(); filesystem.fail_rename = 0;
      assert(display ? store.save("main", new_id, "new") : store.save("main", new_id));
      assert(filesystem.files["/main.id"] != old);
    }
  }
#if !defined(STM32_PLATFORM)
  filesystem = FakeFilesystem();
  filesystem.files["/main.id.bak"] = std::vector<uint8_t>(96, 7);
  IdentityStore recovering(filesystem, "");
  filesystem.fail_rename = 1;
  assert(!recovering.recover("main") && filesystem.files.count("/main.id.bak"));
  filesystem.fail_rename = 0;
  assert(recovering.recover("main") && filesystem.files["/main.id"].size() == 96);
#endif
}
'''
        compile_run(program, platform)

    def test_esp32_preferences_and_channels_recover_or_reset(self):
        store = (ROOT / 'examples/companion_radio/DataStore.cpp').read_text()
        prefs = (ROOT / 'examples/companion_radio/NodePrefs.h').read_text()
        fields = prefs[prefs.index('class CompanionNodePrefs {'):prefs.index('\nprivate:')]
        program = fixture_prefix() + r'''
#include <helpers/BluetoothMac.h>
#include "BluetoothName.h"
''' + fields + '\n};\n' + r'''
struct ChannelDetails { struct { uint8_t secret[32] = {}; uint8_t tx_radio = 0; } channel; char name[32] = {}; };
#define MAX_GROUP_CHANNELS 40
struct DataStoreHost {
  std::vector<ChannelDetails> channels;
  bool onChannelLoaded(uint8_t index, const ChannelDetails& channel) {
    if (index >= channels.size()) channels.resize(index + 1);
    channels[index] = channel; return true;
  }
  bool getChannelForSave(uint8_t index, ChannelDetails& channel) {
    if (index >= channels.size()) return false;
    channel = channels[index]; return true;
  }
};
struct DataStore {
  FakeFilesystem* _fs = &filesystem;
  bool _prefs_load_incomplete = false, _channel_load_incomplete = false;
  const char* _prefs_recovery_source = nullptr;
  const char* _channel_recovery_source = nullptr;
  FakeFilesystem* _getContactsChannelsFS() { return _fs; }
  File openRead(FakeFilesystem* fs, const char* path) { return fs->open(path); }
  bool loadPrefs(CompanionNodePrefs&, double&, double&);
  bool loadPrefsInt(const char*, CompanionNodePrefs&, double&, double&);
  bool savePrefs(const CompanionNodePrefs&, double, double);
  void loadChannels(DataStoreHost*);
  bool saveChannels(DataStoreHost*);
};
'''
        for signature in ('static bool companionPathPresence(',
                          'static bool promoteCompanionRecoveryFile(',
                          'bool DataStore::loadPrefs(', 'bool DataStore::loadPrefsInt(',
                          'bool DataStore::savePrefs(', 'void DataStore::loadChannels(',
                          'bool DataStore::saveChannels('):
            program += extract_braced(store, signature) + '\n'
        program += r'''
int main() {
  CompanionNodePrefs saved; strcpy(saved.node_name, "saved"); saved.ble_pin = 654321;
  DataStore writer; assert(writer.savePrefs(saved, 11, 22));
  const auto prefs_image = filesystem.files["/new_prefs"];
  DataStoreHost original; ChannelDetails channel; strcpy(channel.name, "private");
  channel.channel.secret[0] = 7; original.channels.push_back(channel);
  assert(writer.saveChannels(&original)); const auto channel_image = filesystem.files["/channels2"];
  for (bool channels : {false, true}) {
    const char* path = channels ? "/channels2" : "/new_prefs";
    const std::string backup = std::string(path) + ".bak";
    const auto& image = channels ? channel_image : prefs_image;
    for (unsigned fault = 0; fault < 8; ++fault) {
      filesystem = FakeFilesystem(); filesystem.files[path] = image;
      if (fault == 0) { filesystem.fail_open = path; filesystem.fail_open_remaining = 1; }
      if (fault == 1 || fault == 2 || fault == 3) {
        filesystem.files[path] = {1}; filesystem.files[backup] = image;
        if (fault == 2) filesystem.fail_rename = 1;
        if (fault == 3 && !channels) { filesystem.files.erase(backup); filesystem.files["/node_prefs"] = image; }
      }
      if (fault == 4) filesystem.files[path] = {1};
      if (fault == 5) filesystem.max_read = 1;
      if (fault == 6) filesystem.stat_error = EIO;
      if (fault == 7) filesystem.fail_open = path;
      DataStore reader; CompanionNodePrefs restored; strcpy(restored.node_name, "default");
      double lat = 1, lon = 2; DataStoreHost loaded;
      if (channels) reader.loadChannels(&loaded);
      else assert(reader.loadPrefs(restored, lat, lon));
      const bool recovered = fault < 4;
      if (channels) {
        assert(loaded.channels.size() == (recovered ? 1 : 0));
        if (recovered) assert(!strcmp(loaded.channels[0].name, "private") && loaded.channels[0].channel.secret[0] == 7);
      } else {
        assert(!strcmp(restored.node_name, recovered ? "saved" : "default"));
        assert(restored.ble_pin == (recovered ? 654321u : 0u));
      }
      // Neither failed reads nor failed recovery publication erase the only
      // verified backup. Once I/O recovers, a normal save is allowed.
      if (fault == 2) assert(filesystem.files[backup] == image);
      if (fault >= 4) assert(filesystem.files[path] == (fault == 4 ? std::vector<uint8_t>{1} : image));
      if (fault != 7) filesystem.fail_open.clear();
      filesystem.fail_rename = 0;
      filesystem.stat_error = 0; filesystem.max_read = std::numeric_limits<size_t>::max();
      if (channels) {
        if (!recovered) loaded.channels.push_back(channel);
        assert(reader.saveChannels(&loaded));
        filesystem.fail_open.clear();
        DataStore again; DataStoreHost check; again.loadChannels(&check); assert(check.channels.size() == 1);
      } else {
        assert(reader.savePrefs(restored, lat, lon));
        filesystem.fail_open.clear();
        DataStore again; CompanionNodePrefs check; assert(again.loadPrefs(check, lat, lon));
        assert(!strcmp(check.node_name, restored.node_name));
      }
    }
  }
}
'''
        compile_run(program, 'ESP32_PLATFORM')


if __name__ == '__main__':
    unittest.main()
