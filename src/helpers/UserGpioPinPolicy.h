#pragma once

#include <stddef.h>
#include <stdint.h>

// Simulator targets do not include RadioLib, but board pin macros can still
// use its sentinel value. Keep the fallback local to this header.
#ifndef RADIOLIB_NC
#define RADIOLIB_NC (-1)
#define USER_GPIO_PIN_POLICY_UNDEF_RADIOLIB_NC
#endif

// This header is intentionally included only after target.h. The target's
// build-time pin definitions are the authoritative list of GPIOs claimed by
// the firmware in that particular image.
namespace UserGpioPinPolicy {

inline bool isFirmwareReserved(uint8_t pin) {
  static const int32_t reserved[] = {
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
#ifdef PIN_WIRE_SDA
    PIN_WIRE_SDA,
#endif
#ifdef PIN_WIRE_SCL
    PIN_WIRE_SCL,
#endif
#ifdef ENV_PIN_SDA
    ENV_PIN_SDA,
#endif
#ifdef ENV_PIN_SCL
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

    // Buttons, LEDs, displays, touch, buzzers, and vibration motors.
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
#ifdef PIN_STATUS_LED
    PIN_STATUS_LED,
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
#ifdef PIN_BAT_CTL
    PIN_BAT_CTL,
#endif
#ifdef PIN_BAT_CTRL
    PIN_BAT_CTRL,
#endif
#ifdef PIN_BAT_CHG
    PIN_BAT_CHG,
#endif
#ifdef PIN_VBAT_READ
    PIN_VBAT_READ,
#endif
#ifdef PIN_VBAT_MEAS_EN
    PIN_VBAT_MEAS_EN,
#endif
#ifdef PIN_ADC_CTRL
    PIN_ADC_CTRL,
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
    if (reserved[i] >= 0 && reserved[i] <= 63 && pin == (uint8_t)reserved[i]) return true;
  }
  return false;
}

} // namespace UserGpioPinPolicy

#ifdef USER_GPIO_PIN_POLICY_UNDEF_RADIOLIB_NC
#undef RADIOLIB_NC
#undef USER_GPIO_PIN_POLICY_UNDEF_RADIOLIB_NC
#endif
