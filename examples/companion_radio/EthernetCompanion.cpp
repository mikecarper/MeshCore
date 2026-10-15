#include "MyMesh.h"

#if MESH_ENABLE_ETHERNET_TUNNEL
#include <Preferences.h>
#include <helpers/CLICommandUtils.h>
#include <helpers/CompanionChannelAlias.h>

void MyMesh::beginEthernetTunnel() {
  _ethernet_settings = {};
  _ethernet_settings_valid = false;
  Preferences prefs;
  if (!prefs.begin("mesh-eth", false)) { _ethernet_status = "settings unreadable"; return; }
  const size_t length = prefs.getBytesLength("settings");
  if (!length) _ethernet_settings_valid = true;
  else {
    uint8_t image[mesh::ethernet::SettingsSize];
    _ethernet_settings_valid = length == sizeof(image)
        && prefs.getBytes("settings", image, sizeof(image)) == sizeof(image)
        && mesh::ethernet::decodeSettings(image, sizeof(image), _ethernet_settings);
  }
  prefs.end();
  _ethernet_status = _ethernet_settings_valid ? "off" : "invalid settings; set ethernet.radio to repair";
}

bool MyMesh::ethernetChannelConflict() {
  if (_ethernet_checked_channels == _ethernet_channel_generation) return _ethernet_channels_conflict;
  _ethernet_checked_channels = _ethernet_channel_generation;
  _ethernet_channels_conflict = false;
  for (int i = 0; i < MAX_GROUP_CHANNELS; ++i) {
    ChannelDetails channel;
    if (!BaseChatMesh::getChannel(i, channel) || !channel.name[0]) continue;
    if (parseCompanionChannelAlias(channel.name).kind != CompanionChannelKind::MeshCore) {
      _ethernet_channels_conflict = true;
      break;
    }
  }
  return _ethernet_channels_conflict;
}

void MyMesh::releaseEthernetProfile() {
  _ethernet_active = false;
  _ethernet_kiss.loop(false);
  discardQueuedForeignRadioFrame(mesh::RadioProfileProtocol::Ethernet);
  const auto* profiles = _radio->profiles();
  if (_ethernet_owned && profiles
      && profiles->secondary.params.protocol == mesh::RadioProfileProtocol::Ethernet) {
    _radio_profiles.clearAutoSecondary();
    _radio_profiles.clearSecondaryProtocol();
  }
  _ethernet_owned = false;
  _ethernet_generation = 0;
}

bool MyMesh::saveEthernetSettings(const mesh::ethernet::Settings& settings) {
  if (hasForeignRadioFrame() || !mesh::ethernet::validSettings(settings)) return false;
  uint8_t image[mesh::ethernet::SettingsSize], verify[mesh::ethernet::SettingsSize];
  mesh::ethernet::encodeSettings(image, settings);
  Preferences prefs;
  if (!prefs.begin("mesh-eth", false)) return false;
  const bool ok = prefs.putBytes("settings", image, sizeof(image)) == sizeof(image)
      && prefs.getBytes("settings", verify, sizeof(verify)) == sizeof(verify)
      && !memcmp(image, verify, sizeof(image));
  prefs.end();
  releaseEthernetProfile();
  if (!ok) {
    // The write may have committed even if verification failed. Suspend
    // instead of using a different network now than the next boot will load.
    _ethernet_settings_valid = false;
    _ethernet_status = "settings save failed; suspended";
    return false;
  }
  _ethernet_settings = settings;
  _ethernet_settings_valid = true;
  if (settings.enabled && !ethernetChannelConflict()) {
    // Removing the last MT channel can leave its RAM lease until the next
    // loop. Release that unused owner only after this explicit enable saves.
    const auto* profiles = _radio->profiles();
    if (profiles && profiles->secondary.params.protocol == mesh::RadioProfileProtocol::Meshtext) {
      _radio_profiles.clearAutoSecondary();
      _radio_profiles.clearSecondaryProtocol();
    }
  }
  _ethernet_status = settings.enabled ? "waiting for radio2" : "off";
  return true;
}

bool MyMesh::eraseEthernetSettings() {
  releaseEthernetProfile();
  _ethernet_settings_valid = false;
  _ethernet_settings.enabled = false;
  Preferences prefs;
  if (!prefs.begin("mesh-eth", false)) return false;
  const bool ok = !prefs.getBytesLength("settings") || prefs.remove("settings");
  prefs.end();
  if (ok) { _ethernet_settings = {}; _ethernet_settings_valid = true; }
  _ethernet_status = ok ? "off" : "settings erase failed";
  return ok;
}

bool MyMesh::ethernetRadioConflict(const mesh::ethernet::Settings& settings) {
  if (ethernetChannelConflict() || _radio_profiles.secondaryTemporary()) return true;
  if (!_radio_profiles.manualSecondaryConfigured()) return !_radio_profiles.canAutoSecondary();
  auto saved = _radio_profiles.savedSecondaryConfig();
  saved.params.protocol = mesh::RadioProfileProtocol::Ethernet;
  return saved.mode != mesh::RadioProfileMode::RxTx || saved.params != settings.radio;
}

bool MyMesh::handleEthernetTunnelCommand(const char* command, char* reply, size_t capacity) {
  const bool status = !strcmp(command, "get ethernet") || !strcmp(command, "get ethernet.status");
  const bool get_radio = !strcmp(command, "get ethernet.radio");
  const bool set = !strncmp(command, "set ethernet", 12)
      && (command[12] == 0 || command[12] == ' ' || command[12] == '.');
  if (!status && !get_radio && !set) return false;
  if (status) {
    const auto& server = _ethernet_kiss.status();
    snprintf(reply, capacity, "> ethernet=%s; %s; TCP %u %s; RX=%lu TX=%lu fail=%lu; radio2 only",
        _ethernet_settings.enabled ? "on" : "off", _ethernet_status,
        mesh::ethernet::KissServer::Port, server.connected ? "connected" : server.listening ? "listening" : "stopped",
        (unsigned long)_ethernet_received, (unsigned long)_ethernet_sent, (unsigned long)_ethernet_failed);
    return true;
  }
  if (get_radio) {
    const auto& p = _ethernet_settings.radio;
    if (!_ethernet_settings.configured) snprintf(reply, capacity, "> unset; set ethernet.radio <MHz>,<kHz>,<SF>,<CR>[,<preamble>]");
    else snprintf(reply, capacity, "> %.3f,%g,%u,%u,%u; sync=0x16; %s",
        p.freq, p.bw, p.sf, p.cr, p.preamble, _ethernet_settings_valid ? "saved" : "invalid settings");
    return true;
  }
  auto settings = _ethernet_settings_valid ? _ethernet_settings : mesh::ethernet::Settings{};
  if (!strncmp(command, "set ethernet.radio ", 19)) {
    char tuple[96]; uint16_t preamble;
    if (!mesh::RadioProfileCLI::parseSuffix(command + 19, 4, tuple, sizeof(tuple), preamble)
        || !mesh::cli::parseRadioTupleStrict(tuple, settings.radio.freq, settings.radio.bw,
            settings.radio.sf, settings.radio.cr)) {
      snprintf(reply, capacity, "Error: use set ethernet.radio <MHz>,<kHz>,<SF>,<CR>[,<preamble>]"); return true;
    }
    settings.radio.preamble = preamble ? preamble : 32;
    settings.configured = true;
    if (!mesh::ethernet::validSettings(settings)
        || !_radio->supportsEthernetTunnel() || !_radio->validateProfile(settings.radio)) {
      snprintf(reply, capacity, "Error: Ethernet modem/tuple unsupported"); return true;
    }
    // Editing a running tunnel must not silently evict someone else's lease.
    // Disable the tunnel before changing to a conflicting RF tuple.
    if (settings.enabled && ethernetRadioConflict(settings)) {
      snprintf(reply, capacity, "Error: radio2 reserved/conflicting; set ethernet off first"); return true;
    }
  } else if (!strcmp(command, "set ethernet on")) {
    if (!settings.configured || !settings.radio.freq) {
      snprintf(reply, capacity, "Error: set ethernet.radio first; no automatic frequency"); return true;
    }
    if (!_radio->supportsEthernetTunnel() || ethernetRadioConflict(settings)) {
      snprintf(reply, capacity, "Error: radio2 reserved/conflicting; remove r2-/mt- channels or match saved radio2"); return true;
    }
    settings.enabled = true;
  } else if (!strcmp(command, "set ethernet off")) settings.enabled = false;
  else { snprintf(reply, capacity, "Error: use set ethernet on|off or set ethernet.radio <MHz>,<kHz>,<SF>,<CR>[,<preamble>]"); return true; }
  if (!saveEthernetSettings(settings)) snprintf(reply, capacity, "Error: Ethernet settings not saved (pending TX/storage failure)");
  else snprintf(reply, capacity, "OK - ethernet %s saved; radio2 only; trusted-LAN KISS TCP %u", settings.enabled ? "on" : "off", mesh::ethernet::KissServer::Port);
  return true;
}

void MyMesh::loopEthernetTunnel() {
  const auto* current = _radio->profiles();
  const bool lease_unchanged = _ethernet_active && _ethernet_owned && current
      && current->generation[1] == _ethernet_generation
      && _radio_profiles.secondaryIntentGeneration() == _ethernet_intent_generation
      && current->secondary.mode == mesh::RadioProfileMode::RxTx
      && current->secondary.params == _ethernet_settings.radio
      && _radio_profiles.secondaryRuntimeAvailable();
  const char* blocked = nullptr;
  if (!_ethernet_settings_valid) blocked = "invalid settings";
  else if (!_ethernet_settings.enabled) blocked = "off";
  else if (!_ethernet_settings.configured) blocked = "radio unset";
  else if (!_radio_available || !_radio->supportsEthernetTunnel()) blocked = "modem unavailable";
  else if (ethernetChannelConflict()) blocked = "conflicting r2-/mt- channel";
  else if (_radio_profiles.secondaryTemporary()) blocked = "temporary radio2 owns modem";
  // Validation/timing preview and profile publication happen only on an
  // ownership/configuration edge, never on every RF polling pass.
  if (!blocked && !lease_unchanged) {
    if (_radio_profiles.manualSecondaryConfigured()) {
      auto saved = _radio_profiles.savedSecondaryConfig();
      saved.params.protocol = mesh::RadioProfileProtocol::Ethernet;
      if (saved.mode != mesh::RadioProfileMode::RxTx || saved.params != _ethernet_settings.radio
          || !_radio_profiles.useSecondaryProtocol(mesh::RadioProfileProtocol::Ethernet)) blocked = "saved radio2 conflicts";
    } else {
      mesh::RadioProfileConfig config;
      config.mode = mesh::RadioProfileMode::RxTx; config.params = _ethernet_settings.radio;
      if (!_radio_profiles.requestAutoSecondary(config)) blocked = "radio2 reserved";
    }
  }
  if (blocked) {
    if (_ethernet_active || _ethernet_owned) releaseEthernetProfile();
    else _ethernet_kiss.loop(false);
    _ethernet_status = blocked;
    return;
  }
  const auto* profiles = _radio->profiles();
  if (!profiles || !profiles->enabled() || !profiles->canTransmit(1)
      || profiles->secondary.params.protocol != mesh::RadioProfileProtocol::Ethernet) {
    releaseEthernetProfile(); _ethernet_status = "waiting for radio2"; return;
  }
  if (_ethernet_generation != profiles->generation[1]) _ethernet_kiss.resetPending();
  _ethernet_generation = profiles->generation[1];
  _ethernet_intent_generation = _radio_profiles.secondaryIntentGeneration();
  _ethernet_owned = _ethernet_active = true;
  const auto before = _ethernet_kiss.status().accepted + _ethernet_kiss.status().disconnects;
  _ethernet_kiss.loop(true);
  const auto after = _ethernet_kiss.status().accepted + _ethernet_kiss.status().disconnects;
  if (before != after) discardQueuedForeignRadioFrame(mesh::RadioProfileProtocol::Ethernet);
  _ethernet_status = _ethernet_kiss.status().listening ? "active (RX time-shared)" : "waiting for station WiFi";
  const uint8_t* bytes; size_t length;
  if (!hasForeignRadioFrame() && _ethernet_kiss.peekTransmit(bytes, length)
      && queueForeignRadioFrame(bytes, length, 1, profiles->generation[1], mesh::RadioProfileProtocol::Ethernet))
    _ethernet_kiss.consumeTransmit();
}

bool MyMesh::onEthernetFrame(const uint8_t* bytes, size_t length, uint8_t profile, uint32_t generation) {
  const auto* profiles = _radio->profiles();
  if (!_ethernet_active || !profiles || profile != 1 || generation != profiles->generation[1]
      || profiles->secondary.params.protocol != mesh::RadioProfileProtocol::Ethernet) return false;
  if (!_ethernet_kiss.queueReceive(bytes, length)) return false;
  ++_ethernet_received;
  return true;
}

#if !MESH_ENABLE_MESHTEXT_CHAT
bool MyMesh::onForeignRadioFrame(const uint8_t* bytes, size_t length, uint8_t profile,
                                uint32_t generation, float snr, float rssi) {
  (void)snr; (void)rssi;
  return onEthernetFrame(bytes, length, profile, generation);
}

void MyMesh::onForeignRadioSendComplete(bool success) {
  if (queuedForeignRadioProtocol() != mesh::RadioProfileProtocol::Ethernet) return;
  if (success) ++_ethernet_sent;
  else ++_ethernet_failed;
}
#endif
#endif
