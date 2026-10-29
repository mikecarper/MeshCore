#pragma once

#include <Mesh.h>
#include <helpers/BridgeIngressProfile.h>

class AbstractBridge {
  using PacketFilter = bool (*)(void*, const mesh::Packet*);
  PacketFilter _packet_filter = nullptr;
  void* _packet_filter_context = nullptr;
  const mesh::RadioProfiles* _ingress_profiles = nullptr;
  const mesh::bridge::IngressSource* _ingress_source = nullptr;

protected:
  // Source identity is borrowed only while the main loop admits one frame.
  // The queued Packet retains the selected profile, not this stack pointer.
  class IngressSourceScope {
    AbstractBridge& owner;
    const mesh::bridge::IngressSource* previous;
  public:
    IngressSourceScope(AbstractBridge& bridge, const mesh::bridge::IngressSource& source)
        : owner(bridge), previous(bridge._ingress_source) { owner._ingress_source = &source; }
    ~IngressSourceScope() { owner._ingress_source = previous; }
    IngressSourceScope(const IngressSourceScope&) = delete;
    IngressSourceScope& operator=(const IngressSourceScope&) = delete;
  };

  // The receiver selects a local MeshCore profile, independent of the wire
  // format and the scanner's current tuple. Legacy bridges default to primary.
  // Snapshot the generation so delayed frames cannot acquire a newer lease.
  bool preparePacketIngress(mesh::Packet* packet) const {
    const auto route = mesh::bridge::ingressRoute(_ingress_source);
    const auto target = route.profile;
    if (!packet || target == mesh::bridge::IngressProfile::Blocked
        || target == mesh::bridge::IngressProfile::Off) return false;
    const uint8_t profile = target == mesh::bridge::IngressProfile::Radio2 ? 1 : 0;
    if ((profile == 1 && (!_ingress_profiles || !_ingress_profiles->enabled()
            || !_ingress_profiles->canTransmit(1)))
        || (_ingress_profiles && _ingress_profiles->params(profile).protocol
            != mesh::RadioProfileProtocol::MeshCore)) return false;
    packet->radio_profile = packet->radio_origin = profile;
    packet->radio_generation = packet->radio_origin_generation =
        _ingress_profiles ? _ingress_profiles->generation[profile] : 0;
    packet->radio_bound = false;
    packet->radio_local = false;
    // Explicit secondary ingress stays secondary even if crossing is enabled.
    // The default primary selector retains its historical crossing behavior.
    packet->tx_radio = profile == 1 ? mesh::RADIO_TX_SECONDARY
        : route.explicit_profile ? mesh::RADIO_TX_PRIMARY : mesh::RADIO_TX_AUTO;
    packet->radio_reply = packet->radio_reply_force = false;
    packet->forwarded = true;
    return true;
  }

  // Called on the mesh loop, before copying/marking/queuing a packet. Never
  // invoke the policy from a WiFi callback or the MQTT publishing task.
  bool allowsPacket(const mesh::Packet* packet) const {
    // Route policy is transport-independent: a flood-framed TRACE or CONTROL
    // must never be relayed through a bridge even if the role has no custom
    // bridge filter configured.
    return packet && !packet->violatesRoutePolicy() && (!_packet_filter
        || _packet_filter(_packet_filter_context, packet));
  }

public:
  virtual ~AbstractBridge() {}

  void setIngressProfiles(const mesh::RadioProfiles* profiles) {
    _ingress_profiles = profiles;
  }

  void setPacketFilter(PacketFilter filter, void* context) {
    _packet_filter = filter;
    _packet_filter_context = context;
  }

  /**
   * @brief Initializes the bridge.
   */
  virtual void begin() = 0;

  /**
   * @brief Stops the bridge.
   */
  virtual void end() = 0;

  /**
   * @brief Gets the current state of the bridge.
   *
   * @return true if the bridge is initialized and running, false otherwise.
   */
  virtual bool isRunning() const = 0;

  /**
   * @brief A method to be called on every main loop iteration.
   *        Used for tasks like checking for incoming data.
   */
  virtual void loop() = 0;

  /**
   * @brief A callback that is triggered when the mesh transmits a packet.
   *        The bridge can use this to forward the packet.
   *
   * @param packet The packet that was transmitted.
   */
  virtual void sendPacket(mesh::Packet* packet) = 0;

  /**
   * @brief Processes a received packet from the bridge's medium.
   *
   * @param packet The packet that was received.
   */
  virtual void onPacketReceived(mesh::Packet* packet) = 0;
};
