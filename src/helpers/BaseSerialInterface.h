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

  virtual bool isReadBusy() const = 0;
  virtual bool isWriteBusy() const = 0;
  // True while application-level transport work still needs loop service.
  // Backends whose busy methods are throttling/high-water signals should
  // override this with their actual queue state.
  virtual bool hasPendingIO() const { return isReadBusy() || isWriteBusy(); }
  virtual size_t writeFrame(const uint8_t src[], size_t len) = 0;
  virtual size_t checkRecvFrame(uint8_t dest[]) = 0;
};
