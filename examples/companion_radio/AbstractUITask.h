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


enum class UIEventType {
    none,
    contactMessage,
    channelMessage,
    roomMessage,
    newContactMessage,
    ack
};

class UIShutdownGuard {
public:
  virtual ~UIShutdownGuard() = default;
  virtual bool prepareForUiShutdown() = 0;
};

class AbstractUITask {
protected:
  mesh::MainBoard* _board;
  MultiSerialInterface* _interfaceManager;
  UIShutdownGuard* _shutdownGuard;
  bool _connected;

  AbstractUITask(mesh::MainBoard* board, MultiSerialInterface* interfaceManager)
      : _board(board), _interfaceManager(interfaceManager),
        _shutdownGuard(nullptr) {
    _connected = false;
  }

  bool prepareForShutdown() {
    return _shutdownGuard == nullptr
        || _shutdownGuard->prepareForUiShutdown();
  }



  bool shouldWakeDisplayForMessage() const { return !hasConnection(); }

public:
  void setShutdownGuard(UIShutdownGuard* guard) { _shutdownGuard = guard; }
  void setHasConnection(bool connected) { _connected = connected; }
  bool hasConnection() const { return _interfaceManager->isConnected(); }
  bool hasBluetoothConnection() const { return _interfaceManager->isBluetoothConnected(); }
  const char* connectedClientLabel() const {
    const bool ble = hasBluetoothConnection();
    const bool usb = _interfaceManager->isInterfaceConnected(InterfaceType::USB);
    if (ble && usb) return "BLE + USB";
    if (ble) return "BLUETOOTH";
    if (usb) return "USB";
    if (_interfaceManager->isInterfaceConnected(InterfaceType::WiFi)) return "TCP";
    if (_interfaceManager->isInterfaceConnected(InterfaceType::Ethernet)) return "ETHERNET";
    if (_interfaceManager->isInterfaceConnected(InterfaceType::HardwareSerial)) return "SERIAL";
    return nullptr;
  }
  uint16_t getBattMilliVolts() const { return _board->getBattMilliVolts(); }
  bool isBluetoothEnabled() const { return _interfaceManager->isBluetoothEnabled(); }
  void enableBluetooth() { _interfaceManager->enableBluetooth(); }
  void disableBluetooth() { _interfaceManager->disableBluetooth(); }
  virtual void msgRead(int msgcount) = 0;
  virtual void syncMessageQueue(int msgcount, int removed_index = -1) {
    (void)removed_index;
    msgRead(msgcount);
  }
  virtual bool supportsInboxModes() const { return false; }
  virtual void inboxModeChanged() {}
  virtual void newMsg(uint8_t path_len, const char* from_name,
                      const char* text, int msgcount,
                      int channel_idx = -1,
                      const char* channel_name = nullptr,
                      int queue_index = -1) = 0;
  virtual void notify(UIEventType t = UIEventType::none) = 0;
  virtual bool supportsDisplayRotation() const { return false; }
  virtual bool setDisplayRotationDegrees(uint16_t degrees) {
    (void)degrees;
    return false;
  }
  // Optional, runtime-only touch diagnostics. Non-touch and legacy UIs
  // explicitly report unsupported rather than accepting an invisible toggle.
  virtual bool supportsTouchDebug() const { return false; }
  virtual bool isTouchDebugEnabled() const { return false; }
  virtual bool setTouchDebugEnabled(bool enabled) {
    (void)enabled;
    return false;
  }
  // Display implementations that surface an incoming BLE passkey request can
  // override these hooks. The default keeps setup-screen routing compatible
  // with older UIs that handle pairing only from their regular loop.
  virtual void servicePairingState() {}
  virtual bool isPairingPromptActive() const { return false; }
  virtual void loop() = 0;
};
