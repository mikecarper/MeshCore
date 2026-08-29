#include "OtaStoreQspiNrf52.h"

#if defined(NRF52_PLATFORM) && defined(OTA_QSPI_STORE)

#include "OtaByteIO.h"
#include "OtaFlashLayout_nrf52.h"
#include "hal/nrf_gpio.h"
#include "hal/nrf_qspi.h"
#include "nrf.h"

#include <Arduino.h>
#include <string.h>

namespace mesh {
namespace ota {

namespace {

#if defined(OTA_QSPI_SCK_PHYSICAL_PIN) && defined(OTA_QSPI_SCK_ARDUINO_PIN)
#error "QSPI SCK must use either a physical or Arduino pin override"
#elif !defined(OTA_QSPI_SCK_PHYSICAL_PIN) && !defined(OTA_QSPI_SCK_ARDUINO_PIN)
#define OTA_QSPI_SCK_ARDUINO_PIN PIN_QSPI_SCK
#endif
#if defined(OTA_QSPI_CS_PHYSICAL_PIN) && defined(OTA_QSPI_CS_ARDUINO_PIN)
#error "QSPI CS must use either a physical or Arduino pin override"
#elif !defined(OTA_QSPI_CS_PHYSICAL_PIN) && !defined(OTA_QSPI_CS_ARDUINO_PIN)
#define OTA_QSPI_CS_ARDUINO_PIN PIN_QSPI_CS
#endif
#if defined(OTA_QSPI_IO0_PHYSICAL_PIN) && defined(OTA_QSPI_IO0_ARDUINO_PIN)
#error "QSPI IO0 must use either a physical or Arduino pin override"
#elif !defined(OTA_QSPI_IO0_PHYSICAL_PIN) && !defined(OTA_QSPI_IO0_ARDUINO_PIN)
#define OTA_QSPI_IO0_ARDUINO_PIN PIN_QSPI_IO0
#endif
#if defined(OTA_QSPI_IO1_PHYSICAL_PIN) && defined(OTA_QSPI_IO1_ARDUINO_PIN)
#error "QSPI IO1 must use either a physical or Arduino pin override"
#elif !defined(OTA_QSPI_IO1_PHYSICAL_PIN) && !defined(OTA_QSPI_IO1_ARDUINO_PIN)
#define OTA_QSPI_IO1_ARDUINO_PIN PIN_QSPI_IO1
#endif

#if defined(OTA_QSPI_SCK_PHYSICAL_PIN)
static_assert((uint32_t)(OTA_QSPI_SCK_PHYSICAL_PIN) < 48, "invalid physical QSPI SCK pin");
#else
static_assert((uint32_t)(OTA_QSPI_SCK_ARDUINO_PIN) < PINS_COUNT, "invalid Arduino QSPI SCK pin");
#endif
#if defined(OTA_QSPI_CS_PHYSICAL_PIN)
static_assert((uint32_t)(OTA_QSPI_CS_PHYSICAL_PIN) < 48, "invalid physical QSPI CS pin");
#else
static_assert((uint32_t)(OTA_QSPI_CS_ARDUINO_PIN) < PINS_COUNT, "invalid Arduino QSPI CS pin");
#endif
#if defined(OTA_QSPI_IO0_PHYSICAL_PIN)
static_assert((uint32_t)(OTA_QSPI_IO0_PHYSICAL_PIN) < 48, "invalid physical QSPI IO0 pin");
#else
static_assert((uint32_t)(OTA_QSPI_IO0_ARDUINO_PIN) < PINS_COUNT, "invalid Arduino QSPI IO0 pin");
#endif
#if defined(OTA_QSPI_IO1_PHYSICAL_PIN)
static_assert((uint32_t)(OTA_QSPI_IO1_PHYSICAL_PIN) < 48, "invalid physical QSPI IO1 pin");
#else
static_assert((uint32_t)(OTA_QSPI_IO1_ARDUINO_PIN) < PINS_COUNT, "invalid Arduino QSPI IO1 pin");
#endif

#if defined(OTA_QSPI_IO2_NOT_CONNECTED)
  #if defined(OTA_QSPI_IO2_ARDUINO_PIN) || defined(OTA_QSPI_IO2_PHYSICAL_PIN)
    #error "QSPI IO2 cannot be both connected and disconnected"
  #endif
#elif defined(OTA_QSPI_IO2_PHYSICAL_PIN)
static_assert((uint32_t)(OTA_QSPI_IO2_PHYSICAL_PIN) < 48, "invalid physical QSPI IO2 pin");
#elif defined(OTA_QSPI_IO2_ARDUINO_PIN)
static_assert((uint32_t)(OTA_QSPI_IO2_ARDUINO_PIN) < PINS_COUNT, "invalid Arduino QSPI IO2 pin");
#else
#define OTA_QSPI_IO2_ARDUINO_PIN PIN_QSPI_IO2
static_assert((uint32_t)(OTA_QSPI_IO2_ARDUINO_PIN) < PINS_COUNT, "invalid Arduino QSPI IO2 pin");
#endif

#if defined(OTA_QSPI_IO3_NOT_CONNECTED)
  #if defined(OTA_QSPI_IO3_ARDUINO_PIN) || defined(OTA_QSPI_IO3_PHYSICAL_PIN)
    #error "QSPI IO3 cannot be both connected and disconnected"
  #endif
#elif defined(OTA_QSPI_IO3_PHYSICAL_PIN)
static_assert((uint32_t)(OTA_QSPI_IO3_PHYSICAL_PIN) < 48, "invalid physical QSPI IO3 pin");
#elif defined(OTA_QSPI_IO3_ARDUINO_PIN)
static_assert((uint32_t)(OTA_QSPI_IO3_ARDUINO_PIN) < PINS_COUNT, "invalid Arduino QSPI IO3 pin");
#else
#define OTA_QSPI_IO3_ARDUINO_PIN PIN_QSPI_IO3
static_assert((uint32_t)(OTA_QSPI_IO3_ARDUINO_PIN) < PINS_COUNT, "invalid Arduino QSPI IO3 pin");
#endif

static uint8_t arduino_to_physical(uint32_t arduino_pin) {
  return (uint8_t)g_ADigitalPinMap[arduino_pin];
}

static uint8_t qspi_sck_pin() {
#ifdef OTA_QSPI_SCK_PHYSICAL_PIN
  return OTA_QSPI_SCK_PHYSICAL_PIN;
#else
  return arduino_to_physical(OTA_QSPI_SCK_ARDUINO_PIN);
#endif
}

static uint8_t qspi_cs_pin() {
#ifdef OTA_QSPI_CS_PHYSICAL_PIN
  return OTA_QSPI_CS_PHYSICAL_PIN;
#else
  return arduino_to_physical(OTA_QSPI_CS_ARDUINO_PIN);
#endif
}

static uint8_t qspi_io0_pin() {
#ifdef OTA_QSPI_IO0_PHYSICAL_PIN
  return OTA_QSPI_IO0_PHYSICAL_PIN;
#else
  return arduino_to_physical(OTA_QSPI_IO0_ARDUINO_PIN);
#endif
}

static uint8_t qspi_io1_pin() {
#ifdef OTA_QSPI_IO1_PHYSICAL_PIN
  return OTA_QSPI_IO1_PHYSICAL_PIN;
#else
  return arduino_to_physical(OTA_QSPI_IO1_ARDUINO_PIN);
#endif
}

static uint8_t qspi_io2_pin() {
#ifdef OTA_QSPI_IO2_NOT_CONNECTED
  return NRF_QSPI_PIN_NOT_CONNECTED;
#elif defined(OTA_QSPI_IO2_PHYSICAL_PIN)
  return OTA_QSPI_IO2_PHYSICAL_PIN;
#else
  return arduino_to_physical(OTA_QSPI_IO2_ARDUINO_PIN);
#endif
}

static uint8_t qspi_io3_pin() {
#ifdef OTA_QSPI_IO3_NOT_CONNECTED
  return NRF_QSPI_PIN_NOT_CONNECTED;
#elif defined(OTA_QSPI_IO3_PHYSICAL_PIN)
  return OTA_QSPI_IO3_PHYSICAL_PIN;
#else
  return arduino_to_physical(OTA_QSPI_IO3_ARDUINO_PIN);
#endif
}

static void configure_qspi_gpio(const nrf_qspi_pins_t& pins) {
  // The QSPI PSEL registers do not configure GPIO drive strength. Nordic
  // initializes every connected pad as input-disconnected/high-drive before
  // the peripheral takes ownership and controls each pin's direction.
  nrf_gpio_pin_clear(pins.sck_pin); // mode 0 idle level
  nrf_gpio_pin_set(pins.csn_pin);   // never select the NOR during handoff
  nrf_gpio_cfg(pins.sck_pin, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  nrf_gpio_cfg(pins.csn_pin, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  nrf_gpio_cfg(pins.io0_pin, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  nrf_gpio_cfg(pins.io1_pin, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  if (pins.io2_pin != NRF_QSPI_PIN_NOT_CONNECTED)
    nrf_gpio_cfg(pins.io2_pin, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
                 NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  if (pins.io3_pin != NRF_QSPI_PIN_NOT_CONNECTED)
    nrf_gpio_cfg(pins.io3_pin, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
                 NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
}

static void wake_qspi_flash_before_activate(const nrf_qspi_pins_t& pins) {
  // nRF52840 TASKS_ACTIVATE communicates with the NOR before CINSTR is
  // available. A flash put to sleep by our preceding 0xB9 ignores that
  // activation traffic, so issue the only command it accepts (0xAB) with a
  // short mode-0 GPIO transaction first. IO2/WP# and IO3/HOLD# stay high.
  nrf_gpio_pin_clear(pins.sck_pin);
  nrf_gpio_pin_set(pins.csn_pin);
  nrf_gpio_pin_set(pins.io0_pin);
  if (pins.io2_pin != NRF_QSPI_PIN_NOT_CONNECTED) nrf_gpio_pin_set(pins.io2_pin);
  if (pins.io3_pin != NRF_QSPI_PIN_NOT_CONNECTED) nrf_gpio_pin_set(pins.io3_pin);

  nrf_gpio_cfg(pins.sck_pin, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  nrf_gpio_cfg(pins.csn_pin, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  nrf_gpio_cfg(pins.io0_pin, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  nrf_gpio_cfg(pins.io1_pin, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  if (pins.io2_pin != NRF_QSPI_PIN_NOT_CONNECTED)
    nrf_gpio_cfg(pins.io2_pin, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
                 NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  if (pins.io3_pin != NRF_QSPI_PIN_NOT_CONNECTED)
    nrf_gpio_cfg(pins.io3_pin, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
                 NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);

  delayMicroseconds(1);
  nrf_gpio_pin_clear(pins.csn_pin);
  delayMicroseconds(1);
  for (uint8_t bit = 0; bit < 8u; bit++) {
    nrf_gpio_pin_write(pins.io0_pin, mota_qspi_release_from_dpd_bit(bit));
    delayMicroseconds(1);
    nrf_gpio_pin_set(pins.sck_pin);
    delayMicroseconds(1);
    nrf_gpio_pin_clear(pins.sck_pin);
    delayMicroseconds(1);
  }
  nrf_gpio_pin_set(pins.csn_pin);
  delayMicroseconds(MOTA_QSPI_DPD_WAKE_GUARD_US);
}

} // namespace

OtaStoreQspiNrf52::OtaStoreQspiNrf52() {
  resetSession();
}

OtaStoreQspiNrf52::~OtaStoreQspiNrf52() {
  releaseFlash();
}

void OtaStoreQspiNrf52::fail(const char *message) {
  _io_ok = false;
  // Keep the first concrete failure from a manifest attempt. Higher-level
  // wrappers must not replace a program/status error with a generic one.
  if (_error[0]) return;
  strncpy(_error, message ? message : "QSPI error", sizeof(_error) - 1);
  _error[sizeof(_error) - 1] = 0;
}

void OtaStoreQspiNrf52::resetSession() {
  _total = 0;
  _io_ok = true;
  _meta_dirty = false;
  _data_dirty = false;
  _data_page_index = INVALID_PAGE;
  memset(_meta_page, 0xFF, sizeof(_meta_page));
  memset(_data_page, 0xFF, sizeof(_data_page));
  memset(_known_pages, 0, sizeof(_known_pages));
  _error[0] = 0;
  _stage = OtaQspiStage::IDLE;
}

bool OtaStoreQspiNrf52::pageKnown(uint32_t page) const {
  return page < MAX_PAGES && (_known_pages[page >> 3] & (1u << (page & 7))) != 0;
}

void OtaStoreQspiNrf52::setPageKnown(uint32_t page) {
  if (page < MAX_PAGES) _known_pages[page >> 3] |= (uint8_t)(1u << (page & 7));
}

bool OtaStoreQspiNrf52::waitReady(uint32_t timeout_ms) {
  uint32_t started = millis();
  while (!nrf_qspi_event_check(NRF_QSPI, NRF_QSPI_EVENT_READY)) {
    if ((uint32_t)(millis() - started) >= timeout_ms) return false;
    delay(1);
  }
  nrf_qspi_event_clear(NRF_QSPI, NRF_QSPI_EVENT_READY);
  return true;
}

bool OtaStoreQspiNrf52::customInstruction(uint8_t opcode, uint8_t length, uint8_t *rx) {
  nrf_qspi_cinstr_conf_t config;
  memset(&config, 0, sizeof(config));
  config.opcode = opcode;
  config.length = (nrf_qspi_cinstr_len_t)length;
  config.io2_level = true;
  config.io3_level = true;
  nrf_qspi_event_clear(NRF_QSPI, NRF_QSPI_EVENT_READY);
  nrf_qspi_cinstr_transfer_start(NRF_QSPI, &config);
  if (!waitReady(1000)) return false;
  if (rx) nrf_qspi_cinstrdata_get(NRF_QSPI, config.length, rx);
  return true;
}

bool OtaStoreQspiNrf52::readStatus1() {
  uint8_t status = 0;
  if (!customInstruction(0x05, NRF_QSPI_CINSTR_LEN_2B, &status)) {
    fail("QSPI status read failed");
    return false;
  }
  _status1 = status;
  return true;
}

bool OtaStoreQspiNrf52::waitMemoryReady(uint32_t timeout_ms) {
  const uint32_t started = millis();
  for (;;) {
    if (!readStatus1()) return false;
    if (!mota_qspi_status_busy(_status1)) {
      _memory_operation_pending = false;
      return true;
    }
    // This also covers recovery after a reset during an earlier NOR write:
    // once WIP is observed, releaseFlash must not send DPD or cut power unless
    // a later status read proves the operation finished.
    _memory_operation_pending = true;
    if ((uint32_t)(millis() - started) >= timeout_ms) return false;
    delay(1);
  }
}

bool OtaStoreQspiNrf52::ensureFlash() {
  if (_qspi_ready) return true;

#if defined(OTA_QSPI_POWER_PIN)
  pinMode(OTA_QSPI_POWER_PIN, OUTPUT);
  digitalWrite(OTA_QSPI_POWER_PIN, HIGH);
  delay(2);
#elif defined(PIN_FLASH_EN)
  pinMode(PIN_FLASH_EN, OUTPUT);
  digitalWrite(PIN_FLASH_EN, HIGH);
  delay(2);
#elif defined(QSPI_FLASH_EN)
  pinMode(QSPI_FLASH_EN, OUTPUT);
  digitalWrite(QSPI_FLASH_EN, HIGH);
  delay(2);
#endif

  NVIC_DisableIRQ(QSPI_IRQn);
  NVIC_ClearPendingIRQ(QSPI_IRQn);
  nrf_qspi_int_disable(NRF_QSPI, 0xFFFFFFFFUL);

  nrf_qspi_pins_t pins;
  pins.sck_pin = qspi_sck_pin();
  pins.csn_pin = qspi_cs_pin();
  pins.io0_pin = qspi_io0_pin();
  pins.io1_pin = qspi_io1_pin();
  pins.io2_pin = qspi_io2_pin();
  pins.io3_pin = qspi_io3_pin();
  _stage = OtaQspiStage::WAKE;
  // A reset may have interrupted a NOR program/erase. Until activation and a
  // successful RDSR prove WIP clear, every failure path must retain power and
  // hold CS# high instead of sending B9 or cutting the flash rail.
  _memory_operation_pending = true;
  wake_qspi_flash_before_activate(pins);
  configure_qspi_gpio(pins);
  nrf_qspi_pins_set(NRF_QSPI, &pins);

  nrf_qspi_prot_conf_t protocol;
  protocol.readoc = NRF_QSPI_READOC_FASTREAD;
  protocol.writeoc = NRF_QSPI_WRITEOC_PP;
  protocol.addrmode = NRF_QSPI_ADDRMODE_24BIT;
  protocol.dpmconfig = false;
  nrf_qspi_ifconfig0_set(NRF_QSPI, &protocol);

  nrf_qspi_phy_conf_t physical;
  physical.sck_delay = 5;
  physical.dpmen = false;
  physical.spi_mode = NRF_QSPI_MODE_0;
#ifdef OTA_QSPI_SCK_FREQUENCY
  physical.sck_freq = static_cast<nrf_qspi_frequency_t>(OTA_QSPI_SCK_FREQUENCY);
#else
  physical.sck_freq = NRF_QSPI_FREQ_32MDIV2;
#endif
  nrf_qspi_ifconfig1_set(NRF_QSPI, &physical);

  nrf_qspi_enable(NRF_QSPI);
  _qspi_active = true;
  _stage = OtaQspiStage::ACTIVATE;
  nrf_qspi_event_clear(NRF_QSPI, NRF_QSPI_EVENT_READY);
  nrf_qspi_task_trigger(NRF_QSPI, NRF_QSPI_TASK_ACTIVATE);
  // ACTIVATE itself can wait for a previously interrupted NOR erase. Match the
  // recovery window used by the explicit status poll below.
  if (!waitReady(30000)) {
    releaseFlash();
    fail("QSPI activate timed out");
    return false;
  }

  // The GPIO 0xAB issued before TASKS_ACTIVATE already woke the NOR. CINSTR
  // cannot perform that first wake because it is unavailable until activation.
  _qspi_awake = true;
  // Recover cleanly if this boot/activation follows an interrupted program or
  // erase. RDID is not guaranteed to respond while WIP is set, so prove the
  // NOR idle before requesting its identity.
  _stage = OtaQspiStage::STATUS;
  if (!waitMemoryReady(30000)) {
    if (_io_ok) fail("QSPI wake busy timed out");
    releaseFlash();
    return false;
  }

  alignas(4) uint8_t jedec[4] = { 0, 0, 0, 0 };
  _stage = OtaQspiStage::JEDEC;
  _jedec_id = 0;
  if (!customInstruction(0x9F, NRF_QSPI_CINSTR_LEN_4B, jedec)) {
    releaseFlash();
    fail("QSPI JEDEC read failed");
    return false;
  }
  _jedec_id = ((uint32_t)jedec[0] << 16) | ((uint32_t)jedec[1] << 8) | jedec[2];
  if (jedec[0] == 0 || jedec[0] == 0xFF || jedec[2] < 20 || jedec[2] > 24) {
    releaseFlash();
    fail("QSPI JEDEC ID/capacity unsupported");
    return false;
  }
#ifdef OTA_QSPI_EXPECTED_JEDEC_ID
  if (_jedec_id != (uint32_t)OTA_QSPI_EXPECTED_JEDEC_ID) {
    releaseFlash();
    fail("QSPI JEDEC ID does not match target");
    return false;
  }
#endif
  _flash_size = 1UL << jedec[2];
  if (_flash_size > MAX_FLASH) {
    releaseFlash();
    fail("QSPI flash exceeds 24-bit staging limit");
    return false;
  }
#ifdef OTA_QSPI_EXPECTED_SIZE
  if (_flash_size != (uint32_t)OTA_QSPI_EXPECTED_SIZE) {
    releaseFlash();
    fail("QSPI capacity does not match target");
    return false;
  }
#endif
  _qspi_ready = true;
  return true;
}

void OtaStoreQspiNrf52::releaseFlash() {
  if (_qspi_awake && !_memory_operation_pending) {
    // The repeater can remain idle for hours after a capacity/status probe or
    // a completed checkpoint. Put the NOR into deep power-down before
    // releasing the nRF QSPI peripheral instead of leaving both active for
    // the rest of the boot. ensureFlash() issues 0xAB on the next operation.
    (void)customInstruction(0xB9, NRF_QSPI_CINSTR_LEN_1B);
    // Do not deactivate or let a following ensureFlash() assert CS until the
    // flash has both entered and remained in DPD for its required interval.
    // plan_layout() is intentionally followed immediately by begin().
    delayMicroseconds(MOTA_QSPI_DPD_ENTRY_GUARD_US);
  }
  // If WIP could not be proven clear, do not send DPD or cut flash power.
  // Deactivating the controller leaves CS high while the NOR finishes or
  // remains available for a later diagnostic probe.

  // Match nrfx_qspi_uninit(): DEACTIVATE does not require a READY wait before
  // disabling the peripheral. Trigger it after every successful ENABLE, even
  // when wake or JEDEC identification failed before _qspi_ready was set.
  if (_qspi_active) {
    if (_memory_operation_pending) {
      // QSPI owns pin direction while enabled. Preload and configure CS# high
      // before releasing the peripheral so an unresolved NOR operation cannot
      // see a floating or asserted chip select during the handoff.
      const uint8_t cs_pin = qspi_cs_pin();
      nrf_gpio_pin_set(cs_pin);
      nrf_gpio_cfg(cs_pin, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_DISCONNECT,
                   NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
    }
    nrf_qspi_event_clear(NRF_QSPI, NRF_QSPI_EVENT_READY);
    nrf_qspi_task_trigger(NRF_QSPI, NRF_QSPI_TASK_DEACTIVATE);
    nrf_qspi_disable(NRF_QSPI);
    nrf_qspi_event_clear(NRF_QSPI, NRF_QSPI_EVENT_READY);
    if (_memory_operation_pending) {
      const nrf_qspi_pins_t disconnected = {
        NRF_QSPI_PIN_NOT_CONNECTED, NRF_QSPI_PIN_NOT_CONNECTED,
        NRF_QSPI_PIN_NOT_CONNECTED, NRF_QSPI_PIN_NOT_CONNECTED,
        NRF_QSPI_PIN_NOT_CONNECTED, NRF_QSPI_PIN_NOT_CONNECTED
      };
      nrf_qspi_pins_set(NRF_QSPI, &disconnected);
    }
  }
  _qspi_active = false;
  _qspi_awake = false;
  _qspi_ready = false;
#if defined(OTA_QSPI_POWER_PIN)
  if (!_memory_operation_pending) digitalWrite(OTA_QSPI_POWER_PIN, LOW);
#elif defined(PIN_FLASH_EN)
  if (!_memory_operation_pending) digitalWrite(PIN_FLASH_EN, LOW);
#elif defined(QSPI_FLASH_EN)
  if (!_memory_operation_pending) digitalWrite(QSPI_FLASH_EN, LOW);
#endif
}

bool OtaStoreQspiNrf52::dmaReadAligned(uint32_t address, uint32_t length) {
  // nRF52840 QSPI EasyDMA requires a word-aligned RAM buffer and a transfer
  // count that is a multiple of four. Keep the external address aligned too,
  // so every supported SDK/peripheral revision receives the same safe shape.
  if (!length || length > sizeof(_bounce) || ((address | length) & 3u) != 0 ||
      (uint64_t)address + length > _flash_size) {
    fail("QSPI aligned read invalid");
    return false;
  }
  _stage = OtaQspiStage::READ;
  nrf_qspi_read_buffer_set(NRF_QSPI, _bounce, length, address);
  nrf_qspi_event_clear(NRF_QSPI, NRF_QSPI_EVENT_READY);
  nrf_qspi_task_trigger(NRF_QSPI, NRF_QSPI_TASK_READSTART);
  if (!waitReady(5000)) {
    fail("QSPI read timed out");
    return false;
  }
  return true;
}

bool OtaStoreQspiNrf52::dmaWriteAligned(uint32_t address, uint32_t length) {
  if (!length || length > sizeof(_bounce) || ((address | length) & 3u) != 0 ||
      (address & (PROGRAM - 1)) + length > PROGRAM ||
      (uint64_t)address + length > _flash_size) {
    fail("QSPI aligned program invalid");
    return false;
  }
  _stage = OtaQspiStage::PROGRAM;
  nrf_qspi_write_buffer_set(NRF_QSPI, _bounce, length, address);
  nrf_qspi_event_clear(NRF_QSPI, NRF_QSPI_EVENT_READY);
  _memory_operation_pending = true;
  nrf_qspi_task_trigger(NRF_QSPI, NRF_QSPI_TASK_WRITESTART);
  if (!waitReady(10000)) {
    fail("QSPI program timed out");
    return false;
  }
  // READY only reports that the page-program command/data were sent. Poll the
  // NOR WIP bit before verifying, issuing another operation, or entering DPD.
  _stage = OtaQspiStage::PROGRAM_BUSY;
  if (!waitMemoryReady(10000)) {
    if (_io_ok) fail("QSPI program busy timed out");
    return false;
  }
  _memory_operation_pending = false;
  return true;
}

bool OtaStoreQspiNrf52::rawRead(uint32_t address, void *data, uint32_t length) {
  if (length && !data) {
    fail("QSPI read buffer missing");
    return false;
  }
  if (!length) return true;
  if (!ensureFlash() || (uint64_t)address + length > _flash_size) {
    if (_io_ok) fail("QSPI read outside flash");
    return false;
  }
  uint8_t *out = static_cast<uint8_t *>(data);
  while (length) {
    const uint32_t aligned_address = address & ~3u;
    const uint32_t prefix = address - aligned_address;
    uint32_t n = length;
    if (n > sizeof(_bounce) - prefix) n = sizeof(_bounce) - prefix;
    const uint32_t dma_length = (prefix + n + 3u) & ~3u;
    if (!dmaReadAligned(aligned_address, dma_length)) return false;
    memcpy(out, _bounce + prefix, n);
    address += n;
    out += n;
    length -= n;
  }
  return true;
}

bool OtaStoreQspiNrf52::rawWrite(uint32_t address, const void *data, uint32_t length) {
  if (length && !data) {
    fail("QSPI program buffer missing");
    return false;
  }
  if (!length) return true;
  if (!ensureFlash() || (uint64_t)address + length > _flash_size) {
    if (_io_ok) fail("QSPI program outside flash");
    return false;
  }
  const uint8_t *in = static_cast<const uint8_t *>(data);
  while (length) {
    const uint32_t aligned_address = address & ~3u;
    const uint32_t prefix = address - aligned_address;
    const uint32_t page_room = PROGRAM - (aligned_address & (PROGRAM - 1));
    uint32_t n = length;
    if (n > page_room - prefix) n = page_room - prefix;
    const uint32_t dma_length = (prefix + n + 3u) & ~3u;

    // Read-modify-program the aligned EasyDMA window. NOR flash cannot change
    // a programmed zero back to one without erasing the 4 KiB sector, so
    // reject such a request instead of silently corrupting the container.
    if (!dmaReadAligned(aligned_address, dma_length)) return false;
    for (uint32_t i = 0; i < n; ++i) {
      const uint8_t old_value = _bounce[prefix + i];
      const uint8_t new_value = in[i];
      if ((old_value & new_value) != new_value) {
        fail("QSPI program requires erase");
        return false;
      }
      _bounce[prefix + i] = new_value;
    }
    if (!dmaWriteAligned(aligned_address, dma_length)) return false;
    address += n;
    in += n;
    length -= n;
  }
  return true;
}

bool OtaStoreQspiNrf52::rawErasePage(uint32_t address) {
  if (!ensureFlash() || (address & (PAGE - 1)) != 0 || address > _flash_size - PAGE) return false;
  _stage = OtaQspiStage::ERASE;
  nrf_qspi_erase_ptr_set(NRF_QSPI, address, NRF_QSPI_ERASE_LEN_4KB);
  nrf_qspi_event_clear(NRF_QSPI, NRF_QSPI_EVENT_READY);
  _memory_operation_pending = true;
  nrf_qspi_task_trigger(NRF_QSPI, NRF_QSPI_TASK_ERASESTART);
  if (!waitReady(30000)) {
    fail("QSPI erase timed out");
    return false;
  }
  // As with program, READY precedes completion of the flash's internal erase.
  _stage = OtaQspiStage::ERASE_BUSY;
  if (!waitMemoryReady(30000)) {
    if (_io_ok) fail("QSPI erase busy timed out");
    return false;
  }
  _memory_operation_pending = false;
  return true;
}

bool OtaStoreQspiNrf52::flushPage(uint32_t page, const uint8_t *data) {
  if (!_io_ok || page >= MAX_PAGES || (uint64_t)(page + 1) * PAGE > _flash_size ||
      !rawErasePage(page * PAGE) || !rawWrite(page * PAGE, data, PAGE)) {
    if (_io_ok) fail("QSPI page write failed");
    return false;
  }
  alignas(4) uint8_t verify[PROGRAM];
  for (uint32_t off = 0; off < PAGE; off += sizeof(verify)) {
    if (!rawRead(page * PAGE + off, verify, sizeof(verify)) ||
        memcmp(verify, data + off, sizeof(verify)) != 0) {
      fail("QSPI page verify failed");
      return false;
    }
  }
  setPageKnown(page);
  return true;
}

bool OtaStoreQspiNrf52::flushMeta() {
  if (!_meta_dirty) return _io_ok;
  if (!flushPage(0, _meta_page)) return false;
  _meta_dirty = false;
  return true;
}

bool OtaStoreQspiNrf52::flushData() {
  if (_data_page_index == INVALID_PAGE || !_data_dirty) return _io_ok;
  if (!flushPage(_data_page_index, _data_page)) return false;
  _data_dirty = false;
  return true;
}

bool OtaStoreQspiNrf52::useDataPage(uint32_t page) {
  if (page == 0 || page >= MAX_PAGES) return false;
  if (_data_page_index == page) return true;
  if (!flushData()) return false;
  if (pageKnown(page)) {
    if (!rawRead(page * PAGE, _data_page, PAGE)) {
      fail("QSPI page load failed");
      return false;
    }
  } else {
    memset(_data_page, 0xFF, PAGE);
    setPageKnown(page);
  }
  _data_page_index = page;
  _data_dirty = false;
  return true;
}

uint32_t OtaStoreQspiNrf52::capacity() const {
  OtaStoreQspiNrf52 *self = const_cast<OtaStoreQspiNrf52 *>(this);
  // A read-only CLI probe must not erase the reason a fetch just failed.
  // Preserve the latched failure while still refreshing the JEDEC ID/capacity.
  const bool preserve_failure = self->_error[0] != 0;
  char saved_error[sizeof(self->_error)];
  OtaQspiStage saved_stage = self->_stage;
  uint8_t saved_status1 = self->_status1;
  bool saved_io_ok = self->_io_ok;
  if (preserve_failure) {
    strncpy(saved_error, self->_error, sizeof(saved_error));
    saved_error[sizeof(saved_error) - 1] = 0;
  }
  uint32_t result = self->ensureFlash() ? self->_flash_size : 0;
  self->releaseFlash();
  if (preserve_failure) {
    strncpy(self->_error, saved_error, sizeof(self->_error));
    self->_error[sizeof(self->_error) - 1] = 0;
    self->_stage = saved_stage;
    self->_status1 = saved_status1;
    self->_io_ok = saved_io_ok;
  }
  return result;
}

bool OtaStoreQspiNrf52::plan_layout(bool, uint32_t image_size, uint32_t,
                                    uint32_t payload_size, bool) {
  // This is the start of a new manifest admission attempt. Clear diagnostics
  // left by the normal empty-store reopen probe, then latch the first error
  // from this attempt until the operator reads it or starts another attempt.
  _error[0] = 0;
  _io_ok = true;
  _stage = OtaQspiStage::IDLE;
  const uint32_t app_base = mota_nrf52_app_base();
  const uint32_t app_ceiling = mota_nrf52_application_ceiling();
  if (image_size == 0 || payload_size == 0 || app_base >= app_ceiling ||
      image_size > app_ceiling - app_base) {
    fail("image exceeds nRF52 application region");
    releaseFlash();
    return false;
  }
  bool result = ensureFlash();
  releaseFlash();
  return result;
}

bool OtaStoreQspiNrf52::begin(uint32_t total_size) {
  if (!ensureFlash()) {
    releaseFlash();
    return false;
  }
  if (total_size < 13 || total_size > _flash_size) {
    fail("QSPI lacks space for update");
    releaseFlash();
    return false;
  }

  // Immediately invalidate an older raw container. Page zero is erased and
  // replaced with the new header at the first checkpoint/finalize.
  uint8_t zero[4] = { 0, 0, 0, 0 };
  uint8_t check[sizeof(zero)];
  if (!rawWrite(0, zero, sizeof(zero))) {
    releaseFlash();
    return false;
  }
  if (!rawRead(0, check, sizeof(check))) {
    releaseFlash();
    return false;
  }
  if (memcmp(check, zero, sizeof(zero)) != 0) {
    _stage = OtaQspiStage::INVALIDATE_VERIFY;
    char message[sizeof(_error)];
    snprintf(message, sizeof(message), "QSPI invalidation mismatch sr1=%02X", _status1);
    fail(message);
    releaseFlash();
    return false;
  }
  resetSession();
  _total = total_size;
  releaseFlash();
  return true;
}

bool OtaStoreQspiNrf52::set_meta_size(uint32_t meta_bytes) {
  if (_total < 13 || meta_bytes > PAGE) {
    _stage = OtaQspiStage::META_SIZE;
    fail("QSPI metadata exceeds first page");
    return false;
  }
  return true;
}

bool OtaStoreQspiNrf52::write(uint32_t offset, const uint8_t *data, uint32_t len) {
  if (!_io_ok) {
    releaseFlash();
    return false;
  }
  if (!data || (uint64_t)offset + len > _total) {
    _stage = OtaQspiStage::BUFFER_WRITE;
    fail(!data ? "QSPI write buffer missing" : "QSPI write outside container");
    releaseFlash();
    return false;
  }
  while (len) {
    uint32_t page = offset / PAGE;
    uint32_t in_page = offset & (PAGE - 1);
    uint32_t n = PAGE - in_page;
    if (n > len) n = len;
    if (page == 0) {
      memcpy(_meta_page + in_page, data, n);
      _meta_dirty = true;
    } else {
      if (!useDataPage(page)) {
        releaseFlash();
        return false;
      }
      memcpy(_data_page + in_page, data, n);
      _data_dirty = true;
    }
    offset += n;
    data += n;
    len -= n;
  }
  releaseFlash();
  return true;
}

bool OtaStoreQspiNrf52::read(uint32_t offset, uint8_t *buf, uint32_t len) const {
  OtaStoreQspiNrf52 *self = const_cast<OtaStoreQspiNrf52 *>(this);
  if (!_io_ok || !buf || (uint64_t)offset + len > _total) {
    self->releaseFlash();
    return false;
  }
  while (len) {
    uint32_t page = offset / PAGE;
    uint32_t in_page = offset & (PAGE - 1);
    uint32_t n = PAGE - in_page;
    if (n > len) n = len;
    if (page == 0) {
      memcpy(buf, _meta_page + in_page, n);
    } else if (page == _data_page_index) {
      memcpy(buf, _data_page + in_page, n);
    } else if (pageKnown(page)) {
      if (!self->rawRead(offset, buf, n)) {
        self->releaseFlash();
        return false;
      }
    } else {
      memset(buf, 0xFF, n);
    }
    offset += n;
    buf += n;
    len -= n;
  }
  self->releaseFlash();
  return true;
}

bool OtaStoreQspiNrf52::finalize() {
  if (!_total || !_io_ok) {
    releaseFlash();
    return false;
  }
  // Persist payload first, then the leaf-progress metadata that declares it.
  bool result = flushData() && flushMeta();
  releaseFlash();
  return result;
}

void OtaStoreQspiNrf52::checkpoint() {
  if (!_total || !_io_ok) {
    releaseFlash();
    return;
  }
  if (flushData()) flushMeta();
  releaseFlash();
}

bool OtaStoreQspiNrf52::reopen() {
  resetSession();
  if (!ensureFlash()) {
    releaseFlash();
    return false;
  }
  uint8_t header[8];
  if (!rawRead(0, header, sizeof(header)) || memcmp(header, MOTA_MAGIC, 4) != 0) {
    releaseFlash();
    return false;
  }
  uint32_t total = rd_u32le(header + 4);
  if (total < 13 || total > _flash_size) {
    releaseFlash();
    return false;
  }
  uint8_t trailer[5];
  if (!rawRead(total - sizeof(trailer), trailer, sizeof(trailer)) ||
      memcmp(trailer, MOTA_TRAILER, sizeof(trailer)) != 0) {
    releaseFlash();
    return false;
  }
  if (!rawRead(0, _meta_page, PAGE)) {
    fail("QSPI metadata load failed");
    releaseFlash();
    return false;
  }
  _total = total;
  uint32_t pages = (total + PAGE - 1) / PAGE;
  for (uint32_t page = 0; page < pages; page++)
    setPageKnown(page);
  releaseFlash();
  return true;
}

bool OtaStoreQspiNrf52::discard() {
  bool ok = ensureFlash();
  uint8_t zero[4] = { 0, 0, 0, 0 };
  uint8_t check[sizeof(zero)];
  if (ok) {
    ok = rawWrite(0, zero, sizeof(zero)) && rawRead(0, check, sizeof(check)) &&
         memcmp(check, zero, sizeof(zero)) == 0;
  }
  // The raw QSPI store is dedicated to OTA, and APRV has a fixed manifest
  // offset. A fresh store object has _total == 0 even when an older approved
  // container remains in flash, so invalidate both gates from flash capacity
  // rather than trusting this session's RAM state.
  if (ok && _flash_size >= 8 + MOTA_OFF_APPROVAL + sizeof(zero)) {
    const uint32_t approval = 8 + MOTA_OFF_APPROVAL;
    ok = rawWrite(approval, zero, sizeof(zero)) && rawRead(approval, check, sizeof(check)) &&
         memcmp(check, zero, sizeof(zero)) == 0;
  }
  char saved_error[sizeof(_error)];
  strncpy(saved_error, _error, sizeof(saved_error));
  saved_error[sizeof(saved_error) - 1] = 0;
  resetSession();
  if (!ok) fail(saved_error[0] ? saved_error : "QSPI container invalidation failed");
  releaseFlash();
  return ok;
}

void OtaStoreQspiNrf52::clear() { (void)discard(); }

bool OtaStoreQspiNrf52::approve_for_bootloader() {
  if (!finalize()) return false;
  const uint32_t approval = 8 + MOTA_OFF_APPROVAL;
  if (approval + sizeof(APPROVAL_YES) > _total || !rawWrite(approval, APPROVAL_YES, sizeof(APPROVAL_YES))) {
    fail("QSPI approval write failed");
    releaseFlash();
    return false;
  }
  uint8_t check[sizeof(APPROVAL_YES)];
  if (!rawRead(approval, check, sizeof(check)) || memcmp(check, APPROVAL_YES, sizeof(check)) != 0) {
    fail("QSPI approval verify failed");
    releaseFlash();
    return false;
  }
  memcpy(_meta_page + approval, APPROVAL_YES, sizeof(APPROVAL_YES));
  releaseFlash();
  return true;
}

} // namespace ota
} // namespace mesh

#endif
