#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

namespace mesh {
namespace esp32 {
namespace cpuclock {

// Zero retains each role's established automatic clock policy. Explicit
// selections are deliberately limited to PLL clocks supported by this UI;
// the board must additionally validate the actual chip/SDK capability.
inline bool validSelection(uint16_t mhz) {
  return mhz == 0 || mhz == 80 || mhz == 160 || mhz == 240;
}

inline uint16_t nominalMHz() {
#if defined(ESP32_POST_BOOT_CPU_FREQ)
  return ESP32_POST_BOOT_CPU_FREQ;
#elif defined(ESP32_CPU_FREQ)
  return ESP32_CPU_FREQ;
#elif defined(F_CPU)
  return F_CPU / 1000000UL;
#else
  return 80;
#endif
}

inline bool automaticDownclockAllowed() {
#if defined(SENSECAP_INDICATOR_LORA)
  // The RGB Indicator's Auto policy holds its nominal 240 MHz.
  // Explicit selections still use the board's checked apply/restore path.
  return false;
#else
  return true;
#endif
}

inline uint16_t resolveMHz(uint16_t selected, bool powersaving, bool companion) {
  if (selected && validSelection(selected)) return selected;
  const uint16_t nominal = nominalMHz();
  return companion && powersaving && automaticDownclockAllowed() && nominal > 80 ? 80 : nominal;
}

inline bool profilePerformanceEnabled(bool profiles_enabled) {
#if defined(SENSECAP_INDICATOR_LORA)
  (void)profiles_enabled;
  return false;
#else
  return profiles_enabled;
#endif
}

static constexpr size_t FooterSize = 8;

inline uint16_t footerChecksum(const uint8_t* bytes, size_t length) {
  uint16_t crc = 0xffff;
  for (size_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint16_t>(bytes[i]) << 8;
    for (unsigned bit = 0; bit < 8; ++bit)
      crc = static_cast<uint16_t>((crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1);
  }
  return crc;
}

inline void encodeFooter(uint8_t* out, uint16_t selected) {
  if (!validSelection(selected)) selected = 0;
  out[0] = 'C'; out[1] = 'P'; out[2] = 'U'; out[3] = 1;
  out[4] = static_cast<uint8_t>(selected >> 8);
  out[5] = static_cast<uint8_t>(selected);
  const uint16_t crc = footerChecksum(out, 6);
  out[6] = static_cast<uint8_t>(crc >> 8);
  out[7] = static_cast<uint8_t>(crc);
}

// A missing, truncated, foreign or corrupt footer always selects Auto, even
// when loading into an object which previously held an explicit selection.
inline bool decodeFooter(const uint8_t* in, size_t length, uint16_t& selected) {
  selected = 0;
  if (!in || length != FooterSize || in[0] != 'C' || in[1] != 'P'
      || in[2] != 'U' || in[3] != 1) return false;
  const uint16_t value = static_cast<uint16_t>((in[4] << 8) | in[5]);
  const uint16_t crc = static_cast<uint16_t>((in[6] << 8) | in[7]);
  if (!validSelection(value) || footerChecksum(in, 6) != crc) return false;
  selected = value;
  return true;
}

template <typename File>
uint16_t readFooter(File& file) {
  uint16_t selected = 0;
  uint8_t bytes[FooterSize] = {};
  if (file.size() >= FooterSize && file.seek(file.size() - FooterSize))
    decodeFooter(bytes, file.read(bytes, sizeof(bytes)), selected);
  return selected;
}

inline bool parseSelection(const char* value, uint16_t& selected) {
  if (!value) return false;
  while (*value == ' ' || *value == '\t') ++value;
  const char* end = value + strlen(value);
  while (end > value && (end[-1] == ' ' || end[-1] == '\t')) --end;
  const size_t length = static_cast<size_t>(end - value);
  if (length == 4 && memcmp(value, "auto", 4) == 0) selected = 0;
  else if (length == 2 && memcmp(value, "80", 2) == 0) selected = 80;
  else if (length == 3 && memcmp(value, "160", 3) == 0) selected = 160;
  else if (length == 3 && memcmp(value, "240", 3) == 0) selected = 240;
  else return false;
  return true;
}

template <typename Board>
void formatStatus(Board& board, uint16_t selected, char* reply, size_t capacity,
                  const char* prefix = ">") {
  char selection[8] = "auto";
  if (selected) snprintf(selection, sizeof(selection), "%u", unsigned(selected));
  char supported[24] = "auto";
  const uint16_t choices[] = {80, 160, 240};
  for (uint16_t mhz : choices) {
    if (board.supportsCpuFrequencyMHz(mhz)) {
      const size_t used = strlen(supported);
      snprintf(supported + used, sizeof(supported) - used, ",%u", unsigned(mhz));
    }
  }
  snprintf(reply, capacity, "%s selected=%s actual=%uMHz supported=%s", prefix,
           selection, unsigned(board.getCpuFrequencyMHz()), supported);
}

template <typename Board, typename Save>
bool handleCommand(Board& board, uint16_t& selected, bool powersaving,
                   const char* command, char* reply, size_t capacity, Save save) {
  if (!command || !reply || !capacity) return false;
  const bool get = strncmp(command, "get cpu.freq", 12) == 0;
  const bool set = strncmp(command, "set cpu.freq", 12) == 0;
  if ((!get && !set) || (command[12] && command[12] != ' ' && command[12] != '\t'))
    return false;
  const char* value = command + 12;
  while (*value == ' ' || *value == '\t') ++value;
  if (get) {
    if (*value) snprintf(reply, capacity, "Error: usage get cpu.freq");
    else formatStatus(board, selected, reply, capacity);
    return true;
  }
  uint16_t wanted = 0;
  if (!parseSelection(value, wanted)) {
    snprintf(reply, capacity, "Error: use set cpu.freq auto|80|160|240");
    return true;
  }
  if (wanted && !board.supportsCpuFrequencyMHz(wanted)) {
    snprintf(reply, capacity, "Error: unsupported CPU clock; use get cpu.freq");
    return true;
  }
  const uint16_t previous = selected;
  selected = wanted;
  if (!board.applyCpuFrequencyMHz(wanted, powersaving)) {
    selected = previous;
    const bool restored = board.applyCpuFrequencyMHz(previous, powersaving);
    snprintf(reply, capacity, restored ? "Error: CPU clock apply failed; unchanged"
        : "Error: CPU clock apply and restore failed; check get cpu.freq");
  } else if (!save()) {
    selected = previous;
    const bool restored = board.applyCpuFrequencyMHz(previous, powersaving);
    snprintf(reply, capacity, restored ? "Error: CPU clock save failed; restored"
        : "Error: CPU clock save and restore failed; check get cpu.freq");
  } else {
    formatStatus(board, selected, reply, capacity, "OK - saved");
  }
  return true;
}

} // namespace cpuclock
} // namespace esp32
} // namespace mesh
