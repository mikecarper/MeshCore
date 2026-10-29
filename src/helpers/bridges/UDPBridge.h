#pragma once

#if defined(ESP32) && defined(WITH_UDP_BRIDGE)
#include "helpers/bridges/BridgeBase.h"
#include "helpers/bridges/UDPBridgeProtocol.h"
#include "helpers/UDPBridgePrefs.h"

// All methods run on the mesh loop. No WiFi callbacks own packet memory.
// Network ownership belongs to the role; this class never starts/stops WiFi.
class UDPBridge : public BridgeBase {
public:
  UDPBridge(NodePrefs* prefs, mesh::PacketManager* mgr, mesh::RTCClock* rtc,
            const mesh::bridge::udp::Prefs* settings);
  ~UDPBridge() override;
  void begin() override;
  void end() override;
  void loop() override;
  void serviceNetwork(bool ready, uint32_t local_address = 0, uint32_t netmask = 0);
  void sendPacket(mesh::Packet* packet) override;
  void onPacketReceived(mesh::Packet* packet) override;
  void formatStatus(char* out, size_t capacity) const;
  void formatStats(const char* page, char* out, size_t capacity) const;
  bool sendTest(uint8_t peer_index, uint32_t nonce);
  void resetStats();

private:
  const mesh::bridge::udp::Prefs* _settings;
  mesh::bridge::udp::Prefs _active{};
  mesh::bridge::udp::Session _sessions[mesh::bridge::udp::MAX_PEERS]{};
  uint64_t _boot = 0;
  uint32_t _sequence = 0, _retry_at = 0;
  uint32_t _local_address = 0, _netmask = 0;
  int _socket = -1, _last_error = 0;
  bool _network_ready = false, _secret_valid = false;
  uint8_t _secret[16]{};
  uint8_t _secret_length = 0, _hello_cursor = 0;
  // One logical fan-out packet; each peer receives at most one send attempt.
  uint8_t _tx_payload[mesh::bridge::udp::MAX_PAYLOAD]{};
  uint16_t _tx_length = 0;
  uint8_t _tx_mask = 0;
  mesh::bridge::udp::Kind _tx_kind = mesh::bridge::udp::Kind::Mesh;
  uint32_t _tx_sequence = 0;
  // One verified receive slot survives packet-pool pressure; socket ingress
  // stops until it drains. No packet allocation for probes or invalid frames.
  uint8_t _rx_payload[mesh::bridge::udp::MAX_PAYLOAD]{};
  uint16_t _rx_length = 0;
  uint8_t _rx_peer = 0;
  struct Stats {
    uint32_t frames = 0, authenticated = 0, delivered = 0;
    uint32_t malformed = 0, authentication = 0, foreign = 0, replay = 0;
    uint32_t filtered = 0, duplicate = 0, pool = 0, parse = 0;
    uint32_t requested = 0, queued = 0, sent = 0, failed = 0;
    uint32_t busy = 0, stopped = 0, oversize = 0, tx_filtered = 0, tx_duplicate = 0;
    uint32_t hellos = 0, challenges = 0, established = 0;
    uint32_t probe_queued = 0, probe_sent = 0, probe_failed = 0, probe_received = 0;
    uint32_t last_tx_nonce = 0, last_rx_nonce = 0;
  } _stats;
  bool _debug_dirty = true, _debug_was_enabled = false;
  uint32_t _debug_last_at = 0;

  static bool authenticate(void* context, const uint8_t* data, size_t size, uint8_t digest[32]);
  static uint64_t randomNonce();
  uint8_t peerMask(bool established_only) const;
  uint32_t nextSequence();
  bool sendFrame(uint8_t peer, mesh::bridge::udp::Kind kind, uint64_t recipient,
                 const uint8_t* payload, uint16_t length, uint32_t sequence = 0);
  void receiveFrame(uint8_t peer, const uint8_t* data, size_t length);
  void pumpReceive();
  void drainReceive();
  void pumpTransmit();
  void pumpHello();
  void emitDebug();
  void note(uint32_t& value);
  void error(int code);
  void onIngressResult(IngressResult result) override;
};
#endif
