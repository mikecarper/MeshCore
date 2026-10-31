#include <Arduino.h>
#include <nrf_gpio.h>
#include "target.h"
#include <helpers/radiolib/TunableRadioHal.h>
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
#include <helpers/radiolib/PortableLoRaChipProbe.h>
#endif
#include "variant.h"

MuziBaseBoard board;

static TunableRadioHal radio_hal(SPI);
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
static Module sx1262_module(&radio_hal, P_LORA_NSS, LORA_SX1262_DIO_1,
                            P_LORA_RESET, P_LORA_BUSY);
static Module lr1121_module(&radio_hal, P_LORA_NSS, LORA_LR1121_DIO_1,
                            P_LORA_RESET, P_LORA_BUSY);
UniversalLoRaRadio radio_driver(sx1262_module, sx1262_module, board, &lr1121_module);
#else
RADIO_CLASS radio = new Module(&radio_hal, P_LORA_NSS, P_LORA_DIO_1, P_LORA_RESET, P_LORA_BUSY);

WRAPPER_CLASS radio_driver(radio, board);
#endif

VolatileRTCClock fallback_clock;
AutoDiscoverRTCClock rtc_clock(fallback_clock);
#if ENV_INCLUDE_GPS
  #include <helpers/sensors/MicroNMEALocationProvider.h>
  MicroNMEALocationProvider nmea = MicroNMEALocationProvider(Serial1, &rtc_clock);
  MuziBaseSensorManager sensors = MuziBaseSensorManager(nmea);
#else
  MuziBaseSensorManager sensors;
#endif

// user button for the N-click power-off. single-click mode (multiclick off) so
// each press is its own CLICK; we count them ourselves. reliable across the
// idle-sleep loop because it's the same MomentaryButton the UI navigates with.
static MomentaryButton pwr_btn(PIN_USER_BTN, 0, true, true, false);

// power off, arming the user button as a wake source first so a later press
// turns it back on. wait for the button to release (the 5th click already fires
// on release, so this returns quickly), then set nRF52 GPIO SENSE (press = LOW);
// it survives into SYSTEMOFF because shutdownPeripherals() doesn't touch this pin.
static void muziPowerOff() {
  uint32_t t0 = millis();
  while (digitalRead(PIN_USER_BTN) == USER_BTN_PRESSED && (millis() - t0) < 3000) { delay(5); }
  delay(50);   // settle
  nrf_gpio_cfg_sense_input(g_ADigitalPinMap[PIN_USER_BTN], NRF_GPIO_PIN_PULLUP, NRF_GPIO_PIN_SENSE_LOW);
  board.powerOff();   // shutdownPeripherals() + SYSTEMOFF
}

bool MuziBaseSensorManager::begin() {
  pwr_btn.begin();
#if ENV_INCLUDE_GPS
  // The accessory's mode-switch connector is open on a bare Base. A fixed
  // pulldown prevents that open input from requesting GPS or claiming it is
  // present; the shared manager still requires real UART discovery.
  pinMode(PIN_GPS_SWITCH, INPUT_PULLDOWN);
  _last_gps_sw = digitalRead(PIN_GPS_SWITCH);   // initial gps state from the switch
#endif
  bool ok = EnvironmentSensorManager::begin();
#if ENV_INCLUDE_GPS
  setSettingValue("gps", _last_gps_sw == HIGH ? "1" : "0");
  if (gps_detected) {
    if (_last_gps_sw == HIGH) start_gps(); else stop_gps();
  }
#endif
  return ok;
}

void MuziBaseSensorManager::loop() {
  // user button: USER_BTN_POWER_OFF_CLICKS quick clicks -> power off
  if (pwr_btn.check() == BUTTON_EVENT_CLICK) {
    unsigned long t = millis();
    if (t - _pwr_last_click > 2000) _pwr_clicks = 0;   // restart if too slow
    _pwr_last_click = t;
    if (++_pwr_clicks >= USER_BTN_POWER_OFF_CLICKS) muziPowerOff();
  }
#if ENV_INCLUDE_GPS
  unsigned long now = millis();
  if (now > _next_sw_check) {   // check the mode switch ~once a sec
    _next_sw_check = now + 1000;
    int sw = digitalRead(PIN_GPS_SWITCH);
    if (sw != _last_gps_sw) {
      _last_gps_sw = sw;
      setSettingValue("gps", sw == HIGH ? "1" : "0");
      // An absent optional receiver must never become active merely because
      // a switch moved. Discovery owns the pending UART/power transition.
      if (gps_detected) {
        if (sw == HIGH) start_gps(); else stop_gps();
      }
    }
  }
#endif
  EnvironmentSensorManager::loop();
}

#ifdef DISPLAY_CLASS
  DISPLAY_CLASS display;
  MomentaryButton user_btn(PIN_USER_BTN, 1000, true, false, false);
  MomentaryButton joystick_left(JOYSTICK_LEFT, 1000, true, false, false);
  MomentaryButton joystick_right(JOYSTICK_RIGHT, 1000, true, false, false);
  MomentaryButton back_btn(PIN_BACK_BTN, 1000, true, false, true);
#endif

#if defined(USE_LR1121)
  #ifndef LORA_CR
    #define LORA_CR      5
  #endif

  #ifdef RF_SWITCH_TABLE
  static const uint32_t rfswitch_dios[Module::RFSWITCH_MAX_PINS] = {
    RADIOLIB_LR11X0_DIO5,
    RADIOLIB_LR11X0_DIO6,
    RADIOLIB_NC
  };

  static const Module::RfSwitchMode_t rfswitch_table[] = {
    // mode                 DIO5  DIO6
    { LR11x0::MODE_STBY,   {LOW,  LOW}},
    { LR11x0::MODE_RX,     {HIGH, LOW}},
    { LR11x0::MODE_TX,     {LOW,  HIGH}},
    { LR11x0::MODE_TX_HP,  {LOW,  HIGH}},
    { LR11x0::MODE_TX_HF,  {LOW,  LOW}},
    { LR11x0::MODE_GNSS,   {LOW,  LOW}},
    { LR11x0::MODE_WIFI,   {LOW,  LOW}},
    END_OF_MODE_TABLE,
  };
  #endif
#endif // USE_LR1121

#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
static bool muziInitLR1121(CustomLR1121& chip, SPIClass* spi) {
  spi->setPins(P_LORA_MISO, P_LORA_SCLK, P_LORA_MOSI);
  spi->begin();
  int16_t status = chip.begin(LORA_FREQ, LORA_BW, LORA_SF, LORA_CR,
      RADIOLIB_LR11X0_LORA_SYNC_WORD_PRIVATE, LORA_TX_POWER, 16,
      LR11X0_DIO3_TCXO_VOLTAGE);
  if (status == RADIOLIB_ERR_NONE) status = chip.setCRC(2);
  if (status == RADIOLIB_ERR_NONE) status = chip.explicitHeader();
  if (status == RADIOLIB_ERR_NONE) {
    chip.setRfSwitchTable(rfswitch_dios, rfswitch_table);
    status = chip.setRxBoostedGainMode(RX_BOOSTED_GAIN);
  }
  return status == RADIOLIB_ERR_NONE;
}

mesh::LoRaRadioFamily universalLoRaRadioFamily() {
  return radio_driver.family();
}

bool prepareUniversalLoRaRadio() {
  if (radio_driver.family() != mesh::LoRaRadioFamily::None) return true;
  if (!radio_driver.setLR1121Initializer(muziInitLR1121, radio_init)) return false;
  SPI.setPins(P_LORA_MISO, P_LORA_SCLK, P_LORA_MOSI);
  SPI.begin();
  const mesh::PortableLoRaChipProbePins pins{P_LORA_NSS, P_LORA_RESET, P_LORA_BUSY};
  return radio_driver.selectFamily(mesh::probePortableLoRaChip(SPI, pins));
}
#endif

bool radio_init() {
  //rtc_clock.begin(Wire);

#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
  return prepareUniversalLoRaRadio() && radio_driver.stdInit(&SPI);
#elif defined(USE_LR1121)
  #ifdef LR11X0_DIO3_TCXO_VOLTAGE
    float tcxo = LR11X0_DIO3_TCXO_VOLTAGE;
  #else
    float tcxo = 1.6f;
  #endif

  SPI.setPins(P_LORA_MISO, P_LORA_SCLK, P_LORA_MOSI);
  SPI.begin();
  int status = radio.begin(LORA_FREQ, LORA_BW, LORA_SF, LORA_CR, RADIOLIB_LR11X0_LORA_SYNC_WORD_PRIVATE, LORA_TX_POWER, 16, tcxo);
  if (status != RADIOLIB_ERR_NONE) {
    Serial.print("ERROR: radio init failed: ");
    Serial.println(status);
    return false;  // fail
  }

  radio.setCRC(2);
  radio.explicitHeader();

  #ifdef RF_SWITCH_TABLE
  radio.setRfSwitchTable(rfswitch_dios, rfswitch_table);
  #endif
  #ifdef RX_BOOSTED_GAIN
  radio.setRxBoostedGainMode(RX_BOOSTED_GAIN);
  #endif

  // Preserve this board's TCXO and DIO RF routing during full modem recovery.
  radio_driver.setDeepInitCallback(radio_init);

  return true;  // success
#else  // USE_SX1262
  // CustomSX1262::std_init() configures the SPI pins, runs begin() with the
  // TCXO-voltage fallback, sets CRC, current limit, the DIO2 RF switch, and
  // RX boosted gain from the SX126X_* build flags.
  return radio.std_init(&SPI);
#endif
}

uint32_t radio_get_rng_seed() {
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
  return radio_driver.getRngSeed();
#else
  return radio.random(0x7FFFFFFF);
#endif
}

void radio_set_params(float freq, float bw, uint8_t sf, uint8_t cr) {
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
  radio_driver.setParams(freq, bw, sf, cr);
#else
  radio.setFrequency(freq);
  radio.setSpreadingFactor(sf);
  radio.setBandwidth(bw);
  radio.setCodingRate(cr);
#endif
}

void radio_set_tx_power(int8_t dbm) {
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
  radio_driver.setTxPower(dbm);
#else
  radio.setOutputPower(dbm);
#endif
}

mesh::LocalIdentity radio_new_identity() {
#if defined(MESH_RADIO_AUTO_FAMILY) && MESH_RADIO_AUTO_FAMILY && MESH_RADIO_AUTO_LR1121
  auto* physical = radio_driver.physicalLayer();
  if (!physical) return mesh::LocalIdentity();
  RadioNoiseListener rng(*physical);
#else
  RadioNoiseListener rng(radio);
#endif
  return mesh::LocalIdentity(&rng);  // create new random identity
}
