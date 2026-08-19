#include "UsbLogging.h"

#if defined(ARDUINO)
#include <atomic>

namespace mesh {

static std::atomic<bool> usb_logging_enabled{true};

bool isUsbLoggingEnabled() {
  return usb_logging_enabled.load(std::memory_order_relaxed);
}

void setUsbLoggingEnabled(bool enabled) {
  usb_logging_enabled.store(enabled, std::memory_order_relaxed);
}

}  // namespace mesh
#endif
