#pragma once

#include <Mesh.h>
#include <helpers/IdentityStore.h>
#include <helpers/RadioProfileCLI.h>
#include <helpers/TempRadioReplyBarrier.h>
#include <helpers/FleetCommand.h>
#include <helpers/TransportKeyStore.h>
#include <helpers/RegionMap.h>
#include <helpers/RegionNameUtils.h>

namespace mesh {

// Role-owned mutations must complete after the exact fleet reply, just like
// shared secondary-profile changes. Completion hooks stage work for loop().
struct FleetReplyHooks {
  virtual void beginFleetCommand() {}
  virtual void endFleetCommand() {}
  virtual bool hasFleetReplyMutation() const { return false; }
  virtual void finishFleetReplyMutation(bool) {}
  virtual bool getFleetLocation(int32_t&, int32_t&) const { return false; }
};

inline bool matchesFleetLocation(void* context, int32_t latitude_e6,
                                 int32_t longitude_e6, uint32_t radius_meters) {
  auto* hooks = static_cast<FleetReplyHooks*>(context);
  int32_t local_latitude_e6 = 0, local_longitude_e6 = 0;
  if (!hooks || !hooks->getFleetLocation(local_latitude_e6, local_longitude_e6)
      || (!local_latitude_e6 && !local_longitude_e6)) return false;
  return FleetCommand::withinRadius(latitude_e6, longitude_e6, radius_meters,
                                    local_latitude_e6, local_longitude_e6);
}

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

// Fleet selectors read the current region map without changing routing.
// A configured-list selector includes denied regions too; home selectors
// follow only the selected home's parent chain, never the default TX scope.
inline bool matchesFleetRegion(void* context, FleetCommand::RegionTarget kind,
                               const char* name, size_t length) {
  auto* regions = static_cast<RegionMap*>(context);
  if (!regions || !name || !length || length > FleetCommand::MaxRegionNameLength) return false;
  const auto matches = [name, length](const RegionEntry* entry) {
    if (!entry || entry->isWildcard()) return false;
    const char* canonical = RegionNameUtils::canonical(entry->name);
    const size_t available = sizeof(entry->name) - size_t(canonical - entry->name);
    const size_t actual = strnlen(canonical, available);
    return actual < available && actual == length && !memcmp(canonical, name, length);
  };
  const int count = regions->getCount();
  if (count < 0 || count > MAX_REGION_ENTRIES) return false;
  if (kind == FleetCommand::RegionTarget::Configured) {
    for (int i = 0; i < count; ++i) {
      if (matches(regions->getByIdx(i))) return true;
    }
  } else if (kind == FleetCommand::RegionTarget::Home) {
    const RegionEntry* entry = regions->getHomeRegion();
    // Bound traversal even if a damaged in-memory map has a parent cycle.
    for (int depth = 0; entry && !entry->isWildcard() && depth < count; ++depth) {
      if (matches(entry)) return true;
      entry = regions->findById(entry->parent);
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
  RegionMap* regions_;
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
  // Fragment metadata has only channel-key authentication, not the
  // publisher's signature. Two bounded slots avoid making an unsigned future
  // sequence authoritative over every other command's assembly.
  struct Assembly {
    uint8_t data[FleetCommand::MaxEnvelopeLength]{};
    uint32_t sequence = 0, deadline = 0;
    uint16_t length = 0;
    uint8_t parts = 0;
  };
  static constexpr uint32_t AssemblyLifetimeMillis = 300000;
  static_assert(sizeof(Assembly) <= FleetCommand::MaxEnvelopeLength + 12,
                "fleet fragment slots must stay bounded");
  Assembly assemblies_[2]{};
  uint8_t incoming_len_ = 0, rx_profile_ = 0, path_hash_size_ = 1;
  uint32_t rx_generation_ = 0;
  TransportKey reply_scope_{};
  bool reply_scoped_ = false, reply_scope_known_ = false;
  TempRadioReplyBarrier barrier_;

  bool load();
  bool save(bool enabled, const uint8_t* key, const uint8_t* controller,
            uint32_t sequence);
  enum class DecodeResult { Rejected, Partial, Accepted };
  DecodeResult decode(Mesh& mesh, FleetCommand::Decoded& command);
  void clearAssembly();
  void clearAssembly(Assembly& assembly);
  unsigned assemblyPartCount() const;
  void serviceAssemblyDeadline();
  bool acknowledge(Mesh& mesh, const char* name, uint32_t sequence,
                   const char* reply, bool radio_mutation);
  void finish(RadioProfileCLI& profiles, bool delivered);
  void serviceDeadline(Mesh& mesh, RadioProfileCLI& profiles);

 public:
  explicit FleetChannel(FILESYSTEM* fs, FleetReplyHooks* hooks = nullptr,
                        RegionMap* regions = nullptr)
      : fs_(fs), hooks_(hooks), regions_(regions) { healthy_ = load(); }
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
    serviceAssemblyDeadline();
    if (!pending_) return;
    pending_ = false;
    if (barrier_.waiting() || profiles.hasReplyMutation()
        || (hooks_ && hooks_->hasFleetReplyMutation())) { ++busy_; return; }
    FleetCommand::Decoded command;
    const DecodeResult decoded = decode(mesh, command);
    if (decoded == DecodeResult::Partial) return;
    if (decoded != DecodeResult::Accepted) { ++rejected_; return; }
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
