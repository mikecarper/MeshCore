#pragma once

#include <stdint.h>

namespace mesh {
namespace gps {

inline bool isLeapYear(uint16_t year) {
  return (year % 4U) == 0U
      && ((year % 100U) != 0U || (year % 400U) == 0U);
}

inline uint8_t daysInMonth(uint16_t year, uint8_t month) {
  static const uint8_t days[] = {
      31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31,
  };
  if (month < 1 || month > 12) return 0;
  if (month == 2 && isLeapYear(year)) return 29;
  return days[month - 1];
}

// Reject the zero/partial dates emitted before an NMEA receiver has acquired
// UTC. The upper bound also keeps LocationProvider's signed-long timestamp
// valid on 32-bit targets.
inline bool isValidNmeaDateTime(uint16_t year, uint8_t month, uint8_t day,
                                uint8_t hour, uint8_t minute,
                                uint8_t second) {
  if (year < 2020 || year > 2038
      || month < 1 || month > 12
      || day < 1 || day > daysInMonth(year, month)
      || hour > 23 || minute > 59 || second > 59) {
    return false;
  }
  if (year < 2038) return true;

  // 2,147,483,647 is 2038-01-19 03:14:07 UTC.
  if (month != 1 || day > 19) return false;
  if (day < 19 || hour < 3) return true;
  if (hour > 3 || minute > 14) return false;
  return minute < 14 || second <= 7;
}

}  // namespace gps
}  // namespace mesh
