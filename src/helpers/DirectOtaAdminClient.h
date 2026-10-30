#pragma once

#include <Mesh.h>
#include "TempRadioReplyBarrier.h"
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {

// One USB-owned, RAM-only admin session. The selected peer is overlaid on the
// repeater's decrypt lookup, never added to its incoming-client ACL.
class DirectOtaAdminClient {
public:
  static constexpr int PEER_INDEX = -2;
  static constexpr size_t MAX_PEERS = 8;
  static constexpr size_t RESULT_BYTES = 160;
  static constexpr size_t RESULT_CHUNK = 48;
  static constexpr uint32_t REQUEST_MS = 30000;
  static constexpr uint32_t SESSION_MS = 300000;
  struct Peer { Identity id; int16_t rssi; int8_t snr; uint8_t type; uint32_t heard; };

private:
  enum Phase : uint8_t { Idle, Discover, Login, ReturnPath, Proof, Command, AclCommand };
  Phase phase_ = Idle;
  Identity target_;
  uint8_t secret_[PUB_KEY_SIZE] = {};
  Peer peers_[MAX_PEERS];
  uint8_t peers_count_ = 0;
  bool session_ = false, authenticated_ = false, error_ = false;
  uint32_t job_ = 0, deadline_ = 0, session_until_ = 0, discover_tag_ = 0;
  uint32_t request_ms_ = REQUEST_MS;
  uint8_t radio_profile_ = 0;
  // Discovery callbacks have no Mesh argument; retain the owning radio so all
  // entry points validate the same live configuration and generation.
  Radio* profile_radio_ = nullptr;
  uint32_t radio_generation_ = 0;
  bool radio_snapshot_ = false;
  uint32_t last_peer_timestamp_ = 0;
  uint32_t proof_retry_at_ = 0;
  uint8_t proof_attempts_ = 0;
  uint8_t correlation_ = 0;
  char prefix_[4] = {};
  uint8_t result_[RESULT_BYTES] = {};
  size_t result_length_ = 0;
  TempRadioReplyBarrier return_path_;

  static bool expired(uint32_t now, uint32_t deadline) {
    return static_cast<int32_t>(now - deadline) >= 0;
  }
  static void erase(void* buffer, size_t size) {
    volatile uint8_t* p = static_cast<volatile uint8_t*>(buffer);
    while (size--) *p++ = 0;
  }
  void clearSession() {
    return_path_.clear();
    erase(secret_, sizeof(secret_));
    erase(target_.pub_key, sizeof(target_.pub_key));
    session_ = authenticated_ = false;
    last_peer_timestamp_ = 0;
  }
  bool radioEligible(uint8_t profile) const {
    const auto* profiles = profile_radio_ ? profile_radio_->profiles() : nullptr;
    if (!profiles) return profile == 0;
    return profiles->params(profile).protocol == RadioProfileProtocol::MeshCore
        && profiles->canTransmit(profile)
        && (profile == 0 || profiles->enabled());
  }
  uint32_t radioGeneration() const {
    const auto* profiles = profile_radio_ ? profile_radio_->profiles() : nullptr;
    return profiles ? profiles->generation[radio_profile_] : 0;
  }
  void snapshotRadio() {
    radio_generation_ = radioGeneration();
    radio_snapshot_ = true;
  }
  uint8_t explicitTxRadio() const { return radio_profile_ ? RADIO_TX_SECONDARY : RADIO_TX_PRIMARY; }
  void bindRadio(Packet* packet) const {
    if (!packet) return;
    packet->radio_profile = packet->radio_origin = radio_profile_;
    packet->radio_generation = packet->radio_origin_generation = radio_generation_;
    packet->radio_bound = true;
    packet->tx_radio = explicitTxRadio();
  }
  bool matchesRadio(const Packet* packet) const {
    return packet && radio_snapshot_ && packet->radio_profile == radio_profile_
        && packet->radio_generation == radio_generation_;
  }
  void finish(const char* text, bool error = false) {
    return_path_.clear();
    phase_ = Idle;
    error_ = error;
    result_length_ = strlen(text);
    if (result_length_ > sizeof(result_)) result_length_ = sizeof(result_);
    memcpy(result_, text, result_length_);
  }
  void begin(Phase phase, uint32_t now, uint32_t duration) {
    if (++job_ == 0) ++job_;
    phase_ = phase;
    deadline_ = now + duration;
    error_ = false;
    erase(result_, sizeof(result_));
    result_length_ = 0;
  }
  void begin(Phase phase, uint32_t now) { begin(phase, now, request_ms_); }
  void queued(char* reply, size_t capacity) const {
    snprintf(reply, capacity, "OTApeer job=%08lX state=pending", (unsigned long)job_);
  }
  bool queueCommand(Mesh& mesh, const char* command, uint32_t now) {
    const size_t length = strlen(command);
    // Preserve room for timestamp, type, reflected prefix and AES padding.
    if (length == 0 || length > 154) return false;
    uint8_t plain[5 + 3 + 154] = {};
    const uint32_t timestamp = mesh.getRTCClock()->getCurrentTimeUnique();
    memcpy(plain, &timestamp, 4);
    plain[4] = 3 << 2; // TXT_TYPE_CLI_COMMAND
    snprintf(prefix_, sizeof(prefix_), "%02X|", (unsigned)++correlation_);
    memcpy(plain + 5, prefix_, 3);
    memcpy(plain + 8, command, length);
    Packet* packet = mesh.createDatagram(PAYLOAD_TYPE_TXT_MSG, target_, secret_, plain, 8 + length);
    erase(plain, sizeof(plain));
    bindRadio(packet);
    session_until_ = now + SESSION_MS;
    return packet && mesh.sendDirect(packet, nullptr, 0);
  }
  bool queueReturnPath(Mesh& mesh) {
    const uint8_t empty_path[1] = {};
    Packet* packet = mesh.createPathReturn(target_, secret_, empty_path, 0, 0, nullptr, 0);
    if (!packet) return false;
    bindRadio(packet);
    return_path_.prepare(packet);
    if (!mesh.sendDirect(packet, nullptr, 0)) { return_path_.clear(); return false; }
    return_path_.arm(packet);
    ++proof_attempts_;
    phase_ = ReturnPath;
    return true;
  }
  static bool parseKey(const char* text, Identity& id, const char*& rest) {
    if (strlen(text) < PUB_KEY_SIZE * 2) return false;
    char hex[PUB_KEY_SIZE * 2 + 1];
    memcpy(hex, text, PUB_KEY_SIZE * 2);
    hex[PUB_KEY_SIZE * 2] = 0;
    if (!Utils::fromHex(id.pub_key, PUB_KEY_SIZE, hex)) return false;
    rest = text + PUB_KEY_SIZE * 2;
    return *rest == 0 || *rest == ' ';
  }
  static bool offsetValue(const char* text, size_t& value) {
    value = 0;
    if (!*text) return true;
    for (; *text; ++text) {
      if (*text < '0' || *text > '9' || value > RESULT_BYTES) return false;
      value = value * 10 + static_cast<unsigned>(*text - '0');
    }
    return value <= RESULT_BYTES;
  }

public:
  ~DirectOtaAdminClient() { clearSession(); erase(result_, sizeof(result_)); }
  void reset() {
    // The USB owner's RAM-only timeout and radio choice survive closing.
    clearSession(); erase(result_, sizeof(result_));
    result_length_ = 0; phase_ = Idle; error_ = false; peers_count_ = 0; radio_snapshot_ = false;
  }
  static bool isCommand(const char* text) {
    return text && strncmp(text, "ota.peer", 8) == 0 && (text[8] == 0 || text[8] == ' ');
  }
  // Called before console echo, while the password is still arriving. Once
  // true the caller keeps masking until the complete line (even on overflow).
  static bool sensitiveInput(const char* text) {
    if (strncmp(text, "ota.peer login ", 15) == 0) return strchr(text + 15, ' ') != nullptr;
    return strncmp(text, "ota.peer login.repeater ", 24) == 0 && strchr(text + 24, ' ') != nullptr;
  }
  static void clearInput(char* text, size_t capacity) { erase(text, capacity); }
  bool hasSession() const { return session_; }
  bool awaitingPeer() const { return session_ && (phase_ == Login || phase_ == ReturnPath || phase_ == Proof || phase_ == Command || phase_ == AclCommand); }
  bool matchesHash(const uint8_t* hash) const { return awaitingPeer() && target_.isHashMatch(hash); }
  bool matchesTarget(const Identity& id) const { return awaitingPeer() && target_.matches(id); }
  void sharedSecret(uint8_t* destination) const { memcpy(destination, secret_, PUB_KEY_SIZE); }
  void tick(uint32_t now) {
    if (radio_snapshot_ && (!radioEligible(radio_profile_) || radioGeneration() != radio_generation_)) {
      clearSession(); peers_count_ = 0; radio_snapshot_ = false;
      if (phase_ != Idle) finish("radio profile changed", true);
    }
    if (phase_ != Idle && expired(now, deadline_)) {
      if (phase_ == Discover) finish("discovery complete");
      else {
        if (phase_ == Login || phase_ == ReturnPath || phase_ == Proof || phase_ == AclCommand) clearSession();
        finish("request timed out; result is unknown", true);
      }
    }
    if (session_ && expired(now, session_until_)) {
      clearSession();
      if (phase_ != Idle) finish("admin session expired", true);
    }
  }
  void onSendComplete(const Packet* packet) { return_path_.complete(packet); }
  void onRadioProfileCopyQueued(const Packet* packet, const Packet* original) {
    return_path_.trackCopy(original, packet);
  }
  void onSendFail(const Packet* packet) { return_path_.fail(packet); }
  void service(Mesh& mesh, uint32_t now) {
    profile_radio_ = mesh.getProfileRadio();
    tick(now);
    if (phase_ == Proof && expired(now, proof_retry_at_) && proof_attempts_ < 3) {
      if (!queueReturnPath(mesh)) { clearSession(); finish("login return path queue failed", true); }
      return;
    }
    // Only the owned PATH and its admitted copies gate the proof. Unrelated
    // future traffic must not starve login on a busy repeater. Queue the proof
    // here, after Dispatcher callbacks have drained, never from those hooks.
    if (phase_ == ReturnPath && !return_path_.waiting()) {
      if (!return_path_.succeeded()) {
        clearSession(); finish("login return path transmission failed", true); return;
      }
      return_path_.clear();
      phase_ = Proof;
      if (!queueCommand(mesh, "get public.key", now)) { clearSession(); finish("login proof queue failed", true); }
      else proof_retry_at_ = now + 8000;
    }
  }
  void handleCommand(Mesh& mesh, const char* command, char* reply, size_t capacity, uint32_t now) {
    profile_radio_ = mesh.getProfileRadio();
    tick(now);
    const char* args = command + 8;
    while (*args == ' ') ++args;
    if (!strcmp(args, "capabilities")) {
      strcpy(reply, "OTApeer known_repeater=1");
      return;
    }
    if (!strcmp(args, "status") || !*args) {
      snprintf(reply, capacity, "OTApeer v=1 session=%u busy=%u peers=%u", authenticated_, phase_ != Idle, peers_count_);
      return;
    }
    if (!strncmp(args, "radio", 5) && (args[5] == 0 || args[5] == ' ')) {
      const char* value = args + 5;
      while (*value == ' ') ++value;
      if (*value) {
        if (phase_ != Idle) { strcpy(reply, "ERR peer request pending"); return; }
        uint8_t profile;
        if (!strcmp(value, "radio")) profile = 0;
        else if (!strcmp(value, "radio2")) profile = 1;
        else { strcpy(reply, "ERR peer radio must be radio or radio2"); return; }
        if (!radioEligible(profile)) { strcpy(reply, "ERR peer radio requires enabled MeshCore RXTX"); return; }
        if (profile != radio_profile_) { reset(); radio_profile_ = profile; }
      }
      snprintf(reply, capacity, "OTApeer radio=%s", radio_profile_ ? "radio2" : "radio");
      return;
    }
    if (!strncmp(args, "timeout", 7) && (args[7] == 0 || args[7] == ' ')) {
      const char* value = args + 7;
      while (*value == ' ') ++value;
      if (*value) {
        if (phase_ != Idle) { strcpy(reply, "ERR peer request pending"); return; }
        uint32_t seconds = 0;
        for (const char* p = value; *p; ++p) {
          if (*p < '0' || *p > '9' || seconds > 30 || seconds * 10 + (*p - '0') > 300) {
            strcpy(reply, "ERR peer timeout 5..300 seconds"); return;
          }
          seconds = seconds * 10 + (*p - '0');
        }
        if (seconds < 5) { strcpy(reply, "ERR peer timeout 5..300 seconds"); return; }
        request_ms_ = seconds * 1000;
      }
      snprintf(reply, capacity, "OTApeer timeout=%lus", (unsigned long)(request_ms_ / 1000));
      return;
    }
    if (!strcmp(args, "close")) {
      reset();
      strcpy(reply, "OTApeer closed");
      return;
    }
    if (!strncmp(args, "result", 6) && (args[6] == 0 || args[6] == ' ')) {
      const char* rest = args + 6;
      while (*rest == ' ') ++rest;
      size_t offset;
      if (!offsetValue(rest, offset) || offset > result_length_) { strcpy(reply, "ERR peer result offset"); return; }
      size_t count = result_length_ - offset;
      if (count > RESULT_CHUNK) count = RESULT_CHUNK;
      char hex[RESULT_CHUNK * 2 + 1];
      if (count) Utils::toHex(hex, result_ + offset, count);
      else strcpy(hex, "-");
      snprintf(reply, capacity, "OTApeer job=%08lX state=%s bytes=%u off=%u data=%s", (unsigned long)job_,
          phase_ != Idle ? "pending" : (error_ ? "error" : "ok"), (unsigned)result_length_, (unsigned)offset, hex);
      return;
    }
    if (!strncmp(args, "peers", 5) && (args[5] == 0 || args[5] == ' ')) {
      const char* rest = args + 5;
      while (*rest == ' ') ++rest;
      size_t index;
      if (!offsetValue(rest, index) || index >= peers_count_) { strcpy(reply, "ERR peer not found"); return; }
      char hex[PUB_KEY_SIZE * 2 + 1]; Utils::toHex(hex, peers_[index].id.pub_key, PUB_KEY_SIZE);
      snprintf(reply, capacity, "OTApeer peer=%u key=%s type=%u rssi=%d snr=%d", (unsigned)index, hex,
          (unsigned)peers_[index].type, (int)peers_[index].rssi, (int)peers_[index].snr);
      return;
    }
    if (phase_ != Idle) { strcpy(reply, "ERR peer request pending"); return; }
    if (!strcmp(args, "discover")) {
      if (!radioEligible(radio_profile_)) { strcpy(reply, "ERR peer radio requires enabled MeshCore RXTX"); return; }
      clearSession(); peers_count_ = 0;
      snapshotRadio();
      begin(Discover, now, 6000);
      mesh.getRNG()->random(reinterpret_cast<uint8_t*>(&discover_tag_), 4);
      if (!discover_tag_) discover_tag_ = 1;
      uint8_t data[6] = {0x80, (1 << 2) | (1 << 3)};
      memcpy(data + 2, &discover_tag_, 4);
      Packet* packet = mesh.createControlData(data, sizeof(data));
      bindRadio(packet);
      if (!packet || !mesh.sendDirect(packet, nullptr, 0)) finish("discovery queue failed", true);
      queued(reply, capacity);
      return;
    }
    // One explicitly requested command using the target's existing ACL. This
    // skips login and return-path changes and never creates a proven session.
    if (!strncmp(args, "cmd.acl ", 8)) {
      Identity peer; const char* rest;
      if (!parseKey(args + 8, peer, rest) || *rest != ' ' || !*++rest) { strcpy(reply, "ERR peer key or arguments"); return; }
      if (peer.matches(mesh.self_id)) { strcpy(reply, "ERR peer cannot be this repeater"); return; }
      for (const char* p = rest; *p; ++p) if (static_cast<uint8_t>(*p) < 32 || *p == 127) { strcpy(reply, "ERR peer control character"); return; }
      if (strlen(rest) > 154) { strcpy(reply, "ERR peer command too long"); return; }
      if (!radioEligible(radio_profile_)) { strcpy(reply, "ERR peer radio requires enabled MeshCore RXTX"); return; }
      clearSession(); target_ = peer;
      snapshotRadio();
      mesh.self_id.calcSharedSecret(secret_, target_);
      uint8_t nonzero = 0;
      for (size_t i = 0; i < sizeof(secret_); ++i) nonzero |= secret_[i];
      if (!nonzero) { clearSession(); strcpy(reply, "ERR peer invalid public key"); return; }
      session_ = true; session_until_ = now + SESSION_MS;
      mesh.getRNG()->random(&correlation_, 1);
      begin(AclCommand, now);
      if (!queueCommand(mesh, rest, now)) { clearSession(); finish("ACL command queue failed", true); }
      queued(reply, capacity);
      return;
    }
    // Explicitly use an existing target ACL grant. There is no anonymous
    // password login: the authenticated empty PATH establishes the return
    // route, then the ordinary full-key proof must succeed before commands.
    if (!strncmp(args, "acl ", 4)) {
      Identity peer; const char* rest;
      if (!parseKey(args + 4, peer, rest) || *rest) { strcpy(reply, "ERR peer key or arguments"); return; }
      if (peer.matches(mesh.self_id)) { strcpy(reply, "ERR peer cannot be this repeater"); return; }
      if (!radioEligible(radio_profile_)) { strcpy(reply, "ERR peer radio requires enabled MeshCore RXTX"); return; }
      clearSession(); target_ = peer;
      snapshotRadio();
      mesh.self_id.calcSharedSecret(secret_, target_);
      uint8_t nonzero = 0;
      for (size_t i = 0; i < sizeof(secret_); ++i) nonzero |= secret_[i];
      if (!nonzero) { clearSession(); strcpy(reply, "ERR peer invalid public key"); return; }
      session_ = true; session_until_ = now + SESSION_MS;
      mesh.getRNG()->random(&correlation_, 1);
      proof_attempts_ = 0;
      begin(ReturnPath, now);
      if (!queueReturnPath(mesh)) { clearSession(); finish("login return path queue failed", true); }
      queued(reply, capacity);
      return;
    }
    // The explicit repeater form lets an operator use a complete known key
    // when firmware does not implement discovery. It only chooses the login
    // layout; ECDH, admin permission and the full-key proof still authenticate
    // the peer. Ordinary login keeps discovery-based repeater/room selection.
    const bool known_repeater = !strncmp(args, "login.repeater ", 15);
    const bool login = known_repeater || !strncmp(args, "login ", 6);
    const bool cli = !strncmp(args, "cmd ", 4);
    if (login || cli) {
      Identity peer; const char* rest;
      if (!parseKey(args + (known_repeater ? 15 : (login ? 6 : 4)), peer, rest) || *rest != ' ') { strcpy(reply, "ERR peer key or arguments"); return; }
      ++rest;
      if (peer.matches(mesh.self_id)) { strcpy(reply, "ERR peer cannot be this repeater"); return; }
      for (const char* p = rest; *p; ++p) if (static_cast<uint8_t>(*p) < 32 || *p == 127) { strcpy(reply, "ERR peer control character"); return; }
      if (login) {
        const size_t length = strlen(rest);
        if (length > 63) { strcpy(reply, "ERR peer password too long"); return; }
        const Peer* discovered = nullptr;
        for (unsigned i = 0; i < peers_count_; ++i) {
          if (peers_[i].id.matches(peer) && !expired(now, peers_[i].heard + 60000)) {
            discovered = &peers_[i]; break;
          }
        }
        if (!known_repeater && !discovered) { strcpy(reply, "ERR peer needs fresh discovery"); return; }
        if (!radioEligible(radio_profile_)) { strcpy(reply, "ERR peer radio requires enabled MeshCore RXTX"); return; }
        const size_t password_offset = !known_repeater && discovered->type == 3 ? 8 : 4;
        clearSession(); target_ = peer;
        snapshotRadio();
        mesh.self_id.calcSharedSecret(secret_, target_);
        uint8_t nonzero = 0;
        for (size_t i = 0; i < sizeof(secret_); ++i) nonzero |= secret_[i];
        if (!nonzero) { clearSession(); strcpy(reply, "ERR peer invalid public key"); return; }
        session_ = true; session_until_ = now + SESSION_MS;
        mesh.getRNG()->random(&correlation_, 1);
        begin(Login, now);
        uint8_t data[8 + 63] = {};
        const uint32_t timestamp = mesh.getRTCClock()->getCurrentTimeUnique();
        // Rooms prefix the password with a sync-since cursor; zero requests no
        // destructive cursor change and the server preserves its saved value.
        memcpy(data, &timestamp, 4); memcpy(data + password_offset, rest, length);
        Packet* packet = mesh.createAnonDatagram(PAYLOAD_TYPE_ANON_REQ, mesh.self_id, target_, secret_, data, password_offset + length);
        erase(data, sizeof(data));
        bindRadio(packet);
        if (!packet || !mesh.sendDirect(packet, nullptr, 0)) { clearSession(); finish("login queue failed", true); }
      } else {
        if (!authenticated_ || !target_.matches(peer)) { strcpy(reply, "ERR peer login required"); return; }
        begin(Command, now);
        if (!queueCommand(mesh, rest, now)) finish("command queue failed or command too long", true);
      }
      queued(reply, capacity);
      return;
    }
    snprintf(reply, capacity, "ERR use ota.peer status|radio [radio|radio2]|timeout [s]|acl KEY|discover|peers [n]|login[.repeater] KEY PASS|cmd[.acl] KEY COMMAND|result [off]|close");
  }

  void onDiscovery(const Packet* packet, uint32_t now) {
    tick(now);
    if (phase_ != Discover || !matchesRadio(packet) || !packet->isRouteDirect() || packet->getPathHashCount() != 0
        || packet->payload_len < 6 + PUB_KEY_SIZE
        || (packet->payload[0] != 0x92 && packet->payload[0] != 0x93)) return;
    uint32_t tag; memcpy(&tag, packet->payload + 2, 4);
    if (tag != discover_tag_) return;
    Identity id(packet->payload + 6);
    for (unsigned i = 0; i < peers_count_; ++i) if (peers_[i].id.matches(id)) {
      // A changing advertised role cannot silently change the login layout.
      if (peers_[i].type == (packet->payload[0] & 15)) peers_[i].heard = now;
      return;
    }
    if (peers_count_ == MAX_PEERS) return;
    Peer& peer = peers_[peers_count_++]; peer.id = id; peer.rssi = packet->_rssi; peer.snr = packet->_snr;
    peer.type = packet->payload[0] & 15; peer.heard = now;
  }

  void onData(Mesh& mesh, const Packet* packet, uint8_t type, const uint8_t* data, size_t length, uint32_t now) {
    tick(now);
    if (!session_ || !matchesRadio(packet) || packet->getPathHashCount() != 0 || !data || length < 5) return;
    // Older servers flood their first response before knowing a return path.
    // Only that authenticated login response may use a zero-hop flood envelope.
    if (!packet->isRouteDirect() && !(phase_ == Login && type == PAYLOAD_TYPE_RESPONSE && packet->isRouteFlood())) return;
    uint32_t timestamp; memcpy(&timestamp, data, 4);
    if (timestamp <= last_peer_timestamp_) return;
    if (phase_ == Login && type == PAYLOAD_TYPE_RESPONSE) {
      // Modern ACL role 3 is full admin; region/filter managers cannot update.
      if (length < 13 || data[4] != 0 || (data[7] & 7) != 3) { clearSession(); finish("admin login rejected", true); return; }
      last_peer_timestamp_ = timestamp;
      proof_attempts_ = 0;
      if (!queueReturnPath(mesh)) { clearSession(); finish("login return path queue failed", true); }
      return;
    }
    if ((phase_ != Proof && phase_ != Command && phase_ != AclCommand) || type != PAYLOAD_TYPE_TXT_MSG
        || (data[4] >> 2) != 1 || length < 8 || memcmp(data + 5, prefix_, 3)) return;
    const uint8_t* text = data + 8;
    size_t text_len = length - 8;
    const void* terminator = memchr(text, 0, text_len);
    if (terminator) text_len = static_cast<const uint8_t*>(terminator) - text;
    if (phase_ == Proof) {
      while (text_len && (*text == ' ' || *text == '>')) { ++text; --text_len; }
      char hex[PUB_KEY_SIZE * 2 + 1];
      if (text_len != PUB_KEY_SIZE * 2) { clearSession(); finish("invalid login identity proof", true); return; }
      memcpy(hex, text, text_len); hex[text_len] = 0;
      Identity proof; const char* rest;
      if (!parseKey(hex, proof, rest) || !proof.matches(target_)) { clearSession(); finish("login identity mismatch", true); return; }
      authenticated_ = true;
      finish("authenticated");
    } else {
      result_length_ = text_len > sizeof(result_) ? sizeof(result_) : text_len;
      memcpy(result_, text, result_length_);
      if (phase_ == AclCommand) {
        clearSession(); phase_ = Idle; error_ = false;
        return;
      }
      phase_ = Idle; error_ = false;
    }
    last_peer_timestamp_ = timestamp;
    session_until_ = now + SESSION_MS;
  }
};

} // namespace mesh
