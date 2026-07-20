#pragma once

#include <MeshCore.h>
#include <helpers/ui/DisplayDriver.h>
#include <helpers/ui/UIScreen.h>
#include <helpers/SensorManager.h>
#include <helpers/BaseSerialInterface.h>
#include <Arduino.h>

#ifdef PIN_BUZZER
  #include <helpers/ui/buzzer.h>
#endif

#include "NodePrefs.h"

enum class UIEventType {
    none,
    contactMessage,
    channelMessage,
    roomMessage,
    newContactMessage,
    ack
};

class AbstractUITask {
protected:
  mesh::MainBoard* _board;
  BaseSerialInterface* _serial;
  bool _connected;

  AbstractUITask(mesh::MainBoard* board, BaseSerialInterface* serial) : _board(board), _serial(serial) {
    _connected = false;
  }

  bool isDisplayAutoOffDue(unsigned long configured_deadline,
                           unsigned long configured_timeout_millis) const {
    unsigned long deadline = configured_deadline;
    if (_board->isUsbHostConnected()) {
      // The configured deadline already includes the first timeout period.
      // Add four more periods for a total of 5x while attached to a computer.
      deadline += configured_timeout_millis * 4UL;
    }
    return static_cast<int32_t>(millis() - deadline) > 0;
  }

  bool shouldWakeDisplayForMessage() const {
    // Keep the existing BLE-only behavior, where the connected companion is
    // expected to surface the notification. A computer attached over USB is
    // the exception: show the message on the device even if BLE is connected.
    return !hasConnection() || _board->isUsbHostConnected();
  }

public:
  void setHasConnection(bool connected) { _connected = connected; }
  bool hasConnection() const { return _connected; }
  uint16_t getBattMilliVolts() const { return _board->getBattMilliVolts(); }
  bool isSerialEnabled() const { return _serial->isEnabled(); }
  void enableSerial() { _serial->enable(); }
  void disableSerial() { _serial->disable(); }
  virtual void msgRead(int msgcount) = 0;
  virtual void newMsg(uint8_t path_len, const char* from_name, const char* text, int msgcount) = 0;
  virtual void notify(UIEventType t = UIEventType::none) = 0;
  virtual void loop() = 0;
};
