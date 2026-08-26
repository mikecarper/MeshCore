#include "IndicatorFontClient.h"

#include <Arduino.h>
#include <esp32-hal-psram.h>

namespace {

static const uint32_t FONT_UART_BAUD = 1000000;
static const int FONT_UART_RX = 20;
static const int FONT_UART_TX = 19;
static const size_t MAX_FONT_BYTES = 1536 * 1024;

uint32_t updateCrc32(uint32_t crc, const uint8_t* data, size_t size) {
  for (size_t i = 0; i < size; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ (0xEDB88320UL & (0U - (crc & 1U)));
    }
  }
  return crc;
}

bool readLine(HardwareSerial& serial, char* line, size_t capacity,
              uint32_t timeout_millis) {
  size_t length = 0;
  uint32_t started = millis();
  while (millis() - started < timeout_millis) {
    while (serial.available()) {
      int value = serial.read();
      if (value < 0) break;
      if (value == '\n') {
        line[length] = 0;
        return true;
      }
      if (value != '\r' && length + 1 < capacity) {
        line[length++] = (char)value;
      }
    }
    delay(1);
  }
  line[0] = 0;
  return false;
}

bool parseInfo(const char* line, size_t& size, uint32_t& crc) {
  unsigned long parsed_size = 0;
  unsigned long parsed_crc = 0;
  int present = 0;
  if (sscanf(line, "MCFONT 1 %lu %lx", &parsed_size, &parsed_crc) != 2) {
    if (sscanf(line, "MCFONT %d", &present) == 1 && present == 0) return false;
    return false;
  }
  if (parsed_size < 64 || parsed_size > MAX_FONT_BYTES) return false;
  size = (size_t)parsed_size;
  crc = (uint32_t)parsed_crc;
  return true;
}

bool requestInfo(HardwareSerial& serial, size_t& size, uint32_t& crc) {
  char line[64];
  for (int attempt = 0; attempt < 3; ++attempt) {
    while (serial.available()) serial.read();
    serial.print("MCFONT INFO\n");
    serial.flush();
    if (readLine(serial, line, sizeof(line), 1000)
        && parseInfo(line, size, crc)) {
      return true;
    }
    delay(100);
  }
  return false;
}

bool receiveFont(HardwareSerial& serial, uint8_t* data, size_t size,
                 uint32_t expected_crc) {
  char line[64];
  serial.print("MCFONT GET\n");
  serial.flush();

  size_t response_size;
  uint32_t response_crc;
  if (!readLine(serial, line, sizeof(line), 1500)
      || !parseInfo(line, response_size, response_crc)
      || response_size != size
      || response_crc != expected_crc) {
    return false;
  }

  size_t received = 0;
  uint32_t crc = 0xFFFFFFFFUL;
  uint32_t last_progress = millis();
  while (received < size && millis() - last_progress < 2000) {
    int available = serial.available();
    if (available <= 0) {
      delay(1);
      continue;
    }
    size_t count = (size_t)available;
    if (count > size - received) count = size - received;
    size_t actual = serial.readBytes(data + received, count);
    if (actual == 0) continue;
    crc = updateCrc32(crc, data + received, actual);
    received += actual;
    last_progress = millis();
  }
  return received == size && ~crc == expected_crc;
}

}  // namespace

uint8_t* IndicatorFontClient::load(size_t& size) {
  size = 0;
  if (!psramFound()) return nullptr;

  Serial2.begin(FONT_UART_BAUD, SERIAL_8N1, FONT_UART_RX, FONT_UART_TX);
  delay(50);

  uint32_t expected_crc;
  if (!requestInfo(Serial2, size, expected_crc)) {
    Serial2.end();
    size = 0;
    return nullptr;
  }

  uint8_t* data = (uint8_t*)ps_malloc(size);
  if (data == nullptr || !receiveFont(Serial2, data, size, expected_crc)) {
    free(data);
    data = nullptr;
    size = 0;
  }
  Serial2.end();
  return data;
}
