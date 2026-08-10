#pragma once

#include "BaseSerialInterface.h"
#include <Arduino.h>

class ArduinoSerialInterface : public BaseSerialInterface {
  bool _isEnabled;
  bool _passthroughMode;
  bool _controlSequenceReceived;
  bool _secondaryControlSequenceReceived;
  uint8_t _state;
  size_t _controlSequencePos;
  size_t _secondaryControlSequencePos;
  uint16_t _frame_len;
  uint16_t rx_len;
  Stream* _serial;
  const char* _controlSequence;
  const char* _secondaryControlSequence;
  uint8_t rx_buf[MAX_FRAME_SIZE];

  bool checkControlSequence(uint8_t c, const char* sequence,
                            size_t& position, bool& received);
  void resetReceiveState();

public:
  ArduinoSerialInterface()
      : _isEnabled(false), _passthroughMode(false),
        _controlSequenceReceived(false), _secondaryControlSequenceReceived(false),
        _state(0), _controlSequencePos(0), _secondaryControlSequencePos(0),
        _frame_len(0), rx_len(0), _serial(nullptr),
        _controlSequence(nullptr), _secondaryControlSequence(nullptr) {}

  void begin(Stream& serial, const char* controlSequence = nullptr,
             const char* secondaryControlSequence = nullptr) {
    _serial = &serial;
    _controlSequence = controlSequence;
    _secondaryControlSequence = secondaryControlSequence;
    _passthroughMode = false;
    _controlSequenceReceived = false;
    _secondaryControlSequenceReceived = false;
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
  bool takeSecondaryControlSequence();

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
