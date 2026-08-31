#include <Arduino.h>
#include <Wire.h>
#include "nrf_gpio.h"
#include "nrf_sdm.h"
#include "nrf_soc.h"

#include "RAK4631Board.h"

#ifdef ETHERNET_ENABLED
// Drive WB_IO2 HIGH as early as possible using direct register access.
// WB_IO2 (P1.02, Arduino pin 34) controls the WisBlock slot power switch.
// With POE through RAK13800, this must be enabled before the framework
// initializes or the board will brownout from insufficient power delivery.
// Priority 102 runs just after SystemInit.
static void __attribute__((constructor(102))) rak4631_early_poe_power() {
  nrf_gpio_cfg_output(NRF_GPIO_PIN_MAP(1, 2));  // WB_IO2 = P1.02
  nrf_gpio_pin_set(NRF_GPIO_PIN_MAP(1, 2));
}
#endif

#ifdef NRF52_POWER_MANAGEMENT
// Static configuration for power management
// Values set in variant.h defines
const PowerMgtConfig power_config = {
  .lpcomp_ain_channel = PWRMGT_LPCOMP_AIN,
  .lpcomp_refsel = PWRMGT_LPCOMP_REFSEL,
  .voltage_bootlock = PWRMGT_VOLTAGE_BOOTLOCK
};

void RAK4631Board::initiateShutdown(uint8_t reason) {
  // Disable LoRa module power before shutdown
  digitalWrite(SX126X_POWER_EN, LOW);

  if (reason == SHUTDOWN_REASON_LOW_VOLTAGE ||
      reason == SHUTDOWN_REASON_BOOT_PROTECT) {
    configureVoltageWake(power_config.lpcomp_ain_channel, power_config.lpcomp_refsel);
  }

  enterSystemOff(reason);
}
#endif // NRF52_POWER_MANAGEMENT

void RAK4631Board::begin() {
#ifdef RAK4631_COMPANION_FORCE_LDO
  // Some RAK4631 battery/board combinations have proved unstable with the
  // nRF52 DC/DC enabled. Explicitly select the LDO for companion firmware;
  // merely skipping NRF52BoardDCDC::begin() would inherit any prior state.
  NRF52Board::begin();
  uint8_t sd_enabled = 0;
  sd_softdevice_is_enabled(&sd_enabled);
  if (sd_enabled) {
    sd_power_dcdc_mode_set(NRF_POWER_DCDC_DISABLE);
  } else {
    NRF_POWER->DCDCEN = 0;
  }
#else
  NRF52BoardDCDC::begin();
#endif
  pinMode(PIN_VBAT_READ, INPUT);
#ifdef PIN_USER_BTN
  pinMode(PIN_USER_BTN, INPUT_PULLUP);
#endif

#ifdef PIN_USER_BTN_ANA
  pinMode(PIN_USER_BTN_ANA, INPUT_PULLUP);
#endif


#if defined(PIN_BOARD_SDA) && defined(PIN_BOARD_SCL)
  Wire.setPins(PIN_BOARD_SDA, PIN_BOARD_SCL);
#endif

  Wire.begin();

  // WB_IO2 is the shared 3V3_S supply for WisBlock sensor/interface slots.
  // Keep it enabled before GPS and environmental discovery; treating it as a
  // GPS-only switch makes the subsequent I2C scan see an unpowered bus.
  pinMode(WB_IO2, OUTPUT);
  digitalWrite(WB_IO2, HIGH);

  pinMode(SX126X_POWER_EN, OUTPUT);
#ifdef NRF52_POWER_MANAGEMENT
  // Boot voltage protection check (may not return if voltage too low)
  // We need to call this after we configure SX126X_POWER_EN as output but before we pull high
  checkBootVoltage(&power_config);
#endif
  digitalWrite(SX126X_POWER_EN, HIGH);
  delay(10);   // give sx1262 some time to power up
}
