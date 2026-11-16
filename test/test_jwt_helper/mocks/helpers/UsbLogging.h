#pragma once
#include <Arduino.h>
namespace mesh {
inline bool isUsbLoggingEnabled() { return false; }
inline MockSerial& usbLoggingPort() { return Serial; }
}
