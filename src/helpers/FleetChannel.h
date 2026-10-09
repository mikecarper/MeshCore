#pragma once

#include <Mesh.h>
#include <helpers/IdentityStore.h>
#include <helpers/RadioProfileCLI.h>
#include <helpers/TempRadioReplyBarrier.h>
#include <helpers/FleetCommand.h>
#include <helpers/TransportKeyStore.h>

namespace mesh {

// Role-owned mutations must complete after the exact fleet reply, just like
// shared secondary-profile changes. Completion hooks stage work for loop().
struct FleetReplyHooks {
  virtual void beginFleetCommand() {}
  virtual void endFleetCommand() {}
  virtual bool hasFleetReplyMutation() const { return false; }
  virtual void finishFleetReplyMutation(bool) {}
};

// Scope codes authenticate the original packet's payload. A new reply needs
// fresh codes from the exact matching region key, including rotated keys.
template <typename Regions, typename Region>
bool captureFleetReplyScope(Regions& regions, const Region* region,
                           const Packet* packet, TransportKey& scope) {
  if (!packet || !packet->hasTransportCodes() || !region || region->isWildcard()) return false;
  TransportKey candidates[MAX_TKS_ENTRIES];
  const int count = regions.getTransportKeysFor(*region, candidates, MAX_TKS_ENTRIES);
  for (int i = 0; i < count && i < MAX_TKS_ENTRIES; ++i) {
    const uint16_t code = candidates[i].calcTransportCode(packet);
    if (code == packet->transport_codes[0] || code == packet->transport_codes[1]) {
      scope = candidates[i]; return true;
    }
  }
  return false;
}

// Optional infrastructure-only receiver. The publisher's private key is never
// enrolled here. The ordinary channel key provides privacy; signatures grant
// the deliberately narrower fleet capability.
class FleetChannel {
  FILESYSTEM* fs_;
  FleetReplyHooks* hooks_;
  GroupChannel channel_{};
  Identity controller_;
  uint32_t last_sequence_ = 0;
  uint32_t ack_deadline_ = 0;
  uint32_t ack_profile_generation_ = 0;
  uint32_t verify_window_ = 0;
  uint32_t accepted_ = 0, rejected_ = 0, busy_ = 0;
  uint8_t verify_attempts_ = 0;
  bool configured_ = false, controller_set_ = false, healthy_ = false;
  bool pending_ = false;
  bool broadcast_ = false;
  bool ack_profile_mutation_ = false;
  uint8_t incoming_[MAX_PACKET_PAYLOAD]{};
  uint8_t incoming_len_ = 0, rx_profile_ = 0, path_hash_size_ = 1;
  uint32_t rx_generation_ = 0;
  TransportKey reply_scope_{};
  bool reply_scoped_ = false, reply_scope_known_ = false;
  TempRadioReplyBarrier barrier_;

  bool load();
  bool save(bool enabled, const uint8_t* key, const uint8_t* controller,
            uint32_t sequence);
  bool decode(Mesh& mesh, FleetCommand::Decoded& command);
  bool acknowledge(Mesh& mesh, const char* name, uint32_t sequence,
                   const char* reply, bool radio_mutation);
  void finish(RadioProfileCLI& profiles, bool delivered);
  void serviceDeadline(Mesh& mesh, RadioProfileCLI& profiles);

 public:
  explicit FleetChannel(FILESYSTEM* fs, FleetReplyHooks* hooks = nullptr)
      : fs_(fs), hooks_(hooks) { healthy_ = load(); }
  bool handleConfig(const char* command, char* reply, size_t capacity);
  void receive(Packet* packet, Mesh& mesh, const TransportKey* scope = nullptr);
  void complete(Packet* packet, RadioProfileCLI& profiles);
  void fail(Packet* packet, RadioProfileCLI& profiles);
  void copy(Packet* packet, const Packet* original) { barrier_.trackCopy(original, packet); }
  bool waiting() const { return barrier_.waiting(); }

  template <typename Handler>
  void service(Mesh& mesh, RadioProfileCLI& profiles, const char* name,
               Handler handle) {
    serviceDeadline(mesh, profiles);
    if (!pending_) return;
    pending_ = false;
    if (barrier_.waiting() || profiles.hasReplyMutation()
        || (hooks_ && hooks_->hasFleetReplyMutation())) { ++busy_; return; }
    FleetCommand::Decoded command;
    if (!decode(mesh, command)) { ++rejected_; return; }
    // Reserve before dispatch, including failed commands. A lost reply, reboot,
    // or retry can never repeat a mutation. A storage failure grants no control.
    if (!save(configured_, channel_.secret, controller_.pub_key, command.sequence)) {
      healthy_ = false;
      ++rejected_;
      return;
    }
    last_sequence_ = command.sequence;
    ++accepted_;
    Dispatcher::ReceiveProfileScope receive_scope(mesh, rx_profile_, rx_generation_);
    char reply[160] = {};
    const uint32_t generation = profiles.replyMutationGeneration();
    if (hooks_) hooks_->beginFleetCommand();
    profiles.beginReplyCommand();
    handle(command.sequence, command.command, reply);
    profiles.endReplyCommand();
    if (hooks_) hooks_->endFleetCommand();
    // Only this acknowledgement may release the secondary mutation it staged.
    // Direct recovery can cancel it and accept a newer independent command.
    ack_profile_generation_ = profiles.replyMutationGeneration();
    ack_profile_mutation_ = profiles.hasReplyMutation()
        && ack_profile_generation_ != generation;
    const bool changed = ack_profile_mutation_
        || (hooks_ && hooks_->hasFleetReplyMutation());
    if (!acknowledge(mesh, name, command.sequence, reply, changed) && changed) {
      finish(profiles, false);
    }
  }
};

}  // namespace mesh
