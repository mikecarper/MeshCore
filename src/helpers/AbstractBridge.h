#pragma once

#include <Mesh.h>

class AbstractBridge {
  using PacketFilter = bool (*)(void*, const mesh::Packet*);
  PacketFilter _packet_filter = nullptr;
  void* _packet_filter_context = nullptr;
  const mesh::RadioProfiles* _ingress_profiles = nullptr;

protected:
  // Bridges enter the local primary MeshCore network, not whichever tuple
  // the physical scanner currently visits. Snapshot its generation at queue
  // admission so a delayed frame cannot acquire a newer temporary lease.
  bool preparePacketIngress(mesh::Packet* packet) const {
    if (!packet || (_ingress_profiles
        && _ingress_profiles->params(0).protocol != mesh::RadioProfileProtocol::MeshCore)) return false;
    packet->radio_profile = packet->radio_origin = 0;
    packet->radio_generation = packet->radio_origin_generation =
        _ingress_profiles ? _ingress_profiles->generation[0] : 0;
    packet->radio_bound = false;
    packet->radio_local = false;
    packet->tx_radio = mesh::RADIO_TX_AUTO;
    packet->radio_reply = packet->radio_reply_force = false;
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
