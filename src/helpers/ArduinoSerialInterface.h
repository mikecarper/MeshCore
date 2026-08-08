#pragma once

#include "BaseSerialInterface.h"
#include <Arduino.h>

class ArduinoSerialInterface : public BaseSerialInterface {
  bool _isEnabled;
  bool _passthroughMode;
  bool _controlSequenceReceived;
  uint8_t _state;
  size_t _controlSequencePos;
  uint16_t _frame_len;
  uint16_t rx_len;
  Stream* _serial;
  const char* _controlSequence;
  uint8_t rx_buf[MAX_FRAME_SIZE];

  bool checkControlSequence(uint8_t c);
  void resetReceiveState();

public:
  ArduinoSerialInterface()
      : _isEnabled(false), _passthroughMode(false),
        _controlSequenceReceived(false), _state(0), _controlSequencePos(0),
        _frame_len(0), rx_len(0), _serial(nullptr),
        _controlSequence(nullptr) {}

  void begin(Stream& serial, const char* controlSequence = nullptr) {
    _serial = &serial;
    _controlSequence = controlSequence;
    _passthroughMode = false;
    _controlSequenceReceived = false;
    resetReceiveState();
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

  // BaseSerialInterface methods
  void enable() override;
  void disable() override;
  bool isEnabled() const override { return _isEnabled; }

  bool isConnected() const override;

  bool isReadBusy() const override;
  bool isWriteBusy() const override;
  size_t writeFrame(const uint8_t src[], size_t len) override;
  size_t checkRecvFrame(uint8_t dest[]) override;
};
