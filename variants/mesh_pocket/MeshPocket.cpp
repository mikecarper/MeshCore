#include <Arduino.h>
#include <Wire.h>

#include "MeshPocket.h"

void HeltecMeshPocket::begin() {
  NRF52BoardDCDC::begin();
  Serial.begin(115200);
  pinMode(PIN_VBAT_READ, INPUT);

  pinMode(PIN_USER_BTN, INPUT);
}
