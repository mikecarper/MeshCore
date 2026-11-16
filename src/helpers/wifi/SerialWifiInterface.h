#pragma once

#include "../BaseSerialInterface.h"
#include "../UsbLogging.h"
#include <WiFi.h>

class SerialWifiInterface : public BaseSerialInterface {
public:
  enum class State : uint8_t { Off, WaitingIp, Retrying, Listening };
  enum class StartError : uint8_t { None, InvalidPort, StartFailed };
  enum class DisconnectReason : uint8_t {
    None, PeerClosed, FrameTimeout, InvalidHeader, ShortHeader, ShortPayload,
    Replaced, Disabled, Ended, TxError, TxTimeout
  };
  struct Status {
    State state;
    bool enabled;  // Logical enable intent, including a listener awaiting retry.
    bool requested;
    bool addressReady;
    bool listening;  // Actual SDK listener state, not merely an assigned IP.
    bool clientConnected;
    uint16_t port;
    uint32_t beginAttempts;
    uint32_t beginFailures;
    uint32_t retryInMs;
    StartError lastStartError;
    int lastErrno;
    uint32_t acceptedClients;
    uint32_t validFrames;
    uint8_t lastOpcode;
    uint32_t txFrames;
    uint8_t lastTxOpcode;
    uint32_t disconnects;
    DisconnectReason lastDisconnectReason;
  };

private:
  bool deviceConnected;
  // Keep enable intent separate from actual protocol availability. A failed
  // cold listener needs loop service; a healthy accepted socket remains usable.
  bool _isEnabled;
  unsigned long _last_write;

  // Arduino's listener conversion and client health accessor are not const.
  mutable WiFiServer server;
  mutable WiFiClient client;
  bool listener_requested = false;
  bool listener_was_healthy = false;
  bool listener_attempted = false;
  uint16_t listener_port = 0;
  uint8_t consecutive_start_failures = 0;
  uint32_t listener_last_attempt = 0;
  uint32_t listener_retry_ms = 0;
  uint32_t listener_begin_attempts = 0;
  uint32_t listener_begin_failures = 0;
  StartError listener_last_error = StartError::None;
  int listener_last_errno = 0;
  uint32_t accepted_clients = 0;
  uint32_t valid_frames = 0;
  uint8_t last_opcode = 0;
  uint32_t tx_frames = 0;
  uint8_t last_tx_opcode = 0;
  bool tx_waiting = false;
  uint32_t tx_last_progress = 0;
  uint32_t disconnect_count = 0;
  DisconnectReason last_disconnect_reason = DisconnectReason::None;

  struct FrameHeader {
    uint8_t type;
    uint16_t length;
  };

  struct Frame {
    uint8_t len;
    uint8_t buf[MAX_FRAME_SIZE];
  };

  FrameHeader received_frame_header;

  #define FRAME_QUEUE_SIZE  4
  // Reuse the former (unused) receive queue for one displaced client's replies.
  // No heap allocation or additional frame buffer is needed on reconnect.
  int held_queue_len;
  Frame held_queue[FRAME_QUEUE_SIZE];
  int send_queue_len;
  Frame send_queue[FRAME_QUEUE_SIZE];
  IPAddress queue_ip;
  IPAddress held_ip;
  bool queue_has_ip = false;
  uint32_t held_since = 0;
  size_t send_offset = 0;
  uint32_t receive_started = 0;
  bool receive_pending = false;
  bool receive_capacity_paused = false;
  uint32_t receive_capacity_paused_at = 0;
  void (*session_changed)(void*) = nullptr;
  void* session_context = nullptr;
  bool (*session_boundary)(void*) = nullptr;
  void* boundary_context = nullptr;
  bool ota_raw_receive = false;
  bool loopback_reserved = false;
  bool browser_ota_session = false;

  void clearBuffers();
  void expireHeldQueue(uint32_t now);
  void selectClient(const IPAddress& ip, uint32_t now);
  void disconnectClient(bool cancel_session, DisconnectReason reason);
  bool addressReady();
  bool serviceListener(uint32_t now);
  void stopListener(DisconnectReason reason);
  bool responseCapacityAvailable() const;
  void updateReceiveCapacityPause(uint32_t now);

protected:

public:
  SerialWifiInterface() : server(WiFiServer()), client(WiFiClient()) {
    deviceConnected = false;
    _isEnabled = false;
    _last_write = 0;
    clearBuffers();
    received_frame_header.type = 0;
    received_frame_header.length = 0;
  }

  // Explicitly request a listener; does not enable the protocol interface.
  // Failure or an unready network is retried by loop() only while enabled.
  bool begin(int port);
  void end();
  Status getStatus();
  static const char* stateName(State state);
  static const char* startErrorName(StartError error);
  static const char* disconnectReasonName(DisconnectReason reason);
  // Called synchronously while disconnected, on teardown or before a different
  // IP can send commands/receive replies. Same-IP live replacement preserves
  // the existing owner; actual disconnects cancel its pending operations.
  void setSessionChangedCallback(void (*callback)(void*), void* context) {
    session_changed = callback;
    session_context = context;
  }
  // Guarded OTA must not survive any TCP session boundary, including a
  // same-IP replacement. Returning true discards the old owner's replies.
  void setSessionBoundaryCallback(bool (*callback)(void*), void* context) {
    session_boundary = callback;
    boundary_context = context;
  }
  void enableOtaRawReceive(bool enabled) { ota_raw_receive = enabled; }
  // A local browser gateway must never replace another Companion client.
  // While reserved, only its loopback connection may enter this listener.
  bool reserveLoopbackClient();
  void releaseLoopbackClient() { loopback_reserved = false; }
  bool browserOtaRestricted() const override { return loopback_reserved || browser_ota_session; }
  size_t usbRawPacketReceiveCapacity() const override {
    return ota_raw_receive ? MAX_USB_RAW_FRAME_SIZE : MAX_FRAME_SIZE;
  }

  // BaseSerialInterface methods
  void enable() override;
  void disable() override;
  bool isEnabled() const override {
    // Losing the accept listener must not cancel a stream whose separately
    // accepted socket is still healthy. Failed cold starts remain unavailable.
    return _isEnabled && listener_requested
        && (static_cast<bool>(server) || (deviceConnected && client.connected()));
  }
  void loop() override;

  bool isConnected() const override;
  bool isReadBusy() const override;
  bool isWriteBusy() const override;
  // A parked backlog is not runnable work and must not keep the MCU awake.
  bool hasPendingIO() const override { return _isEnabled && deviceConnected && send_queue_len > 0; }

  size_t writeFrame(const uint8_t src[], size_t len) override;
  size_t checkRecvFrame(uint8_t dest[]) override;
  size_t checkRecvFrame(uint8_t dest[], size_t capacity) override;

  bool hasReceivedFrameHeader();
  void resetReceivedFrameHeader();
};

#if WIFI_DEBUG_LOGGING && ARDUINO
  #include <Arduino.h>
  #define WIFI_DEBUG_PRINT(F, ...) do { if (mesh::isUsbDebugLoggingEnabled()) { mesh::usbLoggingPort().printf("WiFi: " F, ##__VA_ARGS__); } } while(0)
  #define WIFI_DEBUG_PRINTLN(F, ...) do { if (mesh::isUsbDebugLoggingEnabled()) { mesh::usbLoggingPort().printf("WiFi: " F "\n", ##__VA_ARGS__); } } while(0)
#else
  #define WIFI_DEBUG_PRINT(...) {}
  #define WIFI_DEBUG_PRINTLN(...) {}
#endif
