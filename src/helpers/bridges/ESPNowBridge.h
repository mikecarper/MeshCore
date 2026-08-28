#pragma once

#include "MeshCore.h"
#include "esp_now.h"
#include "esp_idf_version.h"
#include <freertos/FreeRTOS.h>
#include "helpers/ESPNowRawFragmentation.h"
#include "helpers/bridges/BridgeBase.h"
#include "helpers/bridges/ESPNowBridgeFormat.h"

#ifdef WITH_ESPNOW_BRIDGE

/**
 * @brief Bridge implementation using ESP-NOW protocol for packet transport
 *
 * This bridge enables mesh packet transport over ESP-NOW, a connectionless communication
 * protocol provided by Espressif that allows ESP32 devices to communicate directly
 * without WiFi router infrastructure.
 *
 * Features:
 * - Broadcast-based communication (all bridges receive all packets)
 * - Selectable wrapped or raw MeshCore wire format
 * - Network isolation using XOR encryption with shared secret in wrapped mode
 * - Duplicate packet detection using SimpleMeshTables tracking
 * - Raw MeshCore packets up to 255 bytes using two frames only when needed
 *
 * Wrapped Packet Structure (the backward-compatible default):
 * [2 bytes] Magic Header - Used to identify ESPNowBridge packets
 * [2 bytes] Fletcher-16 checksum of the plaintext mesh payload
 * [246 bytes max] Mesh packet payload
 *
 * The Fletcher-16 checksum is used to validate packet integrity and detect
 * corrupted packets. It is calculated over the plaintext payload; the
 * checksum and payload are then XORed together using bridge.secret. A receiver
 * with the wrong secret will normally fail checksum validation.
 *
 * Raw mode sends the exact bytes produced by mesh::Packet::writeTo(). It is
 * compatible with primary ESPNOWRadio nodes, but has no bridge-secret wrapper.
 * Packets up to the ESP-NOW frame limit remain byte-for-byte unchanged;
 * 251-255-byte packets use a versioned two-frame envelope understood by
 * updated raw endpoints. Receive parsing is deliberately strict to the
 * selected format so a mixed deployment cannot silently become asymmetric.
 *
 * Configuration:
 * - Define WITH_ESPNOW_BRIDGE to enable this bridge
 * - Define _prefs->bridge_secret with a string to set the network encryption key
 * - Set _prefs->bridge_format to wrapped (default) or raw
 *
 * Network Isolation (wrapped mode only):
 * Multiple independent wrapped mesh networks can coexist by using different
 * _prefs->bridge_secret values. Packets encrypted with a different key will
 * fail the checksum validation and be discarded.
 */
class ESPNowBridge : public BridgeBase {
private:
  static ESPNowBridge *_instance;
#if ESP_IDF_VERSION_MAJOR >= 5
  static void recv_cb(const esp_now_recv_info_t *info, const uint8_t *data, int len);
#else
  static void recv_cb(const uint8_t *mac, const uint8_t *data, int len);
#endif
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
  static void send_cb(const esp_now_send_info_t *info,
                      esp_now_send_status_t status);
#else
  static void send_cb(const uint8_t *mac, esp_now_send_status_t status);
#endif

  /**
   * ESP-NOW Protocol Structure:
   * - ESP-NOW header: 20 bytes (handled by ESP-NOW protocol)
   * - ESP-NOW payload: 250 bytes maximum
   * Total ESP-NOW packet: 270 bytes
   *
   * Our Bridge Packet Structure (must fit in ESP-NOW payload):
   * - Magic header: 2 bytes
   * - Checksum: 2 bytes
   * - Available payload: 246 bytes
   */
  static const size_t MAX_ESPNOW_PACKET_SIZE = mesh::bridge::ESPNOW_MAX_FRAME_SIZE;
  static const uint8_t RX_QUEUE_DEPTH = 4;
  static const uint8_t TX_QUEUE_DEPTH = 6;

  /** Bounded callback-to-loop queue for receiving ESP-NOW packets. */
  uint8_t _rx_buffers[RX_QUEUE_DEPTH][MAX_ESPNOW_PACKET_SIZE];
  uint16_t _rx_lengths[RX_QUEUE_DEPTH];
  uint8_t _rx_sources[RX_QUEUE_DEPTH]
                     [mesh::espnow::ESPNOW_RAW_SOURCE_MAC_SIZE];

  // The ESP-NOW callback runs on the WiFi task. It only prefilters and copies
  // bounded frames here; loop() owns PacketManager and duplicate processing.
  portMUX_TYPE _rx_mux = portMUX_INITIALIZER_UNLOCKED;
  volatile uint8_t _rx_head;
  volatile uint8_t _rx_tail;
  volatile uint8_t _rx_count;
  volatile uint32_t _rx_dropped;
  uint32_t _rx_dropped_reported;

  // Keep logical packets intact in the queue. Espressif recommends waiting
  // for a frame's send callback before submitting the next one, and retaining
  // the packet boundary lets a failed first fragment discard its sibling
  // instead of transmitting an orphan fragment.
  struct QueuedTransmit {
    mesh::espnow::ESPNowRawFrames frames;
    mesh::Packet packet;
    uint8_t next_frame;
    bool started;
  };
  QueuedTransmit _tx_queue[TX_QUEUE_DEPTH];
  portMUX_TYPE _tx_mux = portMUX_INITIALIZER_UNLOCKED;
  volatile uint8_t _tx_head;
  volatile uint8_t _tx_tail;
  volatile uint8_t _tx_count;
  volatile bool _tx_waiting;
  volatile bool _tx_callback_done;
  volatile int _tx_callback_status;
  volatile uint32_t _tx_dropped;
  uint32_t _tx_dropped_reported;

  /** Raw-mode fragment assemblies, keyed by source MAC. */
  mesh::espnow::ESPNowRawReassembler _raw_reassembler;

  // Snapshotted by begin() and immutable until end(), so callbacks and sends
  // never observe a partially applied preference change.
  uint8_t _active_format;

  /**
   * Performs XOR encryption/decryption of data
   * Used to isolate different mesh networks
   *
   * Uses _prefs->bridge_secret as the key in a simple XOR operation.
   * The same operation is used for both encryption and decryption.
   * While not cryptographically secure, it provides basic network isolation.
   *
   * @param data Pointer to data to encrypt/decrypt
   * @param len Length of data in bytes
   */
  bool xorCrypt(uint8_t *data, size_t len);

  /** Parse and queue one serialized MeshCore packet. */
  void receiveMeshPacket(const uint8_t *data, size_t len);

  /**
   * ESP-NOW receive callback
   * Called by ESP-NOW when a packet is received
   *
   * @param mac Source MAC address
   * @param data Received data
   * @param len Length of received data
   */
  void queueReceivedFrame(const uint8_t *mac, const uint8_t *data, int len);

  /** Parse a queued frame from loop(), never from the WiFi callback. */
  void processReceivedFrame(const uint8_t *mac, const uint8_t *data,
                            size_t len);

  /** Atomically append one logical packet's one or two transport frames. */
  bool queueTransmitFrames(const mesh::espnow::ESPNowRawFrames& frames,
                           const mesh::Packet& packet);

  /** Submit at most one queued frame after the previous send callback. */
  void pumpTransmitQueue();

  /**
   * ESP-NOW send callback
   * Called by ESP-NOW after a transmission attempt
   *
   * @param mac_addr Destination MAC address
   * @param status Transmission status
   */
  void onDataSent(const uint8_t *mac_addr, esp_now_send_status_t status);

public:
  /**
   * Constructs an ESPNowBridge instance
   *
   * @param prefs Node preferences for configuration settings
   * @param mgr PacketManager for allocating and queuing packets
   * @param rtc RTCClock for timestamping debug messages
   */
  ESPNowBridge(NodePrefs *prefs, mesh::PacketManager *mgr, mesh::RTCClock *rtc);

  /**
   * Initializes the ESP-NOW bridge
   *
   * - Configures WiFi in station mode
   * - Initializes ESP-NOW protocol
   * - Registers callbacks
   * - Sets up broadcast peer
   */
  void begin() override;

  /**
   * Stops the ESP-NOW bridge
   *
   * - Removes broadcast peer
   * - Unregisters callbacks
   * - Deinitializes ESP-NOW protocol
   * - Turns off WiFi to release radio resources
   */
  void end() override;

  /**
   * Main loop handler
   * Drains the bounded frames handed off by the WiFi receive callback.
   */
  void loop() override;

  /**
   * Called when a packet is received via ESP-NOW
   * Queues the packet for mesh processing if not seen before
   *
   * @param packet The received mesh packet
   */
  void onPacketReceived(mesh::Packet *packet) override;

  /**
   * Called when a packet needs to be transmitted via ESP-NOW
   * Encrypts and broadcasts the packet if not seen before
   *
   * @param packet The mesh packet to transmit
   */
  void sendPacket(mesh::Packet *packet) override;
};

#endif
