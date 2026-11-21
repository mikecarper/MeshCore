#include "gnss.h"
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/ring_buffer.h>
#include <zephyr/sys/timeutil.h>
#include <MicroNMEA.h>

namespace {
const device* const gps = DEVICE_DT_GET(DT_NODELABEL(uart0));
const device* const power = DEVICE_DT_GET(DT_NODELABEL(gpio1));
RING_BUF_DECLARE(bytes, 1024);
char line[100];
MicroNMEA nmea(line, sizeof(line));
TagFix latest{};
bool running;

void receive(const device* dev, void*) {
  uint8_t chunk[32];
  if (!uart_irq_update(dev) || !uart_irq_rx_ready(dev)) return;
  int size;
  while ((size = uart_fifo_read(dev, chunk, sizeof(chunk))) > 0)
    ring_buf_put(&bytes, chunk, size);
}
}

bool tag_gps_available() {
  return device_is_ready(gps) && device_is_ready(power);
}

bool tag_gps_begin() {
  if (running) return true;
  latest = {};
  nmea.clear();
  ring_buf_reset(&bytes);
  if (!tag_gps_available()
      || gpio_pin_configure(power, 2, GPIO_OUTPUT_ACTIVE)
      || uart_irq_callback_user_data_set(gps, receive, nullptr)) return false;
  uart_irq_rx_enable(gps);
  running = true;
  return true;
}

void tag_gps_end() {
  if (!running) return;
  uart_irq_rx_disable(gps);
  gpio_pin_set(power, 2, 0);
  running = false;
  latest = {};
}

void tag_gps_process() {
  if (!running) return;
  uint8_t chunk[96];
  unsigned count;
  while ((count = ring_buf_get(&bytes, chunk, sizeof(chunk))) > 0) {
    for (unsigned i = 0; i < count; ++i) {
      if (!nmea.process(chunk[i]) || !MicroNMEA::testChecksum(nmea.getSentence())
          || strcmp(nmea.getMessageID(), "RMC")
          || !nmea.isValid()) continue;
      const long latitude = nmea.getLatitude(), longitude = nmea.getLongitude();
      if (latitude < -90000000 || latitude > 90000000
          || longitude < -180000000 || longitude > 180000000
          || nmea.getYear() < 2020 || nmea.getYear() > 2105
          || nmea.getMonth() < 1 || nmea.getMonth() > 12
          || nmea.getDay() < 1 || nmea.getDay() > 31 || nmea.getHour() > 23
          || nmea.getMinute() > 59 || nmea.getSecond() > 59) continue;
      struct tm date{};
      date.tm_year = nmea.getYear() - 1900;
      date.tm_mon = nmea.getMonth() - 1;
      date.tm_mday = nmea.getDay();
      date.tm_hour = nmea.getHour();
      date.tm_min = nmea.getMinute();
      date.tm_sec = nmea.getSecond();
      latest = {int32_t(latitude * 10), int32_t(longitude * 10),
                uint32_t(timeutil_timegm64(&date)), k_uptime_get()};
    }
  }
}

bool tag_gps_latest(TagFix& result) {
  result = latest;
  return running && result.utc && k_uptime_get() - result.received_ms <= 15000;
}
