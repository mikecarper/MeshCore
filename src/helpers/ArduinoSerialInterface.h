#pragma once

#include "BaseSerialInterface.h"
#include <Arduino.h>

class ArduinoSerialInterface : public BaseSerialInterface {
public:
  // reports whether a client currently has this stream open (see setConnectedCheck)
  typedef bool (*ConnectedCheck)();

private:
  static constexpr uint8_t TX_QUEUE_SIZE = 4;
  static constexpr uint32_t RX_FRAME_TIMEOUT_MS = 1000;

  struct TxFrame {
    uint16_t len;
    uint8_t buf[MAX_FRAME_SIZE + 3];
  };

  bool _isEnabled;
  bool _passthroughMode;
  bool _controlSequenceReceived;
  bool _secondaryControlSequenceReceived;
  bool _flow_ctl;
  uint8_t _state;
  size_t _controlSequencePos;
  size_t _secondaryControlSequencePos;
  bool _controlSequenceCandidate;
  bool _secondaryControlSequenceCandidate;
  uint16_t _frame_len;
  uint16_t rx_len;
  uint32_t _last_frame_ms;
  uint32_t _completed_frame_count;
  uint32_t _last_rx_byte_ms;
  bool _has_received_frame;
  Stream* _serial;
  const char* _controlSequence;
  const char* _secondaryControlSequence;
  ConnectedCheck _conn_check;
  uint8_t rx_buf[MAX_FRAME_SIZE];
  TxFrame _tx_queue[TX_QUEUE_SIZE];
  uint8_t _tx_queue_len;
  uint16_t _tx_offset;

  void resetControlSequenceState();
  bool checkControlLineByte(uint8_t c);
  void resetReceiveState();
  void serviceReceiveTimeout();
  void resetTransmitState();
  bool enqueueFrame(const uint8_t src[], size_t len);
  void serviceTransmit();

public:
  ArduinoSerialInterface()
      : _isEnabled(false), _passthroughMode(false),
        _controlSequenceReceived(false), _secondaryControlSequenceReceived(false),
        _flow_ctl(false), _state(0), _controlSequencePos(0),
        _secondaryControlSequencePos(0), _controlSequenceCandidate(false),
        _secondaryControlSequenceCandidate(false), _frame_len(0), rx_len(0),
        _last_frame_ms(0), _completed_frame_count(0), _last_rx_byte_ms(0),
        _has_received_frame(false),
        _serial(nullptr), _controlSequence(nullptr),
        _secondaryControlSequence(nullptr), _conn_check(nullptr),
        _tx_queue_len(0), _tx_offset(0) {}

  void begin(Stream& serial, const char* controlSequence = nullptr,
             const char* secondaryControlSequence = nullptr) {
    _serial = &serial;
    _controlSequence = controlSequence;
    _secondaryControlSequence = secondaryControlSequence;
    _passthroughMode = false;
    _controlSequenceReceived = false;
    _secondaryControlSequenceReceived = false;
    _last_frame_ms = 0;
    _completed_frame_count = 0;
    _has_received_frame = false;
    resetReceiveState();
    resetTransmitState();
  #ifdef RAK_4631
    pinMode(WB_IO2, OUTPUT);
  #endif
  }

  // In passthrough mode another line-oriented consumer owns the Stream. Binary
  // frames are neither read from nor written to this interface.
  void setPassthroughMode(bool enabled);
  bool isPassthroughMode() const { return _passthroughMode; }

  // Returns true once for each complete control sequence received while the
  // binary frame parser was idle.
  bool takeControlSequence();
  bool takeSecondaryControlSequence();

  // Optional: let the target report the real link state, e.g. USB-CDC DTR.
  // Without it isConnected() assumes true, as a plain UART has no way of knowing.
  void setConnectedCheck(ConnectedCheck fn) { _conn_check = fn; }

  // Clear all protocol state owned by one host session while retaining the
  // monotonic completed-frame counter used by startup handoff arbitration.
  void resetSessionState();

  // millis() of the last completely received frame, 0 if none since boot.
  // Useful as an activity-based connection check where no DTR state exists.
  uint32_t getLastFrameMillis() const { return _last_frame_ms; }
  bool hasReceivedFrame() const { return _has_received_frame; }
  uint32_t getCompletedFrameCount() const { return _completed_frame_count; }

  // Optional: queue complete frames and drain one at a time according to the
  // stream's TX capacity, so short writes cannot discard or interleave bytes.
  // Report busy while a frame remains queued so bulk streams get paced.
  // Only enable this for streams which really implement availableForWrite()
  // (USB-CDC does, the Print default returns 0).
  void enableFlowControl(bool enable) {
    if (!enable) resetTransmitState();
    _flow_ctl = enable;
  }

  // BaseSerialInterface methods
  void enable() override;
  void disable() override;
  bool isEnabled() const override { return _isEnabled; }

  bool isConnected() const override;
  void loop() override;

  bool isReadBusy() const override;
  bool isWriteBusy() const override;
  bool hasPendingIO() const override;
  size_t writeFrame(const uint8_t src[], size_t len) override;
  size_t checkRecvFrame(uint8_t dest[]) override;
};
