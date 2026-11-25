#include <Arduino.h>
#include <Wire.h>

#include "MeshPocket.h"
#include <helpers/UsbDataLock.h>

void HeltecMeshPocket::begin() {
  NRF52BoardDCDC::begin();
  if (!mesh::usbDataLocked()) Serial.begin(115200);
  pinMode(PIN_VBAT_READ, INPUT);

  pinMode(PIN_USER_BTN, INPUT);
}
