#include <Arduino.h>
#include <Wire.h>

#include "WioTrackerL1Board.h"

#if MESHCORE_HEADLESS_KISS_COMPAT
  #include "HeadlessKissPins.h"
  extern const char meshcore_capability_modem_headless_wio_l1[] =
      "meshcore.capability.modem.headless.wio_l1.v1";
#endif

void WioTrackerL1Board::begin() {
  NRF52BoardDCDC::begin();
#if MESHCORE_HEADLESS_KISS_COMPAT
  const volatile char* capability = meshcore_capability_modem_headless_wio_l1;
  (void)*capability;
  parkWioL1KissDisplayPins();
#endif
  btn_prev_state = HIGH;

  pinMode(PIN_VBAT_READ, INPUT); // VBAT ADC input
  // Set all button pins to INPUT_PULLUP
  pinMode(PIN_BUTTON1, INPUT_PULLUP);
  pinMode(PIN_BUTTON2, INPUT_PULLUP);
  pinMode(PIN_BUTTON3, INPUT_PULLUP);
  pinMode(PIN_BUTTON4, INPUT_PULLUP);
  pinMode(PIN_BUTTON5, INPUT_PULLUP);
  pinMode(PIN_BUTTON6, INPUT_PULLUP);
  

  #if defined(PIN_WIRE_SDA) && defined(PIN_WIRE_SCL)
    Wire.setPins(PIN_WIRE_SDA, PIN_WIRE_SCL);
  #endif

  Wire.begin();

  #ifdef P_LORA_TX_LED
    pinMode(P_LORA_TX_LED, OUTPUT);
    digitalWrite(P_LORA_TX_LED, LOW);
  #endif

  delay(10);   // give sx1262 some time to power up
}
