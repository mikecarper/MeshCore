#include "MyMesh.h"

#if MESH_ENABLE_MESHTEXT_CHAT
#if defined(NRF52_PLATFORM)
#include <helpers/NRF52Crypto.h>
#else
#include <Preferences.h>
#include <esp_system.h>
#endif
#include <helpers/CLICommandUtils.h>
#include <helpers/UTF8Helpers.h>

namespace {
constexpr size_t LegacySettingsSize = 16;
constexpr size_t SettingsSize = 40;
bool validShortName(const char* name) {
  if (!name) return false;
  const size_t length = strnlen(name, 17);
  if (length > 16 || mesh::validUtf8PrefixLength(name, length) != length) return false;
  unsigned characters = 0;
  for (size_t i = 0; i < length;) {
    const uint8_t first = static_cast<uint8_t>(name[i++]);
    uint32_t scalar = first;
    unsigned remaining = 0;
    if (first >= 0xf0) { scalar &= 7; remaining = 3; }
    else if (first >= 0xe0) { scalar &= 15; remaining = 2; }
    else if (first >= 0xc0) { scalar &= 31; remaining = 1; }
    while (remaining--) scalar = (scalar << 6) | (static_cast<uint8_t>(name[i++]) & 63);
    // Reject control/bidi characters in a displayed NodeInfo short name.
    if (scalar <= 32 || (scalar >= 0x7f && scalar <= 0x9f) || scalar == ':'
        || (scalar >= 0x200b && scalar <= 0x200f) || (scalar >= 0x2028 && scalar <= 0x202f)
        || (scalar >= 0x2060 && scalar <= 0x206f) || scalar == 0xfeff) return false;
    if (++characters > 4) return false;
  }
  return true; // Empty is the stored default, selected explicitly by the CLI.
}
void makeWireShortName(const char* configured, uint32_t node, char out[5]) {
  // Stock User.short_name has four UTF-8 bytes, not four Unicode scalars.
  // Project older saved labels without changing their persistent settings.
  const size_t length = mesh::validUtf8PrefixLength(configured, 4);
  if (length) {
    memcpy(out, configured, length);
    out[length] = 0;
  } else {
    snprintf(out, 5, "%04lx", (unsigned long)(node & 0xffff));
  }
}
bool validNodeOverride(uint32_t node) {
  return node == 0 || (node >= 4 && node != UINT32_MAX);
}
void makeSettings(uint8_t image[SettingsSize], mesh::mt::Region region, uint32_t slot,
                  uint8_t hops, const char* short_name, uint32_t node_override) {
  memset(image, 0, SettingsSize);
  memcpy(image, "MT03", 4);
  image[4] = static_cast<uint8_t>(region);
  image[5] = hops;
  for (unsigned i = 0; i < 4; ++i) image[8 + i] = slot >> (8 * i);
  memcpy(image + 12, short_name, strlen(short_name));
  for (unsigned i = 0; i < 4; ++i) image[32 + i] = node_override >> (8 * i);
  mesh::Utils::sha256(image + 36, 4, image, 36);
}
bool describe(const ChannelDetails& channel, mesh::mt::Channel& wire) {
  return mesh::mt::describeChannel(channel.name, channel.channel.secret, wire,
      [](uint8_t* out, size_t size, const uint8_t* in, size_t length) {
        mesh::Utils::sha256(out, size, in, length);
      });
}
bool nextToken(const char*& cursor, char* out, size_t capacity) {
  while (*cursor == ' ' || *cursor == '\t') ++cursor;
  size_t size = 0;
  while (*cursor && *cursor != ' ' && *cursor != '\t') {
    if (size + 1 >= capacity) return false;
    out[size++] = *cursor++;
  }
  out[size] = 0;
  return size != 0;
}
bool endTokens(const char* cursor) {
  while (*cursor == ' ' || *cursor == '\t') ++cursor;
  return !*cursor;
}
}

mesh::mt::Region MyMesh::meshtextRegion() const {
  return _mt_region == mesh::mt::Region::Unset
      ? mesh::mt::inferRegion(_prefs.freq) : _mt_region;
}

void MyMesh::refreshMeshtextIdentity() {
  uint8_t digest[4];
  mesh::Utils::sha256(digest, sizeof(digest), self_id.pub_key, PUB_KEY_SIZE);
  _mt_default_node = uint32_t(digest[0]) | (uint32_t(digest[1]) << 8)
      | (uint32_t(digest[2]) << 16) | (uint32_t(digest[3]) << 24);
  if (_mt_default_node < 4 || _mt_default_node == UINT32_MAX) _mt_default_node = 0x0badf00d;
  _mt_node = _mt_node_override ? _mt_node_override : _mt_default_node;
  _mt_seen.clear();
  // Importing a new MeshCore identity may change the derived sender ID, but
  // must never restart the packet sequence used by any earlier sender ID.
}

void MyMesh::beginMeshtext() {
  refreshMeshtextIdentity();
  // Independent random boot seed, then monotonic IDs. Never reuse a packet ID
  // within this boot (including failed admission) with the same CTR sender.
#if defined(NRF52_PLATFORM)
  getRNG()->random(reinterpret_cast<uint8_t*>(&_mt_packet_id), sizeof(_mt_packet_id));
#if defined(USE_CC310_HW_CRYPTO)
  mesh::mixCC310Random(reinterpret_cast<uint8_t*>(&_mt_packet_id), sizeof(_mt_packet_id));
#endif
#else
  _mt_packet_id = esp_random();
#endif
  if (!_mt_packet_id) _mt_packet_id = 1;
  uint8_t image[SettingsSize] = {}, expected[SettingsSize] = {};
#if defined(NRF52_PLATFORM)
  size_t size = 0;
  if (!_store->loadMeshtextSettings(image, size)) { _mt_settings_valid = false; return; }
#else
  Preferences prefs;
  if (!prefs.begin("mesh-mt", false)) { _mt_settings_valid = false; return; }
  const size_t size = prefs.getBytesLength("settings");
#endif
  if (size) {
#if defined(NRF52_PLATFORM)
    _mt_settings_valid = size == SettingsSize;
#else
    _mt_settings_valid = (size == SettingsSize || size == LegacySettingsSize)
        && prefs.getBytes("settings", image, size) == size;
#endif
    if (_mt_settings_valid) {
      const auto region = static_cast<mesh::mt::Region>(image[4]);
      const bool legacy = size == LegacySettingsSize;
      const bool legacy_v1 = legacy && memcmp(image, "MT01", 4) == 0;
      const uint8_t hops = legacy_v1 ? 7 : image[5];
      const uint32_t slot = uint32_t(image[8]) | (uint32_t(image[9]) << 8)
          | (uint32_t(image[10]) << 16) | (uint32_t(image[11]) << 24);
      if (legacy) {
        memcpy(expected, legacy_v1 ? "MT01" : "MT02", 4);
        expected[4] = image[4]; expected[5] = legacy_v1 ? 0 : hops;
        memcpy(expected + 8, image + 8, 4);
        mesh::Utils::sha256(expected + 12, 4, expected, 12);
      } else if (validShortName(reinterpret_cast<const char*>(image + 12))) {
        const uint32_t node_override = uint32_t(image[32]) | (uint32_t(image[33]) << 8)
            | (uint32_t(image[34]) << 16) | (uint32_t(image[35]) << 24);
        if (validNodeOverride(node_override))
          makeSettings(expected, region, slot, hops, reinterpret_cast<const char*>(image + 12), node_override);
      }
      _mt_settings_valid = region <= mesh::mt::Region::ANZ && slot <= 1024 && hops <= 7
          && memcmp(image, expected, size) == 0;
      if (_mt_settings_valid) {
        _mt_region = region; _mt_slot = slot; _mt_hop_limit = hops;
        if (!legacy) {
          memcpy(_mt_short_name, image + 12, sizeof(_mt_short_name));
          _mt_node_override = uint32_t(image[32]) | (uint32_t(image[33]) << 8)
              | (uint32_t(image[34]) << 16) | (uint32_t(image[35]) << 24);
          _mt_node = _mt_node_override ? _mt_node_override : _mt_default_node;
        }
      }
    }
  }
#if !defined(NRF52_PLATFORM)
  prefs.end();
#endif
  refreshMeshtextProfile();
}

bool MyMesh::saveMeshtextSettings(mesh::mt::Region region, uint32_t slot, uint8_t hops,
                                  const char* short_name, uint32_t node_override) {
  if (hops == UINT8_MAX) hops = _mt_hop_limit;
  if (!short_name) short_name = _mt_short_name;
  if (node_override == UINT32_MAX) node_override = _mt_node_override;
  if (region > mesh::mt::Region::ANZ || slot > 1024 || hops > 7 || !validShortName(short_name)
      || !validNodeOverride(node_override) || hasForeignRadioFrame()) return false;
  uint8_t image[SettingsSize];
  makeSettings(image, region, slot, hops, short_name, node_override);
#if defined(NRF52_PLATFORM)
  const bool ok = _store->saveMeshtextSettings(image);
#else
  uint8_t verify[SettingsSize];
  Preferences prefs;
  if (!prefs.begin("mesh-mt", false)) return false;
  // NVS commits this one blob atomically; never split region and slot into
  // separate commits which could join an unintended network after power loss.
  const bool ok = prefs.putBytes("settings", image, sizeof(image)) == sizeof(image)
      && prefs.getBytes("settings", verify, sizeof(verify)) == sizeof(verify)
      && memcmp(image, verify, sizeof(image)) == 0;
  prefs.end();
#endif
  if (!ok) {
    // A failed read-back cannot prove whether storage committed. Suspend the
    // backend rather than silently use one network now and another at boot.
    _mt_settings_valid = false;
    _mt_profile_dirty = true;
    refreshMeshtextProfile();
    return false;
  }
  _mt_region = region; _mt_slot = slot; _mt_hop_limit = hops; _mt_settings_valid = true;
  memcpy(_mt_short_name, image + 12, sizeof(_mt_short_name));
  _mt_node_override = node_override;
  _mt_node = node_override ? node_override : _mt_default_node;
  // Keep the packet sequence monotonic even when changing away from and back
  // to a sender ID. Never reset it on a name/settings change (CTR nonce reuse).
  _mt_profile_dirty = true;
  _mt_seen.clear();
  refreshMeshtextProfile();
  return true;
}

bool MyMesh::eraseMeshtextSettings() {
  // The filesystem erase also removes channels/identity on the next boot.
  // Do not keep sending with their still-resident RAM copies until then.
  _mt_active = false;
  _mt_settings_valid = false;
  _mt_profile_dirty = true;
  _mt_seen.clear();
  _radio_profiles.clearAutoSecondary();
  _radio_profiles.clearSecondaryProtocol();
#if defined(NRF52_PLATFORM)
  const bool ok = _store->eraseMeshtextSettings();
#else
  Preferences prefs;
  if (!prefs.begin("mesh-mt", false)) {
    _mt_status = "Meshtext settings erase failed";
    return false;
  }
  const bool ok = !prefs.getBytesLength("settings") || prefs.remove("settings");
  prefs.end();
#endif
  if (ok) {
    _mt_region = mesh::mt::Region::Unset; _mt_slot = 0; _mt_hop_limit = 7;
    _mt_short_name[0] = 0; _mt_node_override = 0; _mt_node = _mt_default_node;
  }
  _mt_status = ok ? "settings erased; reboot required" : "Meshtext settings erase failed";
  return ok;
}

bool MyMesh::isMeshtextChannel(const ChannelDetails& channel) const {
  const auto alias = parseCompanionChannelAlias(channel.name);
  if (alias.kind != CompanionChannelKind::Meshtext) return false;
  // With the backend present, Meshtext names are explicit protocol requests even
  // while setup is blocked. Never silently emit a different wire protocol.
  return true;
}

bool MyMesh::validateMeshtextChannelChange(int index, const ChannelDetails& candidate) {
  const auto candidate_kind = parseCompanionChannelAlias(candidate.name).kind;
  // Unrelated MeshCore edits and removals remain possible even when an old
  // Meshtext configuration is suspended or incompatible with a new firmware build.
  if (!candidate.name[0] || candidate_kind == CompanionChannelKind::MeshCore) return true;
  bool have_preset = false, have_mt = false, have_r2 = false;
  mesh::mt::Preset preset = mesh::mt::Preset::LongFast;
  for (int i = 0; i < MAX_GROUP_CHANNELS; ++i) {
    ChannelDetails channel;
    if (i == index) channel = candidate;
    else if (!BaseChatMesh::getChannel(i, channel)) continue;
    if (!channel.name[0]) continue;
    const auto alias = parseCompanionChannelAlias(channel.name);
    if (alias.kind == CompanionChannelKind::Radio2) have_r2 = true;
    if (alias.kind != CompanionChannelKind::Meshtext) continue;
    mesh::mt::Preset found;
    const bool named = mesh::mt::parsePreset(alias.tail, found);
    mesh::mt::Channel wire;
    if (!describe(channel, wire)) return false;
    have_mt = true;
    if (named) {
      if (have_preset && preset != found) return false;
      preset = found; have_preset = true;
    }
  }
  // radio2 is one modem profile, not a second physical transceiver. There is
  // no silent last-channel-wins behavior and no protocol bridging.
  if (have_mt && have_r2) return false;
  if (!have_mt) return true;
  if (!_radio->supportsMeshtext() || !_mt_settings_valid) return false;
  if (have_preset) {
    mesh::RadioProfileParams requested;
    if (!mesh::mt::modemConfig(meshtextRegion(), preset, requested, _mt_slot)
        || !_radio->validateProfile(requested)) return false;
    if (_radio_profiles.manualSecondaryConfigured()) {
      const auto saved = _radio_profiles.savedSecondaryConfig();
      if (!mesh::mt::sameModulation(saved.params, requested)
          || !mesh::mt::frequencyInRegion(meshtextRegion(), saved.params)) return false;
    } else if (!_radio_profiles.canAutoSecondary()) return false;
  }
  return true;
}

void MyMesh::refreshMeshtextProfile() {
#if MESH_ENABLE_ETHERNET_TUNNEL
  // This owner must never revoke an Ethernet lease simply because it has
  // no Meshtext channels. Explicit channel conflicts suspend Ethernet too.
  if (_ethernet_settings.enabled) {
    _mt_active = false;
    _mt_status = "radio2 reserved for Ethernet";
    _mt_profile_dirty = true;
    return;
  }
#endif
  const auto* current_profiles = _radio->profiles();
  const uint32_t primary_generation = current_profiles ? current_profiles->generation[0] : 0;
  const uint32_t secondary_generation = current_profiles ? current_profiles->generation[1] : 0;
  const uint32_t intent_generation = _radio_profiles.secondaryIntentGeneration();
  if (!_mt_profile_dirty && primary_generation == _mt_primary_generation
      && secondary_generation == _mt_secondary_generation && intent_generation == _mt_intent_generation
      && _mt_last_radio_ready == _radio_available && _mt_primary_frequency == _prefs.freq) return;
  // Hashing names and scanning the channel table every radio loop would add
  // blind time to the dual-profile scanner. Recompute only on configuration
  // or lease/generation edges; RX/TX still derive a fresh bounded wire copy.
  struct CacheUpdate {
    MyMesh& owner;
    ~CacheUpdate() {
      const auto* p = owner._radio->profiles();
      owner._mt_primary_generation = p ? p->generation[0] : 0;
      owner._mt_secondary_generation = p ? p->generation[1] : 0;
      owner._mt_intent_generation = owner._radio_profiles.secondaryIntentGeneration();
      owner._mt_last_radio_ready = owner._radio_available;
      owner._mt_primary_frequency = owner._prefs.freq;
      owner._mt_profile_dirty = false;
    }
  } cache_update{*this};
  _mt_active = false;
  bool have_mt = false, have_r2 = false, have_preset = false, conflict = false;
  mesh::mt::Preset preset = mesh::mt::Preset::LongFast;
  for (int i = 0; i < MAX_GROUP_CHANNELS; ++i) {
    ChannelDetails channel;
    if (!BaseChatMesh::getChannel(i, channel) || !channel.name[0]) continue;
    const auto alias = parseCompanionChannelAlias(channel.name);
    if (alias.kind == CompanionChannelKind::Radio2) have_r2 = true;
    if (alias.kind != CompanionChannelKind::Meshtext) continue;
    mesh::mt::Preset found;
    const bool named = mesh::mt::parsePreset(alias.tail, found);
    have_mt = true;
    mesh::mt::Channel wire;
    if (!describe(channel, wire)) conflict = true;
    if (named) {
      if (have_preset && preset != found) conflict = true;
      preset = found; have_preset = true;
    }
  }
  const char* blocked = nullptr;
  if (!have_mt) blocked = "no Meshtext channels";
  else if (conflict || have_r2) blocked = "conflicting radio2 channels/presets";
  else if (!_radio_available || !_radio->supportsMeshtext()) blocked = "modem unavailable";
  else if (!_mt_settings_valid) blocked = "invalid Meshtext settings; set mt.region to repair";
  else if (meshtextRegion() == mesh::mt::Region::Unset) blocked = "region ambiguous; set mt.region";
  if (blocked) {
    _mt_status = blocked;
    _radio_profiles.clearAutoSecondary();
    _radio_profiles.clearSecondaryProtocol();
    return;
  }
  mesh::RadioProfileParams requested;
  if (have_preset && (!mesh::mt::modemConfig(meshtextRegion(), preset, requested, _mt_slot)
      || !_radio->validateProfile(requested))) {
    _mt_status = "preset/slot unavailable in this region or modem";
    _radio_profiles.clearAutoSecondary();
    _radio_profiles.clearSecondaryProtocol();
    return;
  }
  if (_radio_profiles.manualSecondaryConfigured()) {
    const auto saved = _radio_profiles.savedSecondaryConfig();
    if (!mesh::mt::frequencyInRegion(meshtextRegion(), saved.params)
        || (have_preset && !mesh::mt::sameModulation(saved.params, requested))) {
      _mt_status = "manual radio2 does not match Meshtext preset/region";
      _radio_profiles.clearSecondaryProtocol();
      return;
    }
    _mt_active = _radio_profiles.useSecondaryProtocol(mesh::RadioProfileProtocol::Meshtext);
  } else if (have_preset) {
    mesh::RadioProfileConfig config;
    config.params = requested; config.mode = mesh::RadioProfileMode::RxTx;
    const auto* profiles = _radio->profiles();
    if (_radio_profiles.autoSecondaryActive() && profiles
        && profiles->secondary.params != requested) {
      _radio_profiles.clearAutoSecondary(); // only release the profile we own
    }
    _mt_active = _radio_profiles.requestAutoSecondary(config);
  } else {
    // A custom channel cannot reconstruct an RF preset after reboot. Losing
    // its last named-preset dependency must release the auto owner, not leave
    // a ghost-enabled secondary or silently turn into MeshCore channel chat.
    _radio_profiles.clearAutoSecondary();
    _radio_profiles.clearSecondaryProtocol();
    _mt_status = "custom Meshtext channel needs a named preset or manual radio2";
    return;
  }
  _mt_status = _mt_active ? "active" : "radio2 manually off, leased, scheduled or busy";
}

bool MyMesh::sendMeshtextText(const ChannelDetails& channel, const char* text, size_t length) {
  refreshMeshtextProfile();
  if (!_mt_active || !text || !length || hasForeignRadioFrame()) return false;
  const auto* profiles = _radio->profiles();
  if (!profiles || !profiles->canTransmit(1)
      || profiles->secondary.params.protocol != mesh::RadioProfileProtocol::Meshtext) return false;
  mesh::mt::Channel wire;
  if (!describe(channel, wire)) return false;
  // Stock TEXT_MESSAGE_APP contains the user's text only. Sender identity is
  // in the radio header; name metadata requires a separately requested User.
  if (length > mesh::mt::MaxTextSize) return false;
  if (!_mt_packet_id) return false; // exhaustion: do not wrap/reuse CTR nonces.
  const uint32_t id = _mt_packet_id++;
  uint8_t raw[mesh::mt::MaxFrameSize];
  // Outbound Meshtext channel chat defaults to seven hops; the CLI can reduce it.
  // This does not enable forwarding or ACK responses in this Companion.
  const size_t size = mesh::mt::encodeText(_mt_node, id, _mt_hop_limit, wire.name, wire.key,
                                         wire.key_length, text, length, raw);
  return size && queueForeignRadioFrame(raw, size, 1, profiles->generation[1]);
}

bool MyMesh::sendMeshtextNodeInfo(const ChannelDetails& channel) {
  refreshMeshtextProfile();
  if (!_mt_active || hasForeignRadioFrame()) return false;
  const auto* profiles = _radio->profiles();
  if (!profiles || !profiles->canTransmit(1)
      || profiles->secondary.params.protocol != mesh::RadioProfileProtocol::Meshtext) return false;
  mesh::mt::Channel wire;
  if (!describe(channel, wire)) return false;
  const size_t full_length = strnlen(_prefs.node_name, sizeof(_prefs.node_name));
  if (full_length == sizeof(_prefs.node_name)
      || mesh::validUtf8PrefixLength(_prefs.node_name, full_length) != full_length) return false;
  const size_t long_length = mesh::validUtf8PrefixLength(_prefs.node_name, 24);
  char short_name[5];
  makeWireShortName(_mt_short_name, _mt_node, short_name);
  if (!_mt_packet_id) return false;
  // Share the text packet sequence: separate counters would reuse CTR nonces.
  const uint32_t id = _mt_packet_id++;
  uint8_t raw[mesh::mt::MaxFrameSize];
  const size_t size = mesh::mt::encodeNodeInfo(_mt_node, id, _mt_hop_limit, wire.name, wire.key,
      wire.key_length, _prefs.node_name, long_length, short_name, strlen(short_name), raw);
  // Only the explicit CLI reaches here. No timer, RX reply or chat piggyback.
  return size && queueForeignRadioFrame(raw, size, 1, profiles->generation[1]);
}

bool MyMesh::onForeignRadioFrame(const uint8_t* bytes, size_t length, uint8_t profile,
                               uint32_t generation, float snr, float rssi) {
#if MESH_ENABLE_ETHERNET_TUNNEL
  const auto* receiving_profiles = _radio->profiles();
  if (receiving_profiles && profile < 2
      && receiving_profiles->params(profile).protocol == mesh::RadioProfileProtocol::Ethernet)
    return onEthernetFrame(bytes, length, profile, generation);
#endif
  (void)rssi;
  const auto* profiles = _radio->profiles();
  if (!_mt_active || !profiles || profile != 1 || generation != profiles->generation[1]) return false;
  for (int i = 0; i < MAX_GROUP_CHANNELS; ++i) {
    ChannelDetails channel;
    mesh::mt::Channel wire;
    if (!BaseChatMesh::getChannel(i, channel) || !describe(channel, wire)) continue;
    mesh::mt::DecodedText decoded;
    if (!mesh::mt::decodeText(bytes, length, wire.name, wire.key, wire.key_length, _mt_node, decoded)) continue;
    if (_mt_seen.seen(decoded.from, decoded.id)) return true;
    char text[MAX_FRAME_SIZE];
    const int prefix = snprintf(text, sizeof(text), "!%08lx: ", (unsigned long)decoded.from);
    size_t copy = decoded.text_len;
    if (copy > sizeof(text) - prefix - 1) copy = sizeof(text) - prefix - 1;
    // Preserve UTF-8 when fitting a stock peer text into the Companion envelope.
    while (copy < decoded.text_len && copy && (uint8_t(decoded.text[copy]) & 0xc0) == 0x80) --copy;
    memcpy(text + prefix, decoded.text, copy); text[prefix + copy] = 0;
    const uint32_t timestamp = getRTCClock()->getCurrentTime();
    ChannelDetails policy_channel;
    if (!getChannel(i, policy_channel)) return false;
    if (queueChannelText(i, policy_channel.channel.secret, snr, decoded.hops, timestamp, text, false)) {
      _mt_seen.remember(decoded.from, decoded.id);
      ++_mt_received;
    }
    return true;
  }
  return false;
}

void MyMesh::onForeignRadioSendComplete(bool success) {
#if MESH_ENABLE_ETHERNET_TUNNEL
  if (queuedForeignRadioProtocol() == mesh::RadioProfileProtocol::Ethernet) {
    if (success) ++_ethernet_sent;
    else ++_ethernet_failed;
    return;
  }
#endif
  if (success) ++_mt_sent;
  else ++_mt_failed;
}

bool MyMesh::handleMeshtextCommand(const char* command, char* reply, size_t capacity) {
  const char* nodeinfo_args = !strncmp(command, "send mt.nodeinfo ", 17) ? command + 17 : nullptr;
  if (!strcmp(command, "send mt.nodeinfo")) nodeinfo_args = "";
  if (nodeinfo_args) {
    char index_text[12]; uint32_t index = 0;
    ChannelDetails channel;
    if (!nextToken(nodeinfo_args, index_text, sizeof(index_text)) || !endTokens(nodeinfo_args)
        || !mesh::cli::parseUnsignedIntegerStrict(index_text, index) || index >= MAX_GROUP_CHANNELS) {
      snprintf(reply, capacity, "Error: use send mt.nodeinfo <channel slot>");
    } else if (!getChannelForSave(index, channel) || !isMeshtextChannel(channel)) {
      snprintf(reply, capacity, "Error: configured Meshtext channel required");
    } else if (!sendMeshtextNodeInfo(channel)) {
      snprintf(reply, capacity, "Error: NodeInfo not queued; check get mt.status, TX mode or pending TX");
    } else {
      snprintf(reply, capacity, "OK - NodeInfo queued on Meshtext channel %lu; no automatic adverts",
          (unsigned long)index);
    }
    return true;
  }
  const char* channel_args = !strncmp(command, "set mt.channel ", 15) ? command + 15 : nullptr;
  const char* channel_get = !strncmp(command, "get mt.channel ", 15) ? command + 15 : nullptr;
  const char* channel_delete = !strncmp(command, "delete mt.channel ", 18) ? command + 18 : nullptr;
  if (channel_args || channel_get || channel_delete) {
    const char* cursor = channel_args ? channel_args : channel_get ? channel_get : channel_delete;
    char index_text[12]; uint32_t index = 0;
    if (!nextToken(cursor, index_text, sizeof(index_text))
        || !mesh::cli::parseUnsignedIntegerStrict(index_text, index) || index >= MAX_GROUP_CHANNELS) {
      snprintf(reply, capacity, "Error: invalid channel slot"); return true;
    }
    ChannelDetails previous{}, channel{};
    if (!getChannelForSave(index, previous)) {
      snprintf(reply, capacity, "Error: channel slot unavailable"); return true;
    }
    if (channel_get || channel_delete) {
      if (!endTokens(cursor) || !previous.name[0] || !isMeshtextChannel(previous)) {
        snprintf(reply, capacity, "Error: Meshtext channel not found or extra arguments"); return true;
      }
      if (channel_get) {
        mesh::mt::Channel wire;
        if (!describe(previous, wire)) snprintf(reply, capacity, "Error: invalid Meshtext channel");
        else snprintf(reply, capacity, "> %lu %s; wire=%s; AES-%u; key not displayed",
            (unsigned long)index, previous.name, wire.name, unsigned(wire.key_length * 8));
        return true;
      }
    } else {
      char key_text[65];
      if (!nextToken(cursor, channel.name, sizeof(channel.name))
          || !nextToken(cursor, key_text, sizeof(key_text)) || !endTokens(cursor)
          || !isMeshtextChannel(channel)) {
        snprintf(reply, capacity, "Error: use set mt.channel <slot> <mt-name> <32/64 hex digits|AQ==>"); return true;
      }
      if (!strcmp(key_text, "AQ==")) memcpy(channel.channel.secret, mesh::mt::DefaultPsk, 16);
      else {
        const size_t digits = strlen(key_text);
        if ((digits != 32 && digits != 64)
            || !mesh::Utils::fromHex(channel.channel.secret, digits / 2, key_text)) {
          snprintf(reply, capacity, "Error: key must be 32/64 hex digits or AQ=="); return true;
        }
      }
      if (!validateMeshtextChannelChange(index, channel)) {
        snprintf(reply, capacity, "Error: conflicting Meshtext preset, region, manual radio2 or busy owner"); return true;
      }
    }
    if (!setChannel(index, channel)) snprintf(reply, capacity, "Error: invalid Meshtext channel");
    else if (!saveChannels()) {
      setChannel(index, previous);
      snprintf(reply, capacity, "Error: channel not saved; previous channel restored");
    } else {
      _mt_seen.clear(); _mt_profile_dirty = true;
      refreshMeshtextProfile();
      snprintf(reply, capacity, "OK - Meshtext channel %lu %s; %s", (unsigned long)index,
          channel_delete ? "deleted" : "saved", _mt_status);
    }
    return true;
  }
  const char* preset_value = !strncmp(command, "set mt.preset ", 14) ? command + 14 : nullptr;
  if (preset_value) {
    mesh::mt::Preset preset; mesh::RadioProfileParams requested;
    if (!mesh::mt::parsePreset(preset_value, preset)
        || !mesh::mt::modemConfig(meshtextRegion(), preset, requested, _mt_slot)
        || !_radio->supportsMeshtext() || !_radio->validateProfile(requested)) {
      snprintf(reply, capacity, "Error: invalid Meshtext preset/region/slot; use set mt.region first"); return true;
    }
    // Explicit provisioning for custom private names. This is a manual saved
    // radio2 choice, not an automatic alias owner, so deletion cannot undo it.
    char radio_command[96];
    snprintf(radio_command, sizeof(radio_command), "set radio2 %.6f,%g,%u,%u,rxtx",
        requested.freq, requested.bw, requested.sf, requested.cr);
    _radio_profiles.handle(radio_command, reply, capacity);
    _mt_profile_dirty = true;
    refreshMeshtextProfile();
    return true;
  }
  const bool status = !strcmp(command, "get mt") || !strcmp(command, "get mt.status");
  const bool get_region = !strcmp(command, "get mt.region");
  const bool get_slot = !strcmp(command, "get mt.slot");
  const bool get_hops = !strcmp(command, "get mt.hops");
  const bool get_node = !strcmp(command, "get mt.node");
  const bool get_short_name = !strcmp(command, "get mt.shortname");
  const char* region_value = !strncmp(command, "set mt.region ", 14) ? command + 14 : nullptr;
  const char* slot_value = !strncmp(command, "set mt.slot ", 12) ? command + 12 : nullptr;
  const char* hops_value = !strncmp(command, "set mt.hops ", 12) ? command + 12 : nullptr;
  const char* node_value = !strncmp(command, "set mt.node ", 12) ? command + 12 : nullptr;
  const char* short_name_value = !strncmp(command, "set mt.shortname ", 17) ? command + 17 : nullptr;
  if (!strcmp(command, "set mt.node")) node_value = "";
  if (!strcmp(command, "set mt.shortname")) short_name_value = "";
  if (!status && !get_region && !get_slot && !get_hops && !get_node && !get_short_name
      && !region_value && !slot_value && !hops_value && !node_value && !short_name_value) return false;
  if (region_value || slot_value || hops_value || node_value || short_name_value) {
    auto region = _mt_region; uint32_t slot = _mt_slot;
    uint32_t hops = _mt_hop_limit;
    uint32_t node_override = _mt_node_override;
    const char* short_name = _mt_short_name;
    if (region_value && !mesh::mt::parseRegion(region_value, region)) {
      snprintf(reply, capacity, "Error: use set mt.region auto|us|eu868|eu433|anz"); return true;
    }
    if (slot_value && (!mesh::cli::parseUnsignedIntegerStrict(slot_value, slot) || slot > 1024)) {
      snprintf(reply, capacity, "Error: mt.slot must be 0 (preset default) or 1..1024"); return true;
    }
    if (hops_value && (!mesh::cli::parseUnsignedIntegerStrict(hops_value, hops) || hops > 7)) {
      snprintf(reply, capacity, "Error: mt.hops must be 0..7"); return true;
    }
    if (node_value) {
      if (!strcmp(node_value, "default")) node_override = 0;
      else {
        if (*node_value == '!') ++node_value;
        uint8_t bytes[4];
        if (strlen(node_value) != 8 || !mesh::Utils::fromHex(bytes, sizeof(bytes), node_value)) {
          snprintf(reply, capacity, "Error: use set mt.node !<8 hex digits>|default"); return true;
        }
        node_override = (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16)
            | (uint32_t(bytes[2]) << 8) | uint32_t(bytes[3]);
        if (!node_override || !validNodeOverride(node_override)) {
          snprintf(reply, capacity, "Error: mt.node IDs 0..3 and ffffffff are reserved; use default to reset"); return true;
        }
      }
    }
    if (short_name_value) {
      short_name = !strcmp(short_name_value, "default") ? "" : short_name_value;
      if ((!*short_name && strcmp(short_name_value, "default")) || !validShortName(short_name)) {
        snprintf(reply, capacity, "Error: mt.shortname must be 1..4 UTF-8 characters (no spaces/control/colon), or default"); return true;
      }
    }
    if (!saveMeshtextSettings(region, slot, hops, short_name, node_override))
      snprintf(reply, capacity, "Error: Meshtext settings not saved (pending TX or storage error)");
    else if (node_value)
      snprintf(reply, capacity, "OK - mt.node=!%08lx (%s); choose a unique ID", (unsigned long)_mt_node,
          _mt_node_override ? "saved override" : "identity default");
    else if (short_name_value)
      snprintf(reply, capacity, "OK - mt.shortname=%s; node=!%08lx", _mt_short_name[0] ? _mt_short_name : "default",
          (unsigned long)_mt_node);
    else snprintf(reply, capacity, "OK - mt.region=%s mt.slot=%lu mt.hops=%lu; %s", mesh::mt::regionName(region), (unsigned long)slot, (unsigned long)hops, _mt_status);
    return true;
  }
  refreshMeshtextProfile();
  if (get_region) snprintf(reply, capacity, "> %s; effective=%s", mesh::mt::regionName(_mt_region), mesh::mt::regionName(meshtextRegion()));
  else if (get_slot) snprintf(reply, capacity, "> %lu%s", (unsigned long)_mt_slot, _mt_slot ? "" : " (preset default)");
  else if (get_hops) snprintf(reply, capacity, "> %u", _mt_hop_limit);
  else if (get_node) snprintf(reply, capacity, "> !%08lx (%s)", (unsigned long)_mt_node,
      _mt_node_override ? "saved override" : "identity default");
  else if (get_short_name) {
    char short_name[5]; makeWireShortName(_mt_short_name, _mt_node, short_name);
    snprintf(reply, capacity, "> %s; effective=%s (manual NodeInfo only)",
        _mt_short_name[0] ? _mt_short_name : "default", short_name);
  }
  else {
    const auto* profiles = _radio->profiles();
    const auto p = profiles ? profiles->secondary.params : mesh::RadioProfileParams{};
    snprintf(reply, capacity, "> Meshtext !%08lx; %s; %s; %.3f/%g/SF%u/CR%u; hops=%u; RX=%lu TX=%lu fail=%lu; RX time-shared",
        (unsigned long)_mt_node, _mt_status, _radio_profiles.autoSecondaryActive() ? "auto radio2" : "manual radio2",
        p.freq, p.bw, p.sf, p.cr, _mt_hop_limit, (unsigned long)_mt_received, (unsigned long)_mt_sent, (unsigned long)_mt_failed);
  }
  return true;
}
#endif
