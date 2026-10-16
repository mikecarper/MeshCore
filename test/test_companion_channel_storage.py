#!/usr/bin/env python3
"""Run real contact and channel persistence together under sanitizers."""
from pathlib import Path
import unittest

import test_contact_cache as cached
import test_companion_uncached_storage as uncached
from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]

CHANNEL_HOST = r'''
  bool channel_accepts = true;
  std::vector<ChannelDetails> channels;
  bool onChannelLoaded(uint8_t index, const ChannelDetails& channel) {
    if (!channel_accepts) return false;
    if (channels.size() <= index) channels.resize(index + 1);
    channels[index] = channel;
    return true;
  }
  bool getChannelForSave(uint8_t index, ChannelDetails& channel) {
    if (index >= channels.size()) return false;
    channel = channels[index];
    return true;
  }
'''


def fixture_builder(platform, old_coupling=False):
    """Extend existing faulting filesystem adapters, not persistence logic."""
    is_cached = platform in ("NRF52_PLATFORM", "ESP32_PLATFORM")

    def build(work):
        store = (ROOT / "examples/companion_radio/DataStore.cpp").read_text()
        header = (ROOT / "examples/companion_radio/DataStore.h").read_text()
        mesh = (ROOT / "examples/companion_radio/MyMesh.cpp").read_text()
        implementation = (work / "store_under_test.h").read_text()
        load = method(store, "void DataStore::loadChannels(")
        if old_coupling:
            # Restore the exact pre-fix channel-to-contact flag coupling.
            contact_health = method(store, "bool DataStore::hasIncompleteContactLoad() const")
            self_guard = "  if (contactRecoveryPending()) return true;"
            assert self_guard in contact_health
            implementation = implementation.replace(contact_health, contact_health.replace(
                self_guard, self_guard + "\n  if (_channel_load_incomplete) return true;"))
            if platform == "NRF52_PLATFORM":
                assert "  bool& incomplete = _channel_load_incomplete;" in load
                load = load.replace("  bool& incomplete = _channel_load_incomplete;",
                                    "  bool& incomplete = _contact_load_incomplete;")
        if platform == "ESP32_PLATFORM":
            implementation += "\n" + method(store, "static bool promoteCompanionRecoveryFile(")
        implementation += r'''
static bool fail_channel_allocation = false;
static void* allocateChannelSnapshot(size_t size) {
  return fail_channel_allocation ? nullptr : malloc(size);
}
#define malloc allocateChannelSnapshot
'''
        implementation += load + "\n#undef malloc\n"
        implementation += method(store, "bool DataStore::saveChannels(") + "\n"
        if platform != "ESP32_PLATFORM":
            implementation += method(store, "bool DataStore::formatFileSystem()") + "\n"
        (work / "store_under_test.h").write_text(implementation)
        (work / "channel_admission_under_test.h").write_text(
            method(mesh, "bool MyMesh::canMutateContacts() const"))

        fixture = (ROOT / ("test/fixtures/contact_cache/test_contact_cache.cpp" if is_cached
                           else "test/fixtures/companion_uncached_storage/test.cpp"))
        source = fixture.read_text()
        group = method((ROOT / "src/Mesh.h").read_text(), "class GroupChannel {")
        source = ("#include <cstdio>\n#include <Mesh.h>\nnamespace mesh {\n" + group
                  + ";\n}\n#include <helpers/ChannelDetails.h>\n#define MAX_GROUP_CHANNELS 8\n"
                  + "#define ADV_TYPE_NONE 0\n") + source
        source = source.replace("struct Host {", "struct Host {" + CHANNEL_HOST, 1)
        declarations = """
  void loadChannels(DataStoreHost*);
  bool saveChannels(DataStoreHost*);
  bool formatFileSystem();
  void requireContactRecovery() { queueContactRecovery(); }
""" + method(header, "  bool hasIncompleteChannelLoad() const") + "\n"
        if platform == "NRF52_PLATFORM":
            declarations += "  void forceSharedAuthorityFailure() { _contact_load_incomplete = true; }\n"
        source = source.replace("  bool hasIncompleteContactLoad() const;",
                                declarations + "  bool hasIncompleteContactLoad() const;", 1)
        if is_cached:
            source = source.replace("  bool _channel_load_incomplete = false;\n", "")
            source = source.replace("  bool _cache_load_incomplete = false;",
                "  bool _cache_load_incomplete = false;\n  bool _channel_load_incomplete = false;", 1)
            if platform == "ESP32_PLATFORM":
                source = source.replace("  uint32_t _secret_retry_at = 0;",
                    "  const char* _channel_recovery_source = nullptr;\n  uint32_t _secret_retry_at = 0;", 1)
            else:
                source = source.replace("  bool _contact_load_incomplete = false;",
                    "  bool _contact_load_incomplete = false;\n"
                    "  bool _identity_creation_blocked = false, _prefs_load_incomplete = false;\n"
                    "  FakeFilesystem* _fs = &SPIFFS;\n  FakeFilesystem* _fsExtra = nullptr;", 1)
                source = source.replace(
                    "bool contactPathPresence(FakeFilesystem* fs, const char* path, bool& present) {",
                    "static bool fail_channel_metadata = false;\n"
                    "bool contactPathPresence(FakeFilesystem* fs, const char* path, bool& present, uint32_t* size = nullptr) {\n"
                    "  if (fail_channel_metadata && !strcmp(path, \"/channels2\")) return false;")
                source = source.replace("  present = fs->exists(path);\n  return true;",
                    "  present = fs->exists(path);\n"
                    "  if (size) *size = present ? fs->files.at(path).size() : 0;\n  return true;", 1)
            fake = (ROOT / "test/fixtures/contact_cache/FakeFilesystem.h").read_text()
            fake = fake.replace("  size_t largest_read =",
                "  size_t max_channel_read = std::numeric_limits<size_t>::max();\n  size_t largest_read =", 1)
            fake = fake.replace(
                "  const size_t count = std::min(length, data.size() - std::min(data.size(), _position));",
                "  const size_t count = std::min({length, _fs->max_channel_read, data.size() - std::min(data.size(), _position)});")
            fake = fake.replace("  bool mkdir(const char*) { return true; }",
                "  bool fail_channel_format = false;\n"
                "  bool format() { if (fail_channel_format) return false; files.clear(); return true; }\n"
                "  bool mkdir(const char*) { return true; }", 1)
            (work / "FakeFilesystem.h").write_text(fake)
        else:
            source = source.replace("  FakeFilesystem* _fs = &filesystem;",
                "  FakeFilesystem* _fs = &filesystem;\n  FakeFilesystem* _fsExtra = nullptr;\n"
                "  bool _prefs_load_incomplete = false;", 1)
            source = source.replace("  bool mkdir(const char*) { return true; }",
                "  bool fail_channel_format = false, late_channel_rename_error = false;\n"
                "  bool format() { if (fail_channel_format) return false; files.clear(); return true; }\n"
                "  bool mkdir(const char*) { return true; }", 1)
            source = source.replace("    return true;\n  }\n} filesystem;",
                "    return !late_channel_rename_error;\n  }\n} filesystem;", 1)
            source = source.replace('#include "store_under_test.h"',
                                    '#define LittleFS filesystem\n#include "store_under_test.h"', 1)
            for signature in ("static void identity_checks()", "static void contact_checks()",
                              "static void recovery_checks()"):
                source = source.replace(method(source, signature), "")
        source = source[:source.index("int main() {")]
        source += (ROOT / "test/fixtures/companion_channel_storage/checks.h").read_text()
        generated = work / "channel_storage.cpp"
        generated.write_text(source)
        return generated

    return build


class CompanionChannelStorageTest(unittest.TestCase):
    def run_platform(self, platform, old_coupling=False):
        transform = fixture_builder(platform, old_coupling)
        failure = "!f.store.hasIncompleteContactLoad()" if old_coupling else None
        if platform in ("NRF52_PLATFORM", "ESP32_PLATFORM"):
            cached.ContactCacheTest().run_cache_test(
                platform, fixture_transform=transform, expected_failure=failure,
                expected_marker="PASS: channel/contact storage isolation")
        else:
            uncached.CompanionUncachedStorageTest().run_platform(
                platform, fixture_transform=transform, expected_failure=failure)

    def test_nrf52_cached_channels_do_not_quarantine_contacts(self):
        self.run_platform("NRF52_PLATFORM")

    def test_esp32_channel_allocation_and_refusal_are_local(self):
        self.run_platform("ESP32_PLATFORM")

    def test_stm32_uncached_channels_do_not_quarantine_contacts(self):
        self.run_platform("STM32_PLATFORM")

    def test_rp2040_channels_recover_without_quarantining_contacts(self):
        self.run_platform("RP2040_PLATFORM")

    def test_old_nrf52_shared_channel_flag_is_detected(self):
        self.run_platform("NRF52_PLATFORM", old_coupling=True)

    def test_old_stm32_channel_contact_veto_is_detected(self):
        self.run_platform("STM32_PLATFORM", old_coupling=True)


if __name__ == "__main__":
    unittest.main()
