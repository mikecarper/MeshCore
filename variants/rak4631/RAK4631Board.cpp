#include <Arduino.h>
#include <Wire.h>
#include "nrf_gpio.h"
#include "nrf_sdm.h"
#include "nrf_soc.h"

#include "RAK4631Board.h"
#include "target.h"
#include "../../src/helpers/nrf52/SoftDeviceState.h"
#include "../../src/helpers/nrf52/TerminalPeripheralShutdown.h"

#ifdef ETHERNET_ENABLED
// Drive WB_IO2 HIGH as early as possible using direct register access.
// WB_IO2 (P1.02, Arduino pin 34) controls the WisBlock slot power switch.
// Preserve the existing early Ethernet slot-enable timing. RAK13800 PoE's
// 5V feeds VCC_IN separately; IO2 is not the core's upstream power gate.
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
  // Disable LoRa RF-switch power (SX1262 supply itself is not GPIO-gated).
  digitalWrite(SX126X_POWER_EN, LOW);

  if (reason == SHUTDOWN_REASON_LOW_VOLTAGE ||
      reason == SHUTDOWN_REASON_BOOT_PROTECT) {
    configureVoltageWake(power_config.lpcomp_ain_channel, power_config.lpcomp_refsel);
  }

  enterSystemOff(reason);
}
#endif // NRF52_POWER_MANAGEMENT

void RAK4631Board::shutdownPeripherals() {
  // Explicit power-off/deep-sleep only, never ordinary event-driven sleep.
  // Finish display/radio/GPS/crypto/USB shutdown before removing slot power.
  // A combined owner's release hook may restore the radio. Finish it before
  // common shutdown sleeps that radio. Slow DHCP may exceed the bounded wait;
  // in that case do not remove power under a still-running SPI owner.
  const bool ethernet_stopped = mesh::nrf52::terminalEthernetShutdown();
  const bool storage_power_safe = sensors.canRemoveSharedStoragePowerForDeepSleep();
  NRF52Board::shutdownPeripherals();
#if ENV_INCLUDE_GPS && defined(PIN_GPS_TX) && defined(PIN_GPS_RX)
  // Provider stop() does not end UARTE. An unstarted Adafruit Uart::end()
  // can wait forever, so use its real begun-state operator before teardown.
  if (storage_power_safe) {
    if (Serial1) Serial1.end();
    pinMode(PIN_GPS_TX, INPUT);
    pinMode(PIN_GPS_RX, INPUT);
  }
#endif
  // Wire.begin() leaves MCU pull-ups enabled. Stop the controller before
  // removing sensor power, then release its pads without internal pulls.
  // Base-board pull-ups to unswitched VDD are a separate hardware path.
  Wire.end();
  pinMode(PIN_WIRE_SDA, INPUT);
  pinMode(PIN_WIRE_SCL, INPUT);
  digitalWrite(SX126X_POWER_EN, LOW);
  pinMode(SX126X_POWER_EN, OUTPUT);
  // RAK19007 Q7 switches only 3V3_S. RAK13800/RAK19018 PoE feeds the base
  // separately through IO connector pin 9 (VCC_IN), not this switched rail.
  // Ethernet capability therefore must not keep every fitted sensor awake
  // after explicit power-off, even in combined companion builds.
  // A detected NOR may retain a pending/unknown-WIP operation or driven CS31
  // and header pads. Keep it powered until a dedicated storage handoff exists.
  if (ethernet_stopped && storage_power_safe) {
    digitalWrite(WB_IO2, LOW);
    pinMode(WB_IO2, OUTPUT);
  }
}

void RAK4631Board::begin() {
#ifdef RAK4631_COMPANION_FORCE_LDO
  // Some RAK4631 battery/board combinations have proved unstable with the
  // nRF52 DC/DC enabled. Explicitly select the LDO for companion firmware;
  // merely skipping NRF52BoardDCDC::begin() would inherit any prior state.
  NRF52Board::begin();
  uint8_t sd_enabled = 0;
  mesh_nrf52::softdeviceIsEnabled(sd_enabled);
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
