#pragma once

#include <MeshCore.h>
#include <helpers/ui/DisplayDriver.h>
#include <helpers/ui/UIScreen.h>
#include <helpers/SensorManager.h>
#include <helpers/MultiSerialInterface.h>
#include <Arduino.h>

#ifdef PIN_BUZZER
  #include <helpers/ui/buzzer.h>
#endif

#include "NodePrefs.h"

#ifndef UI_USB_AUTO_OFF_MULTIPLIER
  #define UI_USB_AUTO_OFF_MULTIPLIER 5UL
#endif

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
  MultiSerialInterface* _interfaceManager;
  bool _connected;

  AbstractUITask(mesh::MainBoard* board, MultiSerialInterface* interfaceManager) : _board(board), _interfaceManager(interfaceManager) {
    _connected = false;
  }

  bool isDisplayAutoOffDue(unsigned long configured_deadline,
                           unsigned long configured_timeout_millis) const {
    unsigned long deadline = configured_deadline;
#if UI_USB_AUTO_OFF_MULTIPLIER > 1
    if (_board->isUsbHostConnected()) {
      // The configured deadline already includes the first timeout period.
      // Add the remaining periods while attached to a computer.
      deadline += configured_timeout_millis
          * (UI_USB_AUTO_OFF_MULTIPLIER - 1UL);
    }
#endif
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
  bool hasBluetoothConnection() const { return _interfaceManager->isBluetoothConnected(); }
  uint16_t getBattMilliVolts() const { return _board->getBattMilliVolts(); }
  bool isBluetoothEnabled() const { return _interfaceManager->isBluetoothEnabled(); }
  void enableBluetooth() { _interfaceManager->enableBluetooth(); }
  void disableBluetooth() { _interfaceManager->disableBluetooth(); }
  virtual void msgRead(int msgcount) = 0;
  virtual void newMsg(uint8_t path_len, const char* from_name,
                      const char* text, int msgcount,
                      int channel_idx = -1,
                      const char* channel_name = nullptr) = 0;
  virtual void notify(UIEventType t = UIEventType::none) = 0;
  virtual void loop() = 0;
};
