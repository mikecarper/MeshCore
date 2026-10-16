// Included after the existing real-contact persistence fixture and methods.
static std::vector<uint8_t> channelImage(const char* name, unsigned count = 1) {
  std::vector<uint8_t> image(count * 68, 0);
  for (unsigned i = 0; i < count; ++i) {
    image[i * 68] = mesh::encodeRadioTxPolicy(mesh::RADIO_TX_SECONDARY);
    strncpy(reinterpret_cast<char*>(image.data() + i * 68 + 4), name, 31);
    memset(image.data() + i * 68 + 36, 0x51, 32);
  }
  return image;
}

#if MESH_CONTACT_CACHE
using ChannelFixture = Fixture;
static FakeFilesystem& channelFs() { return SPIFFS; }
#else
struct ChannelFixture {
  Host host;
  DataStore store;
  explicit ChannelFixture(unsigned) {
    const auto image = make_original();
    filesystem = FakeFilesystem();
    filesystem.files["/contacts3"] = image;
  }
};
static FakeFilesystem& channelFs() { return filesystem; }
#endif

static void setShortChannelRead(bool active) {
#if MESH_CONTACT_CACHE
  SPIFFS.max_channel_read = active ? 2 : std::numeric_limits<size_t>::max();
#else
  filesystem.max_read = active ? 2 : std::numeric_limits<size_t>::max();
#endif
}

static void failChannelOpenOnce() {
#if MESH_CONTACT_CACHE
  SPIFFS.read_open_failures["/channels2"] = 1;
#else
  filesystem.fail_open = "/channels2";
  filesystem.fail_open_remaining = 1;
#endif
}

static void initializeContacts(ChannelFixture& f) {
#if MESH_CONTACT_CACHE
  f.host.contacts.clear();
#endif
  f.store.loadContacts(&f.host);
  f.store.recoverContacts(&f.host);
  assert(!f.host.contacts.empty() && !f.store.hasIncompleteContactLoad());
  uint8_t path[64];
  assert(f.host.contacts[0].copyPathTo(path));
  ChannelDetails defaults{};
  strcpy(defaults.name, "Public");
  f.host.channels.push_back(defaults);
}

class MyMesh {
  DataStore* _store;
public:
  explicit MyMesh(DataStore& store) : _store(&store) {}
  bool canMutateContacts() const;
};
#include "channel_admission_under_test.h"

static void channel_faults_do_not_poison_contacts() {
  // 0 healthy, 1 malformed framing, 2 one open failure, 3 short read,
  // 4 allocation failure, 5 host refusal, 6 whole-record overcapacity,
  // 7 NRF unknown metadata, 8 preexisting shared-store authority failure.
  for (unsigned fault = 0; fault <= 8; ++fault) {
#if !defined(NRF52_PLATFORM)
    if (fault >= 7) continue;
#endif
#if defined(ESP32_PLATFORM)
    if (fault == 2 || fault == 3) continue; // Existing bounded fallback policy.
#endif
    ChannelFixture f(30);
    initializeContacts(f);
    auto& fs = channelFs();
    fs.files["/channels2"] = channelImage("Saved");
    if (fault == 1) fs.files["/channels2"].pop_back();
    if (fault == 2) failChannelOpenOnce();
    if (fault == 3) setShortChannelRead(true);
    if (fault == 4) fail_channel_allocation = true;
    if (fault == 5) f.host.channel_accepts = false;
    if (fault == 6) fs.files["/channels2"] = channelImage("Larger profile", MAX_GROUP_CHANNELS + 1);
#if defined(NRF52_PLATFORM)
    if (fault == 7) fail_channel_metadata = true;
    if (fault == 8) f.store.forceSharedAuthorityFailure();
#endif
    const auto channel_bytes = fs.files.at("/channels2");
    const auto durable = fs.files;
    const unsigned writes = fs.writes, renames = fs.renames;
    f.store.loadChannels(&f.host);
    setShortChannelRead(false);
    fail_channel_allocation = false;
    f.host.channel_accepts = true;
#if defined(NRF52_PLATFORM)
    fail_channel_metadata = false;
#endif
    MyMesh admission(f.store);
    uint8_t path[64];
    if (fault == 8) {
      assert(f.store.hasIncompleteContactLoad() && !admission.canMutateContacts());
      assert(!f.store.saveContacts(&f.host, nullptr));
      assert(!f.store.saveChannels(&f.host));
#if MESH_CONTACT_CACHE
      assert(!f.host.contacts[0].copyPathTo(path));
#endif
      assert(fs.files == durable && fs.writes == writes && fs.renames == renames);
      continue;
    }
    // This assertion is the runtime failure checked by the old-coupling
    // negative controls, not a source-text or fixture-compilation failure.
    assert(!f.store.hasIncompleteContactLoad());
    assert(admission.canMutateContacts() && !f.store.contactRecoveryPending());
    assert(f.host.contacts[0].copyPathTo(path));
    const bool quarantine = fault >= 2;
    assert(f.store.hasIncompleteChannelLoad() == quarantine);
    assert(fs.files == durable && fs.writes == writes && fs.renames == renames);
    if (quarantine) {
      assert(!f.store.saveChannels(&f.host));
      // Contact-only recovery must not authorize a channel rewrite.
      f.store.requireContactRecovery();
      f.store.recoverContacts(&f.host);
      assert(!f.store.hasIncompleteContactLoad() && !f.store.contactRecoveryPending());
      assert(f.store.hasIncompleteChannelLoad() && !f.host.contacts.empty());
      assert(f.host.contacts[0].copyPathTo(path));
      assert(!f.store.saveChannels(&f.host));
      assert(fs.files.at("/channels2") == channel_bytes);
    }
    strcpy(f.host.contacts[0].name, "Accepted contact change");
    assert(f.store.saveContacts(&f.host, nullptr));
    assert(!f.store.hasIncompleteContactLoad() && f.host.contacts[0].copyPathTo(path));
    assert(fs.files.at("/channels2") == channel_bytes);
    if (!quarantine) {
      if (fault == 1) assert(!strcmp(f.host.channels[0].name, "Public"));
      else assert(!strcmp(f.host.channels[0].name, "Saved"));
      assert(f.store.saveChannels(&f.host));
      assert(fs.files.at("/channels2").size() == 68);
    }
  }
}

#if defined(RP2040_PLATFORM)
static void malformed_primary_channels_prefer_a_valid_backup() {
  // Valid, malformed, unreadable, overcapacity, blocked remove, failed move,
  // and a move whose acknowledgement is false despite successful publication.
  for (unsigned fault = 0; fault < 7; ++fault) {
    ChannelFixture f(30);
    initializeContacts(f);
    auto& fs = channelFs();
    fs.files["/channels2"] = {1, 2, 3};
    fs.files["/channels2.bak"] = channelImage("Recovered backup");
    if (fault == 1) fs.files["/channels2.bak"].pop_back();
    if (fault == 2) fs.fail_open = "/channels2.bak";
    if (fault == 3) fs.files["/channels2.bak"] = channelImage("Larger profile", MAX_GROUP_CHANNELS + 1);
    if (fault == 4) fs.fail_remove = true;
    if (fault == 5) fs.fail_rename = fs.renames + 1;
    if (fault == 6) fs.late_channel_rename_error = true;
    const auto original = fs.files;
    f.store.loadChannels(&f.host);
    assert(!f.store.hasIncompleteContactLoad() && !f.store.contactRecoveryPending());
    assert(f.store.hasIncompleteChannelLoad() == (fault >= 2 && fault <= 5));
    if (fault == 0 || fault >= 4) assert(!strcmp(f.host.channels[0].name, "Recovered backup"));
    else assert(!strcmp(f.host.channels[0].name, "Public"));
    if (fault == 0 || fault == 6) {
      assert(!fs.exists("/channels2.bak"));
      assert(fs.files.at("/channels2") == original.at("/channels2.bak"));
      assert(f.store.saveChannels(&f.host));
    } else if (fault == 1) {
      assert(fs.files == original); // Both malformed images await a normal save.
      assert(f.store.saveChannels(&f.host));
      assert(fs.files.at("/channels2").size() == 68);
    } else {
      assert(!f.store.saveChannels(&f.host));
      assert(fs.files.at("/channels2.bak") == original.at("/channels2.bak"));
    }
  }
}
#endif

#if !defined(ESP32_PLATFORM)
static void explicit_format_alone_clears_channel_quarantine() {
  ChannelFixture f(30);
  initializeContacts(f);
  auto& fs = channelFs();
  fs.files["/channels2"] = channelImage("Protected");
  fail_channel_allocation = true;
  f.store.loadChannels(&f.host);
  fail_channel_allocation = false;
  assert(f.store.hasIncompleteChannelLoad() && !f.store.hasIncompleteContactLoad());
  const auto original = fs.files;
  fs.fail_channel_format = true;
  assert(!f.store.formatFileSystem());
  assert(f.store.hasIncompleteChannelLoad() && fs.files == original);
  fs.fail_channel_format = false;
  assert(f.store.formatFileSystem());
  assert(!f.store.hasIncompleteChannelLoad() && !f.store.hasIncompleteContactLoad());
  assert(fs.files.empty() && f.store.saveChannels(&f.host));
}
#endif

int main() {
  channel_faults_do_not_poison_contacts();
#if defined(RP2040_PLATFORM)
  malformed_primary_channels_prefer_a_valid_backup();
#endif
#if !defined(ESP32_PLATFORM)
  explicit_format_alone_clears_channel_quarantine();
#endif
  puts("PASS: channel/contact storage isolation");
}
