#pragma once

#include <stddef.h>
#include <stdint.h>

#include "sensors/EnvironmentI2CConfig.h"
#if defined(STATION_G3_ESP32)
#include <StationG3RadioSlot.h>
#endif

// Simulator targets do not include RadioLib, but board pin macros can still
// use its sentinel value. Keep the fallback local to this header.
#ifndef RADIOLIB_NC
#define RADIOLIB_NC (-1)
#define USER_GPIO_PIN_POLICY_UNDEF_RADIOLIB_NC
#endif

// This header is intentionally included only after target.h. The target's
// build-time pin definitions are the authoritative list of GPIOs claimed by
// the firmware. Boot-selectable radio slots/families reserve their pin union.
namespace UserGpioPinPolicy {

inline bool samePhysicalPin(uint8_t first, uint8_t second) {
#if defined(NRF52_PLATFORM) && defined(PINS_COUNT)
  // Arduino aliases can name the same nRF pad. Claiming an alias must never
  // bypass a reservation made using the other Arduino number.
  return first < PINS_COUNT && second < PINS_COUNT
      && g_ADigitalPinMap[first] < 48
      && g_ADigitalPinMap[first] == g_ADigitalPinMap[second];
#elif defined(STM32_PLATFORM) && defined(NUM_DIGITAL_PINS)
  return first < NUM_DIGITAL_PINS && second < NUM_DIGITAL_PINS
      && digitalPinToPinName(first) != NC
      && digitalPinToPinName(first) == digitalPinToPinName(second);
#else
  return first == second;
#endif
}

inline bool isFirmwareReserved(uint8_t pin, bool allow_user_button = false) {
#if defined(M5STACK_CARDPUTER_ADV)
  // Cardputer-Adv's built-in IR transmitter is connected even when its driver
  // is not compiled. Its display/audio/I2C aliases are listed below too.
  // https://docs.m5stack.com/en/core/Cardputer-Adv (PinMap)
  if (pin == 44) return true;
#endif
#if defined(LILYGO_TDECK)
  // Audio/microphone, trackball, touch/keyboard IRQs and microSD stay wired
  // even when their optional UI/audio drivers are absent. Vendor pin map:
  // github.com/Xinyuan-LilyGO/T-Deck/blob/master/examples/I2SPlay/utilities.h
  if (pin == 1 || pin == 2 || pin == 3 || pin == 5 || pin == 6 || pin == 7
      || pin == 14 || pin == 15 || pin == 16 || pin == 21 || pin == 39
      || pin == 46 || pin == 47 || pin == 48) return true;
#endif
#if defined(MESH_SENSECAP_INDICATOR)
  // SCIndicatorDisplay uses numeric RGB data/timing and serial-panel pins;
  // the onboard RP2040 font service also owns UART19/20. These remain wired
  // in a headless role and must not become expansion GPIOs.
  if (pin <= 21 || pin == 38 || pin == 39 || pin == 40 || pin == 41
      || pin == 42 || pin == 45 || pin == 48) return true;
#endif
#if defined(SEEED_WIO_TRACKER_L2)
  // WioTrackerL2Display's quad-SPI panel and Sound.cpp's ES8311 I2S bus use
  // numeric pins rather than the common display/audio target aliases.
  if ((pin >= 10 && pin <= 12) || pin == 16 || (pin >= 38 && pin <= 42)
      || pin == 46) return true;
#endif
#if defined(OTA_RAK15002_SD_STORE)
  // The exact RAK15002 recipe claims these WisBlock nets even when no card
  // is fitted: user GPIO must not defeat card detection or a later SD/NOR/
  // Ethernet handoff. WB_IO6 is Arduino pin 10, not GPIO number 6.
  if (pin == PIN_SPI_SCK || pin == WB_IO6 || pin == 26
      || pin == PIN_SPI_MISO || pin == PIN_SPI_MOSI) return true;
#endif
#if defined(STATION_G3_ESP32)
  // A module may be present in the inactive slot too. Never offer its pins
  // for user GPIO, even when the current boot selected the other slot.
  if (station_g3::isRadioPin(pin)) return true;
#endif
  // RadioLib 7 represents its disconnected-pin sentinel as unsigned
  // 0xFFFFFFFF. Use a signed type wide enough for that value as well as the
  // board definitions that use -1, then reject both forms with the byte
  // range check below. A 32-bit signed initializer is a narrowing error under
  // the GCC 14 toolchain used by Arduino-ESP32 3.x.
  static const int64_t reserved[] = {
    -1,

    // LoRa radio, RF switches, FEMs, and activity indicators.
#ifdef P_LORA_DIO_0
    P_LORA_DIO_0,
#endif
#ifdef P_LORA_DIO_1
    P_LORA_DIO_1,
#endif
#ifdef P_LORA_DIO_2
    P_LORA_DIO_2,
#endif
#ifdef P_LORA_DIO_3
    P_LORA_DIO_3,
#endif
#ifdef P_LORA_DIO_4
    P_LORA_DIO_4,
#endif
#ifdef P_LORA_DIO_5
    P_LORA_DIO_5,
#endif
#ifdef LORA_LR1121_DIO_1
    LORA_LR1121_DIO_1,
#endif
#ifdef LORA_SX1262_DIO_1
    LORA_SX1262_DIO_1,
#endif
#ifdef P_LORA_NSS
    P_LORA_NSS,
#endif
#ifdef P_LORA_RESET
    P_LORA_RESET,
#endif
#ifdef P_LORA_BUSY
    P_LORA_BUSY,
#endif
#ifdef P_LORA_SCLK
    P_LORA_SCLK,
#endif
#ifdef P_LORA_MISO
    P_LORA_MISO,
#endif
#ifdef P_LORA_MOSI
    P_LORA_MOSI,
#endif
#ifdef P_LORA_EN
    P_LORA_EN,
#endif
#ifdef P_LORA_PA_POWER
    P_LORA_PA_POWER,
#endif
#ifdef P_LORA_LF_PA_POWER
    P_LORA_LF_PA_POWER,
#endif
#ifdef P_LORA_HF_PA_POWER
    P_LORA_HF_PA_POWER,
#endif
#ifdef P_LORA_TX_LED
    P_LORA_TX_LED,
#endif
#ifdef P_LORA_TX_NEOPIXEL_LED
    P_LORA_TX_NEOPIXEL_LED,
#endif
#ifdef P_LORA_GC1109_PA_EN
    P_LORA_GC1109_PA_EN,
#endif
#ifdef P_LORA_GC1109_PA_TX_EN
    P_LORA_GC1109_PA_TX_EN,
#endif
#ifdef P_LORA_KCT8103L_PA_CSD
    P_LORA_KCT8103L_PA_CSD,
#endif
#ifdef P_LORA_KCT8103L_PA_CTX
    P_LORA_KCT8103L_PA_CTX,
#endif
#ifdef SX126X_POWER_EN
    SX126X_POWER_EN,
#endif
#ifdef SX126X_ANT_SW
    SX126X_ANT_SW,
#endif
#ifdef SX126X_RXEN
    SX126X_RXEN,
#endif
#ifdef SX126X_TXEN
    SX126X_TXEN,
#endif
#ifdef SX127X_RXEN
    SX127X_RXEN,
#endif
#ifdef SX127X_TXEN
    SX127X_TXEN,
#endif
#ifdef LORA_TX_BOOST_PIN
    LORA_TX_BOOST_PIN,
#endif
#ifdef LORA_KCT8103L_EN
    LORA_KCT8103L_EN,
#endif
#ifdef LORA_KCT8103L_TX_RX
    LORA_KCT8103L_TX_RX,
#endif
#ifdef P_PA1_EN
    P_PA1_EN,
#endif
#ifdef P_PRIMARY_LNA_EN
    P_PRIMARY_LNA_EN,
#endif
#ifdef LR1110_BUSY_PIN
    LR1110_BUSY_PIN,
#endif
#ifdef LR1110_GNSS_ANT_PIN
    LR1110_GNSS_ANT_PIN,
#endif
#ifdef LR1110_IRQ_PIN
    LR1110_IRQ_PIN,
#endif
#ifdef LR1110_NRESET_PIN
    LR1110_NRESET_PIN,
#endif
#ifdef LR1110_SPI_MISO_PIN
    LR1110_SPI_MISO_PIN,
#endif
#ifdef LR1110_SPI_MOSI_PIN
    LR1110_SPI_MOSI_PIN,
#endif
#ifdef LR1110_SPI_NSS_PIN
    LR1110_SPI_NSS_PIN,
#endif
#ifdef LR1110_SPI_SCK_PIN
    LR1110_SPI_SCK_PIN,
#endif

    // I2C buses actively selected by the board and sensor firmware.
#ifdef PIN_BOARD_SDA
    PIN_BOARD_SDA,
#endif
#ifdef PIN_BOARD_SCL
    PIN_BOARD_SCL,
#endif
#ifdef PIN_BOARD_SDA1
    PIN_BOARD_SDA1,
#endif
#ifdef PIN_BOARD_SCL1
    PIN_BOARD_SCL1,
#endif
#ifdef PIN_I2C_INTERNAL_SDA
    PIN_I2C_INTERNAL_SDA,
#endif
#ifdef PIN_I2C_INTERNAL_SCL
    PIN_I2C_INTERNAL_SCL,
#endif
#ifdef PIN_WIRE_SDA
    PIN_WIRE_SDA,
#endif
#ifdef PIN_WIRE_SCL
    PIN_WIRE_SCL,
#endif
#ifdef PIN_WIRE0_SDA
    PIN_WIRE0_SDA,
#endif
#ifdef PIN_WIRE0_SCL
    PIN_WIRE0_SCL,
#endif
#if ENV_HAS_SECONDARY_I2C
    ENV_PIN_SDA,
    ENV_PIN_SCL,
#endif
#ifdef I2C_SDA
    I2C_SDA,
#endif
#ifdef I2C_SCL
    I2C_SCL,
#endif
#ifdef I2C_SDA1
    I2C_SDA1,
#endif
#ifdef I2C_SCL1
    I2C_SCL1,
#endif
#ifdef RTC_SDA
    RTC_SDA,
#endif
#ifdef RTC_SCL
    RTC_SCL,
#endif
#ifdef BQ4050_SDA_PIN
    BQ4050_SDA_PIN,
#endif
#ifdef BQ4050_SCL_PIN
    BQ4050_SCL_PIN,
#endif

    // GPS and UART bridge connections.
#ifdef PIN_GPS_RX
    PIN_GPS_RX,
#endif
#ifdef PIN_GPS_TX
    PIN_GPS_TX,
#endif
#ifdef PIN_GPS_EN
    PIN_GPS_EN,
#endif
#ifdef PIN_GPS_RESET
    PIN_GPS_RESET,
#endif
#ifdef PIN_GPS_PPS
    PIN_GPS_PPS,
#endif
#ifdef PIN_GPS_1PPS
    PIN_GPS_1PPS,
#endif
#ifdef PIN_GPS_POWER
    PIN_GPS_POWER,
#endif
#ifdef PIN_GPS_STANDBY
    PIN_GPS_STANDBY,
#endif
#ifdef PIN_GPS_SWITCH
    PIN_GPS_SWITCH,
#endif
#ifdef GPS_RX
    GPS_RX,
#endif
#ifdef GPS_TX
    GPS_TX,
#endif
#ifdef GPS_EN
    GPS_EN,
#endif
#ifdef GPS_RESET
    GPS_RESET,
#endif
#ifdef GPS_RESETB
    GPS_RESETB,
#endif
#ifdef GPS_PPS
    GPS_PPS,
#endif
#ifdef GPS_RX_PIN
    GPS_RX_PIN,
#endif
#ifdef GPS_TX_PIN
    GPS_TX_PIN,
#endif
#ifdef GPS_UART_RX
    GPS_UART_RX,
#endif
#ifdef GPS_UART_TX
    GPS_UART_TX,
#endif
#ifdef GPS_RTC_INT
    GPS_RTC_INT,
#endif
#ifdef GPS_SLEEP_INT
    GPS_SLEEP_INT,
#endif
#ifdef GPS_VRTC_EN
    GPS_VRTC_EN,
#endif
#ifdef WITH_RS232_BRIDGE_RX
    WITH_RS232_BRIDGE_RX,
#endif
#ifdef WITH_RS232_BRIDGE_TX
    WITH_RS232_BRIDGE_TX,
#endif
#ifdef WITH_RS232_BRIDGE_ALT_RX
    WITH_RS232_BRIDGE_ALT_RX,
#endif
#ifdef WITH_RS232_BRIDGE_ALT_TX
    WITH_RS232_BRIDGE_ALT_TX,
#endif
#ifdef PIN_SERIAL_RX
    PIN_SERIAL_RX,
#endif
#ifdef PIN_SERIAL_TX
    PIN_SERIAL_TX,
#endif
#ifdef SERIAL_RX
    SERIAL_RX,
#endif
#ifdef SERIAL_TX
    SERIAL_TX,
#endif

    // Rotary channels, LEDs, displays, touch, buzzers, and vibration motors.
#ifdef PIN_BUTTON1_A
    PIN_BUTTON1_A,
#endif
#ifdef PIN_BUTTON1_B
    PIN_BUTTON1_B,
#endif
#ifdef ENCODER_LEFT
    ENCODER_LEFT,
#endif
#ifdef ENCODER_RIGHT
    ENCODER_RIGHT,
#endif
#ifdef PIN_STATUS_LED
    PIN_STATUS_LED,
#endif
#ifdef PIN_STATUS_LED_R
    PIN_STATUS_LED_R,
#endif
#ifdef PIN_STATUS_LED_G
    PIN_STATUS_LED_G,
#endif
#ifdef PIN_STATUS_LED_B
    PIN_STATUS_LED_B,
#endif
#ifdef PIN_LED
    PIN_LED,
#endif
#ifdef PIN_LED1
    PIN_LED1,
#endif
#ifdef PIN_LED2
    PIN_LED2,
#endif
#ifdef PIN_LED3
    PIN_LED3,
#endif
#ifdef PIN_LED4
    PIN_LED4,
#endif
#ifdef PIN_LED_R
    PIN_LED_R,
#endif
#ifdef PIN_LED_G
    PIN_LED_G,
#endif
#ifdef PIN_LED_B
    PIN_LED_B,
#endif
#ifdef PIN_LED_RED
    PIN_LED_RED,
#endif
#ifdef PIN_LED_GREEN
    PIN_LED_GREEN,
#endif
#ifdef PIN_LED_BLUE
    PIN_LED_BLUE,
#endif
#ifdef LED_BUILTIN
    LED_BUILTIN,
#endif
#ifdef LED_PIN
    LED_PIN,
#endif
#ifdef LED_POWER
    LED_POWER,
#endif
#ifdef LED_GREEN
    LED_GREEN,
#endif
#ifdef LED_BLUE
    LED_BLUE,
#endif
#ifdef LED_RED
    LED_RED,
#endif
#ifdef LED_WHITE
    LED_WHITE,
#endif
#ifdef LED_STATUS
    LED_STATUS,
#endif
#ifdef LED_BAT1
    LED_BAT1,
#endif
#ifdef LED_BAT2
    LED_BAT2,
#endif
#ifdef LED_BAT3
    LED_BAT3,
#endif
#ifdef LED_BAT4
    LED_BAT4,
#endif
#ifdef NEOPIXEL_DATA
    NEOPIXEL_DATA,
#endif
#ifdef PIN_NEOPIXEL
    PIN_NEOPIXEL,
#endif
#ifdef WS2812_PIN
    WS2812_PIN,
#endif
#ifdef PIN_BUZZER
    PIN_BUZZER,
#endif
#ifdef PIN_BUZZER_EN
    PIN_BUZZER_EN,
#endif
#ifdef PIN_BUZZER_VOLTAGE_MULTIPLIER_1
    PIN_BUZZER_VOLTAGE_MULTIPLIER_1,
#endif
#ifdef PIN_BUZZER_VOLTAGE_MULTIPLIER_2
    PIN_BUZZER_VOLTAGE_MULTIPLIER_2,
#endif
#ifdef BUZZER_PIN
    BUZZER_PIN,
#endif
#ifdef BUZZER_EN
    BUZZER_EN,
#endif
#ifdef PIN_VIBRATION
    PIN_VIBRATION,
#endif
#ifdef PIN_OLED_RESET
    PIN_OLED_RESET,
#endif
#ifdef PIN_TFT_CS
    PIN_TFT_CS,
#endif
#ifdef PIN_TFT_DC
    PIN_TFT_DC,
#endif
#ifdef PIN_TFT_RST
    PIN_TFT_RST,
#endif
#ifdef PIN_TFT_SCL
    PIN_TFT_SCL,
#endif
#ifdef PIN_TFT_SCK
    PIN_TFT_SCK,
#endif
#ifdef PIN_TFT_MOSI
    PIN_TFT_MOSI,
#endif
#ifdef PIN_TFT_SDA
    PIN_TFT_SDA,
#endif
#ifdef PIN_TFT_MISO
    PIN_TFT_MISO,
#endif
#ifdef PIN_TFT_LEDA_CTL
    PIN_TFT_LEDA_CTL,
#endif
#ifdef PIN_TFT_VDD_CTL
    PIN_TFT_VDD_CTL,
#endif
#ifdef PIN_TFT_BL
    PIN_TFT_BL,
#endif
#ifdef PIN_TFT_EN
    PIN_TFT_EN,
#endif
#ifdef PIN_AUDIO_SCLK
    PIN_AUDIO_SCLK,
#endif
#ifdef PIN_AUDIO_ASDOUT
    PIN_AUDIO_ASDOUT,
#endif
#ifdef PIN_AUDIO_LRCK
    PIN_AUDIO_LRCK,
#endif
#ifdef PIN_AUDIO_DSDIN
    PIN_AUDIO_DSDIN,
#endif
#ifdef PIN_KEYBOARD_INT
    PIN_KEYBOARD_INT,
#endif
#ifdef ST7789_CS
    ST7789_CS,
#endif
#ifdef ST7789_RS
    ST7789_RS,
#endif
#ifdef PIN_TOUCH_RST
    PIN_TOUCH_RST,
#endif
#ifdef DISP_BUSY
    DISP_BUSY,
#endif
#ifdef DISP_CS
    DISP_CS,
#endif
#ifdef DISP_DC
    DISP_DC,
#endif
#ifdef DISP_MISO
    DISP_MISO,
#endif
#ifdef DISP_MOSI
    DISP_MOSI,
#endif
#ifdef DISP_POWER
    DISP_POWER,
#endif
#ifdef DISP_EN
    DISP_EN,
#endif
#ifdef DISP_RST
    DISP_RST,
#endif
#ifdef DISP_SCLK
    DISP_SCLK,
#endif
#ifdef DISP_BACKLIGHT
    DISP_BACKLIGHT,
#endif
#ifdef PIN_DISPLAY_BUSY
    PIN_DISPLAY_BUSY,
#endif
#ifdef PIN_DISPLAY_CS
    PIN_DISPLAY_CS,
#endif
#ifdef PIN_DISPLAY_DC
    PIN_DISPLAY_DC,
#endif
#ifdef PIN_DISPLAY_MISO
    PIN_DISPLAY_MISO,
#endif
#ifdef PIN_DISPLAY_MOSI
    PIN_DISPLAY_MOSI,
#endif
#ifdef PIN_DISPLAY_RST
    PIN_DISPLAY_RST,
#endif
#ifdef PIN_DISPLAY_SCLK
    PIN_DISPLAY_SCLK,
#endif

    // Power rails, battery measurement, storage, sensors, and watchdogs.
#ifdef PIN_VEXT_EN
    PIN_VEXT_EN,
#endif
#ifdef PIN_3V3_EN
    PIN_3V3_EN,
#endif
#ifdef PIN_3V3_ACC_EN
    PIN_3V3_ACC_EN,
#endif
#ifdef PIN_EXT_VCC
    PIN_EXT_VCC,
#endif
#ifdef VEXT_ENABLE
    VEXT_ENABLE,
#endif
#ifdef PIN_PERF_POWERON
    PIN_PERF_POWERON,
#endif
#ifdef PIN_PWR_EN
    PIN_PWR_EN,
#endif
#ifdef PIN_SOFT_SHUTDOWN
    PIN_SOFT_SHUTDOWN,
#endif
#ifdef PIN_DCDC_EN_MCU_HOLD
    PIN_DCDC_EN_MCU_HOLD,
#endif
#ifdef RT9080_EN
    RT9080_EN,
#endif
#ifdef I2C_POWER
    I2C_POWER,
#endif
#ifdef SCREEN_12V_ENABLE
    SCREEN_12V_ENABLE,
#endif
#ifdef PIN_DRV_EN
    PIN_DRV_EN,
#endif
#ifdef PIN_BAT_CTL
    PIN_BAT_CTL,
#endif
#ifdef PIN_BAT_CTRL
    PIN_BAT_CTRL,
#endif
#ifdef PIN_BAT_CHG
    PIN_BAT_CHG,
#endif
#ifdef PIN_BATTERY_CHARGING
    PIN_BATTERY_CHARGING,
#endif
#ifdef PIN_CHARGER_FAULT
    PIN_CHARGER_FAULT,
#endif
#ifdef EXT_CHRG_DETECT
    EXT_CHRG_DETECT,
#endif
#ifdef EXT_PWR_DETECT
    EXT_PWR_DETECT,
#endif
#ifdef PIN_VBAT_READ
    PIN_VBAT_READ,
#endif
#ifdef PIN_VBAT
    PIN_VBAT,
#endif
#ifdef VBAT_ENABLE
    VBAT_ENABLE,
#endif
#ifdef PIN_CHARGING_CURRENT
    PIN_CHARGING_CURRENT,
#endif
#ifdef BAT_NOT_CHARGING
    BAT_NOT_CHARGING,
#endif
#ifdef ADC_EN
    ADC_EN,
#endif
#ifdef PIN_VBAT_MEAS_EN
    PIN_VBAT_MEAS_EN,
#endif
#ifdef PIN_ADC_CTRL
    PIN_ADC_CTRL,
#endif
#ifdef PIN_BAT_ADC_EN
    PIN_BAT_ADC_EN,
#endif
#ifdef BATTERY_MEASUREMENT_CONTROL
    BATTERY_MEASUREMENT_CONTROL,
#endif
#ifdef NTC_PIN
    NTC_PIN,
#endif
#ifdef BATTERY_PIN
    BATTERY_PIN,
#endif
#ifdef BATTERY_ADC_DATA
    BATTERY_ADC_DATA,
#endif
#ifdef BAT_POWER
    BAT_POWER,
#endif
#ifdef EEPROM_POWER
    EEPROM_POWER,
#endif
#ifdef SDCARD_CS
    SDCARD_CS,
#endif
#ifdef PIN_SD_CS
    PIN_SD_CS,
#endif
    // Board flash remains attached even when an optional storage backend is
    // disabled. Never drive its data or chip-select lines as user outputs.
#if !defined(RAK_3401) && !defined(RAK_4631)
    // These WisBlock variants define hypothetical QSPI nets despite having
    // no onboard flash. Their actual configured OTA storage is reserved below.
#ifdef PIN_QSPI_SCK
    PIN_QSPI_SCK,
#endif
#ifdef PIN_QSPI_CS
    PIN_QSPI_CS,
#endif
#ifdef PIN_QSPI_IO0
    PIN_QSPI_IO0,
#endif
#ifdef PIN_QSPI_IO1
    PIN_QSPI_IO1,
#endif
#ifdef PIN_QSPI_IO2
    PIN_QSPI_IO2,
#endif
#ifdef PIN_QSPI_IO3
    PIN_QSPI_IO3,
#endif
#endif
#ifdef SS_FLASH
    SS_FLASH,
#endif
#ifdef PIN_TXCO
    PIN_TXCO,
#endif
#ifdef PIN_SPI_MISO
    PIN_SPI_MISO,
#endif
#ifdef PIN_SPI_MOSI
    PIN_SPI_MOSI,
#endif
#ifdef PIN_SPI_SCK
    PIN_SPI_SCK,
#endif
#ifdef PIN_SPI_NSS
    PIN_SPI_NSS,
#endif
#if defined(NRF52_PLATFORM) && defined(OTA_QSPI_STORE)
    // The raw NOR backend owns these configured pads even while its bus is
    // released or no matching chip was found. User GPIO/timers must not
    // defeat a later OTA storage claim or drive an installed flash's CS.
#ifdef OTA_QSPI_SCK_ARDUINO_PIN
    OTA_QSPI_SCK_ARDUINO_PIN,
#endif
#ifdef OTA_QSPI_CS_ARDUINO_PIN
    OTA_QSPI_CS_ARDUINO_PIN,
#endif
#ifdef OTA_QSPI_IO0_ARDUINO_PIN
    OTA_QSPI_IO0_ARDUINO_PIN,
#endif
#ifdef OTA_QSPI_IO1_ARDUINO_PIN
    OTA_QSPI_IO1_ARDUINO_PIN,
#endif
#if defined(OTA_QSPI_IO2_ARDUINO_PIN) && !defined(OTA_QSPI_IO2_NOT_CONNECTED)
    OTA_QSPI_IO2_ARDUINO_PIN,
#endif
#if defined(OTA_QSPI_IO3_ARDUINO_PIN) && !defined(OTA_QSPI_IO3_NOT_CONNECTED)
    OTA_QSPI_IO3_ARDUINO_PIN,
#endif
#endif
#ifdef SENSOR_POWER_CTRL_PIN
    SENSOR_POWER_CTRL_PIN,
#endif
#ifdef SENSOR_POWER_PIN
    SENSOR_POWER_PIN,
#endif
#ifdef SENSOR_RST_PIN
    SENSOR_RST_PIN,
#endif
#ifdef SENSOR_INT_PIN
    SENSOR_INT_PIN,
#endif
#ifdef PIN_SENSOR_EN
    PIN_SENSOR_EN,
#endif
#ifdef PIN_LSM6DS3TR_C_INT1
    PIN_LSM6DS3TR_C_INT1,
#endif
#ifdef PIN_LSM6DS3TR_C_POWER
    PIN_LSM6DS3TR_C_POWER,
#endif
#ifdef LIS3DH_INT_PIN_1
    LIS3DH_INT_PIN_1,
#endif
#ifdef LIS3DH_INT_PIN_2
    LIS3DH_INT_PIN_2,
#endif
#ifdef QMA_6100P_INT_PIN
    QMA_6100P_INT_PIN,
#endif
#ifdef EXTERNAL_WATCHDOG_DONE_PIN
    EXTERNAL_WATCHDOG_DONE_PIN,
#endif
#ifdef EXTERNAL_WATCHDOG_WAKE_PIN
    EXTERNAL_WATCHDOG_WAKE_PIN,
#endif
#ifdef PIN_PMU_IRQ
    PIN_PMU_IRQ,
#endif
#ifdef IO_EXPANDER_IRQ
    IO_EXPANDER_IRQ,
#endif
#ifdef BQ4050_EMERGENCY_SHUTDOWN_PIN
    BQ4050_EMERGENCY_SHUTDOWN_PIN,
#endif

    // Ethernet and board-specific direct I/O.
#ifdef ETH_CS_PIN
    ETH_CS_PIN,
#endif
#ifdef ETH_INT_PIN
    ETH_INT_PIN,
#endif
#ifdef ETH_MISO_PIN
    ETH_MISO_PIN,
#endif
#ifdef ETH_MOSI_PIN
    ETH_MOSI_PIN,
#endif
#ifdef ETH_SCLK_PIN
    ETH_SCLK_PIN,
#endif
#ifdef PIN_ETHERNET_RESET
    PIN_ETHERNET_RESET,
#endif
#ifdef PIN_BOARD_DIGITAL_IN
    PIN_BOARD_DIGITAL_IN,
#endif
#ifdef PIN_BOARD_RELAY_CH1
    PIN_BOARD_RELAY_CH1,
#endif
#ifdef PIN_BOARD_RELAY_CH2
    PIN_BOARD_RELAY_CH2,
#endif
#ifdef FAN_CTRL_PIN
    FAN_CTRL_PIN,
#endif
#ifdef RF_PA_DETECT_PIN
    RF_PA_DETECT_PIN,
#endif

    // Escape hatch for pins used numerically in a target implementation.
#ifdef USER_GPIO_RESERVED_PINS
    USER_GPIO_RESERVED_PINS,
#endif
  };

  for (size_t i = 0; i < sizeof(reserved) / sizeof(reserved[0]); i++) {
    if (reserved[i] >= 0 && reserved[i] < UINT8_MAX
        && samePhysicalPin(pin, (uint8_t)reserved[i])) return true;
  }
  // Reusing the existing user button as an encoder's press switch is allowed
  // only for its exact Arduino pin number. All nonbutton hardware above must
  // still be checked, even when a target accidentally aliases those signals.
#ifdef PIN_USER_BTN
  const int64_t user_button = PIN_USER_BTN;
  if (allow_user_button && user_button >= 0 && user_button < UINT8_MAX
      && pin == static_cast<uint8_t>(user_button)) return false;
#else
  (void)allow_user_button;
#endif
  static const int64_t buttons[] = {
    -1,
#ifdef PIN_USER_BTN
    PIN_USER_BTN,
#endif
#ifdef PIN_USER_BTN_ANA
    PIN_USER_BTN_ANA,
#endif
#ifdef PIN_BUTTON1
    PIN_BUTTON1,
#endif
#ifdef PIN_BUTTON2
    PIN_BUTTON2,
#endif
#ifdef PIN_BUTTON3
    PIN_BUTTON3,
#endif
#ifdef PIN_BUTTON4
    PIN_BUTTON4,
#endif
#ifdef PIN_BUTTON5
    PIN_BUTTON5,
#endif
#ifdef PIN_BUTTON6
    PIN_BUTTON6,
#endif
#ifdef ENCODER_PRESS
    ENCODER_PRESS,
#endif
#ifdef BUTTON_PIN
    BUTTON_PIN,
#endif
#ifdef BUTTON_PIN2
    BUTTON_PIN2,
#endif
#ifdef PIN_BACK_BTN
    PIN_BACK_BTN,
#endif
#ifdef PIN_SIDE_BUTTON
    PIN_SIDE_BUTTON,
#endif
#ifdef PIN_PWRBTN
    PIN_PWRBTN,
#endif
#ifdef PIN_TOP_BUTTON
    PIN_TOP_BUTTON,
#endif
#ifdef TTP223_KEY
    TTP223_KEY,
#endif
  };
  for (size_t i = 0; i < sizeof(buttons) / sizeof(buttons[0]); ++i) {
    if (buttons[i] >= 0 && buttons[i] < UINT8_MAX
        && samePhysicalPin(pin, static_cast<uint8_t>(buttons[i]))) return true;
  }
  return false;
}

} // namespace UserGpioPinPolicy

#ifdef USER_GPIO_PIN_POLICY_UNDEF_RADIOLIB_NC
#undef RADIOLIB_NC
#undef USER_GPIO_PIN_POLICY_UNDEF_RADIOLIB_NC
#endif
