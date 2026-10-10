#include "FleetChannel.h"
#include "FilePresence.h"
#include "FileRead.h"
#include "PersistentStoreFormat.h"
#include <Arduino.h>
#include <stdio.h>
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
#include "AtomicFileWriter.h"
#else
#include "ContactFileTransaction.h"
#endif

namespace mesh {
namespace {
constexpr char Path[] = "/fleet_channel";
constexpr size_t ImageSize = 64;
bool zero(const uint8_t* p, size_t length) {
  uint8_t value = 0;
  while (length--) value |= *p++;
  return value == 0;
}
}

bool FleetChannel::load() {
  if (!fs_) return false;
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  if (!ContactFileTransaction::recover(fs_, Path, filePresence<FILESYSTEM>)) return false;
#endif
  bool present = false;
  if (!filePresence(fs_, Path, present)) return false;
  if (!present) return true;
  auto file = openFileRead(fs_, Path);
  uint8_t image[ImageSize];
  const bool read = file && file.size() == ImageSize
      && file.read(image, ImageSize) == (int)ImageSize;
  file.close();
  if (!read || memcmp(image, "FCS1", 4) != 0 || image[4] > 1
      || !zero(image + 5, 3)
      || storage::readLE32(image + 60) != storage::updateCRC32(0xffffffff, image, 60)) return false;
  if (!zero(image + 8, 16) && !FleetCommand::privateKeyAllowed(image + 8)) return false;
  configured_ = image[4] != 0;
  if (configured_ && zero(image + 8, 16)) return false;
  memcpy(channel_.secret, image + 8, 16);
  Utils::sha256(channel_.hash, sizeof(channel_.hash), channel_.secret, 16);
  memcpy(controller_.pub_key, image + 24, 32);
  controller_set_ = !zero(controller_.pub_key, 32);
  last_sequence_ = storage::readLE32(image + 56);
  return true;
}

bool FleetChannel::save(bool enabled, const uint8_t* key, const uint8_t* controller,
                        uint32_t sequence) {
  if (!healthy_ || !fs_) return false;
  uint8_t image[ImageSize] = {'F', 'C', 'S', '1', uint8_t(enabled)};
  memcpy(image + 8, key, 16);
  memcpy(image + 24, controller, 32);
  storage::writeLE32(image + 56, sequence);
  storage::writeLE32(image + 60, storage::updateCRC32(0xffffffff, image, 60));
#if defined(NRF52_PLATFORM) || defined(STM32_PLATFORM)
  AtomicFileWriter writer(fs_, Path);
#else
  ContactFileTransaction writer(fs_, Path, filePresence<FILESYSTEM>);
#endif
  return writer && writer.write(image, sizeof(image)) == sizeof(image) && writer.commit();
}

bool FleetChannel::handleConfig(const char* command, char* reply, size_t capacity) {
  if (!strcmp(command, "get fleet.channel")) {
    char fingerprint[3];
    Utils::toHex(fingerprint, channel_.hash, 1);
    snprintf(reply, capacity, "> %s,controller=%s,store=%s,hash=%s,last=%lu",
             configured_ ? "on" : "off", controller_set_ ? "set" : "unset",
             healthy_ ? "ok" : "error", fingerprint, (unsigned long)last_sequence_);
  } else if (!strcmp(command, "get fleet.controller")) {
    if (controller_set_) {
      char key[65];
      Utils::toHex(key, controller_.pub_key, 32);
      snprintf(reply, capacity, "> %s", key);
    } else snprintf(reply, capacity, "> unset");
  } else if (!strcmp(command, "get fleet.stats")) {
    snprintf(reply, capacity, "> accepted=%lu rejected=%lu busy=%lu pending=%u ack=%u parts=%u",
             (unsigned long)accepted_, (unsigned long)rejected_, (unsigned long)busy_,
             unsigned(pending_), unsigned(barrier_.waiting()),
             assemblyPartCount());
  } else if (!strncmp(command, "set fleet.channel ", 18)) {
    if (pending_ || barrier_.waiting()) {
      snprintf(reply, capacity, "Err - fleet command pending; retry later"); return true;
    }
    const char* value = command + 18;
    uint8_t key[16] = {};
    const bool enabled = strcmp(value, "off") != 0;
    if (enabled && (strlen(value) != 32 || !Utils::fromHex(key, 16, value)
                    || !FleetCommand::privateKeyAllowed(key))) {
      snprintf(reply, capacity, "Err - use a private random 128-bit key or off"); return true;
    }
    if (!save(enabled, key, controller_.pub_key, last_sequence_)) {
      snprintf(reply, capacity, "Err - fleet settings not saved; store unavailable"); return true;
    }
    configured_ = enabled;
    clearAssembly();
    memset(channel_.secret, 0, sizeof(channel_.secret));
    memcpy(channel_.secret, key, sizeof(key));
    Utils::sha256(channel_.hash, sizeof(channel_.hash), key, sizeof(key));
    snprintf(reply, capacity, "OK - fleet channel %s; publisher signature required",
             enabled ? "on" : "off");
  } else if (!strncmp(command, "set fleet.controller ", 21)) {
    if (pending_ || barrier_.waiting()) {
      snprintf(reply, capacity, "Err - fleet command pending; retry later"); return true;
    }
    const char* value = command + 21;
    uint8_t key[32] = {};
    if (strcmp(value, "off") && (strlen(value) != 64 || !Utils::fromHex(key, 32, value) || zero(key, 32))) {
      snprintf(reply, capacity, "Err - use publisher public key (64 hex) or off"); return true;
    }
    if (!save(configured_, channel_.secret, key, last_sequence_)) {
      snprintf(reply, capacity, "Err - fleet settings not saved; store unavailable"); return true;
    }
    memcpy(controller_.pub_key, key, 32);
    controller_set_ = !zero(key, 32);
    clearAssembly();
    snprintf(reply, capacity, "OK - fleet publisher %s", controller_set_ ? "set" : "off");
  } else return false;
  return true;
}

void FleetChannel::receive(Packet* packet, Mesh& mesh, const TransportKey* scope) {
  if (!healthy_ || !configured_ || !controller_set_ || !packet
      || packet->getPayloadType() != PAYLOAD_TYPE_GRP_DATA
      || packet->payload_len < 1 + CIPHER_MAC_SIZE + CIPHER_BLOCK_SIZE
      || packet->payload_len > sizeof(incoming_) || packet->payload[0] != channel_.hash[0]) return;
  if (pending_ || barrier_.waiting()) { ++busy_; return; }
  // Do no decryption, signature verification, or writes on the receive stack.
  memcpy(incoming_, packet->payload, packet->payload_len);
  incoming_len_ = packet->payload_len;
  rx_profile_ = packet->radio_profile;
  rx_generation_ = packet->radio_generation;
  path_hash_size_ = packet->getPathHashSize();
  reply_scoped_ = packet->hasTransportCodes();
  reply_scope_known_ = scope != nullptr && !scope->isNull();
  if (reply_scope_known_) reply_scope_ = *scope;
  pending_ = true;
  (void)mesh;
}

void FleetChannel::clearAssembly(Assembly& assembly) {
  memset(assembly.data, 0, sizeof(assembly.data));
  assembly.sequence = assembly.deadline = 0;
  assembly.length = 0;
  assembly.parts = 0;
}

void FleetChannel::clearAssembly() {
  for (auto& assembly : assemblies_) clearAssembly(assembly);
}

unsigned FleetChannel::assemblyPartCount() const {
  unsigned count = 0;
  for (const auto& assembly : assemblies_)
    count += unsigned(bool(assembly.parts & 1)) + unsigned(bool(assembly.parts & 2));
  return count;
}

void FleetChannel::serviceAssemblyDeadline() {
  for (auto& assembly : assemblies_) {
    if (assembly.parts && int32_t(uint32_t(millis()) - assembly.deadline) >= 0)
      clearAssembly(assembly);
  }
}

FleetChannel::DecodeResult FleetChannel::decode(Mesh& mesh, FleetCommand::Decoded& command) {
  const uint32_t now_ms = millis();
  if (uint32_t(now_ms - verify_window_) >= 1000) {
    verify_window_ = now_ms; verify_attempts_ = 0;
  }
  uint8_t data[MAX_PACKET_PAYLOAD];
  const int length = Utils::MACThenDecrypt(channel_.secret, data, incoming_ + 1, incoming_len_ - 1);
  if (length < 3 || data[0] != uint8_t(FleetCommand::DataType)
      || data[1] != uint8_t(FleetCommand::DataType >> 8)) return DecodeResult::Rejected;
  const size_t payload_length = data[2];
  const size_t unpadded = 3 + payload_length;
  if (payload_length > FleetCommand::MaxPayloadLength || unpadded > (size_t)length
      || (unpadded + 15) / 16 * 16 != (size_t)length
      || !zero(data + unpadded, length - unpadded)) return DecodeResult::Rejected;
  const uint8_t* envelope = data + 3;
  size_t envelope_length = payload_length;
  Assembly* assembled = nullptr;
  if (payload_length >= 4 && !memcmp(envelope, "FMP1", 4)) {
    FleetCommand::Fragment fragment;
    if (!FleetCommand::parseFragment(envelope, payload_length, fragment)
        || fragment.sequence <= last_sequence_) return DecodeResult::Rejected;
    const uint32_t now = mesh.getRTCClock()->getCurrentTime();
    if (fragment.sequence < FleetCommand::MinEpoch
        || (fragment.sequence > now && fragment.sequence - now > FleetCommand::MaxClockLead)
        || (now > fragment.sequence && now - fragment.sequence > FleetCommand::MaxLifetime))
      return DecodeResult::Rejected;
    Assembly* slot = nullptr;
    for (auto& candidate : assemblies_) {
      if (candidate.parts && candidate.sequence == fragment.sequence) {
        // Conflicting metadata cannot replace a live transfer with this
        // sequence. Its identity remains untrusted until the full signature.
        if (candidate.length != fragment.total_length) return DecodeResult::Rejected;
        slot = &candidate;
        break;
      }
    }
    if (!slot) {
      for (auto& candidate : assemblies_) {
        if (!candidate.parts) { slot = &candidate; break; }
      }
      if (!slot) {
        // Evict the oldest collection by monotonic age, never by an unsigned
        // advertised sequence. A forged now+60 fragment cannot reserve the
        // receiver against a legitimate current command for five minutes.
        slot = &assemblies_[0];
        for (auto& candidate : assemblies_) {
          if (int32_t(candidate.deadline - slot->deadline) < 0) slot = &candidate;
        }
        clearAssembly(*slot);
      }
      slot->sequence = fragment.sequence;
      slot->length = fragment.total_length;
      // Fixed five-minute lifetime: duplicates never extend ownership.
      slot->deadline = now_ms + AssemblyLifetimeMillis;
    }
    const size_t offset = fragment.index * FleetCommand::FragmentDataLength;
    const uint8_t mask = uint8_t(1U << fragment.index);
    if (slot->parts & mask) {
      return !memcmp(slot->data + offset, fragment.data, fragment.length)
          ? DecodeResult::Partial : DecodeResult::Rejected;
    }
    memcpy(slot->data + offset, fragment.data, fragment.length);
    slot->parts |= mask;
    if (slot->parts != 3) return DecodeResult::Partial;
    envelope = slot->data;
    envelope_length = slot->length;
    assembled = slot;
  }
  // The visible channel hash is only a routing hint. Charge the expensive
  // verification budget only at Ed25519 verification, after private-key MAC,
  // framing, replay, target, time and command checks. Keyless junk, recorded
  // old requests and incomplete transfers cannot consume those slots.
  const bool valid = envelope_length >= FleetCommand::MinHeaderSize + FleetCommand::SignatureSize
      && storage::readLE32(envelope + 4) > last_sequence_
      && (!assembled || storage::readLE32(envelope + 4) == assembled->sequence)
      && FleetCommand::decode(controller_, channel_.secret, envelope, envelope_length,
                             mesh.getRTCClock()->getCurrentTime(), mesh.self_id.pub_key, command,
                             matchesFleetRegion, regions_, matchesFleetLocation, hooks_,
                             [](void* context) {
                               auto* receiver = static_cast<FleetChannel*>(context);
                               if (receiver->verify_attempts_ >= 4) return false;
                               ++receiver->verify_attempts_;
                               return true;
                             }, this);
  if (valid) clearAssembly();
  else if (assembled) clearAssembly(*assembled);
  if (!valid) return DecodeResult::Rejected;
  broadcast_ = command.broadcast;
  return DecodeResult::Accepted;
}

bool FleetChannel::acknowledge(Mesh& mesh, const char* name, uint32_t sequence,
                               const char* reply, bool radio_mutation) {
  // Mesh::createGroupDatagram reserves one channel byte and a full block's
  // padding allowance, so at most 168 plaintext bytes fit a 184-byte payload.
  uint8_t data[MAX_PACKET_PAYLOAD - CIPHER_BLOCK_SIZE] = {};
  const uint32_t time = mesh.getRTCClock()->getCurrentTime();
  memcpy(data, &time, sizeof(time));
  data[4] = 0;  // TXT_TYPE_PLAIN
  char identity[13];
  Utils::toHex(identity, mesh.self_id.pub_key, 6);
  // Keep names bounded and remove ':' so ordinary app channel parsers agree.
  char sender[17];
  snprintf(sender, sizeof(sender), "%.16s", name ? name : "node");
  for (char* p = sender; *p; ++p) if (*p == ':') *p = ';';
  const int count = snprintf((char*)data + 5, sizeof(data) - 5,
                            "%s: fleet %lu %s: %s", sender,
                            (unsigned long)sequence, identity, reply);
  if (count < 0) return false;
  const size_t length = count >= int(sizeof(data) - 5) ? sizeof(data) - 6 : size_t(count);
  Packet* packet = mesh.createGroupDatagram(PAYLOAD_TYPE_GRP_TXT, channel_, data, 5 + length);
  if (!packet) return false;
  packet->radio_reply = true;
  packet->flood_retry_policy = FLOOD_RETRY_POLICY_DENY;
  // Acknowledgements have one tracked physical attempt per profile. A later
  // untracked retry must never trigger a completed radio transition twice.
  packet->radio_bound = false;
  if (radio_mutation) barrier_.prepare(packet);
  // Keep short temporary leases useful: their clock started at admission and
  // may not be extended by waiting for a fleet acknowledgement.
  const uint32_t maximum_delay = broadcast_ ? 10000 : 1500;
  const uint32_t delay = mesh.getRNG()->nextInt(500, maximum_delay);
  bool queued = false;
  if (reply_scoped_) {
    if (!reply_scope_known_) {
      mesh.releasePacket(packet);
      if (radio_mutation) barrier_.clear();
      return false;
    }
    uint16_t codes[2] = {reply_scope_.calcTransportCode(packet), 0};
    queued = mesh.sendFlood(packet, codes, delay, path_hash_size_);
  } else queued = mesh.sendFlood(packet, delay, path_hash_size_);
  if (!queued) { if (radio_mutation) barrier_.clear(); return false; }
  if (radio_mutation) {
    barrier_.arm(packet);
    ack_deadline_ = millis() + 300000UL;
  }
  return true;
}

void FleetChannel::finish(RadioProfileCLI& profiles, bool delivered) {
  barrier_.clear(); ack_deadline_ = 0;
  if (ack_profile_mutation_
      && profiles.replyMutationGeneration() == ack_profile_generation_)
    profiles.finishReplyMutation(delivered);
  ack_profile_mutation_ = false;
  if (hooks_) hooks_->finishFleetReplyMutation(delivered);
}
void FleetChannel::complete(Packet* packet, RadioProfileCLI& profiles) {
  if (barrier_.complete(packet) && !barrier_.waiting()) finish(profiles, true);
}
void FleetChannel::fail(Packet* packet, RadioProfileCLI& profiles) {
  if (barrier_.fail(packet)) finish(profiles, false);
  else if (!barrier_.waiting() && barrier_.succeeded()) finish(profiles, true);
}
void FleetChannel::serviceDeadline(Mesh& mesh, RadioProfileCLI& profiles) {
  if (!barrier_.waiting() || int32_t(uint32_t(millis()) - ack_deadline_) < 0) return;
  if (barrier_.contains(mesh.getOutboundInFlight())) mesh.cancelOutboundRadioRetry(mesh.getOutboundInFlight());
  for (int i = mesh._mgr->getOutboundTotal() - 1; i >= 0; --i) {
    if (!barrier_.contains(mesh._mgr->getOutboundByIdx(i))) continue;
    Packet* packet = mesh._mgr->removeOutboundByIdx(i);
    static_cast<Dispatcher&>(mesh).onSendFail(packet);
    mesh.releasePacket(packet);
  }
}
}  // namespace mesh
