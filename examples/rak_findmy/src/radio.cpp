#include "radio.h"
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/spi.h>
#include <zephyr/kernel.h>
#include <modules/SX126x/SX1262.h>

namespace {
constexpr uint8_t cs = 42, reset = 38, busy = 46, irq = 47, power = 37;
const device* const bus = DEVICE_DT_GET(DT_NODELABEL(spi1));
const device* const gpios[] = {DEVICE_DT_GET(DT_NODELABEL(gpio0)), DEVICE_DT_GET(DT_NODELABEL(gpio1))};

class TagHal : public RadioLibHal {
  spi_config config{};
public:
  int error = 0;
  TagHal() : RadioLibHal(GPIO_INPUT, GPIO_OUTPUT, 0, 1, GPIO_INT_EDGE_RISING, GPIO_INT_EDGE_FALLING) {
    config.frequency = 1000000;
    config.operation = SPI_OP_MODE_MASTER | SPI_WORD_SET(8) | SPI_TRANSFER_MSB;
  }
  void pinMode(uint32_t pin, uint32_t mode) override {
    if (pin < 48) record(gpio_pin_configure(gpios[pin / 32], pin % 32, mode));
  }
  void digitalWrite(uint32_t pin, uint32_t value) override {
    if (pin < 48) record(gpio_pin_set_raw(gpios[pin / 32], pin % 32, value));
  }
  uint32_t digitalRead(uint32_t pin) override {
    if (pin >= 48) return 0;
    int rc = gpio_pin_get_raw(gpios[pin / 32], pin % 32);
    record(rc);
    return rc > 0;
  }
  // The collar polls the latched DIO1 pin from its own worker. RadioLib's
  // blocking transmit also polls that pin. Neither operation registers an ISR.
  void attachInterrupt(uint32_t, void (*)(void), uint32_t) override { record(-ENOTSUP); }
  void detachInterrupt(uint32_t) override {}
  long pulseIn(uint32_t, uint32_t, RadioLibTime_t) override { record(-ENOTSUP); return 0; }
  void delay(RadioLibTime_t ms) override { k_sleep(K_MSEC(ms)); }
  void delayMicroseconds(RadioLibTime_t us) override { k_busy_wait(us); }
  RadioLibTime_t millis() override { return k_uptime_get_32(); }
  RadioLibTime_t micros() override { return k_ticks_to_us_floor64(k_uptime_ticks()); }
  void yield() override { k_yield(); }
  void spiBegin() override {}
  void spiBeginTransaction() override {}
  void spiEndTransaction() override {}
  void spiEnd() override {}
  void spiTransfer(uint8_t* out, size_t size, uint8_t* in) override {
    spi_buf tx{out, size}, rx{in, size};
    spi_buf_set send{&tx, 1}, recv{&rx, 1};
    record(spi_transceive(bus, &config, &send, &recv));
  }
private:
  void record(int rc) { if (rc < 0 && !error) error = rc; }
};

TagHal hal;
Module module(&hal, cs, irq, reset, busy);
SX1262 radio(&module);
TagRadioProfile active{};
bool initialized;
}
bool tag_radio_available() {
  return device_is_ready(bus) && device_is_ready(gpios[0]) && device_is_ready(gpios[1]);
}
int tag_radio_apply(const TagRadioProfile& p) {
  if (!tag_radio_available()) return -ENODEV;
  if (initialized && active.hz == p.hz && active.bw_tenths == p.bw_tenths
      && active.sf == p.sf && active.cr == p.cr && !hal.error) return 0;
  initialized = false;
  hal.error = 0;
  int rc = gpio_pin_configure(gpios[1], power % 32, GPIO_OUTPUT_ACTIVE);
  if (rc) return rc;
  k_sleep(K_MSEC(10));
  ConfigLoRa_t cfg{};
  cfg.frequency = p.hz / 1000000.0f;
  cfg.bandwidth = p.bw_tenths / 10.0f;
  cfg.spreadingFactor = p.sf;
  cfg.codingRate = p.cr;
  cfg.syncWord = RADIOLIB_SX126X_SYNC_WORD_PRIVATE;
  cfg.power = 14;
  cfg.preambleLength = 16;
  radio.tcxoVoltage = 1.8f;
  rc = radio.begin(cfg);
  if (!rc) rc = radio.setDio2AsRfSwitch(true);
  if (!rc) rc = radio.setCRC(2);
  if (rc || hal.error) return rc ? rc : hal.error;
  active = p;
  initialized = true;
  return 0;
}
int tag_radio_send(const uint8_t* data, size_t size) {
  if (!initialized) return -ENODEV;
  int rc = radio.standby();
  if (!rc) rc = radio.scanChannel();
  if (rc == RADIOLIB_LORA_DETECTED) return -EBUSY;
  if (rc != RADIOLIB_CHANNEL_FREE) return rc ? rc : -EIO;
  rc = radio.transmit(data, size);
  return rc ? rc : hal.error;
}
int tag_radio_listen() {
  if (!initialized) return -ENODEV;
  int rc = radio.startReceive();
  return rc ? rc : hal.error;
}
int tag_radio_read(uint8_t* data, size_t capacity) {
  if (!initialized || !hal.digitalRead(irq)) return 0;
  size_t n = radio.getPacketLength();
  int rc = n && n <= capacity ? radio.readData(data, n) : -EMSGSIZE;
  int resume = tag_radio_listen();
  if (resume) return resume;
  if (rc) return rc;
  return hal.error ? hal.error : int(n);
}
void tag_radio_idle() {
  if (initialized) radio.standby();
}
