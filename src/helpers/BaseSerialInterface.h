#pragma once

#include <Arduino.h>

#define MAX_FRAME_SIZE  176   // +4 for transport codes (region scoping)

class BaseSerialInterface {
protected:
  BaseSerialInterface() { }

public:
  virtual void enable() = 0;
  virtual void disable() = 0;
  virtual bool isEnabled() const = 0;

  virtual bool isConnected() const = 0;
  virtual void loop() {};

  virtual bool isReadBusy() const = 0;
  virtual bool isWriteBusy() const = 0;
  // True while application-level transport work still needs loop service.
  // Backends whose busy methods are throttling/high-water signals should
  // override this with their actual queue state.
  virtual bool hasPendingIO() const { return isReadBusy() || isWriteBusy(); }
  // Returns true once for each pending Bluetooth pairing prompt. Non-BLE
  // transports keep the default implementation so UI code can poll safely.
  virtual bool takePairingRequest() { return false; }
  // Multi-transport implementations can pin a sequence of response frames to
  // the interface which supplied the current command. Single transports have
  // nothing to route, so their default implementations are no-ops.
  virtual void lockReplyRoute() {}
  virtual void unlockReplyRoute() {}
  virtual size_t writeFrame(const uint8_t src[], size_t len) = 0;
  virtual size_t checkRecvFrame(uint8_t dest[]) = 0;
};
