#include <Arduino.h>
#include "target.h"

// Normal V4 images retain the established RadioLib Arduino HAL.  The KISS
// modem can opt into buffered SPI for its on-board dual-profile RX scanner.
#if defined(MC_SX1262_BUFFERED_RADIO_HAL) && MC_SX1262_BUFFERED_RADIO_HAL
  #include <helpers/radiolib/ESP32BufferedRadioHal.h>
#endif

HeltecV4Board board;

#if defined(HELTEC_V4_OLED_KIT_COMPAT) && HELTEC_V4_OLED_KIT_COMPAT == 1
extern const char meshcore_capability_heltec_v4_oled_kit[];
const char meshcore_capability_heltec_v4_oled_kit[] =
    "meshcore.capability.hardware.heltec_v4.oled_kit.v1";
#endif

#if defined(HELTEC_V4_TFT_KIT_COMPAT) && HELTEC_V4_TFT_KIT_COMPAT == 1 \
    && defined(HELTEC_LORA_V4_TFT) && defined(DISPLAY_CLASS) \
    && defined(PIN_BOARD_SDA) && PIN_BOARD_SDA == 4 \
    && defined(PIN_BOARD_SCL) && PIN_BOARD_SCL == 3 \
    && defined(PIN_TFT_RST) && PIN_TFT_RST == 18 \
    && defined(PIN_TFT_LEDA_CTL) && PIN_TFT_LEDA_CTL == 21 \
    && defined(PIN_TFT_CS) && PIN_TFT_CS == 15 \
    && defined(PIN_TFT_DC) && PIN_TFT_DC == 16 \
    && defined(PIN_TFT_SCL) && PIN_TFT_SCL == 17 \
    && defined(PIN_TFT_SDA) && PIN_TFT_SDA == 33
#define MESHCORE_HELTEC_V4_TFT_KIT_COMPAT 1
#include <type_traits>
extern const char meshcore_capability_heltec_v4_tft_kit[];
const char meshcore_capability_heltec_v4_tft_kit[] =
    "meshcore.capability.hardware.heltec_v4.tft_kit.v1";
#endif

#if defined(P_LORA_SCLK)
  static SPIClass spi;
  #if defined(MC_SX1262_BUFFERED_RADIO_HAL) && MC_SX1262_BUFFERED_RADIO_HAL
    static ESP32BufferedRadioHal radio_hal(spi);
    RADIO_CLASS radio = new Module(&radio_hal, P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);
  #else
    RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY, spi);
  #endif
#else
  RADIO_CLASS radio = new Module(P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);
#endif

WRAPPER_CLASS radio_driver(radio, board);

ESP32RTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);

#if ENV_INCLUDE_GPS
  #include <helpers/sensors/MicroNMEALocationProvider.h>
  MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
  EnvironmentSensorManager sensors = EnvironmentSensorManager(nmea);
#else
  EnvironmentSensorManager sensors;
#endif

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display(NULL);
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true);
#endif

bool radio_init() {
#if defined(HELTEC_V4_OLED_KIT_COMPAT) && HELTEC_V4_OLED_KIT_COMPAT == 1
  const volatile char* capability = meshcore_capability_heltec_v4_oled_kit;
  (void)*capability;
#endif
#if defined(MESHCORE_HELTEC_V4_TFT_KIT_COMPAT)
  if (std::is_same<DISPLAY_CLASS, ST7789LCDDisplay>::value) {
    const volatile char* capability = meshcore_capability_heltec_v4_tft_kit;
    (void)*capability;
  }
#endif
  fallback_clock.begin();
  rtc_clock.begin(Wire);
  
#if defined(P_LORA_SCLK)
  return radio.std_init(&spi);
#else
  return radio.std_init();
#endif
}

mesh::LocalIdentity radio_new_identity() {
  RadioNoiseListener rng(radio);
  return mesh::LocalIdentity(&rng);  // create new random identity
}
