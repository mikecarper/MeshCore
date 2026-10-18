#!/usr/bin/env python3
"""Keep whole legacy contact records ahead of an interrupted in-place save."""
from pathlib import Path
import unittest

import test_contact_cache as cached
import test_companion_uncached_storage as uncached
from test_t096_full_memory import method

ROOT = Path(__file__).resolve().parents[1]
MARKER = "PASS: partial contact tail"

# Older firmware rewrote /contacts3 in place. A reset or full volume during
# that save leaves whole records followed by part of the next one.
CHECKS = r'''
static constexpr size_t RS = mesh::storage::CONTACT_RECORD_SIZE;
#if MESH_CONTACT_CACHE
static FakeFilesystem& tailFs() { return SPIFFS; }
struct TailBoot { Host host; DataStore store{host}; };
static bool tailSave(TailBoot& b) { return b.store.saveContacts(&b.host, cachedContactFilter); }
static void failOpens(const char* path, unsigned count) {
  SPIFFS.read_open_failures.clear();
  SPIFFS.read_open_failures[path] = count;
}
static void failMetadata(bool fail) { SPIFFS.metadata_error = fail; }
static std::vector<uint8_t> tailImage() {
  Fixture f(4);
  return SPIFFS.files.at("/contacts3");
}
#else
static FakeFilesystem& tailFs() { return filesystem; }
struct TailBoot { Host host; DataStore store; };
static bool tailSave(TailBoot& b) { return b.store.saveContacts(&b.host); }
static void failOpens(const char* path, unsigned count) {
  filesystem.fail_open = path;
  filesystem.fail_open_remaining = count;
}
[[maybe_unused]] static void failMetadata(bool fail) { filesystem.stat_error = fail ? EIO : 0; }
static std::vector<uint8_t> tailImage() {
  filesystem = FakeFilesystem();
  DataStore store;
  Host host;
  host.contacts = {contact(1), contact(2), contact(3), contact(4)};
  assert(store.saveContacts(&host));
  return filesystem.files.at("/contacts3");
}
#endif

static std::vector<uint8_t> records(const std::vector<uint8_t>& image, size_t count) {
  return std::vector<uint8_t>(image.begin(), image.begin() + count * RS);
}

static std::vector<uint8_t> partial(const std::vector<uint8_t>& image, size_t tail) {
  auto file = records(image, 3);
  file.insert(file.end(), image.begin() + 3 * RS, image.begin() + 3 * RS + tail);
  return file;
}

static void boot(TailBoot& b) {  // MyMesh::begin() order.
  b.store.loadContacts(&b.host);
  b.store.recoverContacts(&b.host);
}

static void checkLoaded(Host& host, const std::vector<uint8_t>& image, size_t count) {
  assert(host.contacts.size() == count);
  for (size_t i = 0; i < count; ++i) {
    const uint8_t* record = image.data() + i * RS;
    uint8_t path[64];
    assert(!memcmp(host.contacts[i].id.pub_key, record, 32));
    assert(!memcmp(host.contacts[i].name, record + 32, 32));
    assert(host.contacts[i].copyPathTo(path) && !memcmp(path, record + 76, 64));
  }
}

static void partial_tail_keeps_whole_records() {
  const auto image = tailImage();
  assert(image.size() == 4 * RS);
  for (size_t tail : {size_t(1), RS / 2, RS - 1}) {
    tailFs() = FakeFilesystem();
    tailFs().files["/contacts3"] = partial(image, tail);
    const auto durable = tailFs().files;
    {
      TailBoot b;
      boot(b);
      assert(!b.store.hasIncompleteContactLoad());
      assert(!b.store.contactRecoveryPending() && !b.store.contactsAreVolatile());
      checkLoaded(b.host, image, 3);
      assert(tailFs().files == durable);  // Loading neither rewrites nor discards.
      assert(tailSave(b));
      assert(tailFs().files.at("/contacts3") == records(image, 3));
    }
    TailBoot rebooted;
    boot(rebooted);
    assert(!rebooted.store.hasIncompleteContactLoad());
    checkLoaded(rebooted.host, image, 3);
  }
}

static void without_a_whole_record_recovery_is_unchanged() {
  const auto image = tailImage();
  tailFs() = FakeFilesystem();
  tailFs().files["/contacts3"] = records(image, 1);
  tailFs().files["/contacts3"].pop_back();
  TailBoot b;
  boot(b);
  assert(!b.store.hasIncompleteContactLoad() && !b.store.contactRecoveryPending());
  assert(b.host.contacts.empty() && !tailFs().files.count("/contacts3"));
}

static void whole_record_read_errors_keep_bounded_recovery() {
  const auto image = tailImage();
  tailFs() = FakeFilesystem();
  tailFs().files["/contacts3"] = records(image, 3);
  failOpens("/contacts3", 2);  // Both boot-time reads.
  TailBoot b;
  b.store.loadContacts(&b.host);
  assert(b.store.contactRecoveryPending() && b.host.contacts.empty());
  failOpens("", 0);
  b.store.recoverContacts(&b.host);
  assert(!b.store.hasIncompleteContactLoad() && !b.store.contactRecoveryPending());
  checkLoaded(b.host, image, 3);
}

#if !defined(STM32_PLATFORM)
static void a_complete_backup_still_wins() {
  const auto image = tailImage();
  for (unsigned fault = 0; fault < 3; ++fault) {
#if defined(RP2040_PLATFORM)
    if (fault == 2) continue;  // Arduino-Pico exists() hides metadata errors.
#endif
    tailFs() = FakeFilesystem();
    tailFs().files["/contacts3"] = partial(image, 1);
    tailFs().files["/contacts3.bak"] = records(image, 2);
    // A backup unreadable or of unknown presence at boot keeps its retry at
    // the recovery boundary; the incomplete primary must not retire it first.
    if (fault == 1) failOpens("/contacts3.bak", 2);
    if (fault == 2) failMetadata(true);
    TailBoot b;
    b.store.loadContacts(&b.host);
    failOpens("", 0);
    failMetadata(false);
    b.store.recoverContacts(&b.host);
    assert(!b.store.hasIncompleteContactLoad() && !b.store.contactRecoveryPending());
    checkLoaded(b.host, image, 2);
    assert(tailFs().files.at("/contacts3") == records(image, 2));
    assert(!tailFs().files.count("/contacts3.bak"));
  }
}

#if defined(ESP32_PLATFORM)
static void unknown_backup_presence_preserves_sources() {
  const auto image = tailImage();
  tailFs() = FakeFilesystem();
  tailFs().files["/contacts3"] = partial(image, 1);
  tailFs().files["/contacts3.bak"] = records(image, 2);
  tailFs().files["/channels2"] = {3, 4, 5};
  tailFs().files["/identity/_main.id"] = {7, 8, 9};
  const auto durable = tailFs().files;
  {
    failMetadata(true);
    TailBoot b;
    b.store.loadContacts(&b.host);
    assert(b.store.contactRecoveryPending() && b.host.contacts.empty());
    assert(!tailSave(b) && tailFs().files == durable);
    b.store.recoverContacts(&b.host);  // Presence remains unknown on the retry.
    assert(!b.store.contactRecoveryPending() && b.store.contactsAreVolatile());
    assert(b.host.contacts.empty() && tailFs().files == durable);
    failMetadata(false);
    assert(!tailSave(b) && tailFs().files == durable);  // Volatile for this boot.
  }
  TailBoot rebooted;
  boot(rebooted);
  assert(!rebooted.store.hasIncompleteContactLoad() && !rebooted.store.contactsAreVolatile());
  checkLoaded(rebooted.host, image, 2);  // The complete backup still wins.
  assert(tailFs().files.at("/contacts3") == records(image, 2));
  assert(!tailFs().files.count("/contacts3.bak"));
  assert(tailFs().files.at("/channels2") == durable.at("/channels2"));
  assert(tailFs().files.at("/identity/_main.id") == durable.at("/identity/_main.id"));
}
#endif
#endif

static void salvaged_primary_open_failure_is_not_absence() {
  const auto image = tailImage();
  tailFs() = FakeFilesystem();
  tailFs().files["/contacts3"] = partial(image, 1);
  {
    TailBoot dry;  // The last read open of a healthy load opens the image to load.
    dry.store.loadContacts(&dry.host);
    assert(dry.host.contacts.size() == 3);
  }
  const unsigned load_open = tailFs().read_opens;
  for (bool add_backup : {false, true}) {
#if defined(STM32_PLATFORM)
    if (add_backup) continue;  // STM32 does not select a contact backup.
#endif
    tailFs() = FakeFilesystem();
    tailFs().files["/contacts3"] = partial(image, 1);
    tailFs().fail_read_open = load_open;
    TailBoot b;
    b.store.loadContacts(&b.host);
    assert(tailFs().read_opens == load_open);
    assert(b.store.hasIncompleteContactLoad() && b.store.contactRecoveryPending());
    assert(b.host.contacts.empty());
    const auto unchanged = tailFs().files;
    const auto writes = tailFs().writes;
    assert(!tailSave(b) && tailFs().files == unchanged && tailFs().writes == writes);
    // A backup available at the recovery boundary takes precedence over the
    // salvaged prefix, even though the first selection had admitted it.
    if (add_backup) tailFs().files["/contacts3.bak"] = records(image, 2);
    b.store.recoverContacts(&b.host);
    assert(!b.store.hasIncompleteContactLoad() && !b.store.contactRecoveryPending());
    checkLoaded(b.host, image, add_backup ? 2 : 3);
    if (add_backup) {
      assert(tailFs().files.at("/contacts3") == records(image, 2));
      assert(!tailFs().files.count("/contacts3.bak"));
    } else assert(tailFs().files == unchanged);
  }
}

int main() {
  partial_tail_keeps_whole_records();
  without_a_whole_record_recovery_is_unchanged();
  whole_record_read_errors_keep_bounded_recovery();
#if !defined(STM32_PLATFORM)
  a_complete_backup_still_wins();
#if defined(ESP32_PLATFORM)
  unknown_backup_presence_preserves_sources();
#endif
#endif
  salvaged_primary_open_failure_is_not_absence();
  printf("''' + MARKER + r'''\n");
  return 0;
}
'''


def uncached_fixture(work):
    source = (ROOT / "test/fixtures/companion_uncached_storage/test.cpp").read_text()
    for signature in ("static std::vector<uint8_t> make_original()",
                      "static void identity_checks()", "static void contact_checks()",
                      "static void recovery_checks()"):
        source = source.replace(method(source, signature), "")
    # Fail one chosen read open, so a later open can fail after earlier ones succeed.
    for old, new in (("  unsigned read_opens = 0;\n",
                      "  unsigned read_opens = 0, fail_read_open = 0;\n"),
                     ("    if (*mode == 'r') ++read_opens;\n",
                      "    if (*mode == 'r' && ++read_opens == fail_read_open) return File();\n")):
        assert source.count(old) == 1, old
        source = source.replace(old, new)
    generated = work / "partial_tail.cpp"
    generated.write_text("#include <cstdio>\n" + source[:source.index("int main() {")] + CHECKS)
    return generated


def cached_fixture(work):
    fixtures = ROOT / "test/fixtures/contact_cache"
    filesystem = (fixtures / "FakeFilesystem.h").read_text()
    # Match the uncached fixture's one-shot final-load-open failure without
    # changing the shared filesystem adapter used by unrelated cache tests.
    for old, new in (("  unsigned false_success_rename = 0, late_rename_error = 0;\n",
                      "  unsigned false_success_rename = 0, late_rename_error = 0;\n"
                      "  unsigned read_opens = 0, fail_read_open = 0;\n"),
                     ("    auto failure = read_open_failures.find(path);\n",
                      "    if (++read_opens == fail_read_open) return File();\n"
                      "    auto failure = read_open_failures.find(path);\n")):
        assert filesystem.count(old) == 1, old
        filesystem = filesystem.replace(old, new)
    (work / "FakeFilesystem.h").write_text(filesystem)
    source = (fixtures / "test_contact_cache.cpp").read_text()
    generated = work / "partial_tail.cpp"
    generated.write_text("#include <cstdio>\n" + source[:source.index("int main() {")] + CHECKS)
    return generated


class CompanionContactPartialTailTest(unittest.TestCase):
    def test_esp32_uncached_partial_tail(self):
        uncached.CompanionUncachedStorageTest().run_platform(
            "ESP32_PLATFORM", fixture_transform=uncached_fixture)

    def test_rp2040_partial_tail(self):
        uncached.CompanionUncachedStorageTest().run_platform(
            "RP2040_PLATFORM", fixture_transform=uncached_fixture)

    def test_stm32_partial_tail(self):
        uncached.CompanionUncachedStorageTest().run_platform(
            "STM32_PLATFORM", fixture_transform=uncached_fixture)

    def test_esp32_cached_partial_tail(self):
        cached.ContactCacheTest().run_cache_test(
            "ESP32_PLATFORM", fixture_transform=cached_fixture, expected_marker=MARKER)


if __name__ == "__main__":
    unittest.main()
