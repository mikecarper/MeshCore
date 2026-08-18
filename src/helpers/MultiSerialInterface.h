#pragma once

#include "BaseSerialInterface.h"
#include "CompanionFrameQueue.h"

#ifndef MAX_INTERFACES
  // ble, usb, wifi, ethernet
  #define MAX_INTERFACES 4
#endif

enum class InterfaceType : uint8_t {
  NONE,
  Bluetooth,
  USB,
  WiFi,
  Ethernet,
  HardwareSerial
};

class MultiSerialInterface : public BaseSerialInterface {
private:

  struct RegisteredInterface {
    InterfaceType type = InterfaceType::NONE;
    BaseSerialInterface* instance = nullptr;
  };

  bool _enabled = false;
  RegisteredInterface _interfaces[MAX_INTERFACES] = {};
  BaseSerialInterface* _lastRxInterface = nullptr;
  BaseSerialInterface* _lockedReplyInterface = nullptr;

  bool isAvailableReplyTarget(BaseSerialInterface* target) const {
    if (target == nullptr) return false;
    for (auto iface : _interfaces) {
      if (iface.instance == target) {
        return target->isEnabled() && target->isConnected();
      }
    }
    return false;
  }

  void clearReplyRouteFor(BaseSerialInterface* target) {
    // Keep a locked pointer until the response producer observes that its
    // target is unavailable and aborts the transaction. Clearing it here would
    // make the remaining frames fall back to broadcast on another interface.
    if (_lockedReplyInterface == target) return;
    if (_lastRxInterface == target) _lastRxInterface = nullptr;
  }

  BaseSerialInterface* replyTarget() const {
    return _lockedReplyInterface != nullptr
        ? _lockedReplyInterface : _lastRxInterface;
  }

public:
  bool addInterface(InterfaceType type, BaseSerialInterface* iface) {
    // make sure an interface was provided
    if(iface == nullptr){
      return false;
    }

    // put it in the first free slot
    for(int i = 0; i < MAX_INTERFACES; i++){
      if(_interfaces[i].instance == nullptr){
        _interfaces[i].instance = iface;
        _interfaces[i].type = type;
        return true;
      }
    }

    // no free slots available
    return false;
  }

  bool removeInterface(BaseSerialInterface* iface) {
    // make sure an interface was provided
    if(iface == nullptr){
      return false;
    }

    // find and remove interface
    for(int i = 0; i < MAX_INTERFACES; i++){
      if(_interfaces[i].instance == iface){
        clearReplyRouteFor(iface);
        _interfaces[i] = {};
        return true;
      }
    }

    // interface not found
    return false;
  }

  void enableBluetooth() {
    for(auto iface : _interfaces){
      if(iface.instance && iface.type == InterfaceType::Bluetooth){
        iface.instance->enable();
      }
    }
  }

  void disableBluetooth() {
    for(auto iface : _interfaces){
      if(iface.instance && iface.type == InterfaceType::Bluetooth){
        iface.instance->disable();
        clearReplyRouteFor(iface.instance);
      }
    }
  }

  bool isBluetoothEnabled() {
    for(auto iface : _interfaces){
      if(iface.instance && iface.type == InterfaceType::Bluetooth){
        return iface.instance->isEnabled();
      }
    }
    return false; 
  }

  bool isBluetoothConnected() const {
    if (!_enabled) return false;

    for (auto iface : _interfaces) {
      if (iface.instance && iface.type == InterfaceType::Bluetooth
          && iface.instance->isEnabled() && iface.instance->isConnected()) {
        return true;
      }
    }
    return false;
  }

  // enable all interfaces
  void enable() override {
    _enabled = true;
    _lastRxInterface = nullptr;
    _lockedReplyInterface = nullptr;
    for(auto iface : _interfaces){
      if(iface.instance){
        iface.instance->enable();
      }
    }
  }

  // disable all interfaces
  void disable() override {
    _enabled = false;
    _lastRxInterface = nullptr;
    _lockedReplyInterface = nullptr;
    for(auto iface : _interfaces){
      if(iface.instance){
        iface.instance->disable();
      }
    }
  }

  bool isEnabled() const override { 
    return _enabled; 
  }

  bool isConnected() const override {
    // not connected when disabled
    if(!_enabled){
      return false;
    }
    
    // A locked multi-frame reply belongs to one client. Treat losing that
    // client as a disconnect even if another transport remains connected, so
    // the producer aborts instead of leaking the remainder to somebody else.
    if (_lockedReplyInterface != nullptr) {
      return isAvailableReplyTarget(_lockedReplyInterface);
    }

    // check if any enabled interface is connected
    for(auto iface : _interfaces){
      if(iface.instance && iface.instance->isEnabled()
          && iface.instance->isConnected()) {
        return true;
      }
    }

    // nothing connected
    return false;
  }

  // loop all interfaces
  void loop() override {
    for(auto iface : _interfaces){
      if(iface.instance){
        iface.instance->loop();
      }
    }
  }

  bool isWriteBusy() const override {
    // not busy when disabled
    if(!_enabled){
      return false;
    }

    // Pace a response stream against its destination. A slow inactive BLE or
    // WiFi client must not stall a contact sync running over USB (or vice versa).
    BaseSerialInterface* target = replyTarget();
    if (isAvailableReplyTarget(target)) return target->isWriteBusy();

    // With no requester yet, preserve the aggregate behavior used for pushes.
    for(auto iface : _interfaces){
      if(iface.instance && iface.instance->isEnabled() && iface.instance->isWriteBusy()){
        return true;
      }
    }

    // nothing busy
    return false;
  }

  bool isReadBusy() const override {
    if (!_enabled) return false;
    for (auto iface : _interfaces) {
      if (iface.instance && iface.instance->isEnabled()
          && iface.instance->isReadBusy()) {
        return true;
      }
    }
    return false;
  }

  bool hasPendingIO() const override {
    if (!_enabled) return false;
    for (auto iface : _interfaces) {
      if (iface.instance && iface.instance->isEnabled()
          && iface.instance->hasPendingIO()) {
        return true;
      }
    }
    return false;
  }

  bool takePairingRequest() override {
    for (auto iface : _interfaces) {
      if (iface.instance && iface.type == InterfaceType::Bluetooth
          && iface.instance->isEnabled()
          && iface.instance->takePairingRequest()) {
        return true;
      }
    }
    return false;
  }

  void lockReplyRoute() override {
    if (isAvailableReplyTarget(_lastRxInterface)) {
      _lockedReplyInterface = _lastRxInterface;
    }
  }

  void unlockReplyRoute() override {
    _lockedReplyInterface = nullptr;
  }

  size_t writeFrame(const uint8_t src[], size_t len) override {
    // don't write when disabled or nothing provided
    if(!_enabled || src == nullptr || len == 0){
      return 0;
    }

    // Responses and delivery-required completion pushes belong to the client
    // which supplied the latest command. Best-effort asynchronous observations
    // remain broadcast so passive connected apps can keep their view fresh.
    if (mesh::companionFrameRequiresDelivery(src, len)) {
      BaseSerialInterface* target = replyTarget();
      if (target != nullptr) {
        if (!isAvailableReplyTarget(target)) return 0;
        return target->writeFrame(src, len);
      }
    }

    // Before any command establishes a reply route, or for best-effort pushes,
    // write the frame to all enabled interfaces.
    bool allSuccessful = true;
    for(auto iface : _interfaces){
      if(iface.instance && iface.instance->isEnabled()){
        if(iface.instance->writeFrame(src, len) != len){
          allSuccessful = false;
        }
      }
    }

    // report success if all writes completed successfully
    return allSuccessful ? len : 0; 
  }

  size_t checkRecvFrame(uint8_t dest[]) override {
    // don't read when disabled
    if(!_enabled || dest == nullptr){
      return 0;
    }

    // Keep a multi-frame response transaction on its originating transport.
    // Other interfaces retain their input until the producer unlocks the route.
    if (_lockedReplyInterface != nullptr) {
      if (!isAvailableReplyTarget(_lockedReplyInterface)) return 0;
      size_t frameSize = _lockedReplyInterface->checkRecvFrame(dest);
      if (frameSize > 0) _lastRxInterface = _lockedReplyInterface;
      return frameSize;
    }

    if (_lastRxInterface != nullptr
        && !isAvailableReplyTarget(_lastRxInterface)) {
      _lastRxInterface = nullptr;
    }

    // try to read a frame from any enabled interface
    for(auto iface : _interfaces){
      if(iface.instance && iface.instance->isEnabled()){
        size_t frameSize = iface.instance->checkRecvFrame(dest);
        if(frameSize > 0){
          _lastRxInterface = iface.instance;
          return frameSize; 
        }
      }
    }

    // no frame received
    return 0;
  }

};
